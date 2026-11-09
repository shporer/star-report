from types import SimpleNamespace

from conftest import load_fixture, make_ctx, ref_identity

from star_report.sources.base import run_source
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
