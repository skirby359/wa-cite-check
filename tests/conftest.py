"""Shared fixtures: a tiny in-process SQLite citation index (no PostgreSQL)."""

from __future__ import annotations

import pytest

from wacite.index.build import write_index
from wacite.index.store import CiteIndex

# Authorities seeded for the test suite. Mirrors how the corpus stores cases:
# canonical_cite is the name-year form; reporter cites live in parallel_cites.
FIXTURE_ROWS = [
    {
        "authority_id": "a-smith",
        "authority_type": "case",
        "canonical_cite": "State v. Smith (Washington Supreme Court 2022)",
        "canonical_cite_normalized": "state v. smith (washington supreme court 2022)",
        "display_title": "State v. Smith",
        "year": 2022,
        "court_name": "Washington Supreme Court",
        "court_level": 1,
        "authority_status": "current",
        "parallel_cites": ["199 Wn.2d 1", "502 P.3d 1"],
    },
    {
        "authority_id": "a-jones",
        "authority_type": "case",
        "canonical_cite": "State v. Jones (Washington Supreme Court 2003)",
        "canonical_cite_normalized": "state v. jones (washington supreme court 2003)",
        "display_title": "State v. Jones",
        "year": 2003,
        "court_name": "Washington Supreme Court",
        "court_level": 1,
        "authority_status": "overruled",
        "parallel_cites": ["150 Wn.2d 50"],
    },
    {
        "authority_id": "a-other",
        "authority_type": "case",
        "canonical_cite": "State v. Other (Washington Court of Appeals 2010)",
        "canonical_cite_normalized": "state v. other (washington court of appeals 2010)",
        "display_title": "State v. Other",
        "year": 2010,
        "court_name": "Washington Court of Appeals",
        "court_level": 2,
        "authority_status": "current",
        "parallel_cites": ["999 P.3d 9"],
    },
    {
        "authority_id": "a-rcw",
        "authority_type": "statute",
        "canonical_cite": "RCW 9A.52.070",
        "canonical_cite_normalized": "rcw 9a.52.070",
        "display_title": "Criminal trespass in the first degree",
        "year": None,
        "court_name": None,
        "court_level": None,
        "authority_status": "current",
        "parallel_cites": [],
    },
]


@pytest.fixture
def index_path(tmp_path):
    path = tmp_path / "cite_index.sqlite"
    write_index(path, FIXTURE_ROWS)
    return str(path)


@pytest.fixture
def index(index_path):
    with CiteIndex(index_path) as idx:
        yield idx
