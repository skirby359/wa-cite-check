# wa-cite-check

A standalone Washington legal **citation checker** built on top of the
[`wa-legal-ai`](../wa-legal-ai) corpus.

**Phase 1 (this version) — format / accuracy audit.** Point it at a motion
(`.docx`, `.pdf`, or `.txt`) and it extracts every citation, confirms each one
exists in the corpus, and cross-checks the attached **case name**, **year**,
**parallel cite**, and **authority status** (overruled / repealed / etc.).

It runs **fully offline**: the corpus PostgreSQL database is read once to build
a small portable SQLite index; all checking happens against that file.

**Phase 2 (optional) — substantive alignment.** With `--align`, each citation
that resolves is checked for *substance*: does the cited authority actually
support the proposition it's offered for? An LLM judge (local **Ollama** by
default, or **Claude**) reads the proposition alongside the most on-point
passages of the cited authority and returns a verdict. This pass needs corpus
text, so — unlike Phase 1 — it reads the live Postgres at check time, but only
for the handful of authorities a motion cites. **All Phase-2 findings are
advisory**: they surface as WARNING/INFO and never change the exit code.

## How it works

```
motion.docx ─▶ extract text ─▶ parse citations ─▶ audit ─▶ report
                                     │                │
                                     │           cite_index.sqlite (offline)
                                     │                ▲
                          short-form (Id./supra)      │
                                                 build-index (one-time, reads corpus Postgres)
```

## Install

```bash
pip install -e .            # runtime (check)
pip install -e ".[build]"   # + psycopg, needed only for build-index
pip install -e ".[align]"   # + LLM judge / embeddings, for the Phase-2 --align pass
pip install -e ".[dev]"     # + pytest
```

The `--align` pass also needs the sibling `wa-legal-ai` package importable:

```bash
pip install -e ../wa-legal-ai
```

## Usage

```bash
# One-time: build the portable index from the live wa-legal-ai corpus.
# DSN defaults to WALEGAL_DB_* env vars / the wa-legal-ai defaults.
wacite build-index --out cite_index.sqlite

# Audit a motion (Phase 1, fully offline).
wacite check motion.docx --index cite_index.sqlite
wacite check motion.pdf  --json        # machine-readable report

# Add the Phase-2 substantive-alignment pass with a local Ollama judge.
ollama pull qwen2.5:7b-instruct
wacite check motion.docx --align \
    --llm-provider ollama --llm-url http://localhost:11434 \
    --llm-model qwen2.5:7b-instruct
```

Exit code is `1` when any ERROR-level finding is present (handy in a
pre-filing check), `0` otherwise.

## What it checks

| Finding | Severity | Meaning |
|---|---|---|
| `NOT_FOUND` | error | Citation doesn't resolve to any corpus authority (typo or fabricated). |
| `NAME_MISMATCH` | error | Reporter cite resolves, but the party name attached is wrong. |
| `YEAR_MISMATCH` | error | Parenthetical year disagrees with the corpus. |
| `PARALLEL_MISMATCH` | error | The given parallel reporters point to different authorities. |
| `NEGATIVE_TREATMENT` | warning | Authority is overruled / repealed / abrogated / etc. |
| `PIN_NOT_VERIFIED` | info | Pin page ("…at 512") can't be confirmed — the corpus has no star pagination. |
| `UNSUPPORTED_PROPOSITION` | warning | *(--align)* The judge found the cited authority doesn't support the proposition. Advisory. |
| `WEAK_SUPPORT` | info | *(--align)* The judge found only partial/uncertain support. Advisory. |

## Limitations

- **Pin pages** are reported, not verified (no page-level mapping in the corpus).
- **Scanned PDFs** need OCR first; only text-based PDFs are read.
- Reporter folding handles common WA / Pacific / Federal variants
  (`Wn.2d` ⇄ `Wash. 2d`); exotic reporters may miss.

## Tests

```bash
pytest        # runs against an in-process fixture index — no PostgreSQL needed
```

## Relationship to `wa-legal-ai`

This project reuses, with attribution in the source, the citation extraction
patterns, cite/name normalization, and `authority_status` vocabulary from
`wa-legal-ai`. It does **not** modify the parent project. Phase 1 couples to it
only through the one-time `build-index` read of the corpus `authority` table.
The optional Phase-2 `--align` pass couples more closely: it imports
`wa-legal-ai`'s LLM provider, embedding loader, and embedding-only
content-support check, and reads the corpus `passage` table at check time.
