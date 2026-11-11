"""Coordinate helpers: parsing, proper-motion propagation, angular separation."""

from __future__ import annotations

import warnings

import astropy.units as u
import erfa
from astropy.coordinates import SkyCoord
from astropy.time import Time

from star_report.models import Position

_DEFAULT_DISTANCE_PC = 1000.0  # 1 kpc when parallax is unknown


def parse_coordinates(text: str) -> tuple[float, float] | None:
    """Parse "RA Dec" (decimal degrees or sexagesimal) to ICRS degrees, or None."""
    tokens = text.replace(",", " ").split()
    if len(tokens) < 2:
        return None
    try:
        if len(tokens) == 2:
            try:
                ra, dec = float(tokens[0]), float(tokens[1])
            except ValueError:
                pass
            else:
                if not (0.0 <= ra <= 360.0 and -90.0 <= dec <= 90.0):
                    return None
                return ra, dec
        sky = SkyCoord(" ".join(tokens), unit=(u.hourangle, u.deg), frame="icrs")
        return float(sky.ra.deg), float(sky.dec.deg)
    except (ValueError, TypeError, IndexError):
        return None


def propagate(pos: Position, epoch: float) -> tuple[float, float]:
    """Return (ra, dec) in degrees at `epoch` (Julian year), applying proper motion."""
    if pos.pmra is None or pos.pmdec is None:
        return pos.ra, pos.dec
    distance_pc = 1000.0 / pos.parallax if pos.parallax and pos.parallax > 0 else _DEFAULT_DISTANCE_PC
    sky = SkyCoord(
        ra=pos.ra * u.deg,
        dec=pos.dec * u.deg,
        distance=distance_pc * u.pc,
        pm_ra_cosdec=pos.pmra * u.mas / u.yr,
        pm_dec=pos.pmdec * u.mas / u.yr,
        radial_velocity=(pos.rv or 0.0) * u.km / u.s,
        obstime=Time(pos.epoch, format="jyear"),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", erfa.ErfaWarning)
        moved = sky.apply_space_motion(new_obstime=Time(epoch, format="jyear"))
    return float(moved.ra.deg), float(moved.dec.deg)


GAIA_EPOCH = 2016.0  # Gaia DR3 reference epoch


def at_gaia_epoch(pos: Position) -> tuple[float, float]:
    """(ra, dec) at Gaia DR3's epoch: a Gaia position as it is, any other one propagated."""
    if pos.source == "gaia_dr3":
        return pos.ra, pos.dec  # exactly, so position-keyed cache entries keep their keys
    return propagate(pos, GAIA_EPOCH)


def separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    a = SkyCoord(ra1 * u.deg, dec1 * u.deg)
    b = SkyCoord(ra2 * u.deg, dec2 * u.deg)
    return float(a.separation(b).arcsec)
