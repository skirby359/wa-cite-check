"""Tests for citation component parsing and short-form resolution."""

from __future__ import annotations

from wacite.normalize import normalize_cite
from wacite.parse.citations import parse_citations
from wacite.parse.shortform import expand_shortforms


def test_parses_full_case_cite_components():
    cits = parse_citations("See State v. Smith, 199 Wn.2d 1, 5 (2022).")
    case = [c for c in cits if c.kind == "case"]
    assert len(case) == 1
    c = case[0]
    assert c.name == "State v. Smith"
    assert c.volume == "199"
    assert c.reporter == "Wn.2d"
    assert c.page == "1"
    assert c.pincite == "5"
    assert c.year == 2022
    assert c.lookup_key == "199 Wn.2d 1"


def test_parallel_cite_not_swallowed_as_pincite():
    cits = parse_citations("State v. Smith, 199 Wn.2d 1, 502 P.3d 1 (2022).")
    c = [c for c in cits if c.kind == "case"][0]
    assert c.pincite is None
    assert c.parallel == ["502 P.3d 1"]


def test_parses_rcw_with_subsection():
    cits = parse_citations("Under RCW 9A.52.070(1)(a), trespass is...")
    rcw = [c for c in cits if c.kind == "rcw"][0]
    assert rcw.raw == "RCW 9A.52.070(1)(a)"
    assert rcw.pincite == "(1)(a)"
    assert rcw.lookup_key == "RCW 9A.52.070"


def test_parses_court_rule_and_const():
    cits = parse_citations("Per CR 56 and Const. art. I, § 7, the motion fails.")
    kinds = {c.kind for c in cits}
    assert "rule" in kinds
    assert "const" in kinds


def test_reporter_normalization_folds_wash_and_wn():
    assert normalize_cite("199 Wash. 2d 1") == normalize_cite("199 Wn.2d 1")
    assert normalize_cite("199 Wn. App. 2d 5") == normalize_cite("199 Wash. App. 2d 5")


def test_shortform_id_resolves_to_antecedent():
    text = "State v. Smith, 199 Wn.2d 1 (2022). Id. at 7."
    full = parse_citations(text)
    short = expand_shortforms(text, full)
    assert len(short) == 1
    assert short[0].from_shortform
    assert short[0].lookup_key == "199 Wn.2d 1"
    assert short[0].pincite == "7"


def test_shortform_reporter_at_resolves():
    text = "State v. Smith, 199 Wn.2d 1 (2022). Later, 199 Wn.2d at 9."
    full = parse_citations(text)
    short = expand_shortforms(text, full)
    assert any(s.lookup_key == "199 Wn.2d 1" and s.pincite == "9" for s in short)
