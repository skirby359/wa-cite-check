"""Offline read API over the portable SQLite citation index.

No PostgreSQL dependency — this is what runs at check time. It is the
equivalent of wa-legal-ai's CitationResolver, but get_authority() returns the
full record (name, year, status, parallel cites) rather than just an id.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from wacite.models import AuthorityRecord
from wacite.normalize import names_match, normalize_cite

# SQLite schema for the portable index (kept in sync with index/build.py).
SCHEMA = """
CREATE TABLE IF NOT EXISTS authority (
    authority_id              TEXT PRIMARY KEY,
    authority_type            TEXT NOT NULL,
    canonical_cite            TEXT NOT NULL,
    canonical_cite_normalized TEXT NOT NULL,
    display_title             TEXT,
    year                      INTEGER,
    court_name                TEXT,
    court_level               INTEGER,
    authority_status          TEXT NOT NULL DEFAULT 'current',
    parallel_cites            TEXT          -- JSON array
);

-- Every authority that lists a cite (since 2026-11-21; before, only the first row the
-- build read, which was arbitrary). A table page is shared by many decisions, and a
-- cite stored on the wrong row is shared too. rank orders the holders when the case
-- name can't choose: 0 the row's text carries star pages for this cite, 1 its
-- canonical cite, 2 a parallel cite; then authority_id.
CREATE TABLE IF NOT EXISTS cite_lookup (
    cite_normalized TEXT NOT NULL,
    authority_id    TEXT NOT NULL,
    rank            INTEGER NOT NULL DEFAULT 2,
    PRIMARY KEY (cite_normalized, authority_id)
);

CREATE INDEX IF NOT EXISTS idx_lookup_aid ON cite_lookup(authority_id);

-- Pages of each reporter whose star pagination a stored text of the case marks (see
-- wacite.pages.page_maps). Cases only; none when no text has markers. Until 2026-11-25
-- one row per case (PRIMARY KEY authority_id); CiteIndex reads both.
CREATE TABLE IF NOT EXISTS page_range (
    authority_id    TEXT NOT NULL,
    cite_normalized TEXT NOT NULL,
    first_page      INTEGER NOT NULL,
    last_page       INTEGER NOT NULL,
    PRIMARY KEY (authority_id, cite_normalized)
);
"""


class CiteIndex:
    """Read-only handle on a cite_index.sqlite file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(
                f"Citation index not found: {self.path}. "
                f"Build one with `wacite build-index`."
            )
        self._conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(cite_lookup)")}
        # An index built before 2026-11-21 holds one row per cite and no rank.
        self._order = " ORDER BY rank, authority_id" if "rank" in cols else ""

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CiteIndex":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def candidates(self, cite: str) -> list[str]:
        """Every authority_id that lists this citation, best first (see SCHEMA)."""
        rows = self._conn.execute(
            "SELECT authority_id FROM cite_lookup WHERE cite_normalized = ?" + self._order,
            (normalize_cite(cite),),
        ).fetchall()
        return [r["authority_id"] for r in rows]

    def resolve(self, cite: str) -> str | None:
        """Return the authority_id for a citation string, or None. When several
        authorities list it, the first by rank (see ``lookup_by_cite`` to choose by name)."""
        ids = self.candidates(cite)
        return ids[0] if ids else None

    def get_authority(self, authority_id: str) -> AuthorityRecord | None:
        """Return the full authority record for an id."""
        row = self._conn.execute(
            "SELECT * FROM authority WHERE authority_id = ?", (authority_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def lookup_by_cite(self, cite: str, name: str | None = None) -> AuthorityRecord | None:
        """Resolve a cite string straight to its authority record.

        When several authorities list the cite, the first whose caption fits ``name``
        (the case name the brief gives) wins, else the first by rank. The record's
        ``shared_with`` holds the other holders' captions.
        """
        ids = self.candidates(cite)
        if not ids:
            return None
        records = [r for r in (self.get_authority(a) for a in ids) if r is not None]
        if not records:
            return None
        pick = records[0]
        if name and len(records) > 1:
            pick = next((r for r in records if r.display_title and names_match(name, r.display_title)), pick)
        pick.shared_with = [r.display_title or r.canonical_cite for r in records if r is not pick]
        return pick

    def page_ranges(self, authority_id: str) -> list[tuple[str, int, int]]:
        """[(marked reporter cite, first page, last page), ...], one per reporter whose
        pages a stored text marks; [] when none does or the index predates page ranges."""
        try:
            rows = self._conn.execute(
                "SELECT cite_normalized, first_page, last_page FROM page_range WHERE authority_id = ? "
                "ORDER BY rowid",
                (authority_id,),
            ).fetchall()
        except sqlite3.OperationalError:  # index built before page_range existed
            return []
        return [(r["cite_normalized"], r["first_page"], r["last_page"]) for r in rows]

    def page_range(self, authority_id: str, cite: str | None = None) -> tuple[str, int, int] | None:
        """With ``cite``, that reporter's (cite, first page, last page), or None when no
        stored text marks its pages. Without, the first range stored for the case."""
        ranges = self.page_ranges(authority_id)
        if cite is None:
            return ranges[0] if ranges else None
        want = normalize_cite(cite)
        return next((r for r in ranges if r[0] == want), None)

    def count_authorities(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM authority").fetchone()[0]

    def suggest_near(self, cite: str, limit: int = 3) -> list[str]:
        """Cheap 'did you mean' for an unresolved cite.

        Matches normalized cites that share the leading token (volume+reporter
        for cases, the title for statutes). Not fuzzy spell-check — just a hint.
        """
        norm = normalize_cite(cite)
        prefix = " ".join(norm.split(" ")[:2])
        if not prefix:
            return []
        rows = self._conn.execute(
            "SELECT DISTINCT cite_normalized FROM cite_lookup "
            "WHERE cite_normalized LIKE ? LIMIT ?",
            (prefix + "%", limit),
        ).fetchall()
        return [r["cite_normalized"] for r in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> AuthorityRecord:
        parallel = json.loads(row["parallel_cites"]) if row["parallel_cites"] else []
        return AuthorityRecord(
            authority_id=row["authority_id"],
            authority_type=row["authority_type"],
            canonical_cite=row["canonical_cite"],
            display_title=row["display_title"],
            year=row["year"],
            court_name=row["court_name"],
            court_level=row["court_level"],
            authority_status=row["authority_status"],
            parallel_cites=parallel,
        )
