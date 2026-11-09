"""MAST observation inventory: what HST, JWST, TESS, Kepler, K2 and HLSP hold for the star."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from astropy.coordinates import SkyCoord
from astropy.table import Table
from astroquery import mast

from star_report.coords import propagate
from star_report.sources.base import Source, SourceContext, records

PORTAL = "https://mast.stsci.edu/portal/Mashup/Clients/Mast/Portal.html?searchQuery={ra:.5f}%20{dec:.5f}"
ROW_COLLECTIONS = ("TESS", "HLSP", "Kepler", "K2")
ROW_KEYS = ("obs_collection", "provenance_name", "project", "target_name", "obs_id",
            "product", "t_exptime", "sequence_number")
_SECTOR_TAG = re.compile(r"(?<![A-Za-z])s\d{4}(?!\d)")
_LONG_CADENCE_S = 900  # Kepler/K2: 1800 s is long cadence, 60 s short


def _j2000(ctx: SourceContext) -> tuple[float, float]:
    ra, dec = propagate(ctx.identity.position, 2000.0)
    return float(ra), float(dec)


def _radius(ctx: SourceContext) -> float:
    return float(ctx.config.sources.radius_arcsec["mast"])


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value == value and float(value).is_integer() else None
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value else None


def aggregate(table: Table) -> dict:
    """Pre-aggregate a query_region table: HST alone can return thousands of rows."""
    counts: Counter = Counter()
    rows: list[dict] = []
    for r in records(table):
        coll = _text(r.get("obs_collection"))
        if coll in ROW_COLLECTIONS:
            trimmed = {k: r.get(k) for k in ROW_KEYS if k != "product"}
            trimmed["product"] = _text(r.get("dataproduct_type"))
            rows.append({k: trimmed.get(k) for k in ROW_KEYS})
        else:
            product = _text(r.get("dataproduct_type"))
            counts[(coll, _text(r.get("instrument_name")), product.lower() if product else None)] += 1
    return {"counts": [{"collection": c, "instrument": i, "product": p, "n": n}
                       for (c, i, p), n in sorted(counts.items(), key=lambda kv: tuple(map(str, kv[0])))],
            "rows": rows}


def _tess(rows: list[dict]) -> dict:
    out: dict[str, set[int]] = {"spoc_120s": set(), "spoc_20s": set(), "ffi": set()}
    for r in rows:
        if r.get("obs_collection") != "TESS" or r.get("provenance_name") != "SPOC":
            continue
        obs_id = _text(r.get("obs_id")) or ""
        sector = _int(r.get("sequence_number"))
        if sector is None or len(_SECTOR_TAG.findall(obs_id)) > 1:
            continue
        if _text(r.get("target_name")) == "TESS FFI":
            out["ffi"].add(sector)
        elif obs_id.endswith("-a_fast"):
            out["spoc_20s"].add(sector)
        elif obs_id.endswith("-s"):
            out["spoc_120s"].add(sector)
    return {k: sorted(v) for k, v in out.items()}


def _hlsp(rows: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for r in rows:
        if r.get("obs_collection") != "HLSP":
            continue
        prov = _text(r.get("provenance_name")) or "unknown"
        g = groups.setdefault(prov, {"provenance": prov, "n": 0, "sectors": set()})
        g["n"] += 1
        sector = _int(r.get("sequence_number"))
        if sector is not None:
            g["sectors"].add(sector)
    return [{**g, "sectors": sorted(g["sectors"])} for _, g in sorted(groups.items())]


def _cadence(rows: list[dict], collection: str) -> dict:
    long_ = short = 0
    for r in rows:
        exp = _num(r.get("t_exptime"))
        if r.get("obs_collection") != collection or exp is None:
            continue
        if exp >= _LONG_CADENCE_S:
            long_ += 1
        else:
            short += 1
    return {"n_long": long_, "n_short": short}


class MastObservations(Source):
    name = "mast"
    group = "mast"
    service = "MAST Observations"

    def query(self, ctx: SourceContext) -> dict:
        ra, dec = _j2000(ctx)
        return {"ra": ra, "dec": dec, "radius": _radius(ctx)}

    def fetch(self, ctx: SourceContext) -> Any:
        ra, dec = _j2000(ctx)
        client = mast.ObservationsClass()
        # Per-instance, no global write. astroquery only checks this timeout after the
        # call returns, so it never bounds a hung socket; the runner's timeout is the guard.
        client.TIMEOUT = ctx.config.sources.timeout_s
        table = client.query_region(SkyCoord(ra, dec, unit="deg"), radius=f"{_radius(ctx)} arcsec")
        return aggregate(table)

    def parse(self, raw: Any, ctx: SourceContext) -> dict:
        raw = raw if isinstance(raw, dict) else {}
        rows = [r for r in raw.get("rows", []) if isinstance(r, dict)] \
            if isinstance(raw.get("rows"), list) else []
        counts = raw.get("counts")
        other = []
        for c in counts if isinstance(counts, list) else []:
            n = _int(c.get("n")) if isinstance(c, dict) else None
            if n is not None:
                other.append({"collection": c.get("collection"), "instrument": c.get("instrument"),
                              "product": c.get("product"), "n": n})
        other.sort(key=lambda o: -o["n"])
        ra, dec = _j2000(ctx)
        return {"tess": _tess(rows), "hlsp": _hlsp(rows),
                "kepler": _cadence(rows, "Kepler"), "k2": _cadence(rows, "K2"),
                "other": other, "portal_url": PORTAL.format(ra=ra, dec=dec)}
