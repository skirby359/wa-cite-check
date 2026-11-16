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
        # The stored text marks the Wn.2d pages, 1 through 30 (wacite.pages).
        "page_range": ("199 Wn.2d 1", 1, 30),
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
        # A mandamus caption naming the judge; briefs cite it as "United States v. Roe".
        "authority_id": "a-roe",
        "authority_type": "case",
        "canonical_cite": "500 F.2d 100",
        "canonical_cite_normalized": "500 f.2d 100",
        "display_title": "United States v. The Honorable Jane Q. Roe, United States District Judge",
        "year": 1971,
        "court_name": "Ninth Circuit Court of Appeals",
        "court_level": 1,
        "authority_status": "current",
        "parallel_cites": ["500 F.2d 100"],
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
    {
        # Bad data: a statute stamped with a CASE treatment ('superseded').
        # Statutes are never overruled/superseded — this must NOT be reported.
        "authority_id": "a-rcw-badstatus",
        "authority_type": "statute",
        "canonical_cite": "RCW 26.16.030",
        "canonical_cite_normalized": "rcw 26.16.030",
        "display_title": "Community property",
        "year": None,
        "court_name": None,
        "court_level": None,
        "authority_status": "superseded",
        "parallel_cites": [],
    },
    {
        # Legitimate: a statute can be 'repealed' — this MUST still be reported.
        "authority_id": "a-rcw-repealed",
        "authority_type": "statute",
        "canonical_cite": "RCW 9A.52.900",
        "canonical_cite_normalized": "rcw 9a.52.900",
        "display_title": "Savings — repealed provision",
        "year": None,
        "court_name": None,
        "court_level": None,
        "authority_status": "repealed",
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
