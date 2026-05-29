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
from typing import Iterable

from wacite.index.store import SCHEMA
from wacite.normalize import normalize_cite

# Pull only what the format/accuracy audit needs. Year prefers date_decided
# (when the opinion issued) and falls back to date_filed.
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
        parallel_cites
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
    authority_status, parallel_cites (list[str] | None).

    Returns (authority_count, cite_lookup_count). Reused by tests to build
    fixture indexes without PostgreSQL.
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


def build_from_postgres(out_path: str | Path, dsn: str | None = None) -> tuple[int, int]:
    """Read the corpus authority table and write the SQLite index."""
    import psycopg  # optional dependency; only needed for the build step
    from psycopg.rows import dict_row

    dsn = dsn or default_dsn()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(_CORPUS_QUERY)
            rows = cur.fetchall()
    return write_index(out_path, rows)
