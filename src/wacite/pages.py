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


@dataclass
class PageMap:
    """The reporter whose pages are marked, and where each page starts."""

    cite: str                       # e.g. "199 Wash. 2d 1" (as stored)
    cite_normalized: str            # normalize_cite(cite)
    first_page: int
    last_page: int
    breaks: list[tuple[int, int]]   # (page, char offset where it starts), page > first_page


def page_map(text: str | None, cites: list[str]) -> PageMap | None:
    """Match the star markers in ``text`` to one of the opinion's reporter cites.

    The cite whose page range holds the most markers wins. Markers are kept in
    increasing order; an out-of-order or far-jumping number is treated as noise
    (a footnote, a quoted cite). Returns None when no reporter cite has a marker.
    """
    if not text:
        return None
    marks = [(int(m.group(1)), m.start()) for m in STAR.finditer(text)]
    if not marks:
        return None
    best: tuple[str, int, list[tuple[int, int]]] | None = None
    for cite in cites or []:
        if not cite or "LEXIS" in cite or "WL" in cite:
            continue
        m = _CITE.match(cite)
        if not m:
            continue
        first = int(m.group(3))
        seq: list[tuple[int, int]] = []
        last = first
        for page, off in marks:
            if last < page <= last + _MAX_JUMP and page <= first + _MAX_PAGES:
                seq.append((page, off))
                last = page
        if seq and (best is None or len(seq) > len(best[2])):
            best = (cite.strip(), first, seq)
    if best is None:
        return None
    cite, first, seq = best
    return PageMap(cite=cite, cite_normalized=normalize_cite(cite), first_page=first,
                   last_page=seq[-1][0], breaks=seq)


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
