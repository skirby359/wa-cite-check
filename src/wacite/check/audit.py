"""Phase-1 format/accuracy checks.

For every citation found in a document:
  1. Existence       — resolves to an authority in the corpus index
  2. Name match       — case name matches the stored display_title
  3. Year match       — parenthetical year matches the stored year
  4. Parallel cite    — every reporter cite given points to the same authority
  5. Authority status — not overruled / repealed / abrogated / etc.
  6. Pin-page         — reported as not-verified (no star pagination in corpus)

Each check is a pure function over (ParsedCitation, AuthorityRecord); audit()
wires them together against a CiteIndex.
"""

from __future__ import annotations

from wacite.index.store import CiteIndex
from wacite.models import (
    AuditReport,
    AuthorityRecord,
    Finding,
    FindingType,
    FINDING_SEVERITY,
    ParsedCitation,
    Severity,
)
from wacite.normalize import normalize_case_name
from wacite.parse.citations import parse_citations
from wacite.parse.document import extract_text
from wacite.parse.shortform import expand_shortforms

# Statuses that mean "still on the books but no longer reliable as cited".
NEGATIVE_STATUSES = {
    "overruled",
    "repealed",
    "abrogated",
    "superseded",
    "questioned",
    "limited",
}


def _finding(cit: ParsedCitation, ftype: FindingType, msg: str,
             authority: AuthorityRecord | None = None) -> Finding:
    return Finding(
        citation=cit,
        finding_type=ftype,
        severity=FINDING_SEVERITY[ftype],
        message=msg,
        authority=authority,
    )


def _check_name(cit: ParsedCitation, auth: AuthorityRecord) -> Finding | None:
    """Check 2: the case name written matches the stored display_title."""
    if cit.kind != "case" or not cit.name or not auth.display_title:
        return None
    got = normalize_case_name(cit.name)
    want = normalize_case_name(auth.display_title)
    if not got or not want:
        return None
    # Accept exact match or a containment either way (short names, "et al.").
    if got == want or got in want or want in got:
        return None
    return _finding(
        cit,
        FindingType.NAME_MISMATCH,
        f"cite resolves to '{auth.display_title}' but the brief names it "
        f"'{cit.name}'",
        auth,
    )


def _check_year(cit: ParsedCitation, auth: AuthorityRecord) -> Finding | None:
    """Check 3: the parenthetical year matches the stored decision year."""
    if cit.kind != "case" or cit.year is None or auth.year is None:
        return None
    if cit.year == auth.year:
        return None
    return _finding(
        cit,
        FindingType.YEAR_MISMATCH,
        f"brief gives year {cit.year} but the corpus records {auth.year} "
        f"for '{auth.display_title or auth.canonical_cite}'",
        auth,
    )


def _check_parallel(cit: ParsedCitation, auth: AuthorityRecord,
                    index: CiteIndex) -> Finding | None:
    """Check 4: every parallel reporter cite resolves to the same authority."""
    for pc in cit.parallel:
        other = index.resolve(pc)
        if other and other != auth.authority_id:
            return _finding(
                cit,
                FindingType.PARALLEL_MISMATCH,
                f"parallel cite '{pc}' resolves to a different authority than "
                f"'{cit.lookup_key}'",
                auth,
            )
    return None


def _check_status(cit: ParsedCitation, auth: AuthorityRecord) -> Finding | None:
    """Check 5: cited authority is still good law."""
    if auth.authority_status in NEGATIVE_STATUSES:
        return _finding(
            cit,
            FindingType.NEGATIVE_TREATMENT,
            f"'{auth.display_title or auth.canonical_cite}' has status "
            f"'{auth.authority_status}' — verify it is still good law before relying on it",
            auth,
        )
    return None


def audit_citations(
    citations: list[ParsedCitation], index: CiteIndex, source: str = ""
) -> AuditReport:
    """Run all Phase-1 checks over already-parsed citations."""
    report = AuditReport(source=source, citations_found=len(citations))

    for cit in citations:
        auth = index.lookup_by_cite(cit.lookup_key)

        if auth is None:
            suggestions = index.suggest_near(cit.lookup_key)
            hint = f" (did you mean: {', '.join(suggestions)}?)" if suggestions else ""
            report.findings.append(
                _finding(
                    cit,
                    FindingType.NOT_FOUND,
                    f"'{cit.raw}' does not resolve to any authority in the corpus"
                    f"{hint}",
                )
            )
            continue

        report.citations_resolved += 1

        for check in (
            _check_name(cit, auth),
            _check_year(cit, auth),
            _check_parallel(cit, auth, index),
            _check_status(cit, auth),
        ):
            if check is not None:
                report.findings.append(check)

        # Check 6: pin-page can't be verified (no star pagination in corpus).
        if cit.pincite and cit.kind == "case":
            report.findings.append(
                _finding(
                    cit,
                    FindingType.PIN_NOT_VERIFIED,
                    f"pin cite 'at {cit.pincite}' could not be verified "
                    f"(corpus has no page-level mapping)",
                    auth,
                )
            )

    return report


def align_document(
    report: AuditReport,
    text: str,
    citations: list[ParsedCitation],
    index: CiteIndex,
    **align_opts,
) -> AuditReport:
    """Append Phase-2 substantive-alignment findings to an existing report.

    Purely additive — Phase-1 findings are untouched. Imported lazily so the
    heavy alignment dependencies are only required when ``--align`` is used.
    """
    from wacite.align.judge import align_citations

    report.findings.extend(align_citations(citations, text, index, **align_opts))
    return report


def audit_document(
    path: str,
    index: CiteIndex,
    *,
    align: bool = False,
    align_opts: dict | None = None,
) -> AuditReport:
    """Extract, parse (incl. short forms), and audit a document end-to-end.

    When ``align`` is set, the optional Phase-2 substantive-alignment pass runs
    after the Phase-1 audit and appends advisory findings.
    """
    text = extract_text(path)
    full = parse_citations(text)
    short = expand_shortforms(text, full)
    citations = sorted(full + short, key=lambda c: c.start)
    report = audit_citations(citations, index, source=str(path))
    if align:
        align_document(report, text, citations, index, **(align_opts or {}))
    return report
