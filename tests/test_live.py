"""Live reference-star suite: real services, real caches. Run with `pytest -m live -v`.

All tests share one session-scoped cache directory (never ~/.cache/star-report); the cold-run
test uses its own empty one.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest
import yaml
from conftest import all_ref_stars

from star_report.cache import Cache
from star_report.cli import main
from star_report.config import load_config
from star_report.resolve import LiveLookup, parse_target, resolve
from star_report.sources.base import make_session

pytestmark = pytest.mark.live

STARS = {s["slug"]: s for s in all_ref_stars()}
SPELLINGS = [(s["slug"], sp) for s in STARS.values() for sp in s["spellings"]]
ALLOWED_STATUSES = {"ok", "empty", "skipped"}
HD_209458_OK = ["simbad", "gaia_core", "tic", "exoplanet_archive", "exofop", "mast",
                "tess_pointing", "eso"]
FAINT, UNCOVERED, MAIN = "TIC_410450857", "HD_125231", "HD_209458"


# ---- helpers ----


def _target(slug: str) -> str:
    star = STARS[slug]
    return star["simbad_main_id"] or f"TIC {star['tic']}"


def _config_file(directory: Path, cache_dir: Path) -> Path:
    path = directory / "live-config.yaml"
    path.write_text(yaml.safe_dump({"cache": {"dir": str(cache_dir)}}))
    return path


def run_report(target: str, out: Path, config: Path, *extra: str) -> tuple[int, dict]:
    """Run the CLI in-process; return (exit code, parsed data.json of the one report)."""
    rc = main([target, "--no-open", "--out", str(out), "--config", str(config), *extra])
    files = sorted(out.glob("*/data.json"))
    assert len(files) == 1, f"expected one data.json under {out}, found {files}"
    return rc, json.loads(files[0].read_text())


def statuses(data: dict) -> dict[str, str]:
    return {name: res["status"] for name, res in data["results"].items()}


def strip_volatile(value):
    """Drop `generated` and every `elapsed_s`, at any depth."""
    if isinstance(value, dict):
        return {k: strip_volatile(v) for k, v in value.items() if k not in ("generated", "elapsed_s")}
    if isinstance(value, list):
        return [strip_volatile(v) for v in value]
    return value


def diff_paths(a, b, path: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += diff_paths(a.get(k), b.get(k), f"{path}/{k}")
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [p for i, (x, y) in enumerate(zip(a, b)) for p in diff_paths(x, y, f"{path}[{i}]")]
    return [] if a == b else [path]


def count_sentences(text: str) -> int:
    text = re.sub(r"\[[^\]]*\]", "", text)  # citations such as [2020A&A...641A..70R]
    return len([s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", text.strip()) if s])


# ---- fixtures ----


@pytest.fixture(scope="session")
def live_cache(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("live-cache")


@pytest.fixture(scope="session")
def live_config(tmp_path_factory, live_cache) -> Path:
    return _config_file(tmp_path_factory.mktemp("live-config"), live_cache)


@pytest.fixture(scope="session")
def lookup(live_cache):
    cfg = load_config()
    cache = Cache(live_cache, cfg.cache.expiry_days, cfg.cache.empty_expiry_days)
    return cfg, LiveLookup(cache, make_session(cfg.sources.timeout_s), cfg)


# ---- tests ----


@pytest.mark.parametrize("slug,spelling", SPELLINGS, ids=[f"{a}:{b}" for a, b in SPELLINGS])
def test_spellings_resolve(slug, spelling, lookup):
    cfg, live = lookup
    star = STARS[slug]
    ident = resolve(parse_target(spelling), live, cfg.resolve)
    assert ident.tic_id == star["tic"]
    assert ident.gaia_dr3_id == star["gaia_dr3"]


@pytest.mark.parametrize("slug", list(STARS))
def test_report_no_llm(slug, tmp_path, live_config):
    rc, data = run_report(_target(slug), tmp_path, live_config, "--no-llm")
    assert rc == 0
    st = statuses(data)
    assert set(st.values()) <= ALLOWED_STATUSES, {k: v for k, v in st.items() if v not in ALLOWED_STATUSES}
    if slug == MAIN:
        assert {name: st[name] for name in HD_209458_OK} == dict.fromkeys(HD_209458_OK, "ok")
    if slug == FAINT:
        assert st["simbad"] == "skipped"
    if slug == UNCOVERED:
        assert st["tess_pointing"] == "empty"


def test_cached_rerun_under_5s(tmp_path, live_config, capsys):
    run_report(_target(MAIN), tmp_path / "warm", live_config, "--no-llm")  # warm every cache
    start = time.perf_counter()
    rc, _ = run_report(_target(MAIN), tmp_path / "timed", live_config, "--no-llm")
    elapsed = time.perf_counter() - start
    with capsys.disabled():
        print(f"\ncached rerun of HD 209458: {elapsed:.1f} s")
    assert rc == 0
    assert elapsed < 5.0


def test_no_llm_deterministic(tmp_path, live_config):
    run_report(_target(MAIN), tmp_path / "warm", live_config, "--no-llm")  # warm every cache
    _, first = run_report(_target(MAIN), tmp_path / "a", live_config, "--no-llm")
    _, second = run_report(_target(MAIN), tmp_path / "b", live_config, "--no-llm")
    a, b = strip_volatile(first), strip_volatile(second)
    assert a == b, f"data.json differs at: {diff_paths(a, b)}"


def _cold_run(tmp_path, capsys, *extra: str) -> tuple[int, dict, float]:
    config = _config_file(tmp_path, tmp_path / "empty-cache")
    start = time.perf_counter()
    rc, data = run_report(_target(MAIN), tmp_path / "out", config, *extra)
    elapsed = time.perf_counter() - start
    with capsys.disabled():
        print(f"\ncold run of HD 209458 {extra or '(plots and LLM)'}: {elapsed:.1f} s")
    return rc, data, elapsed


def test_cold_run_time_no_llm(tmp_path, capsys):
    """Cold cache, plots on, LLM off. Does not cover the LLM leg; see the with_llm test."""
    rc, _, elapsed = _cold_run(tmp_path, capsys, "--no-llm")
    assert rc == 0
    assert elapsed < 150.0


# `ant auth login` profiles are deliberately not detected: the live LLM tests run only when
# the environment variables are set.
@pytest.mark.skipif(not (os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("ADS_DEV_KEY")),
                    reason="needs ANTHROPIC_API_KEY and ADS_DEV_KEY")
def test_cold_run_time_with_llm(tmp_path, capsys):
    rc, data, elapsed = _cold_run(tmp_path, capsys)
    assert rc == 0
    assert data["llm"]["status"] == "ok", data["llm"].get("reason")
    assert elapsed < 150.0


# Same credentials-only rule as above (no `ant auth login` detection).
@pytest.mark.skipif(not (os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("ADS_DEV_KEY")),
                    reason="needs ANTHROPIC_API_KEY and ADS_DEV_KEY")
def test_llm_end_to_end(tmp_path, live_config):
    rc, data = run_report(_target(MAIN), tmp_path, live_config)
    assert rc == 0
    llm = data["llm"]
    assert llm["status"] == "ok", llm.get("reason")
    assert set(llm["cited_bibcodes"]) <= set(llm["bibcodes_sent"])
    assert 3 <= count_sentences(llm["overview"]) <= 5, llm["overview"]
