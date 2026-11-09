"""Light curves (TESS / Kepler / K2) and a Lomb-Scargle periodogram."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from astropy.timeseries import LombScargle

from star_report.config import Config
from star_report.models import StarIdentity
from star_report.sources import exoplanet_archive

TESS_AUTHORS = ["SPOC", "TESS-SPOC", "QLP"]
BIN_DAYS = 30 / 1440


@dataclass
class SectorCurve:
    label: str
    time: np.ndarray
    flux: np.ndarray


def _rank(row: dict) -> int | None:
    author = row.get("author")
    try:
        exptime = float(row.get("exptime"))
    except (TypeError, ValueError):
        return None
    if author == "SPOC" and exptime == 120:
        return 0
    if author == "TESS-SPOC":
        return 1
    if author == "QLP":
        return 2
    if author in ("Kepler", "K2") and exptime > 1000:
        return 3
    return None


def choose_products(rows: list[dict], max_sectors: int) -> tuple[list[int], str | None]:
    """Indices of the rows to download: best product per sector, newest first."""
    best: dict[tuple[str, int], tuple[int, int]] = {}  # key -> (rank, row index)
    for i, row in enumerate(rows):
        rank = _rank(row)
        seq = row.get("sequence_number")
        if rank is None or seq is None:
            continue
        key = ("kepler" if rank == 3 else "tess", int(seq))
        if key not in best or rank < best[key][0]:
            best[key] = (rank, i)
    keys = sorted(best, key=lambda k: k[1], reverse=True)
    n = len(keys)
    keep = keys[:max(int(max_sectors), 0)]
    note = f"Showing the {len(keep)} most recent of {n} sectors." if len(keep) < n else None
    return [best[k][1] for k in keep], note


def _label(lc) -> str:
    meta = getattr(lc, "meta", {}) or {}
    author = meta.get("AUTHOR") or meta.get("ORIGIN") or ""
    for key, word in (("SECTOR", "Sector"), ("QUARTER", "Quarter"), ("CAMPAIGN", "Campaign")):
        if meta.get(key) is not None:
            return f"{word} {meta[key]}" + (f" ({author})" if author else "")
    return "Light curve"


def _num(value, cast):
    """cast(value), or None for masked, NaN or unparsable values."""
    try:
        out = cast(value)
        return None if isinstance(out, float) and np.isnan(out) else out
    except Exception:  # noqa: BLE001 - MaskError, TypeError, ValueError
        return None


def _search_rows(search) -> list[dict]:
    return [{"author": str(r["author"]), "exptime": _num(r["exptime"], float),
             "sequence_number": _num(r["sequence_number"], int), "mission": str(r["mission"])}
            for r in search.table]


def _kepler_id(identity: StarIdentity) -> tuple[str, int] | None:
    for prefix, mission in (("KIC", "Kepler"), ("EPIC", "K2")):
        m = re.fullmatch(rf"{prefix}\s*(\d+)", str(identity.other_ids.get(prefix, "")).strip())
        if m:
            return mission, int(m.group(1))
    return None


def fetch_lightcurves(identity: StarIdentity, cfg: Config) -> tuple[list[SectorCurve], str | None]:
    import lightkurve as lk  # slow import; keep it out of module load

    search = None
    if identity.tic_id is not None:
        found = lk.search_lightcurve(f"TIC {int(identity.tic_id)}", mission="TESS",
                                     author=TESS_AUTHORS)
        if len(found):
            search = found
    if search is None:
        kep = _kepler_id(identity)
        if kep is None:
            return [], "No TESS, Kepler or K2 light curves found."
        mission, num = kep
        prefix = "KIC" if mission == "Kepler" else "EPIC"
        search = lk.search_lightcurve(f"{prefix} {num}", mission=mission)
        if not len(search):
            return [], "No TESS, Kepler or K2 light curves found."
    idx, note = choose_products(_search_rows(search), cfg.plots.max_sectors)
    if not idx:
        return [], "No suitable light curves found."
    curves = []
    for i in idx:
        lc = search[i].download()
        if lc is None:
            continue
        lc = lc.remove_nans().normalize()
        curves.append(SectorCurve(_label(lc), np.asarray(lc.time.value, dtype=float),
                                  np.asarray(lc.flux.value, dtype=float)))
    return curves, note


def _bin(time: np.ndarray, flux: np.ndarray, width: float) -> tuple[np.ndarray, np.ndarray]:
    bins = np.floor((time - time.min()) / width).astype(int)
    counts = np.bincount(bins)
    keep = counts > 0
    t = np.bincount(bins, weights=time)[keep] / counts[keep]
    f = np.bincount(bins, weights=flux)[keep] / counts[keep]
    return t, f


def periodogram(curves: list[SectorCurve], min_period: float,
                max_period: float) -> tuple[np.ndarray, np.ndarray]:
    time = np.concatenate([c.time for c in curves])
    flux = np.concatenate([c.flux for c in curves])
    t, f = _bin(time, flux, BIN_DAYS)
    freq, power = LombScargle(t, f).autopower(
        minimum_frequency=1 / max_period, maximum_frequency=1 / min_period,
        samples_per_peak=10)
    return 1 / freq, power


def merge_marks(marks: list[tuple[str, float]], lo: float, hi: float,
                tolerance: float) -> list[tuple[str, float]]:
    """In-range marks sorted by period; marks within `tolerance` (relative) become one."""
    groups: list[list[tuple[str, float]]] = []
    for name, p in sorted((m for m in marks if lo <= m[1] <= hi), key=lambda m: m[1]):
        if groups and p - groups[-1][0][1] <= tolerance * groups[-1][0][1]:
            groups[-1].append((name, p))
        else:
            groups.append([(name, p)])
    return [(" / ".join(n for n, _ in g), g[0][1]) for g in groups]


def plot_lightcurves(curves: list[SectorCurve], marks: list[tuple[str, float]],
                     cfg: Config, path: Path) -> Path:
    lo, hi = cfg.plots.periodogram_min_period_d, cfg.plots.periodogram_max_period_d
    n = len(curves)
    fig = None
    try:
        fig, axes = plt.subplots(n + 1, 1, figsize=(9, 1.6 * n + 3.2), squeeze=False,
                                 gridspec_kw={"height_ratios": [1] * n + [2]})
        for ax, c in zip(axes[:n, 0], curves, strict=True):
            ax.plot(c.time, c.flux, ".", ms=1, color="0.25", rasterized=True)
            ax.set_ylabel("flux", fontsize=8)
            ax.text(0.01, 0.9, c.label, transform=ax.transAxes, fontsize=8, va="top")
        axes[n - 1, 0].set_xlabel("time (BTJD / BKJD, days)")
        ax = axes[n, 0]
        period, power = periodogram(curves, lo, hi)
        ax.plot(period, power, color="C0", lw=1)
        ax.set_xscale("log")
        ax.set_xlim(lo, hi)
        ax.set_xlabel("period (days)")
        ax.set_ylabel("Lomb-Scargle power")
        for i, (label, p) in enumerate(merge_marks(marks, lo, hi,
                                                   cfg.plots.period_merge_tolerance)):
            ax.axvline(p, color=f"C{1 + i % 8}", ls="--", lw=1, label=f"{label} ({p:.3g} d)")
        if ax.get_legend_handles_labels()[0]:
            ax.legend(fontsize=7, loc="upper right")
        fig.tight_layout()
        fig.savefig(path, dpi=120)
    finally:
        if fig is not None:
            plt.close(fig)
    return path


def plot_stage(report, path: Path, cfg: Config) -> tuple[Path | None, str | None]:
    timeout = float(cfg.plots.lightcurve_timeout_s)
    holder: dict = {}

    def work() -> None:
        try:
            holder["result"] = fetch_lightcurves(report.identity, cfg)
        except BaseException as e:  # noqa: BLE001 - re-raised in the caller's thread
            holder["error"] = e

    # Daemon thread: a stuck download must not hold the interpreter open at exit.
    thread = threading.Thread(target=work, name="lightcurve-fetch", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None, f"Light-curve download timed out after {int(timeout)} s."
    if "error" in holder:
        raise holder["error"]
    curves, note = holder["result"]
    if not curves:
        return None, note
    marks = exoplanet_archive.known_periods(report.results)
    return plot_lightcurves(curves, marks, cfg, path), note
