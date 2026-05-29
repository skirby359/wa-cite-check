"""wacite command-line interface.

  wacite build-index --out cite_index.sqlite      # one-time, needs corpus Postgres
  wacite check motion.docx --index cite_index.sqlite
"""

from __future__ import annotations

import sys

import click

from wacite.check.report import make_console

console = make_console()
DEFAULT_INDEX = "cite_index.sqlite"


@click.group()
@click.version_option()
def cli() -> None:
    """Washington legal citation checker (format/accuracy audit)."""


@cli.command("build-index")
@click.option("--out", default=DEFAULT_INDEX, show_default=True,
              help="Output path for the portable SQLite citation index.")
@click.option("--dsn", default=None,
              help="PostgreSQL DSN for the wa-legal-ai corpus "
                   "(defaults to WALEGAL_DB_* env / wa-legal-ai defaults).")
def build_index(out: str, dsn: str | None) -> None:
    """Build cite_index.sqlite from the live corpus (requires the 'build' extra)."""
    try:
        from wacite.index.build import build_from_postgres
    except ImportError:  # pragma: no cover
        console.print("[red]psycopg is required for build-index. "
                      "Install with: pip install 'wa-cite-check[build]'[/]")
        sys.exit(2)

    console.print(f"Building citation index → [bold]{out}[/] …")
    n_auth, n_lookup = build_from_postgres(out, dsn)
    console.print(
        f"[green]✓[/] Indexed [bold]{n_auth:,}[/] authorities, "
        f"[bold]{n_lookup:,}[/] cite keys → {out}"
    )


@cli.command("check")
@click.argument("document", type=click.Path(exists=True, dir_okay=False))
@click.option("--index", "index_path", default=DEFAULT_INDEX, show_default=True,
              help="Path to cite_index.sqlite.")
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report instead of a table.")
@click.option("--align", "do_align", is_flag=True,
              help="Also run the Phase-2 substantive-alignment pass (advisory; "
                   "needs the 'align' extra and corpus Postgres access).")
@click.option("--llm-provider", default=None,
              help="LLM judge for --align: 'ollama' (local, recommended) or 'claude'. "
                   "Auto-detected from --llm-url / ANTHROPIC_API_KEY if omitted.")
@click.option("--llm-url", default=None,
              help="Ollama base URL for --align, e.g. http://localhost:11434.")
@click.option("--llm-model", default=None,
              help="Judge model override (e.g. qwen2.5:7b-instruct).")
@click.option("--dsn", default=None,
              help="PostgreSQL DSN for the corpus (defaults to WALEGAL_DB_* env).")
@click.option("--top-k", default=3, show_default=True,
              help="Passages per authority shown to the judge.")
def check(document: str, index_path: str, as_json: bool, do_align: bool,
          llm_provider: str | None, llm_url: str | None, llm_model: str | None,
          dsn: str | None, top_k: int) -> None:
    """Audit every citation in DOCUMENT (.docx/.pdf/.txt) against the corpus."""
    from wacite.check.audit import audit_document
    from wacite.check.report import exit_code, render_console, to_json
    from wacite.index.store import CiteIndex

    align_opts = None
    if do_align:
        # The corpus driver (psycopg) and the wa-legal-ai package are both
        # required; embeddings degrade gracefully, so they're not probed here.
        try:
            import psycopg  # noqa: F401
            import walegal.pipeline.llm_provider  # noqa: F401
        except ImportError:
            console.print("[red]--align needs the 'align' extra and the wa-legal-ai "
                          "package. Install with: pip install 'wa-cite-check[align]' "
                          "and `pip install -e ../wa-legal-ai`.[/]")
            sys.exit(2)
        align_opts = {
            "provider": llm_provider,
            "llm_url": llm_url,
            "model": llm_model,
            "dsn": dsn,
            "top_k": top_k,
        }

    with CiteIndex(index_path) as index:
        report = audit_document(document, index, align=do_align, align_opts=align_opts)

    if as_json:
        click.echo(to_json(report))
    else:
        render_console(report, console)

    sys.exit(exit_code(report))


if __name__ == "__main__":  # pragma: no cover
    cli()
