"""Build the portable SQLite citation index from the wa-legal-ai corpus.

Run once against the live PostgreSQL corpus. All subsequent checking runs
offline against the generated cite_index.sqlite (see store.py). PostgreSQL is
only required here, hence psycopg is an optional ("build") dependency.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Iterable, Iterator

from wacite.index.store import SCHEMA
from wacite.normalize import normalize_cite
from wacite.pages import page_map

# Pull only what the format/accuracy audit needs. Year prefers date_decided
# (when the opinion issued) and falls back to date_filed. A case's text is read
# only to find its page markers (wacite.pages); it is not stored in the index.
_CORPUS_QUERY = """
    SELECT
        authority_id::text                                   AS authority_id,
        authority_type::text                                 AS authority_type,
        canonical_cite,
        canonical_cite_normalized,
        display_title,
        EXTRACT(YEAR FROM COALESCE(date_decided, date_filed))::int AS year,
        court_name,
        court_level,
        authority_status::text                               AS authority_status,
        parallel_cites,
        CASE WHEN authority_type = 'case' THEN full_text END AS full_text
    FROM authority
"""


def default_dsn() -> str:
    """DSN mirroring wa-legal-ai's DatabaseSettings defaults / WALEGAL_DB_* env."""
    user = os.getenv("WALEGAL_DB_USER", "walegal")
    password = os.getenv("WALEGAL_DB_PASSWORD", "walegal_dev")
    host = os.getenv("WALEGAL_DB_HOST", "localhost")
    port = os.getenv("WALEGAL_DB_PORT", "5432")
    name = os.getenv("WALEGAL_DB_NAME", "walegal")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def write_index(out_path: str | Path, rows: Iterable[dict]) -> tuple[int, int]:
    """Create a cite_index.sqlite at out_path from authority row dicts.

    Each row dict needs: authority_id, authority_type, canonical_cite,
    canonical_cite_normalized, display_title, year, court_name, court_level,
    authority_status, parallel_cites (list[str] | None); optionally
    page_range: (marked reporter cite, first page, last page).

    Returns (authority_count, cite_lookup_count). Reused by tests to build
    fixture indexes without PostgreSQL. ``rows`` may be a generator: it is read
    once, row by row.
    """
    out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(out_path)
    try:
        conn.executescript(SCHEMA)
        n_auth = 0
        n_lookup = 0
        seen_keys: set[str] = set()
        for r in rows:
            parallel = list(r.get("parallel_cites") or [])
            conn.execute(
                "INSERT OR REPLACE INTO authority "
                "(authority_id, authority_type, canonical_cite, "
                " canonical_cite_normalized, display_title, year, court_name, "
                " court_level, authority_status, parallel_cites) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    r["authority_id"],
                    r["authority_type"],
                    r["canonical_cite"],
                    r["canonical_cite_normalized"],
                    r.get("display_title"),
                    r.get("year"),
                    r.get("court_name"),
                    r.get("court_level"),
                    r.get("authority_status", "current"),
                    json.dumps(parallel),
                ),
            )
            n_auth += 1
            if r.get("page_range"):
                cite, first, last = r["page_range"]
                conn.execute(
                    "INSERT OR REPLACE INTO page_range (authority_id, cite_normalized, first_page, last_page) "
                    "VALUES (?,?,?,?)",
                    (r["authority_id"], normalize_cite(cite), first, last),
                )

            # Index the canonical cite plus every parallel reporter cite.
            keys = [r["canonical_cite_normalized"]]
            keys.extend(normalize_cite(pc) for pc in parallel)
            for key in keys:
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)
                conn.execute(
                    "INSERT OR IGNORE INTO cite_lookup "
                    "(cite_normalized, authority_id) VALUES (?, ?)",
                    (key, r["authority_id"]),
                )
                n_lookup += 1
        conn.commit()
        return n_auth, n_lookup
    finally:
        conn.close()


def with_page_ranges(rows: Iterable[dict]) -> Iterator[dict]:
    """Add each case's page_range from its text's star markers, then drop the text."""
    for r in rows:
        text = r.pop("full_text", None)
        if text and r.get("authority_type") == "case":
            pm = page_map(text, list(r.get("parallel_cites") or []) + [r.get("canonical_cite") or ""])
            if pm:
                r["page_range"] = (pm.cite, pm.first_page, pm.last_page)
        yield r


def build_from_postgres(out_path: str | Path, dsn: str | None = None) -> tuple[int, int]:
    """Read the corpus authority table and write the SQLite index.

    Rows stream through a server-side cursor: case text (about 2.4 GB in
    November 2026) is read only to find page markers, never held all at once.
    """
    import psycopg  # optional dependency; only needed for the build step
    from psycopg.rows import dict_row

    dsn = dsn or default_dsn()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor(name="wacite_build") as cur:
            cur.itersize = 2000
            cur.execute(_CORPUS_QUERY)
            return write_index(out_path, with_page_ranges(cur))
