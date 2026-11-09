"""The per-target pipeline: resolve, gather, derive, write, summarise."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import requests
from rich.console import Console

from star_report import __version__
from star_report.cache import Cache
from star_report.config import Config, ads_token
from star_report.derive import display_name, flags, parameter_rows, slugify
from star_report.models import LLMBlock, Report
from star_report.render.html import write_outputs
from star_report.render.terminal import print_summary
from star_report.resolve import CatalogLookup, LiveLookup, ResolutionError, parse_target, resolve
from star_report.sources.base import Source, SourceContext, make_session, run_sources
from star_report.sources.registry import build_sources

log = logging.getLogger("star_report.pipeline")

LLM_DISABLED_REASON = "LLM disabled (--no-llm or config)"


def no_plots(report: Report, plots_dir: Path, cfg: Config,
             cache: Cache) -> tuple[dict[str, str], dict[str, str]]:
    return {}, {}


def no_llm(report: Report, cfg: Config, cache: Cache) -> LLMBlock:
    return LLMBlock(status="skipped", reason="LLM writer not available yet")


@dataclass
class RunOptions:
    out_dir: Path
    refresh: bool = False
    verbose: bool = False


@dataclass
class TargetOutcome:
    target: str
    status: Literal["ok", "unresolved", "crashed"]
    report_path: Path | None = None
    report: Report | None = None
    message: str | None = None


@dataclass
class PipelineDeps:
    lookup: Callable[[Cache, requests.Session, Config], CatalogLookup] = LiveLookup
    sources: Callable[[Config], list[Source]] = build_sources
    plots: Callable[[Report, Path, Config, Cache],
                    tuple[dict[str, str], dict[str, str]]] = no_plots
    writer: Callable[[Report, Config, Cache], LLMBlock] = no_llm


def _fresh_plots_dir(out: Path, out_root: Path, slug: str) -> Path:
    """Replace out/plots with an empty directory; never deletes outside the output root."""
    plots_dir = out / "plots"
    if slug and out.resolve().is_relative_to(out_root.resolve()) and out.resolve() != out_root.resolve():
        shutil.rmtree(plots_dir, ignore_errors=True)
    plots_dir.mkdir(parents=True, exist_ok=True)
    return plots_dir


def run_target(target: str, cfg: Config, opts: RunOptions, cache: Cache, console: Console,
               deps: PipelineDeps = PipelineDeps()) -> TargetOutcome:
    session = make_session(cfg.sources.timeout_s)
    parsed = parse_target(target)
    try:
        identity = resolve(parsed, deps.lookup(cache, session, cfg), cfg.resolve)
    except ResolutionError as exc:
        return TargetOutcome(target, "unresolved", message=str(exc))

    ctx = SourceContext(identity=identity, config=cfg, cache=cache, session=session,
                        ads_token=ads_token())
    results = run_sources(deps.sources(cfg), ctx)
    for name, res in results.items():
        origin = ("cache" if res.provenance and res.provenance.from_cache else "live"
                  if res.provenance else "-")
        log.info("source %s: %s (%s, %.1f s)", name, res.status, origin, res.elapsed_s or 0.0)

    report = Report(
        tool_version=__version__,
        generated=datetime.now(timezone.utc),
        identity=identity,
        results=results,
        parameters=parameter_rows(results, cfg.parameters),
        flags=flags(identity, results, cfg.flags),
        llm=LLMBlock(status="skipped", reason=LLM_DISABLED_REASON),
    )

    slug = slugify(display_name(identity.main_name))
    out = opts.out_dir / slug
    plots_dir = _fresh_plots_dir(out, opts.out_dir, slug)
    if cfg.plots.enabled:
        plots, plot_notes = deps.plots(report, plots_dir, cfg, cache)
        report = report.model_copy(update={"plots": plots, "plot_notes": plot_notes})
    if cfg.llm.enabled:
        report = report.model_copy(update={"llm": deps.writer(report, cfg, cache)})

    report_path = write_outputs(report, out)
    print_summary(report, report_path, console)
    return TargetOutcome(target, "ok", report_path=report_path, report=report)
