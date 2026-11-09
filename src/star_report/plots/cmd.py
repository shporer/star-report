"""Color-magnitude diagram: a Gaia DR3 background sample with the target marked."""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib.pyplot as plt
import pyvo

from star_report.cache import Cache
from star_report.config import Config
from star_report.models import SourceResult
from star_report.sources import gaia
from star_report.sources.base import records

BACKGROUND_ADQL = (
    "SELECT TOP {n} bp_rp, phot_g_mean_mag + 5*LOG10(parallax) - 10 AS abs_g "
    "FROM gaiadr3.gaia_source WHERE parallax > 10 AND parallax_over_error > 10 "
    "AND ruwe < 1.4 AND phot_bp_mean_flux_over_error > 20 "
    "AND phot_rp_mean_flux_over_error > 20 ORDER BY random_index"
)


def _run_async(session, adql: str) -> list[dict]:
    result = pyvo.dal.TAPService(gaia.TAP_URL, session=session).run_async(adql)
    return records(result.to_table())


def fetch_background(cache: Cache, cfg: Config, session) -> list[list[float]]:
    n = int(cfg.plots.cmd_background_rows)

    def produce() -> list[list[float]]:
        rows = _run_async(session, BACKGROUND_ADQL.format(n=n))
        return [[r["bp_rp"], r["abs_g"]] for r in rows
                if r.get("bp_rp") is not None and r.get("abs_g") is not None]

    return cache.fetch("cmd_background", "background", {"rows": n, "v": 1}, produce).payload


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def target_point(results: dict[str, SourceResult]) -> tuple[float, float] | None:
    res = results.get("gaia_core")
    data = res.data if res is not None and isinstance(res.data, dict) else None
    if data is None:
        return None
    bp_rp, g, plx = (_num(data.get(k)) for k in ("bp_rp", "phot_g_mean_mag", "parallax"))
    if bp_rp is None or g is None or plx is None or plx <= 0:
        return None
    return bp_rp, g + 5 * math.log10(plx) - 10


def plot_cmd(background: list[list[float]], target: tuple[float, float] | None,
             label: str, path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(7.2, 6.0))
    try:
        if background:
            ax.scatter([p[0] for p in background], [p[1] for p in background],
                       s=3, c="0.6", alpha=0.5, linewidths=0, rasterized=True,
                       label="Gaia DR3 stars within 100 pc")
        if target is not None:
            ax.scatter([target[0]], [target[1]], marker="*", s=320, c="red",
                       edgecolors="black", linewidths=0.8, zorder=5, label=label)
        ax.invert_yaxis()
        ax.set_xlabel("BP − RP")
        ax.set_ylabel("M_G")
        ax.set_title(f"Color–magnitude diagram: {label}" if target is not None
                     else "No Gaia parallax for the target")
        ax.grid(alpha=0.25)
        if background or target is not None:
            ax.legend(loc="upper right", fontsize=9)
        fig.tight_layout()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=110)
    finally:
        plt.close(fig)
    return path
