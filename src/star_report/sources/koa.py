"""Keck Observatory Archive: object frames per instrument, via the public KOA TAP service.

Each instrument is its own TAP table and its own cache entry, so an instrument whose query failed
is asked again on the next run while the instruments that answered stay cached.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from star_report.cache import CacheEntry
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


def _instruments(ctx: SourceContext) -> list[str]:
    return [i for i in ctx.config.sources.koa_instruments
            if isinstance(i, str) and _INSTRUMENT.fullmatch(i)]


def _combine(instruments: Sequence[str], outcomes: Sequence[tuple[str, Any]]) -> list[Any]:
    """One payload entry per instrument: its summary, or {"instrument", "error"} when it failed.

    Raises when every instrument failed, so the source reports an error instead of no data.
    """
    if all(status != "ok" for status, _ in outcomes):
        raise RuntimeError(f"all KOA instrument queries failed: {_error_text(outcomes[0][1])}")
    return [value if status == "ok" else {"instrument": inst, "error": _error_text(value)}
            for inst, (status, value) in zip(instruments, outcomes, strict=True)]


class KeckKoa(Source):
    name = "koa"
    group = "koa"
    service = "Keck Observatory Archive TAP"

    def query(self, ctx: SourceContext) -> dict:
        ra, dec, _ = _params(ctx)
        return {"ra": ra, "dec": dec, "radius": float(ctx.config.sources.radius_arcsec["koa"]),
                "instruments": list(ctx.config.sources.koa_instruments)}

    def _instrument_timeout(self, ctx: SourceContext) -> float:
        """The instruments' wait: a share of the source timeout, so a hung instrument becomes
        "not checked" while run_sources still accepts the partial answer."""
        return self.timeout_s(ctx.config) * ctx.config.sources.koa_instrument_timeout_fraction

    def _query_instrument(self, ctx: SourceContext, inst: str) -> dict:
        ra, dec, r_deg = _params(ctx)
        rows = tap_rows(TAP_URL, ADQL.format(inst=inst, ra=ra, dec=dec, r_deg=r_deg), ctx.session)
        return summarize_koa(inst, rows)

    def fetch(self, ctx: SourceContext) -> Any:
        """Every instrument, uncached (the fixture recorder's path)."""
        instruments = _instruments(ctx)
        if not instruments:
            return []
        # One daemon thread per instrument; a failed or timed-out instrument must not sink the rest.
        outcomes = parallel_map(lambda inst: self._query_instrument(ctx, inst), instruments,
                                self._instrument_timeout(ctx), name="koa")
        return _combine(instruments, outcomes)

    def cached_fetch(self, ctx: SourceContext, query: dict) -> CacheEntry:
        """fetch(), with one cache entry per instrument; a failed instrument writes none."""
        instruments = _instruments(ctx)
        if not instruments:
            return CacheEntry([], ctx.now(), False)
        shared = {k: v for k, v in query.items() if k != "instruments"}

        def one(inst: str) -> CacheEntry:
            return ctx.cache.fetch(self.group, self.name, {**shared, "instrument": inst},
                                   lambda: self._query_instrument(ctx, inst))

        outcomes = parallel_map(one, instruments, self._instrument_timeout(ctx), name="koa")
        payload = _combine(instruments, [(status, value.payload if status == "ok" else value)
                                         for status, value in outcomes])
        answered = [value for status, value in outcomes if status == "ok"]
        return CacheEntry(payload, min(e.retrieved for e in answered),
                          all(e.from_cache for e in answered))

    def parse(self, raw: Any, ctx: SourceContext) -> dict:
        found: list[dict] = []
        no_frames: list[str] = []
        errors: list[dict] = []
        for r in raw if isinstance(raw, list) else []:
            if not isinstance(r, dict) or not isinstance(r.get("instrument"), str):
                continue
            if "error" in r:
                errors.append({"instrument": r["instrument"], "error": str(r["error"])})
                continue
            frames = r.get("frames")
            if isinstance(frames, int) and not isinstance(frames, bool):
                if frames > 0:
                    found.append(r)
                elif frames == 0:
                    no_frames.append(r["instrument"])
        if not found and not errors:
            return {}
        out: dict = {"instruments": found}
        if errors:
            out["errors"] = errors
            out["no_frames"] = no_frames
        return out

    def partial_error(self, data: Any) -> str | None:
        errors = data.get("errors") if isinstance(data, dict) else None
        names = [e["instrument"] for e in errors if isinstance(e, dict)
                 and isinstance(e.get("instrument"), str)] if isinstance(errors, list) else []
        return f"{', '.join(names)} not checked" if names else None
