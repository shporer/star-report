import json
import random
import socket
import threading
import time
from datetime import datetime, timezone

import numpy as np
import pytest
import requests
from astropy.table import MaskedColumn, Table

from star_report.cache import Cache
from star_report.config import load_config
from star_report.models import Position, StarIdentity
from star_report.sources import base
from star_report.sources.base import (
    Source,
    SourceContext,
    is_reachable,
    make_session,
    pick_server,
    records,
    run_sources,
    tap_rows,
)

RELEASE = threading.Event()  # set after a test so delayed fakes stop waiting


class Fake(Source):
    group = "gaia"
    service = "fake service"
    calls = 0

    def __init__(self, name, payload=None, delay=0.0, exc=None, needs=(), parse_exc=None):
        self.name = name
        self.payload = payload
        self.delay = delay
        self.exc = exc
        self.needs = needs
        self.parse_exc = parse_exc
        self.fetches = 0

    def query(self, ctx):
        return {"name": self.name}

    def fetch(self, ctx):
        self.fetches += 1
        if self.delay:
            RELEASE.wait(self.delay)
        if self.exc:
            raise self.exc
        return self.payload

    def parse(self, raw, ctx):
        if self.parse_exc:
            raise self.parse_exc
        return raw


@pytest.fixture(autouse=True)
def _release_and_clear():
    RELEASE.clear()
    is_reachable.cache_clear()
    yield
    RELEASE.set()
    is_reachable.cache_clear()


@pytest.fixture
def ctx(tmp_path):
    cfg = load_config(cwd=tmp_path)
    cfg.sources.timeout_s = 0.2
    identity = StarIdentity(
        input="x", main_name="X", simbad_id="X",
        position=Position(ra=1.0, dec=2.0, epoch=2000.0, source="simbad"),
        resolved_via="simbad",
    )
    return SourceContext(identity=identity, config=cfg, cache=Cache(tmp_path / "c", {"gaia": 90}), session=make_session())


def test_statuses(ctx):
    res = run_sources([Fake("a", payload={"x": 1}), Fake("b", payload=[]), Fake("c", exc=RuntimeError("boom"))], ctx)
    assert [r.status for r in res.values()] == ["ok", "empty", "error"]
    assert res["c"].error == "RuntimeError: boom" and res["a"].provenance.service == "fake service"


def test_timeout_does_not_block(ctx):
    t0 = time.monotonic()
    res = run_sources([Fake("slow", delay=5), Fake("fast", payload={"x": 1})], ctx)
    elapsed = time.monotonic() - t0
    RELEASE.set()
    assert res["slow"].status == "timeout" and res["fast"].status == "ok" and elapsed < 2


def test_timeout_frees_slot_for_queue(ctx):
    ctx.config.sources.max_workers = 1
    t0 = time.monotonic()
    res = run_sources([Fake("slow", delay=5), Fake("fast", payload={"x": 1})], ctx)
    elapsed = time.monotonic() - t0
    RELEASE.set()
    assert res["slow"].status == "timeout" and res["fast"].status == "ok" and elapsed < 2


def test_system_exit_is_error_status(ctx):
    r = run_sources([Fake("s", exc=SystemExit(3))], ctx)["s"]
    assert r.status == "error" and "SystemExit" in r.error


def test_skipped_when_needs_missing(ctx):
    assert run_sources([Fake("t", needs=("tic_id",))], ctx)["t"].model_dump(include={"status", "error"}) == {
        "status": "skipped", "error": "no TIC ID"}


def test_disabled_in_config(ctx):
    ctx.config.sources.enabled["a"] = False
    r = run_sources([Fake("a", payload={"x": 1})], ctx)["a"]
    assert (r.status, r.error) == ("skipped", "disabled in config")


def test_second_run_comes_from_cache(ctx):
    f = Fake("a", payload={"x": 1})
    r1 = run_sources([f], ctx)["a"]
    r2 = run_sources([f], ctx)["a"]
    assert f.fetches == 1 and (r1.provenance.from_cache, r2.provenance.from_cache) == (False, True)


def test_order_preserved(ctx):
    ctx.config.sources.timeout_s = 5
    rng = random.Random(1)
    names = [f"s{i}" for i in range(6)]
    fakes = [Fake(n, payload={"n": n}, delay=rng.random() * 0.1) for n in names]
    assert list(run_sources(fakes, ctx)) == names


def test_parse_error_is_error_status(ctx):
    r = run_sources([Fake("p", payload={"x": 1}, parse_exc=KeyError("tois"))], ctx)["p"]
    assert r.status == "error" and "KeyError" in r.error


def test_records_json_safe():
    t = Table()
    t["m"] = MaskedColumn([1, 2, 3], mask=[False, True, False])
    t["f"] = np.array([1.5, np.nan, 3.0], dtype=np.float32)
    t["i"] = np.array([1, 2, 3], dtype=np.int64)
    t["b"] = np.array([b"a", b"b", b"c"])
    t["g"] = np.array([np.inf, -np.inf, 1.0])
    out = records(t)
    json.dumps(out, allow_nan=False)
    assert out[1]["m"] is None and out[1]["f"] is None and out[0]["m"] == 1
    assert out[0]["i"] == 1 and out[0]["b"] == "a" and out[2]["f"] == 3.0
    assert [r["g"] for r in out] == [None, None, 1.0]


def test_session_user_agent():
    assert make_session().headers["User-Agent"] == "star-report/0.1.0"


def test_session_default_timeout(monkeypatch):
    seen = []

    def fake_send(self, request, **kwargs):
        seen.append(kwargs.get("timeout"))
        resp = requests.Response()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    s = make_session()
    s.get("http://example.test/")
    s.get("http://example.test/", timeout=5)
    assert seen == [30.0, 5]


def test_pick_server(monkeypatch):
    calls = []

    class Sock:
        def close(self):
            pass

    def create(addr, timeout=None):
        calls.append(addr[0])
        if addr[0] in down:
            raise OSError("down")
        return Sock()

    down = {"a"}
    monkeypatch.setattr(socket, "create_connection", create)
    assert pick_server(["a", "b"]) == "b"
    n = len(calls)
    assert pick_server(["a", "b"]) == "b" and len(calls) == n
    down.update({"x", "y"})
    assert pick_server(["x", "y"]) == "x"


def test_tap_rows(monkeypatch):
    got = {}

    class FakeResult:
        def to_table(self):
            t = Table()
            t["a"] = np.array([1, 2], dtype=np.int64)
            t["b"] = np.array([1.5, np.nan])
            return t

    class FakeTAP:
        def __init__(self, url, session=None):
            got["url"], got["session"] = url, session

        def run_sync(self, adql):
            got["adql"] = adql
            return FakeResult()

    monkeypatch.setattr(base.pyvo.dal, "TAPService", FakeTAP)
    session = make_session()
    assert tap_rows("http://tap.test", "SELECT 1", session) == [{"a": 1, "b": 1.5}, {"a": 2, "b": None}]
    assert got == {"url": "http://tap.test", "session": session, "adql": "SELECT 1"}
