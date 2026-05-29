"""Live PostgreSQL access for alignment — check-time, cited authorities only.

Phase 1 runs entirely offline against ``cite_index.sqlite``. Substantive
alignment, however, has to read the actual opinion/statute text, which is only
in the wa-legal-ai corpus. A motion cites a handful of authorities, so we open
one connection per ``check --align`` run and pull a few of the most on-point
passages for each cited authority.

``psycopg`` is imported lazily so importing this module never requires it.
"""

from __future__ import annotations

from dataclasses import dataclass

from wacite.index.build import default_dsn


@dataclass
class Passage:
    """A chunk of an authority's text, with its section heading (if any)."""

    text: str
    section_heading: str = ""


def connect(dsn: str | None = None):
    """Open a psycopg connection to the wa-legal-ai corpus.

    Reuses the same DSN resolution as ``build-index`` (WALEGAL_DB_* env /
    wa-legal-ai defaults) when ``dsn`` is None.
    """
    import psycopg  # optional dependency; only needed when --align is used

    return psycopg.connect(dsn or default_dsn())


def _vec_literal(embedding) -> str:
    """Format an embedding vector as a pgvector/halfvec text literal."""
    return "[" + ",".join(f"{float(v):.8f}" for v in embedding) + "]"


def fetch_relevant_passages(conn, authority_id: str, query_embedding, k: int = 3) -> list[Passage]:
    """Return up to ``k`` passages for one authority.

    When ``query_embedding`` is provided, passages are ranked by cosine
    distance to the proposition (pgvector ``<=>``) so the most on-point text is
    fed to the judge. Otherwise we fall back to document order — the opening
    passages of the authority.
    """
    if query_embedding is not None:
        rows = conn.execute(
            """
            SELECT passage_text, COALESCE(section_heading, '')
            FROM passage
            WHERE authority_id = %s::uuid AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::halfvec
            LIMIT %s
            """,
            (str(authority_id), _vec_literal(query_embedding), k),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT passage_text, COALESCE(section_heading, '')
            FROM passage
            WHERE authority_id = %s::uuid
            ORDER BY paragraph_number, chunk_index
            LIMIT %s
            """,
            (str(authority_id), k),
        ).fetchall()
    return [Passage(text=r[0], section_heading=r[1]) for r in rows]
