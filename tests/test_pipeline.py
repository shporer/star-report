"""run_target with fake lookup and fake sources; no network."""

from __future__ import annotations

import logging

import pytest
from conftest import FakeLookup, gaia_row, simbad_obj, tic_row
from rich.console import Console

from star_report.cache import Cache
from star_report.config import load_config
from star_report.models import LLMBlock
from star_report.pipeline import PipelineDeps, RunOptions, run_target
from star_report.sources.base import Source


class _Fake(Source):
    group = "simbad"
    service = "fake"

    def __init__(self, name):
        self.name = name

    def fetch(self, ctx):
        return {"x": 1}

    def parse(self, raw, ctx):
        return raw


def _deps():
    sky = FakeLookup(
        simbad_objects=[simbad_obj("HD 209458", ids=["TIC 1", "Gaia DR3 11"])],
        gaia_rows={"11": gaia_row("11")},
        tic_rows={1: tic_row(1)},
    )
    return PipelineDeps(lookup=lambda cache, session, cfg: sky,
                        sources=lambda cfg: [_Fake("fake_a"), _Fake("fake_b")],
                        plots=lambda *a: ({}, {}),
                        writer=lambda r, c, k: LLMBlock(status="skipped", reason="test"))


@pytest.fixture
def env(tmp_path):
    cfg = load_config(cwd=tmp_path)
    cfg.cache.dir = tmp_path / "cache"
    cache = Cache(cfg.cache.dir, cfg.cache.expiry_days, cfg.cache.empty_expiry_days)
    return cfg, cache, RunOptions(out_dir=tmp_path / "reports"), Console(record=True, width=100)


def test_run_target_writes_outputs(tmp_path, env):
    cfg, cache, opts, console = env
    outcome = run_target("HD 209458", cfg, opts, cache, console, _deps())
    assert outcome.status == "ok"
    assert (tmp_path / "reports" / "HD_209458" / "report.html").exists()
    assert (tmp_path / "reports" / "HD_209458" / "data.json").exists()
    assert (tmp_path / "reports" / "HD_209458" / "plots").is_dir()
    assert set(outcome.report.results) == {"fake_a", "fake_b"}


def test_unresolved_target(env):
    cfg, cache, opts, console = env
    deps = PipelineDeps(lookup=lambda cache, session, cfg: FakeLookup(),
                        sources=lambda cfg: [])
    outcome = run_target("Nonexistent Star", cfg, opts, cache, console, deps)
    assert outcome.status == "unresolved"
    assert outcome.message.startswith("Could not resolve")


def test_no_llm_records_reason(env):
    cfg, cache, opts, console = env
    cfg.llm.enabled = False
    outcome = run_target("HD 209458", cfg, opts, cache, console, _deps())
    assert outcome.report.llm.reason == "LLM disabled (--no-llm or config)"


def test_stale_plots_are_removed_and_hooks_called(tmp_path, env):
    cfg, cache, opts, console = env
    stale = tmp_path / "reports" / "HD_209458" / "plots" / "old.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x")
    deps = _deps()
    deps.plots = lambda report, d, cfg, cache: ({"cmd": "plots/cmd.png"}, {"cmd": "note"})
    deps.writer = lambda report, cfg, cache: report.llm.model_copy(update={"reason": "writer ran"})
    cfg.llm.enabled = True
    outcome = run_target("HD 209458", cfg, opts, cache, console, deps)
    assert not stale.exists()
    assert outcome.report.plots == {"cmd": "plots/cmd.png"}
    assert outcome.report.llm.reason == "writer ran"


def test_logs_one_line_per_source(env, caplog):
    cfg, cache, opts, console = env
    with caplog.at_level(logging.INFO, logger="star_report.pipeline"):
        run_target("HD 209458", cfg, opts, cache, console, _deps())
    lines = [r.getMessage() for r in caplog.records]
    assert sum("fake_a" in m for m in lines) == 1
    assert sum("fake_b" in m for m in lines) == 1


def test_plots_not_called_when_disabled(env):
    cfg, cache, opts, console = env
    cfg.plots.enabled = False
    calls = []
    deps = _deps()
    deps.plots = lambda *a: calls.append(a) or ({}, {})
    outcome = run_target("HD 209458", cfg, opts, cache, console, deps)
    assert outcome.status == "ok"
    assert calls == []


def test_plots_and_writer_run_together(env):
    import threading
    cfg, cache, opts, console = env
    cfg.llm.enabled = True
    barrier = threading.Barrier(2, timeout=5)
    seen = {}
    deps = _deps()

    def plots(report, d, cfg, cache):
        barrier.wait()
        return {"cmd": "plots/cmd.png"}, {}

    def writer(report, cfg, cache):
        barrier.wait()
        seen["plots"] = dict(report.plots)
        return report.llm.model_copy(update={"reason": "writer ran"})

    deps.plots, deps.writer = plots, writer
    outcome = run_target("HD 209458", cfg, opts, cache, console, deps)
    assert outcome.report.plots == {"cmd": "plots/cmd.png"} and outcome.report.llm.reason == "writer ran"
    assert seen["plots"] == {}


def test_default_writer_is_write_sections():
    from star_report.writer import write_sections
    assert PipelineDeps().writer is write_sections
