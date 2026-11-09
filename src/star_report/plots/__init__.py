"""Quick-look plots. Each plotter failing becomes a note; the others still run."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from star_report.cache import Cache  # noqa: E402
from star_report.config import Config  # noqa: E402
from star_report.models import Report  # noqa: E402
from star_report.plots import cmd, lightcurve  # noqa: E402
from star_report.sources.base import make_session  # noqa: E402

log = logging.getLogger("star_report.plots")

# A plotter writes its PNG to `path` and returns (path or None, note or None).
Plotter = Callable[[Report, Path, Config, Cache, object], tuple[Path | None, str | None]]


def _plot_cmd(report, path, cfg, cache, session):
    background = cmd.fetch_background(cache, cfg, session)
    target = cmd.target_point(report.results)
    label = report.identity.main_name
    note = None if target else "No Gaia parallax for the target; only the background is shown."
    return cmd.plot_cmd(background, target, label, path), note


def _plot_lightcurve(report, path, cfg, cache, session):
    return lightcurve.plot_stage(report, path, cfg)


# Report order. Task 23 adds "sky".
PLOTTERS: dict[str, Plotter] = {"cmd": _plot_cmd, "lightcurve": _plot_lightcurve}


def make_plots(report: Report, plots_dir: Path, cfg: Config,
               cache: Cache) -> tuple[dict[str, str], dict[str, str]]:
    plots_dir = Path(plots_dir)
    plots_dir.mkdir(parents=True, exist_ok=True)
    session = make_session(cfg.sources.timeout_s)
    paths: dict[str, str] = {}
    notes: dict[str, str] = {}
    for name, plotter in PLOTTERS.items():
        try:
            path, note = plotter(report, plots_dir / f"{name}.png", cfg, cache, session)
        except Exception as e:  # noqa: BLE001 - one plot must never sink the report
            log.warning("plot %s failed: %s: %s", name, type(e).__name__, e)
            notes[name] = f"{type(e).__name__}: {e}"
            continue
        if path is not None:
            paths[name] = f"{plots_dir.name}/{name}.png"
        if note:
            notes[name] = note
    return paths, notes
