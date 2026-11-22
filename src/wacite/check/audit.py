"""Phase-1 format/accuracy checks.

For every citation found in a document:
  1. Existence       — resolves to an authority in the corpus index
  2. Name match       — case name matches the stored display_title
  3. Year match       — parenthetical year matches the stored year
  4. Parallel cite    — every reporter cite given points to the same authority
  5. Authority status — not overruled / repealed / abrogated / etc.
  6. Pin-page         — inside the opinion's pages, per the star pagination of the
                        stored text (since 2026-11-15); with a page source, a
                        quotation before the cite is looked for on the pinned page.

A cite that resolves to nothing but plainly names a court the corpus does not hold
(the U.S. Supreme Court, another circuit or state) is reported as out of scope, not
as an error.

Each check is a pure function over (ParsedCitation, AuthorityRecord); audit()
wires them together against a CiteIndex.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from wacite.index.store import CiteIndex
from wacite.models import (
    AuditReport,
    AuthorityRecord,
    Finding,
    FindingType,
    FINDING_SEVERITY,
    ParsedCitation,
)
from wacite.normalize import names_match, normalize_cite
from wacite.pages import page_map, page_texts, parse_pin, quote_pages
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

# Case-law treatments apply ONLY to cases. A statute or regulation is never
# "overruled"/"abrogated"/"superseded" — it gets repealed or amended. Gating by
# authority type stops upstream mis-marking (e.g. a current statute stamped
# 'superseded' by noisy citation-graph extraction) from being reported to the
# user as bad law. 'repealed' stays broadly applicable (statutes get repealed).
_CASE_ONLY_STATUSES = {
    "overruled",
    "abrogated",
    "superseded",
    "questioned",
    "limited",
}


def _status_applies(authority_type: str | None, status: str) -> bool:
    """Whether a negative status is legitimate for this authority type."""
    if status in _CASE_ONLY_STATUSES:
        return authority_type == "case"
    return True


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
    if names_match(cit.name, auth.display_title):
        return None
    also = ""
    if auth.shared_with:
        others = "; ".join(f"'{t}'" for t in auth.shared_with[:3])
        more = f" and {len(auth.shared_with) - 3} more" if len(auth.shared_with) > 3 else ""
        also = f" (the corpus also lists this cite for {others}{more})"
    return _finding(
        cit,
        FindingType.NAME_MISMATCH,
        f"cite resolves to '{auth.display_title}' but the brief names it "
        f"'{cit.name}'{also}",
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
        holders = index.candidates(pc)
        if holders and auth.authority_id not in holders:
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
    if (
        auth.authority_status in NEGATIVE_STATUSES
        and _status_applies(auth.authority_type, auth.authority_status)
    ):
        return _finding(
            cit,
            FindingType.NEGATIVE_TREATMENT,
            f"'{auth.display_title or auth.canonical_cite}' has status "
            f"'{auth.authority_status}' — verify it is still good law before relying on it",
            auth,
        )
    return None


# -- courts the corpus does not hold --------------------------------------------------
# The corpus holds the Washington Supreme Court and Court of Appeals, the 9th Circuit
# (Washington-related cases only) and the W.D. and E.D. Wash. Only a positive sign of
# another court moves a not-found cite out of scope; an unclear one stays NOT_FOUND.
_SCOTUS_REPORTER = re.compile(r"^(?:U\.?\s*S\.?|S\.?\s*Ct\.|L\.?\s*Ed\.?(?:\s*2d)?)$")
_OTHER_CIRCUIT = re.compile(r"\b(?:1st|2d|2nd|3d|3rd|4th|5th|6th|7th|8th|10th|11th|D\.\s?C\.|Fed\.)\s*Cir\.")
_WA_DISTRICT = re.compile(r"\b[WE]\.\s?D\.\s?Wash")
_OTHER_DISTRICT = re.compile(r"(?:^|\s)(?:[NSEWMC]\.\s?D\.|D\.)\s?(?!Wash)[A-Z][a-z]")
_OTHER_STATE = re.compile(
    r"(?:^|\s)(?:Ala|Alaska|Ariz|Ark|Cal|Colo|Conn|Del|Fla|Ga|Haw|Idaho|Ill|Ind|Iowa|Kan|Ky|La|Me|Md|Mass"
    r"|Mich|Minn|Miss|Mo|Mont|Neb|Nev|N\.\s?H|N\.\s?J|N\.\s?M|N\.\s?Y|N\.\s?C|N\.\s?D|Ohio|Okla|Or|Pa"
    r"|R\.\s?I|S\.\s?C|S\.\s?D|Tenn|Tex|Utah|Vt|Va|W\.\s?Va|Wis|Wyo)\.?(?=\s|$)")
_NINTH = re.compile(r"\b9th\s*Cir\.")
_FEDERAL_REPORTER = re.compile(r"^F\.")


def _out_of_scope(cit: ParsedCitation) -> str | None:
    """Why a not-found case cite is outside the corpus, or None if it may be inside."""
    if cit.kind != "case":
        return None
    if cit.reporter and _SCOTUS_REPORTER.match(cit.reporter.strip()):
        # Since 2026-11-16 the corpus holds the Court's merits decisions (an index built
        # after that ingest resolves them); orders, cert denials and decisions newer than
        # the corpus still aren't there.
        return ("this U.S. Supreme Court cite isn't in the corpus, which holds at most the Court's "
                "merits decisions (not orders or cert denials, nor the newest decisions)")
    court = cit.court or ""
    if not court or _NINTH.search(court) or _WA_DISTRICT.search(court):
        return None
    if _OTHER_CIRCUIT.search(court):
        return f"the corpus holds no {court.strip()} opinions (federal appeals: 9th Circuit only)"
    if _OTHER_DISTRICT.search(court):
        return f"the corpus holds no {court.strip()} opinions (federal districts: W.D. and E.D. Wash. only)"
    if _OTHER_STATE.search(court):
        return f"the corpus holds no {court.strip()} opinions (state courts: Washington only)"
    return None


# -- pin pages and quotations -----------------------------------------------------------
# A quotation ending shortly before a citation is taken to be what the cite supports.
_QUOTE = re.compile(r"“([^”]{20,2000})”|\"([^\"]{20,2000})\"")
_QUOTE_WINDOW = 400

PageSource = Callable[[str], "str | None"]  # authority_id -> stored opinion text


def quote_before(text: str, cit: ParsedCitation, starts: list[int] | None = None) -> str | None:
    """The last quotation that ends within _QUOTE_WINDOW characters before ``cit``,
    unless another citation (one of ``starts``) begins between the quotation's end and
    ``cit``: then the quotation belongs to that one. A citation inside the quotation
    ("... without a CrR 3.5 hearing") doesn't count."""
    found = None
    for m in _QUOTE.finditer(text, max(0, cit.start - _QUOTE_WINDOW), cit.start):
        found = m
    if found is None or any(found.end() <= s < cit.start for s in starts or []):
        return None
    return found.group(1) or found.group(2)


def _check_pin(cit: ParsedCitation, auth: AuthorityRecord, index: CiteIndex) -> tuple[Finding | None, bool]:
    """(finding, checked). Checks the pin against the marked pages of the reporter
    the pin is given in; any other reporter's pages can't be checked."""
    pins = parse_pin(cit.pincite)
    pr = index.page_range(auth.authority_id)
    if pins is None or pr is None:
        why = "no page markers stored for this opinion" if pr is None else "pin not understood"
        return _finding(cit, FindingType.PIN_NOT_VERIFIED,
                        f"pin cite 'at {cit.pincite}' could not be verified ({why})", auth), False
    marked, first, last = pr
    if normalize_cite(cit.lookup_key) != marked:
        return _finding(cit, FindingType.PIN_NOT_VERIFIED,
                        f"pin cite 'at {cit.pincite}' could not be verified (the stored text marks the "
                        f"pages of {marked}, not {cit.lookup_key})", auth), False
    start, end = pins
    if start < first or end > last:
        return _finding(cit, FindingType.PIN_OUT_OF_RANGE,
                        f"pin 'at {cit.pincite}' is outside the opinion's pages, {first}-{last} "
                        f"(per the page markers in the stored text)", auth), True
    return None, True


def _check_quote(cit: ParsedCitation, auth: AuthorityRecord, quote: str, opinion: str) -> tuple[Finding | None, bool]:
    """(finding, checked): is the quotation on the pinned page? When the pinned page
    can't be located (no pin, no page markers, or another reporter's pages are
    marked), the quotation is still looked for anywhere in the opinion."""
    pm = page_map(opinion, list(auth.parallel_cites) + [auth.canonical_cite])
    pins = parse_pin(cit.pincite)
    short = quote if len(quote) <= 70 else quote[:67] + "..."
    if pm is None or pins is None or pm.cite_normalized != normalize_cite(cit.lookup_key):
        hits, found_all = quote_pages(quote, {0: opinion})
        if not hits:
            return _finding(cit, FindingType.QUOTE_NOT_FOUND,
                            f"quoted words \"{short}\" were not found in the stored opinion text "
                            f"(page not checked)", auth), True
        if not found_all:
            return _finding(cit, FindingType.QUOTE_NOT_FOUND,
                            f"part of the quotation \"{short}\" was not found in the stored opinion text "
                            f"(page not checked)", auth), True
        return None, True
    hits, found_all = quote_pages(quote, page_texts(opinion, pm))
    start, end = pins
    if not hits:
        return _finding(cit, FindingType.QUOTE_NOT_FOUND,
                        f"quoted words \"{short}\" were not found in the stored opinion text", auth), True
    if not any(start <= h <= end for h in hits):
        where = ", ".join(str(h) for h in hits[:3])
        return _finding(cit, FindingType.QUOTE_ELSEWHERE,
                        f"quoted words \"{short}\" start at page {where}, not at the pinned page "
                        f"{cit.pincite}", auth), True
    if not found_all:
        return _finding(cit, FindingType.QUOTE_NOT_FOUND,
                        f"part of the quotation \"{short}\" was not found in the stored opinion text", auth), True
    return None, True


def audit_citations(
    citations: list[ParsedCitation], index: CiteIndex, source: str = "", *,
    text: str | None = None, page_source: PageSource | None = None,
) -> AuditReport:
    """Run all Phase-1 checks over already-parsed citations.

    With the document ``text`` and a ``page_source`` (opinion text by authority id),
    a quotation just before a pinned case cite is also looked for on that page.
    """
    report = AuditReport(source=source, citations_found=len(citations))
    opinions: dict[str, str | None] = {}
    starts = [c.start for c in citations]

    for cit in citations:
        auth = index.lookup_by_cite(cit.lookup_key, name=cit.name if cit.kind == "case" else None)

        if auth is None:
            scope = _out_of_scope(cit)
            if scope:
                report.findings.append(_finding(
                    cit, FindingType.OUT_OF_SCOPE, f"not checked: {scope}"))
                continue
            suggestions = index.suggest_near(cit.lookup_key)
            hint = f" (did you mean: {', '.join(suggestions)}?)" if suggestions else ""
            note = ""
            if cit.kind == "case" and _NINTH.search(cit.court or "") and _FEDERAL_REPORTER.match(cit.reporter or ""):
                note = "; note the corpus holds only Washington-related 9th Circuit cases"
            report.findings.append(
                _finding(
                    cit,
                    FindingType.NOT_FOUND,
                    f"'{cit.raw}' does not resolve to any authority in the corpus"
                    f"{hint}{note}",
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

        # Check 6: the pin page, against the stored text's page markers.
        pin_finding = None
        if cit.pincite and cit.kind == "case":
            pin_finding, checked = _check_pin(cit, auth, index)
            report.pins_checked += checked
            if pin_finding:
                report.findings.append(pin_finding)
        # Check 7 (with a page source): a quotation just before a case cite, on the
        # pinned page, or anywhere in the opinion when its page can't be located. Skipped
        # after a pin outside the opinion's pages: that finding already says what's wrong.
        out_of_range = pin_finding is not None and pin_finding.finding_type is FindingType.PIN_OUT_OF_RANGE
        if cit.kind == "case" and text and page_source and not out_of_range:
            quote = quote_before(text, cit, starts)
            if quote:
                if auth.authority_id not in opinions:
                    opinions[auth.authority_id] = page_source(auth.authority_id)
                if opinions[auth.authority_id]:
                    qf, qchecked = _check_quote(cit, auth, quote, opinions[auth.authority_id])
                    report.quotes_checked += qchecked
                    if qf:
                        report.findings.append(qf)

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
    page_source: PageSource | None = None,
) -> AuditReport:
    """Extract, parse (incl. short forms), and audit a document end-to-end.

    When ``align`` is set, the optional Phase-2 substantive-alignment pass runs
    after the Phase-1 audit and appends advisory findings. With a ``page_source``
    (``--quotes``), quotations are checked against the pinned page.
    """
    text = extract_text(path)
    full = parse_citations(text)
    short = expand_shortforms(text, full)
    citations = sorted(full + short, key=lambda c: c.start)
    report = audit_citations(citations, index, source=str(path), text=text, page_source=page_source)
    if align:
        align_document(report, text, citations, index, **(align_opts or {}))
    return report
