"""Gaia DR3 sources: core parameters and the neighbor cone, via the ESA TAP service."""

from __future__ import annotations

from typing import Any

import requests

from star_report.coords import at_gaia_epoch
from star_report.sources.base import Source, SourceContext, parallel_map, tap_rows

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
        # Centred at Gaia's epoch: during a Gaia outage the identity's position is SIMBAD's
        # J2000 one, which for a high-proper-motion star lies far from its Gaia source.
        ra, dec = at_gaia_epoch(ctx.identity.position)
        return {"ra": ra, "dec": dec, "radius_arcsec": self._radius(ctx)}

    def fetch(self, ctx: SourceContext) -> Any:
        q = self.query(ctx)
        return tap_rows(TAP_URL, cone_adql(q["ra"], q["dec"], q["radius_arcsec"]), ctx.session)

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


def _safe_id(gaia_id: Any) -> int | None:
    try:
        return _int_id(gaia_id)
    except (TypeError, ValueError):
        return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value else None


def _dicts(value: Any) -> list[dict]:
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def _all_ok(outcomes: list[tuple[str, Any]]) -> list[Any]:
    """parallel_map's results, or a raise, so nothing partial is cached.

    A query's own error is raised in preference to a timeout, so the service's message (an HTTP 503,
    say) is not hidden behind "timed out" when another query is still waiting.
    """
    for wanted in ("error", "timeout"):
        for status, value in outcomes:
            if status == wanted:
                raise value
    return [value for _, value in outcomes]


# kind -> (Gaia DR3 table, {parsed key: column}); each row also keeps nss_solution_type as "type".
# One source can have solutions in several tables (HD 18284: an SB1 orbit and an Acceleration7).
_NSS = {
    "orbit": ("nss_two_body_orbit", {
        "period_d": "period", "period_err": "period_error", "ecc": "eccentricity",
        "k1_kms": "semi_amplitude_primary", "inclination": "inclination",
        "mass_ratio": "mass_ratio"}),
    # mas/yr^2; the derivatives, in mas/yr^3, exist for Acceleration9 only
    "acceleration": ("nss_acceleration_astro", {
        "accel_ra": "accel_ra", "accel_ra_err": "accel_ra_error",
        "accel_dec": "accel_dec", "accel_dec_err": "accel_dec_error",
        "deriv_accel_ra": "deriv_accel_ra", "deriv_accel_ra_err": "deriv_accel_ra_error",
        "deriv_accel_dec": "deriv_accel_dec", "deriv_accel_dec_err": "deriv_accel_dec_error",
        "significance": "significance"}),
    # km/s, km/s/d and km/s/d^2; the second derivative exists for SecondDegreeTrendSB1 only
    "trend": ("nss_non_linear_spectro", {
        "mean_velocity": "mean_velocity", "mean_velocity_err": "mean_velocity_error",
        "first_deriv_velocity": "first_deriv_velocity",
        "first_deriv_velocity_err": "first_deriv_velocity_error",
        "second_deriv_velocity": "second_deriv_velocity",
        "second_deriv_velocity_err": "second_deriv_velocity_error"}),
}
_NSS_TABLES = [table for table, _ in _NSS.values()]


class GaiaNss(Source):
    name = "gaia_nss"
    group = "gaia"
    service = "Gaia DR3 (ESA TAP)"
    catalog_version = "Gaia DR3"
    needs = ("gaia_dr3_id",)

    def query(self, ctx: SourceContext) -> dict:
        # The table list is part of the cache key, so payloads cached before a table was added
        # are not reused.
        return {"source_id": ctx.identity.gaia_dr3_id, "tables": list(_NSS_TABLES)}

    def fetch(self, ctx: SourceContext) -> Any:
        sid = _safe_id(ctx.identity.gaia_dr3_id)
        if sid is None:
            return {}
        adql = [f"SELECT source_id, nss_solution_type, {', '.join(cols.values())} "
                f"FROM gaiadr3.{table} WHERE source_id = {sid}" for table, cols in _NSS.values()]
        # The tables are queried concurrently in daemon threads.
        rows = _all_ok(parallel_map(lambda q: tap_rows(TAP_URL, q, ctx.session), adql,
                                    self.timeout_s(ctx.config), name="gaia_nss"))
        return dict(zip(_NSS_TABLES, rows, strict=True))

    def parse(self, raw: Any, ctx: SourceContext) -> list[dict] | None:
        if not isinstance(raw, dict):
            return None
        out = []
        for kind, (table, cols) in _NSS.items():
            for r in _dicts(raw.get(table)):
                sol = r.get("nss_solution_type")
                out.append({"kind": kind, "type": sol if isinstance(sol, str) else None,
                            **{key: _num(r.get(col)) for key, col in cols.items()}})
        return out or None


# in_vari_<flag> -> (table, period column, True when the column is a frequency in 1/d)
_VARI_PERIODS = {
    "eclipsing_binary": ("vari_eclipsing_binary", "frequency", True),
    "long_period_variable": ("vari_long_period_variable", "frequency", True),
    "short_timescale": ("vari_short_timescale", "frequency", True),
    "ms_oscillator": ("vari_ms_oscillator", "frequency1", True),
    "rotation_modulation": ("vari_rotation_modulation", "best_rotation_period", False),
    "rrlyrae": ("vari_rrlyrae", "pf", False),
    "cepheid": ("vari_cepheid", "pf", False),
    "planetary_transit": ("vari_planetary_transit", "transit_period", False),
    "compact_companion": ("vari_compact_companion", "period", False),
}


class GaiaVari(Source):
    name = "gaia_vari"
    group = "gaia"
    service = "Gaia DR3 (ESA TAP)"
    catalog_version = "Gaia DR3"
    needs = ("gaia_dr3_id",)

    def query(self, ctx: SourceContext) -> dict:
        return {"source_id": ctx.identity.gaia_dr3_id}

    def fetch(self, ctx: SourceContext) -> Any:
        sid = _safe_id(ctx.identity.gaia_dr3_id)
        if sid is None:
            return {"summary": None}
        rows = tap_rows(TAP_URL, f"SELECT * FROM gaiadr3.vari_summary WHERE source_id = {sid}",
                        ctx.session)
        if not rows:
            return {"summary": None}
        summary = {k: v for k, v in rows[0].items() if k.startswith("in_vari_")}
        summary["source_id"] = _id(rows[0].get("source_id"))
        classifier_adql = ("SELECT classifier_name, best_class_name, best_class_score "
                           f"FROM gaiadr3.vari_classifier_result WHERE source_id = {sid}")
        flags = [f for f in _VARI_PERIODS if summary.get(f"in_vari_{f}") is True]
        period_adql = {f: f"SELECT {_VARI_PERIODS[f][1]} FROM gaiadr3.{_VARI_PERIODS[f][0]} "
                          f"WHERE source_id = {sid}" for f in flags}
        # Independent queries run concurrently in daemon threads.
        rows = _all_ok(parallel_map(lambda adql: tap_rows(TAP_URL, adql, ctx.session),
                                    [classifier_adql, *period_adql.values()],
                                    self.timeout_s(ctx.config), name="gaia_vari"))
        return {"summary": summary, "classifier": rows[0],
                "periods": dict(zip(period_adql, rows[1:], strict=True))}

    def parse(self, raw: Any, ctx: SourceContext) -> dict:
        if not isinstance(raw, dict) or not isinstance(raw.get("summary"), dict):
            return {}
        classes = []
        for r in _dicts(raw.get("classifier")):
            name = r.get("best_class_name")
            if isinstance(name, str):
                classes.append({"classifier": r.get("classifier_name"), "class": name,
                                "score": _num(r.get("best_class_score"))})
        periods = []
        by_flag = raw.get("periods")
        for flag, (_, col, is_freq) in _VARI_PERIODS.items():
            for r in _dicts(by_flag.get(flag)) if isinstance(by_flag, dict) else []:
                v = _num(r.get(col))
                if v is None or (is_freq and v <= 0):
                    continue
                periods.append({"type": flag, "period_d": 1 / v if is_freq else v})
        return {"classes": classes, "periods": periods}
