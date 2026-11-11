import re

import pytest

from conftest import load_fixture, make_ctx, ref_identity

from star_report import derive
from star_report.models import SourceResult
from star_report.sources import gaia
from star_report.sources.base import run_source
from star_report.sources.gaia import GaiaNss, GaiaVari

SLUGS = ["HD_209458", "TOI-700", "Kepler-10", "Barnards_star"]


@pytest.mark.parametrize("slug", SLUGS)
def test_vari_fixtures_smoke(slug):
    ctx = make_ctx(identity=ref_identity(slug))
    assert isinstance(GaiaVari().parse(load_fixture("gaia_vari", slug), ctx), (dict, type(None)))


def test_nss_orbit_rows(ctx):
    raw = {"nss_two_body_orbit": [{
        "source_id": 1, "nss_solution_type": "Orbital", "period": 3.5, "period_error": 0.01,
        "eccentricity": 0.1, "semi_amplitude_primary": 12.5, "inclination": 80.0,
        "mass_ratio": None}]}
    assert GaiaNss().parse(raw, ctx) == [{
        "kind": "orbit", "type": "Orbital", "period_d": 3.5, "period_err": 0.01, "ecc": 0.1,
        "k1_kms": 12.5, "inclination": 80.0, "mass_ratio": None}]


def test_nss_acceleration_rows(ctx):
    raw = {"nss_acceleration_astro": [{
        "source_id": 2, "nss_solution_type": "Acceleration9",
        "accel_ra": 0.25, "accel_ra_error": 0.07, "accel_dec": -0.83, "accel_dec_error": 0.03,
        "deriv_accel_ra": -9.82, "deriv_accel_ra_error": 0.29,
        "deriv_accel_dec": -0.64, "deriv_accel_dec_error": 0.11, "significance": 34.1}]}
    assert GaiaNss().parse(raw, ctx) == [{
        "kind": "acceleration", "type": "Acceleration9",
        "accel_ra": 0.25, "accel_ra_err": 0.07, "accel_dec": -0.83, "accel_dec_err": 0.03,
        "deriv_accel_ra": -9.82, "deriv_accel_ra_err": 0.29,
        "deriv_accel_dec": -0.64, "deriv_accel_dec_err": 0.11, "significance": 34.1}]


def test_nss_trend_rows(ctx):
    raw = {"nss_non_linear_spectro": [{
        "source_id": 3, "nss_solution_type": "FirstDegreeTrendSB1",
        "mean_velocity": -50.62, "mean_velocity_error": 1.04,
        "first_deriv_velocity": 0.0142, "first_deriv_velocity_error": 0.0037,
        "second_deriv_velocity": None, "second_deriv_velocity_error": None}]}
    assert GaiaNss().parse(raw, ctx) == [{
        "kind": "trend", "type": "FirstDegreeTrendSB1",
        "mean_velocity": -50.62, "mean_velocity_err": 1.04,
        "first_deriv_velocity": 0.0142, "first_deriv_velocity_err": 0.0037,
        "second_deriv_velocity": None, "second_deriv_velocity_err": None}]


def test_nss_second_degree_trend_row(ctx):
    raw = {"nss_non_linear_spectro": [{
        "source_id": 3, "nss_solution_type": "SecondDegreeTrendSB1",
        "mean_velocity": -14.17, "mean_velocity_error": 1.69,
        "first_deriv_velocity": 0.0038, "first_deriv_velocity_error": 0.0070,
        "second_deriv_velocity": 3.2e-05, "second_deriv_velocity_error": 4.3e-05}]}
    assert GaiaNss().parse(raw, ctx) == [{
        "kind": "trend", "type": "SecondDegreeTrendSB1",
        "mean_velocity": -14.17, "mean_velocity_err": 1.69,
        "first_deriv_velocity": 0.0038, "first_deriv_velocity_err": 0.0070,
        "second_deriv_velocity": 3.2e-05, "second_deriv_velocity_err": 4.3e-05}]


def test_nss_orbit_row_with_a_mass_ratio(ctx):
    raw = {"nss_two_body_orbit": [{"nss_solution_type": "SB2", "period": 10.0, "mass_ratio": 0.8}]}
    (row,) = GaiaNss().parse(raw, ctx)
    assert row["mass_ratio"] == 0.8 and row["inclination"] is None


def test_nss_empty(ctx):
    empty = {"nss_two_body_orbit": [], "nss_acceleration_astro": [], "nss_non_linear_spectro": []}
    assert GaiaNss().parse(empty, ctx) is None


def test_nss_malformed_values(ctx):
    raw = {"nss_acceleration_astro": [{"nss_solution_type": 7, "accel_ra": "abc",
                                       "accel_dec": True, "significance": float("nan")}]}
    (row,) = GaiaNss().parse(raw, ctx)
    assert row["kind"] == "acceleration" and row["type"] is None
    assert row["accel_ra"] is None and row["accel_dec"] is None and row["significance"] is None


def test_nss_hd18284_has_an_orbit_and_an_acceleration():
    """Recorded payload: one source with both an SB1 orbit and an Acceleration7 solution."""
    ctx = make_ctx(identity=ref_identity("HD_18284"))
    orbit, accel = GaiaNss().parse(load_fixture("gaia_nss", "HD_18284"), ctx)
    assert (orbit["kind"], orbit["type"]) == ("orbit", "SB1")
    assert orbit["period_d"] == pytest.approx(3.164209, abs=1e-6)
    assert orbit["k1_kms"] == pytest.approx(3.662262, abs=1e-6)
    assert (accel["kind"], accel["type"]) == ("acceleration", "Acceleration7")
    assert accel["accel_ra"] == pytest.approx(2.657901, abs=1e-6)
    assert accel["accel_dec_err"] == pytest.approx(0.097285, abs=1e-6)
    assert accel["significance"] == pytest.approx(50.770977, abs=1e-6)
    assert accel["deriv_accel_ra"] is None and accel["deriv_accel_dec"] is None


def test_nss_hd18284_flags():
    ctx = make_ctx(identity=ref_identity("HD_18284"))
    data = GaiaNss().parse(load_fixture("gaia_nss", "HD_18284"), ctx)
    results = {"gaia_nss": SourceResult(source="gaia_nss", status="ok", data=data)}
    assert derive.flags(ctx.identity, results, ctx.config.flags) == [
        "Gaia DR3 SB1 orbit, P = 3.16 d", "Gaia DR3 Acceleration7 solution"]


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


@pytest.mark.parametrize("raw", [
    None, {}, "text", 5, [None, 3], [{"period": "abc"}], {"a": 1},
    {"nss_two_body_orbit": "x"}, {"nss_acceleration_astro": [None, 3]},
    {"nss_non_linear_spectro": {"mean_velocity": 1.0}},
])
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
    assert GaiaNss().fetch(ctx) == {}
    assert GaiaVari().fetch(ctx) == {"summary": None}
    assert calls == []


_NSS_ROWS = {"nss_two_body_orbit": [{"nss_solution_type": "SB1"}],
             "nss_acceleration_astro": [{"nss_solution_type": "Acceleration7"}],
             "nss_non_linear_spectro": []}


def _nss_fake(calls=None, fail_on=None):
    def fake(url, adql, session):
        if calls is not None:
            calls.append(adql)
        if fail_on and fail_on in adql:
            raise ConnectionError("503")
        return next(rows for table, rows in _NSS_ROWS.items() if f"FROM gaiadr3.{table} " in adql)
    return fake


def test_nss_fetch_queries_each_table(monkeypatch):
    calls = []
    monkeypatch.setattr(gaia, "tap_rows", _nss_fake(calls))
    assert GaiaNss().fetch(make_ctx(identity=ref_identity("HD_18284"))) == _NSS_ROWS
    assert len(calls) == 3
    assert all(c.endswith("WHERE source_id = 21296238855672832") for c in calls)


_SELECTED = {
    "nss_two_body_orbit": ["source_id", "nss_solution_type", "period", "period_error",
                           "eccentricity", "semi_amplitude_primary", "inclination", "mass_ratio"],
    "nss_acceleration_astro": ["source_id", "nss_solution_type", "accel_ra", "accel_ra_error",
                               "accel_dec", "accel_dec_error", "deriv_accel_ra",
                               "deriv_accel_ra_error", "deriv_accel_dec", "deriv_accel_dec_error",
                               "significance"],
    "nss_non_linear_spectro": ["source_id", "nss_solution_type", "mean_velocity",
                               "mean_velocity_error", "first_deriv_velocity",
                               "first_deriv_velocity_error", "second_deriv_velocity",
                               "second_deriv_velocity_error"],
}


def _selected(calls):
    """{table: [selected column, ...]} from the ADQL statements sent."""
    out = {}
    for adql in calls:
        m = re.fullmatch(r"SELECT (.+) FROM gaiadr3\.(\w+) WHERE source_id = (\d+)", adql)
        assert m, adql
        out[m.group(2)] = [c.strip() for c in m.group(1).split(",")]
    return out


def test_nss_fetch_selects_the_archive_columns(monkeypatch):
    calls = []
    monkeypatch.setattr(gaia, "tap_rows", _nss_fake(calls))
    GaiaNss().fetch(make_ctx(identity=ref_identity("HD_18284")))
    assert _selected(calls) == _SELECTED


def test_nss_recorded_rows_carry_exactly_the_selected_columns():
    """A stale fixture, or a column dropped from the query, fails here."""
    raw = load_fixture("gaia_nss", "HD_18284")
    assert sorted(raw) == sorted(_SELECTED)
    for table, rows in raw.items():
        assert all(sorted(row) == sorted(_SELECTED[table]) for row in rows), table
    assert raw["nss_two_body_orbit"] and raw["nss_acceleration_astro"]


@pytest.mark.parametrize("slug", SLUGS)
def test_nss_fixture_of_a_star_without_solutions(slug):
    raw = load_fixture("gaia_nss", slug)
    assert raw == {table: [] for table in _SELECTED}
    assert GaiaNss().parse(raw, make_ctx(identity=ref_identity(slug))) is None


def test_nss_slow_table_times_out_at_the_source_deadline(monkeypatch):
    import threading
    import time

    release = threading.Event()
    fake = _nss_fake()

    def slow(url, adql, session):
        if "nss_acceleration_astro" in adql:
            release.wait(5)
        return fake(url, adql, session)

    monkeypatch.setattr(gaia, "tap_rows", slow)
    ctx = make_ctx(identity=ref_identity("HD_18284"))
    ctx.config.sources.timeout_overrides["gaia_nss"] = 0.3
    t0 = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match=r"timed out after 0\.3 s"):
            GaiaNss().fetch(ctx)
        assert time.monotonic() - t0 < 1.5
    finally:
        release.set()


def test_nss_fetch_fails_when_any_table_fails(monkeypatch):
    monkeypatch.setattr(gaia, "tap_rows", _nss_fake(fail_on="nss_non_linear_spectro"))
    with pytest.raises(ConnectionError):
        GaiaNss().fetch(make_ctx(identity=ref_identity("HD_18284")))


def test_nss_reports_the_service_error_rather_than_a_timeout(monkeypatch):
    """One table is refused at once while an earlier one never answers: the refusal is reported."""
    import threading

    release = threading.Event()

    def fake(url, adql, session):
        if "nss_two_body_orbit" in adql:
            release.wait(5)
            return []
        if "nss_non_linear_spectro" in adql:
            raise ConnectionError("503 Server overloaded")
        return []

    monkeypatch.setattr(gaia, "tap_rows", fake)
    ctx = make_ctx(identity=ref_identity("HD_18284"))
    ctx.config.sources.timeout_s = 0.3
    try:
        with pytest.raises(ConnectionError, match="Server overloaded"):
            GaiaNss().fetch(ctx)
    finally:
        release.set()


def test_nss_queries_run_in_daemon_threads(monkeypatch):
    import threading

    daemon = []
    fake = _nss_fake()

    def spy(url, adql, session):
        daemon.append(threading.current_thread().daemon)
        return fake(url, adql, session)

    monkeypatch.setattr(gaia, "tap_rows", spy)
    GaiaNss().fetch(make_ctx(identity=ref_identity("HD_18284")))
    assert daemon == [True, True, True]


def test_nss_ignores_payloads_cached_before_the_new_tables(monkeypatch, tmp_path):
    """An orbit-only payload cached under the old query is not reused."""
    ctx = make_ctx(identity=ref_identity("HD_18284"), cache_dir=tmp_path / "cache")
    old_query = {"source_id": ctx.identity.gaia_dr3_id}
    ctx.cache.fetch("gaia", "gaia_nss", old_query,
                    lambda: [{"nss_solution_type": "SB1", "period": 3.16}])
    monkeypatch.setattr(gaia, "tap_rows", _nss_fake())
    result = run_source(GaiaNss(), ctx)
    assert result.provenance.from_cache is False
    assert [r["type"] for r in result.data] == ["SB1", "Acceleration7"]


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


def test_vari_queries_run_in_daemon_threads(monkeypatch):
    import threading

    daemon = []
    fake = _vari_fake()

    def spy(url, adql, session):
        if "vari_summary" not in adql:
            daemon.append(threading.current_thread().daemon)
        return fake(url, adql, session)

    monkeypatch.setattr(gaia, "tap_rows", spy)
    GaiaVari().fetch(make_ctx())
    assert daemon == [True] * (len(_FLAGS) + 1)  # classifier + one per flag


def test_vari_slow_query_times_out_at_the_source_deadline(monkeypatch):
    import threading
    import time

    release = threading.Event()

    def fake(url, adql, session):
        if "vari_summary" in adql:
            return [{"source_id": 5, "in_vari_rrlyrae": True}]
        if "vari_rrlyrae" in adql:
            release.wait(5)
        return []

    monkeypatch.setattr(gaia, "tap_rows", fake)
    ctx = make_ctx()
    ctx.config.sources.timeout_s = 0.3
    t0 = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match=r"timed out after 0\.3 s"):
            GaiaVari().fetch(ctx)
        assert time.monotonic() - t0 < 1.5
    finally:
        release.set()


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
