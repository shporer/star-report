import re

from conftest import load_fixture, make_ctx, ref_identity

from star_report.coords import propagate, separation_arcsec
from star_report.sources import gaia
from star_report.sources.gaia import GaiaCore, GaiaNeighbors


def barnard_at_j2000():
    """Barnard's star (10.4″/yr) as SIMBAD gives it during a Gaia outage, and its Gaia position."""
    ident = ref_identity("Barnards_star")
    gaia_pos = ident.position  # Gaia DR3, epoch 2016.0
    ra, dec = propagate(gaia_pos, 2000.0)
    simbad_pos = gaia_pos.model_copy(update={"ra": ra, "dec": dec, "epoch": 2000.0,
                                             "source": "simbad"})
    assert separation_arcsec(ra, dec, gaia_pos.ra, gaia_pos.dec) > 100
    return ident.model_copy(update={"position": simbad_pos}), gaia_pos


def test_neighbor_cone_is_centred_at_gaia_epoch_for_a_j2000_position(monkeypatch):
    ident, gaia_pos = barnard_at_j2000()
    ctx = make_ctx(identity=ident)
    q = GaiaNeighbors().query(ctx)
    # under 1″, not 0: the J2000 position kept the 2016 proper motion (perspective acceleration)
    assert separation_arcsec(q["ra"], q["dec"], gaia_pos.ra, gaia_pos.dec) < 1.0
    sent = []
    monkeypatch.setattr(gaia, "tap_rows", lambda url, adql, session: sent.append(adql) or [])
    GaiaNeighbors().fetch(ctx)
    assert sent == [gaia.cone_adql(q["ra"], q["dec"], q["radius_arcsec"])]


def test_neighbor_cone_keeps_a_gaia_position_as_it_is():
    ctx = make_ctx(identity=ref_identity("Barnards_star"))
    pos = ctx.identity.position
    q = GaiaNeighbors().query(ctx)
    assert (q["ra"], q["dec"]) == (pos.ra, pos.dec)  # cache keys of healthy runs do not change


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
