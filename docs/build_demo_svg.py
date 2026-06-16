#!/usr/bin/env python3
"""Render a REAL `wacite check` run into a terminal SVG (docs/demo.svg).

Builds a tiny in-process fixture index (the same shape as the test suite's),
audits examples/sample_motion.txt, and exports rich's own console render to SVG.
Nothing here is mocked — the findings are produced by the real audit pipeline.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from rich.console import Console

from wacite.check.audit import audit_citations
from wacite.check.report import render_console
from wacite.index.build import write_index
from wacite.index.store import CiteIndex
from wacite.parse.citations import parse_citations

REPO = Path(__file__).resolve().parents[1]

# A small fixture corpus (mirrors tests/conftest.py): canonical name-year cites
# with reporter cites in parallel_cites, plus one statute and one overruled case.
FIXTURE_ROWS = [
    {"authority_id": "a-smith", "authority_type": "case",
     "canonical_cite": "State v. Smith (Washington Supreme Court 2022)",
     "canonical_cite_normalized": "state v. smith (washington supreme court 2022)",
     "display_title": "State v. Smith", "year": 2022,
     "court_name": "Washington Supreme Court", "court_level": 1,
     "authority_status": "current", "parallel_cites": ["199 Wn.2d 1", "502 P.3d 1"]},
    {"authority_id": "a-jones", "authority_type": "case",
     "canonical_cite": "State v. Jones (Washington Supreme Court 2003)",
     "canonical_cite_normalized": "state v. jones (washington supreme court 2003)",
     "display_title": "State v. Jones", "year": 2003,
     "court_name": "Washington Supreme Court", "court_level": 1,
     "authority_status": "overruled", "parallel_cites": ["150 Wn.2d 50"]},
    {"authority_id": "a-other", "authority_type": "case",
     "canonical_cite": "State v. Other (Washington Court of Appeals 2010)",
     "canonical_cite_normalized": "state v. other (washington court of appeals 2010)",
     "display_title": "State v. Other", "year": 2010,
     "court_name": "Washington Court of Appeals", "court_level": 2,
     "authority_status": "current", "parallel_cites": ["999 P.3d 9"]},
    {"authority_id": "a-rcw", "authority_type": "statute",
     "canonical_cite": "RCW 9A.52.070", "canonical_cite_normalized": "rcw 9a.52.070",
     "display_title": "Criminal trespass in the first degree", "year": None,
     "court_name": None, "court_level": None,
     "authority_status": "current", "parallel_cites": []},
]


def main() -> None:
    motion = (REPO / "examples" / "sample_motion.txt").read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory() as d:
        index_path = Path(d) / "cite_index.sqlite"
        write_index(index_path, FIXTURE_ROWS)
        with CiteIndex(str(index_path)) as idx:
            citations = parse_citations(motion)
            report = audit_citations(citations, idx, source="sample_motion.txt")

    console = Console(record=True, width=100)
    console.print("$ wacite check sample_motion.txt", style="green")
    render_console(report, console)

    out = REPO / "docs" / "demo.svg"
    out.write_text(
        console.export_svg(title="wacite check — sample_motion.txt"),
        encoding="utf-8",
    )
    print("wrote", out)


if __name__ == "__main__":
    main()
