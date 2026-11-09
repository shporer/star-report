# star-report

CLI that takes a star name, ID or coordinates, gathers catalog, archive, light-curve and literature
data, and writes an HTML report, `data.json` and a terminal summary, with an optional Claude-written
overview. Built for Avi Shporer (MIT, observational exoplanet science) and colleagues; target stars
are mostly TESS host stars and candidates. See README.md for user-facing docs.

## Project layout

```
star-report/
├── CLAUDE.md, README.md
├── pyproject.toml
├── src/star_report/
│   ├── cli.py            Argument parsing, target loop.
│   ├── pipeline.py       Resolve -> run sources -> derive -> plots -> LLM -> render.
│   ├── config.py         Loads config.yaml (packaged defaults + local override, deep-merged).
│   ├── config.yaml       Packaged defaults. All tunables.
│   ├── cache.py          Per-group expiry cache (~/.cache/star-report).
│   ├── models.py         Data classes.
│   ├── coords.py, resolve.py   Target parsing and cross-matching to Gaia DR3 / TIC.
│   ├── derive.py         Parameter comparison, disagreement and quality flags.
│   ├── writer.py         Claude-written overview and literature summary.
│   ├── sources/          One module per service, plus base.py and registry.py.
│   ├── plots/            cmd, lightcurve, sky.
│   └── render/           html.py, terminal.py, templates/ (Jinja partials).
├── scripts/record_fixtures.py   Records live payloads into tests/fixtures/.
├── tests/                Offline tests, fixtures/, reference_stars.yaml, test_live.py.
├── reports/              Output (git-ignored).
└── config.yaml           Optional local override (git-ignored).
```

## Key commands

- `.venv/bin/star-report "HD 209458"`: full report. Add `--no-llm`, `--no-plots`, `--refresh`, `-v`.
- `.venv/bin/star-report --from-file targets.txt --no-open`: batch.
- `.venv/bin/pytest -q`: offline suite (sockets blocked).
- `.venv/bin/pytest -m live`: real services; slow; needs `ADS_DEV_KEY` and `ANTHROPIC_API_KEY`.
  Timeouts from Gaia, MAST or the VizieR mirror are transient: re-run first.
- `python scripts/record_fixtures.py --source NAME [--star SLUG]` or `--lookups`: re-record fixtures.

## Configuration

All tuning lives in `src/star_report/config.yaml`. Never hardcode radii, timeouts, thresholds, catalog
IDs, model names or expiry in Python. A local `./config.yaml` or `--config` file holds only changed
keys and is deep-merged over the defaults.

## Behavioral rules

1. **Tunables only in config.yaml.**
2. **Secrets only in the environment**: `ADS_DEV_KEY`, `ANTHROPIC_API_KEY`. Never in code, config,
   fixtures, logs or docs.
3. **Unit tests never touch the network.** Record fixtures with `scripts/record_fixtures.py` and
   test parsers against them, including malformed payloads.
4. **Respect services.** No query loops; honor ADS rate-limit headers; use the cache. Don't add
   retries that hammer a service that is down.
5. **Never commit** `reports/`, a local `config.yaml`, `.DS_Store` or `.superpowers/`.
6. **Python 3.11 compatibility**: no 3.12-only syntax (e.g. nested-quote f-strings).
7. **Coerce every value interpolated into ADQL or a URL**, and make parsers tolerate malformed payloads.
8. **Network work that can hang runs in a daemon thread**, never in a ThreadPoolExecutor the
   interpreter joins at exit.
9. **The LLM writer must never crash the report.** Failures degrade to a stated reason in the report.

## Adding a source

1. A `Source` subclass in `sources/` (fetch, parse, query, name, group, service).
2. Register it in `registry.build_sources`, in report order.
3. Record fixtures, then write parser tests against them (include malformed shapes).
4. Add a template partial, or extend one, and a terminal line if needed.
5. Add `sources.enabled.<name>` to `config.yaml` (plus a radius, catalog ID, timeout override or
   cache group if needed).

## When the user asks to change behavior

- Radii, timeouts, enabled sources, cache expiry, thresholds, plot settings, literature limits:
  edit `config.yaml`.
- LLM model, effort or token limit: `llm:` in `config.yaml`. Prompt and output handling: `writer.py`.
- Server mirrors: `servers:` in `config.yaml`.
- New data product: add a source (above). New report section: a template partial in `render/templates/`.
- Changing flag or disagreement logic: `derive.py`, with thresholds from config.

## Things to remember

- CDS hosts (SIMBAD, VizieR, alasky) were unreachable on 2026-11-09. Config lists the Harvard mirrors
  second; the first host accepting a TCP connection is used. The sky image falls back from hips2fits
  to STScI DSS, then to a Gaia-only neighbor chart.
- The Harvard VizieR mirror can silently return empty tables, and the wide-binary catalog depends on
  row_limit. Empty results are rechecked after `cache.empty_expiry_days`.
- ADS search rejects `object:`. The name is translated via `/v1/objects/query` first, with an
  abstract-phrase fallback.
- KOA goes through its TAP service with pyvo, not pykoa.
- MAST needs a 90 s timeout for TESS continuous viewing zone stars (`sources.timeout_overrides`).
- The LLM call was never run live during development (no Anthropic credentials). The first real run
  is unverified; expect to fix surprises there. LLM output is cached forever; `--refresh` regenerates.
- Cost is about $0.10-0.25 per report with the default model and effort.
- The CMD background (50k Gaia stars) is cached indefinitely.
