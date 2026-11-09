import json

import numpy as np
import pytest
from astropy.table import Table
from astroquery import mast

from conftest import load_fixture, make_ctx

from star_report.sources import mast as mast_src
from star_report.sources.mast import MastObservations
from star_report.sources.registry import build_sources


def row(coll, prov=None, obs_id="x", exp=None, seq=None, target="T", product="timeseries",
        project=None):
    return {"obs_collection": coll, "provenance_name": prov, "project": project,
            "target_name": target, "obs_id": obs_id, "product": product, "t_exptime": exp,
            "sequence_number": seq}


def parse(rows, counts=(), ctx=None, raw_extra=None):
    ctx = ctx or make_ctx()
    return MastObservations().parse({"counts": list(counts), "rows": rows}, ctx)


def test_tess_classification():
    rows = [
        row("TESS", "SPOC", "tess2019198215352-s0016-0000000420814525-0152-s", 120, 16),
        row("TESS", "SPOC", "tess2019198215352-s0002-0000000420814525-0152-s", 120, 2),
        row("TESS", "SPOC", "tess2020-s0056-0000000420814525-0152-a_fast", 20, 56),
        row("TESS", "SPOC", "tess2022-s0082-0000000420814525-0152-a_fast", 20, 82),
        row("TESS", "SPOC", "tess-s0056-1-1", 600, 56, target="TESS FFI"),
        row("TESS", "SPOC", "tess-s0014-s0086-0000000420814525-s", 120, 14),
        row("TESS", "QLP", "x-s0010-s", 120, 10),
    ]
    t = parse(rows)["tess"]
    assert t == {"spoc_120s": [2, 16], "spoc_20s": [56, 82], "ffi": [56]}


def test_multi_sector_row_skipped():
    rows = [row("TESS", "SPOC", "tess-s0014-s0086-0000000001-0152-s", 120, 14)]
    assert parse(rows)["tess"] == {"spoc_120s": [], "spoc_20s": [], "ffi": []}


def test_tess_sectors_unique_sorted():
    rows = [row("TESS", "SPOC", f"a-s0009-{i}-s", 120, 9) for i in range(2)]
    rows.append(row("TESS", "SPOC", "a-s0003-1-s", 120, 3))
    assert parse(rows)["tess"]["spoc_120s"] == [3, 9]


def test_hlsp_grouped_by_provenance():
    rows = [row("HLSP", "QLP", "hlsp_qlp_s0016", seq=16), row("HLSP", "QLP", "hlsp_qlp_s0002", seq=2),
            row("HLSP", "QLP", "hlsp_qlp_s0002b", seq=2), row("HLSP", "TARS", "tars", seq=None)]
    out = parse(rows)["hlsp"]
    assert out == [{"provenance": "QLP", "n": 3, "sectors": [2, 16]},
                   {"provenance": "TARS", "n": 1, "sectors": []}]


def test_kepler_and_k2_split_by_cadence():
    rows = [row("Kepler", exp=1800.0), row("Kepler", exp=1800.0), row("Kepler", exp=60.0),
            row("K2", exp=1800.0), row("K2", exp=60.0), row("K2", exp=60.0)]
    r = parse(rows)
    assert r["kepler"] == {"n_long": 2, "n_short": 1}
    assert r["k2"] == {"n_long": 1, "n_short": 2}


def test_other_sorted_by_n_and_portal_url():
    ctx = make_ctx()
    counts = [{"collection": "JWST", "instrument": "NIRSpec", "product": "spectrum", "n": 3},
              {"collection": "HST", "instrument": "STIS", "product": "spectrum", "n": 30}]
    r = MastObservations().parse({"counts": counts, "rows": []}, ctx)
    assert [o["collection"] for o in r["other"]] == ["HST", "JWST"]
    assert r["portal_url"].startswith(
        "https://mast.stsci.edu/portal/Mashup/Clients/Mast/Portal.html?searchQuery=")
    ra, dec = mast_src._j2000(ctx)
    assert r["portal_url"].endswith(f"{ra:.5f}%20{dec:.5f}")


@pytest.mark.parametrize("raw", [None, [], "x", 3, {}, {"counts": 3, "rows": "a"},
                                 {"counts": [1, None], "rows": [1, "a", None]},
                                 {"counts": [{"n": "many"}], "rows": [
                                     {"obs_collection": "TESS", "provenance_name": "SPOC",
                                      "obs_id": 5, "sequence_number": "q", "t_exptime": "a"},
                                     {"obs_collection": "Kepler", "t_exptime": "bad"},
                                     {"obs_collection": "HLSP", "provenance_name": 3}]}])
def test_parse_malformed_does_not_raise(raw):
    r = MastObservations().parse(raw, make_ctx())
    assert set(r) >= {"tess", "hlsp", "kepler", "k2", "other", "portal_url"}


def test_parse_hd209458_fixture():
    r = MastObservations().parse(load_fixture("mast", "HD_209458"), make_ctx())
    tess = r["tess"]
    assert {56, 82} <= set(tess["spoc_120s"]) | set(tess["ffi"])
    assert any(o["collection"] == "HST" for o in r["other"])


def test_fixture_is_json_safe_and_slim():
    raw = load_fixture("mast", "HD_209458")
    json.dumps(raw, allow_nan=False)
    assert len(raw["rows"]) < 500
    assert all(c["collection"] not in ("TESS", "HLSP", "Kepler", "K2") for c in raw["counts"])


def test_aggregate_from_table():
    t = Table({"obs_collection": ["HST", "HST", "TESS", "JWST"],
               "instrument_name": ["STIS", "STIS", "Photometer", "NIRCAM"],
               "dataproduct_type": ["Spectrum", "spectrum", "timeseries", "image"],
               "provenance_name": ["CALSTIS", "CALSTIS", "SPOC", "CALJWST"],
               "project": ["HST", "HST", "TESS", "JWST"],
               "target_name": ["a", "a", "b", "c"], "obs_id": ["1", "2", "t-s", "4"],
               "t_exptime": np.ma.masked_array([1.0, 2.0, 120.0, 3.0], mask=[0, 0, 0, 1]),
               "sequence_number": np.ma.masked_array([0, 0, 5, 0], mask=[1, 1, 0, 1])})
    raw = mast_src.aggregate(t)
    assert {"collection": "HST", "instrument": "STIS", "product": "spectrum", "n": 2} in raw["counts"]
    assert len(raw["rows"]) == 1 and raw["rows"][0]["sequence_number"] == 5
    assert raw["rows"][0]["product"] == "timeseries"
    json.dumps(raw, allow_nan=False)


def test_fetch_uses_fresh_client_with_instance_timeout(monkeypatch):
    seen = {}

    class Fake:
        def __init__(self):
            seen["client"] = self

        def query_region(self, coord, radius):
            seen["args"] = (float(coord.ra.deg), float(coord.dec.deg), str(radius))
            return Table({"obs_collection": ["HST"], "instrument_name": ["STIS"],
                          "dataproduct_type": ["spectrum"]})

    monkeypatch.setattr(mast, "Observations", None, raising=False)
    monkeypatch.setattr(mast_src.mast, "ObservationsClass", Fake)
    before = mast.conf.timeout
    ctx = make_ctx()
    raw = MastObservations().fetch(ctx)
    assert seen["client"].TIMEOUT == ctx.config.sources.timeout_s
    assert mast.conf.timeout == before
    assert raw["counts"][0]["n"] == 1 and "3" in seen["args"][2]


def test_empty_table_fetch(monkeypatch):
    class Fake:
        def query_region(self, coord, radius):
            return Table()

    monkeypatch.setattr(mast_src.mast, "ObservationsClass", Fake)
    assert MastObservations().fetch(make_ctx()) == {"counts": [], "rows": []}


def test_registered_after_wide_binaries():
    from star_report.config import load_config
    names = [s.name for s in build_sources(load_config())]
    assert names[-2:] == ["wide_binaries", "mast"]
