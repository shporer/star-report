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
    assert find_citations(f"x [{A}, {B}] and [{B}; {A}] [ {A} ] [not {B}]") == [A, B, B, A, A]
    grouped = WriterOutput(overview=f"o [{A}, 2001ApJ...529L..45Z]", literature_summary="s",
                           cited_bibcodes=[A])
    assert check_citations(grouped, {A}) == ["2001ApJ...529L..45Z"]
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
    assert writer.PROMPT_VERSION == 4
    assert writer.SYSTEM_PROMPT.startswith("You write short, factual summaries")


def test_prompt_sets_plain_text_and_length_limits():
    """The first live output ran long and used Markdown headings, which the HTML shows as-is."""
    prompt = writer.SYSTEM_PROMPT
    assert "no Markdown" in prompt
    assert "3 to 5 sentences (never more than 5), about 100 words" in prompt
    assert "1 to 3 paragraphs" in prompt and "about 250 words" in prompt
    assert "Quote catalog codes and classes as given" in prompt
    assert "Name instruments, telescopes, surveys and catalogs as the material does" in prompt


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


# ---- Claude Code backend (`claude -p` on the user's Claude Code login) ----

import os  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

from conftest import load_fixture  # noqa: E402

from star_report.writer import (  # noqa: E402
    SYSTEM_PROMPT,
    WriterFailure,
    claude_code_env,
    parse_claude_code_output,
)

GOOD = json.loads(good_json)
NOT_LOGGED_IN = "Claude Code is not logged in (run `claude auth login`)."
FORMAT_FAILURE = "Claude's output did not match the expected format."


def cc_result(structured=None, *, result="", is_error=False, subtype="success", models=None,
              stop="end_turn"):
    """A `claude -p --output-format json` result, in the shape of tests/fixtures/claude_code."""
    usage = {"claude-opus-5-5": 50} if models is None else models
    data = {"type": "result", "subtype": subtype, "is_error": is_error, "result": result,
            "stop_reason": stop, "num_turns": 1, "session_id": "s", "total_cost_usd": 0.0,
            "modelUsage": {m: {"inputTokens": 10, "outputTokens": n} for m, n in usage.items()}}
    if structured is not None:
        data["structured_output"] = structured
    return json.dumps(data)


def _fails(rc, stdout, stderr=""):
    with pytest.raises(WriterFailure) as e:
        parse_claude_code_output(rc, stdout, stderr, default_model="m")
    return e.value.reason


def test_cc_parse_structured_output():
    reply = parse_claude_code_output(0, cc_result(GOOD), "", default_model="m")
    assert WriterOutput.model_validate_json(reply.text) == WriterOutput(**GOOD)
    assert (reply.model, reply.stop_reason) == ("claude-opus-5-5", "end_turn")


def test_cc_parse_falls_back_to_result_text():
    reply = parse_claude_code_output(0, cc_result(result=good_json), "", default_model="m")
    assert reply.text == good_json


def test_cc_parse_model_is_the_one_that_wrote_most():
    out = cc_result(GOOD, models={"claude-haiku-5-5": 20, "claude-opus-5-5": 900})
    assert parse_claude_code_output(0, out, "", default_model="m").model == "claude-opus-5-5"
    assert parse_claude_code_output(0, cc_result(GOOD, models={}), "", default_model="m").model == "m"


def test_cc_parse_skips_noise_before_the_json():
    reply = parse_claude_code_output(0, "warning: x\n" + cc_result(GOOD) + "\n", "", default_model="m")
    assert WriterOutput.model_validate_json(reply.text).overview == "A star."


def test_cc_parse_recorded_success():
    raw = json.dumps(load_fixture("claude_code", "success"))
    reply = parse_claude_code_output(0, raw, "", default_model="m")
    out = WriterOutput.model_validate_json(reply.text)
    assert (reply.model, reply.stop_reason) == ("claude-opus-5-5", "tool_use")
    assert out.overview.startswith("HD 209458") and out.cited_bibcodes == []


def test_cc_parse_recorded_not_logged_in():
    assert _fails(1, json.dumps(load_fixture("claude_code", "not_logged_in"))) == NOT_LOGGED_IN


@pytest.mark.parametrize("message", ["Not logged in · Please run /login",
                                     "Invalid API key · Please run /login"])
def test_cc_parse_login_messages(message):
    assert _fails(1, cc_result(result=message, is_error=True)) == NOT_LOGGED_IN


def test_cc_parse_other_error_is_quoted_and_capped():
    reason = _fails(1, cc_result(result="API Error: 529 " + "overloaded " * 40, is_error=True))
    assert reason.startswith("Claude Code error: API Error: 529 overloaded") and len(reason) <= 200


def test_cc_parse_error_subtype_without_flag():
    assert _fails(1, cc_result(subtype="error_during_execution")) == \
        "Claude Code error: error_during_execution"


def test_cc_parse_structured_output_retries_exhausted():
    out = cc_result(subtype="error_max_structured_output_retries", is_error=True)
    assert _fails(1, out) == FORMAT_FAILURE


@pytest.mark.parametrize("rc,stdout,stderr,reason", [
    (1, "", "warning: x\nerror: unknown option '--json-schema'\n",
     "Claude Code failed (exit 1): error: unknown option '--json-schema'"),
    (137, "", "", "Claude Code failed (exit 137)."),
    (0, "", "", "Claude Code's reply could not be read."),
    (0, "not json", "", "Claude Code's reply could not be read."),
    (0, "[1, 2]", "", "Claude Code's reply could not be read."),
    (2, cc_result(GOOD), "", "Claude Code failed (exit 2)."),
])
def test_cc_parse_unreadable_or_failed(rc, stdout, stderr, reason):
    assert _fails(rc, stdout, stderr) == reason


@pytest.mark.parametrize("payload", [
    {"type": "result"},
    {"is_error": False, "result": 5, "structured_output": [1], "stop_reason": 3},
    {"modelUsage": "x", "result": "{}"},
    {"modelUsage": {"": 1, "a": "x"}, "result": "{}"},
])
def test_cc_parse_tolerates_malformed_fields(payload):
    reply = parse_claude_code_output(0, json.dumps(payload), "", default_model="m")
    assert reply.model in ("m", "a") and reply.stop_reason is None
    assert reply.text in (None, "{}")


def test_cc_env_drops_api_credentials_and_hides_ant_profiles():
    env = {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "t", "ANTHROPIC_PROFILE": "work",
           "ANTHROPIC_CONFIG_DIR": "/profiles", "PATH": "/bin", "HOME": "/h",
           "ANTHROPIC_BASE_URL": "https://gateway", "CLAUDE_CONFIG_DIR": "/c"}
    assert claude_code_env(env, "/empty") == {
        "PATH": "/bin", "HOME": "/h", "ANTHROPIC_BASE_URL": "https://gateway",
        "CLAUDE_CONFIG_DIR": "/c", "ANTHROPIC_CONFIG_DIR": "/empty"}


def test_cc_env_inside_a_claude_code_session_drops_the_session():
    env = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_CODE_SIMPLE": "1",
           "CLAUDE_AGENT_SDK_VERSION": "v", "CLAUDE_PID": "9", "ANTHROPIC_BASE_URL": "http://localhost:1",
           "ANTHROPIC_API_KEY": "k", "CLAUDE_CONFIG_DIR": "/c", "PATH": "/bin", "ADS_DEV_KEY": "a"}
    assert claude_code_env(env, "/empty") == {"CLAUDE_CONFIG_DIR": "/c", "PATH": "/bin",
                                              "ADS_DEV_KEY": "a", "ANTHROPIC_CONFIG_DIR": "/empty"}


FAKE_CLAUDE = """\
import json, os, sys, time
from pathlib import Path
log = Path({log!r})
calls = json.loads(log.read_text()) if log.exists() else []
profiles = os.environ.get("ANTHROPIC_CONFIG_DIR")
calls.append({{"argv": sys.argv[1:], "stdin": sys.stdin.read(), "cwd": os.getcwd(),
              "env": sorted(os.environ), "pid": os.getpid(), "cwd_files": os.listdir("."),
              "profiles": os.listdir(profiles) if profiles else None}})
log.write_text(json.dumps(calls))
answers = json.loads(Path({answers!r}).read_text())
answer = answers[min(len(calls), len(answers)) - 1]
time.sleep(answer.get("sleep", 0))
sys.stderr.write(answer.get("stderr", ""))
sys.stdout.write(answer.get("stdout", ""))
sys.exit(answer.get("exit", 0))
"""


def fake_claude(tmp_path, *answers):
    """An executable stand-in for `claude`: logs each call, then gives the next answer."""
    log, answers_file = tmp_path / "claude-calls.json", tmp_path / "claude-answers.json"
    answers_file.write_text(json.dumps(list(answers)))
    script = tmp_path / "fake-claude"
    script.write_text(f"#!{sys.executable}\n" + FAKE_CLAUDE.format(log=str(log), answers=str(answers_file)))
    script.chmod(0o755)
    return script, lambda: json.loads(log.read_text()) if log.exists() else []


def cc_cfg(command, timeout_s=None):
    cfg = load_config()
    cfg.llm.backend = "claude-code"
    cfg.llm.claude_code.command = str(command)
    if timeout_s is not None:
        cfg.llm.claude_code.timeout_s = timeout_s
    return cfg


def _cc_write(tmp_path, cfg, report=None):
    return write_sections(report or report_with_papers(), cfg, Cache(tmp_path / "cache", EXPIRY))


def test_cc_backend_request_shape(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-for-claude-code")
    script, calls = fake_claude(tmp_path, {"stdout": cc_result(GOOD)})
    block = _cc_write(tmp_path, cc_cfg(script))
    assert block.status == "ok", block.reason
    assert (block.overview, block.model, block.cited_bibcodes) == ("A star.", "claude-opus-5-5", [A])
    assert block.bibcodes_sent == [A, B] and block.effort == "high" and block.from_cache is False
    [call] = calls()
    argv = call["argv"]

    def opt(flag):
        return argv[argv.index(flag) + 1]

    assert argv[:3] == ["-p", "--output-format", "json"]
    assert json.loads(opt("--json-schema")) == WRITER_SCHEMA
    assert opt("--system-prompt") == SYSTEM_PROMPT
    assert (opt("--model"), opt("--effort")) == ("claude-opus-5-5", "high")
    assert opt("--tools") == "" and opt("--setting-sources") == ""
    assert {"--strict-mcp-config", "--no-session-persistence"} <= set(argv)
    assert call["stdin"].startswith("<fact_sheet>") and A in call["stdin"]
    assert "ANTHROPIC_API_KEY" not in call["env"] and call["profiles"] == []
    assert "star-report-claude-" in call["cwd"] and call["cwd_files"] == []


def test_cc_backend_citation_retry(tmp_path):
    bad = json.loads(_out("1999XXX...1....1Z"))
    script, calls = fake_claude(tmp_path, {"stdout": cc_result(bad)}, {"stdout": cc_result(GOOD)})
    block = _cc_write(tmp_path, cc_cfg(script))
    assert block.status == "ok" and len(calls()) == 2
    assert "<correction>" not in calls()[0]["stdin"] and "<correction>" in calls()[1]["stdin"]


def test_cc_backend_refusal_and_bad_format(tmp_path):
    script, _ = fake_claude(tmp_path, {"stdout": cc_result(GOOD, stop="refusal")})
    assert _cc_write(tmp_path, cc_cfg(script)).reason == "Claude declined to write this summary (refusal)."
    script, _ = fake_claude(tmp_path, {"stdout": cc_result({"overview": "x"})})
    assert _cc_write(tmp_path, cc_cfg(script)).reason == FORMAT_FAILURE


def test_cc_backend_not_logged_in(tmp_path):
    raw = json.dumps(load_fixture("claude_code", "not_logged_in"))
    script, _ = fake_claude(tmp_path, {"stdout": raw, "exit": 1})
    block = _cc_write(tmp_path, cc_cfg(script))
    assert block.status == "failed" and block.reason == NOT_LOGGED_IN


def test_cc_backend_not_found(tmp_path):
    block = _cc_write(tmp_path, cc_cfg(tmp_path / "no-such-claude"))
    assert block.status == "failed"
    assert block.reason == "Claude Code (`claude`) was not found; install it or set llm.claude_code.command."


def test_cc_backend_not_executable(tmp_path):
    script = tmp_path / "claude"
    script.write_text("not a program")
    block = _cc_write(tmp_path, cc_cfg(script))
    assert block.status == "failed" and block.reason.startswith("Claude Code could not be started: ")


def test_cc_backend_timeout_kills_claude(tmp_path):
    script, calls = fake_claude(tmp_path, {"sleep": 60})
    start = time.monotonic()
    block = _cc_write(tmp_path, cc_cfg(script, timeout_s=2))
    assert time.monotonic() - start < 15
    assert block.status == "failed" and block.reason == "Claude Code did not finish within 2 s."
    with pytest.raises(ProcessLookupError):
        os.kill(calls()[0]["pid"], 0)


def test_cc_backend_failures_not_cached_and_key_has_backend(tmp_path):
    cache, report = Cache(tmp_path / "cache", EXPIRY), report_with_papers()
    fake = FakeClient([reply(good_json)])
    assert write_sections(report, load_config(), cache, client_factory=lambda: fake).status == "ok"
    script, calls = fake_claude(tmp_path, {"stdout": "", "exit": 1}, {"stdout": cc_result(GOOD)})
    assert write_sections(report, cc_cfg(script), cache).status == "failed"
    second = write_sections(report, cc_cfg(script), cache)
    assert second.status == "ok" and second.from_cache is False and len(calls()) == 2
    third = write_sections(report, cc_cfg(script), cache)
    assert third.from_cache is True and len(calls()) == 2
