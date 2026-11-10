"""Identity resolution: recorded replays of the reference stars, and a fake sky for each rule."""

from __future__ import annotations

import pytest
from conftest import (
    DEC,
    FIXTURES_DIR,
    RA,
    FakeLookup,
    all_ref_stars,
    gaia_row,
    ref_star,
    simbad_obj,
    tic_row,
)

from star_report.cache import Cache
from star_report.config import load_config
from star_report.coords import propagate, separation_arcsec
from star_report.models import Position
from star_report.resolve import (
    LiveLookup,
    RecordingLookup,
    ReplayLookup,
    ResolutionError,
    parse_target,
    resolve,
)
from star_report.sources import exoplanet_archive, gaia, simbad, tic


@pytest.fixture(scope="module")
def cfg(tmp_path_factory):
    return load_config(cwd=tmp_path_factory.mktemp("cfg"))


@pytest.fixture(scope="module")
def rcfg(cfg):
    return cfg.resolve


# ---- recorded replays of the reference stars ----

REPLAYS = [(s["slug"], spelling) for s in all_ref_stars() for spelling in s["spellings"]]


@pytest.mark.parametrize("slug,spelling", REPLAYS, ids=[f"{a}:{b}" for a, b in REPLAYS])
def test_replay_resolves_reference_star(slug, spelling, rcfg):
    star = ref_star(slug)
    lookup = ReplayLookup(FIXTURES_DIR / "lookups" / f"{slug}.json")
    ident = resolve(parse_target(spelling), lookup, rcfg)
    assert ident.tic_id == star["tic"]
    assert ident.gaia_dr3_id == star["gaia_dr3"]
    assert ident.gaia_dr2_id == star["gaia_dr2"]
    assert ident.simbad_id == star["simbad_main_id"]


# ---- a fake sky ----


def foo_sky() -> FakeLookup:
    """One star, Foo: Gaia DR3 11 (DR2 10), TIC 1, KIC 123; host of TOI-700 and KOI-72."""
    return FakeLookup(
        simbad_objects=[simbad_obj("Foo", ids=["TIC 1", "Gaia DR3 11", "Gaia DR2 10", "KIC 123"])],
        gaia_rows={"11": gaia_row("11")},
        dr2_to_dr3={"10": "11"},
        tic_rows={1: tic_row(1, GAIA="10")},
        tois={"700": 1},
        kois={"72": 123},
    )


# ---- decisions ----


def test_tic_input_cross_matches_without_warnings(rcfg):
    ident = resolve(parse_target("TIC 1"), foo_sky(), rcfg)
    assert ident.resolved_via == "tic" and ident.tic_id == 1
    assert ident.gaia_dr3_id == "11" and ident.gaia_dr2_id == "10"
    assert ident.position.source == "gaia_dr3" and ident.position.epoch == 2016.0
    assert ident.main_name == "Foo" and ident.simbad_id == "Foo"
    assert ident.input == "TIC 1"
    assert ident.warnings == []


def test_bright_star_without_gaia_keeps_simbad_position(rcfg):
    sky = FakeLookup(simbad_objects=[simbad_obj("Bright", ra=200.0, dec=-10.0)],
                     tic_rows={5: tic_row(5, ra=200.0, dec=-10.0 + 1 / 3600)})
    ident = resolve(parse_target("Bright"), sky, rcfg)
    assert ident.gaia_dr3_id is None and ident.tic_id == 5
    assert ident.position.source == "simbad" and ident.position.epoch == 2000.0
    assert (ident.position.ra, ident.position.dec) == (200.0, -10.0)
    assert ident.resolved_via == "simbad"


@pytest.mark.parametrize("text,via", [("toi 700", "toi"), ("KOI-72", "koi"),
                                      ("Gaia DR2 10", "gaia_dr2"), ("150.0 20.0", "coords")])
def test_resolved_via(text, via, rcfg):
    ident = resolve(parse_target(text), foo_sky(), rcfg)
    assert ident.resolved_via == via
    assert (ident.tic_id, ident.gaia_dr3_id, ident.simbad_id) == (1, "11", "Foo")


def test_coords_match_j2000_position_of_high_pm_star(rcfg):
    at_2000 = Position(ra=RA, dec=DEC + 0.3 / 3600, epoch=2000.0, pmra=0.0, pmdec=10_000.0,
                       source="gaia_dr3")
    ra16, dec16 = propagate(at_2000, 2016.0)
    assert 159 < separation_arcsec(RA, DEC, ra16, dec16) < 161
    sky = FakeLookup(gaia_rows={
        "21": gaia_row("21", ra16, dec16, pmdec=10_000.0, parallax=None),
        "22": gaia_row("22", RA, DEC + 4 / 3600),  # nearer at 2016.0, but 4" away at both epochs
    })
    ident = resolve(parse_target(f"{RA} {DEC}"), sky, rcfg)
    assert ident.gaia_dr3_id == "21" and ident.resolved_via == "coords"
    assert ident.main_name == "Gaia DR3 21" and ident.simbad_id is None and ident.tic_id is None


def test_coords_without_candidate_raise(rcfg):
    sky = FakeLookup(gaia_rows={"22": gaia_row("22", RA, DEC + 10 / 3600)})
    with pytest.raises(ResolutionError) as err:
        resolve(parse_target(f"{RA} {DEC}"), sky, rcfg)
    assert "Gaia DR3 cone: no source within 5″" in err.value.tried


def test_nothing_found(rcfg):
    with pytest.raises(ResolutionError) as err:
        resolve(parse_target("Foo"), FakeLookup(), rcfg)
    assert str(err.value) == (
        "Could not resolve 'Foo'. Tried: SIMBAD name lookup: no match. If this is a catalog ID, "
        "add its prefix (for example 'TIC 420814525', 'Gaia DR3 1779546757669063552', "
        "'HD 209458').")
    assert err.value.target == "Foo" and err.value.tried == ["SIMBAD name lookup: no match"]


def test_owner_lookup_failure_raises(rcfg):
    sky = foo_sky()
    sky.errors["tic_by_id"] = ConnectionError("refused")
    with pytest.raises(ResolutionError) as err:
        resolve(parse_target("TIC 1"), sky, rcfg)
    assert "TIC v8.2 (MAST): unavailable" in str(err.value)
    assert isinstance(err.value.__cause__, ConnectionError)


def test_simbad_record_without_position_raises(rcfg):
    sky = FakeLookup(simbad_objects=[simbad_obj("Nowhere", ra=None, dec=None)])
    with pytest.raises(ResolutionError) as err:
        resolve(parse_target("Nowhere"), sky, rcfg)
    assert err.value.tried == ["SIMBAD name lookup: no position"]


def test_tic_by_gaia_dr2_prefers_unflagged_entry(rcfg):
    sky = foo_sky()
    sky.simbad_objects = [simbad_obj("Foo", ids=["Gaia DR3 11", "Gaia DR2 10"])]
    sky.tic_rows = {1: tic_row(1, GAIA="10", disposition="DUPLICATE", duplicate_id=2),
                    2: tic_row(2, GAIA="10")}
    ident = resolve(parse_target("Foo"), sky, rcfg)
    assert ident.tic_id == 2 and ident.warnings == []


def test_other_ids_from_simbad(rcfg):
    sky = foo_sky()
    sky.simbad_objects[0]["ids"] += [
        "HD 5", "HDE 6", "HIC 7", "HIP 7", "HR 8", "GJ 9", "2MASS J01234567+0123456", "EPIC 10",
        "TYC  12-34-1", "TOI-700", "TOI-701", "KOI-72", "Kepler-7", "K2-3", "WASP-4", "HAT-P-5",
        "KELT-6", "BD+04  3561a"]
    ident = resolve(parse_target("Foo"), sky, rcfg)
    assert ident.other_ids == {
        "KIC": "KIC 123", "HD": "HD 5", "HIP": "HIP 7", "HR": "HR 8", "GJ": "GJ 9",
        "2MASS": "2MASS J01234567+0123456", "EPIC": "EPIC 10", "TYC": "TYC 12-34-1",
        "TOI": "TOI-700", "KOI": "KOI-72", "Kepler": "Kepler-7", "K2": "K2-3", "WASP": "WASP-4",
        "HAT-P": "HAT-P-5", "KELT": "KELT-6"}


@pytest.mark.parametrize("text", ["TIC 1", "TOI-700"])
def test_tic_input_retries_simbad_by_gaia_id(text, rcfg):
    sky = foo_sky()
    sky.simbad_objects = [simbad_obj("Foo", ids=["Gaia DR3 11", "HD 5", "HIP 7"])]  # no "TIC 1"
    recorder = RecordingLookup(sky)
    ident = resolve(parse_target(text), recorder, rcfg)
    assert (ident.simbad_id, ident.main_name) == ("Foo", "Foo")
    assert ident.other_ids == {"HD": "HD 5", "HIP": "HIP 7"}
    assert (ident.tic_id, ident.gaia_dr3_id, ident.gaia_dr2_id) == (1, "11", "10")
    assert [k for k in recorder.calls if k.startswith("simbad:")] == [
        'simbad:{"name": "TIC 1"}', 'simbad:{"name": "Gaia DR3 11"}']
    assert sky.calls.count("simbad") == 2 and ident.warnings == []


@pytest.mark.parametrize("text", ["Gaia DR3 11", "150.0 20.0", "TIC 1"])
def test_simbad_is_asked_once_per_name(text, rcfg):
    sky = foo_sky()
    sky.simbad_objects = []
    ident = resolve(parse_target(text), sky, rcfg)
    asked = {"Gaia DR3 11": 1, "150.0 20.0": 1, "TIC 1": 2}[text]  # TIC: "TIC 1", then Gaia
    assert sky.calls.count("simbad") == asked and ident.simbad_id is None


def test_simbad_retry_respects_the_down_memo(rcfg):
    sky = foo_sky()
    sky.errors["simbad"] = TimeoutError("t")
    ident = resolve(parse_target("TIC 1"), sky, rcfg)
    assert sky.calls.count("simbad") == 1
    assert ident.warnings == ["SIMBAD unavailable (TimeoutError: t); identifiers may be incomplete."]


def test_without_simbad_names_and_ids_come_from_tic(rcfg):
    sky = foo_sky()
    sky.simbad_objects = []
    sky.tic_rows[1].update(HIP="7", TWOMASS="01234567+0123456", KIC="123")
    ident = resolve(parse_target("TIC 1"), sky, rcfg)
    assert ident.main_name == "TIC 1" and ident.simbad_id is None
    assert ident.other_ids == {"HIP": "HIP 7", "2MASS": "2MASS J01234567+0123456", "KIC": "KIC 123"}
    assert ident.gaia_dr3_id == "11" and ident.gaia_dr2_id == "10"


# ---- positional fallbacks must agree in brightness ----


@pytest.mark.parametrize("g,dr3,source,warnings", [
    (12.0, None, "simbad", ["Nearest Gaia DR3 source within 5″ has G = 12.0, inconsistent with "
                            "V = 0.5; no Gaia match used."]),
    (1.0, "31", "gaia_dr3", []),
], ids=["G=12-rejected", "G=1-accepted"])
def test_gaia_fallback_must_agree_in_brightness(g, dr3, source, warnings, rcfg):
    sky = FakeLookup(simbad_objects=[simbad_obj("Bright", V=0.5)],
                     gaia_rows={"31": gaia_row("31", RA, DEC + 3 / 3600, g=g)})
    ident = resolve(parse_target("Bright"), sky, rcfg)
    assert ident.gaia_dr3_id == dr3 and ident.position.source == source
    assert ident.warnings == warnings


@pytest.mark.parametrize("tic_mags,warning", [
    ({"GAIAmag": 1.0, "Tmag": 0.6}, "Nearest Gaia DR3 source within 5″ has G = 12.0, "
                                    "inconsistent with TIC G = 1.0; no Gaia match used."),
    ({"Tmag": 0.6}, "Nearest Gaia DR3 source within 5″ has G = 12.0, inconsistent with "
                    "Tmag = 0.6; no Gaia match used."),
], ids=["TIC-G", "Tmag"])
def test_gaia_fallback_compares_tic_magnitudes_before_v(tic_mags, warning, rcfg):
    sky = FakeLookup(simbad_objects=[simbad_obj("Bright", ids=["TIC 1"], V=11.5)],  # V agrees
                     tic_rows={1: tic_row(1, **tic_mags)},
                     gaia_rows={"31": gaia_row("31", RA, DEC + 3 / 3600, g=12.0)})
    ident = resolve(parse_target("TIC 1"), sky, rcfg)
    assert ident.gaia_dr3_id is None and ident.position.source == "simbad"
    assert ident.warnings == [warning]


def test_rejected_gaia_match_leaves_tic_position(rcfg):
    sky = FakeLookup(tic_rows={1: tic_row(1, Tmag=0.6)},
                     gaia_rows={"31": gaia_row("31", RA, DEC + 3 / 3600, g=12.0)})
    ident = resolve(parse_target("TIC 1"), sky, rcfg)
    assert ident.gaia_dr3_id is None and ident.position.source == "tic"
    assert ident.warnings == ["Nearest Gaia DR3 source within 5″ has G = 12.0, inconsistent with "
                              "Tmag = 0.6; no Gaia match used."]


@pytest.mark.parametrize("simbad_ids,gaia_rows,source,warning", [
    (["Gaia DR3 31"], {"31": gaia_row("31", g=1.0)}, "gaia_dr3",
     "Nearest TIC entry within 5″ has Tmag = 13.0, inconsistent with Gaia G = 1.0; "
     "no TIC match used."),
    ([], {}, "simbad",
     "Nearest TIC entry within 5″ has Tmag = 13.0, inconsistent with V = 0.5; "
     "no TIC match used."),
], ids=["vs-Gaia-G", "vs-V"])
def test_tic_fallback_must_agree_in_brightness(simbad_ids, gaia_rows, source, warning, rcfg):
    sky = FakeLookup(simbad_objects=[simbad_obj("Bright", ids=simbad_ids, V=0.5)],
                     gaia_rows=gaia_rows,
                     tic_rows={41: tic_row(41, RA, DEC + 2 / 3600, Tmag=13.0)})
    ident = resolve(parse_target("Bright"), sky, rcfg)
    assert ident.tic_id is None and ident.position.source == source
    assert ident.warnings == [warning]


@pytest.mark.parametrize("v,g", [(None, 12.0), (0.5, None)])
def test_gaia_fallback_without_comparable_magnitudes_takes_nearest(v, g, rcfg):
    sky = FakeLookup(simbad_objects=[simbad_obj("Bright", V=v)],
                     gaia_rows={"31": gaia_row("31", RA, DEC + 3 / 3600, g=g)})
    ident = resolve(parse_target("Bright"), sky, rcfg)
    assert ident.gaia_dr3_id == "31" and ident.warnings == []


@pytest.mark.parametrize("v,tmag", [(None, 13.0), (0.5, None)])
def test_tic_fallback_without_comparable_magnitudes_takes_nearest(v, tmag, rcfg):
    sky = FakeLookup(simbad_objects=[simbad_obj("Bright", V=v)],
                     tic_rows={41: tic_row(41, RA, DEC + 2 / 3600, Tmag=tmag)})
    ident = resolve(parse_target("Bright"), sky, rcfg)
    assert ident.tic_id == 41 and ident.warnings == []


# ---- warnings ----


def test_w1_tic_and_simbad_disagree_on_gaia(rcfg):
    sky = foo_sky()
    sky.simbad_objects = [simbad_obj("Foo", ids=["TIC 1", "Gaia DR3 99"])]
    ident = resolve(parse_target("TIC 1"), sky, rcfg)
    assert ident.warnings == [
        "TIC 1 points to Gaia DR2 10, which matches Gaia DR3 11, but SIMBAD gives Gaia DR3 99."]


def test_w2_close_pair(rcfg):
    sky = foo_sky()
    sky.gaia_rows["12"] = gaia_row("12", RA, DEC + 1 / 3600, g=11.2)
    ident = resolve(parse_target("Foo"), sky, rcfg)
    assert ident.warnings == [
        "2 Gaia DR3 sources lie within 2″ with ΔG < 3; the match may be ambiguous."]


def test_w2_ignores_faint_neighbours(rcfg):
    sky = foo_sky()
    sky.gaia_rows["12"] = gaia_row("12", RA, DEC + 1 / 3600, g=13.5)
    assert resolve(parse_target("Foo"), sky, rcfg).warnings == []


@pytest.mark.parametrize("disposition,duplicate,warning", [
    ("SPLIT", 7, "TIC 1 is flagged SPLIT (duplicate of TIC 7)."),
    ("ARTIFACT", None, "TIC 1 is flagged ARTIFACT."),
])
def test_w3_tic_flagged(disposition, duplicate, warning, rcfg):
    sky = foo_sky()
    sky.tic_rows[1].update(disposition=disposition, duplicate_id=duplicate)
    assert resolve(parse_target("TIC 1"), sky, rcfg).warnings == [warning]


def test_w4_multiple_system(rcfg):
    sky = foo_sky()
    sky.simbad_objects = [simbad_obj("alf Cen", otype="**")]
    ident = resolve(parse_target("alf Cen"), sky, rcfg)
    assert ident.gaia_dr3_id == "11" and ident.tic_id == 1
    assert ident.warnings == [
        "SIMBAD's alf Cen is a multiple-system entry; catalog data refer to Gaia DR3 11, "
        "the source nearest its position."]


def test_w4_multiple_system_without_component(rcfg):
    sky = FakeLookup(simbad_objects=[simbad_obj("alf Cen", otype="**")])
    ident = resolve(parse_target("alf Cen"), sky, rcfg)
    assert ident.warnings == [
        "SIMBAD's alf Cen is a multiple-system entry; no single component was matched in "
        "Gaia DR3 or TIC."]


def test_w5_several_simbad_objects(rcfg):
    sky = foo_sky()
    sky.simbad_objects = [simbad_obj("Foo A", ids=["Foo", "TIC 1", "Gaia DR3 11"]),
                          simbad_obj("Foo B", ids=["Foo"])]
    ident = resolve(parse_target("Foo"), sky, rcfg)
    assert ident.simbad_id == "Foo A"
    assert ident.warnings == ["'Foo' matched 2 SIMBAD objects; using Foo A."]


def test_w5_counts_objects_not_rows(rcfg):
    sky = foo_sky()
    sky.simbad_objects = [sky.simbad_objects[0], sky.simbad_objects[0]]
    assert resolve(parse_target("Foo"), sky, rcfg).warnings == []


def test_w6_simbad_unavailable(rcfg):
    sky = foo_sky()
    sky.errors["simbad"] = TimeoutError("t")
    ident = resolve(parse_target("TIC 1"), sky, rcfg)
    assert (ident.tic_id, ident.gaia_dr3_id) == (1, "11")
    assert ident.warnings == [
        "SIMBAD unavailable (TimeoutError: t); identifiers may be incomplete."]


def test_w6_service_is_not_asked_again(rcfg):
    sky = foo_sky()
    sky.errors["gaia_by_id"] = OSError("x")
    ident = resolve(parse_target("Foo"), sky, rcfg)
    assert ident.warnings == ["Gaia DR3 unavailable (OSError: x); identifiers may be incomplete."]
    assert sky.calls.count("gaia_by_id") == 1
    assert not {"gaia_cone", "gaia_dr3_from_dr2", "gaia_dr2_from_dr3"} & set(sky.calls)
    assert ident.gaia_dr3_id is None and ident.position.source == "simbad"
    assert (ident.tic_id, ident.gaia_dr2_id) == (1, "10")


def test_w7_planet(rcfg):
    sky = foo_sky()
    sky.simbad_objects = [simbad_obj("WASP-33", ids=["TIC 1", "Gaia DR3 11"])]
    ident = resolve(parse_target("WASP-33 b"), sky, rcfg)
    assert ident.input == "WASP-33 b" and ident.main_name == "WASP-33"
    assert ident.warnings == ["'WASP-33 b' names a planet; this report covers its host star."]


# ---- lookups ----


def test_record_then_replay(tmp_path, rcfg):
    recorder = RecordingLookup(foo_sky())
    first = resolve(parse_target("TIC 1"), recorder, rcfg)
    assert recorder.calls['tic_by_id:{"tic": 1}']["GAIA"] == "10"
    path = tmp_path / "lookups" / "foo.json"
    recorder.dump(path)
    assert resolve(parse_target("TIC 1"), ReplayLookup(path), rcfg) == first


def test_replay_answers_nothing_for_unrecorded_calls(tmp_path):
    path = tmp_path / "empty.json"
    RecordingLookup(FakeLookup()).dump(path)
    replay = ReplayLookup(path)
    assert [replay.gaia_by_id("1"), replay.tic_by_id(1), replay.toi_host("1"),
            replay.koi_host("1")] == [None] * 4
    assert [replay.simbad("x"), replay.gaia_dr3_from_dr2("1"), replay.gaia_dr2_from_dr3("1"),
            replay.gaia_cone(1.0, 2.0, 3.0), replay.tic_by_gaia_dr2("1"),
            replay.tic_cone(1.0, 2.0, 3.0)] == [[]] * 6


def test_recording_notes_failed_calls(rcfg):
    sky = foo_sky()
    sky.errors["simbad"] = TimeoutError("t")
    recorder = RecordingLookup(sky)
    resolve(parse_target("TIC 1"), recorder, rcfg)
    assert recorder.errors == ['simbad:{"name": "TIC 1"}: TimeoutError: t']
    assert not any(key.startswith("simbad:") for key in recorder.calls)


LIVE_CALLS = [
    # method, kwargs, module, function, cache group, expected positional arguments
    ("simbad", {"name": "HD 1"}, simbad, "lookup", "simbad", ("HD 1", "SESSION", "CFG")),
    ("gaia_by_id", {"dr3_id": "5"}, gaia, "by_id", "gaia", ("5", "SESSION")),
    ("gaia_dr3_from_dr2", {"dr2_id": "5"}, gaia, "dr3_from_dr2", "gaia", ("5", "SESSION")),
    ("gaia_dr2_from_dr3", {"dr3_id": "5"}, gaia, "dr2_from_dr3", "gaia", ("5", "SESSION")),
    ("gaia_cone", {"ra": 1.5, "dec": 2.5, "radius_arcsec": 3.0}, gaia, "cone", "gaia",
     (1.5, 2.5, 3.0, "SESSION")),
    ("tic_by_id", {"tic": 7}, tic, "by_id", "tic", (7, "TIMEOUT")),
    ("tic_by_gaia_dr2", {"dr2_id": "5"}, tic, "by_gaia_dr2", "tic", ("5", "TIMEOUT")),
    ("tic_cone", {"ra": 1.5, "dec": 2.5, "radius_arcsec": 3.0}, tic, "cone", "tic",
     (1.5, 2.5, 3.0, "TIMEOUT")),
    ("toi_host", {"toi": "700"}, exoplanet_archive, "toi_host", "exoplanet_archive",
     ("700", "SESSION", "TIMEOUT")),
    ("koi_host", {"koi": "72"}, exoplanet_archive, "koi_host", "exoplanet_archive",
     ("72", "SESSION", "TIMEOUT")),
]


@pytest.mark.parametrize("method,kwargs,module,function,group,expected", LIVE_CALLS,
                         ids=[c[0] for c in LIVE_CALLS])
def test_live_lookup_goes_through_the_cache(method, kwargs, module, function, group, expected,
                                            monkeypatch, tmp_path, cfg):
    calls = []

    def query(*args):
        calls.append(args)
        return {"answer": 1}

    monkeypatch.setattr(module, function, query)
    session = object()
    cache = Cache(tmp_path, cfg.cache.expiry_days, cfg.cache.empty_expiry_days)
    live = LiveLookup(cache, session, cfg)
    assert getattr(live, method)(**kwargs) == {"answer": 1}
    assert getattr(live, method)(**kwargs) == {"answer": 1}  # the second answer is cached
    stand_ins = {"SESSION": session, "CFG": cfg, "TIMEOUT": cfg.sources.timeout_s}
    assert calls == [tuple(stand_ins.get(a, a) if isinstance(a, str) else a for a in expected)]
    assert len(list((tmp_path / group).glob("*.json"))) == 1


# ---- NASA Exoplanet Archive host lookups ----


class FakeResponse:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self.rows


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.requests = []

    def get(self, url, params=None, timeout=None):
        self.requests.append((url, params, timeout))
        return FakeResponse(self.rows)


def test_toi_host_query():
    session = FakeSession([{"tid": 150428135}])
    assert exoplanet_archive.toi_host("700", session, 12.0) == 150428135
    assert session.requests == [(
        "https://exoplanetarchive.ipac.caltech.edu/TAP/sync",
        {"query": "select tid from toi where toipfx = '700'", "format": "json"}, 12.0)]


def test_koi_host_query():
    session = FakeSession([{"kepid": 11904151}, {"kepid": 11904151}])
    assert exoplanet_archive.koi_host("72", session, 5.0) == 11904151
    assert session.requests[0][1]["query"] == (
        "select kepid from cumulative where kepoi_name like 'K00072%'")


def test_host_lookups_without_rows():
    assert exoplanet_archive.toi_host("99999", FakeSession([]), 5.0) is None
    assert exoplanet_archive.koi_host("99999", FakeSession([]), 5.0) is None
