"""Page mapping from star pagination in stored opinion text.

CourtListener opinion text marks where each reporter page begins with a star
marker: "... the trial court erred. *163 We therefore ...". The markers follow
one reporter per opinion, usually the official one (Wash. 2d, Wash. App.,
F.3d), sometimes the regional (P.2d/P.3d). Measured 2026-11-15 on long opinions
with a reporter cite: Washington Supreme Court 98%, Court of Appeals 90%,
9th Circuit 54%, the districts nearly all.

Both the index build (first/last page per opinion) and the quote check (text of
one page) use ``page_map`` here, so they always agree on which reporter is
marked and where its pages break.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

from wacite.normalize import normalize_cite

# "*163" not inside a word, not a second-reporter "**163", not "*1,234" money.
STAR = re.compile(r"(?<![\w*])\*(\d{1,5})(?![\d,])")
_CITE = re.compile(r"^\s*(\d+)\s+(.+?)\s+(\d+)\s*$")
_MAX_PAGES = 600  # page markers further than this past the first page are noise
_MAX_JUMP = 5     # a marker more than this past the previous one is noise
# The first marker may be this far past the first page: an official reporter's syllabus,
# headnotes and counsel can fill several pages before the opinion's text begins (172 Wn.2d
# 702's first marker is *709). Until 2026-11-21 it was _MAX_JUMP.
_FIRST_GAP = 25
# A separate opinion stored after the lead one may restart lower ("... *728" then "*711
# *712 ..."). A run of at least this many markers outside the lead's chain is kept as its
# own pages; shorter ones are noise (a footnote, a cross-reference).
_MIN_RUN = 3


@dataclass
class PageMap:
    """The reporter whose pages are marked, and where each page starts."""

    cite: str                       # e.g. "199 Wash. 2d 1" (as stored)
    cite_normalized: str            # normalize_cite(cite)
    first_page: int
    last_page: int
    # (page, char offset where it starts), in text order, page > first_page. Pages
    # increase except where a separate opinion's pages start over (see _MIN_RUN).
    breaks: list[tuple[int, int]]


def _chain(marks: list[tuple[int, int]], first: int) -> list[int]:
    """Indexes into ``marks`` of the longest run of increasing pages, each at most
    _MAX_JUMP past the one before, starting at most _FIRST_GAP past ``first``.
    Markers in between that don't fit are skipped. Ties go to the run that ends
    earliest in the text (the lead opinion's)."""
    length: list[int] = [0] * len(marks)
    parent: list[int] = [-1] * len(marks)
    best_at: dict[int, int] = {}  # page -> index of the longest run ending on it so far
    top = -1
    for i, (page, _off) in enumerate(marks):
        if not first < page <= first + _MAX_PAGES:
            continue
        n, p = (1, -1) if page <= first + _FIRST_GAP else (0, -1)
        for prev in range(page - _MAX_JUMP, page):
            j = best_at.get(prev)
            if j is not None and length[j] + 1 > n:
                n, p = length[j] + 1, j
        if n == 0:
            continue
        length[i], parent[i] = n, p
        if page not in best_at or n > length[best_at[page]]:
            best_at[page] = i
        if top < 0 or n > length[top]:
            top = i
    out: list[int] = []
    while top >= 0:
        out.append(top)
        top = parent[top]
    return out[::-1]


def _runs(marks: list[tuple[int, int]], skip: set[int], first: int) -> list[int]:
    """Indexes of markers outside ``skip`` that form runs of at least _MIN_RUN
    increasing pages (a separate opinion whose pages start over)."""
    kept: list[int] = []
    run: list[int] = []
    for i, (page, _off) in enumerate(marks):
        if i in skip or not first < page <= first + _MAX_PAGES:
            continue
        if run and 0 < page - marks[run[-1]][0] <= _MAX_JUMP:
            run.append(i)
            continue
        if len(run) >= _MIN_RUN:
            kept.extend(run)
        run = [i]
    if len(run) >= _MIN_RUN:
        kept.extend(run)
    return kept


def page_map(text: str | None, cites: list[str]) -> PageMap | None:
    """Match the star markers in ``text`` to one of the opinion's reporter cites.

    For each cite, the longest run of increasing markers (``_chain``), plus any
    other runs of at least _MIN_RUN markers (``_runs``): a separate opinion stored
    after the lead one can start over at a lower page. Any other marker is noise
    (a footnote, a cross-reference), except that one up to _MAX_JUMP pages past the
    last page kept extends the last page ("*386 *389 *387 *388": the run keeps 387
    and 388, and 389 is the last page). The cite with the longest run wins (runs of
    other cites don't count: as runs, 476 U.S. 593's markers also fit 90 L. Ed. 2d
    569). On a tie, a cite whose first marker is at most _MAX_JUMP pages past its first
    page beats one further off, since one reporter's markers can fall inside another's
    range (60 Wn.2d 775's *776 is also 33 pages into 375 P.2d 743); otherwise the cite
    listed first wins (317 U.S. 102 over 63 S. Ct. 103, whose ranges differ by a page).
    Returns None when no reporter cite has a marker.
    """
    if not text:
        return None
    marks = [(int(m.group(1)), m.start()) for m in STAR.finditer(text)]
    if not marks:
        return None
    best: tuple[tuple[int, int], str, int, list[int]] | None = None
    for cite in cites or []:
        if not cite or "LEXIS" in cite or "WL" in cite:
            continue
        m = _CITE.match(cite)
        if not m:
            continue
        first = int(m.group(3))
        chain = _chain(marks, first)
        if not chain:
            continue
        score = (len(chain), marks[chain[0]][0] - first <= _MAX_JUMP)
        if best is None or score > best[0]:
            best = (score, cite.strip(), first, chain)
    if best is None:
        return None
    _score, cite, first, chain = best
    kept = sorted(chain + _runs(marks, set(chain), first))
    seq = [marks[i] for i in kept]
    last = max(p for p, _ in seq)
    taken = set(kept)
    for page in sorted(p for i, (p, _) in enumerate(marks) if i not in taken):
        if last < page <= last + _MAX_JUMP:
            last = page
    return PageMap(cite=cite, cite_normalized=normalize_cite(cite), first_page=first,
                   last_page=last, breaks=seq)


def page_texts(text: str, pm: PageMap) -> dict[int, str]:
    """Text of each marked page. Text before the first marker is the first page."""
    out: dict[int, str] = {}
    starts = [(pm.first_page, 0)] + pm.breaks
    for i, (page, off) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(text)
        out[page] = out.get(page, "") + text[off:end]
    return out


_PIN = re.compile(r"^\s*(\d+)(?:\s*[-–]\s*(\d+))?")


def parse_pin(pin: str | None) -> tuple[int, int] | None:
    """'163' -> (163, 163); '163-64' -> (163, 164); '163-175' -> (163, 175)."""
    if not pin:
        return None
    m = _PIN.match(pin)
    if not m:
        return None
    start = int(m.group(1))
    if not m.group(2):
        return start, start
    tail = m.group(2)
    end = int(tail)
    if len(tail) < len(m.group(1)):  # abbreviated end: 163-64
        end = int(m.group(1)[: len(m.group(1)) - len(tail)] + tail)
    return (start, end) if end >= start else (start, start)


# -- quotes ---------------------------------------------------------------------------

_SPLIT = re.compile(r"\[[^\]]*\]|\.\s?\.\s?\.|…")
# "[t]he": a changed letter inside a word is part of the word, not an insertion.
_LETTER_ALTERATION = re.compile(r"\[([A-Za-z])\](?=[A-Za-z])")
_NON_WORD = re.compile(r"[^a-z0-9]+")
MIN_FRAGMENT_WORDS = 4


def _norm(s: str) -> str:
    """Lowercase words only: star markers, punctuation and curly quotes dropped."""
    s = STAR.sub(" ", s.lower().replace("’", "'").replace("‘", "'"))
    return _NON_WORD.sub(" ", s).strip()


def quote_fragments(quote: str) -> list[str]:
    """Pieces of a quotation to look for: split at ellipses and [alterations],
    keeping pieces of at least MIN_FRAGMENT_WORDS words."""
    out = []
    for part in _SPLIT.split(_LETTER_ALTERATION.sub(r"\1", quote)):
        n = _norm(part)
        if len(n.split()) >= MIN_FRAGMENT_WORDS:
            out.append(n)
    return out


def quote_pages(quote: str, pages: dict[int, str]) -> tuple[list[int], bool]:
    """Pages on which the quotation's first fragment starts, and whether every
    fragment occurs somewhere in the opinion. The pages are searched as one text,
    so a quotation that runs across a page break is found on the page it starts."""
    frags = quote_fragments(quote)
    if not frags:
        return [], False
    starts: list[tuple[int, int]] = []  # (offset in whole, page)
    parts: list[str] = []
    pos = 1
    for p in sorted(pages):
        n = _norm(pages[p])
        starts.append((pos, p))
        parts.append(n)
        pos += len(n) + 1
    whole = " " + " ".join(parts) + " "
    found_all = all(f" {f} " in whole for f in frags)
    hits: list[int] = []
    i = whole.find(f" {frags[0]} ")
    while i != -1:
        page = starts[bisect.bisect_right([s for s, _ in starts], i + 1) - 1][1]
        if page not in hits:
            hits.append(page)
        i = whole.find(f" {frags[0]} ", i + 1)
    return hits, found_all
