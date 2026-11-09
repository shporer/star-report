"""TESS Input Catalog v8.2, via MAST Catalogs."""

from __future__ import annotations

from typing import Any

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.table import Table
from astroquery import mast

from star_report.sources.base import Source, SourceContext, records

COLUMNS = (
    "ID", "ra", "dec", "pmRA", "pmDEC", "Teff", "e_Teff", "logg", "e_logg", "MH", "e_MH",
    "rad", "e_rad", "mass", "e_mass", "lum", "e_lum", "d", "e_d", "Tmag", "e_Tmag",
    "Vmag", "e_Vmag", "GAIAmag", "contratio", "disposition", "duplicate_id", "objType",
    "GAIA", "HIP", "TWOMASS", "KIC", "version",
)
_STR_COLS = ("GAIA", "HIP", "TWOMASS", "KIC", "version")
_KEEP = COLUMNS + ("dstArcSec",)


def _blank(value: Any) -> Any:
    if isinstance(value, str) and value.strip() in ("", "--"):
        return None
    return value


def _text(value: Any) -> str | None:
    value = _blank(value)
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def normalize(row: dict) -> dict:
    out = {k: _blank(row[k]) for k in _KEEP if k in row}
    if "ID" in out and out["ID"] is not None:
        out["ID"] = int(out["ID"])
    for k in _STR_COLS:
        if k in out:
            out[k] = _text(out[k])
    return out


def _rows(table: Table) -> list[dict]:
    """JSON-safe rows holding only the wanted columns, nearest first for cones."""
    keep = [c for c in _KEEP if c in table.colnames]
    rows = records(table[keep]) if keep else []
    if "dstArcSec" in keep:
        rows.sort(key=lambda r: (r["dstArcSec"] is None, r["dstArcSec"]))
    return rows


def _client(timeout: float) -> mast.CatalogsClass:
    # Per-instance, no global write. astroquery only checks this timeout after the
    # call returns, so it never bounds a hung socket; the runner's timeout is the guard.
    client = mast.CatalogsClass()
    client.TIMEOUT = timeout
    return client


def by_id(tic: int, timeout: float) -> dict | None:
    table = _client(timeout).query_criteria(catalog="TIC", ID=int(tic))
    rows = _rows(table)
    return normalize(rows[0]) if rows else None


def by_gaia_dr2(dr2: str, timeout: float) -> list[dict]:
    table = _client(timeout).query_criteria(catalog="TIC", GAIA=str(int(dr2)))
    return [normalize(r) for r in _rows(table)]


def cone(ra: float, dec: float, radius_arcsec: float, timeout: float) -> list[dict]:
    coord = SkyCoord(ra * u.deg, dec * u.deg, frame="icrs")
    table = _client(timeout).query_region(coord, radius=radius_arcsec * u.arcsec, catalog="TIC")
    return [normalize(r) for r in _rows(table)]


class Tic(Source):
    name = "tic"
    group = "tic"
    service = "MAST Catalogs (TIC)"
    catalog_version = "TIC v8.2"
    needs = ("tic_id",)

    def query(self, ctx: SourceContext) -> dict:
        return {"catalog": "TIC", "ID": ctx.identity.tic_id}

    def fetch(self, ctx: SourceContext) -> Any:
        table = _client(self.timeout_s(ctx.config)).query_criteria(
            catalog="TIC", ID=int(ctx.identity.tic_id))
        return _rows(table)

    def parse(self, raw: Any, ctx: SourceContext) -> dict | None:
        return normalize(raw[0]) if raw else None
