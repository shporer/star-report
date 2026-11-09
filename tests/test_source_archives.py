import pytest

from conftest import load_fixture, make_ctx, ref_identity

from star_report.models import Position
from star_report.sources import eso as eso_src
from star_report.sources import koa as koa_src
from star_report.sources.eso import EsoSpectra, mjd_to_date
from star_report.sources.koa import KeckKoa, summarize_koa
from star_report.sources.registry import build_sources


def test_mjd_to_date():
    assert mjd_to_date(59000) == "2020-05-31"
    assert mjd_to_date(59000.9) == "2020-05-31"
    assert mjd_to_date("59000") is None
    assert mjd_to_date(None) is None
    assert mjd_to_date(float("nan")) is None


def test_eso_grouping_and_dates():
    rows = [{"instrument_name": "HARPS", "t_min": 59000.0, "dp_id": "a"},
            {"instrument_name": "HARPS", "t_min": 59010.5, "dp_id": "b"},
            {"instrument_name": "UVES", "t_min": 58000.0, "dp_id": "c"},
            {"instrument_name": "ESPRESSO", "t_min": 59100.0, "dp_id": "d"},
            {"instrument_name": "ESPRESSO", "t_min": None, "dp_id": "e"},
            {"instrument_name": "ESPRESSO", "t_min": 59200.0, "dp_id": "f"}]
    out = EsoSpectra().parse(rows, make_ctx())
    assert out["total"] == 6
    assert [i["instrument"] for i in out["instruments"]] == ["ESPRESSO", "HARPS", "UVES"]
    assert out["instruments"][0] == {"instrument": "ESPRESSO", "n": 3,
                                     "first": mjd_to_date(59100), "last": mjd_to_date(59200)}
    assert out["instruments"][1] == {"instrument": "HARPS", "n": 2,
                                     "first": "2020-05-31", "last": "2020-06-10"}


def test_eso_empty():
    assert EsoSpectra().parse([], make_ctx()) == {}


@pytest.mark.parametrize("raw", [None, 5, "x", {"a": 1}, [1, "x", None],
                                 [{"instrument_name": 5, "t_min": "bad"}],
                                 [{"instrument_name": "A", "t_min": "bad"}]])
def test_eso_malformed(raw):
    out = EsoSpectra().parse(raw, make_ctx())
    assert isinstance(out, dict)
    if out:
        assert out["instruments"][0]["first"] is None


def test_eso_query_uses_intersects_and_coerced_values(monkeypatch):
    seen = {}

    def fake(url, adql, session):
        seen.update(url=url, adql=adql)
        return []

    monkeypatch.setattr(eso_src, "tap_rows", fake)
    ctx = make_ctx()
    assert EsoSpectra().fetch(ctx) == []
    assert seen["url"] == "http://archive.eso.org/tap_obs"
    assert "INTERSECTS(CIRCLE('ICRS'," in seen["adql"] and "s_region) = 1" in seen["adql"]
    assert "dataproduct_type = 'spectrum'" in seen["adql"]
    assert str(5 / 3600) in seen["adql"]


def obj(date, kind="object", prog="P1"):
    return {"koaimtyp": kind, "date_obs": date, "progid": prog}


def test_summarize_koa_counts_object_frames_only():
    rows = [obj("2010-01-02 03:04:05"), obj("2010-01-02 04:00:00"),
            obj("2010-01-05 01:00:00", prog="P2"),
            obj("2009-01-01 00:00:00", kind="arclamp")]
    s = summarize_koa("hires", rows)
    assert s == {"instrument": "hires", "frames": 3, "nights": 2, "first": "2010-01-02",
                 "last": "2010-01-05", "programs": ["P1", "P2"]}


def test_summarize_koa_no_object_frames():
    s = summarize_koa("kpf", [obj("2010-01-01", kind="dark"), "junk", None])
    assert s["frames"] == 0 and s["nights"] == 0 and s["first"] is None and s["programs"] == []


def test_koa_parse_keeps_frames_and_errors():
    raw = [{"instrument": "hires", "frames": 5, "nights": 2, "first": "a", "last": "b", "programs": []},
           {"instrument": "kpf", "frames": 0, "nights": 0, "first": None, "last": None, "programs": []},
           {"instrument": "esi", "error": "boom"}]
    out = KeckKoa().parse(raw, make_ctx())
    assert [i["instrument"] for i in out["instruments"]] == ["hires"]
    assert out["errors"] == [{"instrument": "esi", "error": "boom"}]
    assert KeckKoa().parse([raw[1]], make_ctx()) == {}
    assert KeckKoa().parse([], make_ctx()) == {}


@pytest.mark.parametrize("raw", [None, 5, "x", {"a": 1}, [1, "x", None], [{"instrument": "a", "frames": "many"}],
                                 [{"frames": 3}]])
def test_koa_malformed(raw):
    assert isinstance(KeckKoa().parse(raw, make_ctx()), dict)


def test_koa_fetch_per_instrument_with_failure_isolated(monkeypatch):
    calls = []

    def fake(url, adql, session):
        calls.append((url, adql))
        if "koa_nirspec" in adql:
            raise RuntimeError("down")
        return [obj("2010-01-02 00:00:00")]

    monkeypatch.setattr(koa_src, "tap_rows", fake)
    ctx = make_ctx()
    ctx.config.sources.koa_instruments[:] = ["hires", "nirspec", "bad name; DROP", "KPF"]
    raw = KeckKoa().fetch(ctx)
    assert [r["instrument"] for r in raw] == ["hires", "nirspec"]
    assert raw[0]["frames"] == 1
    assert raw[1]["instrument"] == "nirspec" and "RuntimeError" in raw[1]["error"]
    assert all(u == "https://koa.ipac.caltech.edu/TAP" for u, _ in calls)
    assert len(calls) == 2
    adql = next(a for _, a in calls if "koa_hires" in a)
    assert adql.startswith("SELECT koaimtyp, date_obs, progid FROM koa_hires WHERE CONTAINS(POINT('ICRS', ra, dec)")


def test_query_values_are_floats():
    ctx = make_ctx()
    q = EsoSpectra().query(ctx)
    assert all(isinstance(q[k], float) for k in ("ra", "dec", "radius")) and q["radius"] == 5.0
    assert KeckKoa().query(ctx)["radius"] == 10.0


def test_recorded_hd209458():
    ctx = make_ctx()
    eso = EsoSpectra().parse(load_fixture("eso", "HD_209458"), ctx)
    names = {i["instrument"] for i in eso["instruments"]}
    assert {"ESPRESSO", "HARPS"} <= names
    koa = KeckKoa().parse(load_fixture("koa", "HD_209458"), ctx)
    hires = next(i for i in koa["instruments"] if i["instrument"] == "hires")
    assert hires["frames"] >= 100


def test_registry_order():
    names = [s.name for s in build_sources(make_ctx().config)]
    assert names.index("tess_pointing") < names.index("eso") < names.index("koa")


def _fetch_with(monkeypatch, failing):
    def fake(url, adql, session):
        if any(f"koa_{i}" in adql for i in failing):
            raise RuntimeError("down")
        return [obj("2010-01-02 00:00:00")]

    monkeypatch.setattr(koa_src, "tap_rows", fake)
    ctx = make_ctx()
    ctx.config.sources.koa_instruments[:] = ["hires", "kpf"]
    return ctx, KeckKoa().fetch(ctx)


def test_koa_all_instruments_failing_raises(monkeypatch):
    with pytest.raises(RuntimeError, match="all KOA instrument queries failed: RuntimeError: down"):
        _fetch_with(monkeypatch, ["hires", "kpf"])


def test_koa_mixed_payload_and_parse_keep_errors(monkeypatch):
    ctx, raw = _fetch_with(monkeypatch, ["kpf"])
    assert raw[0]["frames"] == 1 and set(raw[1]) == {"instrument", "error"}
    out = KeckKoa().parse(raw, ctx)
    assert [i["instrument"] for i in out["instruments"]] == ["hires"]
    assert out["errors"][0]["instrument"] == "kpf"


def test_koa_no_valid_instruments_returns_empty(monkeypatch):
    monkeypatch.setattr(koa_src, "tap_rows", lambda *a: pytest.fail("no query expected"))
    ctx = make_ctx()
    ctx.config.sources.koa_instruments[:] = ["Bad Name"]
    assert KeckKoa().fetch(ctx) == []
