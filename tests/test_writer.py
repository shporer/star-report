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
