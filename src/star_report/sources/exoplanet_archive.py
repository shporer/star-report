"""NASA Exoplanet Archive: the TAP helper and the TOI/KOI host lookups used by the resolver."""

from __future__ import annotations

import requests

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
