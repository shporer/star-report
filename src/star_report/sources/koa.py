"""Keck Observatory Archive: object frames per instrument, via the public KOA TAP service."""

from __future__ import annotations

import re
from typing import Any

from star_report.coords import propagate
from star_report.sources.base import Source, SourceContext, _error_text, parallel_map, tap_rows

TAP_URL = "https://koa.ipac.caltech.edu/TAP"
ADQL = ("SELECT koaimtyp, date_obs, progid FROM koa_{inst} "
        "WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra}, {dec}, {r_deg})) = 1")
_INSTRUMENT = re.compile(r"[a-z0-9]+")


def summarize_koa(inst: str, rows: list[dict]) -> dict:
    """Reduce one instrument's rows to counts over its `object` frames."""
    frames = 0
    nights: set[str] = set()
    programs: set[str] = set()
    for r in rows:
        if not isinstance(r, dict) or r.get("koaimtyp") != "object":
            continue
        frames += 1
        date_obs = r.get("date_obs")
        if isinstance(date_obs, str) and len(date_obs) >= 10:
            nights.add(date_obs[:10])
        progid = r.get("progid")
        if isinstance(progid, str) and progid.strip():
            programs.add(progid.strip())
    return {"instrument": inst, "frames": frames, "nights": len(nights),
            "first": min(nights, default=None), "last": max(nights, default=None),
            "programs": sorted(programs)}


def _params(ctx: SourceContext) -> tuple[float, float, float]:
    ra, dec = propagate(ctx.identity.position, 2000.0)
    return float(ra), float(dec), float(ctx.config.sources.radius_arcsec["koa"]) / 3600.0


class KeckKoa(Source):
    name = "koa"
    group = "koa"
    service = "Keck Observatory Archive TAP"

    def query(self, ctx: SourceContext) -> dict:
        ra, dec, _ = _params(ctx)
        return {"ra": ra, "dec": dec, "radius": float(ctx.config.sources.radius_arcsec["koa"]),
                "instruments": list(ctx.config.sources.koa_instruments)}

    def fetch(self, ctx: SourceContext) -> Any:
        ra, dec, r_deg = _params(ctx)
        instruments = [i for i in ctx.config.sources.koa_instruments
                       if isinstance(i, str) and _INSTRUMENT.fullmatch(i)]

        def one(inst: str) -> dict:
            rows = tap_rows(TAP_URL, ADQL.format(inst=inst, ra=ra, dec=dec, r_deg=r_deg), ctx.session)
            return summarize_koa(inst, rows)

        if not instruments:
            return []
        # One daemon thread per instrument; a failed or timed-out instrument must not sink the rest.
        outcomes = parallel_map(one, instruments, self.timeout_s(ctx.config), name="koa")
        results = [value if status == "ok" else {"instrument": inst, "error": _error_text(value)}
                   for inst, (status, value) in zip(instruments, outcomes, strict=True)]
        if all("error" in r for r in results):
            raise RuntimeError(f"all KOA instrument queries failed: {results[0]['error']}")
        return results

    def parse(self, raw: Any, ctx: SourceContext) -> dict:
        found: list[dict] = []
        errors: list[dict] = []
        for r in raw if isinstance(raw, list) else []:
            if not isinstance(r, dict) or not isinstance(r.get("instrument"), str):
                continue
            if "error" in r:
                errors.append({"instrument": r["instrument"], "error": str(r["error"])})
                continue
            frames = r.get("frames")
            if isinstance(frames, int) and not isinstance(frames, bool) and frames > 0:
                found.append(r)
        if not found and not errors:
            return {}
        out: dict = {"instruments": found}
        if errors:
            out["errors"] = errors
        return out
