"""LLM writer inputs: fact sheet, system prompt, output schema and citation check."""

from __future__ import annotations

import json
import math
from typing import Any

from pydantic import BaseModel, ConfigDict

from star_report.derive import description_line, display_name, toi_entries
from star_report.models import BIBCODE_RE, Paper, ParamCell, Report

PROMPT_VERSION = 1

SYSTEM_PROMPT = """\
You write short, factual summaries of stars for professional astronomers.
You receive a fact sheet compiled from astronomical catalogs and archives, and a list of papers from NASA ADS with their abstracts.

Rules:
- Use only the material provided. Do not add facts from memory.
- Never state a number that does not appear in the fact sheet or in a paper abstract.
- When the literature is sparse or missing, say so plainly.
- Keep a neutral, factual tone. No hype and no speculation.

Write:
- overview: 3 to 5 sentences on what the star is, its key parameters, any planets, binarity or variability, and what data exist.
- literature_summary: 1 to 3 short paragraphs on what the papers report. Cite papers inline as [bibcode], using only bibcodes from the paper list.
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
        ident = k.get("kepoi_name") or k.get("name") or k.get("koi")
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
    if ident.gaia_dr3_id:
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
    return BIBCODE_RE.findall(text)


def check_citations(out: WriterOutput, allowed: set[str]) -> list[str]:
    found = set(find_citations(out.overview)) | set(find_citations(out.literature_summary))
    found |= set(out.cited_bibcodes)
    return sorted(found - set(allowed))
