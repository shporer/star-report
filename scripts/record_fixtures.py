"""Record raw source payloads as test fixtures. Runs live; not part of pytest.

    python scripts/record_fixtures.py [--source NAME]... [--star SLUG]...
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from conftest import FIXTURES_DIR, all_ref_stars, make_ctx, ref_identity  # noqa: E402

from star_report.config import load_config  # noqa: E402
from star_report.sources.registry import build_sources  # noqa: E402


def write_fixture(source: str, slug: str, payload) -> Path:
    path = FIXTURES_DIR / source / f"{slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    return path


def record_sources(slugs: list[str], source_names: list[str] | None) -> int:
    cfg = load_config()
    sources = [s for s in build_sources(cfg) if not source_names or s.name in source_names]
    failures = 0
    with tempfile.TemporaryDirectory(prefix="star-report-record-") as tmp:
        for slug in slugs:
            ctx = make_ctx(identity=ref_identity(slug), cfg=cfg, cache_dir=Path(tmp))
            for src in sources:
                label = f"{src.name}/{slug}"
                reason = src.skip_reason(ctx)
                if reason:
                    print(f"skipped  {label}: {reason}")
                    continue
                try:
                    path = write_fixture(src.name, slug, src.fetch(ctx))
                    print(f"recorded {label} -> {path.relative_to(ROOT)}")
                except Exception as e:  # noqa: BLE001 - keep going after errors
                    failures += 1
                    print(f"ERROR    {label}: {type(e).__name__}: {e}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--star", action="append", default=[], metavar="SLUG",
                        help="reference star slug (repeatable; default: all)")
    parser.add_argument("--source", action="append", default=[], metavar="NAME",
                        help="source name (repeatable; default: all)")
    args = parser.parse_args(argv)

    known = [s["slug"] for s in all_ref_stars()]
    unknown = [s for s in args.star if s not in known]
    if unknown:
        parser.error(f"unknown star(s): {', '.join(unknown)}; known: {', '.join(known)}")
    return 1 if record_sources(args.star or known, args.source) else 0


if __name__ == "__main__":
    sys.exit(main())
