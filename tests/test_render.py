import json
import re

from rich.console import Console

from star_report import derive
from star_report.config import load_config
from star_report.models import Position, Report
from star_report.render.html import link_citations, render_html, write_outputs
from star_report.render.terminal import print_summary
from conftest import make_report


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
