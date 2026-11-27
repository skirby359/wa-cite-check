"""Opinion text for ``check --quotes``, read from the wa-legal-ai corpus.

The portable index keeps only each opinion's page range (enough to check a pin
offline). Checking that a quotation sits on the pinned page needs the opinion's
text, which only the corpus holds, so this reads it at check time, one query per
cited opinion. ``psycopg`` is imported lazily, as for ``--align``.
"""

from __future__ import annotations


class CorpusText:
    """Callable: authority id -> stored opinion text (None if absent). A case with several
    stored texts (recovered page texts per reporter, full_text) gets them joined with
    wacite.pages.TEXT_SEP."""

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn
        self._conn = None

    def __call__(self, authority_id: str) -> str | None:
        if self._conn is None:
            from wacite.align.corpus import connect
            from wacite.index.build import text_sql

            self._conn = connect(self._dsn)
            self._text = text_sql(self._conn)
        row = self._conn.execute(
            f"SELECT {self._text} FROM authority a WHERE a.authority_id = %s::uuid", (authority_id,)
        ).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
