"""Light curves (TESS / Kepler / K2) and a Lomb-Scargle periodogram."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from astropy.timeseries import LombScargle

from star_report.config import Config
from star_report.models import StarIdentity
from star_report.plots import qlp_time
from star_report.sources import exoplanet_archive
from star_report.sources.base import call_with_timeout

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


def tess_quality_bitmask(cfg: Config) -> int:
    """lightkurve's default TESS quality mask plus plots.lightcurve_exclude_quality_bits."""
    from lightkurve.utils import TessQualityFlags  # slow import; keep it out of module load

    mask = int(TessQualityFlags.DEFAULT_BITMASK)
    for bit in cfg.plots.lightcurve_exclude_quality_bits:
        mask |= 1 << int(bit)
    return mask


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
    rows = _search_rows(search)
    idx, note = choose_products(rows, cfg.plots.max_sectors)
    if not idx:
        return [], "No suitable light curves found."
    tess_mask = tess_quality_bitmask(cfg)
    curves = []
    fixed = []  # "Sector N by +a to +b min" for each QLP sector whose time stamps were corrected
    for i in idx:
        # Kepler and K2 quality bits mean other things: they keep lightkurve's default mask.
        mask = tess_mask if rows[i]["mission"].startswith("TESS") else "default"
        lc = search[i].download(quality_bitmask=mask)
        if lc is None:
            continue
        label = _label(lc)
        column = next((c for c in cfg.plots.lightcurve_detrended_columns if c in lc.columns), None)
        if column is not None:  # QLP's detrended flux instead of its raw sap_flux
            lc = lc.select_flux(column)
            label = f"{label[:-1]}, {column})" if label.endswith(")") else f"{label} ({column})"
        lc = lc.remove_nans().normalize()
        time = np.asarray(lc.time.value, dtype=float)
        sector = rows[i]["sequence_number"]
        model = (cfg.plots.qlp_time_corrections.get(sector)
                 if rows[i]["author"] == "QLP" and rows[i]["mission"].startswith("TESS") else None)
        if model is not None and time.size:
            pos = identity.position
            shift = qlp_time.corrected_time(time, model, pos.ra, pos.dec) - time
            time = time + shift
            label = f"{label[:-1]}, time corrected)" if label.endswith(")") else f"{label} (time corrected)"
            fixed.append(f"Sector {sector} by {shift.min() * 1440:+.1f} to {shift.max() * 1440:+.1f} min")
        curves.append(SectorCurve(label, time, np.asarray(lc.flux.value, dtype=float)))
    if fixed:
        fix_note = ("QLP time stamps corrected for known errors in QLP's barycentric correction "
                    "(as in AutoTTV): " + "; ".join(fixed) + ".")
        note = f"{note} {fix_note}" if note else fix_note
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


@dataclass
class FoldResult:
    ok: bool  # passes the test below, so the folded plot is drawn
    period: float  # highest Lomb-Scargle peak in all data, days
    fap: float  # its Baluev false-alarm probability (assumes white noise)
    agree: int  # segments whose own top peak is the same period with FAP below the threshold
    segments: int


def _ls_peak(time: np.ndarray, flux: np.ndarray, min_period: float,
             max_period: float) -> tuple[float, float]:
    """(period of the highest Lomb-Scargle peak, its Baluev FAP), on 30-minute bins."""
    t, f = _bin(time, flux, BIN_DAYS)
    ls = LombScargle(t, f)
    fmin, fmax = 1 / max_period, 1 / min_period
    freq, power = ls.autopower(minimum_frequency=fmin, maximum_frequency=fmax, samples_per_peak=10)
    i = int(np.argmax(power))
    fap = ls.false_alarm_probability(power[i], minimum_frequency=fmin, maximum_frequency=fmax,
                                     method="baluev")
    return float(1 / freq[i]), float(fap)


def _segments(curves: list[SectorCurve]) -> list[tuple[np.ndarray, np.ndarray]]:
    """Independent stretches of data: the sectors, or a single sector split at its largest gap."""
    if len(curves) != 1:
        return [(c.time, c.flux) for c in curves]
    t, f = curves[0].time, curves[0].flux
    if t.size < 3:
        return [(t, f)]
    cut = int(np.argmax(np.diff(t))) + 1
    return [(t[:cut], f[:cut]), (t[cut:], f[cut:])]


def detect_fold(curves: list[SectorCurve], cfg: Config) -> FoldResult:
    """Fold only on a period that is significant in all data AND recurs in independent segments.

    The Baluev FAP alone passes for almost any TESS light curve with systematics; systematics
    rarely put their top peak at the same period in different sectors.
    """
    p = cfg.plots
    lo, hi = p.periodogram_min_period_d, p.fold_max_period_d
    period, fap = _ls_peak(np.concatenate([c.time for c in curves]),
                           np.concatenate([c.flux for c in curves]), lo, hi)
    segments = _segments(curves)
    agree = 0
    for t, f in segments:
        if t.size < 10:
            continue
        seg_period, seg_fap = _ls_peak(t, f, lo, hi)
        if seg_fap < p.fold_fap_max and abs(seg_period / period - 1) <= p.fold_period_tolerance:
            agree += 1
    ok = (fap < p.fold_fap_max and agree >= p.fold_min_segments
          and 2 * agree >= len(segments))
    return FoldResult(ok, period, fap, agree, len(segments))


def fold_note(found: FoldResult, marks: list[tuple[str, float]], cfg: Config) -> str:
    p = cfg.plots
    where = f"{found.agree} of {found.segments} sectors (or orbits, for a single sector)"
    if not found.ok:
        if found.fap >= p.fold_fap_max:
            return (f"No folded light curve: no periodogram peak below {p.fold_max_period_d:g} d "
                    f"has FAP < {p.fold_fap_max:g} (best: P = {found.period:.5g} d, "
                    f"FAP = {found.fap:.1e}).")
        return (f"No folded light curve: the strongest peak below {p.fold_max_period_d:g} d "
                f"(P = {found.period:.5g} d, FAP = {found.fap:.1e} in all data) is the top peak "
                f"in only {where}; a fold needs at least {p.fold_min_segments} and half of them.")
    parts = [f"Folded on the Lomb-Scargle peak, P = {found.period:.5g} d (top; Baluev FAP "
             f"{found.fap:.1e}, which assumes white noise; the top peak in {where}), and on "
             f"2P = {2 * found.period:.5g} d (bottom), where the odd and even cycles fall at "
             "phase 0 and 0.5: equal dips there mean the true period is P; unequal dips, or "
             "a dip at only one of them, point to 2P.",
             "For transits and eclipses the peak often falls at half the true period."]
    for name, period in marks:
        if abs(period / (2 * found.period) - 1) <= p.fold_period_tolerance:
            parts.append(f"{name} ({period:.5g} d) is twice the folded period.")
        elif abs(period / found.period - 1) <= p.fold_period_tolerance:
            parts.append(f"{name} ({period:.5g} d) matches the folded period.")
    return " ".join(parts)


def _fold_panel(ax, t: np.ndarray, f: np.ndarray, period: float, bins: int, lo: float) -> None:
    """Points and binned median folded on period, phase lo to lo + 1, phase 0 at the lowest median."""
    phase = np.mod(t / period, 1.0)
    idx = np.minimum((phase * bins).astype(int), bins - 1)
    med = np.array([np.median(f[idx == k]) if np.any(idx == k) else np.nan for k in range(bins)])
    centers = (np.arange(bins) + 0.5) / bins
    shift = centers[int(np.nanargmin(med))]
    ph = np.mod(phase - shift - lo, 1.0) + lo
    cph = np.mod(centers - shift - lo, 1.0) + lo
    order = np.argsort(cph)
    counts = np.bincount(idx, minlength=bins)
    err = np.array([np.std(f[idx == k]) / np.sqrt(counts[k]) if counts[k] > 1 else np.nan
                    for k in range(bins)])
    ax.plot(ph, f, ".", ms=1, color="0.8", rasterized=True)
    ax.errorbar(cph[order], med[order], yerr=err[order], fmt="o", ms=3, color="black",
                elinewidth=0.8, capsize=0, label="binned (median ± σ/√n)")
    ax.set_xlim(lo, lo + 1)
    ax.set_ylabel("normalized flux")


def plot_fold(curves: list[SectorCurve], found: FoldResult, path: Path, bins: int = 100) -> Path:
    """All data folded on the peak period P (phase -0.5 to 0.5) and on 2P (phase -0.25 to 0.75,
    so the odd and even cycles, at phase 0 and 0.5, are both whole)."""
    t = np.concatenate([c.time for c in curves])
    f = np.concatenate([c.flux for c in curves])
    fig = None
    try:
        fig, (top, bottom) = plt.subplots(2, 1, figsize=(9, 7))
        _fold_panel(top, t, f, found.period, bins, -0.5)
        top.set_title(f"Folded on P = {found.period:.5g} d (FAP {found.fap:.1e}; top peak in "
                      f"{found.agree} of {found.segments} sectors or orbits)", fontsize=10)
        top.legend(fontsize=8, loc="lower right")
        _fold_panel(bottom, t, f, 2 * found.period, 2 * bins, -0.25)
        bottom.set_title(f"Folded on 2P = {2 * found.period:.5g} d (odd and even cycles at "
                         "phase 0 and 0.5)", fontsize=10)
        bottom.set_xlabel("phase")
        fig.tight_layout()
        fig.savefig(path, dpi=120)
    finally:
        if fig is not None:
            plt.close(fig)
    return path


def plot_stage(report, path: Path, cfg: Config) -> tuple[Path | None, str | None]:
    timeout = float(cfg.plots.lightcurve_timeout_s)
    # Daemon thread: a stuck download must not hold the interpreter open at exit.
    status, value = call_with_timeout(lambda: fetch_lightcurves(report.identity, cfg), timeout,
                                      name="lightcurve-fetch")
    if status == "timeout":
        return None, f"Light-curve download timed out after {int(timeout)} s."
    if status == "error":
        raise value
    curves, note = value
    if not curves:
        return None, note
    marks = exoplanet_archive.known_periods(report.results)
    lc_path = plot_lightcurves(curves, marks, cfg, path)
    found = detect_fold(curves, cfg)
    fold_path = plot_fold(curves, found, path.with_name("fold.png")) if found.ok else None
    return lc_path, note, {"fold": (fold_path, fold_note(found, marks, cfg))}
