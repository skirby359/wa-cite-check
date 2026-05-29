"""End-to-end: parse a whole motion (DOCX + TXT) and confirm seeded errors."""

from __future__ import annotations

import pytest

from wacite.check.audit import audit_document
from wacite.models import FindingType

# A small motion containing: one valid cite, one valid statute, one overruled
# case, one wrong-year cite, one fabricated cite, and an Id. short form.
MOTION_TEXT = (
    "PLAINTIFF'S MOTION FOR SUMMARY JUDGMENT\n\n"
    "The standard is governed by CR 56. Trespass is defined in "
    "RCW 9A.52.070(1)(a). The controlling authority is State v. Smith, "
    "199 Wn.2d 1, 5 (2022). Id. at 7. Defendant relies on State v. Jones, "
    "150 Wn.2d 50 (2003), which no longer controls. Defendant also cites "
    "State v. Smith, 199 Wn.2d 1 (2019) for a different proposition, and the "
    "non-existent State v. Nobody, 777 Wn.2d 777 (2020).\n"
)


def _types(report):
    return {f.finding_type for f in report.findings}


def test_e2e_txt(tmp_path, index):
    p = tmp_path / "motion.txt"
    p.write_text(MOTION_TEXT, encoding="utf-8")
    report = audit_document(str(p), index)
    t = _types(report)
    assert FindingType.NEGATIVE_TREATMENT in t   # State v. Jones (overruled)
    assert FindingType.YEAR_MISMATCH in t        # Smith with wrong 2019
    assert FindingType.NOT_FOUND in t            # State v. Nobody
    # Valid Smith + RCW + CR 56 all resolve.
    assert report.citations_resolved >= 3


def test_e2e_docx(tmp_path, index):
    docx = pytest.importorskip("docx")
    path = tmp_path / "motion.docx"
    doc = docx.Document()
    for line in MOTION_TEXT.split("\n"):
        doc.add_paragraph(line)
    doc.save(str(path))

    report = audit_document(str(path), index)
    t = _types(report)
    assert FindingType.NEGATIVE_TREATMENT in t
    assert FindingType.NOT_FOUND in t
