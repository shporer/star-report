"""Gaia DR3 sources: core parameters and the neighbor cone, via the ESA TAP service."""

from __future__ import annotations

from typing import Any

import requests

from star_report.sources.base import Source, SourceContext, tap_rows

TAP_URL = "https://gea.esac.esa.int/tap-server/tap"

_SOURCE_COLS = [
    "source_id", "ra", "dec", "ref_epoch", "parallax", "parallax_error", "pmra", "pmdec",
    "phot_g_mean_mag", "phot_bp_mean_mag", "phot_rp_mean_mag", "bp_rp", "ruwe",
    "radial_velocity", "radial_velocity_error", "has_rvs", "phot_variable_flag",
    "non_single_star",
]
_AP_BASES = [f"{q}_{m}" for m in ("gspphot", "gspspec") for q in ("teff", "logg", "mh")] + [
    f"{q}_flame" for q in ("radius", "mass", "lum", "age")
]
_AP_COLS = [c for b in _AP_BASES for c in (b, f"{b}_lower", f"{b}_upper")]
_DIST_COLS = ["r_med_geo", "r_lo_geo", "r_hi_geo",
              "r_med_photogeo", "r_lo_photogeo", "r_hi_photogeo"]
_LOOKUP_COLS = ["source_id", "ra", "dec", "ref_epoch", "parallax", "pmra", "pmdec",
                "radial_velocity", "phot_g_mean_mag", "bp_rp"]

_CORE_ADQL = (
    "SELECT " + ", ".join(
        [f"g.{c}" for c in _SOURCE_COLS] + [f"ap.{c}" for c in _AP_COLS]
        + [f"d.{c}" for c in _DIST_COLS])
    + " FROM gaiadr3.gaia_source AS g"
    " LEFT JOIN gaiadr3.astrophysical_parameters AS ap ON ap.source_id = g.source_id"
    " LEFT JOIN external.gaiaedr3_distance AS d ON d.source_id = g.source_id"
    " WHERE g.source_id = {id}"
)


def _id(value: Any) -> str | None:
    return None if value is None else str(int(value))


def _int_id(gaia_id: str) -> int:
    return int(str(gaia_id).strip())  # int() also rejects anything that is not digits


def _norm_lookup(row: dict) -> dict:
    out = dict(row)
    out["source_id"] = _id(row.get("source_id"))
    return out


def by_id(dr3_id: str, session: requests.Session) -> dict | None:
    adql = (f"SELECT {', '.join(_LOOKUP_COLS)} FROM gaiadr3.gaia_source "
            f"WHERE source_id = {_int_id(dr3_id)}")
    rows = tap_rows(TAP_URL, adql, session)
    return _norm_lookup(rows[0]) if rows else None


def cone_adql(ra: float, dec: float, radius_arcsec: float) -> str:
    r_deg = radius_arcsec / 3600
    point = f"POINT('ICRS', {ra!r}, {dec!r})"
    return (
        f"SELECT {', '.join(_LOOKUP_COLS)}, "
        f"3600 * DISTANCE(POINT('ICRS', ra, dec), {point}) AS dist_arcsec "
        f"FROM gaiadr3.gaia_source "
        f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra!r}, {dec!r}, {r_deg!r})) "
        f"ORDER BY dist_arcsec"
    )


def cone(ra: float, dec: float, radius_arcsec: float, session: requests.Session) -> list[dict]:
    return [_norm_lookup(r) for r in tap_rows(TAP_URL, cone_adql(ra, dec, radius_arcsec), session)]


def _neighbourhood(adql: str, session: requests.Session) -> list[dict]:
    out = []
    for r in tap_rows(TAP_URL, adql, session):
        out.append({**r, "dr2_source_id": _id(r.get("dr2_source_id")),
                    "dr3_source_id": _id(r.get("dr3_source_id"))})
    return out


_NBH_COLS = "dr2_source_id, dr3_source_id, angular_distance, magnitude_difference"


def dr3_from_dr2(dr2_id: str, session: requests.Session) -> list[dict]:
    return _neighbourhood(
        f"SELECT {_NBH_COLS} FROM gaiadr3.dr2_neighbourhood "
        f"WHERE dr2_source_id = {_int_id(dr2_id)}", session)


def dr2_from_dr3(dr3_id: str, session: requests.Session) -> list[dict]:
    return _neighbourhood(
        f"SELECT {_NBH_COLS} FROM gaiadr3.dr2_neighbourhood "
        f"WHERE dr3_source_id = {_int_id(dr3_id)}", session)


class GaiaCore(Source):
    name = "gaia_core"
    group = "gaia"
    service = "Gaia DR3 (ESA TAP)"
    catalog_version = "Gaia DR3"
    needs = ("gaia_dr3_id",)

    def query(self, ctx: SourceContext) -> dict:
        return {"source_id": ctx.identity.gaia_dr3_id}

    def fetch(self, ctx: SourceContext) -> Any:
        adql = _CORE_ADQL.format(id=_int_id(ctx.identity.gaia_dr3_id))
        return tap_rows(TAP_URL, adql, ctx.session)

    def parse(self, raw: Any, ctx: SourceContext) -> dict | None:
        if not raw:
            return None
        row = raw[0]
        out = {c: row.get(c) for c in _SOURCE_COLS}
        out["source_id"] = _id(row.get("source_id"))
        out["ap"] = {c: row.get(c) for c in _AP_COLS}
        out["distance"] = {c: row.get(c) for c in _DIST_COLS}
        return out


class GaiaNeighbors(Source):
    name = "gaia_neighbors"
    group = "gaia"
    service = "Gaia DR3 (ESA TAP)"
    catalog_version = "Gaia DR3"
    needs = ("gaia_dr3_id",)

    def _radius(self, ctx: SourceContext) -> float:
        return ctx.config.sources.radius_arcsec["gaia_neighbors"]

    def query(self, ctx: SourceContext) -> dict:
        pos = ctx.identity.position
        return {"ra": pos.ra, "dec": pos.dec, "radius_arcsec": self._radius(ctx)}

    def fetch(self, ctx: SourceContext) -> Any:
        pos = ctx.identity.position
        return tap_rows(TAP_URL, cone_adql(pos.ra, pos.dec, self._radius(ctx)), ctx.session)

    def parse(self, raw: Any, ctx: SourceContext) -> list[dict] | None:
        if not raw:
            return None
        target_id = str(ctx.identity.gaia_dr3_id)
        target_g = next((r.get("phot_g_mean_mag") for r in raw
                        if _id(r.get("source_id")) == target_id), None)
        out = []
        for r in raw:
            g = r.get("phot_g_mean_mag")
            out.append({
                "source_id": _id(r.get("source_id")),
                "ra": r.get("ra"),
                "dec": r.get("dec"),
                "G": g,
                "bp_rp": r.get("bp_rp"),
                "dist_arcsec": r.get("dist_arcsec"),
                "delta_g": g - target_g if g is not None and target_g is not None else None,
            })
        return out
