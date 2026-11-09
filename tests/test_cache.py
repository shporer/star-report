import json
from datetime import datetime, timedelta, timezone

import pytest

from star_report.cache import Cache

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def test_miss_then_hit_across_instances(tmp_path):
    calls = []
    first = Cache(tmp_path, {"gaia": 90}).fetch("gaia", "core", {"id": 1}, lambda: calls.append(1) or {"x": 1})
    second = Cache(tmp_path, {"gaia": 90}).fetch("gaia", "core", {"id": 1}, lambda: calls.append(2) or {"x": 2})
    assert (first.payload, first.from_cache, second.payload, second.from_cache) == ({"x": 1}, False, {"x": 1}, True)
    assert calls == [1] and second.retrieved == first.retrieved


def test_expired_entry_is_refetched(tmp_path):
    clock = Clock()
    cache = Cache(tmp_path, {"gaia": 90}, now=clock)
    cache.fetch("gaia", "core", "q", lambda: {"x": 1})
    clock.t = T0 + timedelta(days=89)
    assert cache.fetch("gaia", "core", "q", lambda: {"x": 2}).from_cache
    clock.t = T0 + timedelta(days=91)
    entry = cache.fetch("gaia", "core", "q", lambda: {"x": 3})
    assert (entry.payload, entry.from_cache) == ({"x": 3}, False)


def test_null_expiry_never_expires(tmp_path):
    clock = Clock()
    cache = Cache(tmp_path, {"llm": None}, now=clock)
    cache.fetch("llm", "o", "q", lambda: {"x": 1})
    clock.t = T0 + timedelta(days=3650)
    entry = cache.fetch("llm", "o", "q", lambda: {"x": 2})
    assert (entry.payload, entry.from_cache) == ({"x": 1}, True)


def test_empty_payload_expires_after_a_day(tmp_path):
    clock = Clock()
    cache = Cache(tmp_path, {"vizier": 90}, now=clock)
    cache.fetch("vizier", "t", "q", lambda: {"t": []})
    clock.t = T0 + timedelta(days=0.5)
    assert cache.fetch("vizier", "t", "q", lambda: {"t": [1]}).from_cache
    clock.t = T0 + timedelta(days=1.5)
    entry = cache.fetch("vizier", "t", "q", lambda: {"t": [1]})
    assert (entry.payload, entry.from_cache) == ({"t": [1]}, False)


def test_empty_expiry_never_exceeds_group_expiry(tmp_path):
    clock = Clock()
    cache = Cache(tmp_path, {"vizier": 0.25}, empty_expiry_days=1.0, now=clock)
    cache.fetch("vizier", "t", "q", lambda: None)
    clock.t = T0 + timedelta(days=0.5)
    assert not cache.fetch("vizier", "t", "q", lambda: {"a": 1}).from_cache


def test_refresh_ignores_old_entries_but_reuses_this_runs(tmp_path):
    Cache(tmp_path, {"gaia": 90}).fetch("gaia", "core", "q", lambda: {"x": "old"})
    cache = Cache(tmp_path, {"gaia": 90}, refresh=True)
    first = cache.fetch("gaia", "core", "q", lambda: {"x": "new"})
    second = cache.fetch("gaia", "core", "q", lambda: {"x": "newer"})
    assert (first.payload, first.from_cache) == ({"x": "new"}, False)
    assert (second.payload, second.from_cache) == ({"x": "new"}, True)


def test_query_key_is_order_independent(tmp_path):
    cache = Cache(tmp_path, {"gaia": 90})
    cache.fetch("gaia", "core", {"a": 1, "b": 2}, lambda: {"x": 1})
    assert cache.fetch("gaia", "core", {"b": 2, "a": 1}, lambda: {"x": 2}).from_cache


def test_producer_error_writes_nothing(tmp_path):
    cache = Cache(tmp_path, {"gaia": 90})

    def boom():
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        cache.fetch("gaia", "core", "q", boom)
    entry = cache.fetch("gaia", "core", "q", lambda: {"x": 1})
    assert not entry.from_cache
    assert not list((tmp_path / "gaia").glob("*.tmp"))


def test_corrupt_file_is_a_miss(tmp_path):
    cache = Cache(tmp_path, {"gaia": 90})
    cache.fetch("gaia", "core", "q", lambda: {"x": 1})
    (path,) = (tmp_path / "gaia").glob("*.json")
    path.write_text("{")
    entry = Cache(tmp_path, {"gaia": 90}).fetch("gaia", "core", "q", lambda: {"x": 2})
    assert (entry.payload, entry.from_cache) == ({"x": 2}, False)
    assert json.loads(path.read_text())["payload"] == {"x": 2}


def test_unknown_group_raises(tmp_path):
    with pytest.raises(ValueError, match="nope"):
        Cache(tmp_path, {"gaia": 90}).fetch("nope", "x", {}, dict)


@pytest.mark.parametrize("payload", [{"x": object()}, {"x": float("nan")}])
def test_non_json_payload_raises_and_writes_nothing(tmp_path, payload):
    cache = Cache(tmp_path, {"gaia": 90})
    with pytest.raises((TypeError, ValueError)):
        cache.fetch("gaia", "core", "q", lambda: payload)
    assert not list((tmp_path / "gaia").glob("*"))
