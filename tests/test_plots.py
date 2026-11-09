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
    monkeypatch.setitem(plots.PLOTTERS, "sky", lambda *a: (None, None))
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


# ---- light curves ----------------------------------------------------------

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from star_report.plots.lightcurve import (  # noqa: E402
    SectorCurve,
    choose_products,
    periodogram,
    plot_lightcurves,
)


def _row(author, exptime, seq, mission=""):
    return {"author": author, "exptime": exptime, "sequence_number": seq, "mission": mission}


def test_choose_most_recent_spoc():
    rows = [_row("SPOC", 120, s, f"TESS Sector {s:02d}") for s in range(1, 38)]
    rows += [_row("TESS-SPOC", 200, s) for s in (36, 37)]
    idx, note = choose_products(rows, 10)
    assert [rows[i]["sequence_number"] for i in idx] == list(range(37, 27, -1))
    assert all(rows[i]["author"] == "SPOC" for i in idx)
    assert note == "Showing the 10 most recent of 37 sectors."


def test_no_note_when_under_limit():
    idx, note = choose_products([_row("SPOC", 120, 1), _row("SPOC", 120, 2)], 10)
    assert len(idx) == 2 and note is None


def test_qlp_fills_missing_sector():
    rows = [_row("SPOC", 120, 4), _row("QLP", 1800, 5), _row("SPOC", 120, 6)]
    idx, _ = choose_products(rows, 10)
    assert sorted(rows[i]["sequence_number"] for i in idx) == [4, 5, 6]
    assert rows[[i for i in idx if rows[i]["sequence_number"] == 5][0]]["author"] == "QLP"


def test_spoc_20s_ignored():
    rows = [_row("SPOC", 20, 5), _row("SPOC", 120, 5)]
    idx, _ = choose_products(rows, 10)
    assert idx == [1]
    assert choose_products([_row("SPOC", 20, 5)], 10) == ([], None)


def test_kepler_long_cadence_only():
    rows = []
    for q in (1, 2, 3):
        rows += [_row("Kepler", 60, q), _row("Kepler", 1800, q), _row("KBONUS-BKG", 1800, q)]
    idx, _ = choose_products(rows, 10)
    assert len(idx) == 3 and all(rows[i]["exptime"] == 1800 and rows[i]["author"] == "Kepler"
                                 for i in idx)


def test_periodogram_finds_period():
    t = np.arange(0, 27, 2 / 1440)
    curve = SectorCurve("s", t, 1 + 0.01 * np.sin(2 * np.pi * t / 3.0))
    period, power = periodogram([curve], 0.1, 30)
    assert period[np.argmax(power)] == pytest.approx(3.0, rel=0.01)


def test_plot_lightcurves_png(tmp_path):
    rng = np.random.default_rng(1)
    curves = []
    for k in range(3):
        t = np.arange(27 * k, 27 * (k + 1), 10 / 1440)
        curves.append(SectorCurve(f"Sector {k + 1} (SPOC)", t,
                                  1 + 0.001 * rng.standard_normal(t.size)))
    cfg = load_config(cwd=tmp_path)
    out = plot_lightcurves(curves, [("b", 3.0), ("far", 500.0)], cfg, tmp_path / "lc.png")
    assert out.read_bytes().startswith(PNG)


def test_lightcurve_timeout_note(tmp_path, monkeypatch):
    import time

    from star_report.plots import lightcurve

    monkeypatch.setattr(lightcurve, "fetch_lightcurves", lambda i, c: time.sleep(2))
    cfg = load_config(cwd=tmp_path)
    cfg.plots.lightcurve_timeout_s = 1.0
    start = time.monotonic()
    path, note = lightcurve.plot_stage(make_report(), tmp_path / "lc.png", cfg)
    assert time.monotonic() - start < 1.8
    assert path is None and note == "Light-curve download timed out after 1 s."


def test_lightcurve_fetch_error_propagates(tmp_path, monkeypatch):
    from star_report.plots import lightcurve

    def boom(i, c):
        raise RuntimeError("mast down")

    monkeypatch.setattr(lightcurve, "fetch_lightcurves", boom)
    with pytest.raises(RuntimeError, match="mast down"):
        lightcurve.plot_stage(make_report(), tmp_path / "lc.png", load_config(cwd=tmp_path))


def test_choose_skips_rows_without_sequence_or_exptime():
    rows = [_row("SPOC", 120, None), _row("SPOC", None, 3), _row("SPOC", 120, 4)]
    idx, _ = choose_products(rows, 10)
    assert idx == [2]


def test_search_rows_tolerates_masked_values():
    from astropy.table import Table

    from star_report.plots.lightcurve import _search_rows

    class FakeSearch:
        table = Table({"author": ["SPOC", "SPOC"], "exptime": [120.0, 120.0],
                       "sequence_number": [5, 6], "mission": ["a", "b"]}, masked=True)

    FakeSearch.table["sequence_number"].mask = [True, False]
    FakeSearch.table["exptime"][1] = np.nan
    rows = _search_rows(FakeSearch)
    assert rows[0]["sequence_number"] is None and rows[1]["exptime"] is None
    assert choose_products(rows, 10) == ([], None)


def test_merge_marks_and_plot_draws_one_line(tmp_path, monkeypatch):
    from star_report.plots.lightcurve import merge_marks

    marks = [("b", 9.98), ("TOI 1.01", 10.03), ("c", 16.1)]
    assert merge_marks(marks, 0.1, 30, 0.01) == [("b / TOI 1.01", 9.98), ("c", 16.1)]
    assert merge_marks([("far", 500.0)], 0.1, 30, 0.01) == []
    cfg = load_config(cwd=tmp_path)
    t = np.arange(0, 27, 10 / 1440)
    curve = SectorCurve("Sector 1 (SPOC)", t, np.ones_like(t))
    drawn = []
    real = plt.Axes.axvline
    monkeypatch.setattr(plt.Axes, "axvline",
                        lambda self, x=0, *a, **k: (drawn.append(k.get("label")),
                                                    real(self, x, *a, **k))[1])
    plot_lightcurves([curve], marks, cfg, tmp_path / "lc.png")
    assert drawn == ["b / TOI 1.01 (9.98 d)", "c (16.1 d)"]


# ---- sky ----

def _tan_wcs(ra=10.0, dec=20.0, n=300):
    from astropy.wcs import WCS

    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [n / 2, n / 2]
    w.wcs.cdelt = [-1 / 3600, 1 / 3600]
    return w


def test_select_neighbors_filters_delta_g_and_drops_target():
    from star_report.plots.sky import select_neighbors

    rows = [{"source_id": "1", "delta_g": 0.0}, {"source_id": "2", "delta_g": 2.5},
            {"source_id": "3", "delta_g": 6.0}, {"source_id": "4", "delta_g": 6.1},
            {"source_id": "5", "delta_g": -1.0}, "junk", {"source_id": "6"}]
    assert [r["source_id"] for r in select_neighbors(rows, 6)] == ["2", "3", "5"]


def test_plot_sky_with_image(tmp_path):
    import numpy as np

    from star_report.plots.sky import plot_sky

    cfg = load_config(cwd=tmp_path)
    img = np.random.default_rng(1).normal(100, 5, (300, 300))
    nb = [{"ra": 10.0 + 20 / 3600, "dec": 20.0 + 10 / 3600, "delta_g": 3.2}]
    out = plot_sky(img, _tan_wcs(), (10.0, 20.0), nb, cfg, "DSS2 red (STScI)", tmp_path / "s.png")
    assert out.read_bytes().startswith(PNG)


def test_plot_sky_without_image(tmp_path):
    from star_report.plots.sky import plot_sky

    cfg = load_config(cwd=tmp_path)
    nb = [{"ra": 10.0 + 20 / 3600, "dec": 20.0 + 10 / 3600, "delta_g": 3.2}]
    out = plot_sky(None, None, (10.0, 20.0), nb, cfg, "", tmp_path / "s.png")
    assert out.read_bytes().startswith(PNG)


def _fits_bytes():
    import io

    import numpy as np
    from astropy.io import fits

    buf = io.BytesIO()
    fits.PrimaryHDU(np.ones((20, 20)), header=_tan_wcs(n=20).to_header()).writeto(buf)
    return buf.getvalue()


class _Resp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, content):
        self.content, self.calls = content, []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        return _Resp(self.content)


def test_fetch_cutout_falls_back_to_stsci(tmp_path, monkeypatch):
    from star_report.plots import sky

    monkeypatch.setattr(sky, "is_reachable", lambda host: False)
    cfg = load_config(cwd=tmp_path)
    session = _Session(_fits_bytes())
    image, wcs, label = sky.fetch_cutout("10.0", 20.0, cfg, session)
    assert "STScI" in label and image.shape == (20, 20) and wcs.has_celestial
    url, kw = session.calls[0]
    assert url == "https://archive.stsci.edu/cgi-bin/dss_search"
    assert kw["params"]["v"] == cfg.plots.sky_fallback_survey and kw["params"]["r"] == 10.0
    assert kw["timeout"] == cfg.sources.timeout_s


def test_fetch_cutout_rejects_non_fits(tmp_path, monkeypatch):
    from star_report.plots import sky

    monkeypatch.setattr(sky, "is_reachable", lambda host: False)
    cfg = load_config(cwd=tmp_path)
    with pytest.raises(ValueError, match="not return a FITS"):
        sky.fetch_cutout(10.0, 20.0, cfg, _Session(b"<html>error</html>"))


def test_sky_plotter_draws_no_image_chart_when_cutout_fails(tmp_path, monkeypatch):
    from star_report.plots import sky

    def boom(*a, **k):
        raise ValueError("STScI down")

    monkeypatch.setattr(sky, "fetch_cutout", boom)
    cfg = load_config(cwd=tmp_path)
    path, note = plots._plot_sky(make_report(), tmp_path / "sky.png", cfg, None, None)
    assert path.read_bytes().startswith(PNG)
    assert "STScI down" in note


def test_select_neighbors_by_target_id():
    from star_report.plots.sky import select_neighbors

    rows = [{"source_id": "T", "delta_g": None}, {"source_id": "N", "delta_g": 0.0},
            {"source_id": "M", "delta_g": 2.0}]
    assert [r["source_id"] for r in select_neighbors(rows, 6, target_id="T")] == ["N", "M"]
    assert [r["source_id"] for r in select_neighbors(rows, 6)] == ["M"]


def test_sky_plotter_notes_missing_magnitudes(tmp_path, monkeypatch):
    from star_report.plots import sky

    monkeypatch.setattr(sky, "fetch_cutout", lambda *a: (_ for _ in ()).throw(ValueError("x")))
    rows = [{"source_id": "T", "ra": 1.0, "dec": 2.0, "delta_g": None},
            {"source_id": "N", "ra": 1.0, "dec": 2.0, "delta_g": None}]
    report = make_report()
    report.results["gaia_neighbors"] = SourceResult(source="gaia_neighbors", status="ok", data=rows)
    _, note = plots._plot_sky(report, tmp_path / "s.png", load_config(cwd=tmp_path), None, None)
    assert "Gaia neighbors have no G magnitudes; none shown." in note


def _fake_hips(monkeypatch, behavior):
    from astroquery.hips2fits import hips2fitsClass

    calls = []

    def query(self, **kw):
        calls.append((self.timeout, kw))
        return behavior()

    monkeypatch.setattr(hips2fitsClass, "query", query)
    return calls


def test_fetch_cutout_uses_hips2fits_when_reachable(tmp_path, monkeypatch):
    import io

    from astropy.io import fits

    from star_report.plots import sky

    monkeypatch.setattr(sky, "is_reachable", lambda host: True)
    calls = _fake_hips(monkeypatch, lambda: fits.open(io.BytesIO(_fits_bytes())))
    cfg = load_config(cwd=tmp_path)
    image, wcs, label = sky.fetch_cutout(10.0, 20.0, cfg, _Session(b""))
    assert label == "DSS2 red (CDS hips2fits)" and image.shape == (20, 20)
    timeout, kw = calls[0]
    assert timeout == cfg.sources.timeout_s
    assert kw["hips"] == cfg.plots.sky_survey and kw["projection"] == "TAN"
    assert kw["width"] == kw["height"] == 300
    assert kw["fov"].to("arcmin").value == cfg.plots.cutout_arcmin


def test_fetch_cutout_hips2fits_failure_falls_back(tmp_path, monkeypatch):
    from star_report.plots import sky

    monkeypatch.setattr(sky, "is_reachable", lambda host: True)

    def boom():
        raise ConnectionError("hips down")

    _fake_hips(monkeypatch, boom)
    session = _Session(_fits_bytes())
    _, _, label = sky.fetch_cutout(10.0, 20.0, load_config(cwd=tmp_path), session)
    assert "STScI" in label and len(session.calls) == 1


def test_fetch_cutout_both_fail_propagates_and_plotter_draws_fallback(tmp_path, monkeypatch):
    from star_report.plots import sky

    monkeypatch.setattr(sky, "is_reachable", lambda host: True)

    def boom():
        raise ConnectionError("hips down")

    _fake_hips(monkeypatch, boom)
    cfg = load_config(cwd=tmp_path)
    with pytest.raises(ValueError, match="not return a FITS"):
        sky.fetch_cutout(10.0, 20.0, cfg, _Session(b"<html>"))
    path, note = plots._plot_sky(make_report(), tmp_path / "s.png", cfg, None, _Session(b"<html>"))
    assert path.read_bytes().startswith(PNG) and "Sky image unavailable" in note
