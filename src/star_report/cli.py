"""The `star-report` command line."""

from __future__ import annotations

import argparse
import logging
import webbrowser
from collections.abc import Sequence
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from star_report import __version__, pipeline
from star_report.cache import Cache
from star_report.config import ConfigError, load_config
from star_report.pipeline import PipelineDeps, RunOptions, TargetOutcome


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="star-report",
        description="Resolve a star name or ID, gather catalog data, and write an HTML report.",
    )
    p.add_argument("targets", nargs="*", metavar="TARGET",
                   help="star name or ID: 'HD 209458', 'TIC 420814525', 'TOI-700', coordinates")
    p.add_argument("--from-file", type=Path, metavar="PATH",
                   help="read targets from a file, one per line ('#' comments allowed)")
    p.add_argument("--out", type=Path, metavar="DIR", help="output directory (default: config output.dir)")
    p.add_argument("--no-llm", action="store_true", help="skip the LLM-written sections")
    p.add_argument("--no-plots", action="store_true", help="skip plots")
    p.add_argument("--refresh", action="store_true", help="ignore cached data and re-query")
    p.add_argument("--open", action=argparse.BooleanOptionalAction, default=None,
                   help="open the report in a browser (default: only for a single target)")
    p.add_argument("--config", type=Path, metavar="PATH", help="config file to merge over the defaults")
    p.add_argument("-v", "--verbose", action="store_true", help="log one line per source")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def read_targets_file(path: Path) -> list[str]:
    targets = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            targets.append(line)
    return targets


def _setup_logging(console: Console) -> None:
    logger = logging.getLogger("star_report")
    logger.setLevel(logging.INFO)
    logger.addHandler(RichHandler(console=console, show_path=False, markup=False))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console()

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}", markup=True, highlight=False)
        return 2

    targets = list(args.targets)
    if args.from_file is not None:
        try:
            targets += read_targets_file(args.from_file)
        except OSError as exc:
            console.print(f"Cannot read targets file {args.from_file}: {exc}", markup=False)
            return 2
    if not targets:
        parser.error("give at least one TARGET or --from-file")

    if args.no_llm:
        cfg.llm.enabled = False
    if args.no_plots:
        cfg.plots.enabled = False
    if args.verbose:
        _setup_logging(console)

    opts = RunOptions(out_dir=args.out or cfg.output.dir, refresh=args.refresh, verbose=args.verbose)
    cache = Cache(cfg.cache.dir, cfg.cache.expiry_days, cfg.cache.empty_expiry_days,
                  refresh=args.refresh)
    deps = PipelineDeps()

    outcomes: list[TargetOutcome] = []
    for target in targets:
        try:
            outcome = pipeline.run_target(target, cfg, opts, cache, console, deps)
        except Exception:  # noqa: BLE001 - one bad target must not stop the batch
            console.print_exception()
            outcome = TargetOutcome(target, "crashed", message="unexpected error")
        if outcome.status == "unresolved":
            console.print(f"{target}: {outcome.message}", markup=False, highlight=False)
        elif outcome.status == "crashed":
            console.print(f"{target}: crashed ({outcome.message})", markup=False, highlight=False)
        outcomes.append(outcome)

    if args.open or (args.open is None and len(targets) == 1 and cfg.output.open_single):
        for outcome in outcomes:
            if outcome.status == "ok" and outcome.report_path is not None:
                webbrowser.open(outcome.report_path.resolve().as_uri())

    return 0 if all(o.status == "ok" for o in outcomes) else 1
