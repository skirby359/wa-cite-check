"""Tests for the Phase-1 audit checks, one per finding type."""

from __future__ import annotations

from wacite.check.audit import audit_citations
from wacite.models import FindingType
from wacite.parse.citations import parse_citations
from wacite.parse.shortform import expand_shortforms


def _audit(text, index):
    return audit_citations(parse_citations(text), index)


def _types(report):
    return {f.finding_type for f in report.findings}


def test_valid_cite_has_no_findings(index):
    report = _audit("State v. Smith, 199 Wn.2d 1 (2022).", index)
    assert report.citations_resolved == 1
    assert report.findings == []


def test_valid_statute_resolves(index):
    report = _audit("Under RCW 9A.52.070(1)(a), the defendant...", index)
    assert report.citations_resolved == 1
    assert report.error_count == 0


def test_fabricated_cite_flagged_not_found(index):
    report = _audit("State v. Nobody, 777 Wn.2d 777 (2020).", index)
    assert FindingType.NOT_FOUND in _types(report)
    assert report.error_count == 1


def test_wrong_name_flagged(index):
    report = _audit("State v. Wrongman, 199 Wn.2d 1 (2022).", index)
    assert FindingType.NAME_MISMATCH in _types(report)


def test_short_name_for_a_long_caption_is_not_a_mismatch(index):
    assert _audit("United States v. Roe, 500 F.2d 100 (9th Cir. 1971).", index).findings == []
    report = _audit("United States v. Doe, 500 F.2d 100 (9th Cir. 1971).", index)
    assert FindingType.NAME_MISMATCH in _types(report)
    report = _audit("State v. Smithson, 199 Wn.2d 1 (2022).", index)
    assert FindingType.NAME_MISMATCH in _types(report)


def test_wrong_year_flagged(index):
    report = _audit("State v. Smith, 199 Wn.2d 1 (2019).", index)
    assert FindingType.YEAR_MISMATCH in _types(report)


def test_overruled_authority_flagged(index):
    report = _audit("State v. Jones, 150 Wn.2d 50 (2003).", index)
    assert FindingType.NEGATIVE_TREATMENT in _types(report)
    assert report.warning_count == 1


def test_statute_mismarked_case_treatment_not_flagged(index):
    # A statute wrongly stamped with a case treatment ('superseded') must NOT be
    # reported as bad law — statutes are never overruled/superseded.
    report = _audit("Under RCW 26.16.030, community property is...", index)
    assert report.citations_resolved == 1
    assert FindingType.NEGATIVE_TREATMENT not in _types(report)


def test_repealed_statute_still_flagged(index):
    # 'repealed' is legitimate for a statute and must still be reported.
    report = _audit("See RCW 9A.52.900 for the savings clause.", index)
    assert FindingType.NEGATIVE_TREATMENT in _types(report)


def test_parallel_mismatch_flagged(index):
    # "199 Wn.2d 1" -> Smith, but "999 P.3d 9" -> a different authority.
    report = _audit("State v. Smith, 199 Wn.2d 1, 999 P.3d 9 (2022).", index)
    assert FindingType.PARALLEL_MISMATCH in _types(report)


def test_pincite_reported_not_verified_without_page_markers(index):
    # Jones's stored text has no page markers, so its pin can't be checked.
    report = _audit("State v. Jones, 150 Wn.2d 50, 55 (2003).", index)
    assert FindingType.PIN_NOT_VERIFIED in _types(report)
    # PIN_NOT_VERIFIED is informational, not an error.
    assert report.error_count == 0
    assert report.pins_checked == 0


def _audit_with_short_forms(text, index):
    full = parse_citations(text)
    return audit_citations(sorted(full + expand_shortforms(text, full), key=lambda c: c.start), index)


def test_pin_inside_the_marked_pages_passes(index):
    report = _audit_with_short_forms(
        "State v. Smith, 199 Wn.2d 1, 5 (2022). Id. at 7. Smith, 199 Wn.2d at 12-14.", index)
    assert report.findings == []
    assert report.pins_checked == 3


def test_pin_outside_the_marked_pages_is_a_warning(index):
    report = _audit_with_short_forms("State v. Smith, 199 Wn.2d 1, 45 (2022). Smith, 199 Wn.2d at 29-31.", index)
    out = [f for f in report.findings if f.finding_type is FindingType.PIN_OUT_OF_RANGE]
    assert len(out) == 2 and "1-30" in out[0].message
    assert report.error_count == 0 and report.warning_count == 2


def test_pin_in_an_unmarked_parallel_reporter_is_not_verified(index):
    # Smith's markers follow Wn.2d; a pin to its P.3d pages can't be checked.
    report = _audit("State v. Smith, 502 P.3d 1, 7 (2022).", index)
    f = [f for f in report.findings if f.finding_type is FindingType.PIN_NOT_VERIFIED]
    assert f and "199 wn.2d 1" in f[0].message
    assert report.pins_checked == 0


def test_cites_to_courts_outside_the_corpus_are_out_of_scope_not_errors(index):
    text = ("Doe v. Roe, 466 U.S. 668, 687 (1984); Poe v. Moe, 104 S. Ct. 2052 (1984); "
            "Ace v. Bay, 500 F.3d 1 (2d Cir. 2007); Cue v. Dee, 900 P.2d 1 (Or. 1995); "
            "Eve v. Fay, 100 F. Supp. 2d 5 (N.D. Cal. 2000).")
    report = _audit(text, index)
    assert [f.finding_type for f in report.findings] == [FindingType.OUT_OF_SCOPE] * 5
    assert "U.S. Supreme Court" in report.findings[0].message
    assert report.error_count == 0


def test_unclear_or_in_scope_courts_stay_not_found(index):
    report = _audit("Gus v. Hal, 500 F.3d 1 (9th Cir. 2007); Ivy v. Jon, 900 P.2d 1 (1995); "
                    "Kay v. Lou, 100 F. Supp. 2d 5 (W.D. Wash. 2000).", index)
    assert [f.finding_type for f in report.findings] == [FindingType.NOT_FOUND] * 3
    assert "Washington-related 9th Circuit" in report.findings[0].message
    assert report.error_count == 3


OPINION = ("Smith appeals. The facts are undisputed. *2 We hold that a warrantless search of the "
           "vehicle was unlawful [because no exception applied]. *3 Reversed and remanded with instructions.")


def _audit_quotes(text, index):
    return audit_citations(parse_citations(text), index, text=text,
                           page_source=lambda aid: OPINION if aid == "a-smith" else None)


def test_quote_on_the_pinned_page_passes(index):
    report = _audit_quotes("“a warrantless search of the vehicle was unlawful.” State v. Smith, 199 Wn.2d 1, 2 (2022).", index)
    assert report.findings == [] and report.quotes_checked == 1


def test_quote_on_another_page_is_flagged(index):
    report = _audit_quotes("“a warrantless search of the vehicle” State v. Smith, 199 Wn.2d 1, 3 (2022).", index)
    f = [f for f in report.findings if f.finding_type is FindingType.QUOTE_ELSEWHERE]
    assert f and "page 2" in f[0].message


def test_misquote_is_flagged_and_no_quote_means_no_quote_check(index):
    report = _audit_quotes("“a warrantless seizure of the truck was lawful” State v. Smith, 199 Wn.2d 1, 2 (2022).", index)
    assert FindingType.QUOTE_NOT_FOUND in _types(report)
    report = _audit_quotes("Searches need a warrant. State v. Smith, 199 Wn.2d 1, 2 (2022).", index)
    assert report.quotes_checked == 0 and report.findings == []


def test_wash_reporter_variant_resolves(index):
    # Brief uses official "Wash. 2d"; corpus stored "Wn.2d" — should still hit.
    report = _audit("State v. Smith, 199 Wash. 2d 1 (2022).", index)
    assert report.citations_resolved == 1
    assert report.error_count == 0
