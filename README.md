# star-report

Give it a star name, a catalog ID, or coordinates. It resolves the star, gathers catalog, archive,
light-curve and literature data from public services, and writes a self-contained HTML report, a
`data.json`, and a short terminal summary. An optional Claude-written overview and literature summary
sit on top of the data.

## What a report contains

- Identity: the resolved Gaia DR3, TIC and SIMBAD identifiers and the cross-match used.
- Parameters from several catalogs side by side, with disagreements and quality flags (RUWE,
  contamination) highlighted.
- Planets and TOIs: NASA Exoplanet Archive and ExoFOP.
- Companions: Gaia neighbors; Gaia DR3 non-single-star orbits, astrometric accelerations and RV
  trends; WDS, SB9, VSX, and the El-Badry wide-binary catalog.
- Observation inventory: MAST, TESS sector pointing, ESO, Keck KOA, HARPS RV bank, APOGEE, LAMOST.
- Plots: color-magnitude diagram on a Gaia background, TESS light curve and periodogram, and a sky
  image with neighbors marked.
- Literature: ADS records, with the most cited and most recent papers listed.
- Claude-written overview and literature summary (skipped with `--no-llm`).

## Install

Python 3.11 or newer.

```
cd star-report
python3.13 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

The `star-report` command is then at `.venv/bin/star-report`.

## Credentials

Keys live in the environment only.

- `ADS_DEV_KEY`: needed for the literature section (the same variable arxiv-agent uses).
- The LLM sections need one of:
  - `ANTHROPIC_API_KEY`, or a profile from `ant auth login` (the default, `llm.backend: api`),
    billed to an Anthropic API organization;
  - a logged-in Claude Code with `llm.backend: claude-code`: no API key, and the requests count
    against your Claude plan's usage. See "Claude Code backend" below.

  Without them the report still renders and states why those sections are missing.

## Usage

```
star-report "HD 209458"
star-report "TIC 150428135" --no-llm
star-report --from-file targets.txt --no-open
```

`targets.txt` has one target per line; `#` starts a comment.

| Option | Effect |
|---|---|
| `TARGET ...` | star name, ID (`HD 209458`, `TIC 420814525`, `TOI-700`) or coordinates |
| `--from-file PATH` | read targets from a file |
| `--out DIR` | output directory (default: `output.dir`, `reports`) |
| `--no-llm` | skip the LLM-written sections |
| `--no-plots` | skip plots |
| `--refresh` | ignore cached data and re-query (also regenerates the LLM text) |
| `--open` / `--no-open` | open the report in a browser (default: only for a single target) |
| `--config PATH` | config file to merge over the defaults |
| `-v`, `--verbose` | log one line per source |
| `--version` | print the version |

Typical timing: a cached rerun takes about 1 s. A cold run without the LLM takes about 40-80 s,
depending on service latency.

## Output layout

```
reports/<slug>/
  report.html    self-contained, open it directly
  data.json      everything gathered, machine-readable
  plots/*.png
```

Raw query results are cached in `~/.cache/star-report` (`cache.dir`), with a per-service expiry.

## Configuration

Defaults are in `src/star_report/config.yaml`. To change something, create a `./config.yaml` (or pass
`--config PATH`) containing only the keys you change; it is deep-merged over the defaults. For example:

```yaml
llm:
  effort: medium
sources:
  enabled: {koa: false}
```

Everything tunable is there: enabled sources, search radii, VizieR catalog IDs, cache expiry, timeouts,
resolver tolerances, flag thresholds, plot settings, literature limits and the LLM settings.
A local `config.yaml` is git-ignored. Every timeout and `sources.max_workers` must be positive; an
invalid value is a configuration error (exit code 2).

## Mirrors and known service issues

- SIMBAD and VizieR: `servers.simbad` and `servers.vizier` list CDS first and the Harvard mirrors
  second. Once per run, each host in turn gets one small query (`servers.probes`), and the first
  that answers HTTP 200 with a VOTable within `servers.probe_timeout_s` (5 s) is used. The Harvard
  mirrors take over when CDS is unreachable, and also when it accepts connections but serves error
  pages, as on 2026-11-10. If no host answers, the first one is used.
- Sky image: CDS hips2fits when alasky is reachable; otherwise, or if hips2fits fails, the STScI DSS
  cutout service. If both fail, the report shows a Gaia-only neighbor chart.
- The Harvard VizieR mirror sometimes silently returns empty tables, and for the wide-binary catalog
  whether a row comes back can depend on the row limit. Empty results are rechecked after
  `cache.empty_expiry_days` (1 day), whatever their group's expiry. VizieR queries bypass
  astroquery's own cache, so that recheck and `--refresh` really ask VizieR again.
- ESA Gaia archive outages (on 2026-11-10 it rejected queries for hours with HTTP 503, "synchronous
  job rejected"). If the archive cannot confirm the Gaia DR3 ID that SIMBAD lists, the report keeps
  that ID, shows it as "Gaia DR3 … (from SIMBAD, unconfirmed)", and opens with one warning: what
  was kept, that the position is SIMBAD's (J2000), which identity checks did not run, and the
  archive's error. Sources keyed by the ID still run: the wide-binary catalog and APOGEE query
  VizieR, and the Gaia sources try the archive once and use results already in the cache. Without
  a Gaia DR3 row, the color-magnitude diagram places the star from the Gaia DR2 magnitudes and
  parallax in the TIC, drawn as an open star and labelled as such. Run the star again once the
  archive is back; `data.json` marks the ID with `identity.gaia_dr3_unconfirmed`.
- ADS: the search API does not accept the `object:` field, so the name is first translated through
  ADS's `/v1/objects/query` endpoint and then searched with `doctype:(article OR eprint)`. If the
  translation fails, it searches for the exact phrase in abstracts. Each star gets two search
  requests: one sorted by citation count (up to `literature.max_records` records) for the most cited
  papers and the record count, and one sorted by date (`most_cited + most_recent` records) for the
  most recent papers.
- While CDS is down, ADS's own SIMBAD lookup fails too, so ADS matches papers by abstract text only
  and finds fewer. Those results stay cached for 7 days (`cache.expiry_days.ads`): after a CDS
  outage, rerun the affected stars with `--refresh`.
- KOA is queried through its public TAP service with pyvo (not pykoa).
- Timeouts: `sources.timeout_s` (30 s) by default, also for each lookup made while identifying the
  star; `sources.timeout_overrides` raises it per source.
  MAST is 90 s because its observation query can take about 45 s for stars in TESS's continuous
  viewing zone. Light-curve downloads use `plots.lightcurve_timeout_s` (120 s).
- The CMD background (50k Gaia stars) is fetched once and cached indefinitely. Its download is limited
  to `plots.cmd_background_timeout_s` (120 s); on a timeout the report leaves out the CMD with a note.

## Claude Code backend (no API key)

With `llm.backend: claude-code`, the two LLM sections are written by Claude Code's print mode
(`claude -p`) on your Claude Code login, so no API key or API credit is needed. Setup:

1. Install Claude Code, then log in once from a terminal: `claude auth login`, choosing the account
   whose plan should be used. `claude-opus-5-5` needs Claude Code 2.1.280 or newer (`claude update`);
   an older version is refused with an error that names the version.
2. Put this in a local `config.yaml`:

   ```yaml
   llm:
     backend: claude-code
   ```

claude gets the report's own system prompt and output schema, and the configured model and effort.
It runs with no tools, settings, plugins or MCP servers, saves no session, and runs in an empty
temporary directory. Anthropic API credentials are kept from it: `ANTHROPIC_API_KEY` and
`ANTHROPIC_AUTH_TOKEN` are not passed, and `ant auth login` profiles are hidden. Without a login of
its own, claude would otherwise use such a profile and bill that API organization. When star-report
itself runs inside a Claude Code session, that session's own `CLAUDE*` and `ANTHROPIC_*` variables
are left out too.

`llm.claude_code.timeout_s` (600 s) limits each call; claude is stopped at the deadline.
`llm.claude_code.command` names the executable (default `claude`). A missing or logged-out Claude
Code, a usage limit or a timeout appears in the report as the reason the sections are missing.
Failures are not cached, so a later run fills them in. Each star is one large request (two if the
first answer cites a paper outside the list), so a long batch can reach the plan's usage limit
partway through.

## LLM cost

With the API backend, about $0.10-0.25 per report; the Claude Code backend counts against your
plan's usage instead. The model and effort are under `llm:` in the config (defaults:
`claude-opus-5-5`, `high`). For the API backend only, so are the token limit, the server-side
refusal fallback (on), the client's per-request timeout `llm.timeout_s` (300 s) and its retry count
`llm.max_retries` (1). The output is cached and never expires, so re-rendering is free;
`--refresh` regenerates it. The Claude Code backend has been run live; the API backend has not
(no API credit was available), so its first real run is also its first live test. Structured
outputs (`output_config.format`) are sent without a beta header. If that first API call fails with a
`BadRequestError` about the output format (the report shows it where the overview would be), please
report it.

## Tests

```
.venv/bin/pytest            # offline; sockets are blocked
.venv/bin/pytest -m live    # real services; slow
```

Unit tests never touch the network; they run against recorded fixtures in `tests/fixtures/`. The live
suite (reference stars in `tests/reference_stars.yaml`) needs `ADS_DEV_KEY` and `ANTHROPIC_API_KEY`
set, and can fail when ESA Gaia, MAST or the VizieR mirror time out: re-run before debugging. The live
LLM tests (`test_llm_end_to_end` and `test_cold_run_time_with_llm`) skip when `ANTHROPIC_API_KEY` or
`ADS_DEV_KEY` isn't set; an `ant auth login` profile alone doesn't enable them.
`test_llm_end_to_end_claude_code` runs the writer through Claude Code instead; it skips unless
`ADS_DEV_KEY` is set and `claude auth status` reports a login.

Re-record fixtures from the live services:

```
python scripts/record_fixtures.py --source NAME [--star SLUG]
python scripts/record_fixtures.py --lookups [--star SLUG]
python scripts/record_fixtures.py --claude-code   # one writer reply via your Claude Code login
```
