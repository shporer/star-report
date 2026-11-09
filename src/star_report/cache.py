"""On-disk JSON cache with per-group expiry."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple


class CacheEntry(NamedTuple):
    payload: Any
    retrieved: datetime
    from_cache: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_empty(payload: Any) -> bool:
    """None, an empty container, or a dict whose values are all empty."""
    if payload is None:
        return True
    if isinstance(payload, dict):
        return all(_is_empty(v) for v in payload.values())
    if isinstance(payload, (list, tuple, set)):
        return len(payload) == 0
    return False


class Cache:
    def __init__(
        self,
        root: Path,
        expiry_days: Mapping[str, float | None],
        empty_expiry_days: float = 1.0,
        refresh: bool = False,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.root = Path(root)
        self.expiry_days = dict(expiry_days)
        self.empty_expiry_days = empty_expiry_days
        self.refresh = refresh
        self.now = now
        self._written: set[Path] = set()

    def fetch(
        self,
        group: str,
        name: str,
        query: Mapping[str, Any] | str,
        producer: Callable[[], Any],
    ) -> CacheEntry:
        if group not in self.expiry_days:
            raise ValueError(f"unknown cache group: {group!r}")
        path = self._path(group, name, query)
        if self.refresh is False or path in self._written:
            hit = self._read(path, self.expiry_days[group])
            if hit is not None:
                return hit
        payload = producer()
        retrieved = self.now()
        self._write(path, name, query, retrieved, payload)
        self._written.add(path)
        return CacheEntry(payload, retrieved, False)

    def _path(self, group: str, name: str, query: Mapping[str, Any] | str) -> Path:
        raw = json.dumps({"name": name, "query": query}, sort_keys=True, default=str)
        return self.root / group / f"{hashlib.sha256(raw.encode()).hexdigest()[:32]}.json"

    def _read(self, path: Path, group_expiry: float | None) -> CacheEntry | None:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            retrieved = datetime.fromisoformat(doc["retrieved"])
            payload = doc["payload"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if retrieved.tzinfo is None:
            retrieved = retrieved.replace(tzinfo=timezone.utc)
        limit = group_expiry
        if _is_empty(payload):
            limit = self.empty_expiry_days if limit is None else min(limit, self.empty_expiry_days)
        if limit is not None and self.now() - retrieved >= timedelta(days=limit):
            return None
        return CacheEntry(payload, retrieved, True)

    def _write(self, path: Path, name: str, query: Any, retrieved: datetime, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"name": name, "query": query, "retrieved": retrieved.isoformat(), "payload": payload}
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, allow_nan=False)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
