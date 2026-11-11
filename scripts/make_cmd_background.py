"""Rebuild the CMD background that ships with star-report, from the Gaia DR3 archive.

    python scripts/make_cmd_background.py [--timeout SECONDS]

Runs plots.cmd.BACKGROUND_ADQL for `plots.cmd_background_rows` stars (an asynchronous TAP job)
and writes src/star_report/data/cmd_background.csv.gz. Gaia DR3 is a fixed release and the query
orders by random_index, so the file changes only when the query or the row count does.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from star_report.config import load_config
from star_report.plots import cmd
from star_report.sources.base import call_with_timeout, make_session

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "star_report" / "data" / cmd.BACKGROUND_FILE


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=float, default=float(cfg.plots.cmd_background_timeout_s),
                        help="seconds to wait for the archive (default: "
                             "plots.cmd_background_timeout_s)")
    args = parser.parse_args(argv)
    n = int(cfg.plots.cmd_background_rows)
    session = make_session(cfg.sources.timeout_s)
    status, rows = call_with_timeout(lambda: cmd.query_background(session, n), args.timeout,
                                     name="cmd-background")
    if status != "ok":
        print(f"Gaia query failed: {type(rows).__name__}: {rows}", file=sys.stderr)
        return 1
    if len(rows) != n:
        print(f"Gaia returned {len(rows)} usable stars, expected {n}; file not written.",
              file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    cmd.save_background(rows, OUT)
    print(f"wrote {len(rows)} stars to {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
