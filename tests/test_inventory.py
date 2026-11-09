import re

import pytest
from rich.console import Console

from conftest import load_fixture, make_ctx, make_report, ref_identity

from star_report.render.html import render_html
from star_report.render.terminal import print_summary
from star_report.sources import vizier
from star_report.sources.registry import build_sources

# gaia_core is left out: the companions section (Task 16) reads gaia_core.ruwe unguarded.
ARCHIVE = {"mast", "tess_pointing", "eso", "koa", "exofop", "harps_rvbank", "apogee", "lamost"}


def _src(name):
    return {s.name: s for s in build_sources(make_ctx().config)}[name]


def _parse(name, slug):
    return _src(name).parse(load_fixture(name, slug), make_ctx(identity=ref_identity(slug)))


def test_registered_after_koa_in_order():
    names = [s.name for s in build_sources(make_ctx().config)]
    idx = [names.index(n) for n in ("koa", "harps_rvbank", "apogee", "lamost")]
    assert idx == sorted(idx)


def test_harps_barnards_star():
    d = _parse("harps_rvbank", "Barnards_star")
    assert d["name"] == "GJ699" and d["n_rv"] == 306 and 10 < d["span_yr"] < 11


def test_apogee_kepler10():
    d = _parse("apogee", "Kepler-10")
    assert 5300 < d["teff"] < 6000 and d["apogee_id"]


def test_lamost_kepler10():
    # Kepler-10 has a LAMOST DR11 low-resolution spectrum, so no other reference star is needed.
    d = _parse("lamost", "Kepler-10")
    assert d["n_spectra"] >= 1 and d["last_obs"] and 5000 < d["teff"] < 6500


def test_lamost_table_pinned():
    assert make_ctx().config.sources.vizier_catalogs["lamost"] == "V/162/dr11sl"


def test_lamost_picks_highest_snr_row():
    raw = {"V/162/dr11sl": [
        {"Obs.Date": "2012-01-01", "Teff": 5000.0, "logg": 4.0, "[Fe/H]": 0.0, "snrg": 10.0},
        {"Obs.Date": "2014-01-01", "Teff": 5600.0, "logg": 4.3, "[Fe/H]": -0.1, "snri": 90.0}]}
    d = vizier.parse_lamost(raw, make_ctx())
    assert d["n_spectra"] == 2 and d["teff"] == 5600.0 and d["last_obs"] == "2014-01-01"


@pytest.mark.parametrize("parser", [vizier.parse_harps_rvbank, vizier.parse_apogee, vizier.parse_lamost])
@pytest.mark.parametrize("raw", [None, [], "x", 3, {"t": 5}, {"t": [1, "a", None]},
                                 {"t": [{"Teff": "hot", "Nrv": "many", "BJD": "x", "SNR": [1]}]}])
def test_parsers_tolerate_malformed_shapes(parser, raw):
    assert isinstance(parser(raw, make_ctx()), dict)


def test_harps_followup_skips_without_names(monkeypatch):
    monkeypatch.setattr(vizier, "vizier_client", lambda *a, **k: pytest.fail("queried"))
    assert vizier.harps_rvs(make_ctx(), "J/A+A/636/A74", {"J/A+A/636/A74/list": [{"Name": " "}]}) == {}


def test_apogee_malformed_gaia_id_skips_query(monkeypatch):
    monkeypatch.setattr(vizier, "vizier_client", lambda *a, **k: pytest.fail("queried"))
    ident = ref_identity("Kepler-10").model_copy(update={"gaia_dr3_id": "1; DROP"})
    ctx = make_ctx(identity=ident)
    cfg = ctx.config.model_copy(deep=True)
    cfg.sources.radius_arcsec.pop("apogee")
    ctx = make_ctx(identity=ident, cfg=cfg)
    assert _src("apogee").fetch(ctx) == {}


def test_inventory_section_hd209458(tmp_path):
    html = render_html(make_report("HD_209458"), tmp_path)
    sec = html[html.index('id="inventory"'):]
    assert "ESPRESSO" in sec and "HARPS" in sec and "None" not in sec
    observed = re.search(r"<li>Observed: ([^<]*)</li>", sec)
    assert observed and 56 in [int(x) for x in observed.group(1).split(", ")]


def test_inventory_barnards_star(tmp_path):
    html = render_html(make_report("Barnards_star"), tmp_path)
    sec = html[html.index('id="inventory"'):]
    assert "306" in sec and "118" in sec and "None" not in sec


def test_inventory_kepler10_surveys(tmp_path):
    sec = render_html(make_report("Kepler-10"), tmp_path)
    sec = sec[sec.index('id="inventory"'):]
    assert "APOGEE" in sec and "LAMOST" in sec


def test_inventory_status_labels_distinct(tmp_path):
    base = make_report("HD_209458")
    results = dict(base.results)
    results["eso"] = results["eso"].model_copy(update={"status": "error", "data": None, "error": "boom"})
    results["koa"] = results["koa"].model_copy(update={"status": "empty", "data": {}})
    html = render_html(base.model_copy(update={"results": results}), tmp_path)
    sec = html[html.index('id="inventory"'):]
    assert "eso: unavailable: boom" in sec and "koa: no data found" in sec
    assert "ESPRESSO" not in sec


def test_inventory_escapes_and_rejects_bad_urls(tmp_path):
    evil = "<script>alert(1)</script>"
    base = make_report("HD_209458")
    results = dict(base.results)
    mast = dict(results["mast"].data, portal_url="javascript:alert(1)")
    mast["other"] = [{"collection": evil, "instrument": evil, "product": evil, "n": 1}]
    results["mast"] = results["mast"].model_copy(update={"data": mast})
    html = render_html(base.model_copy(update={"results": results}), tmp_path)
    assert evil not in html and "javascript:" not in html


@pytest.mark.parametrize("bad", ["x", 5, [1], {"tess": "x", "hlsp": 3, "other": ["a", 1], "kepler": 2}])
def test_inventory_tolerates_malformed_data(tmp_path, bad):
    base = make_report("HD_209458")
    results = {k: v.model_copy(update={"data": bad}) for k, v in base.results.items() if k in ARCHIVE}
    render_html(base.model_copy(update={"results": {**base.results, **results}}), tmp_path)


def test_terminal_data_line():
    console = Console(record=True, width=120)
    print_summary(make_report("HD_209458"), None, console)
    line = next(ln for ln in console.export_text().splitlines() if ln.startswith("Data:"))
    assert "ESO 341" in line and "2 observed" in line


def test_inventory_upcoming_sector_with_date(tmp_path):
    base = make_report("HD_209458")
    results = dict(base.results)
    tp = dict(results["tess_pointing"].data, upcoming=[{"sector": 99, "start": "2027-01-05"}])
    results["tess_pointing"] = results["tess_pointing"].model_copy(update={"data": tp})
    html = render_html(base.model_copy(update={"results": results}), tmp_path)
    assert "<li>Upcoming: sector 99 (2027-01-05)</li>" in html
