"""Several page ranges per case: one per reporter whose pages a stored text marks
(2026-11-25). Invented case and text: one decision held as an official copy marking the
Wn.2d pages and a Pacific copy marking the P.3d pages."""

from __future__ import annotations

import sqlite3

import pytest

from wacite.check.audit import audit_citations
from wacite.index import build
from wacite.index.build import with_page_ranges, write_index
from wacite.index.store import CiteIndex
from wacite.models import FindingType
from wacite.pages import TEXT_SEP, page_maps, segments
from wacite.parse.citations import parse_citations

OFFICIAL = ("Doe appeals his conviction. *102 The officer stopped the car without any cause at all. "
            "*103 We hold the stop was unlawful because no exception applied. *104 Reversed and remanded.")
PACIFIC = ("Doe appeals his conviction. *501 The officer stopped the car without any cause at all. "
           "*502 We hold the stop was unlawful because no exception applied. *503 Reversed and remanded.")
CITES = ["150 Wn.2d 101", "70 P.3d 500"]
JOINED = PACIFIC + TEXT_SEP + OFFICIAL  # a recovered page text, then full_text


def _row(text=JOINED):
    return {
        "authority_id": "a-doe", "authority_type": "case",
        "canonical_cite": "State v. Doe (Washington Supreme Court 2003)",
        "canonical_cite_normalized": "state v. doe (washington supreme court 2003)",
        "display_title": "State v. Doe", "year": 2003, "court_name": "Washington Supreme Court",
        "court_level": 1, "authority_status": "current", "parallel_cites": list(CITES), "full_text": text,
    }


@pytest.fixture
def doe(tmp_path):
    path = tmp_path / "cite_index.sqlite"
    write_index(path, with_page_ranges([_row()]))
    with CiteIndex(path) as idx:
        yield idx


def test_each_joined_text_maps_its_own_reporter():
    assert segments(JOINED) == [PACIFIC, OFFICIAL]
    maps = page_maps(JOINED, CITES)
    assert [(pm.cite, pm.first_page, pm.last_page) for pm, _ in maps] == [
        ("70 P.3d 500", 500, 503), ("150 Wn.2d 101", 101, 104)]
    # Two texts marking one reporter: the one spanning more pages wins, else the first.
    assert [pm.last_page for pm, _ in page_maps(PACIFIC.replace(" *503 ", " ") + TEXT_SEP + PACIFIC, CITES)] == [503]
    assert [seg for _, seg in page_maps(PACIFIC + TEXT_SEP + PACIFIC + " tail", CITES)] == [PACIFIC]


def test_the_index_holds_a_range_per_marked_reporter(doe):
    assert doe.page_ranges("a-doe") == [("70 p.3d 500", 500, 503), ("150 wn.2d 101", 101, 104)]
    assert doe.page_range("a-doe", "150 Wash. 2d 101") == ("150 wn.2d 101", 101, 104)
    assert doe.page_range("a-doe", "71 P.3d 1") is None
    assert doe.page_range("a-doe") == ("70 p.3d 500", 500, 503)  # no cite: the first stored
    ranks = dict(doe._conn.execute("SELECT cite_normalized, rank FROM cite_lookup WHERE authority_id = 'a-doe'"))
    assert ranks["150 wn.2d 101"] == 0 and ranks["70 p.3d 500"] == 0


def test_a_pin_in_either_reporter_is_checked(doe):
    for cite in ("State v. Doe, 150 Wn.2d 101, 103 (2003).", "State v. Doe, 70 P.3d 500, 502 (2003)."):
        report = audit_citations(parse_citations(cite), doe)
        assert report.findings == [] and report.pins_checked == 1, cite
    out = audit_citations(parse_citations("State v. Doe, 70 P.3d 500, 520 (2003)."), doe)
    assert [f.finding_type for f in out.findings] == [FindingType.PIN_OUT_OF_RANGE]
    assert "500-503" in out.findings[0].message


def test_a_pin_in_an_unmarked_reporter_names_the_marked_ones(tmp_path):
    path = tmp_path / "one.sqlite"
    write_index(path, with_page_ranges([_row(OFFICIAL)]))
    with CiteIndex(path) as idx:
        report = audit_citations(parse_citations("State v. Doe, 70 P.3d 500, 502 (2003)."), idx)
    (f,) = report.findings
    assert f.finding_type is FindingType.PIN_NOT_VERIFIED
    assert "marks the pages of 150 wn.2d 101, not 70 P.3d 500" in f.message
    assert report.pins_checked == 0


def _quotes(text, index):
    return audit_citations(parse_citations(text), index, text=text, page_source=lambda aid: JOINED)


def test_a_quote_is_found_on_the_pinned_page_of_either_reporter(doe):
    quote = "“the stop was unlawful because no exception applied.” "
    for cite in ("State v. Doe, 70 P.3d 500, 502 (2003).", "State v. Doe, 150 Wn.2d 101, 103 (2003)."):
        report = _quotes(quote + cite, doe)
        assert report.findings == [] and report.quotes_checked == 1, cite
    report = _quotes(quote + "State v. Doe, 70 P.3d 500, 501 (2003).", doe)
    (f,) = [f for f in report.findings if f.finding_type is FindingType.QUOTE_ELSEWHERE]
    assert "page 502" in f.message


def test_a_quote_only_in_another_stored_text_is_still_found(tmp_path):
    # The page text holds the lead opinion; a dissent is only in full_text.
    joined = PACIFIC + TEXT_SEP + OFFICIAL + " GREEN, J. (dissenting) The stop was reasonable on these facts."
    path = tmp_path / "d.sqlite"
    write_index(path, with_page_ranges([_row(joined)]))
    text = "“The stop was reasonable on these facts.” State v. Doe, 70 P.3d 500, 503 (2003)."
    with CiteIndex(path) as idx:
        report = audit_citations(parse_citations(text), idx, text=text, page_source=lambda aid: joined)
    assert report.quotes_checked == 1
    assert not [f for f in report.findings if f.finding_type is FindingType.QUOTE_NOT_FOUND]


def test_an_index_with_one_range_per_case_still_reads(tmp_path):
    path = tmp_path / "old.sqlite"
    write_index(path, [{k: v for k, v in _row().items() if k != "full_text"} | {"page_range": ("150 Wn.2d 101", 101, 104)}])
    con = sqlite3.connect(path)
    con.executescript(
        "DROP TABLE page_range; CREATE TABLE page_range (authority_id TEXT PRIMARY KEY, cite_normalized TEXT NOT NULL, "
        "first_page INTEGER NOT NULL, last_page INTEGER NOT NULL); "
        "INSERT INTO page_range VALUES ('a-doe', '150 wn.2d 101', 101, 104);")
    con.commit()
    con.close()
    with CiteIndex(path) as idx:
        assert idx.page_ranges("a-doe") == [("150 wn.2d 101", 101, 104)]
        assert idx.page_range("a-doe") == ("150 wn.2d 101", 101, 104)
        assert audit_citations(parse_citations("State v. Doe, 150 Wn.2d 101, 103 (2003)."), idx).pins_checked == 1


def test_the_build_sql_joins_page_texts_before_full_text_with_the_separator():
    assert build._SQL_SEP == r"E'\n\n\x1e\n\n'"
    sql = build.PAGINATED_TEXT_SQL
    assert sql.index("case_page_text") < sql.index("a.full_text") and "merged_full_text" not in sql
    assert "GROUP BY CASE WHEN c.source_field LIKE 'html%%'" in sql


def test_the_wider_of_two_texts_marking_one_reporter_wins():
    # Invented: a merged copy marks only the first pages; the full text marks all of them.
    short = "Lead text *102 more *103 end."
    full = "Lead text *102 more *103 more *104 more *105 dissent."
    pms = page_maps(short + TEXT_SEP + full, ["150 Wn.2d 101"])
    assert [(pm.first_page, pm.last_page) for pm, _ in pms] == [(101, 105)]
