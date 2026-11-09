"""Derived tables, names and flags, pinned with synthetic source results."""

from __future__ import annotations

import pytest

from star_report.config import load_config
from star_report.derive import (
    description_line,
    disagree,
    display_name,
    flags,
    header_line,
    parameter_rows,
    simbad_references,
    slugify,
)
from star_report.models import ParamCell, Position, SourceResult, StarIdentity


@pytest.fixture(scope="module")
def cfg(tmp_path_factory):
    return load_config(cwd=tmp_path_factory.mktemp("cfg"))


def ok(source, data):
    return SourceResult(source=source, status="ok", data=data)


def failed(source):
    return SourceResult(source=source, status="error", error="boom")


def identity():
    return StarIdentity(
        input="x", main_name="x", resolved_via="simbad",
        position=Position(ra=1.0, dec=2.0, epoch=2000.0, source="simbad"),
    )


def row(rows, key):
    return next((r for r in rows if r.key == key), None)


@pytest.mark.parametrize("main_id,display,slug", [
    ("HD 209458", "HD 209458", "HD_209458"),
    ("NAME Barnard's star", "Barnard's star", "Barnards_star"),
    ("* alf Lyr", "alf Lyr", "alf_Lyr"),
    ("V* V1298 Tau", "V1298 Tau", "V1298_Tau"),
    ("TIC 420814525", "TIC 420814525", "TIC_420814525"),
    ("2MASS J22031077+1853036", "2MASS J22031077+1853036", "2MASS_J22031077+1853036"),
    ("***", "***", "star"),
])
def test_names(main_id, display, slug):
    assert display_name(main_id) == display
    assert slugify(display) == slug


def test_gaia_prefers_gspspec(cfg):
    gaia = {"ap": {"teff_gspspec": 5950.0, "teff_gspspec_lower": 5910.0,
                   "teff_gspspec_upper": 5990.0, "teff_gspphot": 6100.0,
                   "teff_gspphot_lower": 6090.0, "teff_gspphot_upper": 6110.0}}
    rows = parameter_rows({"gaia_core": ok("gaia_core", gaia)}, cfg.parameters)
    assert row(rows, "teff").cells["gaia"] == ParamCell(value=5950, err=40, note="GSP-Spec")


def test_gaia_falls_back_to_gspphot(cfg):
    gaia = {"ap": {"teff_gspspec": None, "teff_gspphot": 6100.0,
                   "teff_gspphot_lower": 6090.0, "teff_gspphot_upper": 6110.0}}
    rows = parameter_rows({"gaia_core": ok("gaia_core", gaia)}, cfg.parameters)
    assert row(rows, "teff").cells["gaia"] == ParamCell(value=6100, err=10, note="GSP-Phot")


def test_distance_prefers_photogeo(cfg):
    dist = {"r_med_photogeo": 48.0, "r_lo_photogeo": 47.0, "r_hi_photogeo": 49.0,
            "r_med_geo": 50.0, "r_lo_geo": 49.0, "r_hi_geo": 51.0}
    rows = parameter_rows({"gaia_core": ok("gaia_core", {"distance": dist})}, cfg.parameters)
    assert row(rows, "distance").cells["gaia"] == ParamCell(
        value=48.0, err=1.0, note="Bailer-Jones photogeo")


def test_sigma_rule():
    assert disagree(ParamCell(value=6000, err=50), ParamCell(value=6300, err=50), 3, 0.1, None)
    assert not disagree(ParamCell(value=6000, err=100), ParamCell(value=6200, err=100), 3, 0.1, None)


def test_fraction_rule():
    assert disagree(ParamCell(value=1.0), ParamCell(value=1.2), 3, 0.1, None)
    assert not disagree(ParamCell(value=1.0), ParamCell(value=1.05), 3, 0.1, None)


def test_absolute_rule_for_logg(cfg):
    tic = lambda v: ok("tic", {"logg": v})  # noqa: E731
    gaia = {"ap": {"logg_gspspec": 4.40}}
    for other, expected in [(4.55, False), (4.70, True)]:
        rows = parameter_rows({"gaia_core": ok("gaia_core", gaia), "tic": tic(other)},
                              cfg.parameters)
        assert row(rows, "logg").disagree is expected


def test_row_disagree_and_fraction_via_rows(cfg):
    gaia = {"ap": {"radius_flame": 1.0}}
    rows = parameter_rows({"gaia_core": ok("gaia_core", gaia),
                           "tic": ok("tic", {"rad": 1.2})}, cfg.parameters)
    assert row(rows, "radius").disagree is True


def test_rows_skip_missing_sources(cfg):
    results = {"gaia_core": failed("gaia_core"),
               "tic": ok("tic", {"Teff": 5900, "e_Teff": 100, "Tmag": 7.0})}
    rows = parameter_rows(results, cfg.parameters)
    assert [r.key for r in rows] == ["teff", "Tmag"]
    assert all(set(r.cells) == {"tic"} for r in rows)
    assert row(rows, "teff").cells["tic"] == ParamCell(value=5900, err=100)
    assert not any(r.disagree for r in rows)


def test_description_line(cfg):
    results = {
        "simbad": ok("simbad", {"sp_type": "G0V", "V": 7.65}),
        "gaia_core": ok("gaia_core", {"distance": {"r_med_photogeo": 48.3}}),
    }
    rows = parameter_rows(results, cfg.parameters)
    assert description_line(results, rows) == "G0V, V = 7.65, 48.3 pc"
    results2 = {"simbad": ok("simbad", {"sp_type": None, "V": 7.65}),
                "tic": ok("tic", {"d": 50.04})}
    rows2 = parameter_rows(results2, cfg.parameters)
    assert description_line(results2, rows2) == "V = 7.65, 50.0 pc"
    assert description_line({}, []) == ""


@pytest.mark.parametrize("result,text", [
    (ok("simbad", {"nbref": 1203}), "SIMBAD: 1203 references"),
    (ok("simbad", {"nbref": 1}), "SIMBAD: 1 reference"),
    (ok("simbad", {"nbref": 0}), "SIMBAD: 0 references"),
    (ok("simbad", {"nbref": 12.0}), "SIMBAD: 12 references"),
    (ok("simbad", {"nbref": None}), ""),
    (ok("simbad", {"nbref": "1203"}), ""),
    (ok("simbad", {"nbref": True}), ""),
    (ok("simbad", {"nbref": float("nan")}), ""),
    (ok("simbad", {"nbref": float("inf")}), ""),
    (ok("simbad", {}), ""),
    (ok("simbad", ["not", "a", "dict"]), ""),
    (SourceResult(source="simbad", status="empty"), ""),
    (failed("simbad"), ""),
])
def test_simbad_references(result, text):
    assert simbad_references({"simbad": result}) == text


def test_header_line(cfg):
    results = {"simbad": ok("simbad", {"sp_type": "G0V", "V": 7.65, "nbref": 1203})}
    rows = parameter_rows(results, cfg.parameters)
    assert header_line(results, rows) == "G0V, V = 7.65 · SIMBAD: 1203 references"
    assert header_line({"simbad": ok("simbad", {"nbref": 3})}, []) == "SIMBAD: 3 references"
    assert header_line({}, []) == simbad_references({}) == ""


def test_core_flags(cfg):
    results = {
        "gaia_core": ok("gaia_core", {"ruwe": 2.4, "phot_variable_flag": "VARIABLE",
                                      "non_single_star": 1}),
        "tic": ok("tic", {"contratio": 0.35}),
    }
    assert flags(identity(), results, cfg.flags) == [
        "RUWE 2.4: possible unresolved companion",
        "TIC contamination ratio 0.35",
        "Gaia DR3 flags the star as photometrically variable",
        "Gaia DR3 non-single-star flag set",
    ]
    quiet = {
        "gaia_core": ok("gaia_core", {"ruwe": 1.0, "phot_variable_flag": "NOT_AVAILABLE",
                                      "non_single_star": 0}),
        "tic": ok("tic", {"contratio": 0.01}),
    }
    assert flags(identity(), quiet, cfg.flags) == []
    assert flags(identity(), {"gaia_core": failed("gaia_core")}, cfg.flags) == []


def _flags_for(results):
    return flags(identity(), results, load_config(cwd=__import__("pathlib").Path("/tmp")).flags)


def test_companion_flags_in_order():
    results = {
        "exoplanet_archive": ok("exoplanet_archive", {
            "planets": [{"name": "a b"}, {"name": "a c"}],
            "tois": [{"toi": "700.01", "disposition": "CP"}, {"toi": "700.02", "disposition": None}],
            "kois": [{"koi": "K00001.01", "disposition": "CONFIRMED"}], "k2": []}),
        "exofop": ok("exofop", {"tois": [{"toi": "9.01", "disp_tfop": "PC"}]}),
        "gaia_nss": ok("gaia_nss", [{"type": "SB1", "period_d": 12.345},
                                    {"type": "Astrometric", "period_d": None}]),
        "gaia_vari": ok("gaia_vari", {"classes": [{"class": "EP"}],
                                      "periods": [{"type": "planetary_transit", "period_d": 0.899005}]}),
        "vsx": ok("vsx", [{"name": "V0376 Peg", "type": "EP", "period_d": 3.52474859},
                          {"name": "X", "type": "RR", "period_d": None}]),
        "wds": ok("wds", [{"wds": "00001+0001", "disc": "AB", "comp": "C", "sep_arcsec": 4.26,
                           "last_obs": 2015}]),
        "sb9": ok("sb9", [{"period_d": 2.8673, "k1_kms": 80.1}]),
        "wide_binaries": ok("wide_binaries", [{"companion": "123", "sep_arcsec": 10.04,
                                               "sep_au": 1500.4, "R_chance": 0.00123}]),
    }
    assert _flags_for(results) == [
        "2 confirmed planets",
        "TOIs: 700.01 (CP), 700.02",
        "KOIs: K00001.01 (CONFIRMED)",
        "Gaia DR3 SB1 orbit, P = 12.35 d",
        "Gaia DR3 Astrometric solution",
        "Gaia DR3 variability class: EP",
        "Gaia DR3 planetary_transit period 0.899 d",
        "VSX: V0376 Peg, type EP, P = 3.52474859 d",
        "VSX: X, type RR",
        "WDS 00001+0001 AB C: 4.3″ (observed 2015)",
        "SB9 orbit: P = 2.8673 d, K1 = 80.1 km/s",
        "Gaia wide binary: Gaia DR3 123 at 10.0″ (1500 au), R = 0.0012",
    ]


def test_toi_flag_fallback_and_singular():
    results = {
        "exoplanet_archive": failed("exoplanet_archive"),
        "exofop": ok("exofop", {"tois": [{"toi": "9.01", "disp_tfop": "PC"}]}),
    }
    assert _flags_for(results) == ["TOIs: 9.01 (PC)"]
    one = {"exoplanet_archive": ok("exoplanet_archive", {"planets": [{"name": "p"}], "tois": [],
                                                         "kois": [], "k2": []}),
           "exofop": ok("exofop", {"tois": [{"toi": "9.01", "disp_tfop": "PC"}]})}
    assert _flags_for(one) == ["1 confirmed planet"]


def test_companion_flags_tolerate_missing_fields():
    results = {"wide_binaries": ok("wide_binaries", [{"companion": "1", "sep_arcsec": 3.0,
                                                      "sep_au": None, "R_chance": None}])}
    out = _flags_for(results)
    assert out == ["Gaia wide binary: Gaia DR3 1 at 3.0″"]
    assert not any("None" in f for f in out)
