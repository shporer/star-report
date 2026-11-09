from datetime import datetime, timedelta, timezone

import pytest

from conftest import load_fixture, make_ctx, ref_identity

from star_report.sources import tess_pointing as tp
from star_report.sources.registry import build_sources
from star_report.sources.tess_pointing import TessSectors, classify

UTC = timezone.utc
NOW = datetime(2026, 11, 9, tzinfo=UTC)


def window(start_days, end_days):
    return (NOW + timedelta(days=start_days), NOW + timedelta(days=end_days))


# 1 long past, 2 past, 3 current, 4 future
DATES = {1: window(-300, -273), 2: window(-100, -73), 3: window(-10, 17), 4: window(50, 77)}
ON = [{"sector": s, "camera": 1, "ccd": s} for s in (1, 2, 3, 4, 200)]


def test_classify_buckets():
    out = classify(ON, [1], DATES, NOW)
    assert out["observed"] == [1]
    assert out["pending"] == [2]
    assert out["in_progress"] == [3]
    assert [u["sector"] for u in out["upcoming"]] == [4, 200]
    assert out["upcoming"][0]["start"] == DATES[4][0].isoformat()
    assert out["upcoming"][1]["start"] is None
    assert out["cameras"] == {1: "1/1", 2: "1/2", 3: "1/3", 4: "1/4", 200: "1/200"}
    assert "release_unknown" not in out


def test_classify_released_in_progress_is_observed():
    out = classify(ON, [1, 3], DATES, NOW)
    assert out["observed"] == [1, 3]
    assert out["in_progress"] == []


def test_classify_tesscut_failed():
    out = classify(ON, None, DATES, NOW)
    assert out["release_unknown"] is True
    assert out["observed"] == [1, 2]
    assert out["in_progress"] == [3]
    assert out["pending"] == []
    assert [u["sector"] for u in out["upcoming"]] == [4, 200]


def test_registered_after_mast():
    names = [s.name for s in build_sources(make_ctx().config)]
    assert names.index("mast") < names.index("tess_pointing")


def parse(slug, now=NOW):
    ctx = make_ctx(identity=ref_identity(slug), now=now)
    return TessSectors().parse(load_fixture("tess_pointing", slug), ctx)


def test_hd209458_fixture():
    out = parse("HD_209458")
    assert {56, 82} <= set(out["observed"])


def test_toi700_fixture():
    out = parse("TOI-700")
    assert len(out["observed"]) >= 30


def test_sector_dates_utc():
    dates = tp.sector_dates()
    start, end = dates[56]
    assert start.tzinfo is not None and start < end
    assert start.year == 2022


def test_no_coverage_is_empty():
    ctx = make_ctx(now=NOW)
    raw = {"on_silicon": [], "released": [], "released_error": None}
    assert TessSectors().parse(raw, ctx) == {}


def test_released_error_surfaces():
    ctx = make_ctx(now=NOW)
    raw = {"on_silicon": [{"sector": 56, "camera": 1, "ccd": 2}], "released": None,
           "released_error": "boom"}
    out = TessSectors().parse(raw, ctx)
    assert out["release_unknown"] is True and out["released_error"] == "boom"


@pytest.mark.parametrize("raw", [None, [], "x", 5, {"on_silicon": 5}, {"on_silicon": ["a", None]},
                                 {"on_silicon": [{"sector": "x"}], "released": "abc"},
                                 {"on_silicon": [{"sector": 1}], "released": [None, "z", {}]}])
def test_malformed_payloads(raw):
    TessSectors().parse(raw, make_ctx(now=NOW))


class FakeResp:
    def __init__(self, payload=None, exc=None):
        self.payload, self.exc = payload, exc

    def raise_for_status(self):
        if self.exc:
            raise self.exc

    def json(self):
        return self.payload


def test_fetch_tesscut_failure_does_not_fail(monkeypatch):
    import requests
    ctx = make_ctx()

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(ctx.session, "get", boom)
    out = TessSectors().fetch(ctx)
    assert out["released"] is None and "down" in out["released_error"]
    assert {56, 82} <= {r["sector"] for r in out["on_silicon"]}


def test_fetch_parses_tesscut(monkeypatch):
    ctx = make_ctx()
    calls = []

    def get(url, **k):
        calls.append((url, k))
        return FakeResp({"results": [{"sector": "0056", "camera": "1", "ccd": "2"}, "junk"]})
    monkeypatch.setattr(ctx.session, "get", get)
    out = TessSectors().fetch(ctx)
    assert out["released"] == [56] and out["released_error"] is None
    assert calls[0][0].startswith("https://mast.stsci.edu/tesscut/api/v0.1/sector")
    assert calls[0][1]["timeout"] == ctx.config.sources.timeout_s
