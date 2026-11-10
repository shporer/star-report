"""SIMBAD source: identity, spectral type, magnitudes and identifiers via TAP."""

from __future__ import annotations

from typing import Any

import requests

from star_report.config import Config
from star_report.sources.base import Source, SourceContext, pick_server, tap_rows

_ADQL = (
    "SELECT b.main_id, b.ra, b.dec, b.otype, b.sp_type, b.rvz_radvel, b.nbref, "
    "b.pmra, b.pmdec, b.plx_value, f.B, f.V, ids.ids "
    "FROM ident AS i JOIN basic AS b ON b.oid = i.oidref "
    "LEFT JOIN allfluxes AS f ON f.oidref = b.oid LEFT JOIN ids ON ids.oidref = b.oid "
    "WHERE i.id = '{name}'"
)


def adql(name: str) -> str:
    return _ADQL.format(name=name.replace("'", "''"))


def _url(cfg: Config) -> str:
    servers = cfg.servers
    host = pick_server(servers.simbad, servers.probes.simbad, servers.probe_timeout_s)
    return f"https://{host}/simbad/sim-tap"


def _fetch(name: str, session: requests.Session, cfg: Config) -> list[dict]:
    return tap_rows(_url(cfg), adql(name), session)


def _normalize(row: dict) -> dict:
    ids = row.get("ids")
    return {
        "main_id": row.get("main_id"),
        "ra": row.get("ra"),
        "dec": row.get("dec"),
        "otype": row.get("otype"),
        "sp_type": row.get("sp_type"),
        "rv": row.get("rvz_radvel"),
        "nbref": row.get("nbref"),
        "pmra": row.get("pmra"),
        "pmdec": row.get("pmdec"),
        "plx": row.get("plx_value"),
        "B": row.get("B"),
        "V": row.get("V"),
        "ids": [i.strip() for i in ids.split("|") if i.strip()] if ids else [],
    }


def lookup(name: str, session: requests.Session, cfg: Config) -> list[dict]:
    return [_normalize(r) for r in _fetch(name, session, cfg)]


class Simbad(Source):
    name = "simbad"
    group = "simbad"
    service = "SIMBAD TAP"
    needs = ("simbad_id",)

    def query(self, ctx: SourceContext) -> dict:
        return {"name": ctx.identity.simbad_id}

    def fetch(self, ctx: SourceContext) -> Any:
        return _fetch(ctx.identity.simbad_id, ctx.session, ctx.config)

    def parse(self, raw: Any, ctx: SourceContext) -> dict | None:
        return _normalize(raw[0]) if raw else None
