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


def _pages(pages, label="page"):
    return " ".join(f"*{p} {label} page {p} text." for p in pages)


def test_page_map_allows_headnote_pages_before_the_first_marker():
    # An official reporter's syllabus and headnotes can fill several pages: 172 Wn.2d 702's
    # first marker is *709. The sparse regional-reporter markers lose to the official run.
    text = "Caption and counsel. " + _pages(range(709, 729)) + " *122 *128 *138"
    pm = page_map(text, ["261 P.3d 119", "172 Wash. 2d 702"])
    assert (pm.cite, pm.first_page, pm.last_page) == ("172 Wash. 2d 702", 702, 728)
    assert page_map("Caption. " + _pages(range(740, 750)), ["172 Wash. 2d 702"]) is None  # 38 pages in


def test_page_map_keeps_a_separate_opinion_whose_pages_start_over():
    # The lead opinion runs 513-522; a separate opinion stored after it marks 510-517; the
    # lead's last page follows. Two stray markers (*722 *711) are noise.
    text = ("x " + _pages([513, 514, 516, 518, 519, 520, 521, 522], "lead") + " *722 *711 "
            + _pages([510, 511, 512, 515, 517], "other") + " *523 end")
    pm = page_map(text, ["158 Wash. 2d 506"])
    assert (pm.first_page, pm.last_page) == (506, 523)
    assert [p for p, _ in pm.breaks] == [513, 514, 516, 518, 519, 520, 521, 522, 510, 511, 512, 515, 517, 523]
    pages = page_texts(text, pm)
    assert "other page 511" in pages[511] and "lead page 514" in pages[514] and "end" in pages[523]
    assert quote_pages("other page 515 text", pages) == ([515], True)


def test_page_map_prefers_the_longest_run_over_an_early_stray_marker():
    # A footnote "*103" before the text's real markers would have started the old greedy
    # run and pushed *102 out.
    text = "Footnote *103 first. " + _pages(range(102, 110))
    pm = page_map(text, ["150 Wn.2d 101"])
    assert (pm.first_page, pm.last_page) == (101, 109)
    assert [p for p, _ in pm.breaks] == list(range(102, 110))


def test_page_map_counts_a_stray_marker_just_past_the_run_as_the_last_page():
    # "*386 *389 *387 *388": the run keeps 387 and 388; 389 is still the opinion's last page.
    text = "x " + _pages(range(374, 387)) + " *389 tail *387 a *388 b"
    pm = page_map(text, ["343 U.S. 373"])
    assert (pm.first_page, pm.last_page) == (373, 389)
    assert 389 not in [p for p, _ in pm.breaks]


def test_page_map_tie_goes_to_the_reporter_whose_markers_start_closest():
    # 60 Wn.2d 775's markers (*776 on) are also 33 pages into 375 P.2d 743; listed first,
    # the P.2d cite must not win them.
    text = "Caption. " + _pages(range(776, 782))
    import wacite.pages as pages

    old = pages._FIRST_GAP
    pages._FIRST_GAP = 40
    try:
        pm = page_map(text, ["375 P.2d 743", "60 Wash. 2d 775"])
    finally:
        pages._FIRST_GAP = old
    assert (pm.cite, pm.first_page, pm.last_page) == ("60 Wash. 2d 775", 775, 781)


def test_page_map_chooses_the_cite_by_its_own_run_not_by_extra_runs():
    # 476 U.S. 593's markers start at *595; a separate opinion repeats *594 *595. Those two
    # let 90 L. Ed. 2d 569 start a run, and the U.S. markers would then fit it as extra runs.
    text = "x " + _pages(range(595, 607)) + " *594 a *595 b " + _pages(range(607, 610))
    pm = page_map(text, ["476 U.S. 593", "90 L. Ed. 2d 569"])
    assert (pm.cite, pm.first_page, pm.last_page) == ("476 U.S. 593", 593, 609)


def test_page_map_tie_between_close_reporters_goes_to_the_cite_listed_first():
    # Markers *104 on fit 317 U.S. 102 and 63 S. Ct. 103 alike; the official cite, listed
    # first, keeps them (as before 2026-11-21).
    text = "Syllabus. " + _pages(range(104, 112))
    pm = page_map(text, ["317 U.S. 102", "63 S. Ct. 103", "87 L. Ed. 113"])
    assert (pm.cite, pm.first_page, pm.last_page) == ("317 U.S. 102", 102, 111)
