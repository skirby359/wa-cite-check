# wa-cite-check

A standalone Washington legal **citation checker** built on top of the
[`wa-legal-ai`](../wa-legal-ai) corpus.

**Phase 1 (this version) — format / accuracy audit.** Point it at a motion
(`.docx`, `.pdf`, or `.txt`) and it extracts every citation, confirms each one
exists in the corpus, and cross-checks the attached **case name**, **year**,
**parallel cite**, and **authority status** (overruled / repealed / etc.).

It runs **fully offline**: the corpus PostgreSQL database is read once to build
a small portable SQLite index; all checking happens against that file.

> Phase 2 (planned) — *substantive alignment*: judge whether each cited
> authority actually supports the surrounding proposition. Not built yet.

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
pip install -e ".[dev]"     # + pytest
```

## Usage

```bash
# One-time: build the portable index from the live wa-legal-ai corpus.
# DSN defaults to WALEGAL_DB_* env vars / the wa-legal-ai defaults.
wacite build-index --out cite_index.sqlite

# Audit a motion.
wacite check motion.docx --index cite_index.sqlite
wacite check motion.pdf  --json        # machine-readable report
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
`wa-legal-ai`. It does **not** modify the parent project. The only coupling is
the one-time `build-index` read of the corpus `authority` table.
