"""Known QLP time-stamp errors and their correction, ported from the AutoTTV project
(`qlp_time_fix.py`, 2026-10).

QLP light curves carry TIME stamps converted to BJD_TDB by QLP's own barycentric correction. For
some sectors that correction used a wrong observer position:

  s1415  Sectors 14 and 15: the observer position was (Y_ecl, Z_ecl, 0), the spacecraft's
         ecliptic y and z coordinates put in the equatorial x and y slots, instead of the
         equatorial (x, y, z). AutoTTV's model reproduces the QLP time stamps of 48 stars to
         0.8 s rms.
  s7479  the ecliptic-frame ephemeris used as equatorial, the error QLP documents for Sectors
         74-79 (fixed in its v02 files, which MAST serves) and that AutoTTV also found in all of
         Sector 80 (v01).

Which sectors are corrected is configuration (`plots.qlp_time_corrections`), not code. The
Earth's barycentric position, a good stand-in for the spacecraft's at this level, comes from
astropy's built-in ephemeris, so no download is needed.
"""

from __future__ import annotations

import numpy as np

MODELS = ("s1415", "s7479")
BTJD0 = 2457000.0  # BTJD = BJD_TDB - 2457000
_EPS = np.radians(84381.406 / 3600)  # J2000 obliquity (IAU 2006)


def _earth_bary_au(bjd: np.ndarray) -> np.ndarray:
    import astropy.units as u
    from astropy.coordinates import get_body_barycentric
    from astropy.time import Time

    p = get_body_barycentric("earth", Time(np.atleast_1d(np.asarray(bjd, dtype=float)),
                                           format="jd", scale="tdb"))
    return np.stack([p.x.to_value(u.AU), p.y.to_value(u.AU), p.z.to_value(u.AU)], -1)


def _to_ecliptic(v: np.ndarray) -> np.ndarray:
    return np.stack([v[..., 0],
                     v[..., 1] * np.cos(_EPS) + v[..., 2] * np.sin(_EPS),
                     -v[..., 1] * np.sin(_EPS) + v[..., 2] * np.cos(_EPS)], -1)


def _unit(ra: float, dec: float) -> np.ndarray:
    a, d = np.radians(ra), np.radians(dec)
    return np.array([np.cos(d) * np.cos(a), np.cos(d) * np.sin(a), np.sin(d)])


def error_s(model: str, bjd: np.ndarray, ra: float, dec: float) -> np.ndarray:
    """QLP time minus true BJD_TDB, in seconds, for error `model` at times `bjd` (BJD_TDB), for a
    star at (ra, dec) in degrees."""
    import astropy.constants as const
    import astropy.units as u

    if model not in MODELS:
        raise ValueError(f"unknown QLP error model {model!r}")
    au_s = float((1 * u.AU / const.c).to(u.s).value)  # 499.004784 s
    r = _earth_bary_au(bjd)
    re = _to_ecliptic(r)
    if model == "s1415":
        used = np.stack([re[..., 1], re[..., 2], np.zeros(re.shape[:-1])], -1)
    else:
        used = re
    return np.sum((used - r) * _unit(ra, dec), -1) * au_s


def corrected_time(btjd: np.ndarray, model: str, ra: float, dec: float) -> np.ndarray:
    """QLP time stamps (BTJD) with the error of `model` removed."""
    btjd = np.asarray(btjd, dtype=float)
    return btjd - error_s(model, btjd + BTJD0, ra, dec) / 86400.0
