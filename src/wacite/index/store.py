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
from wacite.normalize import normalize_cite

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

CREATE TABLE IF NOT EXISTS cite_lookup (
    cite_normalized TEXT PRIMARY KEY,
    authority_id    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lookup_aid ON cite_lookup(authority_id);
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

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CiteIndex":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def resolve(self, cite: str) -> str | None:
        """Return the authority_id for a citation string, or None."""
        norm = normalize_cite(cite)
        row = self._conn.execute(
            "SELECT authority_id FROM cite_lookup WHERE cite_normalized = ?",
            (norm,),
        ).fetchone()
        return row["authority_id"] if row else None

    def get_authority(self, authority_id: str) -> AuthorityRecord | None:
        """Return the full authority record for an id."""
        row = self._conn.execute(
            "SELECT * FROM authority WHERE authority_id = ?", (authority_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def lookup_by_cite(self, cite: str) -> AuthorityRecord | None:
        """Resolve a cite string straight to its authority record."""
        aid = self.resolve(cite)
        return self.get_authority(aid) if aid else None

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
            "SELECT cite_normalized FROM cite_lookup "
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
