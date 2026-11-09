from types import SimpleNamespace

import pytest

from conftest import load_fixture, make_ctx, ref_identity

from star_report.sources.base import run_source, run_sources
from star_report.sources.exofop import ExoFop


def test_hd209458(ctx):
    d = ExoFop().parse(load_fixture("exofop", "HD_209458"), ctx)
    assert [(t["toi"], t["disp_tfop"], t["sectors"]) for t in d["tois"]] == [("5972.01", "KP", [56, 82])]
    assert d["followup"]["imaging"] >= 3
    assert d["url"] == "https://exofop.ipac.caltech.edu/tess/target.php?id=420814525"


def test_toi700():
    ctx = make_ctx(identity=ref_identity("TOI-700"))
    d = ExoFop().parse(load_fixture("exofop", "TOI-700"), ctx)
    assert {"700.01", "700.02", "700.03", "700.04"} <= {t["toi"] for t in d["tois"]}
    assert d["followup"]["time_series"] >= 16
    assert d["followup"]["spectroscopy"] >= 3
    assert all(isinstance(n, int) for n in d["files"].values())


def test_missing_keys(ctx):
    assert ExoFop().parse({"basic_info": {}}, ctx) == {}


def test_html_response_is_error(ctx):
    class Html:
        headers = {"Content-Type": "text/html; charset=utf-8"}
        text = "<html></html>"

        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("not json")

    ctx.session = SimpleNamespace(get=lambda url, timeout=None: Html())
    result = run_source(ExoFop(), ctx)
    assert result.status == "error"
    assert "non-JSON" in result.error


_GOOD_TOI = {"toi": "TOI 1.01", "disp_tfop": "PC", "sector": "1,2"}


@pytest.mark.parametrize("raw, expected_tois, expected_followup", [
    ({"tois": {}}, 0, 0),
    ({"tois": [None, _GOOD_TOI]}, 1, 0),
    ({"files": [{}]}, 0, 0),
    ({"files": [{"ftype": ["x"]}], "tois": [_GOOD_TOI]}, 1, 0),
    ({"imaging": 5}, 0, 0),
    ({"imaging": "abc"}, 0, 0),
    ({"imaging": [{}, {}]}, 0, 2),
    ([], 0, 0),
    ("text", 0, 0),
])
def test_wrong_shapes_are_ignored(ctx, raw, expected_tois, expected_followup):
    d = ExoFop().parse(raw, ctx)
    if not (expected_tois or expected_followup):
        assert d == {}
    else:
        assert len(d["tois"]) == expected_tois
        assert d["followup"]["imaging"] == expected_followup
        assert d["files"] == {}


def test_malformed_tic_is_skipped(ctx):
    ctx.identity = ctx.identity.model_copy(update={"tic_id": "abc"})
    ctx.session = SimpleNamespace(get=lambda *a, **k: pytest.fail("no request expected"))
    result = run_sources([ExoFop()], ctx)["exofop"]
    assert result.status == "skipped"
