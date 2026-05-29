"""Tests for the Phase-1 audit checks, one per finding type."""

from __future__ import annotations

from wacite.check.audit import audit_citations
from wacite.models import FindingType
from wacite.parse.citations import parse_citations


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


def test_wrong_year_flagged(index):
    report = _audit("State v. Smith, 199 Wn.2d 1 (2019).", index)
    assert FindingType.YEAR_MISMATCH in _types(report)


def test_overruled_authority_flagged(index):
    report = _audit("State v. Jones, 150 Wn.2d 50 (2003).", index)
    assert FindingType.NEGATIVE_TREATMENT in _types(report)
    assert report.warning_count == 1


def test_parallel_mismatch_flagged(index):
    # "199 Wn.2d 1" -> Smith, but "999 P.3d 9" -> a different authority.
    report = _audit("State v. Smith, 199 Wn.2d 1, 999 P.3d 9 (2022).", index)
    assert FindingType.PARALLEL_MISMATCH in _types(report)


def test_pincite_reported_not_verified(index):
    report = _audit("State v. Smith, 199 Wn.2d 1, 5 (2022).", index)
    assert FindingType.PIN_NOT_VERIFIED in _types(report)
    # PIN_NOT_VERIFIED is informational, not an error.
    assert report.error_count == 0


def test_wash_reporter_variant_resolves(index):
    # Brief uses official "Wash. 2d"; corpus stored "Wn.2d" — should still hit.
    report = _audit("State v. Smith, 199 Wash. 2d 1 (2022).", index)
    assert report.citations_resolved == 1
    assert report.error_count == 0
