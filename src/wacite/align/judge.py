"""Substantive-alignment judge.

For each citation that resolved in Phase 1, decide whether the cited authority
actually supports the surrounding proposition in the motion.

Design (see plan): the LLM provider does the *judging*; embeddings only *select*
the most on-point passages of a long opinion to show it. When no LLM provider is
usable, we fall back to wa-legal-ai's embedding-only
``_check_content_support_batch`` for a crude YES/PARTIAL/NO read so the feature
degrades gracefully instead of failing.

All wa-legal-ai imports live behind thin module-level wrappers so this module
imports cleanly without the ``walegal`` package installed (and so tests can
monkeypatch them without a live model, DB, or LLM).
"""

from __future__ import annotations

import os
import re

from wacite.align import corpus
from wacite.index.store import CiteIndex
from wacite.models import (
    FINDING_SEVERITY,
    AuthorityRecord,
    Finding,
    FindingType,
    ParsedCitation,
)

JUDGE_SYSTEM_PROMPT = (
    "You are a meticulous legal cite-checker. You are given a PROPOSITION taken "
    "from a legal brief, the citation the brief offers for it, and one or more "
    "EXCERPTS from the cited authority. Decide whether the excerpts actually "
    "support the proposition.\n\n"
    "Reply on exactly two lines:\n"
    "VERDICT: SUPPORTED | PARTIAL | UNSUPPORTED\n"
    "REASON: <one short sentence>\n\n"
    "Use SUPPORTED only when the excerpts clearly establish the proposition. "
    "Use PARTIAL when they are related but do not fully establish it. Use "
    "UNSUPPORTED when the excerpts do not support the proposition or are off "
    "point. Judge only from the excerpts shown; do not rely on outside knowledge."
)

# Sentence terminators used to carve the proposition out of the motion text.
_SENT_END = re.compile(r"[.;!?]")

# Verdict label -> (finding type) for the LLM path and the embedding fallback.
_LLM_VERDICT_FINDING = {
    "UNSUPPORTED": FindingType.UNSUPPORTED_PROPOSITION,
    "PARTIAL": FindingType.WEAK_SUPPORT,
    # SUPPORTED -> no finding.
}
_EMBED_LABEL_FINDING = {
    "NO": FindingType.UNSUPPORTED_PROPOSITION,
    "PARTIAL": FindingType.WEAK_SUPPORT,
    # YES / unchecked -> no finding.
}


def extract_proposition(text: str, cit: ParsedCitation, *, lead: bool = True) -> str:
    """Carve out the proposition a citation is offered for, using char offsets.

    Returns the sentence containing the citation; when ``lead`` is set (default)
    the immediately preceding sentence is included too, because briefs commonly
    state the claim in one sentence and drop the citation as the next "sentence"
    (``... beyond a reasonable doubt. State v. Smith, 199 Wn.2d 1 (2022).``).
    """
    m = _SENT_END.search(text, cit.end)
    end = m.end() if m else len(text)
    # Sentence-boundary ends preceding the citation. The last is the start of the
    # sentence the cite sits in; the one before it starts the lead sentence.
    starts = [mm.end() for mm in _SENT_END.finditer(text, 0, cit.start)]
    if lead:
        # Reach back to the previous sentence (or the top of the text) so a claim
        # stated just before a trailing cite is captured.
        start = starts[-2] if len(starts) >= 2 else 0
    elif starts:
        start = starts[-1]
    else:
        start = 0
    return text[start:end].strip()


def build_user_message(
    proposition: str, passages: list[corpus.Passage], cit: ParsedCitation, auth: AuthorityRecord
) -> str:
    title = auth.display_title or auth.canonical_cite
    excerpts = "\n\n".join(
        f"[{p.section_heading}] {p.text}" if p.section_heading else p.text
        for p in passages
    )
    return (
        f"PROPOSITION: {proposition}\n\n"
        f"CITATION: {cit.raw}  (resolves to: {title})\n\n"
        f"EXCERPTS FROM THE CITED AUTHORITY:\n{excerpts}"
    )


def _parse_verdict(reply: str) -> tuple[str, str]:
    """Parse the model's two-line reply into (verdict, reason)."""
    verdict = "PARTIAL"  # conservative default when the model is vague
    reason = ""
    for line in reply.splitlines():
        s = line.strip()
        up = s.upper()
        if up.startswith("VERDICT"):
            val = s.split(":", 1)[1].upper() if ":" in s else up
            # Check UNSUPPORTED before SUPPORTED — the former contains the latter.
            for v in ("UNSUPPORTED", "PARTIAL", "SUPPORTED"):
                if v in val:
                    verdict = v
                    break
        elif up.startswith("REASON") and ":" in s:
            reason = s.split(":", 1)[1].strip()
    return verdict, reason


def _align_finding(
    cit: ParsedCitation, auth: AuthorityRecord, ftype: FindingType, label: str, reason: str
) -> Finding:
    title = auth.display_title or auth.canonical_cite
    verb = (
        "only partially supports"
        if ftype is FindingType.WEAK_SUPPORT
        else "may not support"
    )
    msg = f"cited authority '{title}' {verb} the surrounding proposition (judge: {label})"
    if reason:
        msg += f" — {reason}"
    return Finding(
        citation=cit,
        finding_type=ftype,
        severity=FINDING_SEVERITY[ftype],
        message=msg,
        authority=auth,
        rationale=reason or None,
    )


# --------------------------------------------------------------------------- #
# Thin wrappers over wa-legal-ai — imported lazily, monkeypatchable in tests.  #
# --------------------------------------------------------------------------- #


def _resolve_provider(provider: str | None, llm_url: str | None) -> str | None:
    try:
        from walegal.pipeline.llm_provider import resolve_provider

        return resolve_provider(provider, llm_url)
    except Exception:
        return None


def _llm_chat(**kwargs) -> str:
    from walegal.pipeline.llm_provider import llm_chat

    return llm_chat(**kwargs)


def _load_embed_model():
    try:
        from walegal.ingestion.embeddings import load_embedding_model

        return load_embedding_model()
    except Exception:
        return None


def _embedding_fallback(checks, motion_text, passages_by_authority) -> None:
    from walegal.pipeline.verify import _check_content_support_batch

    _check_content_support_batch(checks, motion_text, passages_by_authority)


def _make_check(raw: str, authority_id: str):
    from walegal.pipeline.types import CitationCheck

    return CitationCheck(raw_citation=raw, resolved=True, authority_id=authority_id)


def _shim_passage(auth: AuthorityRecord, text: str):
    from walegal.pipeline.types import RetrievedPassage

    return RetrievedPassage(
        passage_id=auth.authority_id,  # unused by the support check
        authority_id=auth.authority_id,
        canonical_cite=auth.canonical_cite,
        authority_type=auth.authority_type,
        court_level=auth.court_level or 0,
        date_filed="",
        passage_text=text,
        section_heading="",
        display_title=auth.display_title or "",
        authority_status=auth.authority_status,
    )


def _provider_usable(active: str | None, llm_url: str | None) -> bool:
    """Whether the resolved provider can actually be called.

    ``resolve_provider`` defaults to "ollama" even with nothing configured, so
    we confirm the prerequisites are present before committing to the LLM path.
    """
    if active == "claude":
        return os.environ.get("ANTHROPIC_API_KEY", "").startswith("sk-")
    if active == "ollama":
        return bool(llm_url)
    return False


# --------------------------------------------------------------------------- #
# Orchestration                                                                #
# --------------------------------------------------------------------------- #


def align_citations(
    citations: list[ParsedCitation],
    text: str,
    index: CiteIndex,
    *,
    provider: str | None = None,
    llm_url: str | None = None,
    model: str | None = None,
    dsn: str | None = None,
    top_k: int = 3,
) -> list[Finding]:
    """Judge substantive support for every citation that resolves in the index.

    Returns advisory ``Finding``s (UNSUPPORTED_PROPOSITION / WEAK_SUPPORT) for
    the citations whose authority appears not to fully support the proposition.
    Citations the judge finds supported, and those with no retrievable text,
    produce no finding.
    """
    resolved: list[tuple[ParsedCitation, AuthorityRecord, str]] = []
    for cit in citations:
        auth = index.lookup_by_cite(cit.lookup_key)
        if auth is None:
            continue
        prop = extract_proposition(text, cit)
        if prop:
            resolved.append((cit, auth, prop))
    if not resolved:
        return []

    # Embed propositions once (batched) to rank passages. None => document order.
    embeddings = None
    embed_model = _load_embed_model()
    if embed_model is not None:
        try:
            embeddings = embed_model.encode(
                [prop for _c, _a, prop in resolved],
                normalize_embeddings=True,
                batch_size=16,
            )
        except Exception:
            embeddings = None

    # Fetch the most on-point passages for each cited authority.
    conn = corpus.connect(dsn)
    try:
        passages_per: list[list[corpus.Passage]] = []
        for i, (_cit, auth, _prop) in enumerate(resolved):
            qemb = embeddings[i] if embeddings is not None else None
            passages_per.append(
                corpus.fetch_relevant_passages(conn, auth.authority_id, qemb, top_k)
            )
    finally:
        conn.close()

    active = _resolve_provider(provider, llm_url)
    if _provider_usable(active, llm_url):
        return _judge_with_llm(resolved, passages_per, active, llm_url, model)
    return _judge_with_embeddings(resolved, passages_per, text)


def _judge_with_llm(resolved, passages_per, provider, llm_url, model) -> list[Finding]:
    findings: list[Finding] = []
    for (cit, auth, prop), passages in zip(resolved, passages_per):
        if not passages:
            continue  # nothing to judge against
        try:
            reply = _llm_chat(
                system_prompt=JUDGE_SYSTEM_PROMPT,
                user_message=build_user_message(prop, passages, cit, auth),
                provider=provider,
                llm_url=llm_url,
                model=model,
                temperature=0.0,
                max_tokens=200,
            )
        except Exception:
            continue  # advisory: a failed judgement just yields no finding
        verdict, reason = _parse_verdict(reply)
        ftype = _LLM_VERDICT_FINDING.get(verdict)
        if ftype is not None:
            findings.append(_align_finding(cit, auth, ftype, verdict, reason))
    return findings


def _judge_with_embeddings(resolved, passages_per, motion_text) -> list[Finding]:
    """No-LLM fallback: wa-legal-ai's embedding-only content-support check."""
    checks = []
    check_for: dict[int, object] = {}
    passages_by_authority: dict[str, list] = {}
    for idx, ((cit, auth, _prop), passages) in enumerate(zip(resolved, passages_per)):
        if not passages:
            continue
        check = _make_check(cit.raw, auth.authority_id)
        checks.append(check)
        check_for[idx] = check
        passages_by_authority[str(auth.authority_id)] = [
            _shim_passage(auth, p.text) for p in passages
        ]
    if not checks:
        return []

    _embedding_fallback(checks, motion_text, passages_by_authority)

    findings: list[Finding] = []
    for idx, (cit, auth, _prop) in enumerate(resolved):
        check = check_for.get(idx)
        if check is None:
            continue
        ftype = _EMBED_LABEL_FINDING.get(check.content_supports)
        if ftype is not None:
            findings.append(
                _align_finding(
                    cit,
                    auth,
                    ftype,
                    check.content_supports,
                    "embedding similarity only (no LLM provider configured)",
                )
            )
    return findings
