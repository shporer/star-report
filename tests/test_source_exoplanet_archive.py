from types import SimpleNamespace

from conftest import load_fixture, make_ctx, ref_identity

from star_report.sources import exoplanet_archive
from star_report.sources.exoplanet_archive import ExoplanetArchive, known_periods


def test_toi700():
    ctx = make_ctx(identity=ref_identity("TOI-700"))
    d = ExoplanetArchive().parse(load_fixture("exoplanet_archive", "TOI-700"), ctx)
    assert [p["name"] for p in d["planets"]] == ["TOI-700 b", "TOI-700 c", "TOI-700 d", "TOI-700 e"]
    assert {"700.01", "700.02", "700.03", "700.04"} <= {t["toi"] for t in d["tois"]}


def test_kepler10():
    ctx = make_ctx(identity=ref_identity("Kepler-10"))
    d = ExoplanetArchive().parse(load_fixture("exoplanet_archive", "Kepler-10"), ctx)
    assert {"koi": "K00072.01", "name": "Kepler-10 b"}.items() <= next(
        k for k in d["kois"] if k["koi"] == "K00072.01").items()
    assert next(k for k in d["kois"] if k["koi"] == "K00072.01")["disposition"] == "CONFIRMED"
    assert "Kepler-10 b" in {p["name"] for p in d["planets"]}


def test_hd209458_has_toi(ctx):
    d = ExoplanetArchive().parse(load_fixture("exoplanet_archive", "HD_209458"), ctx)
    assert "5972.01" in {t["toi"] for t in d["tois"]}


def test_k2_deduplicated(ctx):
    raw = {"planets": [], "tois": [], "kois": [], "k2": [
        {"pl_name": "A", "epic_candname": "EPIC 1.01", "disposition": "CANDIDATE",
         "pl_orbper": 3.0, "pl_rade": 1.0},
        {"pl_name": "B", "epic_candname": "EPIC 1.01", "disposition": "CONFIRMED",
         "pl_orbper": 4.0, "pl_rade": 2.0},
        {"pl_name": "C", "epic_candname": "EPIC 1.01", "disposition": "CONFIRMED",
         "pl_orbper": 5.0, "pl_rade": 3.0},
    ]}
    d = ExoplanetArchive().parse(raw, ctx)
    assert d["k2"] == [{"name": "A", "candidate": "EPIC 1.01", "disposition": "CANDIDATE",
                        "period_d": 3.0, "radius_re": 1.0}]


def test_parse_all_empty(ctx):
    assert ExoplanetArchive().parse({"planets": [], "tois": [], "kois": [], "k2": []}, ctx) == {}


def test_only_known_ids_queried(ctx, monkeypatch):
    calls = []
    monkeypatch.setattr(exoplanet_archive, "tap",
                        lambda adql, session, timeout: calls.append(adql) or [])
    assert ctx.identity.tic_id == 420814525
    raw = ExoplanetArchive().fetch(ctx)
    assert len(calls) == 2
    assert "from pscomppars where tic_id = 'TIC 420814525'" in calls[0]
    assert "from toi where tid = 420814525" in calls[1]
    assert raw == {"planets": [], "tois": [], "kois": [], "k2": []}


def test_all_ids_queried_and_gaia_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(exoplanet_archive, "tap",
                        lambda adql, session, timeout: calls.append(adql) or [])
    ident = ref_identity("Kepler-10").model_copy(
        update={"other_ids": {"KIC": "KIC 11904151", "EPIC": "EPIC 201367065"}})
    ExoplanetArchive().fetch(make_ctx(identity=ident))
    assert len(calls) == 4
    assert "kepid = 11904151" in calls[2] and "epic_hostname = 'EPIC 201367065'" in calls[3]
    calls.clear()
    ExoplanetArchive().fetch(make_ctx(identity=ident.model_copy(update={"tic_id": None})))
    assert "gaia_dr3_id = 'Gaia DR3 2132155017099178624'" in calls[0]
    assert len(calls) == 3  # no tois without a TIC


def test_query_lists_known_ids(ctx):
    assert ExoplanetArchive().query(ctx) == {"tic": 420814525, "gaia_dr3": "1779546757669063552",
                                             "kic": None, "epic": None}


def test_known_periods():
    data = {"planets": [{"name": "TOI-700 d", "period_d": 37.42}, {"name": "x", "period_d": None}],
            "tois": [{"toi": "700.01", "period_d": 9.98}, {"toi": "9.01", "period_d": None}]}
    results = {"exoplanet_archive": SimpleNamespace(data=data)}
    assert known_periods(results) == [("TOI-700 d", 37.42), ("TOI 700.01", 9.98)]
    assert known_periods({}) == []
    assert known_periods({"exoplanet_archive": SimpleNamespace(data=None)}) == []


def _spy(monkeypatch):
    calls = []
    monkeypatch.setattr(exoplanet_archive, "tap",
                        lambda adql, session, timeout: calls.append(adql) or [])
    return calls


def test_malformed_kic_epic_skip_queries(monkeypatch):
    calls = _spy(monkeypatch)
    ident = ref_identity("Kepler-10").model_copy(
        update={"other_ids": {"KIC": "KIC abc", "EPIC": ""}})
    raw = ExoplanetArchive().fetch(make_ctx(identity=ident))
    assert len(calls) == 2 and raw["kois"] == [] and raw["k2"] == []


def test_malformed_gaia_id_never_reaches_adql(monkeypatch):
    calls = _spy(monkeypatch)
    ident = ref_identity("Kepler-10").model_copy(
        update={"tic_id": None, "gaia_dr3_id": "1' or '1'='1", "other_ids": {}})
    raw = ExoplanetArchive().fetch(make_ctx(identity=ident))
    assert calls == [] and raw == {"planets": [], "tois": [], "kois": [], "k2": []}


def test_non_int_tic_is_skipped_or_coerced(monkeypatch):
    calls = _spy(monkeypatch)
    base = ref_identity("Kepler-10").model_copy(update={"other_ids": {}, "gaia_dr3_id": None})
    ExoplanetArchive().fetch(make_ctx(identity=base.model_copy(update={"tic_id": "1' or '1'='1"})))
    assert calls == []
    ExoplanetArchive().fetch(make_ctx(identity=base.model_copy(update={"tic_id": "0377780790"})))
    assert "tic_id = 'TIC 377780790'" in calls[0] and "tid = 377780790" in calls[1]
