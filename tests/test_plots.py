"""Plot stage: CMD helpers, make_plots error isolation, HTML embedding."""

from __future__ import annotations

import pytest
from conftest import make_report

from star_report import plots
from star_report.cache import Cache
from star_report.config import load_config
from star_report.models import Provenance, SourceResult
from star_report.plots import cmd
from star_report.render.html import render_html

PNG = b"\x89PNG"
BACKGROUND = [[0.5 + 0.01 * i, 4.0 + 0.05 * i] for i in range(50)]


def _gaia(**data):
    return {"gaia_core": SourceResult(source="gaia_core", status="ok", data=data)}


def test_target_point_hd209458():
    bp_rp, abs_g = cmd.target_point(_gaia(bp_rp=0.713, phot_g_mean_mag=7.521, parallax=20.7694))
    assert bp_rp == pytest.approx(0.713)
    assert abs_g == pytest.approx(4.108, abs=0.01)


@pytest.mark.parametrize("data", [
    {"bp_rp": 0.7, "phot_g_mean_mag": 7.5, "parallax": 0},
    {"bp_rp": 0.7, "phot_g_mean_mag": 7.5, "parallax": -1.0},
    {"bp_rp": 0.7, "phot_g_mean_mag": 7.5, "parallax": None},
    {"bp_rp": None, "phot_g_mean_mag": 7.5, "parallax": 10.0},
    {"bp_rp": 0.7, "phot_g_mean_mag": "bright", "parallax": 10.0},
])
def test_target_point_none_when_unusable(data):
    assert cmd.target_point(_gaia(**data)) is None


def test_target_point_none_without_gaia():
    assert cmd.target_point({}) is None
    assert cmd.target_point({"gaia_core": SourceResult(source="gaia_core", status="error")}) is None


@pytest.mark.parametrize("target", [(0.71, 4.1), None])
def test_plot_cmd_writes_png(tmp_path, target):
    path = cmd.plot_cmd(BACKGROUND, target, "HD 209458", tmp_path / "cmd.png")
    assert path.read_bytes().startswith(PNG)


def test_background_adql_is_verbatim():
    assert cmd.BACKGROUND_ADQL.format(n=7).startswith("SELECT TOP 7 bp_rp,")
    assert "ORDER BY random_index" in cmd.BACKGROUND_ADQL


def test_fetch_background_caches_and_drops_none(tmp_path, monkeypatch):
    cfg = load_config(cwd=tmp_path)
    cfg.plots.cmd_background_rows = 3
    cache = Cache(tmp_path / "cache", cfg.cache.expiry_days, cfg.cache.empty_expiry_days)
    calls = []

    def fake_async(session, adql):
        calls.append(adql)
        return [{"bp_rp": 0.5, "abs_g": 4.0}, {"bp_rp": None, "abs_g": 3.0},
                {"bp_rp": 1.0, "abs_g": None}, {"bp_rp": 1.5, "abs_g": 7.0}]

    monkeypatch.setattr(cmd, "_run_async", fake_async)
    assert cmd.fetch_background(cache, cfg, object()) == [[0.5, 4.0], [1.5, 7.0]]
    assert cmd.fetch_background(cache, cfg, object()) == [[0.5, 4.0], [1.5, 7.0]]
    assert len(calls) == 1 and "TOP 3" in calls[0]


def test_make_plots_isolates_failures(tmp_path, monkeypatch):
    def ok_cmd(report, path, cfg, cache, session):
        path.write_bytes(PNG + b"x")
        return path, None

    def bad_lightcurve(report, path, cfg, cache, session):
        raise RuntimeError("x")

    monkeypatch.setitem(plots.PLOTTERS, "cmd", ok_cmd)
    monkeypatch.setitem(plots.PLOTTERS, "lightcurve", bad_lightcurve)
    cfg = load_config(cwd=tmp_path)
    paths, notes = plots.make_plots(make_report(), tmp_path / "plots", cfg, None)
    assert paths == {"cmd": "plots/cmd.png"}
    assert notes == {"lightcurve": "RuntimeError: x"}
    assert (tmp_path / "plots" / "cmd.png").exists()


def test_html_embeds_plot_and_note(tmp_path):
    (tmp_path / "plots").mkdir()
    (tmp_path / "plots" / "cmd.png").write_bytes(PNG + b"data")
    report = make_report(plots={"cmd": "plots/cmd.png"},
                         plot_notes={"lightcurve": "RuntimeError: x"})
    html = render_html(report, tmp_path)
    assert "data:image/png;base64," in html
    assert "RuntimeError: x" in html


def test_html_without_plots_has_no_images(tmp_path):
    assert "data:image/png" not in render_html(make_report(), tmp_path)
