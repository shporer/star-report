"""Rich terminal summary."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from star_report.derive import description_line, display_name
from star_report.models import Report
from star_report.render.html import STATUS_LABELS

_MARKS = {"ok": ("✓", "green"), "empty": ("·", "dim"), "error": ("✗", "red"),
          "timeout": ("✗", "red"), "skipped": ("-", "dim")}


def _cell(c, decimals: int) -> str:
    if c is None or c.value is None:
        return "-"
    text = f"{c.value:.{decimals}f}"
    if c.err is not None:
        text += f" ± {c.err:.{decimals}f}"
    return text


def _planets_line(report: Report) -> str:
    arch = report.results.get("exoplanet_archive")
    if arch is None or arch.status != "ok" or not isinstance(arch.data, dict):
        return ""
    planets = [p for p in arch.data.get("planets") or [] if isinstance(p, dict)]
    tois = [str(t["toi"]) for t in arch.data.get("tois") or [] if isinstance(t, dict) and t.get("toi")]
    text = f"Planets: {len(planets)} confirmed"
    if tois:
        text += f"; TOIs: {', '.join(tois)}"
    return text


def print_summary(report: Report, report_path: Path | None, console: Console) -> None:
    ident = report.identity
    lines = [f"[bold]{escape(display_name(ident.main_name))}[/bold]"]
    ids = []
    if ident.tic_id is not None:
        ids.append(f"TIC {ident.tic_id}")
    if ident.gaia_dr3_id:
        ids.append(f"Gaia DR3 {ident.gaia_dr3_id}")
    for key in ("HD", "HIP"):
        if key in ident.other_ids:
            ids.append(ident.other_ids[key] if ident.other_ids[key].startswith(key)
                       else f"{key} {ident.other_ids[key]}")
    lines.append(escape("  ".join(ids)))
    desc = description_line(report.results, report.parameters)
    if desc:
        lines.append(escape(desc))
    console.print(Panel("\n".join(lines), expand=True))

    if report.parameters:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Parameter")
        for col in ("Gaia", "TIC", "SIMBAD"):
            table.add_column(col, justify="right")
        for row in report.parameters:
            label = f"{row.label} ({row.unit})" if row.unit else row.label
            if row.disagree:
                label = f"[yellow]⚠ {escape(label)}[/yellow]"
            else:
                label = escape(label)
            table.add_row(label, *(_cell(row.cells.get(k), row.decimals)
                                   for k in ("gaia", "tic", "simbad")))
        console.print(table)
    else:
        console.print("No parameters available", style="dim")

    for text in [*ident.warnings, *report.flags]:
        console.print(f"⚠ {escape(text)}", style="yellow")

    planets_line = _planets_line(report)
    if planets_line:
        console.print(escape(planets_line))

    for name, res in report.results.items():
        mark, style = _MARKS[res.status]
        label = STATUS_LABELS[res.status]
        if res.status in ("error", "timeout", "skipped") and res.error:
            label = f"{label}: {res.error}"
        origin = ""
        if res.provenance is not None and res.status in ("ok", "empty"):
            origin = "  (cache)" if res.provenance.from_cache else "  (live)"
        console.print(f"[{style}]{mark}[/{style}] {escape(name)}  {escape(label)}{origin}"
                      f"  {res.elapsed_s:.1f} s", soft_wrap=True)

    if report_path is not None:
        console.print(f"Report: {report_path}", soft_wrap=True, highlight=False)
