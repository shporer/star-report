import re

from conftest import load_fixture

from star_report.sources import gaia
from star_report.sources.gaia import GaiaCore, GaiaNeighbors


def test_core_hd209458(ctx):
    d = GaiaCore().parse(load_fixture("gaia_core", "HD_209458"), ctx)
    assert d["source_id"] == "1779546757669063552" and d["phot_variable_flag"] == "VARIABLE"
    assert 20.6 < d["parallax"] < 20.9 and 0.9 < d["ruwe"] < 1.1
    assert 5900 < d["ap"]["teff_gspphot"] < 6100 and 5900 < d["ap"]["teff_gspspec"] < 6150
    assert 47 < d["distance"]["r_med_photogeo"] < 49.5


def test_core_no_rows(ctx):
    assert GaiaCore().parse([], ctx) is None


def test_neighbors_include_target(ctx):
    rows = GaiaNeighbors().parse(load_fixture("gaia_neighbors", "HD_209458"), ctx)
    assert len(rows) > 1
    assert all(isinstance(r["source_id"], str) for r in rows)
    assert all("delta_g" in r for r in rows)
    target = next(r for r in rows if r["source_id"] == ctx.identity.gaia_dr3_id)
    assert target["delta_g"] == 0 and target["dist_arcsec"] < 1


def test_neighbors_no_rows(ctx):
    assert GaiaNeighbors().parse([], ctx) is None


def test_cone_adql():
    adql = gaia.cone_adql(330.795, 18.884, 5)
    assert "CONTAINS(POINT('ICRS', ra, dec)" in adql
    radius = float(re.search(r"CIRCLE\('ICRS',\s*[^,]+,\s*[^,]+,\s*([0-9.eE+-]+)\)", adql).group(1))
    assert abs(radius - 5 / 3600) < 1e-9


def test_toi700_dr2_differs(ctx):
    d = GaiaCore().parse(load_fixture("gaia_core", "TOI-700"), ctx)
    assert d["source_id"] == "5284517766615850752"
