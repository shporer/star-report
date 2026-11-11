"""LLM writer: fact sheet, system prompt, output schema, citation check, and the Claude call.

The call goes to the Anthropic API (llm.backend: api) or to Claude Code's print mode on the user's
Claude Code login (llm.backend: claude-code), which needs no API key.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import anthropic
from pydantic import BaseModel, ConfigDict, ValidationError

from star_report.cache import Cache
from star_report.config import Config, LLMConfig
from star_report.derive import description_line, display_name, toi_entries
from star_report.models import BIBCODE_PART_RE, CITATION_RE, LLMBlock, Paper, ParamCell, Report

PROMPT_VERSION = 4

SYSTEM_PROMPT = """\
You write short, factual summaries of stars for professional astronomers.
They open an HTML report whose tables, plots and paper list already hold the full details, so a summary picks out what matters most instead of repeating every value.
You receive a fact sheet compiled from astronomical catalogs and archives, and a list of papers from NASA ADS with their abstracts.

Rules:
- Use only the material provided. Do not add facts from memory. That includes historical context, such as which result was the first of its kind, unless a paper's abstract says so.
- Quote catalog codes and classes as given (for example a variability class); do not expand or interpret them unless the material explains them.
- Name instruments, telescopes, surveys and catalogs as the material does; do not abbreviate or substitute names yourself.
- Never state a number that does not appear in the fact sheet or in a paper abstract, and keep the qualifiers that come with it: an upper limit stays an upper limit, and an approximate value stays approximate.
- When the literature is sparse or missing, say so plainly.
- Keep a neutral, factual tone. No hype and no speculation.
- Write plain prose, with no Markdown, headings, bold, italics or lists. The report prints the text exactly as written, so any markup would show as literal symbols.

Write:
- overview: 3 to 5 sentences (never more than 5), about 100 words in total, on what the star is and its key parameters, any planets, binarity or variability, and what data exist. Combine related facts in one sentence and choose the most important ones; the report's tables carry the rest.
- literature_summary: 1 to 3 paragraphs separated by a blank line, about 250 words in total, on what the papers report. Group related papers instead of summarizing each in turn. Cite papers inline as [bibcode], using only bibcodes from the paper list; you need not cite every paper.
- cited_bibcodes: every bibcode you cited.
If no papers are provided, the literature_summary says that and cites nothing.
"""


class WriterOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overview: str
    literature_summary: str
    cited_bibcodes: list[str]


WRITER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "literature_summary": {"type": "string"},
        "cited_bibcodes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overview", "literature_summary", "cited_bibcodes"],
    "additionalProperties": False,
}


# ---- fact sheet ----

def _data(report: Report, name: str) -> dict:
    res = report.results.get(name)
    return res.data if res is not None and res.status == "ok" and isinstance(res.data, dict) else {}


def _dicts(value: Any) -> list[dict]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _ints(value: Any) -> list[int]:
    return [x for x in value if isinstance(x, int) and not isinstance(x, bool)] if isinstance(value, list) else []


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _fmt_cell(cell: ParamCell, decimals: int) -> str | None:
    if cell.value is None:
        return None
    text = f"{cell.value:.{decimals}f}"
    if cell.err is not None:
        text += f" ± {cell.err:.{decimals}f}"
    if cell.note:
        text += f" ({cell.note})"
    return text


def _prune(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _planets(report: Report) -> list[dict]:
    out = []
    for p in _dicts(_data(report, "exoplanet_archive").get("planets")):
        out.append(_prune({
            "name": p.get("name"), "period_d": _num(p.get("period_d")),
            "radius_re": _num(p.get("radius_re")), "mass_me": _num(p.get("mass_me")),
            "method": p.get("method"), "year": p.get("year"),
        }))
    return out


def _kois(report: Report) -> list[str]:
    out = []
    for k in _dicts(_data(report, "exoplanet_archive").get("kois")):
        ident = k.get("name") or k.get("koi")
        disp = k.get("disposition")
        if ident:
            out.append(f"{ident} ({disp})" if disp else str(ident))
    return out


def _inventory(report: Report) -> dict:
    tp = _data(report, "tess_pointing")
    upcoming = [_prune({"sector": u.get("sector"), "start": (u.get("start") or "")[:10] or None})
                for u in _dicts(tp.get("upcoming"))]
    mast = _data(report, "mast")
    missions: dict[str, Any] = {}
    tess = mast.get("tess") if isinstance(mast.get("tess"), dict) else {}
    for key in ("spoc_120s", "spoc_20s", "ffi"):
        if secs := _ints(tess.get(key)):
            missions[f"tess_{key}_sectors"] = len(secs)
    for key in ("kepler", "k2"):
        block = mast.get(key)
        if isinstance(block, dict) and (block.get("n_long") or block.get("n_short")):
            missions[key] = {"long_cadence": block.get("n_long") or 0,
                             "short_cadence": block.get("n_short") or 0}
    hlsp = [h["provenance"] for h in _dicts(mast.get("hlsp")) if h.get("provenance")]
    if hlsp:
        missions["hlsp"] = hlsp
    other: dict[str, int] = {}
    for o in _dicts(mast.get("other")):
        if o.get("collection"):
            other[str(o["collection"])] = other.get(str(o["collection"]), 0) + (
                o["n"] if isinstance(o.get("n"), int) else 1)
    if other:
        missions["other_collections"] = dict(sorted(other.items()))

    spectra: dict[str, Any] = {}
    eso = _data(report, "eso")
    if eso_inst := [{"instrument": i.get("instrument"), "n": i.get("n")}
                    for i in _dicts(eso.get("instruments")) if i.get("instrument")]:
        spectra["eso"] = {"total": eso.get("total"), "instruments": eso_inst}
    if koa := [{"instrument": i.get("instrument"), "frames": i.get("frames")}
               for i in _dicts(_data(report, "koa").get("instruments")) if i.get("instrument")]:
        spectra["koa"] = koa
    harps = _data(report, "harps_rvbank")
    if harps:
        spectra["harps_rvbank"] = _prune({"n_rv": harps.get("n_rv"),
                                          "span_yr": (round(s, 1) if (s := _num(harps.get("span_yr"))) is not None else None)})
    if _data(report, "apogee"):
        spectra["apogee"] = True
    if _data(report, "lamost"):
        spectra["lamost"] = True
    if _data(report, "gaia_core").get("has_rvs"):
        spectra["gaia_rvs"] = True

    followup = _data(report, "exofop").get("followup")
    return {
        "tess_observed": _ints(tp.get("observed")),
        "tess_upcoming": upcoming,
        "missions": missions,
        "spectra": spectra,
        "exofop_followup": followup if isinstance(followup, dict) else {},
    }


def _paper_dicts(report: Report) -> list[dict]:
    return _dicts(_data(report, "ads").get("papers"))


def _literature(report: Report) -> dict:
    ads = report.results.get("ads")
    data = _data(report, "ads")
    papers = _paper_dicts(report)
    found = data.get("num_found")
    note = None
    if ads is None:
        note = "ADS was not queried"
    elif ads.status == "skipped":
        note = "ADS was skipped" + (f": {ads.error}" if ads.error else "")
    elif ads.status in ("error", "timeout"):
        note = "ADS was unavailable"
    elif not papers:
        note = "ADS holds no papers for this star"
    return {
        "ads_records_found": found if isinstance(found, int) else 0,
        "papers_provided": len(papers),
        "note": note,
    }


def build_fact_sheet(report: Report) -> dict:
    ident = report.identity
    pos = ident.position
    identifiers: dict[str, Any] = {"main_name": ident.main_name}
    if ident.tic_id is not None:
        identifiers["TIC"] = ident.tic_id
    if ident.gaia_dr3_id and not ident.gaia_dr3_unconfirmed:  # the warnings explain an unconfirmed ID
        identifiers["Gaia DR3"] = ident.gaia_dr3_id
    identifiers.update({k: v for k, v in sorted(ident.other_ids.items()) if v})

    parameters = []
    for row in report.parameters:
        values = {k: t for k, c in row.cells.items() if (t := _fmt_cell(c, row.decimals)) is not None}
        if values:
            parameters.append({"quantity": row.label, "unit": row.unit, "values": values,
                               "sources_disagree": row.disagree})

    return {
        "name": display_name(ident.main_name),
        "identifiers": identifiers,
        "position": _prune({
            "ra_deg": round(pos.ra, 5), "dec_deg": round(pos.dec, 5), "epoch": pos.epoch,
            "pmra_mas_yr": pos.pmra, "pmdec_mas_yr": pos.pmdec,
            "parallax_mas": pos.parallax, "rv_km_s": pos.rv,
        }),
        "description": description_line(report.results, report.parameters),
        "parameters": parameters,
        "flags": list(report.flags),
        "warnings": list(ident.warnings),
        "planets": _planets(report),
        "tois": [f"{t} ({d})" if d else str(t) for t, d in toi_entries(report.results) if t],
        "kois": _kois(report),
        "inventory": _inventory(report),
        "literature": _literature(report),
    }


# ---- user message and citations ----

def _first_author(paper: Paper) -> str:
    return paper.authors[0] if paper.authors else "unknown"


def build_user_message(facts: dict, papers: list[Paper], literature_note: str | None,
                       correction: str | None = None) -> str:
    sheet = json.dumps(facts, indent=1, sort_keys=True, ensure_ascii=False)
    if papers:
        blocks = []
        for i, p in enumerate(papers, 1):
            blocks.append(
                f"[{i}] {p.bibcode} | {p.year if p.year is not None else 'n.d.'} | "
                f"{_first_author(p)} | {p.citation_count} citations\n"
                f"Title: {p.title}\n"
                f"Abstract: {p.abstract or '(no abstract)'}")
        body = "\n\n".join(blocks)
    else:
        body = f"No papers were provided: {literature_note or 'none available'}"
    msg = f"<fact_sheet>\n{sheet}\n</fact_sheet>\n\n<papers>\n{body}\n</papers>"
    if correction:
        msg += f"\n\n<correction>\n{correction}\n</correction>"
    return msg


def find_citations(text: str) -> list[str]:
    """Every bibcode cited in brackets, alone or grouped: [A], [A, B] or [A; B]."""
    return [code for group in CITATION_RE.finditer(text)
            for code in BIBCODE_PART_RE.findall(group.group(0))]


def check_citations(out: WriterOutput, allowed: set[str]) -> list[str]:
    found = set(find_citations(out.overview)) | set(find_citations(out.literature_summary))
    found |= set(out.cited_bibcodes)
    return sorted(found - set(allowed))


# ---- Claude call, retry, failures, caching ----

_LITERATURE_NOTES = {
    "skipped": "ADS_DEV_KEY not set; the literature was not searched.",
    "error": "ADS search failed.",
    "timeout": "ADS search failed.",
    "empty": "ADS found no papers for this object.",
}
_NO_CREDENTIALS = "No Anthropic credentials found (set ANTHROPIC_API_KEY or run `ant auth login`)."
_FORMAT_FAILURE = "Claude's output did not match the expected format."


@dataclass
class ClaudeReply:
    stop_reason: str | None
    text: str | None
    model: str


class WriterFailure(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def call_claude(client, llm: LLMConfig, system: str, user: str) -> ClaudeReply:
    params = dict(model=llm.model, max_tokens=llm.max_tokens, system=system,
                  messages=[{"role": "user", "content": user}],
                  output_config={"effort": llm.effort,
                                 "format": {"type": "json_schema", "schema": WRITER_SCHEMA}})
    if llm.server_fallback:
        params |= dict(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    with client.beta.messages.stream(**params) as stream:   # streaming: long input, adaptive thinking
        msg = stream.get_final_message()
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text") or None
    return ClaudeReply(msg.stop_reason, text, msg.model)     # msg.model = model that served it (fallback-aware)


def _check(reply: ClaudeReply) -> WriterOutput:
    """The stop-reason and format checks both backends share."""
    if reply.stop_reason == "refusal":
        raise WriterFailure("Claude declined to write this summary (refusal).")
    if reply.stop_reason == "max_tokens":
        raise WriterFailure("Claude's output was cut off (max_tokens).")
    try:
        if not reply.text:
            raise ValueError("no text")
        return WriterOutput.model_validate_json(reply.text)
    except ValueError as e:   # includes pydantic ValidationError
        raise WriterFailure(_FORMAT_FAILURE) from e


def _api_caller(llm: LLMConfig,
                client_factory: Callable[[], Any] | None) -> Callable[[str], ClaudeReply]:
    """The API backend: one SDK client; API errors become stated reasons."""
    try:
        client = client_factory() if client_factory is not None else default_client(llm)
    except anthropic.CredentialsError as e:
        raise WriterFailure(_NO_CREDENTIALS) from e

    def call(user: str) -> ClaudeReply:
        try:
            return call_claude(client, llm, SYSTEM_PROMPT, user)
        except (anthropic.CredentialsError, TypeError) as e:
            if isinstance(e, TypeError) and "Could not resolve authentication method" not in str(e):
                raise
            raise WriterFailure(_NO_CREDENTIALS) from e
        except anthropic.AuthenticationError as e:
            raise WriterFailure("Anthropic API rejected the credentials.") from e
        except anthropic.APIError as e:
            raise WriterFailure(f"Claude API error: {type(e).__name__}: {e}"[:200]) from e

    return call


def _papers_and_note(report: Report) -> tuple[list[Paper], str | None]:
    ads = report.results.get("ads")
    if ads is not None and ads.status == "ok":
        papers = []
        for d in _paper_dicts(report):
            try:
                papers.append(Paper.model_validate(d))
            except ValidationError:
                continue
        return papers, None
    return [], _LITERATURE_NOTES.get(ads.status if ads is not None else "skipped",
                                     "ADS search failed.")


def default_client(llm: LLMConfig) -> anthropic.Anthropic:
    """The SDK client with the configured per-request timeout and retry count."""
    return anthropic.Anthropic(timeout=llm.timeout_s, max_retries=llm.max_retries)


# ---- Claude Code backend: `claude -p` on the user's Claude Code login, no API key ----

_CC_NOT_FOUND = "Claude Code (`claude`) was not found; install it or set llm.claude_code.command."
_CC_NOT_LOGGED_IN = "Claude Code is not logged in (run `claude auth login`)."
_CC_LOGIN_HINTS = ("authenticate", "not logged in", "/login", "oauth")
_CC_API_CREDENTIALS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE",
                       "ANTHROPIC_CONFIG_DIR")
_CC_SESSION_PREFIXES = ("CLAUDE", "ANTHROPIC_")
_CC_KEEP = ("CLAUDE_CONFIG_DIR",)


def claude_code_env(environ: Mapping[str, str], empty_dir: str) -> dict[str, str]:
    """The environment for claude: its own login only, never Anthropic API credentials.

    Exported API keys are dropped, and ANTHROPIC_CONFIG_DIR points at the empty `empty_dir`: with no
    login of its own, claude would otherwise fall back to an `ant auth login` profile and bill that
    API organization. From inside a Claude Code session (CLAUDECODE set), that session's CLAUDE*
    and ANTHROPIC_* variables are dropped too, so claude runs as it would from a terminal.
    """
    nested = bool(environ.get("CLAUDECODE"))
    env = {k: v for k, v in environ.items()
           if k not in _CC_API_CREDENTIALS
           and not (nested and k.startswith(_CC_SESSION_PREFIXES) and k not in _CC_KEEP)}
    env["ANTHROPIC_CONFIG_DIR"] = empty_dir
    return env


def claude_code_argv(llm: LLMConfig, system: str) -> list[str]:
    """Print mode with structured output; no tools, settings, plugins, MCP servers or saved session."""
    return [llm.claude_code.command, "-p", "--output-format", "json",
            "--json-schema", json.dumps(WRITER_SCHEMA), "--system-prompt", system,
            "--model", llm.model, "--effort", llm.effort,
            "--tools", "", "--setting-sources", "", "--strict-mcp-config",
            "--no-session-persistence"]


def run_claude_code(llm: LLMConfig, system: str, user: str) -> tuple[int, str, str]:
    """Run claude once in an empty temporary directory, the user message on stdin.

    Returns (exit code, stdout, stderr). claude is killed at llm.claude_code.timeout_s, so the call
    cannot hang; it stays in star-report's process group, so Ctrl-C stops it too.
    """
    timeout = llm.claude_code.timeout_s
    with tempfile.TemporaryDirectory(prefix="star-report-claude-", ignore_cleanup_errors=True) as tmp:
        cwd, no_profiles = os.path.join(tmp, "work"), os.path.join(tmp, "no-profiles")
        os.mkdir(cwd)
        os.mkdir(no_profiles)
        try:
            proc = subprocess.Popen(
                claude_code_argv(llm, system), cwd=cwd, env=claude_code_env(os.environ, no_profiles),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace")
        except FileNotFoundError as e:
            raise WriterFailure(_CC_NOT_FOUND) from e
        except OSError as e:
            raise WriterFailure(f"Claude Code could not be started: {e}"[:200]) from e
        try:
            out, err = proc.communicate(user, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            raise WriterFailure(f"Claude Code did not finish within {timeout:g} s.") from e
    return proc.returncode, out, err


def _json_object(text: str) -> dict | None:
    """The JSON object claude printed: the whole output, else its last line that parses."""
    for candidate in [text, *(ln for ln in reversed(text.splitlines()) if ln.lstrip().startswith("{"))]:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _served_model(usage: Any, default: str) -> str:
    """The model that wrote the most output tokens, from claude's modelUsage."""
    best, best_tokens = default, -1
    for name, stats in usage.items() if isinstance(usage, dict) else ():
        tokens = stats.get("outputTokens") if isinstance(stats, dict) else None
        tokens = tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else 0
        if isinstance(name, str) and name and tokens > best_tokens:
            best, best_tokens = name, tokens
    return best


def _exit_reason(returncode: int, stderr: str) -> str:
    last = next((ln.strip() for ln in reversed(stderr.splitlines()) if ln.strip()), "")
    return (f"Claude Code failed (exit {returncode}): {last}" if last
            else f"Claude Code failed (exit {returncode}).")[:200]


def parse_claude_code_output(returncode: int, stdout: str, stderr: str, *,
                             default_model: str) -> ClaudeReply:
    """claude's `--output-format json` result as a ClaudeReply; failures raise WriterFailure."""
    data = _json_object(stdout)
    if data is None:
        raise WriterFailure(_exit_reason(returncode, stderr) if returncode
                            else "Claude Code's reply could not be read.")
    message = data.get("result") if isinstance(data.get("result"), str) else ""
    subtype = data.get("subtype") if isinstance(data.get("subtype"), str) else ""
    if subtype == "error_max_structured_output_retries":
        raise WriterFailure(_FORMAT_FAILURE)
    if data.get("is_error") is True or subtype.startswith("error"):
        if any(hint in message.lower() for hint in _CC_LOGIN_HINTS):
            raise WriterFailure(_CC_NOT_LOGGED_IN)
        raise WriterFailure(f"Claude Code error: {message or subtype or 'unknown'}"[:200])
    if returncode:
        raise WriterFailure(_exit_reason(returncode, stderr))
    structured = data.get("structured_output")
    text = json.dumps(structured) if isinstance(structured, dict) else (message or None)
    stop = data.get("stop_reason")
    return ClaudeReply(stop if isinstance(stop, str) else None, text,
                       _served_model(data.get("modelUsage"), default_model))


def call_claude_code(llm: LLMConfig, system: str, user: str) -> ClaudeReply:
    rc, out, err = run_claude_code(llm, system, user)
    return parse_claude_code_output(rc, out, err, default_model=llm.model)


def write_sections(report: Report, cfg: Config, cache: Cache,
                   client_factory: Callable[[], Any] | None = None) -> LLMBlock:
    llm = cfg.llm
    sent: list[str] = []

    def producer() -> dict:
        if llm.backend == "claude-code":
            def call(user: str) -> ClaudeReply:
                return call_claude_code(llm, SYSTEM_PROMPT, user)
        else:
            call = _api_caller(llm, client_factory)

        def ask(correction: str | None = None) -> tuple[ClaudeReply, WriterOutput]:
            reply = call(build_user_message(facts, papers, note, correction))
            return reply, _check(reply)

        reply, out = ask()
        bad = check_citations(out, set(sent))
        if bad:
            reply, out = ask(f"Your previous answer cited bibcodes that are not in the paper list: "
                             f"{', '.join(bad)}. Cite only bibcodes from the list, written as [bibcode].")
            if check_citations(out, set(sent)):
                raise WriterFailure("LLM summary failed citation check")
        return LLMBlock(status="ok", overview=out.overview, literature_summary=out.literature_summary,
                        cited_bibcodes=out.cited_bibcodes, model=reply.model, effort=llm.effort,
                        generated=datetime.now(timezone.utc), bibcodes_sent=sent).model_dump(mode="json")

    try:
        facts = build_fact_sheet(report)
        papers, note = _papers_and_note(report)
        sent = [p.bibcode for p in papers]
        key = {"facts": facts, "bibcodes": sent, "backend": llm.backend, "model": llm.model,
               "effort": llm.effort, "server_fallback": llm.server_fallback,
               "prompt_version": PROMPT_VERSION}
        entry = cache.fetch("llm", "writer", key, producer)
        block = LLMBlock.model_validate(entry.payload)
        return block.model_copy(update={"from_cache": entry.from_cache})
    except WriterFailure as e:
        return LLMBlock(status="failed", reason=e.reason, bibcodes_sent=sent)
    except Exception as e:  # the report must still render; nothing is cached on failure
        return LLMBlock(status="failed", reason=f"LLM writer error: {type(e).__name__}: {e}"[:200],
                        bibcodes_sent=sent)
