"""Citation/name normalization helpers.

Ported from wa-legal-ai so the SQLite index keys match exactly:
  - cite normalization mirrors CitationResolver._normalize_cite
    (src/walegal/pipeline/retrieve.py:74)
  - case-name normalization mirrors verify._normalize_case_name
    (src/walegal/pipeline/verify.py:52)
Keeping these identical guarantees a cite we extract resolves against the
same key the corpus stored.
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")
_PAREN_TAIL = re.compile(r"\s*\(.*?\)\s*$")

# Reporter canonicalization. The corpus stores parallel cites in whatever form
# CourtListener supplied (often the official "Wash. 2d"), while briefs usually
# use Bluebook "Wn.2d". We fold both to one token so lookups hit. Each rule
# requires a following series marker (2d / app / supp), so a bare "Washington"
# inside a party name is never rewritten. Applied to reporter cites and
# parallel cites only — never to free document text. Order matters: the most
# specific (……app. 2d, ……supp. 3d) must come before the shorter forms.
_REPORTER_CANON: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bwash(?:ington)?\.?\s*app\.?\s*2d\b"), "wn. app. 2d"),
    (re.compile(r"\bwn\.?\s*app\.?\s*2d\b"), "wn. app. 2d"),
    (re.compile(r"\bwash(?:ington)?\.?\s*app\.?\b"), "wn. app."),
    (re.compile(r"\bwn\.?\s*app\.?\b"), "wn. app."),
    (re.compile(r"\bwash(?:ington)?\.?\s*2d\b"), "wn.2d"),
    (re.compile(r"\bwn\.?\s*2d\b"), "wn.2d"),
    (re.compile(r"\bp\.?\s*3d\b"), "p.3d"),
    (re.compile(r"\bp\.?\s*2d\b"), "p.2d"),
    (re.compile(r"\bf\.?\s*supp\.?\s*3d\b"), "f. supp. 3d"),
    (re.compile(r"\bf\.?\s*supp\.?\s*2d\b"), "f. supp. 2d"),
    (re.compile(r"\bf\.?\s*supp\.?\b"), "f. supp."),
    (re.compile(r"\bf\.?\s*4th\b"), "f.4th"),
    (re.compile(r"\bf\.?\s*3d\b"), "f.3d"),
    (re.compile(r"\bf\.?\s*2d\b"), "f.2d"),
]


def _canonicalize_reporters(s: str) -> str:
    for pat, repl in _REPORTER_CANON:
        s = pat.sub(repl, s)
    return s


def normalize_cite(cite: str) -> str:
    """Normalize a citation string for index lookup.

    Mirrors the corpus resolver (lowercase, collapse whitespace, strip trailing
    period) and additionally canonicalizes WA/Pacific/Federal reporter tokens so
    "Wn.2d" and "Wash. 2d" resolve to the same key. Build and check use this
    same function, so keys stay consistent.
    """
    s = cite.strip().lower()
    s = _WS.sub(" ", s)
    s = _canonicalize_reporters(s)
    s = _WS.sub(" ", s)
    s = s.rstrip(".")
    return s


def normalize_case_name(name: str) -> str:
    """Normalize a case name for fuzzy comparison.

    Drops a trailing court/year parenthetical, collapses whitespace, and
    removes punctuation so "State v. Smith," and "State v Smith" compare equal.
    """
    name = _PAREN_TAIL.sub("", name)
    name = _WS.sub(" ", name).strip().lower()
    name = name.replace(".", "").replace(",", "")
    return name
