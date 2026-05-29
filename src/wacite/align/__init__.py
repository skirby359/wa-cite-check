"""Phase-2 substantive alignment (advisory).

Unlike the Phase-1 audit, alignment needs the *text* of each cited authority —
which lives in the wa-legal-ai PostgreSQL corpus, not in the offline SQLite
index. So this package connects to Postgres at check time, but only for the
handful of authorities a motion actually cites, and only when ``--align`` is
requested. Everything here is advisory: findings surface as WARNING/INFO and
never change the exit code.
"""
