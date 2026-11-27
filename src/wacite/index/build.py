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
from wacite.pages import TEXT_SEP, page_maps

# A case's stored texts, joined with wacite.pages.TEXT_SEP, then full_text:
# - the corpus's case_page_text recovered from CourtListener's richer fields (since
#   2026-11-16), all of a case's opinions as one text, lead first, as before: a separate
#   opinion's pages continue the lead's;
# - each other case_page_text row on its own: the text of a merged copy of the decision
#   (source_field merged_full_text), which may mark another reporter (official vs Pacific).
# Until 2026-11-25 case_page_text, where present, replaced full_text, so only one
# reporter's pages were known per case.
_SQL_SEP = "E'" + TEXT_SEP.encode("unicode_escape").decode("ascii") + "'"
PAGINATED_TEXT_SQL = f"""NULLIF(CONCAT_WS({_SQL_SEP},
    (SELECT string_agg(g.t, {_SQL_SEP} ORDER BY g.r, g.o)
     FROM (SELECT string_agg(c.paginated_text, E'\\n\\n' ORDER BY c.type_rank, c.opinion_id) AS t,
                  min(c.type_rank) AS r, min(c.opinion_id) AS o
           FROM case_page_text c WHERE c.authority_id = a.authority_id
           GROUP BY CASE WHEN c.source_field LIKE 'html%%' OR c.source_field LIKE 'xml%%'
                         THEN '' ELSE c.opinion_id::text END) g),
    a.full_text), '')"""


def text_sql(conn) -> str:
    """PAGINATED_TEXT_SQL, or plain full_text on a corpus without case_page_text."""
    row = conn.execute("SELECT to_regclass('public.case_page_text') IS NOT NULL").fetchone()
    has_table = row[0] if not isinstance(row, dict) else next(iter(row.values()))
    return PAGINATED_TEXT_SQL if has_table else "a.full_text"


# Pull only what the format/accuracy audit needs. Year prefers date_decided
# (when the opinion issued) and falls back to date_filed. A case's text is read
# only to find its page markers (wacite.pages); it is not stored in the index.
_CORPUS_QUERY = """
    SELECT
        a.authority_id::text                                 AS authority_id,
        a.authority_type::text                               AS authority_type,
        a.canonical_cite,
        a.canonical_cite_normalized,
        a.display_title,
        EXTRACT(YEAR FROM COALESCE(a.date_decided, a.date_filed))::int AS year,
        a.court_name,
        a.court_level,
        a.authority_status::text                             AS authority_status,
        a.parallel_cites,
        CASE WHEN a.authority_type = 'case' THEN {text} END  AS full_text
    FROM authority a
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
    page_ranges: [(marked reporter cite, first page, last page), ...], one per
    reporter (or page_range: one such tuple).

    Returns (authority_count, distinct cite keys). Reused by tests to build
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
            marked: set[str] = set()
            ranges = list(r.get("page_ranges") or ([r["page_range"]] if r.get("page_range") else []))
            for cite, first, last in ranges:
                key = normalize_cite(cite)
                if key in marked:
                    continue  # the first range given for a reporter wins
                marked.add(key)
                conn.execute(
                    "INSERT OR REPLACE INTO page_range (authority_id, cite_normalized, first_page, last_page) "
                    "VALUES (?,?,?,?)",
                    (r["authority_id"], key, first, last),
                )

            # Index the canonical cite plus every parallel reporter cite. A cite another
            # row also lists gets a second row; rank orders them (see store.SCHEMA).
            canonical = r["canonical_cite_normalized"]
            keys = [canonical]
            keys.extend(normalize_cite(pc) for pc in parallel)
            for key in dict.fromkeys(k for k in keys if k):
                rank = 0 if key in marked else 1 if key == canonical else 2
                conn.execute(
                    "INSERT OR IGNORE INTO cite_lookup "
                    "(cite_normalized, authority_id, rank) VALUES (?, ?, ?)",
                    (key, r["authority_id"], rank),
                )
                if key not in seen_keys:
                    seen_keys.add(key)
                    n_lookup += 1
        conn.commit()
        return n_auth, n_lookup
    finally:
        conn.close()


def with_page_ranges(rows: Iterable[dict]) -> Iterator[dict]:
    """Add each case's page_ranges, one per reporter its stored texts mark (``page_maps``),
    then drop the text."""
    for r in rows:
        text = r.pop("full_text", None)
        if text and r.get("authority_type") == "case":
            cites = list(r.get("parallel_cites") or []) + [r.get("canonical_cite") or ""]
            ranges = [(pm.cite, pm.first_page, pm.last_page) for pm, _seg in page_maps(text, cites)]
            if ranges:
                r["page_ranges"] = ranges
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
        query = _CORPUS_QUERY.format(text=text_sql(conn))
        with conn.cursor(name="wacite_build") as cur:
            cur.itersize = 2000
            cur.execute(query)
            return write_index(out_path, with_page_ranges(cur))
