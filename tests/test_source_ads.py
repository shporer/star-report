"""ADS source: query string, paper selection, rate limits, secrets, literature section."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from conftest import FIXTURES_DIR, make_ctx, make_report

from star_report.models import Paper, Provenance, SourceResult
from star_report.render.html import render_html
from star_report.sources.ads import Ads, query_string, select_papers
from star_report.sources.base import run_source
from star_report.sources.registry import build_sources

TOKEN = "tok-SECRET-0123456789"


def _raw():
    return json.loads((FIXTURES_DIR / "ads" / "synthetic.json").read_text())


def _papers():
    return [Paper(bibcode=d["bibcode"], title=d["title"][0], year=int(d["year"]),
                  pubdate=d["pubdate"], citation_count=d["citation_count"])
            for d in _raw()["response"]["docs"]]


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


TRANSLATED = '(=abs:"HD 209458" OR simbid:"7" OR nedid:"0") database:astronomy'


class FakeSession:
    def __init__(self, response, objects=None):
        self.response = response
        self.objects = objects if objects is not None else FakeResponse(200, {"query": TRANSLATED})
        self.calls = []
        self.posts = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if isinstance(self.objects, Exception):
            raise self.objects
        return self.objects


def _ctx(tmp_path, response=None, token=TOKEN, objects=None):
    ctx = make_ctx(cache_dir=tmp_path / "cache")
    return replace(ctx, ads_token=token, session=FakeSession(response, objects))


def test_query_string():
    assert query_string('=abs:"HD 209458"', ["article", "eprint"]) == \
        '(=abs:"HD 209458") doctype:(article OR eprint)'


def test_select_papers_order_and_count():
    papers = _papers()
    chosen = select_papers(papers, 15, 10)
    assert len(chosen) == 25 and len({p.bibcode for p in chosen}) == 25
    cited = sorted(papers, key=lambda p: (-p.citation_count, p.bibcode))[:15]
    assert chosen[:15] == cited
    rest = chosen[15:]
    assert [p.pubdate for p in rest] == sorted((p.pubdate for p in rest), reverse=True)
    assert min(p.pubdate for p in rest) >= sorted(p.pubdate for p in papers)[-10]


def test_select_papers_overlap_dedupes():
    papers = [Paper(bibcode=f"B{i}", title="t", pubdate=f"20{i:02d}-01-00", citation_count=i)
              for i in range(10)]
    chosen = select_papers(papers, 4, 4)  # cited: B9..B6; recent: the same four
    assert [p.bibcode for p in chosen] == ["B9", "B8", "B7", "B6"]
    chosen = select_papers(papers, 3, 5)  # recent B9..B5 -> adds B6, B5
    assert [p.bibcode for p in chosen] == ["B9", "B8", "B7", "B6", "B5"]


def test_select_papers_ties_break_by_bibcode():
    papers = [Paper(bibcode=b, title="t", pubdate="2020-01-00", citation_count=5) for b in "CAB"]
    assert [p.bibcode for p in select_papers(papers, 2, 0)] == ["A", "B"]
    assert [p.bibcode for p in select_papers(papers, 0, 2)] == ["A", "B"]


def test_registered_after_lamost():
    names = [s.name for s in build_sources(make_ctx().config)]
    assert names.index("lamost") < names.index("ads")


def test_skipped_without_token():
    ctx = replace(make_ctx(), ads_token=None)
    assert Ads().skip_reason(ctx) == "ADS_DEV_KEY not set"
    assert Ads().skip_reason(replace(ctx, ads_token=TOKEN)) is None


def test_query_has_no_token(tmp_path):
    ctx = _ctx(tmp_path)
    q = Ads().query(ctx)
    assert set(q) == {"name", "doctypes", "rows"} and TOKEN not in json.dumps(q)
    assert q["name"] == "HD 209458" and q["doctypes"] == ["article", "eprint"]


def test_fetch_translates_then_searches(tmp_path):
    ctx = _ctx(tmp_path, FakeResponse(payload=_raw()))
    payload = Ads().fetch(ctx)
    url, kw = ctx.session.posts[0]
    assert url == "https://api.adsabs.harvard.edu/v1/objects/query"
    assert kw["json"] == {"query": ['object:"HD 209458"']}
    assert kw["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert kw["headers"]["Content-Type"] == "application/json"
    url, kw = ctx.session.calls[0]
    assert url == "https://api.adsabs.harvard.edu/v1/search/query"
    assert kw["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert kw["params"]["q"] == f"({TRANSLATED}) doctype:(article OR eprint)"
    assert kw["params"]["fl"] == "bibcode,title,year,pubdate,author,citation_count,abstract,doctype"
    assert kw["params"]["sort"] == "citation_count desc"
    assert kw["params"]["rows"] == ctx.config.literature.max_records
    assert payload["q"] == kw["params"]["q"] and TOKEN not in json.dumps(payload)


@pytest.mark.parametrize("objects", [
    FakeResponse(500), FakeResponse(200, {}), FakeResponse(200, {"query": "  "}),
    FakeResponse(200, ["x"]), ConnectionError("down")])
def test_translation_failure_falls_back(tmp_path, objects):
    ctx = _ctx(tmp_path, FakeResponse(payload=_raw()), objects=objects)
    Ads().fetch(ctx)
    assert ctx.session.calls[0][1]["params"]["q"] == \
        '(=abs:"HD 209458") doctype:(article OR eprint)'


def test_translation_rate_limited(tmp_path):
    objects = FakeResponse(429, headers={"X-RateLimit-Reset": "1788998400"})
    result = run_source(Ads(), _ctx(tmp_path, FakeResponse(payload=_raw()), objects=objects))
    assert result.status == "error" and "rate limit" in result.error
    assert TOKEN not in json.dumps(result.model_dump(mode="json"))


def test_parse_synthetic(tmp_path):
    data = Ads().parse(_raw(), make_ctx())
    assert data["num_found"] == 412 and len(data["papers"]) == 25
    first = data["papers"][0]
    assert first["citation_count"] == 2400 and isinstance(first["title"], str)
    assert len(first["authors"]) == 3 and first["year"] == 2001
    json.dumps(data)


@pytest.mark.parametrize("raw", [None, [], "x", {}, {"response": []}, {"response": {"docs": 3}},
                                 {"response": {"docs": []}}, {"response": {"docs": ["a", 1, None]}}])
def test_parse_malformed_is_empty(raw):
    assert Ads().parse(raw, make_ctx()) == {}


def test_parse_tolerates_bad_fields():
    raw = {"response": {"numFound": "many", "docs": [
        {"bibcode": "2020X", "title": "plain", "year": "n/a", "citation_count": "9",
         "author": "Smith", "pubdate": 5, "abstract": 3, "doctype": None},
        {"title": ["no bibcode"]}]}}
    data = Ads().parse(raw, make_ctx())
    assert data["num_found"] == 1 and len(data["papers"]) == 1
    p = data["papers"][0]
    assert p["title"] == "plain" and p["year"] is None and p["authors"] == []
    assert p["citation_count"] == 9


@pytest.mark.parametrize("response", [
    FakeResponse(429, headers={"X-RateLimit-Reset": "1788998400"}),
    FakeResponse(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1788998400"}),
    FakeResponse(200, {"response": {"numFound": 0, "docs": []}},
                 {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1788998400"}),
])
def test_rate_limit_is_error(tmp_path, response):
    result = run_source(Ads(), _ctx(tmp_path, response))
    assert result.status == "error"
    assert "rate limit" in result.error and "resets at 2026-" in result.error
    assert TOKEN not in json.dumps(result.model_dump(mode="json"))


def test_rate_limit_without_reset_header(tmp_path):
    result = run_source(Ads(), _ctx(tmp_path, FakeResponse(429)))
    assert "rate limit" in result.error and "unknown" in result.error


def test_last_allowed_request_still_ok(tmp_path):
    response = FakeResponse(200, _raw(), {"X-RateLimit-Remaining": "0"})
    assert run_source(Ads(), _ctx(tmp_path, response)).status == "ok"


def test_token_never_in_result(tmp_path):
    result = run_source(Ads(), _ctx(tmp_path, FakeResponse(payload=_raw())))
    assert result.status == "ok"
    assert TOKEN not in json.dumps(result.model_dump(mode="json"))
    err = run_source(Ads(), _ctx(tmp_path / "e", FakeResponse(500)))
    assert err.status == "error"
    assert TOKEN not in json.dumps(err.model_dump(mode="json"))


def _report_with(ads_result):
    base = make_report()
    return base.model_copy(update={"results": {**base.results, "ads": ads_result}})


def test_literature_lists_papers_with_links(tmp_path):
    data = Ads().parse(_raw(), make_ctx())
    res = SourceResult(source="ads", status="ok", data=data,
                       provenance=Provenance(service="NASA ADS search API", query="{}",
                                             retrieved=make_report().generated, from_cache=False))
    html = render_html(_report_with(res), tmp_path)
    assert "ADS found 412 records; showing 25." in html
    for p in data["papers"]:
        assert f"https://ui.adsabs.harvard.edu/abs/{p['bibcode']}/abstract" in html


def test_literature_escapes_titles(tmp_path):
    data = {"num_found": 1, "papers": [{"bibcode": "2020X", "title": "<script>alert(1)</script>",
                                        "authors": ["<b>x</b>"], "citation_count": 1}]}
    html = render_html(_report_with(SourceResult(source="ads", status="ok", data=data)), tmp_path)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_literature_skipped_text(tmp_path):
    res = SourceResult(source="ads", status="skipped", error="ADS_DEV_KEY not set")
    assert "ADS_DEV_KEY not set; the literature was not searched." in render_html(
        _report_with(res), tmp_path)


def test_literature_error_and_empty(tmp_path):
    err = SourceResult(source="ads", status="error", error="boom")
    assert "unavailable" in render_html(_report_with(err), tmp_path)
    empty = SourceResult(source="ads", status="empty")
    assert "ADS found no records." in render_html(_report_with(empty), tmp_path)


def test_no_literature_section_without_ads_result(tmp_path):
    assert 'id="literature"' not in render_html(make_report(), tmp_path)
