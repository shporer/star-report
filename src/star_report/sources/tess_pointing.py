"""TESS sector coverage: which sectors put the star on silicon (tess-point), which have FFIs released (TESScut)."""

from __future__ import annotations

import functools
from datetime import datetime, timezone
from typing import Any

from star_report.coords import propagate
from star_report.sources.base import Source, SourceContext

TESSCUT_URL = "https://mast.stsci.edu/tesscut/api/v0.1/sector?ra={ra}&dec={dec}&radius=0"
POINTING_EPOCH = 2016.0


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value == value and float(value).is_integer() else None
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


@functools.cache
def sector_dates() -> dict[int, tuple[datetime, datetime]]:
    """Sector -> (start, end) in UTC from tesswcs.pointings; read once per process."""
    import tesswcs  # lazy: keeps importing this module cheap
    from astropy.time import Time

    table = tesswcs.pointings
    starts = Time([float(v) for v in table["Start"]], format="jd").to_datetime(timezone.utc)
    ends = Time([float(v) for v in table["End"]], format="jd").to_datetime(timezone.utc)
    return {int(s): (a, b) for s, a, b in zip(table["Sector"], starts, ends)}


def classify(on_silicon: list[dict], released: list[int] | None,
             dates: dict[int, tuple[datetime, datetime]], now: datetime,
             released_cameras: dict[int, str] | None = None) -> dict:
    """Sort sectors into observed / in_progress / pending / upcoming. Pure.

    Every released sector is observed, even one tess-point misses; its cam/ccd then
    comes from `released_cameras` (TESScut). The other buckets use on-silicon sectors only.
    """
    released_set = set(released or [])
    unknown = released is None
    out: dict[str, Any] = {"observed": [], "in_progress": [], "pending": [], "upcoming": [],
                           "cameras": {}}
    if unknown:
        out["release_unknown"] = True
    on_sectors = {e["sector"] for e in on_silicon}
    extra = sorted(released_set - on_sectors)
    out["observed"].extend(extra)
    for sector in extra:
        cam = (released_cameras or {}).get(sector)
        if cam:
            out["cameras"][sector] = cam
    for entry in sorted(on_silicon, key=lambda e: e["sector"]):
        sector = entry["sector"]
        out["cameras"][sector] = f"{entry.get('camera')}/{entry.get('ccd')}"
        window = dates.get(sector)
        if window is None:  # beyond the date table
            if sector in released_set:
                out["observed"].append(sector)
            else:
                out["upcoming"].append({"sector": sector, "start": None})
            continue
        start, end = window
        if sector in released_set or (unknown and end < now):
            out["observed"].append(sector)
        elif start > now:
            out["upcoming"].append({"sector": sector, "start": start.isoformat()})
        elif end < now:
            out["pending"].append(sector)
        else:
            out["in_progress"].append(sector)
    out["observed"].sort()
    out["cameras"] = dict(sorted(out["cameras"].items()))
    return out


def _position(ctx: SourceContext) -> tuple[float, float]:
    ra, dec = propagate(ctx.identity.position, POINTING_EPOCH)
    return float(ra), float(dec)


class TessSectors(Source):
    name = "tess_pointing"
    group = "mast"
    service = "tess-point + TESScut"

    def query(self, ctx: SourceContext) -> dict:
        ra, dec = _position(ctx)
        return {"ra": ra, "dec": dec}

    def fetch(self, ctx: SourceContext) -> Any:
        from tess_stars2px import tess_stars2px_function_entry

        ra, dec = _position(ctx)
        _, _, _, sectors, cameras, ccds, _, _, _ = tess_stars2px_function_entry([0], [ra], [dec])
        on_silicon = [{"sector": int(s), "camera": int(c), "ccd": int(d)}
                      for s, c, d in zip(sectors, cameras, ccds)]
        released: list[int] | None = None
        released_cameras: list[dict] = []
        error: str | None = None
        try:
            resp = ctx.session.get(TESSCUT_URL.format(ra=ra, dec=dec),
                                   timeout=self.timeout_s(ctx.config))
            resp.raise_for_status()
            payload = resp.json()
            results = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(results, list):
                raise ValueError("unexpected TESScut payload")
            found: dict[int, dict] = {}
            for r in results:
                sector = _int(r.get("sector")) if isinstance(r, dict) else None
                if sector is not None:
                    found.setdefault(sector, {"sector": sector, "camera": _int(r.get("camera")),
                                              "ccd": _int(r.get("ccd"))})
            released = sorted(found)
            released_cameras = [found[s] for s in released]
        except Exception as e:  # noqa: BLE001 - TESScut failure must not fail the source
            error = f"{type(e).__name__}: {e}"
        return {"on_silicon": on_silicon, "released": released,
                "released_cameras": released_cameras, "released_error": error}

    def parse(self, raw: Any, ctx: SourceContext) -> dict:
        raw = raw if isinstance(raw, dict) else {}
        on_silicon: dict[int, dict] = {}
        entries = raw.get("on_silicon")
        for e in entries if isinstance(entries, list) else []:
            sector = _int(e.get("sector")) if isinstance(e, dict) else None
            if sector is not None:
                on_silicon.setdefault(sector, {"sector": sector, "camera": _int(e.get("camera")),
                                               "ccd": _int(e.get("ccd"))})
        released_raw = raw.get("released")
        released = None
        if isinstance(released_raw, list):
            released = sorted({s for v in released_raw if (s := _int(v)) is not None})
        cams: dict[int, str] = {}
        cam_raw = raw.get("released_cameras")
        for e in cam_raw if isinstance(cam_raw, list) else []:
            sector = _int(e.get("sector")) if isinstance(e, dict) else None
            if sector is not None:
                cams[sector] = f"{_int(e.get('camera'))}/{_int(e.get('ccd'))}"
        out = classify(list(on_silicon.values()), released, sector_dates(), ctx.now(), cams)
        if not (out["observed"] or out["in_progress"] or out["pending"] or out["upcoming"]):
            return {}
        error = raw.get("released_error")
        if released is None:
            out["released_error"] = error if isinstance(error, str) else None
        return out
