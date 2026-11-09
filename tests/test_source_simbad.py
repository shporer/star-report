from conftest import load_fixture, make_ctx, ref_identity

from star_report.sources import simbad
from star_report.sources.base import run_sources
from star_report.sources.simbad import Simbad


def test_adql_escapes_apostrophe():
    assert "i.id = 'Barnard''s Star'" in simbad.adql("Barnard's Star")


def test_parse_hd209458(ctx):
    rec = Simbad().parse(load_fixture("simbad", "HD_209458"), ctx)
    assert rec["main_id"] == "HD 209458" and rec["sp_type"].startswith("F9")
    assert 7.5 < rec["V"] < 7.8 and rec["nbref"] > 1000
    assert {"TIC 420814525", "Gaia DR3 1779546757669063552", "HIP 108859"} <= set(rec["ids"])


def test_parse_barnard(ctx):
    rec = Simbad().parse(load_fixture("simbad", "Barnards_star"), ctx)
    assert rec["main_id"] == "NAME Barnard's star"
    assert "GJ 699" in rec["ids"]


def test_parse_no_rows(ctx):
    assert Simbad().parse([], ctx) is None


def test_skipped_without_simbad_id(tmp_path):
    ident = ref_identity("HD_209458").model_copy(update={"simbad_id": None})
    res = run_sources([Simbad()], make_ctx(identity=ident, cache_dir=tmp_path / "c"))["simbad"]
    assert res.status == "skipped" and res.error == "no SIMBAD entry"
