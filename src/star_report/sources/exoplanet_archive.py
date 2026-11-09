"""NASA Exoplanet Archive: the TAP helper and the TOI/KOI host lookups used by the resolver."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import requests

from star_report.models import SourceResult
from star_report.sources.base import Source, SourceContext

TAP_URL = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"


def tap(adql: str, session: requests.Session, timeout: float) -> list[dict]:
    response = session.get(TAP_URL, params={"query": adql, "format": "json"}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _first(rows: list[dict], column: str) -> int | None:
    value = rows[0].get(column) if rows else None
    return None if value is None else int(value)


def toi_host(toi: str, session: requests.Session, timeout: float) -> int | None:
    """TIC ID of the host of TOI `toi` (the number before any ".01")."""
    return _first(tap(f"select tid from toi where toipfx = '{int(toi)}'", session, timeout), "tid")


def koi_host(koi: str, session: requests.Session, timeout: float) -> int | None:
    """KIC ID of the host of KOI `koi` (the number before any ".01")."""
    adql = f"select kepid from cumulative where kepoi_name like 'K{int(koi):05d}%'"
    return _first(tap(adql, session, timeout), "kepid")


_PLANETS = ("select pl_name, pl_orbper, pl_rade, pl_bmasse, discoverymethod, disc_year "
            "from pscomppars where ")
_TOIS = "select toi, tfopwg_disp, pl_orbper, pl_trandep, pl_rade from toi where tid = {tic}"
_KOIS = ("select kepoi_name, kepler_name, koi_disposition, koi_period, koi_prad "
         "from cumulative where kepid = {kic}")
_K2 = ("select pl_name, epic_candname, disposition, pl_orbper, pl_rade "
       "from k2pandc where epic_hostname = 'EPIC {epic}'")


def _number(other_ids: dict, key: str) -> int | None:
    match = re.search(r"\d+", other_ids.get(key) or "")
    return int(match.group()) if match else None


def _sort_key(value: Any) -> tuple:
    """Numeric-aware, None-safe: '5972.01' sorts by value, other strings alphabetically."""
    text = "" if value is None else str(value)
    try:
        return (0, float(text), text)
    except ValueError:
        return (1, 0.0, text)


class ExoplanetArchive(Source):
    name = "exoplanet_archive"
    group = "exoplanet_archive"
    service = "NASA Exoplanet Archive TAP"

    def query(self, ctx: SourceContext) -> dict:
        ident = ctx.identity
        return {"tic": ident.tic_id, "gaia_dr3": ident.gaia_dr3_id,
                "kic": _number(ident.other_ids, "KIC"), "epic": _number(ident.other_ids, "EPIC")}

    def fetch(self, ctx: SourceContext) -> Any:
        q = self.query(ctx)
        session, timeout = ctx.session, ctx.config.sources.timeout_s
        out: dict[str, list[dict]] = {"planets": [], "tois": [], "kois": [], "k2": []}
        if q["tic"] is not None:
            out["planets"] = tap(_PLANETS + f"tic_id = 'TIC {q['tic']}'", session, timeout)
            out["tois"] = tap(_TOIS.format(tic=q["tic"]), session, timeout)
        elif q["gaia_dr3"] is not None:
            out["planets"] = tap(_PLANETS + f"gaia_dr3_id = 'Gaia DR3 {q['gaia_dr3']}'",
                                 session, timeout)
        if q["kic"] is not None:
            out["kois"] = tap(_KOIS.format(kic=q["kic"]), session, timeout)
        if q["epic"] is not None:
            out["k2"] = tap(_K2.format(epic=q["epic"]), session, timeout)
        return out

    def parse(self, raw: Any, ctx: SourceContext) -> dict:
        planets = [{"name": r.get("pl_name"), "period_d": r.get("pl_orbper"),
                    "radius_re": r.get("pl_rade"), "mass_me": r.get("pl_bmasse"),
                    "method": r.get("discoverymethod"), "year": r.get("disc_year")}
                   for r in raw.get("planets", [])]
        tois = [{"toi": r.get("toi"), "disposition": r.get("tfopwg_disp"),
                 "period_d": r.get("pl_orbper"), "depth_ppm": r.get("pl_trandep"),
                 "radius_re": r.get("pl_rade")} for r in raw.get("tois", [])]
        kois = [{"koi": r.get("kepoi_name"), "name": r.get("kepler_name"),
                 "disposition": r.get("koi_disposition"), "period_d": r.get("koi_period"),
                 "radius_re": r.get("koi_prad")} for r in raw.get("kois", [])]
        k2: dict[Any, dict] = {}
        for r in raw.get("k2", []):
            k2.setdefault(r.get("epic_candname"), {
                "name": r.get("pl_name"), "candidate": r.get("epic_candname"),
                "disposition": r.get("disposition"), "period_d": r.get("pl_orbper"),
                "radius_re": r.get("pl_rade")})
        data = {"planets": sorted(planets, key=lambda p: _sort_key(p["name"])),
                "tois": sorted(tois, key=lambda t: _sort_key(t["toi"])),
                "kois": sorted(kois, key=lambda k: _sort_key(k["koi"])),
                "k2": sorted(k2.values(), key=lambda k: _sort_key(k["name"]))}
        return data if any(data.values()) else {}


def known_periods(results: Mapping[str, SourceResult]) -> list[tuple[str, float]]:
    """Labelled orbital periods (days) of the confirmed planets, then the TOIs."""
    result = results.get(ExoplanetArchive.name)
    data = (result.data if result else None) or {}
    out = [(p["name"], float(p["period_d"])) for p in data.get("planets", [])
           if p.get("period_d") is not None]
    out += [(f"TOI {t['toi']}", float(t["period_d"])) for t in data.get("tois", [])
            if t.get("period_d") is not None]
    return out
