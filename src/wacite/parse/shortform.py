"""Resolve short-form citations back to their antecedent full cite.

Briefs cite a case in full once, then refer back with short forms:
  - "Id." / "Id. at 5"            -> the immediately preceding citation
  - "199 Wn.2d at 5"              -> a prior full cite with the same volume+reporter
  - "Smith, supra" / "Smith at 5" -> a prior full cite whose name matches

Each resolved short form becomes a ParsedCitation flagged ``from_shortform``
that copies the antecedent's lookup_key/name/year, so the audit can still
account for it (and confirm its antecedent resolves). This runs after
parse_citations() and only adds non-overlapping spans.
"""

from __future__ import annotations

import re

from wacite.models import ParsedCitation
from wacite.normalize import normalize_case_name
from wacite.parse.citations import REPORTER, _NAME

ID_PATTERN = re.compile(r"\bId\.(?:\s+at\s+(?P<pin>\d+(?:[-–]\d+)?))?", re.I)
REPORTER_SHORT = re.compile(
    rf"(?P<vol>\d+)\s+(?P<rep>{REPORTER})\s+at\s+(?P<pin>\d+(?:[-–]\d+)?)"
)
NAME_SHORT = re.compile(
    rf"(?P<name>{_NAME})(?:,?\s*supra)?(?:,?\s*at\s+(?P<pin>\d+(?:[-–]\d+)?))"
)


def _antecedent(full_cites: list[ParsedCitation], pos: int) -> ParsedCitation | None:
    """The full case cite that ends closest before ``pos``."""
    best = None
    for c in full_cites:
        if c.kind == "case" and c.end <= pos and (best is None or c.end > best.end):
            best = c
    return best


def expand_shortforms(
    text: str, full_cites: list[ParsedCitation]
) -> list[ParsedCitation]:
    """Return short-form citations resolved to their antecedents."""
    if not full_cites:
        return []

    spans = [(c.start, c.end) for c in full_cites]

    def overlaps(start: int, end: int) -> bool:
        return any(start < ue and end > us for us, ue in spans)

    out: list[ParsedCitation] = []

    def add(start: int, end: int, raw: str, antecedent: ParsedCitation, pin: str | None):
        out.append(
            ParsedCitation(
                raw=raw,
                kind="case",
                start=start,
                end=end,
                name=antecedent.name,
                volume=antecedent.volume,
                reporter=antecedent.reporter,
                page=antecedent.page,
                pincite=pin,
                year=antecedent.year,
                lookup_key=antecedent.lookup_key,
                from_shortform=True,
            )
        )
        spans.append((start, end))

    # 1. "199 Wn.2d at 5" — match volume+reporter to a prior full cite.
    for m in REPORTER_SHORT.finditer(text):
        if overlaps(m.start(), m.end()):
            continue
        vol, rep = m.group("vol"), m.group("rep")
        ante = None
        for c in full_cites:
            if (
                c.kind == "case"
                and c.end <= m.start()
                and c.volume == vol
                and c.reporter
                and c.reporter.replace(" ", "") == rep.replace(" ", "")
                and (ante is None or c.end > ante.end)
            ):
                ante = c
        if ante:
            add(m.start(), m.end(), m.group(0).strip(), ante, m.group("pin"))

    # 2. "Name, supra" / "Name at 5" — match normalized name to a prior full cite.
    for m in NAME_SHORT.finditer(text):
        if overlaps(m.start(), m.end()):
            continue
        target = normalize_case_name(m.group("name"))
        ante = None
        for c in full_cites:
            if c.kind == "case" and c.name and c.end <= m.start():
                cn = normalize_case_name(c.name)
                if cn == target or cn.endswith(target) or target.endswith(cn):
                    if ante is None or c.end > ante.end:
                        ante = c
        if ante:
            add(m.start(), m.end(), m.group(0).strip(), ante, m.group("pin"))

    # 3. "Id." — the immediately preceding case citation.
    for m in ID_PATTERN.finditer(text):
        if overlaps(m.start(), m.end()):
            continue
        ante = _antecedent(full_cites, m.start())
        if ante:
            add(m.start(), m.end(), m.group(0).strip(), ante, m.group("pin"))

    out.sort(key=lambda c: c.start)
    return out
