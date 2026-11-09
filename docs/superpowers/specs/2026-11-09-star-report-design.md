# star-report: design spec

- **Date:** 2026-11-09
- **Status:** Design approved in chat. This written spec is awaiting review.
- **Owner:** Avi Shporer (MIT)

## 1. Purpose

You give the tool a star's name or catalog ID. It gathers what major catalogs,
archives, and the literature say about that star and writes a short report.

The users are Avi and his colleagues, for research. Typical uses are a quick
look at a new TOI, an observing target, or a star named in a paper or proposal.

The first version is a command-line tool. A web app may come later and will
reuse the same core code.

## 2. Scope

### In v1

- One command, `star-report <target>`, writes an HTML report and a `data.json`
  file and prints a short summary in the terminal.
- Several targets per run, given as arguments or from a file. They run one
  after another.
- The report has these sections:
  - **Core:** identifiers, coordinates, and stellar parameters.
  - **Planets, binarity, and variability.**
  - **Data inventory.**
  - **Quick-look plots.**
  - **Literature summary.**
  - **Overview:** written by an LLM.
- The tool keeps working when some sources fail. Responses are cached on disk.
  `--no-llm` gives a fully reproducible report.

### Not in v1

- Any web app or hosting. The core stays independent of the CLI so a web app
  can be added later.
- Downloading or reducing TESS full-frame images (TESScut) or doing custom
  photometry. Plots use only light-curve products that already exist.
- Light-curve fitting, vetting, or period searches beyond a quick periodogram.
- Running several targets in parallel.
- Proprietary data, and archives that require a login.

## 3. Command-line interface

```
star-report TARGET [TARGET ...] [options]
star-report --from-file targets.txt [options]

  --out DIR           output directory (default: ./reports)
  --no-llm            skip the LLM-written overview and literature summary
  --no-plots          skip quick-look plots
  --refresh           ignore cached responses and refetch everything
  --open / --no-open  open the HTML report in the browser
                      (default: open when there is a single target)
  --config PATH       config file (default: ./config.yaml if present,
                      otherwise the packaged defaults)
  -v, --verbose       log each source query and its timing
```

In a targets file, each line holds one target. Lines starting with `#` and
blank lines are ignored.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Every target produced a report, even if some sources failed. |
| 1 | At least one target could not be resolved. The other targets still get their reports. |
| 2 | Usage or configuration error. |

## 4. Architecture

Each run goes through five stages, one target at a time:

1. **Resolve:** Turn the input into a single `StarIdentity` (see section 6).
2. **Gather:** Run every enabled source in parallel. Each source has its own
   timeout and returns a `SourceResult`.
3. **Plot:** Make the quick-look figures. This stage needs the identity and
   some gathered data.
4. **Write:** Claude writes the overview and the literature summary
   (section 8).
5. **Render:** Write the HTML report, `data.json`, the plot PNGs, and the
   terminal summary.

Each source is its own module and implements one shared interface. Adding,
removing, or disabling a source never touches the other modules.

### Project layout

```
star-report/
├── pyproject.toml              # package metadata; `star-report` console script
├── config.yaml                 # default settings (also packaged)
├── CLAUDE.md                   # project briefing for Claude Code
├── README.md
├── src/star_report/
│   ├── cli.py                  # arguments, batch loop, exit codes
│   ├── pipeline.py             # runs the five stages for one target
│   ├── config.py               # loads config.yaml and environment variables
│   ├── cache.py                # disk cache with per-source expiry
│   ├── models.py               # StarIdentity, SourceResult, Report
│   ├── resolve.py              # input parsing and identity resolution
│   ├── sources/
│   │   ├── base.py             # Source interface; runner with timeouts
│   │   ├── simbad.py
│   │   ├── gaia.py
│   │   ├── tic.py
│   │   ├── exoplanet_archive.py
│   │   ├── exofop.py
│   │   ├── vizier.py           # WDS, SB9, VSX, wide binaries, APOGEE, LAMOST, HARPS RV bank
│   │   ├── mast.py             # mission observations and HLSP light curves
│   │   ├── tess_pointing.py    # past and upcoming TESS sectors (tess-point)
│   │   ├── eso.py
│   │   ├── koa.py
│   │   └── ads.py
│   ├── plots/
│   │   ├── lightcurve.py       # light curve and periodogram
│   │   ├── cmd.py              # Gaia color-magnitude diagram
│   │   └── sky.py              # sky cutout with neighbors and TESS pixel grid
│   ├── writer.py               # Claude overview and literature summary
│   └── render/
│       ├── html.py
│       ├── terminal.py
│       └── templates/report.html.j2
└── tests/
    ├── fixtures/               # recorded raw responses for offline tests
    ├── reference_stars.yaml    # live-test targets and their expected IDs
    └── ...
```

The project needs Python 3.11 or later. Its main dependencies are `astroquery`,
`astropy`, `numpy`, `matplotlib`, `lightkurve`, `tess-point`, `pyvo`, `pykoa`,
`requests`, `jinja2`, `pyyaml`, `rich`, `anthropic`, and `pydantic`. Tests use
`pytest`.

## 5. Data model

- **`StarIdentity`** holds:
  - The input string.
  - The main name.
  - The SIMBAD ID, TIC ID, Gaia DR3 ID, and Gaia DR2 ID, each optional.
  - The ICRS position at Gaia epoch 2016.0, with proper motion. For a star
    with no Gaia match, such as some very bright stars, SIMBAD's J2000
    position is used instead, and the identity records which one it has.
  - Other identifiers, keyed by catalog.
  - How the identity was resolved.
  - A list of warnings.
- **`SourceResult`** holds:
  - The source name.
  - A status: `ok`, `empty`, `error`, `timeout`, or `skipped`.
  - The data, which must be JSON-serializable.
  - Provenance: the service, the query or URL, the catalog version, the
    retrieval time, and whether the data came from the cache.
  - An error message, if any.
  - The elapsed time.
- **`Report`** holds:
  - The identity.
  - The results, keyed by source.
  - Derived tables: parameter rows and flags.
  - The plot file paths.
  - The LLM block: either its output or the reason it was skipped.

`data.json` is the serialized `Report`. It carries a `schema_version` field,
starting at 1.

## 6. Identity resolution

1. **Parse the input.** Regular expressions recognize prefixed IDs. TIC, Gaia
   DR3, Gaia DR2, TOI (with or without a `.01` planet suffix), KOI, KIC,
   EPIC, HD, HIP, HR, GJ, 2MASS, and coordinate strings such as "RA Dec" in
   sexagesimal or degrees are parsed directly. Anything else is treated as a
   name. A trailing planet letter, as in "WASP-33 b", is stripped and the
   host star is used.
2. **Query the catalog that owns the ID.**

   | Input | Catalog used | What it yields |
   |---|---|---|
   | TIC | TIC v8.2 at MAST | Position and the Gaia DR2 ID |
   | Gaia DR3 | Gaia archive | Position, proper motion, and parallax |
   | Gaia DR2 | `gaiadr3.dr2_neighbourhood` | The DR3 ID |
   | TOI | TOI table | The host's TIC ID |
   | KOI | Kepler KOI table | The host's KIC ID, which then goes to SIMBAD |
   | Coordinates | Gaia DR3 | The nearest source within 5″ |

   This way, faint stars that have no SIMBAD entry still resolve.
3. **Send everything else to SIMBAD by name.** This covers HD, HIP, 2MASS, KIC,
   EPIC, Kepler-, K2-, WASP-, HAT-P-, and KELT- names, and common names.
4. **Complete the identity by cross-matching.**
   - Take the Gaia DR3 and TIC IDs from SIMBAD's identifier list when they are
     there.
   - Otherwise, run a Gaia DR3 cone search at the SIMBAD position, propagated
     to epoch 2016.0. Then look up the TIC entry by its Gaia DR2 ID, falling
     back to a TIC cone search.
5. **Run consistency checks.** Each failed check becomes a warning shown at the
   top of the report and in the terminal:
   - The TIC's Gaia DR2 ID maps to a different DR3 source than SIMBAD gives.
   - More than one Gaia DR3 source lies within 2″ with ΔG < 3. The target may
     be a close pair, so the match is ambiguous.
   - The TIC entry is flagged as a duplicate or artifact.
   - The SIMBAD object is the parent entry of a multiple system. The warning
     names which component was used.
   - The name matched several SIMBAD objects.

If nothing resolves, the tool exits with code 1. The message lists each method
it tried and suggests adding a catalog prefix.

**Positions and proper motion:** Cone searches use the Gaia DR3 position
propagated to the epoch of the catalog being queried. Most VizieR catalogs use
J2000. This keeps high-proper-motion stars such as Barnard's Star matched
correctly. Default cone radii are set per source in `config.yaml`.

## 7. Sources

### Core: identity and parameters

| Source | Access | Fields used |
|---|---|---|
| SIMBAD | `astroquery.simbad` | Main ID, all identifiers, object type, spectral type, B and V magnitudes, RV, reference count |
| Gaia DR3 `gaia_source` | `astroquery.gaia` (TAP) | Parallax, proper motion, G/BP/RP, RUWE, `radial_velocity`, `has_rvs`, `phot_variable_flag`, `non_single_star` |
| Gaia DR3 `astrophysical_parameters` | `astroquery.gaia` (TAP) | Teff, logg, and [M/H] from GSP-Phot and GSP-Spec; radius, mass, age, and luminosity from FLAME |
| Bailer-Jones distances (`external.gaiaedr3_distance`) | `astroquery.gaia` (TAP) | Geometric and photogeometric distances with their intervals |
| TIC v8.2 | `astroquery.mast` Catalogs | Teff, logg, radius, mass, Tmag, contamination ratio, disposition flags |

### Planets, binarity, and variability

| Source | Access | Fields used |
|---|---|---|
| NASA Exoplanet Archive | TAP | `pscomppars` (confirmed planets), `toi`, `cumulative` (KOIs), `k2pandc` |
| ExoFOP-TESS target page | JSON over HTTP; TIC targets only | TOIs and CTOIs with dispositions, follow-up observation lists (imaging, spectroscopy, time series), notes |
| Gaia DR3 binarity | TAP | RUWE; `nss_two_body_orbit` solutions |
| Gaia wide binaries (El-Badry et al. 2021) | VizieR `J/MNRAS/506/2269` | Companion source ID, separation, chance-alignment probability |
| WDS | VizieR `B/wds/wds` | Visual companions: separation, position angle, magnitudes |
| SB9 | VizieR `B/sb9` | Spectroscopic binary orbits |
| VSX | VizieR `B/vsx/vsx` | Variable type and period |
| Gaia DR3 variability | TAP: `vari_summary`, `vari_classifier_result` | Variability class and best period, where one exists |

### Data inventory

Nothing is downloaded in this section. It lists what exists and links to it.

| Source | Access | Fields used |
|---|---|---|
| MAST observations | `astroquery.mast` Observations | See the list below |
| TESS pointing | `tess-point` | Every sector whose footprint covers the target, past and upcoming |
| ExoFOP follow-up | The ExoFOP target JSON (above) | Counts and lists of imaging, spectroscopy, and time-series uploads |
| ESO archive | TAP (`ivoa.ObsCore`) via `pyvo` | Reduced (phase 3) spectra from HARPS, ESPRESSO, FEROS, UVES, and others |
| Keck KOA | `pykoa`, public data only | HIRES and other instruments' observations |
| HARPS RV bank | VizieR `J/A+A/636/A74` | Number of RVs and time span |
| Survey spectra | VizieR and Gaia | See the list below |

- **MAST observations:**
  - TESS SPOC 2-min and 20-s data, by sector.
  - Kepler and K2.
  - HST and JWST.
  - Other missions.
  - HLSP light curves: QLP, TESS-SPOC, and others.
- **Survey spectra:**
  - APOGEE DR17, from VizieR `III/286`.
  - LAMOST, from the latest data release available in VizieR.
  - Gaia RVS, from `has_rvs`.

### Quick-look plots

- **Light curve and periodogram:**
  - **Download:** `lightkurve` fetches existing products in this order of
    preference: SPOC 2-min for TESS, then TESS-SPOC FFI, then QLP, then
    PDCSAP for Kepler and K2. At most `plots.max_sectors` sectors are
    downloaded (default 10). When more exist, the most recent are used and
    the report says so.
  - **Light-curve plot:** Each sector is normalized and drawn in its own
    panel with its sector label.
  - **Periodogram:** A Lomb-Scargle periodogram of the stitched light curve,
    with periods of known planets and TOIs marked.
- **Color-magnitude diagram:** The target is shown in absolute G against BP−RP,
  over a background of nearby Gaia DR3 stars (parallax > 10 mas, with quality
  cuts). The background is fetched once and cached locally.
- **Sky image:**
  - **Image:** A 5′ × 5′ hips2fits cutout, DSS2 by default, configurable.
  - **Overlays:** Gaia DR3 neighbors brighter than the target's G + 6 are
    marked with their ΔG, along with a TESS 21″ pixel grid and a scale bar.

### Literature

- **ADS search API:**
  - **Query:** `object:"<main name>"`, so ADS resolves the object by its
    SIMBAD and NED identifiers.
  - **Fields:** bibcode, title, year, first three authors, citation count,
    abstract, and document type.
  - **Document types:** articles and e-prints only (`doctype:(article OR
    eprint)`, configurable). This drops meeting abstracts, proposals, and
    catalog entries.
  - **Limit:** up to `literature.max_records` records (default 200).
  - **Authentication:** needs `ADS_DEV_KEY`.
- **Papers given to the LLM:** the 15 most-cited papers plus the 10 most
  recent, with duplicates removed, for at most 25 papers. Both counts are
  configurable.
- **Paper list in the report:** the same 25 papers, linked to ADS, along with
  the total number of records ADS found.

## 8. LLM writer

- **Client:** The official `anthropic` Python SDK.
  - **Model:** `claude-opus-5-5` (setting `llm.model`), with adaptive
    thinking.
  - **Effort:** `high` (setting `llm.effort`).
  - **Credentials:** The SDK's usual lookup: `ANTHROPIC_API_KEY`, or a profile
    stored by `ant auth login`.
- **Input:** Claude never sees the raw source rows. It gets a compact fact
  sheet built from `data.json`:
  - The identity and key parameters with their sources.
  - Flags and warnings.
  - Planets and TOIs.
  - A summary of the data inventory.
  - The papers selected in section 7, each with bibcode, title, year, first
    author, and abstract.
- **Output:** Structured output validated against a Pydantic schema:
  - `overview`: 3–5 sentences.
  - `literature_summary`: one to three short paragraphs.
  - `cited_bibcodes`: a list of the bibcodes cited.

  Citations appear in the text as `[bibcode]`.
- **System prompt rules:**
  - Use only the material provided.
  - Never state a number that isn't in the fact sheet.
  - Say plainly when the literature is sparse or missing.
  - Keep a neutral, factual tone.
- **Validation:** Every `[bibcode]` in the text must be in the list of papers
  sent to Claude. On failure, the request is retried once with the error
  explained. If it fails again, the LLM sections are left out and the report
  says "LLM summary failed citation check". The paper list is still shown.
- **Failure handling:** Any of the following leaves the LLM sections out:
  - No credentials.
  - An API error after the SDK's retries.
  - A refusal (`stop_reason == "refusal"`). The server-side refusal fallback,
    `fallbacks: "default"`, is enabled.
  - A validation failure.

  In each case the rest of the report renders normally, and the reason the
  LLM sections are missing is shown in their place.
- **Labeling:** The overview and literature summary are marked "LLM-written"
  with the model ID. `data.json` records the model, effort, timestamp, and the
  bibcodes Claude received.
- **Caching:** LLM output is cached under a hash of the fact sheet, papers,
  model, and effort. Re-rendering a report doesn't trigger another API call.
  `--refresh` regenerates it.
- **Cost (estimate):** Roughly 15–20k input tokens and a few thousand output
  tokens, including thinking, per star. At `claude-opus-5-5` list prices
  ($4/$20 per million tokens as of October 2026), that is about $0.10–0.25 per
  report. The model and effort can be changed in `config.yaml`.

## 9. Report output

Each target gets `<out>/<slug>/`, containing `report.html`, `data.json`, and
`plots/*.png`. The slug comes from the main name; for example, `HD 209458`
becomes `HD_209458`. Re-running a target overwrites its folder. `data.json`
records when the report was generated.

### HTML

The report is one self-contained file built from a Jinja2 template. CSS is
inline, and plots are embedded as base64 PNGs. The first screen is the short
report: header, warnings, overview, key parameters, and flags. Longer detail
sits further down, in collapsible `<details>` blocks. From top to bottom:

1. **Header:** main name, key IDs (TIC, Gaia DR3, HD/HIP, 2MASS), coordinates,
   and a one-line description. For example: "G0V, V = 7.65, 48.3 pc".
2. **Warnings:** any warnings from resolution or the consistency checks.
3. **Overview:** LLM-written.
4. **Parameters:**
   - **Layout:** One row per quantity: Teff, logg, [M/H], radius, mass,
     luminosity, distance, age, and the V, G, and Tmag magnitudes. One column
     per source: Gaia, TIC, and SIMBAD. Each cell shows the value and its
     uncertainty. The Gaia column uses GSP-Spec values when they exist and
     GSP-Phot otherwise, and labels which one it shows.
   - **Disagreement:** A quantity is flagged when two sources differ by more
     than 3σ (combined) or, when uncertainties are missing, by more than 10%.
     Both thresholds are configurable.
5. **Planets and candidates, binarity, and variability:** tables, plus flags
   such as "RUWE 2.4: possible unresolved companion".
6. **Data inventory:** TESS sectors observed and upcoming, other missions,
   spectra and RVs, and ExoFOP follow-up.
7. **Plots.**
8. **Literature:** the LLM summary, then the paper list with links to ADS.
9. **Sources footer:** each source's status, whether its data came from the
   cache, the query time, and the catalog version.

The report keeps "no data found" (`empty`) distinct from "source unavailable"
(`error` or `timeout`). An empty result is evidence of absence. A failed
source isn't.

### Terminal

A `rich`-formatted summary showing:

- The name and IDs.
- Key parameters.
- Planets and TOIs.
- Flags and warnings.
- One status line per source.
- The path to the report.

## 10. Caching, concurrency, and rate limits

- **Cache location:** On disk under `~/.cache/star-report/`, configurable. Entries are
  keyed by source plus a hash of the normalized query, and store the raw response
  and its retrieval time.
- **Expiry by source** (configurable):

  | Source | Expiry |
  |---|---|
  | Gaia, TIC, VizieR catalogs | 90 days |
  | SIMBAD | 30 days |
  | Exoplanet Archive, TOI table, ExoFOP | 1 day |
  | MAST, ESO, KOA, ADS | 7 days |
  | CMD background sample | Never expires; replaced only by `--refresh` |

- **Light curves:** Cached by lightkurve's own download cache.
- **Parallel queries:** Sources run concurrently in a thread pool, with 8 workers
  by default. The default timeout is 30 s per source, and 120 s for light-curve
  downloads. A source that exceeds its timeout is recorded as `timeout`.
- **Dependencies between stages:** Resolution runs first. Sources that need a
  TIC ID, namely TIC and ExoFOP, are marked `skipped` when the star has none.
  Plots wait for the results they use. The LLM runs last.
- **ADS:** One search request per star, honoring ADS's rate-limit headers.
- **Requests:** Every HTTP request sends the user-agent `star-report/<version>`.

## 11. Configuration and secrets

`config.yaml` holds:

- Which sources are enabled.
- Timeouts, cone radii, and cache expiry times.
- Plot settings: maximum sectors, cutout size, and sky survey.
- Literature settings: maximum records, and how many most-cited and most-recent
  papers to use.
- LLM settings: enabled, model, effort, and `max_tokens`.
- Output defaults.

Secrets never go in config files. They come from the environment:
`ADS_DEV_KEY`, the same variable arxiv-agent uses, and the Anthropic
credentials described in section 8. A missing `ADS_DEV_KEY` disables only
the literature section. The report says so, and the overview is still written
from catalog data.

## 12. Testing

- **Default runs are offline:** `pytest` runs with no network access by
  default.
- **Source parsers:** Each source's parsing is tested against recorded raw
  responses in `tests/fixtures/`. A helper script captures these from the live
  services.
- **Resolver tests:**
  - A table of input spellings and the parse each should produce.
  - Resolution decisions, tested against fixtures.
  - Each consistency-check warning.
- **Rendering tests:** Each of these renders without errors:
  - A full report built from fixtures.
  - A report in which every source failed.
  - A `--no-llm` report.
  - A report with no ADS key.
- **Writer tests:** These use a stubbed client and make no API calls. They
  cover:
  - How the fact sheet and prompt are assembled.
  - Schema validation.
  - The citation check, with valid and invalid citations.
  - Retry-then-omit.
  - Each failure path in section 8.
- **Live tests:** These run only with `pytest -m live` and are skipped by
  default. They use the reference stars listed in `tests/reference_stars.yaml`,
  which stores each star's expected TIC and Gaia DR3 IDs:
  - **HD 209458:** bright, with many identifiers, a transiting hot Jupiter, and
    a large literature.
  - **TOI-700:** an M dwarf with several planets, in or near TESS's continuous
    viewing zone, so it has many sectors.
  - **Kepler-10:** in the Kepler field, with KOIs.
  - **Barnard's Star:** high proper motion, to test position propagation.
  - **Three stars picked by catalog query during implementation,** with the
    queries recorded in the YAML file:
    - A faint star (Tmag 14–16) that is in the TIC but has no SIMBAD entry.
    - A star with a Gaia DR3 SB1 orbit in `nss_two_body_orbit`.
    - A star that no TESS sector covers, checked with `tess-point`.
- **Input spellings:** For each reference star, the live tests also try several
  spellings of its name and IDs. For HD 209458 these are "HD 209458",
  "HD209458", its TIC ID with and without a space after "TIC", and its Gaia DR3
  ID.

## 13. Success criteria

- Every spelling of every reference star resolves to the expected TIC and
  Gaia DR3 IDs.
- A report renders for any combination of failed sources.
- **Speed:**
  - A first run with plots and the LLM takes about 1 minute for a typical star.
  - A repeat run from the cache takes under 5 seconds.
- No LLM text contains a bibcode outside the paper list it was given.
- With `--no-llm` and the same cache, repeated runs give identical `data.json`
  content apart from timestamps.

## 14. Build order

The tool is usable after each milestone, and each adds to what the previous
one built.

1. **Skeleton:** Package, config, cache, data model, and identity resolution.
   SIMBAD, Gaia, and TIC sources. An HTML report, `data.json`, and terminal
   summary covering the core section. This is already a useful lookup tool.
2. **Planets, binarity, and variability:** NASA Exoplanet Archive, ExoFOP,
   Gaia binarity and variability, WDS, SB9, VSX, and wide binaries.
3. **Data inventory:** MAST, tess-point, ESO, KOA, the HARPS RV bank, and
   survey spectra.
4. **Quick-look plots:** Light curve and periodogram, color-magnitude diagram,
   and sky image.
5. **Literature and LLM:** ADS, the writer, citation validation, and the
   overview.
6. **Finish:** The live reference-star tests, then the README and `CLAUDE.md`.

## 15. Risks

- **Changing or slow services:** Some services, especially KOA, the ESO TAP
  service, and ExoFOP's JSON, may change or respond slowly. Each sits behind
  its own module and can be disabled in `config.yaml`.
- **KOA logins:** If KOA turns out to require a login even for public
  metadata, KOA is dropped from v1.
- **Name spellings:** SIMBAD and ADS handle hyphens and spaces in host names
  inconsistently. The resolver normalizes common variants, and the live tests
  cover them.
- **Long light-curve histories:** Stars in TESS's continuous viewing zone have
  dozens of sectors. Downloads are capped by `plots.max_sectors`.

## 16. Later, not in v1

- A web app that reuses the pipeline and templates, possibly hosted for
  colleagues.
- A Claude Code `/star-report` command that wraps the CLI.
- More archives, such as DACE, NEID, SOPHIE, and CARMENES, and TESScut photometry.
- Running batch targets in parallel.
