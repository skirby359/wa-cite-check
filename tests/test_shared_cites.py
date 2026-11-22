"""A cite several authorities list: a table page, or a cite stored on the wrong row
(invented rows). Until 2026-11-21 the index kept whichever row the build read first."""

from __future__ import annotations

import pytest

from wacite.check.audit import audit_citations
from wacite.index.build import write_index
from wacite.index.store import CiteIndex
from wacite.models import FindingType
from wacite.parse.citations import parse_citations


def _case(aid, title, cites, page_range=None):
    row = {
        "authority_id": aid, "authority_type": "case",
        "canonical_cite": f"{title} (Supreme Court of the United States 2014)",
        "canonical_cite_normalized": f"{title.lower()} (supreme court of the united states 2014)",
        "display_title": title, "year": 2014, "court_name": "Supreme Court of the United States",
        "court_level": 0, "authority_status": "current", "parallel_cites": cites,
    }
    if page_range:
        row["page_range"] = page_range
    return row


ROWS = [
    # Read first, and it wrongly lists the other case's S. Ct. and L. Ed. cites.
    _case("z-wrong-row", "Halliday Co. v. Fund", ["134 S. Ct. 2259", "189 L. Ed. 2d 262"]),
    _case("m-right-row", "Abrams v. United States", ["573 U.S. 169", "134 S. Ct. 2259", "189 L. Ed. 2d 262"],
          page_range=("573 U.S. 169", 169, 200)),
    _case("a-table-1", "State v. One", ["158 Wn.2d 1024"]),
    _case("b-table-2", "State v. Two", ["158 Wn.2d 1024"]),
]


@pytest.fixture
def shared(tmp_path):
    path = tmp_path / "cite_index.sqlite"
    n_auth, n_keys = write_index(path, ROWS)
    assert n_auth == 4
    with CiteIndex(path) as idx:
        yield idx


def test_every_holder_is_kept_in_rank_order(shared):
    assert shared.candidates("134 S. Ct. 2259") == ["m-right-row", "z-wrong-row"]  # same rank: by id
    assert shared.candidates("158 Wash. 2d 1024") == ["a-table-1", "b-table-2"]
    assert shared.resolve("134 S. Ct. 2259") == "m-right-row"


def test_the_case_name_chooses_among_holders(shared):
    assert shared.lookup_by_cite("158 Wn.2d 1024", name="State v. Two").authority_id == "b-table-2"
    rec = shared.lookup_by_cite("134 S. Ct. 2259", name="Halliday Co. v. Fund")
    assert rec.authority_id == "z-wrong-row" and rec.shared_with == ["Abrams v. United States"]
    assert shared.lookup_by_cite("158 Wn.2d 1024").authority_id == "a-table-1"  # no name: by rank


def test_audit_uses_the_named_holder_and_says_who_else_holds_the_cite(shared):
    ok = audit_citations(parse_citations("Abrams v. United States, 134 S. Ct. 2259 (2014)."), shared)
    assert ok.findings == []
    ok = audit_citations(parse_citations("State v. Two, 158 Wn.2d 1024 (2014)."), shared)
    assert not [f for f in ok.findings if f.finding_type is FindingType.NAME_MISMATCH]
    bad = audit_citations(parse_citations("State v. Three, 158 Wn.2d 1024 (2014)."), shared)
    (f,) = [f for f in bad.findings if f.finding_type is FindingType.NAME_MISMATCH]
    assert "'State v. One'" in f.message and "also lists this cite for 'State v. Two'" in f.message


def test_parallel_check_accepts_any_holder(shared):
    report = audit_citations(
        parse_citations("Abrams v. United States, 573 U.S. 169, 134 S. Ct. 2259 (2014)."), shared)
    assert FindingType.PARALLEL_MISMATCH not in {f.finding_type for f in report.findings}


def test_an_index_built_before_ranks_still_reads(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    write_index(path, ROWS[:1])
    con = sqlite3.connect(path)
    con.executescript("DROP TABLE cite_lookup; CREATE TABLE cite_lookup (cite_normalized TEXT PRIMARY KEY, "
                      "authority_id TEXT NOT NULL); INSERT INTO cite_lookup VALUES ('134 s. ct. 2259', 'z-wrong-row');")
    con.commit()
    con.close()
    with CiteIndex(path) as idx:
        assert idx.resolve("134 S. Ct. 2259") == "z-wrong-row"
