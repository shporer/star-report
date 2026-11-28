"""Known QLP time-stamp errors: the model ported from AutoTTV, and where fetch_lightcurves applies it."""

from __future__ import annotations

import numpy as np
import pytest
from conftest import make_report

from star_report.config import ConfigError, load_config
from star_report.plots import lightcurve, qlp_time


def test_s1415_error_matches_autottv():
    """Reference values from AutoTTV's qlp_time_fix.error_s, for TIC 385180287 in Sector 14."""
    bjd = np.array([1683.4, 1696.8, 1710.2]) + qlp_time.BTJD0
    err = qlp_time.error_s("s1415", bjd, 289.8980, 22.1131)
    assert err == pytest.approx([-506.20, -477.82, -425.01], abs=0.05)


def test_s7479_error_matches_autottv():
    bjd = np.array([3480.0, 3505.0]) + qlp_time.BTJD0
    assert qlp_time.error_s("s7479", bjd, 100.0, -30.0) == pytest.approx([-136.88, -127.82], abs=0.05)


def test_corrected_time_removes_the_error():
    btjd = np.array([1690.0, 1700.0])
    fixed = qlp_time.corrected_time(btjd, "s1415", 330.79488, 18.88432)
    assert (fixed - btjd) * 86400 == pytest.approx([698.36, 696.70], abs=0.05)  # QLP was early


def test_unknown_model_is_an_error():
    with pytest.raises(ValueError, match="unknown QLP error model"):
        qlp_time.error_s("s9999", np.array([2459000.0]), 10.0, 20.0)


# ---- configuration ----

def test_default_corrections(tmp_path):
    assert load_config(cwd=tmp_path).plots.qlp_time_corrections == {14: "s1415", 15: "s1415", 80: "s7479"}


def test_a_qlp_time_correction_can_be_turned_off_per_sector(tmp_path):
    (tmp_path / "config.yaml").write_text("plots: {qlp_time_corrections: {80: null}}\n")
    assert load_config(cwd=tmp_path).plots.qlp_time_corrections == {14: "s1415", 15: "s1415", 80: None}


@pytest.mark.parametrize("override,key", [
    ("plots: {qlp_time_corrections: {14: s9999}}", "plots.qlp_time_corrections.14"),
    ("plots: {qlp_time_corrections: {0: s1415}}", r"plots.qlp_time_corrections.0.\[key\]"),
])
def test_bad_corrections_are_config_errors(tmp_path, override, key):
    (tmp_path / "config.yaml").write_text(override + "\n")
    with pytest.raises(ConfigError, match=f"invalid configuration: {key}: "):
        load_config(cwd=tmp_path)


# ---- where fetch_lightcurves applies it ----
def _fake_sectors(monkeypatch, products):
    """lightkurve.search_lightcurve returning one product per (author, sector, start BTJD)."""
    import lightkurve as lk
    from astropy.table import Table

    class Product:
        def __init__(self, author, sector, start):
            self.author, self.sector, self.start = author, sector, start

        def download(self, quality_bitmask="default"):
            lc = lk.LightCurve(time=self.start + np.arange(0, 20, 0.5), flux=np.ones(40))
            lc.meta.update(SECTOR=self.sector, AUTHOR=self.author)
            return lc

    class FakeSearch:
        table = Table({"author": [a for a, _, _ in products],
                       "exptime": [1800.0 if a == "QLP" else 120.0 for a, _, _ in products],
                       "sequence_number": [s for _, s, _ in products],
                       "mission": [f"TESS Sector {s}" for _, s, _ in products]})

        def __len__(self):
            return len(products)

        def __getitem__(self, i):
            return Product(*products[i])

    monkeypatch.setattr(lk, "search_lightcurve", lambda *a, **k: FakeSearch())


def test_qlp_time_stamps_are_corrected_only_in_listed_sectors(tmp_path, monkeypatch):
    from star_report.plots import qlp_time

    _fake_sectors(monkeypatch, [("QLP", 14, 1683.5), ("SPOC", 15, 1711.5), ("QLP", 54, 2770.0)])
    ident = make_report().identity
    curves, note = lightcurve.fetch_lightcurves(ident, load_config(cwd=tmp_path))
    by_label = {c.label: c for c in curves}
    assert set(by_label) == {"Sector 14 (QLP, time corrected)", "Sector 15 (SPOC)", "Sector 54 (QLP)"}
    raw = 1683.5 + np.arange(0, 20, 0.5)
    expected = qlp_time.corrected_time(raw, "s1415", ident.position.ra, ident.position.dec)
    assert by_label["Sector 14 (QLP, time corrected)"].time == pytest.approx(expected, abs=1e-9)
    assert by_label["Sector 15 (SPOC)"].time == pytest.approx(1711.5 + np.arange(0, 20, 0.5))
    assert by_label["Sector 54 (QLP)"].time == pytest.approx(2770.0 + np.arange(0, 20, 0.5))
    assert "QLP time stamps corrected" in note and "Sector 14 by +11.5 to +11.7 min" in note  # AutoTTV: 11.48 to 11.67 min


def test_a_sector_set_to_null_is_not_corrected(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("plots: {qlp_time_corrections: {14: null}}\n")
    _fake_sectors(monkeypatch, [("QLP", 14, 1683.5)])
    curves, note = lightcurve.fetch_lightcurves(make_report().identity, load_config(cwd=tmp_path))
    assert curves[0].label == "Sector 14 (QLP)" and note is None
    assert curves[0].time == pytest.approx(1683.5 + np.arange(0, 20, 0.5))
