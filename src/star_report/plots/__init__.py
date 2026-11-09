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
from star_report.plots import cmd, lightcurve, sky  # noqa: E402
from star_report.sources.base import call_with_timeout, make_session  # noqa: E402

log = logging.getLogger("star_report.plots")

# A plotter writes its PNG to `path` and returns (path or None, note or None).
Plotter = Callable[[Report, Path, Config, Cache, object], tuple[Path | None, str | None]]


def _plot_cmd(report, path, cfg, cache, session):
    timeout = float(cfg.plots.cmd_background_timeout_s)
    # pyvo's async query can wait up to 600 s; the plotters run one after another.
    status, background = call_with_timeout(lambda: cmd.fetch_background(cache, cfg, session),
                                           timeout, name="cmd-background")
    if status == "timeout":
        return None, f"Gaia CMD background download timed out after {int(timeout)} s."
    if status == "error":
        raise background
    target = cmd.target_point(report.results)
    label = report.identity.main_name
    note = None if target else "No Gaia parallax for the target; only the background is shown."
    return cmd.plot_cmd(background, target, label, path), note


def _plot_lightcurve(report, path, cfg, cache, session):
    return lightcurve.plot_stage(report, path, cfg)


def _plot_sky(report, path, cfg, cache, session):
    pos = report.identity.position
    target = (float(pos.ra), float(pos.dec))
    res = report.results.get("gaia_neighbors")
    note = None
    neighbors: list[dict] = []
    if res is not None and res.status == "ok" and isinstance(res.data, list):
        neighbors = sky.select_neighbors(res.data, cfg.plots.neighbor_max_delta_g,
                                         target_id=report.identity.gaia_dr3_id)
        if res.data and not any(isinstance(n, dict) and isinstance(n.get("delta_g"), int | float)
                                for n in res.data):
            note = "Gaia neighbors have no G magnitudes; none shown."
    else:
        note = "Gaia neighbors unavailable; only the target is marked."
    try:
        image, wcs, label = sky.fetch_cutout(target[0], target[1], cfg, session)
    except Exception as e:  # noqa: BLE001 - fall back to the no-image chart
        log.warning("sky cutout failed: %s: %s", type(e).__name__, e)
        image, wcs, label = None, None, ""
        msg = f"Sky image unavailable ({type(e).__name__}: {e})."
        note = f"{note} {msg}" if note else msg
    return sky.plot_sky(image, wcs, target, neighbors, cfg, label, path), note


# Report order.
PLOTTERS: dict[str, Plotter] = {"cmd": _plot_cmd, "lightcurve": _plot_lightcurve,
                                "sky": _plot_sky}


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
