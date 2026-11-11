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


def has_gaia_row(results: dict[str, SourceResult]) -> bool:
    """The report holds the target's Gaia DR3 row. When it does, that row alone places the star."""
    res = results.get("gaia_core")
    return res is not None and res.status == "ok" and isinstance(res.data, dict)


TIC_DR2_PARALLAX = "gaia2"  # the TIC's PARflag for a Gaia DR2 parallax
TIC_LABEL = "Gaia DR2 values from TIC v8.2"
TIC_NOTE = ("The target is placed from its TIC v8.2 values (Gaia DR2 G, BP and RP magnitudes and "
            "parallax), because this report has no Gaia DR3 data for it. The background is Gaia "
            "DR3; DR2 and DR3 magnitudes can differ by a few hundredths of a magnitude.")
NO_PARALLAX_NOTE = "No Gaia parallax for the target; only the background is shown."
NO_DATA_NOTE = ("No Gaia DR3 data for the target in this report, and no Gaia DR2 values in the "
                "TIC to place it; only the background is shown.")
NO_DATA_TITLE = "Target not placed: no Gaia DR3 or TIC values"


def tic_point(results: dict[str, SourceResult]) -> tuple[float, float] | None:
    """(BP - RP, M_G) from the TIC's Gaia DR2 magnitudes and parallax.

    Only when the TIC says its parallax is Gaia DR2's, so the point is all DR2.
    """
    res = results.get("tic")
    data = res.data if res is not None and res.status == "ok" and isinstance(res.data, dict) else None
    if data is None or data.get("PARflag") != TIC_DR2_PARALLAX:
        return None
    g, bp, rp, plx = (_num(data.get(k)) for k in ("GAIAmag", "gaiabp", "gaiarp", "plx"))
    if g is None or bp is None or rp is None or plx is None or plx <= 0:
        return None
    return bp - rp, g + 5 * math.log10(plx) - 10


def plot_cmd(background: list[list[float]], target: tuple[float, float] | None,
             label: str, path: Path, *, from_tic: bool = False,
             empty_title: str = "No Gaia parallax for the target") -> Path:
    """from_tic: the target comes from tic_point, drawn as an open star and labelled as such."""
    fig, ax = plt.subplots(figsize=(7.2, 6.0))
    try:
        if background:
            ax.scatter([p[0] for p in background], [p[1] for p in background],
                       s=3, c="0.6", alpha=0.5, linewidths=0, rasterized=True,
                       label="Gaia DR3 stars within 100 pc")
        if target is not None and from_tic:
            ax.scatter([target[0]], [target[1]], marker="*", s=320, facecolors="none",
                       edgecolors="red", linewidths=1.6, zorder=5, label=f"{label} ({TIC_LABEL})")
        elif target is not None:
            ax.scatter([target[0]], [target[1]], marker="*", s=320, c="red",
                       edgecolors="black", linewidths=0.8, zorder=5, label=label)
        ax.invert_yaxis()
        ax.set_xlabel("BP − RP")
        ax.set_ylabel("M_G")
        if target is None:
            ax.set_title(empty_title)
        else:
            ax.set_title(f"Color–magnitude diagram: {label}"
                         + (f"\ntarget: {TIC_LABEL}" if from_tic else ""))
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
