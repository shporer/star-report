import pytest
from astropy.table import Table

from conftest import load_fixture, make_ctx, ref_identity

from star_report.coords import propagate, separation_arcsec
from star_report.sources import vizier
from star_report.sources.base import run_source
from star_report.sources.registry import build_sources
from star_report.sources.vizier import SB9, VSX, WDS, WIDE_BINARIES, VizierSource, VizierSpec

GAIA_ID = "4472832130942575872"


class FakeVizier:
    """Records every call; query_region/query_constraints return the queued tables."""

    def __init__(self, region=None, constraints=None):
        self.region, self.constraints = region or [], constraints or []
        self.calls = []
        self.column_requests = []

    def query_region(self, coordinates, radius=None, catalog=None):
        self.calls.append(("region", {"catalog": catalog, "radius": radius}))
        return self.region

    def query_constraints(self, catalog=None, **constraints):
        self.calls.append(("constraints", {"catalog": catalog, **constraints}))
        return self.constraints.pop(0) if self.constraints else []


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        client = FakeVizier(**kw)

        def make(cfg, columns, row_limit=200):
            client.column_requests.append(list(columns))
            return client
        monkeypatch.setattr(vizier, "vizier_client", make)
        return client
    return install


def _table(name, **cols):
    t = Table(cols)
    t.meta["name"] = name
    return t


def _gaia_ctx(gaia_id=GAIA_ID):
    return make_ctx(identity=ref_identity("HD_209458").model_copy(update={"gaia_dr3_id": gaia_id}))


def test_registered_in_order():
    names = [s.name for s in build_sources(make_ctx().config)]
    idx = [names.index(n) for n in ("wds", "sb9", "vsx", "wide_binaries")]
    assert idx == list(range(idx[0], idx[0] + 4))


def test_vsx_hd209458(ctx):
    rows = VSX.parse(load_fixture("vsx", "HD_209458"), ctx)
    assert rows[0]["name"] == "V0376 Peg"  # live VSX zero-pads the number
    assert rows[0]["type"] == "EP"
    assert rows[0]["period_d"] == pytest.approx(3.5247, abs=1e-3)
    assert rows[0]["dist_arcsec"] < 1


def test_wds_kepler10():
    ctx = make_ctx(identity=ref_identity("Kepler-10"))
    rows = WDS.parse(load_fixture("wds", "Kepler-10"), ctx)
    assert rows[0]["wds"] == "19027+5014"
    assert 1.5 < rows[0]["sep_arcsec"] < 2.5
    assert rows[0]["dist_arcsec"] < 5


def test_empty_fixtures_parse_empty(ctx):
    assert WDS.parse(load_fixture("wds", "HD_209458"), ctx) == []
    assert SB9.parse(load_fixture("sb9", "HD_209458"), ctx) == []
    assert WIDE_BINARIES.parse(load_fixture("wide_binaries", "HD_209458"), ctx) == []


def test_wds_separation_computed(ctx):
    ra, dec = propagate(ctx.identity.position, 2000.0)
    ra2, dec2 = ra + 3.0 / 3600 / 0.9, dec + 4.0 / 3600
    near = {"WDS": "A", "Disc": "X", "Comp": "AB", "Obs1": 1900, "Obs2": 2015, "pa1": 10, "pa2": 20,
            "sep1": 1.0, "sep2": 2.0, "mag1": 8.0, "mag2": 9.0, "_RAJ2000": ra2, "_DEJ2000": dec2}
    far = {**near, "WDS": "B", "_RAJ2000": ra + 8 / 3600, "_DEJ2000": dec}
    rows = WDS.parse({"B/wds/wds": [far, near]}, ctx)
    assert [r["wds"] for r in rows] == ["A", "B"]
    assert rows[0]["dist_arcsec"] == pytest.approx(separation_arcsec(ra, dec, ra2, dec2), abs=0.01)
    assert (rows[0]["pa"], rows[0]["sep_arcsec"], rows[0]["last_obs"]) == (20, 2.0, 2015)


def test_wds_falls_back_to_first_epoch(ctx):
    row = {"WDS": "A", "Obs1": 1900, "pa1": 10, "sep1": 1.5, "sep2": None, "pa2": None, "Obs2": None}
    out = WDS.parse({"B/wds/wds": [row]}, ctx)[0]
    assert (out["last_obs"], out["pa"], out["sep_arcsec"]) == (1900, 10, 1.5)


def test_wide_binary_target_as_source2(ctx):
    ctx = _gaia_ctx()
    row = {"Source1": 111, "Source2": int(GAIA_ID), "theta": 12.5 / 3600, "sepAU": 800.0, "R": 1e-5,
           "BinType": "MSMS"}
    assert WIDE_BINARIES.parse({"Source1": [], "Source2": [row]}, ctx) == [
        {"companion": "111", "sep_arcsec": pytest.approx(12.5), "sep_au": 800.0, "R_chance": 1e-5, "type": "MSMS"}]


def test_wide_binary_target_as_source1():
    row = {"Source1": int(GAIA_ID), "Source2": 222, "theta": 3.0 / 3600, "sepAU": 1.0, "R": 0.0,
           "BinType": "MSWD"}
    out = WIDE_BINARIES.parse({"Source1": [row], "Source2": []}, _gaia_ctx())
    assert out[0]["companion"] == "222"


def test_wide_binary_needs_gaia_id():
    assert WIDE_BINARIES.skip_reason(_gaia_ctx(None)) == "no Gaia DR3 ID"


def test_constraint_value_is_bare(fake):
    client = fake()
    assert WIDE_BINARIES.fetch(_gaia_ctx()) == {"Source1": [], "Source2": []}
    assert [c for _, c in client.calls] == [
        {"catalog": "J/MNRAS/506/2269/catalog", "Source1": GAIA_ID},
        {"catalog": "J/MNRAS/506/2269/catalog", "Source2": GAIA_ID}]


@pytest.mark.parametrize("bad", ["1; DROP", "12 OR 1=1", "-5", "1e5", "", "４２"])
def test_malformed_gaia_id_skips_query(fake, bad):
    client = fake()
    assert WIDE_BINARIES.fetch(_gaia_ctx(bad)) == {}
    assert client.calls == []


def test_sb9_two_step(fake):
    main = {"B/sb9/main": _table("B/sb9/main", Seq=[42], Name=["HD 1"],
                                 _RAJ2000=[330.795], _DEJ2000=[18.884])}
    orbits = {"B/sb9/orbits": _table("B/sb9/orbits", Seq=[42], Per=[3.52], e=[0.01], K1=[80.0],
                                     K2=[np_nan()], Grade=[5])}
    client = fake(region=main, constraints=[orbits])
    ctx = make_ctx()
    raw = SB9.fetch(ctx)
    assert client.calls[0][1]["catalog"] == "B/sb9/main"
    assert client.calls[1] == ("constraints", {"catalog": "B/sb9/orbits", "Seq": "42"})
    out = SB9.parse(raw, ctx)
    assert out == [{"name": "HD 1", "period_d": 3.52, "ecc": 0.01, "k1_kms": 80.0, "k2_kms": None,
                    "grade": 5.0}]


def test_constraint_queries_use_default_columns(fake):
    client = fake()
    WIDE_BINARIES.fetch(_gaia_ctx())
    assert client.column_requests == [[]]


def test_sb9_no_main_match_skips_followup(fake):
    client = fake(region=[])
    assert SB9.fetch(make_ctx()) == {}
    assert [kind for kind, _ in client.calls] == ["region"]


def np_nan():
    return float("nan")


def test_empty_tablelist_is_empty(fake, ctx):
    fake(region=[])
    result = run_source(VSX, ctx)
    assert result.status == "empty"


@pytest.mark.parametrize("raw", [None, [], "x", 5, {"a": 1}, {"B/vsx/vsx": 5},
                                 {"B/vsx/vsx": [None, 3, "x", {"Name": 7, "Period": "3"}]}])
@pytest.mark.parametrize("src", [WDS, SB9, VSX, WIDE_BINARIES])
def test_malformed_payloads_do_not_raise(src, raw, ctx):
    out = src.parse(raw, ctx)
    assert isinstance(out, list)


# ---- gaia_id mode with cone fallback (the shape Task 20's apogee uses) ----

def _apogee_like(radius=3.0):
    src = VizierSource(VizierSpec("apogee", "apogee", ("GaiaEDR3", "Teff"),
                                  mode="gaia_id", gaia_columns=("GaiaEDR3",)), lambda raw, ctx: raw)
    ctx = _gaia_ctx()
    if radius is None:
        ctx.config.sources.radius_arcsec.pop("apogee", None)
    else:
        ctx.config.sources.radius_arcsec["apogee"] = radius
    return src, ctx


def test_gaia_id_mode_uses_constraint_when_id_valid(fake):
    client = fake()
    src, ctx = _apogee_like()
    src.fetch(ctx)
    assert client.calls == [("constraints", {"catalog": "III/286/catalog", "GaiaEDR3": GAIA_ID})]
    assert src.query(ctx) == {"catalog": "III/286/catalog", "gaia_dr3_id": GAIA_ID}


@pytest.mark.parametrize("bad", [None, "", "12; DROP", "-3"])
def test_gaia_id_mode_falls_back_to_cone(fake, bad):
    client = fake()
    src, ctx = _apogee_like()
    ctx.identity = ctx.identity.model_copy(update={"gaia_dr3_id": bad})
    src.fetch(ctx)
    assert [k for k, _ in client.calls] == ["region"]
    assert client.calls[0][1]["catalog"] == "III/286/catalog"
    ra, dec = propagate(ctx.identity.position, 2000.0)
    q = src.query(ctx)
    assert q == {"catalog": "III/286/catalog", "ra": ra, "dec": dec, "radius_arcsec": 3.0}


def test_gaia_id_mode_without_radius_skips_unusable_id(fake):
    client = fake()
    src, ctx = _apogee_like(radius=None)
    ctx.identity = ctx.identity.model_copy(update={"gaia_dr3_id": None})
    assert src.fetch(ctx) == {}
    assert client.calls == []
    assert src.query(ctx) == {"catalog": "III/286/catalog", "gaia_dr3_id": None}


# ---- real-data parser fixtures (one-off recordings, not in REPORT_SOURCES) ----

def test_wide_binary_61_cyg_theta_in_degrees():
    ctx = _gaia_ctx("1872046609345556480")
    (row,) = WIDE_BINARIES.parse(load_fixture("wide_binaries", "61_Cyg"), ctx)
    assert row["companion"] == "1872046574983497216"
    assert row["sep_arcsec"] == pytest.approx(31.6, abs=1.0)
    assert row["sep_au"] / row["sep_arcsec"] == pytest.approx(3.5, rel=0.2)  # ~3.5 pc
    assert row["type"] == "MSMS"


def test_sb9_algol():
    from star_report.models import Position
    ident = ref_identity("HD_209458").model_copy(update={
        "position": Position(ra=47.0422083, dec=40.9556389, epoch=2000.0, source="simbad")})
    rows = SB9.parse(load_fixture("sb9", "Algol"), make_ctx(identity=ident))
    with_orbit = [r for r in rows if r["period_d"] is not None]
    assert [r["name"] for r in with_orbit] == ["HIP 14576"]
    assert with_orbit[0]["period_d"] == pytest.approx(2.867, abs=0.001)
    assert with_orbit[0]["k2_kms"] == pytest.approx(201.0)
