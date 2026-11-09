import json
from datetime import datetime, timezone

from star_report.models import (
    BIBCODE_RE,
    LLMBlock,
    Position,
    Provenance,
    Report,
    SourceResult,
    StarIdentity,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def _report() -> Report:
    ident = StarIdentity(
        input="HD 1",
        main_name="HD 1",
        position=Position(ra=1.0, dec=2.0, epoch=2000.0, source="simbad"),
        resolved_via="simbad",
    )
    result = SourceResult(
        source="simbad",
        status="ok",
        data={"a": [1, 2]},
        provenance=Provenance(service="simbad", query="HD 1", retrieved=NOW, from_cache=False),
    )
    return Report(
        tool_version="0.1.0",
        generated=NOW,
        identity=ident,
        results={"simbad": result},
        llm=LLMBlock(status="skipped", reason="no key"),
    )


def test_report_dumps_to_json_with_schema_version():
    dumped = json.loads(json.dumps(_report().model_dump(mode="json")))
    assert dumped["schema_version"] == 1


def test_report_round_trip():
    report = _report()
    dumped = json.loads(json.dumps(report.model_dump(mode="json")))
    assert Report.model_validate(dumped) == report


def test_bibcode_re():
    assert BIBCODE_RE.search("see [2020A&A...643A.148G] here").group(1) == "2020A&A...643A.148G"
