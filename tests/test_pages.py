"""wacite.pages: star-page mapping, pin parsing and quotation search (invented text)."""

from __future__ import annotations

from wacite.pages import page_map, page_texts, parse_pin, quote_fragments, quote_pages

TEXT = ("Opening text on the first page, citing 10 Wn.2d 400, 405 (1990). *102 Second page text. "
        "Footnote *7 is noise. *103 Third page text runs on *104 into the fourth page. **55 other reporter")


def test_page_map_follows_the_reporter_with_the_most_markers():
    pm = page_map(TEXT, ["50 P.3d 54", "150 Wn.2d 101", "2004 WL 12345"])
    assert (pm.cite, pm.first_page, pm.last_page) == ("150 Wn.2d 101", 101, 104)
    assert [p for p, _ in pm.breaks] == [102, 103, 104]
    pages = page_texts(TEXT, pm)
    assert pages[101].startswith("Opening") and "Second page" in pages[102]
    assert "Third page" in pages[103] and "fourth page" in pages[104]


def test_page_map_needs_a_marker_inside_a_cite_range():
    assert page_map("No markers here.", ["150 Wn.2d 101"]) is None
    assert page_map("Only *900 far away.", ["150 Wn.2d 101"]) is None
    assert page_map(TEXT, ["2004 WL 12345"]) is None


def test_parse_pin():
    assert parse_pin("163") == (163, 163)
    assert parse_pin("163-64") == (163, 164)
    assert parse_pin("163–175") == (163, 175)
    assert parse_pin("1099-101") == (1099, 1101)
    assert parse_pin(None) is None and parse_pin("n.4") is None


def test_quote_search_finds_the_start_page_across_breaks_and_alterations():
    pm = page_map(TEXT, ["150 Wn.2d 101"])
    pages = page_texts(TEXT, pm)
    assert quote_pages("Third page text runs on into the fourth page", pages) == ([103], True)
    assert quote_pages("[t]hird page text runs . . . into the fourth page", pages) == ([103], True)
    assert quote_pages("Third page text runs on and on and on", pages)[1] is False
    assert quote_fragments("a b c") == []  # too short to check
