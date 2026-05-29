"""Tests for the Phase-2 substantive-alignment pass.

All wa-legal-ai touch points (LLM provider, embedding model, content-support
fallback, corpus Postgres) are monkeypatched, so these run with no model, no
database, and without the ``walegal`` package installed.
"""

from __future__ import annotations

import pytest

from wacite.align import corpus, judge
from wacite.check.audit import audit_document
from wacite.models import FindingType, Severity
from wacite.parse.citations import parse_citations

# A citation that resolves in the fixture index ("199 Wn.2d 1" -> State v. Smith),
# with the proposition stated in the sentence before the cite.
MOTION = (
    "The State must prove every element beyond a reasonable doubt. "
    "State v. Smith, 199 Wn.2d 1 (2022). The burden never shifts to the defense."
)


class _DummyConn:
    def close(self) -> None:  # pragma: no cover - trivial
        pass


def _write(tmp_path, text=MOTION):
    doc = tmp_path / "motion.txt"
    doc.write_text(text, encoding="utf-8")
    return str(doc)


@pytest.fixture
def stub_corpus(monkeypatch):
    """No embedding model, a dummy connection, and a canned supporting passage."""
    monkeypatch.setattr(judge, "_load_embed_model", lambda: None)
    monkeypatch.setattr(corpus, "connect", lambda dsn=None: _DummyConn())
    monkeypatch.setattr(
        corpus,
        "fetch_relevant_passages",
        lambda conn, aid, qemb, k=3: [
            corpus.Passage(
                text="A defendant is presumed innocent; the State bears the burden "
                "of proving every element beyond a reasonable doubt."
            )
        ],
    )


def test_align_supported_produces_no_finding(index, stub_corpus, monkeypatch, tmp_path):
    monkeypatch.setattr(judge, "_resolve_provider", lambda p, u: "ollama")
    monkeypatch.setattr(
        judge, "_llm_chat",
        lambda **k: "VERDICT: SUPPORTED\nREASON: The excerpt states the standard.",
    )
    report = audit_document(
        _write(tmp_path), index, align=True, align_opts={"llm_url": "http://x"}
    )
    align_types = {FindingType.UNSUPPORTED_PROPOSITION, FindingType.WEAK_SUPPORT}
    assert not any(f.finding_type in align_types for f in report.findings)


def test_align_unsupported_warns(index, stub_corpus, monkeypatch, tmp_path):
    monkeypatch.setattr(judge, "_resolve_provider", lambda p, u: "ollama")
    monkeypatch.setattr(
        judge, "_llm_chat",
        lambda **k: "VERDICT: UNSUPPORTED\nREASON: The excerpt is about an unrelated rule.",
    )
    report = audit_document(
        _write(tmp_path), index, align=True, align_opts={"llm_url": "http://x"}
    )
    flagged = [f for f in report.findings if f.finding_type == FindingType.UNSUPPORTED_PROPOSITION]
    assert len(flagged) == 1
    assert flagged[0].severity is Severity.WARNING
    assert "State v. Smith" in flagged[0].message
    assert flagged[0].rationale  # the judge's reason is carried through
    # Advisory only: an UNSUPPORTED finding must not raise the exit code.
    assert report.error_count == 0


def test_align_partial_is_info(index, stub_corpus, monkeypatch, tmp_path):
    monkeypatch.setattr(judge, "_resolve_provider", lambda p, u: "ollama")
    monkeypatch.setattr(
        judge, "_llm_chat",
        lambda **k: "VERDICT: PARTIAL\nREASON: Related but does not fully establish it.",
    )
    report = audit_document(
        _write(tmp_path), index, align=True, align_opts={"llm_url": "http://x"}
    )
    flagged = [f for f in report.findings if f.finding_type == FindingType.WEAK_SUPPORT]
    assert len(flagged) == 1
    assert flagged[0].severity is Severity.INFO


def test_align_embedding_fallback_when_no_llm(index, stub_corpus, monkeypatch, tmp_path):
    # resolve_provider defaults to ollama, but with no llm_url it is not usable,
    # so we route through the embedding-only content-support check.
    monkeypatch.setattr(judge, "_resolve_provider", lambda p, u: "ollama")

    class _FakeCheck:
        def __init__(self):
            self.content_supports = "unchecked"

    monkeypatch.setattr(judge, "_make_check", lambda raw, aid: _FakeCheck())
    monkeypatch.setattr(judge, "_shim_passage", lambda auth, text: text)

    def fake_fallback(checks, motion_text, passages_by_authority):
        for c in checks:
            c.content_supports = "NO"

    monkeypatch.setattr(judge, "_embedding_fallback", fake_fallback)

    report = audit_document(_write(tmp_path), index, align=True, align_opts={})
    flagged = [f for f in report.findings if f.finding_type == FindingType.UNSUPPORTED_PROPOSITION]
    assert len(flagged) == 1
    assert "embedding similarity only" in flagged[0].message


def test_align_off_by_default_keeps_phase1_only(index, monkeypatch, tmp_path):
    # Without --align, no alignment code runs and no corpus connection is opened.
    def _boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("alignment ran without --align")

    monkeypatch.setattr(corpus, "connect", _boom)
    report = audit_document(_write(tmp_path), index)
    align_types = {FindingType.UNSUPPORTED_PROPOSITION, FindingType.WEAK_SUPPORT}
    assert not any(f.finding_type in align_types for f in report.findings)


def test_extract_proposition_includes_lead_sentence(index):
    case = next(c for c in parse_citations(MOTION) if c.kind == "case")
    prop = judge.extract_proposition(MOTION, case)
    assert "prove every element" in prop  # the actual claim, not just the cite


def test_parse_verdict_distinguishes_unsupported_from_supported():
    assert judge._parse_verdict("VERDICT: UNSUPPORTED\nREASON: x")[0] == "UNSUPPORTED"
    assert judge._parse_verdict("VERDICT: SUPPORTED\nREASON: y")[0] == "SUPPORTED"
    assert judge._parse_verdict("VERDICT: PARTIAL")[0] == "PARTIAL"
