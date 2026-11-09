import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(pytest.mark.enable_socket)


# ---- shared helpers for source tests and the fixture recorder ----
import json  # noqa: E402
import tempfile  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import yaml  # noqa: E402

from star_report.cache import Cache  # noqa: E402
from star_report.config import Config, load_config  # noqa: E402
from star_report.models import Position, StarIdentity  # noqa: E402
from star_report.sources.base import SourceContext, make_session  # noqa: E402

TESTS_DIR = Path(__file__).parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
REFERENCE_STARS = TESTS_DIR / "reference_stars.yaml"
DEFAULT_STAR = "HD_209458"


def all_ref_stars() -> list[dict]:
    return yaml.safe_load(REFERENCE_STARS.read_text())


def ref_star(slug: str) -> dict:
    for star in all_ref_stars():
        if star["slug"] == slug:
            return star
    raise KeyError(slug)


def ref_identity(slug: str) -> StarIdentity:
    star = ref_star(slug)
    return StarIdentity(
        input=star["simbad_main_id"],
        main_name=star["simbad_main_id"],
        simbad_id=star["simbad_main_id"],
        tic_id=star["tic"],
        gaia_dr3_id=star["gaia_dr3"],
        gaia_dr2_id=star["gaia_dr2"],
        position=Position(**star["position"], source="gaia_dr3"),
        other_ids=dict(star["other_ids"]),
        resolved_via="simbad",
    )


def load_fixture(source: str, slug: str) -> Any:
    return json.loads((FIXTURES_DIR / source / f"{slug}.json").read_text())


def make_ctx(
    identity: StarIdentity | None = None,
    cfg: Config | None = None,
    cache_dir: Path | None = None,
    now: datetime | None = None,
) -> SourceContext:
    """A context on a throwaway cache directory (never the real user cache)."""
    cfg = cfg or load_config(cwd=Path(tempfile.gettempdir()))
    cache_dir = cache_dir or Path(tempfile.mkdtemp(prefix="star-report-test-cache-"))
    cache_kwargs: dict[str, Any] = {}
    if now is not None:
        cache_kwargs["now"] = lambda: now
    cache = Cache(cache_dir, cfg.cache.expiry_days, cfg.cache.empty_expiry_days, **cache_kwargs)
    ctx_kwargs: dict[str, Any] = {}
    if now is not None:
        ctx_kwargs["now"] = lambda: now
    return SourceContext(
        identity=identity or ref_identity(DEFAULT_STAR),
        config=cfg,
        cache=cache,
        session=make_session(),
        **ctx_kwargs,
    )


@pytest.fixture
def ctx(tmp_path):
    return make_ctx(cache_dir=tmp_path / "cache")


# ---- full-report builder for render tests ----
from star_report import derive  # noqa: E402
from star_report.models import LLMBlock, Provenance, Report, SourceResult  # noqa: E402
from star_report.sources.registry import build_sources  # noqa: E402

REPORT_SOURCES = ["simbad", "gaia_core", "gaia_neighbors", "tic"]
_FIXED_TIME = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)


def make_report(slug: str = DEFAULT_STAR, **overrides) -> Report:
    """A deterministic Report built by parsing the recorded fixtures offline."""
    cfg = load_config(cwd=Path(tempfile.gettempdir()))
    sources = {s.name: s for s in build_sources(cfg)}
    identity = ref_identity(slug)
    ctx = make_ctx(identity=identity, cfg=cfg)
    results: dict[str, SourceResult] = {}
    for name in REPORT_SOURCES:
        src = sources[name]
        data = src.parse(load_fixture(name, slug), ctx)
        results[name] = SourceResult(
            source=name,
            status="ok" if data else "empty",
            data=data,
            provenance=Provenance(service=src.service, query=json.dumps(src.query(ctx), default=str),
                                  catalog_version=src.catalog_version, retrieved=_FIXED_TIME,
                                  from_cache=True),
            elapsed_s=0.4,
        )
    rows = derive.parameter_rows(results, cfg.parameters)
    report = Report(
        tool_version="0.1.0",
        generated=_FIXED_TIME,
        identity=identity,
        results=results,
        parameters=rows,
        flags=derive.flags(identity, results, cfg.flags),
        llm=LLMBlock(status="skipped", reason="LLM disabled (--no-llm or config)"),
    )
    return report.model_copy(update=overrides) if overrides else report
