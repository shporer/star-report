import json

import numpy as np
import pytest
from astroquery import mast
from astropy.table import Table

from conftest import load_fixture

from star_report.sources import tic
from star_report.sources.tic import Tic


def test_parse_hd209458(ctx):
    r = Tic().parse(load_fixture("tic", "HD_209458"), ctx)
    assert r["ID"] == 420814525 and r["GAIA"] == "1779546757669063552" and r["version"] == "20190415"
    assert 6000 < r["Teff"] < 6100 and 7.0 < r["Tmag"] < 7.3 and r["contratio"] < 0.01


def test_normalize_missing_values():
    r = tic.normalize({"ID": "5", "GAIA": "--", "HIP": "", "Teff": None})
    assert r["ID"] == 5 and r["GAIA"] is None and r["HIP"] is None and r["Teff"] is None


def test_normalize_keeps_only_columns_and_dst():
    r = tic.normalize({"ID": 1, "junk": 3, "dstArcSec": 2.5, "KIC": 11904151, "version": 20190415})
    assert r == {"ID": 1, "dstArcSec": 2.5, "KIC": "11904151", "version": "20190415"}


def test_toi700_points_to_dr2(ctx):
    r = Tic().parse(load_fixture("tic", "TOI-700"), ctx)
    assert r["GAIA"] == "5284517766615492736"


def test_parse_empty(ctx):
    assert Tic().parse([], ctx) is None


def test_fixture_is_json_safe_and_slim():
    rows = load_fixture("tic", "HD_209458")
    json.dumps(rows, allow_nan=False)
    assert set(rows[0]) <= set(tic.COLUMNS) | {"dstArcSec"}


def test_rows_from_masked_table():
    t = Table({"ID": [1, 2], "Teff": np.ma.masked_array([5000.0, 1.0], mask=[False, True]),
               "dstArcSec": [3.0, 1.0], "x": [1, 2]})
    rows = tic._rows(t)
    assert [r["ID"] for r in rows] == [2, 1] and rows[0]["Teff"] is None


class FakeCatalogs:
    """Stands in for mast.CatalogsClass; records calls and returns a canned table."""

    table = Table({"ID": [1], "GAIA": ["77"], "x": [0]})
    instances: list = []

    def __init__(self):
        self.calls = []
        FakeCatalogs.instances.append(self)

    def query_criteria(self, **kw):
        self.calls.append(("criteria", kw))
        return FakeCatalogs.table

    def query_region(self, coord, **kw):
        self.calls.append(("region", coord, kw))
        return FakeCatalogs.table


@pytest.fixture
def fake(monkeypatch):
    FakeCatalogs.instances = []
    FakeCatalogs.table = Table({"ID": [1], "GAIA": ["77"], "x": [0]})
    monkeypatch.setattr(tic.mast, "CatalogsClass", FakeCatalogs)
    return FakeCatalogs


def test_by_id_wires_timeout_without_touching_global(fake):
    before = mast.conf.timeout
    r = tic.by_id(1, 12.5)
    assert r == {"ID": 1, "GAIA": "77"}
    inst = fake.instances[0]
    assert inst.TIMEOUT == 12.5 and mast.conf.timeout == before
    assert inst.calls == [("criteria", {"catalog": "TIC", "ID": 1})]


def test_by_gaia_dr2_passes_str_and_returns_list(fake):
    assert tic.by_gaia_dr2("77", 5) == [{"ID": 1, "GAIA": "77"}]
    assert fake.instances[0].calls == [("criteria", {"catalog": "TIC", "GAIA": "77"})]


def test_empty_tables(fake):
    fake.table = Table({"ID": [], "GAIA": []})
    assert tic.by_id(1, 5) is None
    assert tic.by_gaia_dr2("77", 5) == []
    assert tic.cone(1.0, 2.0, 10, 5) == []


def test_cone_sorted_by_distance(fake):
    fake.table = Table({"ID": [1, 2, 3], "dstArcSec": [5.0, 1.0, 3.0]})
    rows = tic.cone(10.0, 20.0, 30, 7)
    assert [r["ID"] for r in rows] == [2, 3, 1]
    kind, coord, kw = fake.instances[0].calls[0]
    assert kind == "region" and kw["catalog"] == "TIC" and kw["radius"].to("arcsec").value == 30
    assert fake.instances[0].TIMEOUT == 7


def test_fetch_uses_config_timeout(fake, ctx):
    rows = Tic().fetch(ctx)
    assert rows == [{"ID": 1, "GAIA": "77"}]
    inst = fake.instances[0]
    assert inst.TIMEOUT == ctx.config.sources.timeout_s
    assert inst.calls == [("criteria", {"catalog": "TIC", "ID": ctx.identity.tic_id})]
