import io
import json
import re

import pytest
from rich.console import Console

from star_report import derive
from star_report.config import load_config
from star_report.models import Position, Report
from star_report.render.html import link_citations, render_html, write_outputs
from star_report.render.terminal import print_summary
from star_report.sources.gaia import GaiaNss
from conftest import load_fixture, make_ctx, make_report, ref_identity


def _all_failed():
    base = make_report()
    results = {
        k: v.model_copy(update={"status": "error", "data": None, "error": "ConnectionError: down"})
        for k, v in base.results.items()
    }
    return base.model_copy(update={"results": results, "parameters": [], "flags": []}), results


def test_full_report_renders(tmp_path):
    html = render_html(make_report(), tmp_path)
    for s in ["HD 209458", "TIC 420814525", "Gaia DR3 1779546757669063552", "Teff", "<details"]:
        assert s in html
    assert "{{" not in html and "{%" not in html
    assert re.search(r'<(img|script|link)[^>]+(src|href)="https?://', html) is None


def test_all_sources_failed_still_renders(tmp_path):
    report, results = _all_failed()
    html = render_html(report, tmp_path)
    assert html[html.index('id="sources"'):].count("unavailable") == len(results)
    assert "unavailable" in html[html.index('id="planets"'):html.index('id="sources"')]
    assert "No parameters available" in html
    assert "Traceback" not in html


def test_empty_and_error_are_distinct(tmp_path):
    base = make_report()
    results = dict(base.results)
    results["tic"] = results["tic"].model_copy(update={"status": "empty", "data": None})
    results["gaia_core"] = results["gaia_core"].model_copy(
        update={"status": "error", "data": None, "error": "ConnectionError: down"})
    html = render_html(base.model_copy(update={"results": results}), tmp_path)
    assert "no data found" in html
    assert "unavailable: ConnectionError: down" in html


def test_no_gaia_identity(tmp_path):
    base = make_report()
    pos = Position(ra=base.identity.position.ra, dec=base.identity.position.dec,
                   epoch=2000.0, source="simbad")
    ident = base.identity.model_copy(update={"position": pos, "gaia_dr3_id": None})
    html = render_html(base.model_copy(update={"identity": ident}), tmp_path)
    assert "J2000 (SIMBAD)" in html


def test_link_citations():
    out = str(link_citations("Hot Jupiter [2000ApJ...529L..45C] & [2008A&A...481..529X]."))
    assert 'href="https://ui.adsabs.harvard.edu/abs/2000ApJ...529L..45C/abstract"' in out and "&amp; " in out
    assert "2008A&amp;A...481..529X</a>" in out


def test_write_outputs(tmp_path):
    out = tmp_path / "nested" / "star"
    path = write_outputs(make_report(), out)
    assert path == out / "report.html" and path.is_file()
    data = json.loads((out / "data.json").read_text())
    assert data["schema_version"] == 1
    Report.model_validate(data)
    (out / "report.html").write_text("stale")
    write_outputs(make_report(), out)
    assert "HD 209458" in (out / "report.html").read_text()


def test_terminal_summary(tmp_path):
    console = Console(record=True, width=110)
    print_summary(make_report(), tmp_path / "report.html", console)
    text = console.export_text()
    assert "HD 209458" in text and "TIC 420814525" in text and str(tmp_path / "report.html") in text
    assert text.count("gaia_core") == 1


def test_html_escaping(tmp_path):
    from star_report.models import ParamCell
    evil = "<script>alert(1)</script>&"
    base = make_report()
    ident = base.identity.model_copy(update={"main_name": evil, "warnings": [evil]})
    results = {k: v.model_copy(update={"status": "error", "error": evil})
               for k, v in base.results.items()}
    rows = [r.model_copy(update={"cells": {k: ParamCell(value=1.0, note=evil)
                                           for k in r.cells}}) for r in base.parameters]
    html = render_html(base.model_copy(update={"identity": ident, "results": results,
                                               "parameters": rows, "flags": [evil]}), tmp_path)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_link_citations_grouped():
    out = str(link_citations(
        "depth [2001ApJ...548..466B, 2002A&A...385..156G; 2003A&A...402..701B]. Not [2001 a]."))
    assert out.count('<a href="https://ui.adsabs.harvard.edu/abs/') == 3
    assert "466B</a>, <a" in out and "156G</a>; <a" in out
    assert out.startswith("depth <a") and out.endswith("402..701B</a>. Not [2001 a].")


def test_link_citations_escapes_html():
    out = str(link_citations("<b>bold</b> [2000ApJ...529L..45C]"))
    assert "<b>" not in out and "&lt;b&gt;" in out


def test_companions_section_toi700(tmp_path):
    html = render_html(make_report("TOI-700"), tmp_path)
    assert "TOI-700 d" in html and "700.01" in html


def test_companions_section_hd209458(tmp_path):
    assert "V0376 Peg" in render_html(make_report("HD_209458"), tmp_path)


def test_companions_status_labels_distinct(tmp_path):
    base = make_report("TOI-700")
    results = dict(base.results)
    results["vsx"] = results["vsx"].model_copy(update={"status": "error", "data": None, "error": "x"})
    html = render_html(base.model_copy(update={"results": results}), tmp_path)
    sec = html[html.index('id="variability"'):]
    assert "unavailable" in sec
    assert "no data found" in html[html.index('id="binarity"'):html.index('id="variability"')]


def test_terminal_planets_line(tmp_path):
    console = Console(record=True, width=110)
    print_summary(make_report("TOI-700"), tmp_path / "report.html", console)
    assert "Planets:" in console.export_text()


def _with(report, **updates):
    results = dict(report.results)
    for k, v in updates.items():
        results[k] = results[k].model_copy(update=v)
    return report.model_copy(update={"results": results})


_NSS_ORBIT = {"kind": "orbit", "type": "SB1", "period_d": 3.164209, "period_err": 0.000198,
              "ecc": 0.090952, "k1_kms": 3.662262, "inclination": None, "mass_ratio": None}
_NSS_ACCEL = {"kind": "acceleration", "type": "Acceleration9",
              "accel_ra": 0.249932, "accel_ra_err": 0.068566,
              "accel_dec": -0.834295, "accel_dec_err": 0.029093,
              "deriv_accel_ra": -9.824574, "deriv_accel_ra_err": 0.290533,
              "deriv_accel_dec": -0.642292, "deriv_accel_dec_err": 0.110981,
              "significance": 34.090904}
_NSS_TREND = {"kind": "trend", "type": "SecondDegreeTrendSB1",
              "mean_velocity": -14.171673, "mean_velocity_err": 1.692782,
              "first_deriv_velocity": 0.003772, "first_deriv_velocity_err": 0.006963,
              "second_deriv_velocity": 3.2e-05, "second_deriv_velocity_err": 4.3e-05}


def _binarity(rows, tmp_path):
    report = _with(make_report(), gaia_nss={"status": "ok", "data": rows})
    html = render_html(report, tmp_path)
    return html[html.index('id="binarity"'):html.index('id="variability"')]


def _tables(sec):
    """{heading: [row, ...]} for each table in a section; each row is a {header: cell} dict."""
    out = {}
    for heading, table in re.findall(r"<h3>(.*?)</h3>\s*<table>(.*?)</table>", sec, flags=re.S):
        head = re.findall(r"<th>(.*?)</th>", table)
        body = table[table.index("<tbody>"):]
        out[heading] = [dict(zip(head, re.findall(r"<td[^>]*>(.*?)</td>", tr), strict=True))
                        for tr in re.findall(r"<tr>(.*?)</tr>", body, flags=re.S)]
    return out


_ACCEL_HEADERS = ["Type", "Accel. RA (mas/yr²)", "Accel. Dec (mas/yr²)",
                  "d/dt accel. RA (mas/yr³)", "d/dt accel. Dec (mas/yr³)", "Significance"]


def test_binarity_puts_each_nss_kind_in_its_own_table(tmp_path):
    tables = _tables(_binarity([_NSS_ORBIT, _NSS_ACCEL, _NSS_TREND], tmp_path))
    assert tables["Gaia DR3 non-single-star orbits"] == [{
        "Type": "SB1", "Period (d)": "3.164", "Eccentricity": "0.091", "K1 (km/s)": "3.66",
        "Inclination (deg)": "", "Mass ratio": ""}]
    assert tables["Gaia DR3 astrometric accelerations"] == [dict(zip(_ACCEL_HEADERS, [
        "Acceleration9", "0.250 ± 0.069", "-0.834 ± 0.029", "-9.82 ± 0.29", "-0.64 ± 0.11",
        "34.1"]))]
    assert tables["Gaia DR3 RV trends"] == [{
        "Type": "SecondDegreeTrendSB1", "Mean RV (km/s)": "-14.17 ± 1.69",
        "dRV/dt (km/s/d)": "0.0038 ± 0.0070", "d²RV/dt² (km/s/d²)": "0.000032 ± 0.000043"}]


def test_binarity_renders_the_recorded_hd18284_solutions(tmp_path):
    """The parser's output for the recorded star, whose acceleration derivatives are NULL."""
    ctx = make_ctx(identity=ref_identity("HD_18284"))
    rows = GaiaNss().parse(load_fixture("gaia_nss", "HD_18284"), ctx)
    tables = _tables(_binarity(rows, tmp_path))
    assert tables["Gaia DR3 non-single-star orbits"][0]["Period (d)"] == "3.164"
    assert tables["Gaia DR3 astrometric accelerations"] == [dict(zip(_ACCEL_HEADERS, [
        "Acceleration7", "2.658 ± 0.118", "-4.183 ± 0.097", "", "", "50.8"]))]
    assert "Gaia DR3 RV trends" not in tables


def test_binarity_renders_rows_without_values(tmp_path):
    first_degree = {**_NSS_TREND, "type": "FirstDegreeTrendSB1", "second_deriv_velocity": None,
                    "second_deriv_velocity_err": None}
    bare = {k: None for k in _NSS_ACCEL} | {"kind": "acceleration"}
    tables = _tables(_binarity([bare, first_degree], tmp_path))
    assert tables["Gaia DR3 astrometric accelerations"] == [dict.fromkeys(_ACCEL_HEADERS, "")]
    assert tables["Gaia DR3 RV trends"] == [{
        "Type": "FirstDegreeTrendSB1", "Mean RV (km/s)": "-14.17 ± 1.69",
        "dRV/dt (km/s/d)": "0.0038 ± 0.0070", "d²RV/dt² (km/s/d²)": ""}]


def test_binarity_leaves_a_missing_solution_type_blank(tmp_path):
    for row in (_NSS_ORBIT, _NSS_ACCEL, _NSS_TREND):
        sec = _binarity([{**row, "type": None}], tmp_path)
        assert "<td>None</td>" not in sec and "<tr><td></td>" in sec


def test_binarity_omits_empty_nss_tables(tmp_path):
    sec = _binarity([_NSS_ORBIT], tmp_path)
    assert "non-single-star orbits" in sec
    assert "astrometric accelerations" not in sec and "RV trends" not in sec
    sec = _binarity([_NSS_ACCEL], tmp_path)
    assert "non-single-star orbits" not in sec and "astrometric accelerations" in sec


def test_toi_rule_archive_ok_but_empty(tmp_path):
    base = make_report("TOI-700")
    arch = dict(base.results["exoplanet_archive"].data, tois=[])
    report = _with(base, exoplanet_archive={"data": arch})
    html = render_html(report, tmp_path)
    assert "(ExoFOP)" not in html and "TFOP disposition" not in html
    assert '<p class="muted">none</p>' in html
    assert not any(f.startswith("TOIs:") for f in derive.flags(
        report.identity, report.results, load_config(cwd=tmp_path).flags))
    console = Console(record=True, width=110)
    print_summary(report, tmp_path / "r.html", console)
    assert "TOIs:" not in console.export_text().split("Planets:")[1].splitlines()[0]


def test_toi_rule_archive_error_falls_back_to_exofop(tmp_path):
    base = make_report("TOI-700")
    report = _with(base, exoplanet_archive={"status": "error", "data": None, "error": "x"})
    html = render_html(report, tmp_path)
    assert "TFOP disposition" in html and "700.01" in html
    flags = derive.flags(report.identity, report.results, load_config(cwd=tmp_path).flags)
    assert any(f.startswith("TOIs: 700.01") for f in flags)
    console = Console(record=True, width=110)
    print_summary(report, tmp_path / "r.html", console)
    assert "TOIs: 700.01" in console.export_text()


def _summary(report, tmp_path):
    console = Console(record=True, width=110, file=io.StringIO())
    print_summary(report, tmp_path / "r.html", console)
    return console.export_text()


def _planets(text):
    return [line for line in text.splitlines() if line.startswith("Planets:")]


@pytest.mark.parametrize("update", [
    {"status": "error", "data": None, "error": "ConnectionError: down"},
    {"status": "timeout", "data": None, "error": "timed out after 30 s"},
    {"status": "skipped", "data": None, "error": "disabled in config"},
])
def test_terminal_planet_count_unknown_when_archive_did_not_answer(tmp_path, update):
    text = _summary(_with(make_report("TOI-700"), exoplanet_archive=update), tmp_path)
    assert _planets(text) == ["Planets: unknown; TOIs: 700.01, 700.02, 700.03, 700.04"]
    assert "0 confirmed" not in text


def test_terminal_planet_count_unknown_without_archive_result(tmp_path):
    base = make_report("TOI-700")
    results = {k: v for k, v in base.results.items() if k != "exoplanet_archive"}
    text = _summary(base.model_copy(update={"results": results}), tmp_path)
    assert _planets(text) == ["Planets: unknown; TOIs: 700.01, 700.02, 700.03, 700.04"]


def test_terminal_planet_count_from_archive(tmp_path):
    base = make_report("TOI-700")
    assert _planets(_summary(base, tmp_path)) == [
        "Planets: 4 confirmed; TOIs: 700.01, 700.02, 700.03, 700.04"]
    empty = _with(base, exoplanet_archive={"status": "empty", "data": {}})  # answered: none
    assert _planets(_summary(empty, tmp_path)) == [
        "Planets: 0 confirmed; TOIs: 700.01, 700.02, 700.03, 700.04"]


def test_header_shows_simbad_reference_count(tmp_path):
    report = make_report()
    html = render_html(report, tmp_path)
    header = html[html.index("<header>"):html.index("</header>")]
    assert "<p>F9V, V = 7.63, 48.1 pc · SIMBAD: 1203 references</p>" in header
    assert "F9V, V = 7.63, 48.1 pc · SIMBAD: 1203 references" in _summary(report, tmp_path)


def test_no_reference_count_without_simbad(tmp_path):
    report = _with(make_report(), simbad={"status": "error", "data": None, "error": "down"})
    assert "references" not in render_html(report, tmp_path)
    assert "references" not in _summary(report, tmp_path)


def _ads_report(llm):
    from star_report.models import SourceResult
    r = make_report()
    ads = SourceResult(source="ads", status="ok", data={"num_found": 1, "papers": [
        {"bibcode": "2008A&A...481..529X", "title": "T", "authors": [], "citation_count": 1}]})
    return r.model_copy(update={"results": {**r.results, "ads": ads}, "llm": llm})


def _ok_llm(cached=False):
    from star_report.models import LLMBlock
    return LLMBlock(status="ok", overview="A <b>star</b>.", model="claude-opus-5-5", from_cache=cached,
                    literature_summary="Transit seen [2008A&A...481..529X].",
                    cited_bibcodes=["2008A&A...481..529X"], bibcodes_sent=["2008A&A...481..529X"])


def test_llm_ok_renders(tmp_path):
    html = render_html(_ads_report(_ok_llm()), tmp_path)
    assert "LLM-written · claude-opus-5-5" in html and "(cached)" not in html
    assert "A &lt;b&gt;star&lt;/b&gt;." in html
    assert 'href="https://ui.adsabs.harvard.edu/abs/2008A&amp;A...481..529X/abstract">2008A&amp;A...481..529X</a>.' in html
    assert html.index("Transit seen") < html.index('class="papers"')
    assert "LLM sections not included" not in html
    assert "LLM-written · claude-opus-5-5 (cached)" in render_html(_ads_report(_ok_llm(True)), tmp_path)


def test_llm_failed_renders_reason_in_both_places(tmp_path):
    from star_report.models import LLMBlock
    html = render_html(_ads_report(LLMBlock(status="failed", reason="boom <x>")), tmp_path)
    assert html.count("LLM sections not included: boom &lt;x&gt;") == 2
    assert "LLM-written" not in html


def test_terminal_llm(tmp_path):
    console = Console(record=True, width=110)
    print_summary(_ads_report(_ok_llm()), tmp_path / "r.html", console)
    text = console.export_text()
    assert "A <b>star</b>." in text and "claude-opus-5-5" in text
    from star_report.models import LLMBlock
    console = Console(record=True, width=110)
    print_summary(_ads_report(LLMBlock(status="failed", reason="boom")), None, console)
    assert "LLM sections not included: boom" in console.export_text()
