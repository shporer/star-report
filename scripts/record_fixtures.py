"""Record raw source payloads or resolver lookups as test fixtures. Runs live; not part of pytest.

    python scripts/record_fixtures.py [--source NAME]... [--star SLUG]...
    python scripts/record_fixtures.py --lookups [--star SLUG]...
    python scripts/record_fixtures.py --claude-code
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from conftest import FIXTURES_DIR, all_ref_stars, make_ctx, ref_identity, ref_star  # noqa: E402

from star_report.cache import Cache  # noqa: E402
from star_report.config import load_config  # noqa: E402
from star_report.resolve import (  # noqa: E402
    LiveLookup,
    RecordingLookup,
    ResolutionError,
    parse_target,
    resolve,
)
from star_report.sources.base import make_session  # noqa: E402
from star_report.sources.registry import build_sources  # noqa: E402
from star_report.writer import (  # noqa: E402
    SYSTEM_PROMPT,
    WriterFailure,
    build_user_message,
    parse_claude_code_output,
    run_claude_code,
)


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


def record_lookups(slugs: list[str]) -> int:
    """Resolve every spelling of each star live and write tests/fixtures/lookups/<slug>.json.

    One temporary cache serves the whole run, so a query repeated across spellings is sent once.
    """
    cfg = load_config()
    failures = 0
    with tempfile.TemporaryDirectory(prefix="star-report-record-") as tmp:
        cache = Cache(Path(tmp), cfg.cache.expiry_days, cfg.cache.empty_expiry_days)
        live = LiveLookup(cache, make_session(cfg.sources.timeout_s), cfg)
        for slug in slugs:
            star = ref_star(slug)
            recorder = RecordingLookup(live)
            for spelling in star["spellings"]:
                label = f"lookups/{slug} {spelling!r}"
                try:
                    ident = resolve(parse_target(spelling), recorder, cfg.resolve)
                except ResolutionError as e:
                    failures += 1
                    print(f"ERROR    {label}: {e}")
                    continue
                matched = (ident.tic_id, ident.gaia_dr3_id) == (star["tic"], star["gaia_dr3"])
                failures += not matched
                print(f"{'resolved' if matched else 'MISMATCH'} {label}: TIC {ident.tic_id}, "
                      f"Gaia DR3 {ident.gaia_dr3_id}, via {ident.resolved_via}")
                for warning in ident.warnings:
                    print(f"         warning: {warning}")
            for error in recorder.errors:  # a failed call would replay as "no match"
                failures += 1
                print(f"ERROR    lookups/{slug}: {error}")
            path = FIXTURES_DIR / "lookups" / f"{slug}.json"
            recorder.dump(path)
            print(f"recorded lookups/{slug} -> {path.relative_to(ROOT)} "
                  f"({len(recorder.calls)} calls)")
    return failures


def record_claude_code() -> int:
    """Send one short writer request through `claude -p` (the user's Claude Code login).

    Writes the raw result to tests/fixtures/claude_code/success.json. No papers are sent.
    """
    cfg = load_config()
    facts = {"name": "HD 209458", "description": "G0 V star, V = 7.65, host of the transiting "
             "hot Jupiter HD 209458 b (period 3.52475 d)"}
    user = build_user_message(facts, [], "no papers were searched for this recording")
    try:
        rc, out, err = run_claude_code(cfg.llm, SYSTEM_PROMPT, user)
        parse_claude_code_output(rc, out, err, default_model=cfg.llm.model)
        payload = json.loads(out)
    except WriterFailure as e:
        print(f"ERROR    claude_code/success: {e.reason}")
        return 1
    except ValueError:
        print("ERROR    claude_code/success: claude printed more than one JSON object")
        return 1
    if isinstance(payload, dict) and "credential_expires_at" in payload:
        payload["credential_expires_at"] = None   # account detail, not needed by the parser
    path = write_fixture("claude_code", "success", payload)
    print(f"recorded claude_code/success -> {path.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--star", action="append", default=[], metavar="SLUG",
                        help="reference star slug (repeatable; default: all)")
    parser.add_argument("--source", action="append", default=[], metavar="NAME",
                        help="source name (repeatable; default: all)")
    parser.add_argument("--lookups", action="store_true",
                        help="record resolver lookups for every spelling, not source payloads")
    parser.add_argument("--claude-code", action="store_true",
                        help="record one `claude -p` writer reply (uses your Claude Code login)")
    args = parser.parse_args(argv)
    if args.claude_code:
        if args.source or args.lookups or args.star:
            parser.error("--claude-code takes no other options")
        return record_claude_code()

    known = [s["slug"] for s in all_ref_stars()]
    unknown = [s for s in args.star if s not in known]
    if unknown:
        parser.error(f"unknown star(s): {', '.join(unknown)}; known: {', '.join(known)}")
    if args.lookups:
        if args.source:
            parser.error("--lookups records resolver lookups; it takes no --source")
        return 1 if record_lookups(args.star or known) else 0
    return 1 if record_sources(args.star or known, args.source) else 0


if __name__ == "__main__":
    sys.exit(main())
