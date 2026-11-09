import json

import numpy as np
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
    assert set(r) == {"ID", "dstArcSec", "KIC", "version"} or "dstArcSec" in r
    assert "junk" not in r and r["KIC"] == "11904151" and r["version"] == "20190415"


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
