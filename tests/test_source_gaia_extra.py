import pytest

from conftest import load_fixture, make_ctx, ref_identity

from star_report.sources import gaia
from star_report.sources.gaia import GaiaNss, GaiaVari

SLUGS = ["HD_209458", "TOI-700", "Kepler-10", "Barnards_star"]


@pytest.mark.parametrize("slug", SLUGS)
def test_nss_fixtures_smoke(slug):
    ctx = make_ctx(identity=ref_identity(slug))
    assert isinstance(GaiaNss().parse(load_fixture("gaia_nss", slug), ctx), (list, type(None)))


@pytest.mark.parametrize("slug", SLUGS)
def test_vari_fixtures_smoke(slug):
    ctx = make_ctx(identity=ref_identity(slug))
    assert isinstance(GaiaVari().parse(load_fixture("gaia_vari", slug), ctx), (dict, type(None)))


def test_nss_synthetic(ctx):
    raw = [{"source_id": 1, "nss_solution_type": "Orbital", "period": 3.5, "period_error": 0.01,
            "eccentricity": 0.1, "eccentricity_error": 0.02, "semi_amplitude_primary": 12.5,
            "inclination": 80.0, "mass_ratio": None}]
    assert GaiaNss().parse(raw, ctx) == [{
        "type": "Orbital", "period_d": 3.5, "period_err": 0.01, "ecc": 0.1, "k1_kms": 12.5,
        "inclination": 80.0, "mass_ratio": None}]


def test_nss_empty(ctx):
    assert GaiaNss().parse([], ctx) is None


def test_vari_frequency_to_period(ctx):
    raw = {"summary": {"source_id": "1", "in_vari_eclipsing_binary": True},
           "classifier": [{"classifier_name": "nTransits:5+", "best_class_name": "ECL",
                           "best_class_score": 0.9}],
           "periods": {"eclipsing_binary": [{"frequency": 0.25}]}}
    assert GaiaVari().parse(raw, ctx) == {
        "classes": [{"classifier": "nTransits:5+", "class": "ECL", "score": 0.9}],
        "periods": [{"type": "eclipsing_binary", "period_d": 4.0}]}


def test_vari_period_columns(ctx):
    raw = {"summary": {"in_vari_ms_oscillator": True},
           "periods": {"ms_oscillator": [{"frequency1": 2.0}],
                       "rrlyrae": [{"pf": 0.5}],
                       "rotation_modulation": [{"best_rotation_period": 7.0}],
                       "planetary_transit": [{"transit_period": 3.0}],
                       "compact_companion": [{"period": 9.0}]}}
    got = {p["type"]: p["period_d"] for p in GaiaVari().parse(raw, ctx)["periods"]}
    assert got == {"ms_oscillator": 0.5, "rrlyrae": 0.5, "rotation_modulation": 7.0,
                   "planetary_transit": 3.0, "compact_companion": 9.0}


def test_vari_missing_period_column_skipped(ctx):
    raw = {"summary": {}, "periods": {"cepheid": [{"other": 1}], "rrlyrae": [{"pf": None}],
                                      "long_period_variable": [{"frequency": 0}]}}
    assert GaiaVari().parse(raw, ctx) == {"classes": [], "periods": []}


def test_vari_no_summary(ctx):
    assert GaiaVari().parse({"summary": None}, ctx) == {}


def test_hd209458_vari_summary():
    ctx = make_ctx(identity=ref_identity("HD_209458"))
    raw = load_fixture("gaia_vari", "HD_209458")
    assert raw["summary"]["source_id"] == ctx.identity.gaia_dr3_id
    assert all(k == "source_id" or k.startswith("in_vari_") for k in raw["summary"])
    d = GaiaVari().parse(raw, ctx)
    assert [(c["class"], c["classifier"]) for c in d["classes"]] == [("EP", "nTransits:5+")]
    assert [p["type"] for p in d["periods"]] == ["planetary_transit"]
    assert 0.5 < d["periods"][0]["period_d"] < 5


@pytest.mark.parametrize("raw", [
    None, [], "text", 5, {}, {"summary": 5}, {"summary": []}, {"summary": {}, "periods": []},
    {"summary": {}, "periods": {"rrlyrae": 5}}, {"summary": {}, "periods": {"rrlyrae": [None, "x"]}},
    {"summary": {}, "periods": {"rrlyrae": [{"pf": "abc"}]}},
    {"summary": {}, "classifier": {"a": 1}}, {"summary": {}, "classifier": [None, 3]},
    {"summary": {}, "classifier": [{"best_class_score": "x"}]},
])
def test_vari_malformed_shapes(ctx, raw):
    out = GaiaVari().parse(raw, ctx)
    assert out is None or isinstance(out, dict)


@pytest.mark.parametrize("raw", [None, {}, "text", 5, [None, 3], [{"period": "abc"}], {"a": 1}])
def test_nss_malformed_shapes(ctx, raw):
    out = GaiaNss().parse(raw, ctx)
    assert out is None or isinstance(out, list)


def _spy(monkeypatch, rows=None):
    calls = []

    def fake(url, adql, session):
        calls.append(adql)
        return rows if rows is not None else []
    monkeypatch.setattr(gaia, "tap_rows", fake)
    return calls


def test_malformed_id_skips_queries(monkeypatch):
    calls = _spy(monkeypatch)
    ident = ref_identity("HD_209458").model_copy(update={"gaia_dr3_id": "1' or '1'='1"})
    ctx = make_ctx(identity=ident)
    assert GaiaNss().fetch(ctx) == []
    assert GaiaVari().fetch(ctx) == {"summary": None}
    assert calls == []


def test_vari_fetch_queries_per_true_flag(monkeypatch):
    calls = []

    def fake(url, adql, session):
        calls.append(adql)
        if "vari_summary" in adql:
            return [{"source_id": 5, "in_vari_rrlyrae": True, "in_vari_cepheid": False,
                     "in_vari_classification_result": True, "num_selected_g_fov": 12}]
        return []
    monkeypatch.setattr(gaia, "tap_rows", fake)
    raw = GaiaVari().fetch(make_ctx())
    assert raw["summary"] == {"source_id": "5", "in_vari_rrlyrae": True, "in_vari_cepheid": False,
                              "in_vari_classification_result": True}
    assert list(raw["periods"]) == ["rrlyrae"]
    assert any("vari_classifier_result" in c for c in calls)
    assert any("vari_rrlyrae" in c for c in calls)
    assert not any("vari_cepheid" in c for c in calls)


_FLAGS = ["rrlyrae", "cepheid", "eclipsing_binary", "rotation_modulation"]


def _vari_fake(delay=0.0, fail_on=None):
    import time

    def fake(url, adql, session):
        if "vari_summary" in adql:
            return [{"source_id": 5, **{f"in_vari_{f}": True for f in _FLAGS}}]
        if "vari_classifier_result" in adql:
            return [{"classifier_name": "c", "best_class_name": "X", "best_class_score": 1.0}]
        time.sleep(delay)
        if fail_on and fail_on in adql:
            raise RuntimeError("boom")
        return [{"pf": 0.5, "frequency": 0.25, "best_rotation_period": 7.0}]
    return fake


def test_vari_period_queries_run_concurrently(monkeypatch):
    import time
    monkeypatch.setattr(gaia, "tap_rows", _vari_fake(delay=0.3))
    t0 = time.monotonic()
    raw = GaiaVari().fetch(make_ctx())
    assert time.monotonic() - t0 < 1.0  # sequential would take >= 1.2 s
    assert sorted(raw["periods"]) == sorted(_FLAGS)


def test_vari_failing_period_query_raises(monkeypatch):
    monkeypatch.setattr(gaia, "tap_rows", _vari_fake(fail_on="vari_cepheid"))
    with pytest.raises(RuntimeError, match="boom"):
        GaiaVari().fetch(make_ctx())


def test_vari_concurrent_output_matches_sequential(monkeypatch):
    fake = _vari_fake()
    monkeypatch.setattr(gaia, "tap_rows", fake)
    raw = GaiaVari().fetch(make_ctx())
    expected = {
        "summary": {"source_id": "5", **{f"in_vari_{f}": True for f in _FLAGS}},
        "classifier": fake(None, "vari_classifier_result", None),
        "periods": {f: fake(None, f"vari_{f}", None) for f in _FLAGS},
    }
    assert raw == expected
