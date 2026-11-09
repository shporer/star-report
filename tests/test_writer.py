import json

import pytest
from conftest import make_report

from star_report import writer
from star_report.models import Paper, SourceResult
from star_report.writer import (
    WRITER_SCHEMA,
    WriterOutput,
    build_fact_sheet,
    build_user_message,
    check_citations,
    find_citations,
)

A = "2008A&A...481..529X"
B = "2000ApJ...529L..45C"


def _papers():
    return [
        Paper(bibcode=A, title="A hot Jupiter transit", year=2008, authors=["Smith, J.", "Doe, A."],
              citation_count=12, abstract="We observe a transit."),
        Paper(bibcode=B, title="Atmosphere of HD 209458b", year=2000, authors=["Charbonneau, D."],
              citation_count=900, abstract=None),
    ]


def test_fact_sheet_basics():
    report = make_report()
    facts = build_fact_sheet(report)
    assert set(facts) == {"name", "identifiers", "position", "description", "parameters", "flags",
                          "warnings", "planets", "tois", "kois", "inventory", "literature"}
    assert "results" not in facts
    assert facts["name"] == "HD 209458"
    teff = next(p for p in facts["parameters"] if p["quantity"] == "Teff")
    assert teff["values"]["gaia"] == "6020 ± 15 (GSP-Spec)"
    assert teff["values"]["tic"] == "6052 ± 101"
    assert set(facts["inventory"]) == {"tess_observed", "tess_upcoming", "missions", "spectra",
                                       "exofop_followup"}
    assert set(facts["literature"]) == {"ads_records_found", "papers_provided", "note"}
    assert facts["tois"] == ["5972.01 (KP)"]
    assert facts["inventory"]["tess_observed"] == [56, 82]
    assert len(json.dumps(facts)) < 20_000
    assert build_fact_sheet(report) == facts
    assert "None" not in json.dumps(facts)


def test_fact_sheet_kois_read_the_parsed_keys():
    report = make_report("Kepler-10")
    assert build_fact_sheet(report)["kois"] == ["Kepler-10 b (CONFIRMED)", "Kepler-10 c (CONFIRMED)"]
    arch = report.results["exoplanet_archive"]
    kois = [{"koi": "K00072.03", "name": None, "disposition": "CANDIDATE"},
            {"koi": "K00072.04", "name": None, "disposition": None},
            {"kepoi_name": "K00072.05", "disposition": "CANDIDATE"}]  # not a key parse emits
    arch = arch.model_copy(update={"data": {**arch.data, "kois": kois}})
    report = report.model_copy(update={"results": {**report.results, "exoplanet_archive": arch}})
    assert build_fact_sheet(report)["kois"] == ["K00072.03 (CANDIDATE)", "K00072.04"]


def test_fact_sheet_ads_and_malformed():
    report = make_report()
    ads = SourceResult(source="ads", status="ok",
                       data={"num_found": 40, "papers": [p.model_dump() for p in _papers()]})
    facts = build_fact_sheet(report.model_copy(update={"results": {**report.results, "ads": ads}}))
    assert facts["literature"]["ads_records_found"] == 40
    assert facts["literature"]["papers_provided"] == 2
    bad = {k: v.model_copy(update={"data": "junk"}) for k, v in report.results.items()}
    build_fact_sheet(report.model_copy(update={"results": bad}))


def test_user_message():
    msg = build_user_message({"name": "X"}, _papers(), None)
    assert "<fact_sheet>" in msg and "<papers>" in msg
    for p in _papers():
        assert p.bibcode in msg and p.title in msg
    assert f"[1] {A} | 2008 | Smith, J. | 12 citations" in msg
    assert "<correction>" not in msg
    msg = build_user_message({"name": "X"}, [], "ADS key missing")
    assert "No papers were provided: ADS key missing" in msg
    assert "<correction>" in build_user_message({}, [], "n", correction="fix it")


def test_find_and_check_citations():
    assert find_citations(f"x [{A}] and [{B}].") == [A, B]
    out = WriterOutput(overview=f"o [{A}]", literature_summary="s", cited_bibcodes=[B])
    assert check_citations(out, {A, B}) == []
    assert check_citations(out, {B}) == [A]
    out = WriterOutput(overview="o", literature_summary=f"s [{A}] [{A}]", cited_bibcodes=["2001ApJ...529L..45Z"])
    assert check_citations(out, {B}) == sorted([A, "2001ApJ...529L..45Z"])


def _schema_accepts(obj: dict) -> bool:
    """Minimal check of WRITER_SCHEMA (required, additionalProperties, types)."""
    props = WRITER_SCHEMA["properties"]
    if not set(WRITER_SCHEMA["required"]) <= set(obj):
        return False
    if WRITER_SCHEMA["additionalProperties"] is False and set(obj) - set(props):
        return False
    return (isinstance(obj["overview"], str) and isinstance(obj["literature_summary"], str)
            and isinstance(obj["cited_bibcodes"], list)
            and all(isinstance(x, str) for x in obj["cited_bibcodes"]))


def test_schema_and_prompt():
    good = {"overview": "a", "literature_summary": "b", "cited_bibcodes": []}
    assert WRITER_SCHEMA["type"] == "object"
    assert props_types() == {"overview": "string", "literature_summary": "string",
                             "cited_bibcodes": "array"}
    assert WRITER_SCHEMA["properties"]["cited_bibcodes"]["items"] == {"type": "string"}
    assert _schema_accepts(good)
    assert not _schema_accepts({**good, "extra": 1})
    assert not _schema_accepts({"overview": "a"})
    assert WriterOutput(**good).cited_bibcodes == []
    with pytest.raises(Exception):
        WriterOutput(**good, extra=1)
    assert writer.PROMPT_VERSION == 1
    assert writer.SYSTEM_PROMPT.startswith("You write short, factual summaries")


def props_types():
    return {k: v["type"] for k, v in WRITER_SCHEMA["properties"].items()}


# ---- Claude call, retry, failures, caching ----

from contextlib import nullcontext  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import anthropic  # noqa: E402
import httpx2  # noqa: E402

from star_report.cache import Cache  # noqa: E402
from star_report.config import load_config  # noqa: E402
from star_report.writer import write_sections  # noqa: E402

EXPIRY = load_config().cache.expiry_days


class FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return nullcontext(SimpleNamespace(get_final_message=lambda: r))


def reply(text, stop="end_turn", model="claude-opus-5-5"):
    return SimpleNamespace(stop_reason=stop, model=model, content=[SimpleNamespace(type="text", text=text)])


def _out(cite=A, overview="A star."):
    return json.dumps({"overview": overview, "literature_summary": f"It transits [{cite}].",
                       "cited_bibcodes": [cite]})


good_json = _out()


def report_with_papers():
    report = make_report()
    ads = SourceResult(source="ads", status="ok",
                       data={"num_found": 2, "papers": [p.model_dump() for p in _papers()]})
    return report.model_copy(update={"results": {**report.results, "ads": ads}})


def _run(tmp_path, replies, report=None, cfg=None):
    fake = FakeClient(replies)
    cfg = cfg or load_config()
    block = write_sections(report or report_with_papers(), cfg, Cache(tmp_path, EXPIRY),
                           client_factory=lambda: fake)
    return block, fake


def test_request_shape(tmp_path):
    block, fake = _run(tmp_path, [reply(good_json)])
    kw = fake.calls[0]
    assert block.status == "ok" and kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["output_config"]["effort"] == "high"
    assert kw["output_config"]["format"]["schema"] == WRITER_SCHEMA
    assert "thinking" not in kw and "temperature" not in kw
    assert block.bibcodes_sent == [A, B] and block.effort == "high" and block.generated is not None
    assert block.cited_bibcodes == [A] and block.from_cache is False


@pytest.mark.parametrize("timeout_s,max_retries", [(None, None), (12.5, 0)])
def test_default_client_gets_configured_timeout_and_retries(tmp_path, monkeypatch, timeout_s,
                                                            max_retries):
    made = []

    class FakeAnthropic(FakeClient):
        def __init__(self, **kwargs):
            made.append(kwargs)
            super().__init__([reply(good_json)])

    monkeypatch.setattr(writer.anthropic, "Anthropic", FakeAnthropic)
    cfg = load_config(cwd=tmp_path)
    if timeout_s is not None:
        cfg.llm.timeout_s, cfg.llm.max_retries = timeout_s, max_retries
    block = write_sections(report_with_papers(), cfg, Cache(tmp_path / "cache", EXPIRY))
    assert block.status == "ok"
    assert made == [{"timeout": cfg.llm.timeout_s, "max_retries": cfg.llm.max_retries}]
    if timeout_s is None:
        assert made == [{"timeout": 300, "max_retries": 1}]


def test_no_server_fallback(tmp_path):
    cfg = load_config()
    cfg.llm.server_fallback = False
    block, fake = _run(tmp_path, [reply(good_json)], cfg=cfg)
    assert block.status == "ok" and "betas" not in fake.calls[0] and "fallbacks" not in fake.calls[0]


def test_model_comes_from_response(tmp_path):
    block, _ = _run(tmp_path, [reply(good_json, model="claude-fallback-x")])
    assert block.model == "claude-fallback-x"


def test_bad_citation_retry_succeeds(tmp_path):
    block, fake = _run(tmp_path, [reply(_out("1999XXX...1....1Z")), reply(good_json)])
    assert block.status == "ok" and len(fake.calls) == 2
    second = fake.calls[1]["messages"][0]["content"]
    assert "1999XXX...1....1Z" in second and "Cite only bibcodes from the list" in second
    assert "<correction>" not in fake.calls[0]["messages"][0]["content"]


def test_bad_citation_twice_fails(tmp_path):
    block, fake = _run(tmp_path, [reply(_out("1999XXX...1....1Z"))] * 2)
    assert block.status == "failed" and block.reason == "LLM summary failed citation check"
    assert len(fake.calls) == 2


def test_refusal_no_retry(tmp_path):
    block, fake = _run(tmp_path, [reply("", stop="refusal")])
    assert block.status == "failed" and len(fake.calls) == 1
    assert block.reason == "Claude declined to write this summary (refusal)."


def test_max_tokens(tmp_path):
    block, fake = _run(tmp_path, [reply('{"overview": "cut', stop="max_tokens")])
    assert block.reason == "Claude's output was cut off (max_tokens)." and len(fake.calls) == 1


@pytest.mark.parametrize("text", ["not json", '{"overview": "x"}'])
def test_invalid_output(tmp_path, text):
    block, fake = _run(tmp_path, [reply(text)])
    assert block.status == "failed" and len(fake.calls) == 1
    assert block.reason == "Claude's output did not match the expected format."


def test_no_text_block(tmp_path):
    r = SimpleNamespace(stop_reason="end_turn", model="m", content=[SimpleNamespace(type="thinking")])
    block, _ = _run(tmp_path, [r])
    assert block.reason == "Claude's output did not match the expected format."


def test_credentials_type_error(tmp_path):
    block, fake = _run(tmp_path, [TypeError("Could not resolve authentication method. Expected ...")])
    assert block.status == "failed" and len(fake.calls) == 1
    assert block.reason == "No Anthropic credentials found (set ANTHROPIC_API_KEY or run `ant auth login`)."


def test_other_type_error_becomes_failed_block(tmp_path):
    block, fake = _run(tmp_path, [TypeError("something else")])
    assert block.status == "failed" and len(fake.calls) == 1
    assert block.reason.startswith("LLM writer error: TypeError")


def test_credentials_error(tmp_path):
    block, _ = _run(tmp_path, [anthropic.CredentialsError("no profile")])
    assert block.reason.startswith("No Anthropic credentials found")


def test_credentials_error_from_factory(tmp_path):
    def factory():
        raise anthropic.CredentialsError("no profile")
    block = write_sections(report_with_papers(), load_config(), Cache(tmp_path, EXPIRY), client_factory=factory)
    assert block.reason.startswith("No Anthropic credentials found")


def test_api_connection_error(tmp_path):
    err = anthropic.APIConnectionError(message="down", request=httpx2.Request("POST", "https://api.anthropic.com"))
    block, _ = _run(tmp_path, [err])
    assert block.status == "failed" and block.reason.startswith("Claude API error: APIConnectionError: ")
    assert len(block.reason) <= 200


def test_authentication_error(tmp_path):
    req = httpx2.Request("POST", "https://api.anthropic.com")
    err = anthropic.AuthenticationError("bad", response=httpx2.Response(401, request=req), body=None)
    block, _ = _run(tmp_path, [err])
    assert block.reason == "Anthropic API rejected the credentials."


def test_cache_hit_and_failures_not_cached(tmp_path):
    fake = FakeClient([TypeError("Could not resolve authentication method"), reply(good_json)])
    cache = Cache(tmp_path, EXPIRY)
    report, cfg = report_with_papers(), load_config()
    first = write_sections(report, cfg, cache, client_factory=lambda: fake)
    assert first.status == "failed"
    second = write_sections(report, cfg, cache, client_factory=lambda: fake)
    assert second.status == "ok" and second.from_cache is False and len(fake.calls) == 2
    third = write_sections(report, cfg, cache, client_factory=lambda: fake)
    assert third.status == "ok" and third.from_cache is True and len(fake.calls) == 2
    assert third.overview == second.overview and third.model == "claude-opus-5-5"


def test_cache_key_changes_with_effort(tmp_path):
    fake = FakeClient([reply(good_json), reply(good_json)])
    cache, report = Cache(tmp_path, EXPIRY), report_with_papers()
    cfg = load_config()
    write_sections(report, cfg, cache, client_factory=lambda: fake)
    cfg.llm.effort = "low"
    block = write_sections(report, cfg, cache, client_factory=lambda: fake)
    assert block.from_cache is False and len(fake.calls) == 2


@pytest.mark.parametrize("status,note", [
    ("skipped", "ADS_DEV_KEY not set; the literature was not searched."),
    ("error", "ADS search failed."), ("timeout", "ADS search failed."),
    ("empty", "ADS found no papers for this object."),
])
def test_literature_note(tmp_path, status, note):
    report = make_report()
    ads = SourceResult(source="ads", status=status, error="x" if status != "empty" else None)
    report = report.model_copy(update={"results": {**report.results, "ads": ads}})
    block, fake = _run(tmp_path, [reply(json.dumps({"overview": "o", "literature_summary": "none",
                                                    "cited_bibcodes": []}))], report=report)
    assert block.status == "ok" and block.bibcodes_sent == []
    assert note in fake.calls[0]["messages"][0]["content"]


def test_setup_errors_become_failed_block(tmp_path, monkeypatch):
    def boom(report):
        raise RuntimeError("bad facts")
    monkeypatch.setattr(writer, "build_fact_sheet", boom)
    block, fake = _run(tmp_path, [])
    assert block.status == "failed" and block.reason.startswith("LLM writer error:") and fake.calls == []


def test_text_blocks_are_joined_across_fallback(tmp_path):
    half = json.loads(good_json)
    full = json.dumps(half)
    cut = full.index("star") + 2
    r = SimpleNamespace(stop_reason="end_turn", model="claude-fallback-x", content=[
        SimpleNamespace(type="text", text=full[:cut]), SimpleNamespace(type="fallback"),
        SimpleNamespace(type="text", text=full[cut:])])
    block, _ = _run(tmp_path, [r])
    assert block.status == "ok" and block.overview == "A star."
