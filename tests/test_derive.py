"""Derived tables, names and flags, pinned with synthetic source results."""

from __future__ import annotations

import pytest

from star_report.config import load_config
from star_report.derive import (
    description_line,
    disagree,
    display_name,
    flags,
    parameter_rows,
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
