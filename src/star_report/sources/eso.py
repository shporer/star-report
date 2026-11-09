"""ESO archive: spectra the ESO science archive holds at the star's position."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from star_report.coords import propagate
from star_report.sources.base import Source, SourceContext, tap_rows

TAP_URL = "http://archive.eso.org/tap_obs"
# INTERSECTS on s_region takes ~1 s; the CONTAINS(POINT...) form takes ~15 s.
ADQL = ("SELECT instrument_name, t_min, dp_id FROM ivoa.ObsCore WHERE dataproduct_type = 'spectrum' "
        "AND INTERSECTS(CIRCLE('ICRS', {ra}, {dec}, {r_deg}), s_region) = 1")
_MJD_EPOCH = date(1858, 11, 17)


def mjd_to_date(mjd: Any) -> str | None:
    if isinstance(mjd, bool) or not isinstance(mjd, (int, float)) or mjd != mjd:
        return None
    try:
        return (_MJD_EPOCH + timedelta(days=int(mjd // 1))).isoformat()
    except OverflowError:
        return None


def _params(ctx: SourceContext) -> tuple[float, float, float]:
    ra, dec = propagate(ctx.identity.position, 2000.0)
    return float(ra), float(dec), float(ctx.config.sources.radius_arcsec["eso"]) / 3600.0


class EsoSpectra(Source):
    name = "eso"
    group = "eso"
    service = "ESO archive TAP"

    def query(self, ctx: SourceContext) -> dict:
        ra, dec, _ = _params(ctx)
        return {"ra": ra, "dec": dec, "radius": float(ctx.config.sources.radius_arcsec["eso"])}

    def fetch(self, ctx: SourceContext) -> Any:
        ra, dec, r_deg = _params(ctx)
        return tap_rows(TAP_URL, ADQL.format(ra=ra, dec=dec, r_deg=r_deg), ctx.session)

    def parse(self, raw: Any, ctx: SourceContext) -> dict:
        groups: dict[str, dict] = {}
        for r in raw if isinstance(raw, list) else []:
            inst = r.get("instrument_name") if isinstance(r, dict) else None
            if not isinstance(inst, str) or not inst.strip():
                continue
            g = groups.setdefault(inst.strip(), {"n": 0, "dates": []})
            g["n"] += 1
            d = mjd_to_date(r.get("t_min"))
            if d:
                g["dates"].append(d)
        instruments = [{"instrument": k, "n": g["n"],
                        "first": min(g["dates"], default=None), "last": max(g["dates"], default=None)}
                       for k, g in groups.items()]
        instruments.sort(key=lambda i: (-i["n"], i["instrument"]))
        if not instruments:
            return {}
        return {"total": sum(i["n"] for i in instruments), "instruments": instruments}
