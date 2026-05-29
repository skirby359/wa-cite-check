"""Render an AuditReport to the console (rich) or JSON."""

from __future__ import annotations

import json
import sys

from rich.console import Console
from rich.table import Table

from wacite.models import AuditReport, Severity

_SEVERITY_STYLE = {
    Severity.ERROR: "bold red",
    Severity.WARNING: "yellow",
    Severity.INFO: "dim cyan",
}
_SEVERITY_ORDER = [Severity.ERROR, Severity.WARNING, Severity.INFO]


def make_console(**kwargs) -> Console:
    """Build a Console that won't crash on legacy Windows (cp1252) consoles.

    rich encodes its output with the stdout stream's codec. On a default Windows
    PowerShell/conhost that codec is cp1252, which can't encode the ✓ marker or
    the box-drawing characters the tables use, so it raises UnicodeEncodeError on
    otherwise-fine output. Reconfiguring the stream to UTF-8 with
    errors="replace" lets those characters render on a UTF-8-capable console and
    degrade to a replacement character (never a crash) on a legacy one.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        # Stream isn't a reconfigurable TextIOWrapper (e.g. captured/redirected).
        pass
    return Console(**kwargs)


def to_json(report: AuditReport) -> str:
    return json.dumps(report.to_dict(), indent=2)


def render_console(report: AuditReport, console: Console | None = None) -> None:
    console = console or make_console()

    console.print()
    console.rule(f"[bold]Citation audit: {report.source}")
    console.print(
        f"Citations found: [bold]{report.citations_found}[/]   "
        f"resolved: [green]{report.citations_resolved}[/]   "
        f"errors: [red]{report.error_count}[/]   "
        f"warnings: [yellow]{report.warning_count}[/]"
    )

    if not report.findings:
        console.print("\n[green]✓ No citation issues found.[/]\n")
        return

    by_sev = {s: [f for f in report.findings if f.severity is s] for s in _SEVERITY_ORDER}

    for sev in _SEVERITY_ORDER:
        items = by_sev[sev]
        if not items:
            continue
        table = Table(
            title=f"{sev.value.upper()} ({len(items)})",
            title_style=_SEVERITY_STYLE[sev],
            show_lines=False,
            expand=True,
        )
        table.add_column("Type", style=_SEVERITY_STYLE[sev], no_wrap=True)
        table.add_column("Citation", style="bold", no_wrap=False)
        table.add_column("Issue")
        for f in items:
            label = f.finding_type.value + (" (short)" if f.citation.from_shortform else "")
            table.add_row(label, f.citation.raw, f.message)
        console.print(table)

    console.print()


def exit_code(report: AuditReport) -> int:
    """0 clean, 1 if any ERROR-level findings (useful in CI / pre-filing hooks)."""
    return 1 if report.error_count else 0
