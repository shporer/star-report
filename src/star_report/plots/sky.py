"""Sky cutout (DSS) with Gaia DR3 neighbors and a TESS-pixel-scale grid."""

from __future__ import annotations

import io
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits
from astropy.visualization import AsinhStretch, ImageNormalize, ZScaleInterval
from astropy.wcs import WCS

from star_report.config import Config
from star_report.sources.base import is_reachable

CDS_HOST = "alasky.cds.unistra.fr"
STSCI_URL = "https://archive.stsci.edu/cgi-bin/dss_search"
CAPTION = ("Grid: TESS pixel scale (21″ cells centered on the target, aligned with RA/Dec); "
           "it does not show any sector's orientation. Neighbors at Gaia DR3 epoch 2016.0.")


def _from_hdul(hdul) -> tuple[np.ndarray, WCS]:
    hdu = hdul[0]
    if hdu.data is None:
        raise ValueError("The cutout FITS file holds no image data.")
    return np.asarray(hdu.data, dtype=float).squeeze(), WCS(hdu.header, naxis=2)


def fetch_cutout(ra: float, dec: float, cfg: Config, session) -> tuple[np.ndarray, WCS, str]:
    ra, dec = float(ra), float(dec)
    arcmin = float(cfg.plots.cutout_arcmin)
    if is_reachable(CDS_HOST):
        import astropy.units as u
        from astroquery.hips2fits import hips2fits

        npix = int(round(arcmin * 60))  # about 1 arcsec per pixel
        hdul = hips2fits.query(hips=cfg.plots.sky_survey, width=npix, height=npix,
                               projection="TAN", ra=ra * u.deg, dec=dec * u.deg,
                               fov=arcmin * u.arcmin, format="fits")
        image, wcs = _from_hdul(hdul)
        return image, wcs, "DSS2 red (CDS hips2fits)"
    params = {"v": cfg.plots.sky_fallback_survey, "r": ra, "d": dec, "e": "J2000",
              "h": arcmin, "w": arcmin, "f": "fits", "c": "none"}
    resp = session.get(STSCI_URL, params=params, timeout=cfg.sources.timeout_s)
    resp.raise_for_status()
    if not resp.content.lstrip().startswith(b"SIMPLE"):
        raise ValueError("STScI DSS did not return a FITS file "
                         f"(got {resp.content[:40]!r}).")
    with fits.open(io.BytesIO(resp.content)) as hdul:
        image, wcs = _from_hdul(hdul)
    return image, wcs, "DSS2 red (STScI)"


def select_neighbors(neighbors: list[dict], max_delta_g: float) -> list[dict]:
    """Non-target rows (the target has delta_g == 0) no more than max_delta_g fainter."""
    out = []
    for n in neighbors:
        if not isinstance(n, dict):
            continue
        dg = n.get("delta_g")
        if not isinstance(dg, int | float) or dg == 0:
            continue
        if dg <= max_delta_g:
            out.append(n)
    return out


def _offsets_arcsec(ra0: float, dec0: float, ra: float, dec: float) -> tuple[float, float]:
    dra = (ra - ra0 + 180) % 360 - 180
    return dra * np.cos(np.radians(dec0)) * 3600, (dec - dec0) * 3600


def _grid_lines(half: float, step: float) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Segments in arcsec offsets (east, north): cell edges at (k + 1/2) * step."""
    n = int(half // step) + 1
    edges = [(k + 0.5) * step for k in range(-n, n)]
    edges = [e for e in edges if abs(e) <= half]
    lines = []
    for e in edges:
        lines.append(((e, e), (-half, half)))
        lines.append(((-half, half), (e, e)))
    return lines


def plot_sky(image: np.ndarray | None, wcs: WCS | None, target: tuple[float, float],
             neighbors: list[dict], cfg: Config, label: str, path: Path) -> Path:
    ra0, dec0 = float(target[0]), float(target[1])
    half = float(cfg.plots.cutout_arcmin) * 30  # arcsec
    step = float(cfg.plots.tess_pixel_arcsec)
    cosd = np.cos(np.radians(dec0))
    has_image = image is not None and wcs is not None
    fig = None
    try:
        fig = plt.figure(figsize=(7, 7.6))
        if has_image:
            ax = fig.add_subplot(1, 1, 1, projection=wcs)
            norm = ImageNormalize(image, interval=ZScaleInterval(), stretch=AsinhStretch())
            ax.imshow(image, origin="lower", cmap="gray_r", norm=norm)
            world = ax.get_transform("world")

            def to_xy(dx, dy):  # arcsec offsets -> (transform data coords)
                return ra0 + np.asarray(dx) / 3600 / cosd, dec0 + np.asarray(dy) / 3600

            kw = {"transform": world}
            ax.set_xlabel("RA")
            ax.set_ylabel("Dec")
            ax.set_title(f"Sky cutout: {label}", fontsize=10)
        else:
            ax = fig.add_subplot(1, 1, 1)
            ax.set_xlim(half, -half)  # east to the left
            ax.set_ylim(-half, half)
            ax.set_aspect("equal")

            def to_xy(dx, dy):
                return np.asarray(dx), np.asarray(dy)

            kw = {}
            ax.set_xlabel("ΔRA cos(Dec) (arcsec)")
            ax.set_ylabel("ΔDec (arcsec)")
            ax.set_title("Gaia DR3 neighbors (no image available)", fontsize=10)

        for (xs, ys) in _grid_lines(half, step):
            x, y = to_xy(xs, ys)
            ax.plot(x, y, color="C0", lw=0.6, alpha=0.6, **kw)

        x, y = to_xy(0.0, 0.0)
        ax.plot(x, y, marker="+", ms=18, mew=1.5, color="red", ls="none", **kw)
        ax.plot(x, y, marker="o", ms=14, mfc="none", mec="red", mew=1.2, ls="none", **kw)
        for n in neighbors:
            try:
                dx, dy = _offsets_arcsec(ra0, dec0, float(n["ra"]), float(n["dec"]))
                dg = float(n["delta_g"])
            except (KeyError, TypeError, ValueError):
                continue
            if abs(dx) > half or abs(dy) > half:
                continue
            x, y = to_xy(dx, dy)
            ax.plot(x, y, marker="o", ms=12, mfc="none", mec="orange", mew=1.2, ls="none", **kw)
            ax.annotate(f"ΔG={dg:+.1f}", (float(x), float(y)),
                        xycoords=world if has_image else "data",
                        xytext=(8, 8), textcoords="offset points", fontsize=8, color="darkorange")

        # 1 arcmin scale bar along Dec, lower-left corner
        bx, by = -0.8 * half, -0.85 * half
        sx, sy = to_xy([bx, bx], [by, by + 60.0])
        ax.plot(sx, sy, color="k", lw=2.5, solid_capstyle="butt", **kw)
        tx, ty = to_xy(bx, by + 30.0)
        ax.annotate("1′", (float(tx), float(ty)), xycoords=world if has_image else "data",
                    xytext=(5, 0), textcoords="offset points", fontsize=9, va="center",
                    bbox={"boxstyle": "round,pad=0.15", "fc": "white", "alpha": 0.7, "lw": 0})
        if has_image:
            ax.coords.grid(False)
            ax.set_xlim(-0.5, image.shape[1] - 0.5)  # overlays must not grow the frame
            ax.set_ylim(-0.5, image.shape[0] - 0.5)
        fig.text(0.5, 0.01, CAPTION, ha="center", va="bottom", fontsize=7, wrap=True)
        fig.tight_layout(rect=(0, 0.04, 1, 1))
        fig.savefig(path, dpi=120)
    finally:
        if fig is not None:
            plt.close(fig)
    return path
