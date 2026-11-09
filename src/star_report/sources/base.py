"""Source interface, the parallel runner, and shared HTTP/table helpers."""

from __future__ import annotations

import functools
import json
import math
import socket
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

import numpy as np
import pyvo
import requests
from astropy.table import Table
from requests.adapters import HTTPAdapter

from star_report import __version__
from star_report.cache import Cache
from star_report.config import Config
from star_report.models import Provenance, SourceResult, StarIdentity

USER_AGENT = f"star-report/{__version__}"

_NEEDS_REASONS = {
    "tic_id": "no TIC ID",
    "gaia_dr3_id": "no Gaia DR3 ID",
    "simbad_id": "no SIMBAD entry",
}


class _TimeoutAdapter(HTTPAdapter):
    """Applies a default timeout when a call passes none (pyvo passes none)."""

    def __init__(self, default_timeout: float, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.default_timeout = default_timeout

    def send(self, request, **kwargs):  # type: ignore[override]
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.default_timeout
        return super().send(request, **kwargs)


def make_session(default_timeout: float = 30.0) -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    adapter = _TimeoutAdapter(default_timeout)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _json_safe(value: Any) -> Any:
    if value is np.ma.masked or value is None:
        return None
    if isinstance(value, (bytes, np.bytes_)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, np.generic):
        value = value.item()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, np.ndarray):
        return [_json_safe(v) for v in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def records(table: Table) -> list[dict]:
    names = table.colnames
    return [{n: _json_safe(row[n]) for n in names} for row in table]


def tap_rows(url: str, adql: str, session: requests.Session) -> list[dict]:
    return records(pyvo.dal.TAPService(url, session=session).run_sync(adql).to_table())


@functools.cache
def is_reachable(host: str, timeout: float = 3.0) -> bool:
    try:
        socket.create_connection((host, 443), timeout).close()
    except OSError:
        return False
    return True


def pick_server(hosts: Sequence[str]) -> str:
    for host in hosts:
        if is_reachable(host):
            return host
    return hosts[0]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SourceContext:
    identity: StarIdentity
    config: Config
    cache: Cache
    session: requests.Session
    ads_token: str | None = None
    now: Callable[[], datetime] = field(default=_utc_now)


class Source(ABC):
    name: ClassVar[str]
    group: ClassVar[str]
    service: ClassVar[str]
    catalog_version: ClassVar[str | None] = None
    needs: ClassVar[tuple[str, ...]] = ()

    def query(self, ctx: SourceContext) -> dict:
        """Cache key and provenance query."""
        return {}

    @abstractmethod
    def fetch(self, ctx: SourceContext) -> Any:
        """Network I/O; returns the raw JSON-safe payload."""

    @abstractmethod
    def parse(self, raw: Any, ctx: SourceContext) -> Any:
        """Pure; returns None or an empty container when there is no data."""

    def skip_reason(self, ctx: SourceContext) -> str | None:
        for field_name in self.needs:
            if getattr(ctx.identity, field_name) is None:
                return _NEEDS_REASONS.get(field_name, f"no {field_name}")
        return None

    def timeout_s(self, cfg: Config) -> float:
        return cfg.sources.timeout_s


def _error_text(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:300]


def run_source(src: Source, ctx: SourceContext) -> SourceResult:
    """Fetch (through the cache) and parse one source. Never raises."""
    t0 = time.monotonic()
    provenance: Provenance | None = None
    try:
        query = src.query(ctx)
        entry = ctx.cache.fetch(src.group, src.name, query, lambda: src.fetch(ctx))
        provenance = Provenance(
            service=src.service,
            query=json.dumps(query, sort_keys=True),
            catalog_version=src.catalog_version,
            retrieved=entry.retrieved,
            from_cache=entry.from_cache,
        )
        data = src.parse(entry.payload, ctx)
        status = "ok" if data else "empty"
        return SourceResult(source=src.name, status=status, data=data, provenance=provenance,
                            elapsed_s=time.monotonic() - t0)
    except Exception as e:  # noqa: BLE001 - a source must never take down the run
        return SourceResult(source=src.name, status="error", provenance=provenance,
                            error=_error_text(e), elapsed_s=time.monotonic() - t0)


def run_sources(sources: Sequence[Source], ctx: SourceContext) -> dict[str, SourceResult]:
    """Run sources in parallel (at most max_workers at a time); never raises.

    Each source runs in its own daemon thread, gated by a semaphore. The runner
    stops waiting on a timed-out source and frees its slot, but cannot kill its
    thread, so every source must still pass its own timeout to the underlying
    library (requests timeout=, astroquery TIMEOUT, or the pyvo session).
    """
    cfg = ctx.config
    results: dict[str, SourceResult] = {}
    runnable: list[Source] = []
    for src in sources:
        if not cfg.sources.enabled.get(src.name, True):
            reason: str | None = "disabled in config"
        else:
            reason = src.skip_reason(ctx)
        if reason:
            results[src.name] = SourceResult(source=src.name, status="skipped", error=reason)
        else:
            runnable.append(src)

    slots = threading.BoundedSemaphore(cfg.sources.max_workers)
    cond = threading.Condition()
    state = {s.name: "queued" for s in runnable}  # queued -> running -> done | timeout
    starts: dict[str, float] = {}

    def worker(src: Source) -> None:
        slots.acquire()
        with cond:
            state[src.name] = "running"
            starts[src.name] = time.monotonic()
        t0 = time.monotonic()
        try:
            res = run_source(src, ctx)
        except BaseException as e:  # noqa: BLE001 - nothing may escape the thread
            res = SourceResult(source=src.name, status="error", error=_error_text(e),
                               elapsed_s=time.monotonic() - t0)
        with cond:
            if state[src.name] == "running":
                state[src.name] = "done"
                results[src.name] = res
                slots.release()
                cond.notify_all()
            # else: already timed out and its slot freed; discard the late result

    for src in runnable:
        threading.Thread(target=worker, args=(src,), daemon=True, name=f"source-{src.name}").start()

    with cond:
        while any(st in ("queued", "running") for st in state.values()):
            cond.wait(0.2)
            now = time.monotonic()
            for src in runnable:
                if state[src.name] != "running":
                    continue
                limit = src.timeout_s(cfg)
                if now - starts[src.name] > limit:
                    state[src.name] = "timeout"
                    results[src.name] = SourceResult(
                        source=src.name, status="timeout",
                        error=f"timed out after {limit:g} s", elapsed_s=now - starts[src.name])
                    slots.release()
    return {s.name: results[s.name] for s in sources}
