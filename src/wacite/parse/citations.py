"""Citation extraction and component parsing.

The simple "is there a cite here" patterns are ported from wa-legal-ai
(src/walegal/pipeline/citations.py). The new work is parse_citations(), which
breaks a case reporter cite into name / volume / reporter / page / pincite /
parallel / year so the audit can verify the *name and year* attached to a real
reporter cite — not just that the cite exists.

The case-cite parser is intentionally case-sensitive (no re.IGNORECASE): party
names must start with a capital, which is how briefs are actually written, and
keeping case lets the name anchor work. Statute/rule/const patterns stay
case-insensitive where the corpus uses that.
"""

from __future__ import annotations

import re

from wacite.models import ParsedCitation

# ── Reporter alternation (longest forms first so "App. 2d" wins over "2d") ──
REPORTER = (
    r"Wn\.?\s*App\.?\s*2d|Wash\.?\s*App\.?\s*2d"
    r"|Wn\.?\s*App\.?|Wash\.?\s*App\.?"
    r"|Wn\.?\s*2d|Wash\.?\s*2d|Wn\.?|Wash\.?"
    r"|P\.?\s*3d|P\.?\s*2d"
    r"|F\.?\s*Supp\.?\s*3d|F\.?\s*Supp\.?\s*2d|F\.?\s*Supp\.?"
    r"|F\.?\s*4th|F\.?\s*3d|F\.?\s*2d"
    r"|S\.?\s*Ct\.|U\.?\s*S\.?|L\.?\s*Ed\.?\s*2d|L\.?\s*Ed\.?"
)

# A "party" is a capitalized word followed by more capitalized words or small
# connectors (of/the/and/…). Restricting the inter-word tokens to connectors or
# Capitalized words is what stops a name from swallowing a preceding clause:
# in "…the year in State v. Smith", the lowercase "in"/"year" break the chain,
# so party-1 is just "State".
_CONNECTOR = r"(?:of|the|and|for|ex|rel\.?|&)"
_PARTY = (
    rf"[A-Z][A-Za-z0-9.'&\-]*"
    rf"(?:\s+(?:{_CONNECTOR}|[A-Z][A-Za-z0-9.'&\-]*)){{0,6}}"
)
# Case name: "X v. Y" or an "In re / Matter of / Estate of / Ex parte" form.
_NAME = (
    rf"(?:"
    rf"(?:In re(?:\s+the)?|Matter of|Ex parte|Estate of)\s+{_PARTY}"
    rf"|{_PARTY}\s+v\.?\s+{_PARTY}"
    rf")"
)

# A case name appearing immediately before a reporter cite. Anchored at end of
# the left-context window; re.search returns the tightest such name.
NAME_BEFORE = re.compile(rf"({_NAME})\s*,?\s*$")
_NAME_WINDOW = 160  # chars of left context to scan for a preceding name

# Full case citation core. The post-page "tail" (a run of comma-separated
# chunks that may mix a pincite "5" with parallel cites "502 P.3d 1") is
# captured whole and split in Python — distinguishing pin vs parallel inside
# one regex invites backtracking that splits numbers like "502" into "50"+"2".
# The case name is found by a backward scan (see _name_before), not a greedy
# optional group, so a match never absorbs a preceding clause or citation.
CASE_FULL = re.compile(
    rf"(?P<vol>\d+)\s+(?P<rep>{REPORTER})\s+(?P<page>\d+)"
    rf"(?P<tail>(?:,[^,()]+)*)"
    rf"(?:\s*\((?P<court>[^)]*?)\s*(?P<year>(?:18|19|20)\d{{2}})\s*\))?"
)

_PARALLEL_RE = re.compile(rf"\d+\s+(?:{REPORTER})\s+\d+")
_PIN_RE = re.compile(r"\d+(?:\s*[-–]\s*\d+)?")

# Citation signals that can precede a party name ("See State v. Smith").
_SIGNAL_WORDS = frozenset({
    "see", "also", "cf", "accord", "contra", "but", "citing", "quoting",
    "compare", "eg", "id", "viz", "cited", "following", "rev'd", "aff'd",
})

# Statute / regulation / constitution / court-rule patterns (ported, case-insensitive).
RCW_PATTERN = re.compile(r"RCW\s+\d+[A-Z]?\.\d+\.\d+(?:\(\d+\)(?:\([a-z]\))*)?", re.I)
WAC_PATTERN = re.compile(r"WAC\s+\d+-\d+-\d+(?:\(\d+\)(?:\([a-z]\))*)?", re.I)
CONST_PATTERN = re.compile(
    r"Const\.\s+art\.\s+[IVXLC]+(?:,?\s*[§]\s*\d+(?:\([a-z]\))?)?", re.I
)
# Court rules are case-sensitive on purpose (see wa-legal-ai note: ER/CR/MAR
# collide with ordinary words when matched case-insensitively).
COURT_RULE_PATTERN = re.compile(
    r"\b(?:CR|CrR|CRLJ|CrRLJ|RAP|RALJ|ER|RPC|GR|MAR|SPR|JuCR|IRLJ)"
    r"\s+\d+(?:\.\d+)?\b"
)

# Trailing subsection on a statute cite, e.g. "(1)(a)".
_SUBSECTION = re.compile(r"(\(\d+\)(?:\([a-z]\))*)+$")


def _strip_signal(name: str | None) -> str | None:
    """Drop leading citation signal words from a captured case name."""
    if not name:
        return name
    tokens = name.split()
    while tokens and tokens[0].strip(".,'").lower() in _SIGNAL_WORDS:
        tokens.pop(0)
    return " ".join(tokens) or None


def _name_before(text: str, cite_start: int) -> tuple[str | None, int]:
    """Find a case name immediately preceding a reporter cite.

    Scans a bounded left-context window and returns (name, name_start_offset)
    where name_start_offset is the absolute position the name begins (so the
    caller can widen the citation span to include the name). Returns
    (None, cite_start) when no name abuts the cite.
    """
    window_start = max(0, cite_start - _NAME_WINDOW)
    left = text[window_start:cite_start]
    m = NAME_BEFORE.search(left)
    if not m:
        return None, cite_start
    name = _strip_signal(m.group(1).strip())
    if not name:
        return None, cite_start
    # Recompute start: the (possibly signal-stripped) name's offset in `text`.
    name_start = window_start + m.start(1) + (len(m.group(1)) - len(m.group(1).lstrip()))
    # Adjust for any signal words we stripped off the front.
    stripped_lead = m.group(1).strip()
    if name != stripped_lead and stripped_lead.endswith(name):
        name_start += len(stripped_lead) - len(name)
    return name, name_start


def _split_tail(tail: str | None) -> tuple[str | None, list[str]]:
    """Split a citation tail into (pincite, parallel reporter cites).

    Parallel cites (number+reporter+number) are extracted first; the first bare
    number left over is the pincite.
    """
    if not tail:
        return None, []
    parallels: list[str] = []
    spans: list[tuple[int, int]] = []
    for m in _PARALLEL_RE.finditer(tail):
        parallels.append(m.group(0).strip())
        spans.append((m.start(), m.end()))

    pin = None
    for m in _PIN_RE.finditer(tail):
        if any(m.start() < e and m.end() > s for s, e in spans):
            continue  # part of a parallel cite, not a pincite
        pin = re.sub(r"\s*([-–])\s*", r"\1", m.group(0))
        break
    return pin, parallels


def parse_citations(text: str) -> list[ParsedCitation]:
    """Find and parse every citation in text, returning non-overlapping results.

    Case reporter cites are fully parsed into components. Statute/rule/const
    cites carry their raw string and (for RCW/WAC) the subsection in ``pincite``.
    """
    results: list[ParsedCitation] = []
    used: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < ue and end > us for us, ue in used)

    # 1. Case reporter cites first (longest, most informative).
    for m in CASE_FULL.finditer(text):
        cite_start, end = m.start(), m.end()
        vol, rep, page = m.group("vol"), m.group("rep"), m.group("page")
        name, name_start = _name_before(text, cite_start)
        start = name_start if name else cite_start
        if overlaps(start, end):
            continue
        pin, parallel = _split_tail(m.group("tail"))
        results.append(
            ParsedCitation(
                raw=text[start:end].strip(),
                kind="case",
                start=start,
                end=end,
                name=name,
                volume=vol,
                reporter=rep.strip(),
                page=page,
                pincite=pin,
                year=int(m.group("year")) if m.group("year") else None,
                parallel=parallel,
                lookup_key=f"{vol} {rep} {page}",
            )
        )
        used.append((start, end))

    # 2. Statutes, regulations, constitution, court rules.
    for kind, pat in (
        ("rcw", RCW_PATTERN),
        ("wac", WAC_PATTERN),
        ("const", CONST_PATTERN),
        ("rule", COURT_RULE_PATTERN),
    ):
        for m in pat.finditer(text):
            start, end = m.start(), m.end()
            if overlaps(start, end):
                continue
            raw = m.group(0).strip()
            sub = None
            lookup = raw
            if kind in ("rcw", "wac"):
                sm = _SUBSECTION.search(raw)
                if sm:
                    sub = sm.group(0)
                    # Look up the base statute (corpus indexes base, not subsection).
                    lookup = raw[: sm.start()].strip()
            results.append(
                ParsedCitation(
                    raw=raw,
                    kind=kind,
                    start=start,
                    end=end,
                    pincite=sub,
                    lookup_key=lookup,
                )
            )
            used.append((start, end))

    results.sort(key=lambda c: c.start)
    return results
