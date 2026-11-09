# star-report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `star-report`, a command-line tool that takes a star's name or catalog ID and writes three outputs: a self-contained HTML report, a `data.json` file, and a terminal summary. The data comes from catalogs, archives, light curves, and the literature, and Claude writes an overview.

**Architecture:** Each target goes through one pipeline: resolve → gather → plot → write → render.
- Every data source is a small class. Its `fetch` method does the network call and returns a raw payload that can be serialized to JSON. Its `parse` method turns that payload into report data without any network access.
- A runner executes the sources in a thread pool, with a timeout per source, a disk cache, and provenance records.
- The pure logic is tested offline against recorded or hand-written fixtures: parsing, resolution decisions, derived tables, prompt building, and rendering.
- Only `pytest -m live` touches the network.

**Tech Stack:** Python ≥ 3.11 · astroquery · astropy · pyvo · lightkurve · tess-point · numpy · matplotlib · requests · pydantic 2 · Jinja2 · rich · anthropic ≥ 2.6 · pytest + pytest-socket

**Spec:** `docs/superpowers/specs/2026-11-09-star-report-design.md`. Read it before starting; this plan argues from it.

## Global Constraints

- **Python and packaging:**
  - Python ≥ 3.11.
  - The package `star_report` lives under `src/`.
  - The console script is `star-report = "star_report.cli:main"`.
  - The version is `0.1.0`, defined once in `src/star_report/__init__.py` and read by hatchling.
- **Configuration:**
  - Every tunable comes from config: thresholds, radii, timeouts, expiry times, counts, and model names. Nothing is hardcoded.
  - The defaults live in the packaged `src/star_report/config.yaml`.
  - A `./config.yaml`, or the file given with `--config`, only needs the keys it changes. It is deep-merged over the defaults.
- **Secrets:**
  - Secrets come only from the environment: `ADS_DEV_KEY`, and the Anthropic SDK's default credential lookup (`ANTHROPIC_API_KEY`, or an `ant auth login` profile).
  - Never write a secret to config, cache, logs, `data.json`, or test fixtures.
- **HTTP:**
  - Every request made with `requests` or `pyvo` goes through `sources.base.make_session()`, which sends `User-Agent: star-report/0.1.0`. The User-Agent never contains an email address or other personal data.
  - astroquery and lightkurve keep their own user agents.
- **Serializable data:**
  - Every raw payload, cache entry, and `SourceResult.data` must be serializable to JSON: no astropy, numpy, or bytes objects, and NaN becomes `null`.
  - Gaia source IDs are strings. TIC IDs are integers.
- **Source statuses:** exactly `ok`, `empty`, `error`, `timeout`, and `skipped`.
- **Exit codes:**

  | Code | Meaning |
  |---|---|
  | 0 | Every report was written. |
  | 1 | At least one target was unresolved or crashed. The other targets are still written. |
  | 2 | Usage or configuration error. |

- **`data.json`** has `schema_version: 1`.
- **LLM:**
  - `claude-opus-5-5`, with `output_config.effort` set to `"high"`.
  - Send no `thinking` parameter. Thinking is always on, and adaptive, for this model.
  - Pass `fallbacks="default"` with `betas=["server-side-fallback-2026-07-01"]`.
  - Check `stop_reason` before reading any content.
- **Tests:**
  - Runs are offline by default (pytest-socket).
  - Live tests carry the `@pytest.mark.live` marker and run only with `pytest -m live`.
- **Cache:**
  - The root is `~/.cache/star-report/`.
  - Each source belongs to a cache group, and each group has its own expiry. The expiry values are the spec §10 values, set in Task 1's `config.yaml`.

- **Commits:**
  - One commit per task, on `main`. The repo is new and has no remote.
  - Every message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

These are input classes and failure modes that the spec implies but no task's main-path tests exercise, most likely first. Each has a pinning test in the task that owns the code.

1. **SIMBAD main IDs with prefixes and punctuation.**
   - Examples: `NAME Barnard's star`, `* alf Lyr`, `V* V1298 Tau`.
   - These feed the display name, the folder slug, and the ADS `object:` query.
   - Expected: a clean display name (`Barnard's star`, `alf Lyr`, `V1298 Tau`) and a filesystem-safe slug that is never empty.
   - Tests in Task 9.
2. **A star with no Gaia DR3 match or no TIC ID.**
   - Examples: very bright stars, and stars outside the TIC.
   - Expected:
     - The identity uses SIMBAD's J2000 position, with `position.source == "simbad"`.
     - Sources that need the missing ID are `skipped` with a reason.
     - The parameter table and plots degrade without crashing.
   - Tests in Tasks 8, 10, and 21.
3. **Input spelling noise.**
   - Examples: odd case and spacing (`hd209458`, `TIC420814525`, `toi 700`), a curly apostrophe (`Barnard’s Star`), and planet suffixes (`HD 209458b`, `TOI-700 d`, `TOI-700.01`).
   - Note that an uppercase `B` is a stellar component and must be kept.
   - Expected: the same parse as the canonical spelling.
   - Tests in Task 3.
4. **Upstream responses that changed shape.**
   - Examples: an HTML error page where JSON was expected, a missing key, a zero-row table, a VizieR catalog that returns no tables.
   - Expected: status `error` with a one-line message, or `empty`, and never a crash or a broken report.
   - Tests in Tasks 4, 13, and 15.
5. **One target crashing in the middle of a batch.**
   - Example: an unexpected exception inside one target's pipeline.
   - Expected: the traceback is shown, the other targets still run, and the exit code is 1.
   - Tests in Task 11.

## Conventions Used in This Plan

- Run everything from the repo root with the venv active: `source .venv/bin/activate`.
- **"Approach" lines** name the library calls and pinned values. The implementer writes the function body.
- **Fixtures:**
  - Fixture files are raw payloads recorded with `scripts/record_fixtures.py` (Task 5).
  - Assertions on measured quantities use ranges, so re-recording a fixture does not break the test.
  - Identifiers and counts that cannot drift are asserted exactly.
- **Shared test helpers** in `tests/conftest.py` arrive in Tasks 1 and 5:
  - `load_fixture(source: str, slug: str) -> Any`
  - `ref_star(slug: str) -> dict`, which reads an entry from `tests/reference_stars.yaml`
  - `ref_identity(slug: str) -> StarIdentity`
  - `make_ctx(identity: StarIdentity | None = None, cfg: Config | None = None, cache_dir: Path | None = None, now: datetime | None = None) -> SourceContext`

## File Structure

```
pyproject.toml                    package metadata, deps, console script, pytest config
.gitignore
README.md, CLAUDE.md              Task 28
scripts/record_fixtures.py        records raw payloads and resolver lookups into tests/fixtures (Task 5, grows per source)
src/star_report/
  __init__.py                     __version__ = "0.1.0"
  config.yaml                     packaged defaults: the single home of every tunable
  config.py                       Config models, load_config, ConfigError, ads_token
  models.py                       Position, StarIdentity, Provenance, SourceResult, ParamCell, ParamRow, Paper, LLMBlock, Report
  cache.py                        Cache: JSON files with per-group expiry and refresh
  coords.py                       coordinate parsing, epoch propagation, separations
  resolve.py                      parse_target, CatalogLookup protocol, resolve, consistency checks, LiveLookup, Recording/ReplayLookup
  derive.py                       display_name, slugify, parameter rows, disagreement, flags, description line
  pipeline.py                     run_target and PipelineDeps (the five stages for one target)
  cli.py                          arguments, targets file, batch loop, exit codes, --open
  sources/
    base.py                       Source ABC, SourceContext, run_sources, records, make_session
    registry.py                   build_sources(cfg): registry order = report order
    simbad.py  gaia.py  tic.py  exoplanet_archive.py  exofop.py  vizier.py
    mast.py  tess_pointing.py  eso.py  koa.py  ads.py
  plots/
    __init__.py                   make_plots(report, plots_dir, cfg, cache)
    cmd.py  lightcurve.py  sky.py
  writer.py                       fact sheet, prompt, schema, Claude call, citation check, LLMBlock
  render/
    html.py                       render_html, write_outputs, link_citations
    terminal.py                   print_summary
    templates/report.html.j2 and _*.html.j2 partials
tests/
  conftest.py  reference_stars.yaml  fixtures/<source>/<slug>.json
  test_<module>.py per module, plus test_live.py
```

These files are additions to the spec's layout: `coords.py`, `derive.py`, `sources/registry.py`, `plots/__init__.py`, and `scripts/`.

One file differs from the spec's layout. The repo has no top-level `config.yaml`; the packaged file holds the defaults, and a local `config.yaml` is an optional override.

---

## Milestone 1: Skeleton and core section

### Task 1: Package skeleton and configuration

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/star_report/__init__.py`, `src/star_report/config.yaml`, `src/star_report/config.py`, `tests/conftest.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `star_report.__version__ = "0.1.0"`
  - `config.Config`: pydantic models with `model_config = ConfigDict(extra="forbid")` at every level. The sections are `output`, `servers`, `cache`, `sources`, `resolve`, `parameters`, `flags`, `plots`, `literature`, and `llm`, mirroring the YAML below key for key. Each section's class is named `<Section>Config`: `OutputConfig`, `ServersConfig`, `CacheConfig`, `SourcesConfig`, `ResolveConfig`, `ParametersConfig`, `FlagsConfig`, `PlotsConfig`, `LiteratureConfig`, and `LLMConfig`.
  - `cache.dir` is a `Path`, with `~` expanded.
  - `config.load_config(path: Path | None = None, cwd: Path | None = None) -> Config`. Search order: explicit `path`; else `<cwd>/config.yaml` if it exists; else defaults only.
  - `config.ConfigError(Exception)`: raised for YAML errors, unknown keys, bad types, and a missing explicit path.
  - `config.ads_token() -> str | None`: returns `ADS_DEV_KEY`, with an empty value treated as `None`.

- [ ] **Step 1: Create the project files.**

`pyproject.toml`:
- hatchling build backend, with `[tool.hatch.version] path = "src/star_report/__init__.py"`, `dynamic = ["version"]`, and `[tool.hatch.build.targets.wheel] packages = ["src/star_report"]`.
- `dependencies`:
  - `astroquery>=0.4.10`, `astropy>=7.0`, `pyvo>=1.6`
  - `lightkurve>=2.5`, `tess-point>=0.9.5`, `tesswcs>=1.9`. tesswcs provides the sector dates; Task 18 explains why.
  - `numpy>=2.0`, `matplotlib>=3.9`
  - `requests>=2.32`, `jinja2>=3.1`, `pyyaml>=6.0`, `rich>=13`, `pydantic>=2.7`
  - `anthropic>=2.6,<3`
- `[project.optional-dependencies] dev = ["pytest>=8", "pytest-socket>=0.7"]`
- `[tool.pytest.ini_options]`:
  - `testpaths = ["tests"]`
  - `markers = ["live: queries real services; run with -m live"]`
  - `addopts = "-m 'not live' --disable-socket --allow-unix-socket"`

`.gitignore`: `.venv/`, `__pycache__/`, `*.egg-info/`, `.pytest_cache/`, `reports/`, `/config.yaml`. The last one keeps a personal local override out of git.

`tests/conftest.py` starts with a `pytest_collection_modifyitems` hook that adds `pytest.mark.enable_socket` to every item marked `live`.

`src/star_report/config.yaml`. These values are copied verbatim from the spec:

```yaml
# star-report defaults. A ./config.yaml or --config file only needs the keys it changes.
output:
  dir: reports
  open_single: true            # open the HTML report when there is exactly one target
servers:                       # the first host that accepts a TCP connection on 443 is used (checked once per run)
  simbad: [simbad.cds.unistra.fr, simbad.harvard.edu]
  vizier: [vizier.cds.unistra.fr, vizier.cfa.harvard.edu]
cache:
  dir: ~/.cache/star-report
  empty_expiry_days: 1         # an empty result is rechecked after 1 day, whatever its group
  expiry_days:                 # null = never expires; replaced only by --refresh
    gaia: 90
    tic: 90
    vizier: 90
    simbad: 30
    exoplanet_archive: 1
    exofop: 1
    mast: 7
    eso: 7
    koa: 7
    ads: 7
    cmd_background: null
    llm: null
sources:
  max_workers: 8
  timeout_s: 30
  enabled: {simbad: true, gaia_core: true, gaia_neighbors: true, tic: true,
            exoplanet_archive: true, exofop: true, gaia_nss: true, gaia_vari: true,
            wide_binaries: true, wds: true, sb9: true, vsx: true, mast: true,
            tess_pointing: true, eso: true, koa: true, harps_rvbank: true,
            apogee: true, lamost: true, ads: true}
  radius_arcsec: {gaia_neighbors: 215, wds: 10, sb9: 5, vsx: 5, harps_rvbank: 5,
                  apogee: 3, lamost: 3, mast: 3, eso: 5, koa: 10}
  vizier_catalogs: {wds: B/wds/wds, sb9: B/sb9, vsx: B/vsx/vsx, wide_binaries: J/MNRAS/506/2269/catalog,
                    harps_rvbank: J/A+A/636/A74, apogee: III/286/catalog, lamost: V/162}   # Task 20 pins the LAMOST table
  koa_instruments: [hires, kpf, nirspec, esi, nirc2, nires, mosfire]
resolve:
  match_radius_arcsec: 5       # coordinates or SIMBAD position -> nearest Gaia DR3 / TIC
  coord_search_arcsec: 200     # candidate cone for coordinate input (covers J2000 positions of high-PM stars)
  pair_radius_arcsec: 2
  pair_max_delta_g: 3
parameters:
  disagreement_sigma: 3
  disagreement_fraction: 0.10
  disagreement_absolute: {logg: 0.2, mh: 0.2, V: 0.1, G: 0.05}   # replaces the fraction for log/mag rows
flags:
  ruwe_max: 1.4
  contamination_max: 0.1
plots:
  enabled: true
  max_sectors: 10
  lightcurve_timeout_s: 120
  cutout_arcmin: 5
  sky_survey: CDS/P/DSS2/red   # hips2fits HiPS ID (CDS)
  sky_fallback_survey: poss2ukstu_red   # STScI DSS cutout service, used when CDS is unreachable
  neighbor_max_delta_g: 6
  tess_pixel_arcsec: 21
  cmd_background_rows: 50000
  periodogram_min_period_d: 0.1
  periodogram_max_period_d: 30
literature:
  max_records: 200
  doctypes: [article, eprint]
  most_cited: 15
  most_recent: 10
llm:
  enabled: true
  model: claude-opus-5-5
  effort: high
  max_tokens: 16000
  server_fallback: true
```

- [ ] **Step 2: Write the failing tests** in `tests/test_config.py`.

```python
def test_defaults(tmp_path):
    cfg = load_config(cwd=tmp_path)
    assert (cfg.llm.model, cfg.llm.effort) == ("claude-opus-5-5", "high")
    assert (cfg.sources.max_workers, cfg.sources.timeout_s) == (8, 30)
    assert cfg.cache.expiry_days["cmd_background"] is None and cfg.cache.expiry_days["simbad"] == 30
    assert (cfg.literature.most_cited, cfg.literature.most_recent) == (15, 10)
    assert cfg.servers.simbad == ["simbad.cds.unistra.fr", "simbad.harvard.edu"]
    assert "~" not in str(cfg.cache.dir)

def test_local_file_overrides_only_its_keys(tmp_path):
    (tmp_path / "config.yaml").write_text("llm:\n  effort: medium\n")
    cfg = load_config(cwd=tmp_path)
    assert (cfg.llm.effort, cfg.llm.model) == ("medium", "claude-opus-5-5")

def test_explicit_path_beats_local_file(tmp_path): ...    # both exist; explicit file's value wins
def test_unknown_key_is_config_error(tmp_path): ...       # "llm:\n  modle: x\n" -> ConfigError whose message contains "modle"
def test_bad_yaml_is_config_error(tmp_path): ...          # "llm: [" -> ConfigError
def test_missing_explicit_path_is_config_error(tmp_path): ...

def test_ads_token(monkeypatch):
    monkeypatch.setenv("ADS_DEV_KEY", ""); assert ads_token() is None
    monkeypatch.setenv("ADS_DEV_KEY", "abc"); assert ads_token() == "abc"
    monkeypatch.delenv("ADS_DEV_KEY"); assert ads_token() is None

def test_network_blocked_by_default():
    with pytest.raises(Exception):
        socket.create_connection(("example.com", 80), timeout=1)
```

- [ ] **Step 3: Create the venv and confirm the tests fail.**

Run:

```
python3.13 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]' && pytest tests/test_config.py -v
```

Expected: FAIL with `ImportError` (`star_report.config` does not exist yet).

- [ ] **Step 4: Implement `config.py`.**

Approach:
- Read the defaults with `importlib.resources.files("star_report") / "config.yaml"`.
- Deep-merge the override dict, recursing into nested dicts. Lists and scalars replace the default value.
- `Config.model_validate(merged)`.
- Wrap `yaml.YAMLError`, `pydantic.ValidationError`, and `FileNotFoundError` in `ConfigError` with a one-line message.

- [ ] **Step 5: Run the tests.**

Run: `pytest tests/test_config.py -v`

Expected: all PASS.

- [ ] **Step 6: Commit.**

```bash
git add pyproject.toml .gitignore src tests
git commit -m "feat: package skeleton and layered config"
```

### Task 2: Data model and disk cache

**Files:**
- Create: `src/star_report/models.py`, `src/star_report/cache.py`, `tests/test_models.py`, `tests/test_cache.py`

**Interfaces:**
- Produces `models.py`. All classes are pydantic `BaseModel`; dumping uses `model_dump(mode="json")`.

```python
Status = Literal["ok", "empty", "error", "timeout", "skipped"]
BIBCODE_RE = re.compile(r"\[([0-9]{4}[A-Za-z0-9&.]{14}[A-Za-z.])\]")   # "[bibcode]" citation marker (19-char bibcode)

class Position(BaseModel):
    ra: float; dec: float; epoch: float                     # 2016.0 for Gaia DR3, 2000.0 for SIMBAD/TIC
    pmra: float | None = None; pmdec: float | None = None   # mas/yr (pmra includes cos dec)
    parallax: float | None = None; rv: float | None = None  # mas, km/s
    source: Literal["gaia_dr3", "simbad", "tic"]

class StarIdentity(BaseModel):
    input: str; main_name: str                              # main_name = SIMBAD main_id, or "TIC n" / "Gaia DR3 n"
    simbad_id: str | None = None; tic_id: int | None = None
    gaia_dr3_id: str | None = None; gaia_dr2_id: str | None = None
    position: Position
    other_ids: dict[str, str] = {}                          # key: "HD", "HIP", "2MASS", "KIC", "EPIC", "TOI", ...
    resolved_via: str                                       # "tic" | "gaia_dr3" | "gaia_dr2" | "toi" | "koi" | "coords" | "simbad"
    warnings: list[str] = []

class Provenance(BaseModel):
    service: str; query: str; catalog_version: str | None = None
    retrieved: datetime; from_cache: bool

class SourceResult(BaseModel):
    source: str; status: Status; data: Any = None
    provenance: Provenance | None = None; error: str | None = None; elapsed_s: float = 0.0

class ParamCell(BaseModel):
    value: float | None = None; err: float | None = None; note: str | None = None   # note e.g. "GSP-Spec"

class ParamRow(BaseModel):
    key: str; label: str; unit: str; decimals: int
    cells: dict[str, ParamCell]                             # keys among "gaia", "tic", "simbad"
    disagree: bool = False

class Paper(BaseModel):
    bibcode: str; title: str; year: int | None = None; pubdate: str | None = None
    authors: list[str] = []; citation_count: int = 0; abstract: str | None = None; doctype: str | None = None

class LLMBlock(BaseModel):
    status: Literal["ok", "skipped", "failed"]; reason: str | None = None
    overview: str | None = None; literature_summary: str | None = None; cited_bibcodes: list[str] = []
    model: str | None = None; effort: str | None = None; generated: datetime | None = None
    bibcodes_sent: list[str] = []; from_cache: bool = False

class Report(BaseModel):
    schema_version: Literal[1] = 1; tool_version: str; generated: datetime
    identity: StarIdentity; results: dict[str, SourceResult]
    parameters: list[ParamRow] = []; flags: list[str] = []
    plots: dict[str, str] = {}; plot_notes: dict[str, str] = {}   # name -> "plots/<name>.png"; name -> note
    llm: LLMBlock
```

- Produces `cache.py`:
  - `CacheEntry(NamedTuple)`: `payload: Any`, `retrieved: datetime`, `from_cache: bool`.
  - `class Cache`:
    - `__init__(self, root: Path, expiry_days: Mapping[str, float | None], empty_expiry_days: float = 1.0, refresh: bool = False, now: Callable[[], datetime] = <UTC now>)`
    - A payload counts as empty when it is `None`, an empty container, or a dict whose values are all empty. Empty payloads expire after `min(group expiry, empty_expiry_days)`, so a mirror that silently returned nothing is rechecked the next day.
    - `fetch(self, group: str, name: str, query: Mapping[str, Any] | str, producer: Callable[[], Any]) -> CacheEntry`

- [ ] **Step 1: Write the failing tests.**

`tests/test_models.py`: build a minimal `Report` with one `SourceResult`. Then:
- `json.loads(json.dumps(report.model_dump(mode="json")))["schema_version"] == 1`
- `Report.model_validate(dumped) == report`, a round trip.

`tests/test_cache.py`:

```python
def test_miss_then_hit_across_instances(tmp_path):
    calls = []
    first = Cache(tmp_path, {"gaia": 90}).fetch("gaia", "core", {"id": 1}, lambda: calls.append(1) or {"x": 1})
    second = Cache(tmp_path, {"gaia": 90}).fetch("gaia", "core", {"id": 1}, lambda: calls.append(2) or {"x": 2})
    assert (first.payload, first.from_cache, second.payload, second.from_cache) == ({"x": 1}, False, {"x": 1}, True)
    assert calls == [1] and second.retrieved == first.retrieved

def test_expired_entry_is_refetched(tmp_path): ...       # now() = t0, then t0 + 91 days -> producer runs again
def test_null_expiry_never_expires(tmp_path): ...        # {"llm": None}, t0 + 3650 days -> hit
def test_empty_payload_expires_after_a_day(tmp_path): ... # {"vizier": 90}: payload {"t": []} -> hit at t0 + 0.5 d, miss at t0 + 1.5 d
def test_refresh_ignores_old_entries_but_reuses_this_runs(tmp_path): ...
    # old entry on disk; Cache(refresh=True): 1st fetch calls producer, 2nd fetch of same key is from_cache
def test_query_key_is_order_independent(tmp_path): ...   # {"a":1,"b":2} then {"b":2,"a":1} -> hit
def test_producer_error_writes_nothing(tmp_path): ...    # raises -> exception propagates, next fetch calls producer
def test_corrupt_file_is_a_miss(tmp_path): ...           # overwrite the entry file with "{" -> producer runs
def test_unknown_group_raises(tmp_path):
    with pytest.raises(ValueError, match="nope"):
        Cache(tmp_path, {"gaia": 90}).fetch("nope", "x", {}, dict)
```

- [ ] **Step 2: Run the tests to confirm they fail.**

Run: `pytest tests/test_models.py tests/test_cache.py -v`

Expected: FAIL, because the modules are missing.

- [ ] **Step 3: Implement `models.py` and `cache.py`.**

Approach for the cache:
- **Entry file:** `root/<group>/<key>.json`, where `key = sha256(json.dumps({"name": name, "query": query}, sort_keys=True, default=str))[:32]`.
- **File contents:** `{"name", "query", "retrieved" (ISO UTC), "payload"}`.
- **Writes are atomic:** write a temp file in the same directory, then `os.replace`.
- **Hit rule:** the file parses, it is not expired, and either `refresh` is off or the key was written by this instance. Keep a set of the keys written during this run.

- [ ] **Step 4: Run the tests.**

Run: `pytest tests/test_models.py tests/test_cache.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/star_report/models.py src/star_report/cache.py tests/test_models.py tests/test_cache.py
git commit -m "feat: report data model and disk cache"
```

### Task 3: Coordinates and target parsing

**Files:**
- Create: `src/star_report/coords.py`, `src/star_report/resolve.py` (the parsing half only), `tests/test_coords.py`, `tests/test_parse_target.py`

**Interfaces:**
- Produces in `coords.py`:
  - `parse_coordinates(text: str) -> tuple[float, float] | None`: returns ICRS degrees, or `None` when the text is not coordinates.
  - `propagate(pos: Position, epoch: float) -> tuple[float, float]`: returns `(ra, dec)` at `epoch`. When `pmra` or `pmdec` is `None`, it returns the input position unchanged.
  - `separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float`
- Produces in `resolve.py`:
  - `IdKind = Literal["tic", "gaia_dr3", "gaia_dr2", "toi", "koi", "kic", "epic", "hd", "hip", "hr", "gj", "2mass", "coords", "name"]`
  - `@dataclass(frozen=True) class ParsedTarget`, with fields:
    - `raw: str`
    - `kind: IdKind`
    - `value: str`: the bare number or identifier
    - `query_name: str`: a SIMBAD-friendly spelling
    - `coords: tuple[float, float] | None = None`
    - `planet: str | None = None`
  - `parse_target(text: str) -> ParsedTarget`

- [ ] **Step 1: Write the failing tests.** `tests/test_parse_target.py` is a parametrized table:

```python
CASES = [  # input, kind, value, query_name, planet
    ("TIC 420814525", "tic", "420814525", "TIC 420814525", None),
    ("TIC420814525", "tic", "420814525", "TIC 420814525", None),
    ("tic 420814525", "tic", "420814525", "TIC 420814525", None),
    ("Gaia DR3 1779546757669063552", "gaia_dr3", "1779546757669063552", "Gaia DR3 1779546757669063552", None),
    ("Gaia DR2 1779546757669063552", "gaia_dr2", "1779546757669063552", "Gaia DR2 1779546757669063552", None),
    ("TOI-700", "toi", "700", "TOI-700", None),
    ("TOI 700.01", "toi", "700", "TOI-700", ".01"),
    ("toi700", "toi", "700", "TOI-700", None),
    ("TOI-700 d", "toi", "700", "TOI-700", "d"),
    ("KOI-72", "koi", "72", "KOI-72", None),
    ("K00072.01", "koi", "72", "KOI-72", ".01"),
    ("KIC 11904151", "kic", "11904151", "KIC 11904151", None),
    ("EPIC 201367065", "epic", "201367065", "EPIC 201367065", None),
    ("HD 209458", "hd", "209458", "HD 209458", None),
    ("hd209458", "hd", "209458", "HD 209458", None),
    ("HD 209458b", "hd", "209458", "HD 209458", "b"),
    ("HD 209458B", "hd", "209458B", "HD 209458B", None),   # uppercase = stellar component, kept
    ("HIP 108859", "hip", "108859", "HIP 108859", None),
    ("HR 8781", "hr", "8781", "HR 8781", None),
    ("GJ 699", "gj", "699", "GJ 699", None),
    ("Gl 699", "gj", "699", "GJ 699", None),
    ("2MASS J22031077+1853036", "2mass", "J22031077+1853036", "2MASS J22031077+1853036", None),
    ("WASP-33 b", "name", "WASP-33", "WASP-33", "b"),
    ("Kepler-10c", "name", "Kepler-10", "Kepler-10", "c"),
    ("Barnard’s Star", "name", "Barnard's Star", "Barnard's Star", None),   # curly apostrophe normalized
    ("  HD   209458  ", "hd", "209458", "HD 209458", None),
]

def test_coordinates_are_parsed():
    for text in ["330.7950 18.8843", "330.7950, +18.8843", "22:03:10.77 +18:53:03.5",
                 "22 03 10.77 +18 53 03.5", "22h03m10.77s +18d53m03.5s"]:
        p = parse_target(text)
        assert p.kind == "coords" and abs(p.coords[0] - 330.7949) < 1e-3 and abs(p.coords[1] - 18.8843) < 1e-3

def test_negative_declination():
    assert parse_target("06 45 08.9 -16 42 58").coords[1] < 0
```

`tests/test_coords.py`:

```python
def test_barnard_propagates_to_j2000():   # Gaia DR3 epoch 2016.0 -> 2000.0: Dec drops ~165.8", RA* grows ~12.8"
    pos = Position(ra=269.4486, dec=4.7394, epoch=2016.0, pmra=-801.55, pmdec=10362.39, parallax=546.98, source="gaia_dr3")
    ra, dec = propagate(pos, 2000.0)
    assert abs((dec - 4.7394) * 3600 + 165.8) < 1.0
    assert abs((ra - 269.4486) * 3600 * math.cos(math.radians(4.7394)) - 12.8) < 0.5

def test_no_proper_motion_returns_input(): ...
def test_separation(): ...                       # 1 arcsec along Dec -> 1.0 +/- 1e-6
def test_non_coordinates_return_none(): ...      # "HD 209458", "Kepler-10" -> None
```

- [ ] **Step 2: Run the tests to confirm they fail.**

Run: `pytest tests/test_parse_target.py tests/test_coords.py -v`

Expected: FAIL, because the modules are missing.

- [ ] **Step 3: Implement.**

Approach for `parse_target`:
1. Normalize the input:
   - Apply `unicodedata.normalize("NFKC")`.
   - Replace `’` and `‘` with `'`.
   - Collapse whitespace and strip.
2. **Strip a planet suffix.**
   - TOI and KOI: a trailing `.\d\d`.
   - Otherwise, the regex `^(?P<host>.+?)(?:\s+|(?<=\d))(?P<planet>[b-i])$`, which matches lowercase letters only.
3. Match the prefixed-ID regexes, case-insensitively, in the order of the table above. `Gl`, `Gliese`, and `GJ` all map to `GJ`. This comes before the coordinate check so that `2MASS J22031077+1853036` is never read as coordinates.
4. Try `parse_coordinates`.
5. Anything else is `kind="name"`.

Approach for the coordinate helpers:
- **`parse_coordinates`:**
  - Replace commas with spaces.
  - Two float tokens are degrees.
  - Otherwise try `SkyCoord(text, unit=(u.hourangle, u.deg))`, catching `ValueError`.
- **`propagate`:**
  - Use `SkyCoord(..., obstime=Time(epoch, format="jyear")).apply_space_motion(new_obstime=Time(new_epoch, format="jyear"))`.
  - When the parallax is missing or ≤ 0, use a distance of 1 kpc.
  - When the RV is missing, use 0.

- [ ] **Step 4: Run the tests.**

Run: `pytest tests/test_parse_target.py tests/test_coords.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/star_report/coords.py src/star_report/resolve.py tests/test_parse_target.py tests/test_coords.py
git commit -m "feat: target parsing and coordinate helpers"
```

### Task 4: Source framework and runner

**Files:**
- Create: `src/star_report/sources/__init__.py`, `src/star_report/sources/base.py`, `src/star_report/sources/registry.py`, `tests/test_runner.py`

**Interfaces:**
- Consumes: `Config` (Task 1); `Cache` and `CacheEntry` (Task 2); `StarIdentity`, `SourceResult`, and `Provenance` (Task 2).
- Produces in `sources/base.py`:
  - `USER_AGENT = f"star-report/{__version__}"`
  - `make_session(default_timeout: float = 30.0) -> requests.Session`:
    - Sets the User-Agent.
    - Mounts an `HTTPAdapter` subclass that applies `default_timeout` whenever a call passes no `timeout`. This means pyvo, which takes no timeout argument, still times out.
  - `records(table: astropy.table.Table) -> list[dict]`: JSON-safe; masked values and NaN become `None`; numpy scalars become Python types; bytes are decoded as UTF-8.
  - `tap_rows(url: str, adql: str, session: requests.Session) -> list[dict]`: returns `records(pyvo.dal.TAPService(url, session=session).run_sync(adql).to_table())`.
    - All TAP services (SIMBAD, Gaia, ESO, KOA) go through this one helper instead of astroquery's wrappers. That way they share the session's User-Agent and timeout, are safe across threads, and can use a mirror.
    - This was verified on 2026-11-09 against Gaia and the SIMBAD mirror.
  - `is_reachable(host: str, timeout: float = 3.0) -> bool`: checks whether `socket.create_connection((host, 443), timeout)` succeeds. Memoized per process with `functools.cache`.
  - `pick_server(hosts: Sequence[str]) -> str`: returns the first reachable host, or `hosts[0]` when none is reachable.
    - On 2026-11-09 every `*.cds.unistra.fr` host timed out from the development machine, while the Harvard mirrors answered.
  - `@dataclass class SourceContext`: `identity: StarIdentity`, `config: Config`, `cache: Cache`, `session: requests.Session`, `ads_token: str | None = None`, `now: Callable[[], datetime] = <UTC now>`. Tests can fix `now`.
  - `class Source(ABC)`:
    - Class attributes: `name: ClassVar[str]`, `group: ClassVar[str]` (the cache group), `service: ClassVar[str]`, `catalog_version: ClassVar[str | None] = None`, `needs: ClassVar[tuple[str, ...]] = ()` (identity fields that must not be `None`).
    - Methods:
      - `query(self, ctx) -> dict`: the cache key and provenance query.
      - `fetch(self, ctx) -> Any`: abstract; does network I/O and returns the raw JSON-safe payload.
      - `parse(self, raw, ctx) -> Any`: abstract; pure. It returns `None` or an empty container when there is no data.
      - `skip_reason(self, ctx) -> str | None`: the default checks `needs`, using `{"tic_id": "no TIC ID", "gaia_dr3_id": "no Gaia DR3 ID", "simbad_id": "no SIMBAD entry"}`.
      - `timeout_s(self, cfg) -> float`: the default is `cfg.sources.timeout_s`.
  - `run_source(src: Source, ctx: SourceContext) -> SourceResult`: synchronous; never raises.
  - `run_sources(sources: Sequence[Source], ctx: SourceContext) -> dict[str, SourceResult]`: keys in input order; uses `ctx.config.sources.max_workers` threads.
- Produces in `sources/registry.py`:
  - `build_sources(cfg: Config) -> list[Source]`: the report order. It starts empty, and each source task appends its class.

- [ ] **Step 1: Write the failing tests.** Use fake sources defined in the test file, with configurable payload, delay, exception, and `needs`.

```python
def test_statuses(ctx):
    res = run_sources([Fake("a", payload={"x": 1}), Fake("b", payload=[]), Fake("c", exc=RuntimeError("boom"))], ctx)
    assert [r.status for r in res.values()] == ["ok", "empty", "error"]
    assert res["c"].error == "RuntimeError: boom" and res["a"].provenance.service == "fake service"

def test_timeout_does_not_block(ctx):        # ctx.config.sources.timeout_s = 0.2
    t0 = time.monotonic()
    res = run_sources([Fake("slow", delay=5), Fake("fast", payload={"x": 1})], ctx)
    assert res["slow"].status == "timeout" and res["fast"].status == "ok" and time.monotonic() - t0 < 2

def test_skipped_when_needs_missing(ctx):    # identity.tic_id is None
    assert run_sources([Fake("t", needs=("tic_id",))], ctx)["t"].model_dump(include={"status", "error"}) == \
        {"status": "skipped", "error": "no TIC ID"}

def test_disabled_in_config(ctx): ...        # enabled["a"] = False -> skipped, error "disabled in config"
def test_second_run_comes_from_cache(ctx): ...   # fetch counter == 1; provenance.from_cache False then True
def test_order_preserved(ctx): ...           # 6 fakes with random delays -> keys in input order
def test_parse_error_is_error_status(ctx): ...   # parse raises KeyError('tois') -> status error, message names KeyError
def test_records_json_safe(): ...            # masked int column, float32 NaN, int64, bytes -> json.dumps works; None where masked/NaN
def test_session_user_agent():
    assert make_session().headers["User-Agent"] == "star-report/0.1.0"
def test_session_default_timeout(monkeypatch): ...   # patch requests.adapters.HTTPAdapter.send to capture kwargs;
                                                     # session.get(url) -> timeout == 30.0; session.get(url, timeout=5) -> 5
def test_pick_server(monkeypatch): ...               # patch socket.create_connection: host a raises OSError, host b works -> "b";
                                                     # all fail -> first host; second call with same hosts does not reconnect
def test_tap_rows(monkeypatch): ...                  # patch pyvo.dal.TAPService with a fake whose run_sync().to_table() is an
                                                     # astropy Table -> list of JSON-safe dicts; the session is passed through
```

The `ctx` fixture lives in `test_runner.py`: a `SourceContext` with a test identity, `Cache(tmp_path, {"gaia": 90})`, `make_session()`, and a config whose `sources.enabled` includes the fake names.

`Config.sources.enabled` is a `dict[str, bool]`. A name missing from it counts as enabled.

- [ ] **Step 2: Run the tests to confirm they fail.**

Run: `pytest tests/test_runner.py -v`

Expected: FAIL, because the module is missing.

- [ ] **Step 3: Implement `base.py` and the empty `registry.py`.**

Algorithm for `run_sources`. Its timing rule is not determined by the tests alone, so it is spelled out here:
1. Compute the skipped and disabled results first.
2. Submit each remaining source to a `ThreadPoolExecutor`. The worker wrapper records its own start time in a shared dict before calling `run_source`.
3. Loop on `concurrent.futures.wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)`:
   - Collect finished futures.
   - Mark any running future whose start time is more than `src.timeout_s(cfg)` ago as `timeout`, and drop it.
4. When the loop ends, call `executor.shutdown(wait=False, cancel_futures=True)`.
5. Return the results in input order.

Timed-out threads keep running in the background, so every network call in every source must pass its own timeout to the underlying library: `requests` (`timeout=`), astroquery (`TIMEOUT`), or pyvo (the session).

`run_source`:
- Calls `ctx.cache.fetch(src.group, src.name, src.query(ctx), lambda: src.fetch(ctx))`, then `src.parse(...)`.
- Sets the status to `empty` when the parsed data is falsy.
- Formats errors as `f"{type(e).__name__}: {e}"`, cut to 300 characters.
- Builds the provenance from `src.service`, `json.dumps(src.query(ctx), sort_keys=True)`, `src.catalog_version`, `entry.retrieved`, and `entry.from_cache`.

- [ ] **Step 4: Run the tests.**

Run: `pytest tests/test_runner.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/star_report/sources tests/test_runner.py
git commit -m "feat: source interface and parallel runner with timeouts and cache"
```

### Task 5: SIMBAD source, reference stars, and fixture recording

**Files:**
- Create: `src/star_report/sources/simbad.py`, `scripts/record_fixtures.py`, `tests/reference_stars.yaml`, `tests/test_source_simbad.py`, and `tests/fixtures/simbad/<slug>.json` (recorded)
- Modify: `src/star_report/sources/registry.py`, `tests/conftest.py`

**Facts** (verified 2026-11-09):
- **Endpoint.** SIMBAD TAP is at `https://{host}/simbad/sim-tap`, with `host = pick_server(cfg.servers.simbad)`.
- **Matching.** `ident.id` matching ignores case and spacing: `HD209458` and `hd 209458` both match. `Barnard's Star` matches `NAME Barnard's star`; escape `'` as `''`.
- **No match.** An unknown name returns zero rows; it does not raise.
- **ADQL.** There are no subqueries; use joins.
- **The tested query.** For HD 209458 it returns one row: V 7.63, B 8.21, `sp_type` F9V, `nbref` 1203. `ids.ids` is a single string separated by `|`.

```sql
SELECT b.main_id, b.ra, b.dec, b.otype, b.sp_type, b.rvz_radvel, b.nbref, b.pmra, b.pmdec, b.plx_value, f.B, f.V, ids.ids
FROM ident AS i JOIN basic AS b ON b.oid = i.oidref
LEFT JOIN allfluxes AS f ON f.oidref = b.oid LEFT JOIN ids ON ids.oidref = b.oid
WHERE i.id = '{name}'
```

**Interfaces:**
- Consumes: `Source`, `SourceContext`, `tap_rows`, `pick_server`, `make_session`, and `run_source` (Task 4).
- Produces:
  - `simbad.adql(name: str) -> str`
  - `simbad.lookup(name: str, session: requests.Session, cfg: Config) -> list[dict]`. Each record is normalized to `{"main_id", "ra", "dec", "otype", "sp_type", "rv", "nbref", "pmra", "pmdec", "plx", "B", "V", "ids": list[str]}`.
  - `class Simbad(Source)`:
    - `name="simbad"`, `group="simbad"`, `service="SIMBAD TAP"`, `needs=("simbad_id",)`.
    - `query` is `{"name": identity.simbad_id}`.
    - `fetch` returns the raw `tap_rows`.
    - `parse` returns the first normalized record, or `None`.
  - In `conftest.py`:
    - `ref_star(slug) -> dict`
    - `ref_identity(slug) -> StarIdentity`: `main_name` and `simbad_id` both equal `simbad_main_id`; `position.source="gaia_dr3"`; `resolved_via="simbad"`.
    - `load_fixture(source, slug) -> Any`
    - `make_ctx(identity=None, cfg=None, cache_dir=None, now=None) -> SourceContext`
  - `scripts/record_fixtures.py [--star SLUG]... [--source NAME]... [--lookups]`:
    - With no `--star`, it covers all reference stars.
    - For each star and each source of `build_sources(load_config())` it builds a live context from `ref_identity` (no cache), calls `src.fetch(ctx)`, and writes `tests/fixtures/<source>/<slug>.json` with `indent=1, sort_keys=True`.
    - It prints the status for each fixture and carries on after errors.

`tests/reference_stars.yaml`. These values were verified on 2026-11-09 against SIMBAD, MAST TIC v8.2, and the Gaia archive. Positions are Gaia DR3 at epoch 2016.0.

```yaml
- slug: HD_209458
  simbad_main_id: HD 209458
  tic: 420814525
  gaia_dr3: "1779546757669063552"
  gaia_dr2: "1779546757669063552"
  other_ids: {HD: HD 209458, HIP: HIP 108859, 2MASS: 2MASS J22031077+1853036}
  position: {ra: 330.7950263, dec: 18.8842394, epoch: 2016.0, pmra: 29.766, pmdec: -17.976, parallax: 20.7694, rv: -14.78}
  spellings: [HD 209458, HD209458, hd 209458, TIC 420814525, TIC420814525, Gaia DR3 1779546757669063552, HIP 108859]
- slug: TOI-700
  simbad_main_id: TOI-700
  tic: 150428135
  gaia_dr3: "5284517766615850752"
  gaia_dr2: "5284517766615492736"            # differs from the DR3 ID
  other_ids: {TOI: TOI-700}
  position: {ra: 97.0956822, dec: -65.5785924, epoch: 2016.0, pmra: -102.641, pmdec: 161.748, parallax: 32.1328, rv: -4.71}
  spellings: [TOI-700, TOI 700, TOI-700.01, TOI-700 d, TIC 150428135, Gaia DR2 5284517766615492736]
- slug: Kepler-10
  simbad_main_id: Kepler-10
  tic: 377780790
  gaia_dr3: "2132155017099178624"
  gaia_dr2: "2132155017099178624"
  other_ids: {KIC: KIC 11904151, KOI: KOI-72, 2MASS: 2MASS J19024305+5014286, TOI: TOI-5988}
  position: {ra: 285.6792940, dec: 50.2414899, epoch: 2016.0, pmra: -18.483, pmdec: 41.382, parallax: 5.3698, rv: -98.44}
  spellings: [Kepler-10, Kepler-10 b, KIC 11904151, KOI-72, K00072.01, TIC 377780790]
- slug: Barnards_star
  simbad_main_id: NAME Barnard's star
  tic: 325554331
  gaia_dr3: "4472832130942575872"
  gaia_dr2: "4472832130942575872"
  other_ids: {HIP: HIP 87937, GJ: GJ 699, 2MASS: 2MASS J17574849+0441405}
  position: {ra: 269.4485025, dec: 4.7394201, epoch: 2016.0, pmra: -801.551, pmdec: 10362.394, parallax: 546.9759, rv: -110.47}
  spellings: [Barnard's Star, Barnard’s Star, GJ 699, HIP 87937, TIC 325554331, Gaia DR3 4472832130942575872, "17 57 48.50 +04 41 36.1"]   # last one = J2000 coordinates
```

- [ ] **Step 1: Write the failing tests** in `tests/test_source_simbad.py`.

```python
def test_adql_escapes_apostrophe():
    assert "i.id = 'Barnard''s Star'" in simbad.adql("Barnard's Star")

def test_parse_hd209458(ctx):
    rec = Simbad().parse(load_fixture("simbad", "HD_209458"), ctx)
    assert rec["main_id"] == "HD 209458" and rec["sp_type"].startswith("F9")
    assert 7.5 < rec["V"] < 7.8 and rec["nbref"] > 1000
    assert {"TIC 420814525", "Gaia DR3 1779546757669063552", "HIP 108859"} <= set(rec["ids"])

def test_parse_barnard(ctx): ...          # main_id == "NAME Barnard's star"; "GJ 699" in ids
def test_parse_no_rows(ctx): ...          # Simbad().parse([], ctx) is None
def test_skipped_without_simbad_id(ctx): ...  # run_source with identity.simbad_id None -> skipped, "no SIMBAD entry"
```

- [ ] **Step 2: Run the tests to confirm they fail.**

Run: `pytest tests/test_source_simbad.py -v`

Expected: FAIL, because the module is missing.

- [ ] **Step 3: Implement `simbad.py`, the conftest helpers, `reference_stars.yaml`, and the recorder. Register `Simbad` first in `build_sources`.**

- [ ] **Step 4: Record the fixtures and run the tests.**

Run: `python scripts/record_fixtures.py --source simbad && pytest tests/test_source_simbad.py -v`

Expected:
- Four fixture files are written.
- All tests PASS.

Troubleshooting: if a subprocess starts as x86_64 on this Mac and numpy fails to import, prefix the command with `arch -arm64`.

- [ ] **Step 5: Commit.**

```bash
git add src/star_report/sources scripts tests
git commit -m "feat: SIMBAD source, reference stars and fixture recorder"
```

### Task 6: Gaia DR3 core and neighbor sources

**Files:**
- Create: `src/star_report/sources/gaia.py`, `tests/test_source_gaia.py`, and `tests/fixtures/gaia_core/*.json` and `tests/fixtures/gaia_neighbors/*.json` (recorded)
- Modify: `sources/registry.py`

**Facts** (verified 2026-11-09):
- **Endpoint.** TAP is at `https://gea.esac.esa.int/tap-server/tap`. pyvo sync queries take about 1–2 s, and no login is needed.
- **Columns:**
  - All the listed `gaia_source` and `astrophysical_parameters` columns exist, with their `_lower` and `_upper` bounds.
  - `external.gaiaedr3_distance` has `source_id`, `r_med_geo`, `r_lo_geo`, `r_hi_geo`, `r_med_photogeo`, `r_lo_photogeo`, `r_hi_photogeo`, and `flag`.
  - `gaiadr3.dr2_neighbourhood` has `dr2_source_id`, `dr3_source_id`, `angular_distance` (arcsec), and `magnitude_difference`.
- **Query patterns:**
  - An equality filter on either ID takes about 1.5 s. Never use `IN (...)`, which took about 2 minutes.
  - Cone search: `1=CONTAINS(POINT('ICRS',ra,dec),CIRCLE('ICRS',{ra},{dec},{r_deg}))`.
  - `DISTANCE(POINT('ICRS',ra,dec),POINT('ICRS',{ra},{dec}))` returns degrees.
- **HD 209458 values:**

  | Quantity | Value |
  |---|---|
  | parallax | 20.769 mas |
  | RUWE | 0.984 |
  | `phot_variable_flag` | VARIABLE |
  | `teff_gspphot` | 5996 K |
  | `teff_gspspec` | 6020 K |
  | FLAME radius | 1.197 R☉ |
  | `r_med_photogeo` | 48.07 pc |

**Interfaces:**
- Consumes: `tap_rows` (Task 4).
- Produces:
  - `gaia.TAP_URL`
  - Lookup columns, used by `by_id` and `cone`: `source_id`, `ra`, `dec`, `ref_epoch`, `parallax`, `pmra`, `pmdec`, `radial_velocity`, `phot_g_mean_mag`, `bp_rp`.
  - `by_id(dr3_id: str, session) -> dict | None`
  - `cone(ra: float, dec: float, radius_arcsec: float, session) -> list[dict]`: adds `dist_arcsec` and orders by it.
  - `cone_adql(ra, dec, radius_arcsec) -> str`
  - `dr3_from_dr2(dr2_id: str, session) -> list[dict]` and `dr2_from_dr3(dr3_id: str, session) -> list[dict]`: rows from `dr2_neighbourhood`.
  - IDs are always returned as `str`.
  - `class GaiaCore(Source)`:
    - `name="gaia_core"`, `group="gaia"`, `service="Gaia DR3 (ESA TAP)"`, `catalog_version="Gaia DR3"`, `needs=("gaia_dr3_id",)`.
    - `parse` returns the `gaia_source` columns at the top level, an `"ap"` sub-dict, and a `"distance"` sub-dict.
  - `class GaiaNeighbors(Source)`:
    - `name="gaia_neighbors"`, `needs=("gaia_dr3_id",)`.
    - It searches a cone at the identity's 2016.0 position, with radius `radius_arcsec.gaia_neighbors`.
    - `parse` returns `[{"source_id", "ra", "dec", "G", "bp_rp", "dist_arcsec", "delta_g"}]`, where `delta_g` is the difference from the target row. The target row is included, with `delta_g = 0`.

The `GaiaCore` ADQL is `SELECT g.<gaia_source cols>, ap.<AP cols>, d.<distance cols> FROM gaiadr3.gaia_source AS g LEFT JOIN gaiadr3.astrophysical_parameters AS ap ON ap.source_id = g.source_id LEFT JOIN external.gaiaedr3_distance AS d ON d.source_id = g.source_id WHERE g.source_id = {id}`, with these column lists:
- **gaia_source columns:** `source_id`, `ra`, `dec`, `ref_epoch`, `parallax`, `parallax_error`, `pmra`, `pmdec`, `phot_g_mean_mag`, `phot_bp_mean_mag`, `phot_rp_mean_mag`, `bp_rp`, `ruwe`, `radial_velocity`, `radial_velocity_error`, `has_rvs`, `phot_variable_flag`, `non_single_star`.
- **AP columns:** `{q}_{m}`, `{q}_{m}_lower`, and `{q}_{m}_upper` for `q` in teff, logg, mh and `m` in gspphot, gspspec; plus `{q}_flame` with its bounds for `q` in radius, mass, lum, age.
- **Distance columns:** the six `r_*` columns.

- [ ] **Step 1: Write the failing tests.**

```python
def test_core_hd209458(ctx):
    d = GaiaCore().parse(load_fixture("gaia_core", "HD_209458"), ctx)
    assert d["source_id"] == "1779546757669063552" and d["phot_variable_flag"] == "VARIABLE"
    assert 20.6 < d["parallax"] < 20.9 and 0.9 < d["ruwe"] < 1.1
    assert 5900 < d["ap"]["teff_gspphot"] < 6100 and 5900 < d["ap"]["teff_gspspec"] < 6150
    assert 47 < d["distance"]["r_med_photogeo"] < 49.5

def test_core_no_rows(ctx): ...           # parse([], ctx) is None
def test_neighbors_include_target(ctx): ...   # target row: delta_g == 0 and dist_arcsec < 1; every row has delta_g
def test_cone_adql(): ...                  # contains "CONTAINS(POINT('ICRS', ra, dec)" and radius 5" written as 0.00138889 deg
def test_toi700_dr2_differs(ctx): ...      # parse of the TOI-700 gaia_core fixture -> source_id "5284517766615850752"
```

- [ ] **Step 2: Run the tests to confirm they fail.**

Run: `pytest tests/test_source_gaia.py -v`

Expected: FAIL.

- [ ] **Step 3: Implement `gaia.py` and register `GaiaCore` and `GaiaNeighbors` after `Simbad`.**

- [ ] **Step 4: Record the fixtures and run the tests.**

Run: `python scripts/record_fixtures.py --source gaia_core --source gaia_neighbors && pytest tests/test_source_gaia.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/star_report/sources tests
git commit -m "feat: Gaia DR3 core parameters and neighbor sources"
```

### Task 7: TIC v8.2 source

**Files:**
- Create: `src/star_report/sources/tic.py`, `tests/test_source_tic.py`, and `tests/fixtures/tic/*.json` (recorded)
- Modify: `sources/registry.py`

**Facts** (verified 2026-11-09):
- **Calls:**
  - `astroquery.mast.Catalogs.query_criteria(catalog="TIC", ID=420814525)`
  - `query_criteria(catalog="TIC", GAIA="<DR2 id as str>")`
  - `query_region(SkyCoord, radius=r*u.arcsec, catalog="TIC")`, which adds a `dstArcSec` column.
- **Positions** are ICRS at epoch J2000.
- **Missing values** come back as masked or `--`.
- **`TWOMASS`** has no `2MASS J` prefix.
- **`version`** is `20190415`, which is the TIC 8.2 release date.
- **HD 209458 values:** Teff 6052 ± 100.6, Tmag 7.1274, contratio 0.00206, GAIAmag 7.509, Vmag 7.65, rad 1.187, mass 1.13, d 48.30.

**Interfaces:**
- Produces:
  - `tic.COLUMNS = ("ID", "ra", "dec", "pmRA", "pmDEC", "Teff", "e_Teff", "logg", "e_logg", "MH", "e_MH", "rad", "e_rad", "mass", "e_mass", "lum", "e_lum", "d", "e_d", "Tmag", "e_Tmag", "Vmag", "e_Vmag", "GAIAmag", "contratio", "disposition", "duplicate_id", "objType", "GAIA", "HIP", "TWOMASS", "KIC", "version")`
  - `normalize(row: dict) -> dict`:
    - Keeps `COLUMNS`, plus `dstArcSec` when present.
    - Converts `ID` to `int`, and `GAIA`, `HIP`, `TWOMASS`, `KIC`, and `version` to `str`.
    - Turns `""`, `"--"`, and masked values into `None`.
  - `by_id(tic: int, timeout: float) -> dict | None`
  - `by_gaia_dr2(dr2: str, timeout: float) -> list[dict]`
  - `cone(ra, dec, radius_arcsec, timeout) -> list[dict]`: sorted by `dstArcSec`.
  - Each call builds a new `astroquery.mast.CatalogsClass()`, so no state is shared between threads, and sets `astroquery.mast.conf.timeout = timeout`.
  - `class Tic(Source)`: `name="tic"`, `group="tic"`, `service="MAST Catalogs (TIC)"`, `catalog_version="TIC v8.2"`, `needs=("tic_id",)`. `parse` returns `normalize(rows[0])`, or `None`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_parse_hd209458(ctx):
    r = Tic().parse(load_fixture("tic", "HD_209458"), ctx)
    assert r["ID"] == 420814525 and r["GAIA"] == "1779546757669063552" and r["version"] == "20190415"
    assert 6000 < r["Teff"] < 6100 and 7.0 < r["Tmag"] < 7.3 and r["contratio"] < 0.01

def test_normalize_missing_values(): ...   # {"ID": "5", "GAIA": "--", "HIP": "", "Teff": None} -> ID 5, GAIA None, HIP None, Teff None
def test_toi700_points_to_dr2(ctx): ...    # TOI-700 fixture -> GAIA == "5284517766615492736"
```

- [ ] **Step 2: Run the tests to confirm they fail.**

Run: `pytest tests/test_source_tic.py -v`

Expected: FAIL.

- [ ] **Step 3: Implement `tic.py` and register `Tic` after the Gaia sources.**

- [ ] **Step 4: Record the fixtures and run the tests.**

Run: `python scripts/record_fixtures.py --source tic && pytest tests/test_source_tic.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/star_report/sources tests
git commit -m "feat: TIC v8.2 source and lookups"
```

### Task 8: Identity resolution

**Files:**
- Modify: `src/star_report/resolve.py` (adds the resolution half), `scripts/record_fixtures.py` (adds `--lookups`)
- Create: `src/star_report/sources/exoplanet_archive.py` (TAP helper and host lookups only; the source class comes in Task 12), `tests/test_resolve.py`, and `tests/fixtures/lookups/<slug>.json` (recorded)

**Interfaces:**
- Consumes:
  - `parse_target` and `ParsedTarget`, plus `propagate` and `separation_arcsec` (Task 3).
  - `simbad.lookup` (Task 5).
  - `gaia.by_id`, `gaia.cone`, `gaia.dr3_from_dr2`, and `gaia.dr2_from_dr3` (Task 6).
  - `tic.by_id`, `tic.by_gaia_dr2`, and `tic.cone` (Task 7).
  - All of these return the normalized record dicts those tasks define.
- Produces:

```python
class CatalogLookup(Protocol):          # every method may raise on network failure
    def simbad(self, name: str) -> list[dict]: ...
    def gaia_by_id(self, dr3_id: str) -> dict | None: ...
    def gaia_dr3_from_dr2(self, dr2_id: str) -> list[dict]: ...   # dr2_neighbourhood rows
    def gaia_dr2_from_dr3(self, dr3_id: str) -> list[dict]: ...
    def gaia_cone(self, ra: float, dec: float, radius_arcsec: float) -> list[dict]: ...  # sorted by dist_arcsec
    def tic_by_id(self, tic: int) -> dict | None: ...
    def tic_by_gaia_dr2(self, dr2_id: str) -> list[dict]: ...
    def tic_cone(self, ra: float, dec: float, radius_arcsec: float) -> list[dict]: ...   # sorted by dstArcSec
    def toi_host(self, toi: str) -> int | None: ...    # TIC ID
    def koi_host(self, koi: str) -> int | None: ...    # KIC ID

class ResolutionError(Exception):       # .target: str, .tried: list[str]
def resolve(parsed: ParsedTarget, lookup: CatalogLookup, cfg: ResolveConfig) -> StarIdentity
class LiveLookup:      # __init__(cache: Cache, session: requests.Session, cfg: Config); implements CatalogLookup
class RecordingLookup: # __init__(inner: CatalogLookup); .calls: dict[str, Any]; .dump(path: Path)
class ReplayLookup:    # __init__(path: Path); a call that was not recorded returns None or []
```

- In `exoplanet_archive.py`:
  - `TAP_URL = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"`
  - `tap(adql: str, session: requests.Session, timeout: float) -> list[dict]`: sends `params={"query": adql, "format": "json"}`.
  - `toi_host(toi: str, session, timeout) -> int | None`: runs `select tid from toi where toipfx = '{n}'` and returns the first `tid`.
  - `koi_host(koi: str, session, timeout) -> int | None`: runs `select kepid from cumulative where kepoi_name like 'K{n:05d}%'` and returns the first `kepid`.
  - Both queries were verified on 2026-11-09: TOI 700 gives TIC 150428135, and KOI 72 gives KIC 11904151.

- [ ] **Step 1: Write the failing tests.**

`tests/test_resolve.py` has two parts:
- **Recorded replays.** For every reference star and every spelling, `resolve(parse_target(s), ReplayLookup(fixtures / "lookups" / f"{slug}.json"), cfg.resolve)` gives the expected `tic_id` and `gaia_dr3_id` from `reference_stars.yaml`.
- **A dict-backed `FakeLookup`** (defined in the test file) for each decision and warning below. Each assertion compares exact strings.

| Case | Expected |
|---|---|
| `TIC 1`. TIC row has `GAIA="10"`; dr2→dr3 gives `"11"`; SIMBAD has "Gaia DR3 11" | `resolved_via == "tic"`, `gaia_dr3_id == "11"`, `position.source == "gaia_dr3"`, `position.epoch == 2016.0`, no warnings |
| Same, but SIMBAD lists "Gaia DR3 99" | warning `TIC 1 points to Gaia DR2 10, which matches Gaia DR3 11, but SIMBAD gives Gaia DR3 99.` |
| A bright star: SIMBAD record, no Gaia match, TIC found by cone | `position.source == "simbad"`, `position.epoch == 2000.0`, `gaia_dr3_id is None` |
| The 2″ cone returns the target plus one source with ΔG = 1.2 | warning `2 Gaia DR3 sources lie within 2″ with ΔG < 3; the match may be ambiguous.` |
| TIC row `disposition="SPLIT"`, `duplicate_id=7` | warning `TIC 1 is flagged SPLIT (duplicate of TIC 7).` |
| SIMBAD `otype == "**"` | warning `SIMBAD's alf Cen is a multiple-system entry; catalog data refer to Gaia DR3 11, the source nearest its position.` |
| SIMBAD returns 2 records | warning `'Foo' matched 2 SIMBAD objects; using Foo A.` |
| `WASP-33 b` | warning `'WASP-33 b' names a planet; this report covers its host star.` |
| `TIC 1`, and the SIMBAD lookup raises `TimeoutError("t")` | identity resolves; warning `SIMBAD unavailable (TimeoutError: t); identifiers may be incomplete.` |
| `tic_by_id` raises | `ResolutionError` whose message contains `TIC v8.2 (MAST): unavailable` |
| Nothing found for `Foo` | `ResolutionError` whose message is `Could not resolve 'Foo'. Tried: SIMBAD name lookup: no match. If this is a catalog ID, add its prefix (for example 'TIC 420814525', 'Gaia DR3 1779546757669063552', 'HD 209458').` |
| `toi 700`, `KOI-72`, `Gaia DR2 …`, and coordinates | the `resolved_via` values `toi`, `koi`, `gaia_dr2`, `coords` |
| J2000 coordinates of a star with pmdec = 10″/yr. The candidate's 2016.0 position is 160″ away, but its position propagated to 2000.0 is 0.3″ away | resolves to that candidate |
| Coordinates with no candidate within 5″ at either epoch | `ResolutionError` (the tried list contains `Gaia DR3 cone: no source within 5″`) |

- [ ] **Step 2: Run the tests to confirm they fail.**

Run: `pytest tests/test_resolve.py -v`

Expected: FAIL, because `resolve` is not defined.

- [ ] **Step 3: Implement.**

The tests don't fully determine the algorithm, so it is pinned here:
1. **Query the catalog that owns the ID** (the spec §6 table):
   - `tic`: `tic_by_id`.
   - `gaia_dr3`: `gaia_by_id`.
   - `gaia_dr2`: `gaia_dr3_from_dr2`, taking the row with the smallest `angular_distance`, then `gaia_by_id`.
   - `toi`: `toi_host`, then the `tic` path.
   - `koi`: `koi_host`, then SIMBAD with `KIC n`.
   - `coords`:
     1. Call `gaia_cone(ra, dec, coord_search_arcsec)`.
     2. For each candidate, take the smaller of two separations from the input: its 2016.0 position, and its position `propagate`d to 2000.0.
     3. Pick the smallest, if it is ≤ `match_radius_arcsec`.
     4. The input epoch is unknown, and users usually paste J2000 coordinates.
   - Everything else: `simbad(query_name)`.

   If the owner lookup raises, raise `ResolutionError`. Each tried step is recorded as `"<method>: no match"` or `"<method>: unavailable"`.
2. **SIMBAD record.**
   - When there is none yet, try `simbad(f"Gaia DR3 {id}")`, then `simbad(f"TIC {n}")`.
   - Failures here become the W6 warning. They are not fatal.
3. **Gaia DR3.**
   - Take the ID from SIMBAD's `ids` (`Gaia DR3 n`) and call `gaia_by_id`.
   - Otherwise use the TIC's `GAIA` value and call `gaia_dr3_from_dr2`.
   - Otherwise do a cone search at the SIMBAD or TIC position `propagate`d to 2016.0, with radius `match_radius_arcsec`, and take the nearest source.
4. **Gaia DR2.** Take it from SIMBAD `ids`, then `gaia_dr2_from_dr3`.
5. **TIC.**
   - Take it from SIMBAD `ids` and call `tic_by_id`.
   - Otherwise call `tic_by_gaia_dr2(dr2)`.
   - Otherwise do `tic_cone` at the J2000-propagated position and take the nearest entry.
6. **Position.** Gaia DR3 if found; else SIMBAD, at epoch 2000.0; else TIC, at epoch 2000.0.
7. **`main_name`.** SIMBAD's `main_id`, else `TIC n`, else `Gaia DR3 n`.
8. **`other_ids`.**
   - From SIMBAD `ids`, keep only these prefixes: HD, HIP, HR, GJ, 2MASS, KIC, EPIC, TYC, TOI, KOI, Kepler, K2, WASP, HAT-P, KELT. The key is the prefix and the value is the full ID.
   - When there is no SIMBAD record, fill HIP, 2MASS, and KIC from the TIC row.
9. **Warnings W1–W7** use the exact strings in the table.
   - W2 runs `gaia_cone(pair_radius_arcsec)` around the Gaia position and counts sources with |ΔG| < `pair_max_delta_g`, the target included.

`LiveLookup` wraps each call as `cache.fetch(group, f"lookup.{method}", args, lambda: ...)` and returns its `.payload`. The groups are `simbad`, `gaia`, `tic`, and `exoplanet_archive`.

`RecordingLookup` keys its calls as `f"{method}:{json.dumps(args, sort_keys=True)}"`.

Recording: `python scripts/record_fixtures.py --lookups` runs every spelling of every reference star through `RecordingLookup(LiveLookup(...))` and writes `tests/fixtures/lookups/<slug>.json`.

- [ ] **Step 4: Record the lookups and run the tests.**

Run: `python scripts/record_fixtures.py --lookups && pytest tests/test_resolve.py -v`

Expected: the fixtures are written and all tests PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/star_report/resolve.py src/star_report/sources/exoplanet_archive.py scripts tests
git commit -m "feat: identity resolution with cross-matching and consistency warnings"
```

### Task 9: Derived tables, names, and flags

**Files:**
- Create: `src/star_report/derive.py`, `tests/test_derive.py`

**Interfaces:**
- Consumes: the `SourceResult.data` shapes from Tasks 5–7: `simbad`, `gaia_core` (gaia_source columns plus nested `ap` and `distance`), and `tic` (TIC column names).
- Produces:
  - `display_name(main_id: str) -> str`
  - `slugify(name: str) -> str`
  - `parameter_rows(results: Mapping[str, SourceResult], cfg: ParametersConfig) -> list[ParamRow]`
  - `disagree(a: ParamCell, b: ParamCell, sigma: float, fraction: float, absolute: float | None) -> bool`
  - `flags(identity: StarIdentity, results: Mapping[str, SourceResult], cfg: FlagsConfig) -> list[str]`
  - `description_line(results: Mapping[str, SourceResult], rows: list[ParamRow]) -> str`

**Row table.** The spec leaves the per-row sources to the plan, so they are fixed here. Rows appear in this order. A row with no values is dropped.

| key | label | unit | decimals | gaia (note) | tic | simbad |
|---|---|---|---|---|---|---|
| teff | Teff | K | 0 | `teff_gspspec`, else `teff_gspphot` ("GSP-Spec"/"GSP-Phot") | Teff, e_Teff | |
| logg | log g | dex | 2 | `logg_gspspec`, else `logg_gspphot` | logg, e_logg | |
| mh | [M/H] | dex | 2 | `mh_gspspec`, else `mh_gspphot` | MH, e_MH | |
| radius | Radius | R☉ | 3 | `radius_flame` ("FLAME") | rad, e_rad | |
| mass | Mass | M☉ | 3 | `mass_flame` | mass, e_mass | |
| lum | Luminosity | L☉ | 3 | `lum_flame` | lum, e_lum | |
| distance | Distance | pc | 1 | `r_med_photogeo` ("Bailer-Jones photogeo"), else `r_med_geo` | d, e_d | |
| age | Age | Gyr | 2 | `age_flame` | | |
| V | V | mag | 3 | | Vmag, e_Vmag | V |
| G | G | mag | 3 | `phot_g_mean_mag` | GAIAmag | |
| Tmag | T | mag | 3 | | Tmag, e_Tmag | |

Gaia errors are `(upper - lower) / 2` from the matching `_lower`/`_upper` or `r_lo_*`/`r_hi_*` columns.

**Disagreement rule.**
- With both errors: `|a-b| > sigma * hypot(ea, eb)`.
- Otherwise: `|a-b| > absolute`, when the row key is in `disagreement_absolute`. Else `|a-b| / mean(|a|, |b|) > fraction`.
- A row's `disagree` is true when any pair of its cells disagrees.

- [ ] **Step 1: Write the failing tests.**

```python
@pytest.mark.parametrize("main_id,display,slug", [
    ("HD 209458", "HD 209458", "HD_209458"),
    ("NAME Barnard's star", "Barnard's star", "Barnards_star"),
    ("* alf Lyr", "alf Lyr", "alf_Lyr"),
    ("V* V1298 Tau", "V1298 Tau", "V1298_Tau"),
    ("TIC 420814525", "TIC 420814525", "TIC_420814525"),
    ("2MASS J22031077+1853036", "2MASS J22031077+1853036", "2MASS_J22031077+1853036"),
    ("***", "***", "star"),
])
def test_names(main_id, display, slug): ...   # display_name(main_id) == display; slugify(display) == slug

def test_gaia_prefers_gspspec(): ...          # cell == ParamCell(value=5950, err=40, note="GSP-Spec") from 5910/5990 bounds
def test_gaia_falls_back_to_gspphot(): ...
def test_sigma_rule(): ...                    # 6000±50 vs 6300±50 -> True; 6000±100 vs 6200±100 -> False
def test_fraction_rule(): ...                 # radius 1.0 vs 1.2 (no errors) -> True; 1.0 vs 1.05 -> False
def test_absolute_rule_for_logg(): ...        # 4.40 vs 4.55 -> False; 4.40 vs 4.70 -> True (no errors)
def test_rows_skip_missing_sources(): ...     # only tic ok -> rows have only "tic" cells; failed gaia ignored
def test_description_line(): ...             # sp_type "G0V", V 7.65, distance 48.3 -> "G0V, V = 7.65, 48.3 pc"; parts drop out when missing
def test_core_flags(): ...
    # ruwe 2.4 -> "RUWE 2.4: possible unresolved companion"; contratio 0.35 -> "TIC contamination ratio 0.35";
    # phot_variable_flag "VARIABLE" -> "Gaia DR3 flags the star as photometrically variable";
    # non_single_star 1 -> "Gaia DR3 non-single-star flag set"; all below thresholds -> []
```

- [ ] **Step 2: Run the tests to confirm they fail.**

Run: `pytest tests/test_derive.py -v`

Expected: FAIL.

- [ ] **Step 3: Implement `derive.py`.**

- **`display_name`** strips one leading prefix from `("NAME ", "V* ", "EM* ", "** ", "* ")`, then collapses spaces.
- **`slugify`:**
  1. Replace runs of whitespace with `_`.
  2. Drop characters outside `[A-Za-z0-9+._-]`.
  3. Collapse runs of `_` and strip `._-` from both ends.
  4. Return `"star"` if the result is empty.
- **`description_line`** joins `sp_type`, `V = {V:.2f}`, and `{distance:.1f} pc`. The distance is the Gaia distance cell, else the TIC one.

- [ ] **Step 4: Run the tests.**

Run: `pytest tests/test_derive.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/star_report/derive.py tests/test_derive.py
git commit -m "feat: parameter table, disagreement flags, names and slugs"
```

### Task 10: HTML report, data.json, and terminal summary (core sections)

**Files:**
- Create:
  - `src/star_report/render/__init__.py`, `render/html.py`, `render/terminal.py`
  - `render/templates/report.html.j2`, `_parameters.html.j2`, `_sources.html.j2`
  - `tests/test_render.py`
- Modify: `tests/conftest.py`, which adds `make_report`.

**Interfaces:**
- Consumes: `Report` and related models (Task 2); `derive.*` (Task 9).
- Produces:
  - `render_html(report: Report, base_dir: Path) -> str`. It reads PNGs listed in `report.plots` relative to `base_dir` and embeds them as base64.
  - `write_outputs(report: Report, out_dir: Path) -> Path`. It writes `out_dir/report.html` and `out_dir/data.json` (indent 2) and returns the HTML path.
  - `link_citations(text: str) -> markupsafe.Markup`. It escapes the text and turns each `[bibcode]` into a link `<a href="https://ui.adsabs.harvard.edu/abs/{bibcode}/abstract">{bibcode}</a>`.
    - It uses `models.BIBCODE_RE`.
    - Split on the pattern before escaping, so the `&` inside `A&A` bibcodes survives.
  - `STATUS_LABELS = {"ok": "ok", "empty": "no data found", "error": "unavailable", "timeout": "timed out", "skipped": "skipped"}`
  - `print_summary(report: Report, report_path: Path | None, console: rich.console.Console) -> None`
- Produces in `tests/conftest.py`:
  - `make_report(slug: str = "HD_209458", **overrides) -> Report`. It parses the recorded fixture of every name in `REPORT_SOURCES` through its source class, then fills `parameters` and `flags` with `derive`.
  - `REPORT_SOURCES` starts as `["simbad", "gaia_core", "gaia_neighbors", "tic"]`. Each later source task appends to it.

**Page layout** (spec §9). The page is a single Jinja2 `Environment(loader=PackageLoader("star_report", "render/templates"), autoescape=True)`, with inline CSS that has light and dark variants through `prefers-color-scheme`. It shows, in this order:
1. **Header:** `display_name` as the `<h1>`; the TIC, Gaia DR3, HD/HIP, and 2MASS IDs; RA/Dec in sexagesimal and degrees, with the epoch label; and `description_line`.
2. **Warnings**, only when there are any.
3. **Overview.** Until Task 26, this shows only `LLM sections not included: {reason}` when `llm.status != "ok"`.
4. **Parameters** (`_parameters.html.j2`):
   - Columns: Gaia, TIC, SIMBAD.
   - A cell shows `value ± err` with the row's decimals, plus the note in small text.
   - A disagreeing row is marked `⚠` and has a `title` explaining the thresholds.
5. **Flags.**
6. **The sources footer** (`_sources.html.j2`), inside `<details>`: source, status label, `cache` or `live`, `elapsed_s`, catalog version, and error.

Later tasks add the companions, inventory, plots, and literature partials between the flags and the footer.

- [ ] **Step 1: Write the failing tests.**

```python
def test_full_report_renders(tmp_path):
    html = render_html(make_report(), tmp_path)
    for s in ["HD 209458", "TIC 420814525", "Gaia DR3 1779546757669063552", "Teff", "<details"]:
        assert s in html
    assert "{{" not in html and "{%" not in html
    assert re.search(r'<(img|script|link)[^>]+(src|href)="https?://', html) is None   # self-contained

def test_all_sources_failed_still_renders(tmp_path): ...
    # every result -> status "error", error "ConnectionError: down"; html contains "unavailable" once per source,
    # contains "No parameters available", and no traceback text
def test_empty_and_error_are_distinct(tmp_path): ...    # "no data found" vs "unavailable: ConnectionError: down"
def test_no_gaia_identity(tmp_path): ...                # position.source "simbad" -> header shows "J2000 (SIMBAD)"
def test_link_citations():
    out = str(link_citations("Hot Jupiter [2000ApJ...529L..45C] & [2008A&A...481..529X]."))
    assert 'href="https://ui.adsabs.harvard.edu/abs/2000ApJ...529L..45C/abstract"' in out and "&amp; " in out
    assert "2008A&amp;A...481..529X</a>" in out
def test_write_outputs(tmp_path): ...                   # report.html + data.json; json.loads(...)["schema_version"] == 1;
                                                         # Report.model_validate(json) works; second call overwrites
def test_terminal_summary(tmp_path):
    console = Console(record=True, width=110)
    print_summary(make_report(), tmp_path / "report.html", console)
    text = console.export_text()
    assert "HD 209458" in text and "TIC 420814525" in text and str(tmp_path / "report.html") in text
    assert text.count("gaia_core") == 1   # one status line per source
```

- [ ] **Step 2: Run the tests to confirm they fail.**

Run: `pytest tests/test_render.py -v`

Expected: FAIL.

- [ ] **Step 3: Implement the renderer, the templates, and `print_summary`.**

`print_summary` prints, using rich:
- a title panel with the name, IDs, and description
- a parameters table with one value per row, Gaia first, then TIC, then SIMBAD
- flags and warnings in yellow
- one status line per source, such as `✓ gaia_core  ok  (cache)  0.4 s`
- the report path

- [ ] **Step 4: Run the tests.**

Run: `pytest tests/test_render.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/star_report/render tests/test_render.py tests/conftest.py
git commit -m "feat: HTML report, data.json and terminal summary for the core section"
```

### Task 11: Pipeline and command line

**Files:**
- Create: `src/star_report/pipeline.py`, `src/star_report/cli.py`, `tests/test_pipeline.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 1–10.
- Produces:

```python
@dataclass
class RunOptions: out_dir: Path; refresh: bool = False; verbose: bool = False
@dataclass
class TargetOutcome: target: str; status: Literal["ok", "unresolved", "crashed"]
                     report_path: Path | None = None; report: Report | None = None; message: str | None = None
@dataclass
class PipelineDeps:
    lookup: Callable[[Cache, requests.Session, Config], CatalogLookup] = LiveLookup
    sources: Callable[[Config], list[Source]] = build_sources
    plots: Callable[[Report, Path, Config, Cache], tuple[dict[str, str], dict[str, str]]] = no_plots  # Task 21 swaps in make_plots
    writer: Callable[[Report, Config, Cache], LLMBlock] = no_llm                                          # Task 26 swaps in write_sections
def run_target(target: str, cfg: Config, opts: RunOptions, cache: Cache, console: Console,
               deps: PipelineDeps = PipelineDeps()) -> TargetOutcome
def build_parser() -> argparse.ArgumentParser
def read_targets_file(path: Path) -> list[str]
def main(argv: Sequence[str] | None = None) -> int
```

- `no_plots` returns `({}, {})`.
- `no_llm` returns `LLMBlock(status="skipped", reason="LLM writer not available yet")`.

`run_target` goes through these steps in order:
1. `parse_target`.
2. `resolve`. On `ResolutionError`, return `unresolved` with the error message.
3. `run_sources(deps.sources(cfg), SourceContext(...))`.
4. Build the `Report`, filling `parameters` and `flags` with `derive`.
5. `out = opts.out_dir / slugify(display_name(main_name))`. Replace `out/plots` with an empty directory.
6. If `cfg.plots.enabled`, call `deps.plots`.
7. If `cfg.llm.enabled`, call `deps.writer`. Otherwise set `LLMBlock(status="skipped", reason="LLM disabled (--no-llm or config)")`.
8. `write_outputs`.
9. `print_summary`.

**CLI options** (spec §3):
- `TARGET...`
- `--from-file PATH`
- `--out DIR`
- `--no-llm`, which sets `cfg.llm.enabled=False`
- `--no-plots`, which sets `cfg.plots.enabled=False`
- `--refresh`
- `--open/--no-open`, with `argparse.BooleanOptionalAction`, default `None`
- `--config PATH`
- `-v/--verbose`, which sets up rich logging at INFO; the runner logs one line per source

**`main` behavior:**
- Load the config. On `ConfigError`, print it and return 2.
- With no targets and no file, call `parser.error(...)`, which exits with code 2.
- A missing targets file prints a message and returns 2.
- Build the cache once per run: `Cache(cfg.cache.dir, cfg.cache.expiry_days, cfg.cache.empty_expiry_days, refresh=args.refresh)`.
- Run each target in order inside `try/except Exception`. On an exception, print the rich traceback and record `crashed`.
- Open the browser when `--open` is given, or when `--open` is unset, there is exactly one target, and `cfg.output.open_single` is true.
- Return 1 if any outcome is not `ok`, else 0.

- [ ] **Step 1: Write the failing tests.**

The pipeline tests use fake `PipelineDeps`:
- `lookup` returns a `FakeLookup` (reuse the one from `test_resolve`, moved to `conftest`).
- `sources` returns two fake sources.

```python
def test_run_target_writes_outputs(tmp_path): ...       # outcome.status "ok"; tmp_path/"reports"/"HD_209458"/"report.html" exists
def test_unresolved_target(tmp_path): ...                 # status "unresolved", message starts "Could not resolve"
def test_no_llm_records_reason(tmp_path): ...            # cfg.llm.enabled False -> report.llm.reason == "LLM disabled (--no-llm or config)"

def test_targets_file(tmp_path):
    p = tmp_path / "t.txt"; p.write_text("# list\nHD 209458\n\n  TOI-700  \n")
    assert read_targets_file(p) == ["HD 209458", "TOI-700"]

def test_exit_codes(monkeypatch, tmp_path): ...
    # monkeypatch pipeline.run_target: ok,ok -> 0; ok,unresolved -> 1; first target raises RuntimeError, second ok -> 1 and the second still ran;
    # main(["--config", str(bad_yaml)]) -> 2; main([]) raises SystemExit(2)
def test_open_defaults(monkeypatch, tmp_path): ...       # webbrowser.open called once for one target; not for two; --no-open suppresses
def test_flags_reach_config(monkeypatch, tmp_path): ...  # --no-llm/--no-plots/--out/--refresh arrive in cfg/opts/cache passed to run_target
```

- [ ] **Step 2: Run the tests to confirm they fail.**

Run: `pytest tests/test_pipeline.py tests/test_cli.py -v`

Expected: FAIL.

- [ ] **Step 3: Implement `pipeline.py` and `cli.py`** as specified above.

- [ ] **Step 4: Run the whole suite, then a live smoke run.**

Run: `pytest -v`

Expected: all PASS.

Then run: `star-report "HD 209458" --no-llm --no-plots --no-open --out /tmp/sr-smoke`

Expected:
- The terminal summary shows TIC 420814525.
- `/tmp/sr-smoke/HD_209458/report.html` opens with the parameter table filled.
- Rerunning the same command finishes in under 5 s, from the cache.

- [ ] **Step 5: Commit.**

```bash
git add src/star_report/pipeline.py src/star_report/cli.py tests
git commit -m "feat: per-target pipeline and star-report command"
```

**Milestone 1 is done** when `star-report "HD 209458" --no-llm --no-plots` writes a core report.

---

## Milestone 2: Planets, binarity, and variability

Every source task from here on follows the same pattern as Tasks 5–7:
1. Write the parser tests.
2. Run them and see them fail.
3. Implement the source and register it in `build_sources`, in the order the tasks appear.
4. Record fixtures with `python scripts/record_fixtures.py --source <name>`.
5. Run the tests and see them pass.
6. Append the name to `REPORT_SOURCES` in `tests/conftest.py`.
7. Commit.

The source-task steps below give only the parts that differ.

### Task 12: NASA Exoplanet Archive source

**Files:**
- Modify: `sources/exoplanet_archive.py`, `sources/registry.py`, `tests/conftest.py`
- Create: `tests/test_source_exoplanet_archive.py`, and the fixtures in `tests/fixtures/exoplanet_archive/`

**Facts** (verified 2026-11-09). TAP `format=json` returns a list of row dicts.
- **`pscomppars`:**
  - Columns: `pl_name`, `hostname`, `tic_id` (`"TIC 150428135"`), `gaia_dr3_id` (`"Gaia DR3 …"`), `pl_orbper`, `pl_rade`, `pl_bmasse`, `discoverymethod`, `disc_year`.
  - There is no `gaia_id` column.
  - TOI-700 has 4 planets.
- **`toi`:**
  - Columns: `toi` (the string `"700.01"`), `tid` (int), `tfopwg_disp`, `pl_orbper`, `pl_trandep` (ppm), `pl_rade`.
  - TOI-700 has 4 rows. HD 209458 is TOI 5972.01.
- **`cumulative`:**
  - Columns: `kepoi_name` (`"K00072.01"`), `kepler_name`, `kepid`, `koi_disposition`, `koi_period`, `koi_prad`.
  - Kepler-10 has 2 rows.
- **`k2pandc`:**
  - Columns: `pl_name`, `epic_hostname` (`"EPIC n"`), `epic_candname`, `tic_id`, `disposition`, `pl_orbper`, `pl_rade`.
  - There is one row per reference, so a candidate repeats.

**Interfaces:**
- **`class ExoplanetArchive(Source)`:**
  - `name="exoplanet_archive"`, `group="exoplanet_archive"`, `service="NASA Exoplanet Archive TAP"`.
  - `query` is `{"tic", "gaia_dr3", "kic", "epic"}`. KIC and EPIC numbers are parsed from `identity.other_ids`.
- **`fetch`** runs only the queries whose key is known:

  | Query | ADQL |
  |---|---|
  | planets | `select pl_name, pl_orbper, pl_rade, pl_bmasse, discoverymethod, disc_year from pscomppars where tic_id = 'TIC {tic}'`. Without a TIC, match on `gaia_dr3_id = 'Gaia DR3 {id}'`. |
  | tois | `select toi, tfopwg_disp, pl_orbper, pl_trandep, pl_rade from toi where tid = {tic}` |
  | kois | `select kepoi_name, kepler_name, koi_disposition, koi_period, koi_prad from cumulative where kepid = {kic}` |
  | k2 | `select pl_name, epic_candname, disposition, pl_orbper, pl_rade from k2pandc where epic_hostname = 'EPIC {epic}'` |

  It returns `{"planets", "tois", "kois", "k2"}`, each a list of rows.
- **`parse`** returns:

  ```
  {"planets": [{"name", "period_d", "radius_re", "mass_me", "method", "year"}],
   "tois":    [{"toi", "disposition", "period_d", "depth_ppm", "radius_re"}],
   "kois":    [{"koi", "name", "disposition", "period_d", "radius_re"}],
   "k2":      [{"name", "candidate", "disposition", "period_d", "radius_re"}]}
  ```

  - Each list is sorted by its first key.
  - `k2` is de-duplicated by `candidate`, and the first row wins.
  - It returns `{}` when all four lists are empty.
- **`known_periods(results: Mapping[str, SourceResult]) -> list[tuple[str, float]]`** gives labelled periods from the planets and TOIs. Task 22 uses it.

- [ ] **Tests** (`tests/test_source_exoplanet_archive.py`):

```python
def test_toi700(ctx):
    d = ExoplanetArchive().parse(load_fixture("exoplanet_archive", "TOI-700"), ctx)
    assert [p["name"] for p in d["planets"]] == ["TOI-700 b", "TOI-700 c", "TOI-700 d", "TOI-700 e"]
    assert {"700.01", "700.02", "700.03", "700.04"} <= {t["toi"] for t in d["tois"]}
def test_kepler10(ctx): ...              # "K00072.01" in kois with disposition "CONFIRMED"; "Kepler-10 b" in planets
def test_k2_deduplicated(ctx): ...       # synthetic payload, three rows for "EPIC 1.01" -> one k2 entry
def test_only_known_ids_queried(ctx, monkeypatch): ...   # patch tap(); identity without KIC/EPIC -> exactly 2 ADQL calls
def test_known_periods(): ...            # planets + TOIs -> [("TOI-700 d", 37.42...), ("TOI 700.01", ...)] as floats
```

- [ ] **Implement, record, run, and commit:** `feat: NASA Exoplanet Archive source`.

### Task 13: ExoFOP-TESS source

**Files:**
- Create: `sources/exofop.py`, `tests/test_source_exofop.py`, and the fixtures in `tests/fixtures/exofop/`
- Modify: `sources/registry.py`, `tests/conftest.py`

**Facts** (verified 2026-11-09):
- **URL:** `GET https://exofop.ipac.caltech.edu/tess/target.php?id={tic}&json`.
- **Top-level keys:** `basic_info`, `coordinates`, `tois`, `ctois`, `planet_parameters`, `stellar_parameters`, `stellar_companions`, `magnitudes`, `imaging`, `spectroscopy`, `time_series`, `files`. There are also `*_units` dicts, which hold no data.
- **`tois[]` keys:** `toi` (`"TOI 5972.01"`), `disp_tess`, `disp_tfop`, `sector` (the comma-separated string `"56,82"`), `notes`, `date`.
- **Follow-up counts** are the lengths of `imaging`, `spectroscopy`, and `time_series`. Each entry in `files[]` has an `ftype`.
- **Recorded counts:**

  | Star | TOIs | Dispositions | Imaging | Spectroscopy | Time series |
  |---|---|---|---|---|---|
  | HD 209458 | TOI 5972.01 | KP / KP | 3 | 0 | 0 |
  | TOI-700 | 700.01–700.04 | CP | 3 | 3 | 16 |

**Interfaces:**
- **`class ExoFop(Source)`:**
  - `name="exofop"`, `group="exofop"`, `service="ExoFOP-TESS"`, `needs=("tic_id",)`.
  - `fetch` uses `ctx.session`. A non-JSON response raises `ValueError(f"ExoFOP returned non-JSON content ({content_type})")`.
- **`parse`** returns:

  ```
  {"url": "https://exofop.ipac.caltech.edu/tess/target.php?id={tic}",
   "tois": [{"toi": "5972.01", "disp_tess", "disp_tfop", "sectors": [56, 82], "notes"}],
   "ctois": [{"ctoi", "disposition", "notes"}],
   "followup": {"imaging": n, "spectroscopy": n, "time_series": n},
   "files": {ftype: count}}
  ```

  - Missing keys count as empty.
  - It returns `{}` when there are no TOIs, no CTOIs, no follow-up, and no files.

- [ ] **Tests:**

```python
def test_hd209458(ctx):
    d = ExoFop().parse(load_fixture("exofop", "HD_209458"), ctx)
    assert [(t["toi"], t["disp_tfop"], t["sectors"]) for t in d["tois"]] == [("5972.01", "KP", [56, 82])]
    assert d["followup"]["imaging"] >= 3
def test_toi700(ctx): ...                 # {"700.01".."700.04"} <= tois; followup time_series >= 16, spectroscopy >= 3
def test_missing_keys(ctx): ...           # parse({"basic_info": {}}, ctx) == {}
def test_html_response_is_error(ctx): ... # fake session returns text/html -> run_source status "error", message contains "non-JSON"
```

- [ ] **Implement, record, run, and commit:** `feat: ExoFOP-TESS source`.

### Task 14: Gaia DR3 binarity and variability sources

**Files:**
- Modify: `sources/gaia.py`, `sources/registry.py`, `tests/conftest.py`
- Create: `tests/test_source_gaia_extra.py`, and the fixtures in `tests/fixtures/gaia_nss/` and `tests/fixtures/gaia_vari/`

**Facts** (verified 2026-11-09):
- **`gaiadr3.nss_two_body_orbit`** columns: `source_id`, `nss_solution_type`, `period`, `period_error`, `eccentricity`, `eccentricity_error`, `semi_amplitude_primary`, `inclination`, `mass_ratio`.
- **`gaiadr3.vari_summary`** has boolean `in_vari_*` flags.
- **`gaiadr3.vari_classifier_result`** has `classifier_name`, `best_class_name`, `best_class_score`.
- **Periods** come from these tables, in days. Frequencies are in d⁻¹.

  | `in_vari_` flag | table | period |
  |---|---|---|
  | `eclipsing_binary` | `vari_eclipsing_binary` | `1/frequency` |
  | `long_period_variable` | `vari_long_period_variable` | `1/frequency` |
  | `short_timescale` | `vari_short_timescale` | `1/frequency` |
  | `ms_oscillator` | `vari_ms_oscillator` | `1/frequency1` |
  | `rotation_modulation` | `vari_rotation_modulation` | `best_rotation_period` |
  | `rrlyrae` | `vari_rrlyrae` | `pf` |
  | `cepheid` | `vari_cepheid` | `pf` |
  | `planetary_transit` | `vari_planetary_transit` | `transit_period` |
  | `compact_companion` | `vari_compact_companion` | `period` |

**Interfaces:**
- **`GaiaNss(Source)`:**
  - `name="gaia_nss"`, `group="gaia"`, `needs=("gaia_dr3_id",)`.
  - `parse` returns `[{"type", "period_d", "period_err", "ecc", "k1_kms", "inclination", "mass_ratio"}]`.
- **`GaiaVari(Source)`:**
  - `name="gaia_vari"`.
  - `fetch` runs `SELECT * FROM gaiadr3.vari_summary WHERE source_id = {id}`. With no row, it returns `{"summary": None}`. Otherwise it runs the classifier query plus one period query for each true flag in the table, and returns `{"summary", "classifier", "periods": {flag: rows}}`.
  - `parse` returns `{"classes": [{"classifier", "class", "score"}], "periods": [{"type", "period_d"}]}`, or `{}`.

- [ ] **Tests.** Use synthetic payloads to pin the rules:
  - frequency 0.25 → 4.0 d
  - a missing period column is skipped
  - `{"summary": None}` → `{}`

  For all four reference stars, the recorded fixtures must parse to a `dict`, a `list`, or `None` without raising.

- [ ] **Implement, record, run, and commit:** `feat: Gaia DR3 binarity and variability sources`.

### Task 15: VizieR framework and companion catalogs

**Files:**
- Create: `sources/vizier.py`, `tests/test_source_vizier.py`, and the fixtures in `tests/fixtures/{wds,sb9,vsx,wide_binaries}/`
- Modify: `sources/registry.py`, `tests/conftest.py`

**Facts** (verified 2026-11-09):
- **Client construction:**
  - Build each client as `Vizier(vizier_server=pick_server(cfg.servers.vizier), columns=[...], row_limit=200, timeout=cfg.sources.timeout_s)`.
  - Pass the catalog to `query_region` or `query_constraints`. Passing it to the constructor returned nothing.
  - Setting `conf.server` after import has no effect.
- **Positions.** Add `"_RAJ2000"` and `"_DEJ2000"`, in degrees, to the columns and compute separations yourself. The native `RAJ2000` can come back as a sexagesimal string.
- **Constraint values** are bare: `Source1="123"` works, but a leading `=` returned nothing.
- **Catalogs:**

  | Catalog | Tables and columns | Notes |
  |---|---|---|
  | WDS `B/wds/wds` | `WDS`, `Disc`, `Comp`, `Obs1`, `Obs2`, `Nobs`, `pa1`, `pa2`, `sep1`, `sep2`, `mag1`, `mag2`, `SpType` | J2000. `pa2` and `sep2` are at epoch `Obs2`. |
  | SB9 `B/sb9` | `main` (`Seq`, `Name`, `Comp`, `Sp1`, `Sp2`, `mag1`, `mag2`) and `orbits` (`Seq`, `Per`, `e`, `K1`, `K2`, `omega`, `T0`, `V0`, `Grade`) | The tables join on `Seq`. |
  | VSX `B/vsx/vsx` | `Name`, `OID`, `Type`, `Period`, `max`, `min`, `Sp` | HD 209458 is V376 Peg. |
  | El-Badry+2021 `J/MNRAS/506/2269/catalog` | `Source1`, `Source2`, `theta` (arcsec), `sepAU`, `R` (chance alignment), `BinType` | Source IDs are EDR3, which are the same as DR3. |

- **Flaky mirror.** The Harvard mirror sometimes answers with empty tables during backend outages, without raising. The 1-day expiry for empty cache entries (Task 2) limits the damage.

**Interfaces:**
- **`@dataclass(frozen=True) class VizierSpec`:** `name: str`, `catalog_key: str`, `columns: tuple[str, ...]`, `mode: Literal["cone", "gaia_id"] = "cone"`, `gaia_columns: tuple[str, ...] = ()`.
- **`vizier_client(cfg: Config, columns: Sequence[str], row_limit: int = 200) -> VizierClass`**
- **`class VizierSource(Source)`:**
  - `__init__(spec, parser)`, with `group="vizier"` and `service=f"VizieR {catalog}"`.
  - The catalog is `cfg.sources.vizier_catalogs[catalog_key]`.
  - Cone mode searches at `propagate(position, 2000.0)` with `radius_arcsec[name]`.
  - `fetch` returns `{table_name: records(table)}`.
- **Sources and their parse output:**

  | Source | Parse output | Notes |
  |---|---|---|
  | `wds` | `[{"wds", "disc", "comp", "last_obs", "pa", "sep_arcsec", "mag1", "mag2", "dist_arcsec"}]` | |
  | `sb9` | `[{"name", "period_d", "ecc", "k1_kms", "k2_kms", "grade"}]` | Two-step fetch: a cone on `B/sb9/main`, then `query_constraints(catalog="B/sb9/orbits", Seq=...)` for each match. |
  | `vsx` | `[{"name", "type", "period_d", "max", "min", "dist_arcsec"}]` | |
  | `wide_binaries` | `[{"companion", "sep_arcsec", "sep_au", "R_chance", "type"}]` | Mode `gaia_id`, `needs=("gaia_dr3_id",)`. Two constraint queries, one on `Source1` and one on `Source2`. |

  Each parse output is sorted by `dist_arcsec` or by separation.

- [ ] **Tests:**

```python
def test_vsx_hd209458(ctx):
    assert VSX_SOURCE.parse(load_fixture("vsx", "HD_209458"), ctx)[0]["name"] == "V376 Peg"
def test_wds_separation_computed(ctx): ...    # synthetic rows with _RAJ2000/_DEJ2000 -> dist_arcsec within 0.01" of truth
def test_wide_binary_target_as_source2(ctx): ...   # companion = Source1 value
def test_sb9_two_step(ctx, monkeypatch): ...  # fake client: main match Seq 42 -> orbits query_constraints(Seq="42") -> period from Per
def test_constraint_value_is_bare(ctx, monkeypatch): ...  # fake client records kwargs: Source1="4472832130942575872"
def test_empty_tablelist_is_empty(ctx): ...   # run_source with fake client returning [] -> status "empty"
```

- [ ] **Implement, record** (`--source wds --source sb9 --source vsx --source wide_binaries`), **run, and commit:** `feat: VizieR companion and variability catalogs`.

### Task 16: Planets, binarity, and variability in the report

**Files:**
- Modify: `derive.py`, `render/templates/report.html.j2`, `render/terminal.py`, `tests/test_derive.py`, `tests/test_render.py`
- Create: `render/templates/_companions.html.j2`

**Interfaces:** `derive.flags` adds these exact strings, in this order:
- `f"{n} confirmed planet{'s' if n != 1 else ''}"`
- `"TOIs: " + ", ".join(f"{toi} ({disposition})")`
- `"KOIs: " + …` in the same form
- `f"Gaia DR3 {type} orbit, P = {period_d:.2f} d"`, or `f"Gaia DR3 {type} solution"` when there is no period
- `f"Gaia DR3 variability class: {class}"`
- `f"Gaia DR3 {type} period {period_d:.3f} d"`
- `f"VSX: {name}, type {type}"`, plus `f", P = {period_d} d"` when a period is present
- `f"WDS {wds} {disc} {comp}: {sep_arcsec:.1f}″ (observed {last_obs})"`
- `f"SB9 orbit: P = {period_d} d, K1 = {k1_kms} km/s"`
- `f"Gaia wide binary: Gaia DR3 {companion} at {sep_arcsec:.1f}″ ({sep_au:.0f} au), R = {R_chance:.2g}"`

**Section layout** (`_companions.html.j2`), placed after the flags:
- **Planets and candidates:** confirmed planets, TOIs, KOIs, and K2 tables.
- **Binarity:** RUWE, NSS orbits, WDS, SB9, wide binaries.
- **Variability:** Gaia classes and periods, VSX.

Each subsection shows its source's status label when that source is not `ok`, so `empty` and `error` stay distinct.

**Terminal:** a `Planets:` line with the planet count and the TOI list.

- [ ] **Tests:**
  - Each new flag string, from synthetic results.
  - `render_html(make_report("TOI-700"))` contains `TOI-700 d` and `700.01`.
  - The HD 209458 report contains `V376 Peg`.
  - The terminal output contains `Planets:`.
- [ ] **Run, implement, run, and commit:** `feat: planets, binarity and variability section`.

## Milestone 3: Data inventory

### Task 17: MAST observations source

**Files:**
- Create: `sources/mast.py`, `tests/test_source_mast.py`, and the fixtures in `tests/fixtures/mast/`
- Modify: `sources/registry.py`, `tests/conftest.py`

**Facts** (verified 2026-11-09):
- **The query.**
  - `Observations.query_region(SkyCoord(ra, dec, unit="deg"), radius="3 arcsec")` matches footprints, so it returns HST and JWST fields that contain the star.
  - For HD 209458 it returned 4691 rows in 13.5 s: HST 3838, HLA 788, JWST 36, HLSP 12, TESS 7, and others.
  - `dataproduct_type` comes in mixed case.
- **TESS rows:**
  - `obs_collection` is `"TESS"` and `provenance_name` is `"SPOC"`.
  - 2-min: `obs_id` ends in `-s`, and `t_exptime` is 120.
  - 20-s: `obs_id` ends in `-a_fast`, and `t_exptime` is 20.
  - FFI: `target_name` is `"TESS FFI"`.
  - The sector is in `sequence_number`.
  - Rows whose `obs_id` holds two sector tags (for example `s0014-s0086`) are multi-sector products. Skip them.
- **HLSP rows:** `obs_collection` is `"HLSP"`, and the pipeline is in `provenance_name` (QLP, TESS-SPOC, TARS, TICA, …).
- **Kepler rows:** `obs_collection` is `"Kepler"`, and `t_exptime` is 60 or 1800. `KeplerFFI` is a separate collection.

**Interfaces:**
- **`class MastObservations(Source)`:** `name="mast"`, `group="mast"`, `service="MAST Observations"`.
- **`query`** is `{"ra", "dec", "radius"}` at J2000.
- **`fetch`:**
  - Builds a new `ObservationsClass()` and sets `astroquery.mast.conf.timeout`.
  - Calls `query_region`, then pre-aggregates the result, because HST alone can return thousands of rows.
  - Returns `{"counts": [{"collection", "instrument", "product", "n"}], "rows": [...]}`.
    - `counts` covers every collection except TESS, HLSP, Kepler, and K2.
    - `rows` holds the trimmed TESS, HLSP, Kepler, and K2 rows, with keys `obs_collection`, `provenance_name`, `project`, `target_name`, `obs_id`, `product`, `t_exptime`, `sequence_number`.
- **`parse`** returns:

  ```
  {"tess": {"spoc_120s": [...], "spoc_20s": [...], "ffi": [...]},
   "hlsp": [{"provenance", "n", "sectors"}],
   "kepler": {"n_long", "n_short"}, "k2": {"n_long", "n_short"},
   "other": [{"collection", "instrument", "product", "n"}],
   "portal_url": f"https://mast.stsci.edu/portal/Mashup/Clients/Mast/Portal.html?searchQuery={ra:.5f}%20{dec:.5f}"}
  ```

  - The sector lists are sorted and unique.
  - `other` is sorted by `n`, descending.

- [ ] **Tests:**
  - **Synthetic rows:**
    - The 2-min, 20-s, and FFI rows are classified correctly.
    - A multi-sector row is skipped.
    - HLSP rows are grouped by provenance.
    - Kepler rows are split by cadence.
  - **The recorded HD 209458 fixture:**
    - `{56, 82} <= set(tess["spoc_120s"]) | set(tess["ffi"])`
    - `any(o["collection"] == "HST" for o in other)`
- [ ] **Implement, record, run, and commit:** `feat: MAST observation inventory source`.

### Task 18: TESS sectors (tess-point and TESScut)

**Files:**
- Create: `sources/tess_pointing.py`, `tests/test_source_tess_pointing.py`, and the fixtures in `tests/fixtures/tess_pointing/`
- Modify: `sources/registry.py`, `tests/conftest.py`

**Facts** (verified 2026-11-09):
- **tess-point:**
  - Import: `from tess_stars2px import tess_stars2px_function_entry`.
  - Call `tess_stars2px_function_entry(starIDs, starRas, starDecs)` with array-like arguments.
  - It returns `(outID, eclipLong, eclipLat, sector, camera, ccd, colPix, rowPix, scinfo)`.
  - HD 209458 gives sectors 56 and 82. TOI-700 gives 37 sectors.
  - tess-point has no dates.
- **tesswcs:**
  - `tesswcs.pointings` is an astropy Table with columns `Cycle`, `Sector`, `RA`, `Dec`, `Roll`, `Start`, `End`. Start and End are JD, and future dates are nominal.
  - `tesswcs.latest_sector` is stale. Don't use it.
- **TESScut:**
  - `GET https://mast.stsci.edu/tesscut/api/v0.1/sector?ra={ra}&dec={dec}&radius=0` returns `{"results": [{"sectorName": "tess-s0056-1-2", "sector": "0056", "camera": "1", "ccd": "2"}]}`.
  - These are the sectors whose FFIs have been released.

**Interfaces:**
- **`class TessSectors(Source)`:** `name="tess_pointing"`, `group="mast"`, `service="tess-point + TESScut"`.
- **`fetch`** returns `{"on_silicon": [{"sector", "camera", "ccd"}], "released": list[int] | None, "released_error": str | None}`. A TESScut failure does not fail the source.
- **`sector_dates() -> dict[int, tuple[datetime, datetime]]`** reads `tesswcs.pointings` once per process.
- **`classify(on_silicon, released, dates, now) -> dict`** is pure:
  - `observed`: released. When TESScut failed, use `End < now` instead, and set `"release_unknown": True`.
  - `in_progress`: `Start <= now <= End` and not released.
  - `pending`: `End < now` and not released.
  - `upcoming`: `Start > now`, or a sector beyond the date table. Each entry is `{"sector", "start"}`.
  - It also returns `"cameras": {sector: "cam/ccd"}`.
- **`parse`** calls `classify(..., sector_dates(), ctx.now())` and returns `{}` when no sector covers the star.

- [ ] **Tests:**
  - **`classify`**, with a 4-sector fake date table and a fixed `now`, puts each sector in the right bucket. This includes the TESScut-failed path.
  - **The recorded HD 209458 fixture** has 56 and 82 in `observed`.
  - **The recorded TOI-700 fixture** has ≥ 30 sectors in `observed` (`now` fixed at 2026-11-09).
- [ ] **Implement, record, run, and commit:** `feat: TESS sector coverage source`.

### Task 19: ESO and Keck archive sources

**Files:**
- Create: `sources/eso.py`, `sources/koa.py`, `tests/test_source_archives.py`, and the fixtures in `tests/fixtures/{eso,koa}/`
- Modify: `sources/registry.py`, `tests/conftest.py`

**Facts** (verified 2026-11-09):
- **ESO:**
  - TAP endpoint: `http://archive.eso.org/tap_obs`, through `tap_rows`.
  - Query: `SELECT instrument_name, t_min, dp_id FROM ivoa.ObsCore WHERE dataproduct_type = 'spectrum' AND INTERSECTS(CIRCLE('ICRS', {ra}, {dec}, {r_deg}), s_region) = 1`. It took 1.1 s; the `CONTAINS(POINT…)` form took 14.7 s.
  - `t_min` is MJD.
  - HD 209458 within 5″: 342 spectra (CORALIE 185, ESPRESSO 88, UVES 61, CRIRES 4, HARPS 4).
- **KOA:**
  - A public TAP service at `https://koa.ipac.caltech.edu/TAP` works with no login, so `pykoa` is not needed.
  - There is one table per instrument, `koa_<inst>`, with columns `koaid`, `object`, `instrume`, `ra`, `dec`, `date_obs`, `mjd`, `koaimtyp`, `exptime`, `progid`.
  - HD 209458: `koa_hires` has 139 rows, 130 of them `object` frames, and took about 5 s. `koa_kpf` has 443 rows and `koa_nirspec` has 1909.

**Interfaces:**
- **`EsoSpectra(Source)`:**
  - `name="eso"`, `group="eso"`, `service="ESO archive TAP"`.
  - `parse` returns `{"total": n, "instruments": [{"instrument", "n", "first", "last"}]}`. Dates are `YYYY-MM-DD` from the MJD, and instruments are sorted by `n`, descending.
- **`KeckKoa(Source)`:** `name="koa"`, `group="koa"`, `service="Keck Observatory Archive TAP"`.
- **`fetch`** queries each instrument in `cfg.sources.koa_instruments` concurrently, one thread per instrument:
  - The query is `SELECT koaimtyp, date_obs, progid FROM koa_{inst} WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra}, {dec}, {r_deg})) = 1`.
  - It reduces each instrument's rows with `summarize_koa(inst: str, rows: list[dict]) -> dict`, which returns `{"instrument", "frames", "nights", "first", "last", "programs"}`. Only `koaimtyp == "object"` frames count, and a night is the distinct `date_obs[:10]`.
  - A failed instrument becomes `{"instrument", "error"}`.
- **`parse`** keeps the instruments with frames > 0, plus any errors. It returns `{}` when there are none.
- **Positions** are propagated to 2000.0. Radii come from `radius_arcsec`.

- [ ] **Tests:**
  - **ESO:** grouping and dates; MJD 59000 is `2020-05-31`.
  - **`summarize_koa`:** with `object` and `arclamp` rows over two nights, `frames` counts only the object frames and `nights` is 2.
  - **Recorded HD 209458 fixtures:** ESO has `ESPRESSO` and `HARPS`, and the KOA `hires` frames number ≥ 100.
- [ ] **Implement, record, run, and commit:** `feat: ESO and Keck archive sources`.

### Task 20: Survey spectra, HARPS RV bank, and the inventory section

**Files:**
- Modify: `sources/vizier.py`, `sources/registry.py`, `src/star_report/config.yaml` (to pin the LAMOST table), `render/templates/report.html.j2`, `render/terminal.py`, `tests/conftest.py`
- Create: `render/templates/_inventory.html.j2`, `tests/test_inventory.py`, and the fixtures in `tests/fixtures/{harps_rvbank,apogee,lamost}/`

**Facts** (verified 2026-11-09):
- **HARPS RV bank** (`J/A+A/636/A74`):
  - Tables: `list` (`Name`, `Nrv`, `SimbadName`) and `rvbank` (`Name`, `BJD`, …).
  - Names are written like `GJ699`.
  - Barnard's Star has `Nrv` = 306, with BJD 2454194.9–2458027.5.
  - HD 209458 is in the catalog.
- **APOGEE DR17** (`III/286/catalog`): columns `APOGEE`, `Teff`, `logg`, `[M/H]`, `Nvis`, `SNR`, `HRV`, `GaiaEDR3`. Kepler-10 is in it.
- **LAMOST:** `V/162` is DR11 (Luo+ 2026). Its table names have not been checked yet.

**Interfaces** (new `VizierSpec` sources):

| Source | Fetch | Parse output |
|---|---|---|
| `harps_rvbank` | A cone on `{cat}/list`. Then `query_constraints(catalog=f"{cat}/rvbank", Name=name)` with columns `["BJD"]` and `row_limit=-1`. | `{"name", "n_rv", "first_bjd", "last_bjd", "span_yr"}` |
| `apogee` | Mode `gaia_id` on `GaiaEDR3`. Use a cone when there is no Gaia ID. | `{"apogee_id", "teff", "logg", "mh", "n_visits", "snr", "rv"}` |
| `lamost` | — | `{"n_spectra", "last_obs", "teff", "logg", "feh"}`, taken from the row with the highest S/N |

The inventory section (`_inventory.html.j2`, spec §9 item 6) has four parts:
1. **TESS sectors.** Cadence from `mast`; observed, pending, in-progress, and upcoming sectors, with dates, from `tess_pointing`.
2. **Other missions.** Kepler and K2 counts, HLSP pipelines, a table of other collections, and the MAST portal link.
3. **Spectra and RVs.** The ESO and KOA tables, the HARPS RV bank (number of RVs and span), APOGEE visits, LAMOST spectra, and Gaia RVS (`has_rvs`).
4. **ExoFOP follow-up.** Counts, with a link.

**Terminal:** one `Data:` line with the TESS sectors observed and upcoming, and the counts of ESO, KOA, and HARPS spectra.

- [ ] **Step 0: Pin the LAMOST table.**
  1. Run `vizier_client(cfg, ["**"], 5).query_region(SkyCoord(285.6795, 50.2414, unit="deg"), radius=3*u.arcsec, catalog="V/162")` for Kepler-10, and print the table names and columns.
  2. Set `sources.vizier_catalogs.lamost` to the low-resolution stellar-parameter table: the one with Teff, logg, [Fe/H], an obsid, and an observation date.
  3. Write the table ID and the column mapping as a comment in `config.yaml`. `parse_lamost` uses those columns.
- [ ] **Tests:**
  - The recorded Barnard's Star `harps_rvbank` fixture gives `n_rv == 306` and `10 < span_yr < 11`.
  - The Kepler-10 `apogee` fixture gives `5300 < teff < 6000`.
  - The Kepler-10 `lamost` fixture gives `n_spectra >= 1`. If LAMOST has no spectrum of Kepler-10, use the first reference star that has one, and say which in the test.
  - `render_html(make_report())` contains `ESPRESSO` and `HARPS`, and lists sector `56`.
  - The terminal output has a `Data:` line.
- [ ] **Implement, record, run, and commit:** `feat: survey spectra and data inventory section`.

**Milestone 3 is done** when a report lists TESS sectors, archive spectra, and ExoFOP follow-up.

## Milestone 4: Quick-look plots

### Task 21: Plot stage and color–magnitude diagram

**Files:**
- Create: `plots/__init__.py`, `plots/cmd.py`, `render/templates/_plots.html.j2`, `tests/test_plots.py`
- Modify: `pipeline.py` (the default for `PipelineDeps.plots` becomes `make_plots`), `render/html.py`, `report.html.j2`

**Interfaces:**
- **`plots/__init__.py`:**
  - Calls `matplotlib.use("Agg")` before anything imports pyplot.
  - `make_plots(report: Report, plots_dir: Path, cfg: Config, cache: Cache) -> tuple[dict[str, str], dict[str, str]]`:
    - Runs `cmd`, `lightcurve` (Task 22), and `sky` (Task 23), in that order.
    - An exception in one plot becomes the note `f"{type(e).__name__}: {e}"`, and the other plots still run.
    - Returns the paths as `{"cmd": "plots/cmd.png", ...}`, plus the notes.
- **`plots/cmd.py`:**
  - `BACKGROUND_ADQL = "SELECT TOP {n} bp_rp, phot_g_mean_mag + 5*LOG10(parallax) - 10 AS abs_g FROM gaiadr3.gaia_source WHERE parallax > 10 AND parallax_over_error > 10 AND ruwe < 1.4 AND phot_bp_mean_flux_over_error > 20 AND phot_rp_mean_flux_over_error > 20 ORDER BY random_index"`
  - `fetch_background(cache: Cache, cfg: Config, session) -> list[list[float]]`:
    - Runs the query with `pyvo.dal.TAPService(gaia.TAP_URL, session=session).run_async(...)`. A 50k-row async job took 27 s without login (verified).
    - Caches the result as `cache.fetch("cmd_background", "background", {"rows": n, "v": 1}, ...)`, which never expires.
  - `target_point(results) -> tuple[float, float] | None` returns `(bp_rp, G + 5*log10(parallax) - 10)` from `gaia_core`. It returns `None` when the parallax is ≤ 0 or missing.
  - `plot_cmd(background, target, label: str, path: Path) -> Path`:
    - Grey background points; the target as a red star.
    - The y axis is inverted.
    - Axis labels are `BP − RP` and `M_G`.
    - With no target, the title notes `No Gaia parallax for the target`.
- **HTML:** `_plots.html.j2` embeds each PNG as `data:image/png;base64,…` and shows its note underneath.

- [ ] **Tests:**
  - `target_point` with parallax 20.7694 and G 7.521 gives an `abs_g` within 0.01 of 4.108.
  - `plot_cmd` writes a PNG, with and without a target. Check that the file starts with `b"\x89PNG"`.
  - `make_plots` with the fetchers monkeypatched (cmd OK, lightcurve raising `RuntimeError("x")`) returns `"cmd"` in the paths and `notes["lightcurve"] == "RuntimeError: x"`.
  - The HTML contains `data:image/png;base64,`.
  - With `cfg.plots.enabled=False`, the pipeline never calls `deps.plots` (use a spy).
- [ ] **Implement, run, and commit:** `feat: plot stage and color-magnitude diagram`.

### Task 22: Light curves and periodogram

**Files:**
- Create: `plots/lightcurve.py`
- Modify: `plots/__init__.py`, `tests/test_plots.py`

**Facts** (verified 2026-11-09 with lightkurve 2.5.1):
- **Search.** `lk.search_lightcurve("TIC 150428135", mission="TESS", author="SPOC", exptime=120)` returns 37 rows.
  - `SearchResult.table` has the columns `mission` (`"TESS Sector 01"`), `year`, `author`, `exptime`, `target_name`, `distance`, `sequence_number`.
  - SPOC with no `exptime` filter also returns 20 s rows. TESS-SPOC and QLP have exptime 200, 600, or 1800.
  - Kepler rows have mission `"Kepler Quarter 02"`, author `"Kepler"` (KBONUS-BKG also appears), and exptime 60 or 1800.
- **Download.** `result[i].download()` gives PDCSAP flux. The light curve's `meta` has `SECTOR`, `QUARTER`, and `CAMPAIGN`.
- **Cache.** The default cache is `~/.lightkurve/cache`.

**Interfaces:**
- **`@dataclass class SectorCurve`:** `label: str`, `time: np.ndarray`, `flux: np.ndarray`. The flux is normalized and NaN-free.
- **`choose_products(rows: list[dict], max_sectors: int) -> tuple[list[int], str | None]`:**
  - **Rank** each row:

    | Row | Rank |
    |---|---|
    | SPOC with exptime 120 | 0 |
    | TESS-SPOC | 1 |
    | QLP | 2 |
    | Kepler or K2 with exptime > 1000 | 3 |
    | anything else (including SPOC 20 s) | dropped |

  - Keep one row per sector, quarter, or campaign: the lowest rank.
  - Take the `max_sectors` most recent by `sequence_number`.
  - When rows are dropped for that limit, the note is `f"Showing the {k} most recent of {n} sectors."`.
- **`fetch_lightcurves(identity: StarIdentity, cfg: Config) -> tuple[list[SectorCurve], str | None]`:**
  - First search TESS with `f"TIC {tic}"` and `author=["SPOC", "TESS-SPOC", "QLP"]`.
  - If there are no TESS rows, search Kepler and K2 with the KIC or EPIC ID from `other_ids`.
  - Download each chosen row, then apply `.remove_nans().normalize()`.
  - Each curve's label is `f"Sector {SECTOR} ({author})"`, or the Quarter or Campaign form.
- **`periodogram(curves, min_period: float, max_period: float) -> tuple[np.ndarray, np.ndarray]`:**
  - Concatenate the curves and bin them to 30 min.
  - Run astropy `LombScargle(...).autopower(minimum_frequency=1/max_period, maximum_frequency=1/min_period, samples_per_peak=10)`.
  - Return `(period, power)`.
- **`plot_lightcurves(curves, marks: list[tuple[str, float]], cfg: Config, path: Path) -> Path`:**
  - One panel per curve, then the periodogram panel.
  - `marks` come from `exoplanet_archive.known_periods`. Each is drawn as a labelled vertical line, and marks outside the period range are skipped.
- **Timeout.** `make_plots` runs `fetch_lightcurves` in a one-worker executor, so a slow download doesn't hold up the rest of the run.
  - It waits `cfg.plots.lightcurve_timeout_s`.
  - On timeout the note is `"Light-curve download timed out after 120 s."`, using the configured value.

- [ ] **Tests:**

```python
def test_choose_most_recent_spoc():
    rows = [{"author": "SPOC", "exptime": 120, "sequence_number": s, "mission": f"TESS Sector {s:02d}"} for s in range(1, 38)]
    rows += [{"author": "TESS-SPOC", "exptime": 200, "sequence_number": s, "mission": ""} for s in (36, 37)]
    idx, note = choose_products(rows, 10)
    assert [rows[i]["sequence_number"] for i in idx] == list(range(37, 27, -1)) and all(rows[i]["author"] == "SPOC" for i in idx)
    assert note == "Showing the 10 most recent of 37 sectors."
def test_qlp_fills_missing_sector(): ...      # sector 5 only in QLP -> chosen
def test_spoc_20s_ignored(): ...              # 20 s + 120 s for sector 5 -> the 120 s row
def test_kepler_long_cadence_only(): ...      # exptime 60 and 1800 per quarter -> only 1800 rows
def test_periodogram_finds_period(): ...      # synthetic sinusoid P = 3.0 d over 27 d -> peak within 1%
def test_plot_lightcurves_png(tmp_path): ...  # 3 synthetic curves, one mark in range and one outside -> PNG written
```

- [ ] **Implement, run, live-check, and commit.**
  - Live check: `star-report "TOI-700" --no-llm --no-open` shows 10 panels and the note.
  - Commit: `feat: light curves and periodogram`.

### Task 23: Sky image

**Files:**
- Create: `plots/sky.py`
- Modify: `plots/__init__.py`, `tests/test_plots.py`

**Facts** (verified 2026-11-09):
- **hips2fits:**
  - CDS, the hips2fits host, was unreachable from the development machine.
  - The signature, from the astroquery 0.4.10 source, is `hips2fits.query(hips, width, height, projection, ra: Longitude, dec: Latitude, fov: Angle, format="fits")`, which returns an `HDUList`.
- **STScI DSS:**
  - It works: `GET https://archive.stsci.edu/cgi-bin/dss_search?v={survey}&r={ra}&d={dec}&e=J2000&h={arcmin}&w={arcmin}&f=fits&c=none`.
  - It returned a FITS file in 0.3 s, about 300 × 300 px at roughly 1″/px, with a celestial (TAN) WCS.

**Interfaces:**
- **`fetch_cutout(ra: float, dec: float, cfg: Config, session) -> tuple[np.ndarray, WCS, str]`** returns `(image, wcs, label)`:
  - It uses hips2fits with `cfg.plots.sky_survey` when `is_reachable("alasky.cds.unistra.fr")`. The label is `"DSS2 red (CDS hips2fits)"`.
  - Otherwise it uses STScI DSS with `cfg.plots.sky_fallback_survey`, through `session`. The label is `"DSS2 red (STScI)"`.
  - The cutout is `cutout_arcmin` wide.
- **`select_neighbors(neighbors: list[dict], max_delta_g: float) -> list[dict]`** keeps the non-target rows with `delta_g <= max_delta_g`.
- **`plot_sky(image: np.ndarray | None, wcs: WCS | None, target: tuple[float, float], neighbors: list[dict], cfg: Config, label: str, path: Path) -> Path`:**
  - **With an image:**
    - Plot on WCS axes.
    - Stretch with `ImageNormalize(interval=ZScaleInterval(), stretch=AsinhStretch())`.
    - Mark the target.
    - Circle each neighbor and label it `f"ΔG={dg:+.1f}"`.
    - Draw a 21″ grid (`tess_pixel_arcsec`) centered on the target and aligned with RA/Dec. The caption says the grid shows the TESS pixel scale, not any sector's orientation.
    - Add a 1′ scale bar.
  - **With `image=None`:** draw the same overlays on tangent-plane offset axes in arcsec, with the title `"Gaia DR3 neighbors (no image available)"`.
- The neighbors come from the `gaia_neighbors` result, at their 2016.0 positions.

- [ ] **Tests:**
  - `select_neighbors` filters by ΔG and excludes the target.
  - `plot_sky` writes a PNG both with a synthetic image and a TAN `WCS(naxis=2)` at 1″/px, and with `image=None`.
  - `fetch_cutout` falls back to STScI when `is_reachable` is monkeypatched to `False`. The session is faked to return the bytes of an in-memory FITS file, and the label mentions `STScI`.
- [ ] **Implement, run, live-check, and commit.**
  - Live check: `star-report "HD 209458" --no-llm --no-open` shows all three plots.
  - Commit: `feat: sky image with Gaia neighbors and TESS pixel scale`.

**Milestone 4 is done** when all three plots appear in the report and failed plots show their reason.

## Milestone 5: Literature and LLM

### Task 24: ADS source and paper list

**Files:**
- Create: `sources/ads.py`, `render/templates/_literature.html.j2`, `tests/test_source_ads.py`, `tests/fixtures/ads/synthetic.json` (hand-written, 30 docs in the ADS response shape)
- Modify: `sources/registry.py`, `report.html.j2`, `tests/conftest.py`

**Facts** (from the ADS API docs, checked 2026-11-09):
- **Request.** `GET https://api.adsabs.harvard.edu/v1/search/query` with the header `Authorization: Bearer <token>`.
- **Parameters:**
  - `q`
  - `fl`, comma-separated
  - `rows`, at most 2000
  - `sort`, for example `citation_count desc`
- **Rate limits.** The response headers are `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` (UTC epoch seconds).
- **`object:"<name>"`** resolves the name through SIMBAD and NED.
- **The response shape** is `{"response": {"numFound": n, "docs": [...]}}`.

**Interfaces:**
- **`query_string(name: str, doctypes: Sequence[str]) -> str`** gives `object:"HD 209458" doctype:(article OR eprint)`, with any `"` in the name escaped.
- **`class Ads(Source)`:**
  - `name="ads"`, `group="ads"`, `service="NASA ADS search API"`.
  - `skip_reason` returns `"ADS_DEV_KEY not set"` when `ctx.ads_token` is `None`.
  - `query` is `{"q", "rows"}`. It never contains the token.
  - `fetch` sends `fl="bibcode,title,year,pubdate,author,citation_count,abstract,doctype"`, `rows=literature.max_records`, and `sort="citation_count desc"`.
  - On HTTP 429, or `X-RateLimit-Remaining == "0"`, it raises `RuntimeError(f"ADS rate limit reached; resets at {iso_utc}")`.
- **`parse`** returns `{"num_found": int, "papers": [Paper as dict]}`, after `select_papers`. It returns `{}` when there are no docs.
  - `title` is the first element of the title list.
  - `authors` are the first three.
- **`select_papers(papers: list[Paper], most_cited: int, most_recent: int) -> list[Paper]`:**
  - First the `most_cited` papers by `(-citation_count, bibcode)`.
  - Then the `most_recent` papers by `(pubdate desc, bibcode)` that are not already chosen.
  - The order is the cited block, then the recent block.
- **The query name** is `display_name(identity.main_name)`.
- **The literature partial** shows `ADS found {num_found} records; showing {k}.`, then the paper list linked to ADS. For a skipped source it shows `ADS_DEV_KEY not set; the literature was not searched.`

- [ ] **Tests:**
  - `select_papers` on the synthetic fixture: 15 + 10 = 25 papers, with no duplicates. When the two sets overlap, fewer than 25 come back, still with no duplicates. Check the order.
  - `query_string` gives the expected string.
  - The source is skipped without a token.
  - A fake session returning 429 gives status `error` with `rate limit` in the message.
  - The token string never appears in `json.dumps(result.model_dump(mode="json"))`.
  - The rendered paper list links `https://ui.adsabs.harvard.edu/abs/<bibcode>/abstract`.
- [ ] **Implement, run, and commit:** `feat: ADS literature source and paper list`.

### Task 25: Writer: fact sheet, prompt, schema, and citation check

**Files:**
- Create: `src/star_report/writer.py`, `tests/test_writer.py`

**Interfaces:**
- **`class WriterOutput(BaseModel)`:** `model_config = ConfigDict(extra="forbid")`, with fields `overview: str`, `literature_summary: str`, `cited_bibcodes: list[str]`.
- **`WRITER_SCHEMA`:** a hand-written JSON schema with those three properties (the third is an array of strings). All three are required, and `"additionalProperties": False`.
- **`PROMPT_VERSION = 1`**
- **`SYSTEM_PROMPT`**, exactly this text:

```
You write short, factual summaries of stars for professional astronomers.
You receive a fact sheet compiled from astronomical catalogs and archives, and a list of papers from NASA ADS with their abstracts.

Rules:
- Use only the material provided. Do not add facts from memory.
- Never state a number that does not appear in the fact sheet or in a paper abstract.
- When the literature is sparse or missing, say so plainly.
- Keep a neutral, factual tone. No hype and no speculation.

Write:
- overview: 3 to 5 sentences on what the star is, its key parameters, any planets, binarity or variability, and what data exist.
- literature_summary: 1 to 3 short paragraphs on what the papers report. Cite papers inline as [bibcode], using only bibcodes from the paper list.
- cited_bibcodes: every bibcode you cited.
If no papers are provided, the literature_summary says that and cites nothing.
```

- **`build_fact_sheet(report: Report) -> dict`** has these keys: `name`, `identifiers`, `position`, `description`, `parameters`, `flags`, `warnings`, `planets`, `tois`, `kois`, `inventory`, `literature`.
  - Each parameter is `{"quantity", "unit", "values": {"gaia": "5996 ± 2 (GSP-Phot)", …}, "sources_disagree"}`, formatted with the row's decimals.
  - `inventory` is `{"tess_observed", "tess_upcoming", "missions", "spectra", "exofop_followup"}`.
  - `literature` is `{"ads_records_found", "papers_provided", "note"}`.
  - It contains no raw rows and no `results` key.
- **`build_user_message(facts: dict, papers: list[Paper], literature_note: str | None, correction: str | None = None) -> str`**:
  - `<fact_sheet>` holds `json.dumps(facts, indent=1, sort_keys=True, ensure_ascii=False)`.
  - `<papers>` holds one block per paper:
    - `[{i}] {bibcode} | {year} | {first author} | {citation_count} citations`
    - `Title: …`
    - `Abstract: …`
  - With no papers, `<papers>` holds `No papers were provided: {literature_note}`.
  - When `correction` is given, it is appended in a `<correction>` block.
- **`find_citations(text: str) -> list[str]`** uses `models.BIBCODE_RE`.
- **`check_citations(out: WriterOutput, allowed: set[str]) -> list[str]`** returns the sorted, unique bibcodes that appear in either text field or in `cited_bibcodes` but are not in `allowed`.

- [ ] **Tests:**
  - The fact sheet from `make_report()`:
    - has a name and formatted parameter values;
    - has no `results` key;
    - is under 20 kB when serialized with `json.dumps`.
  - `build_user_message` includes every bibcode and title, and the `No papers were provided` line when the list is empty.
  - `find_citations("… [2008A&A...481..529X] and [2000ApJ...529L..45C].") == ["2008A&A...481..529X", "2000ApJ...529L..45C"]`
  - `check_citations` catches an unknown bibcode in the text and one in the list, and returns `[]` when everything is allowed.
  - The schema accepts good JSON and rejects an extra key.
- [ ] **Implement, run, and commit:** `feat: writer fact sheet, prompt and citation check`.

### Task 26: Writer: Claude call, retry, failures, caching, and wiring

**Files:**
- Modify: `writer.py`, `pipeline.py` (the default for `PipelineDeps.writer` becomes `write_sections`, and the plot and write stages run concurrently), `report.html.j2`, `_literature.html.j2`, `render/terminal.py`, `tests/test_writer.py`, `tests/test_pipeline.py`, `tests/test_render.py`

**Interfaces:**
- **`@dataclass class ClaudeReply`:** `stop_reason: str | None`, `text: str | None`, `model: str`.
- **`call_claude(client, llm: LLMConfig, system: str, user: str) -> ClaudeReply`.** This code is pinned because SDK usage must be exact. It was checked against the anthropic 2.6.0 source, where `fallbacks` accepts `"default"` and `beta.messages.stream` takes `betas`, `fallbacks`, and `output_config`.

```python
params = dict(model=llm.model, max_tokens=llm.max_tokens, system=system,
              messages=[{"role": "user", "content": user}],
              output_config={"effort": llm.effort,
                             "format": {"type": "json_schema", "schema": WRITER_SCHEMA}})
if llm.server_fallback:
    params |= dict(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
with client.beta.messages.stream(**params) as stream:   # streaming: long input, adaptive thinking
    msg = stream.get_final_message()
text = next((b.text for b in msg.content if b.type == "text"), None)
return ClaudeReply(msg.stop_reason, text, msg.model)     # msg.model = model that served it (fallback-aware)
```

- **`class WriterFailure(Exception)`**, carrying `.reason`.
- **`write_sections(report: Report, cfg: Config, cache: Cache, client_factory: Callable[[], Any] = anthropic.Anthropic) -> LLMBlock`.**
  - **Papers.** Take them from `results["ads"]` when that source is `ok`. Otherwise use a `literature_note`:

    | ADS status | Note |
    |---|---|
    | skipped | `ADS_DEV_KEY not set; the literature was not searched.` |
    | error or timeout | `ADS search failed.` |
    | empty | `ADS found no papers for this object.` |

  - **Cache key.** `{facts, bibcodes, model, effort, server_fallback, prompt_version}`, through `cache.fetch("llm", "writer", key, producer)`.
    - The producer returns the successful block.
    - Every failure raises `WriterFailure`, so failures are never cached.
    - A cache hit sets `from_cache=True`.
  - **Failure reasons.** Use these exact strings. Check `stop_reason` before parsing.

    | Condition | Reason |
    |---|---|
    | `TypeError` containing `Could not resolve authentication method`, or `anthropic.CredentialsError` | ``No Anthropic credentials found (set ANTHROPIC_API_KEY or run `ant auth login`).`` |
    | `anthropic.AuthenticationError` | `Anthropic API rejected the credentials.` |
    | any other `anthropic.APIError` | `f"Claude API error: {type(e).__name__}: {e}"`, cut to 200 characters |
    | `stop_reason == "refusal"` (the server-side fallback also declined) | `Claude declined to write this summary (refusal).` |
    | `stop_reason == "max_tokens"` | `Claude's output was cut off (max_tokens).` |
    | no text, or a `ValidationError` | `Claude's output did not match the expected format.` |
    | bad citations after one retry | `LLM summary failed citation check` |

  - **The retry** runs only for citations. It repeats the call with `correction=f"Your previous answer cited bibcodes that are not in the paper list: {', '.join(bad)}. Cite only bibcodes from the list, written as [bibcode]."`.
  - **On success** it returns `LLMBlock(status="ok", …, model=reply.model, effort=llm.effort, generated=now, bibcodes_sent=[...])`.
- **Pipeline.** The plot stage and the write stage don't depend on each other, so `run_target` runs `deps.plots` and `deps.writer` in a 2-worker `ThreadPoolExecutor`.
- **Render:**
  - The overview shows `link_citations(overview)` under the label `LLM-written · {model}`, with ` (cached)` added when it came from the cache.
  - The literature summary goes above the paper list.
  - When the LLM block is not `ok`, both places show `LLM sections not included: {reason}`.

- [ ] **Tests.** These use a fake client and make no network calls:

```python
class FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
    def _stream(self, **kw):
        self.calls.append(kw); r = self.replies.pop(0)
        if isinstance(r, Exception): raise r
        return nullcontext(SimpleNamespace(get_final_message=lambda: r))

def reply(text, stop="end_turn", model="claude-opus-5-5"):
    return SimpleNamespace(stop_reason=stop, model=model, content=[SimpleNamespace(type="text", text=text)])

def test_request_shape(tmp_path):
    fake = FakeClient([reply(good_json)])
    block = write_sections(report_with_papers(), cfg, Cache(tmp_path, EXPIRY), client_factory=lambda: fake)
    kw = fake.calls[0]
    assert block.status == "ok" and kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["output_config"]["effort"] == "high"
    assert kw["output_config"]["format"]["schema"] == WRITER_SCHEMA
    assert "thinking" not in kw and "temperature" not in kw
```

  Further cases, each asserting `block.status`, `block.reason`, and `len(fake.calls)`:
  - A bad citation, then a good one: `ok`, 2 calls, and the second user message names the bad bibcode.
  - Bad twice: `failed` with `LLM summary failed citation check`.
  - A refusal: one call, no retry.
  - `max_tokens`.
  - Invalid JSON.
  - A credentials `TypeError`.
  - `anthropic.APIConnectionError(message="down", request=httpx2.Request("POST", "https://api.anthropic.com"))`.
  - `server_fallback: false`: no `betas` and no `fallbacks` keys.
  - Cache: a second identical call comes from the cache with 0 new calls, while a failure is retried next time.
  - The pipeline runs plots and the writer together; a spy shows both called.
  - Render: an `ok` block shows `LLM-written · claude-opus-5-5` and an ADS link; a `failed` block shows its reason.

- [ ] **Implement, run, live-check, and commit.**
  - Live check: `star-report "HD 209458" --no-open`, with `ANTHROPIC_API_KEY` and `ADS_DEV_KEY` set. Expect an overview and a literature summary with linked citations. It costs about $0.10–0.25.
  - Commit: `feat: Claude-written overview and literature summary`.

**Milestone 5 is done** when a full report has the LLM sections, and every failure path shows its reason.

## Milestone 6: Finish

### Task 27: Live reference-star tests

**Files:**
- Modify: `tests/reference_stars.yaml`, which gets three more stars, with their selection queries as comments.
- Create: `tests/test_live.py`

- [ ] **Step 1: Pick the three extra stars**, per spec §12, and record each with the query that found it.
  - **A faint TIC-only star:**
    1. Run `Catalogs.query_region("120.0 -60.0", radius=3*u.arcmin, catalog="TIC")`.
    2. Keep rows with `objType == "STAR"` and `14 < Tmag < 16`.
    3. Take the first whose `simbad.lookup(f"TIC {ID}")` is empty and which has no SIMBAD object within 5″.
  - **A Gaia SB1:**
    1. Run `SELECT TOP 20 n.source_id, g.phot_g_mean_mag FROM gaiadr3.nss_two_body_orbit AS n JOIN gaiadr3.gaia_source AS g ON g.source_id = n.source_id WHERE n.nss_solution_type = 'SB1' AND g.phot_g_mean_mag BETWEEN 8 AND 9 ORDER BY n.source_id`.
    2. Take the first that has both a SIMBAD entry and a TIC ID.
  - **A star no TESS sector covers:**
    1. Step along the ecliptic, at latitude 0° and longitudes 0–360° in 5° steps.
    2. Take the first point where `tess_stars2px_function_entry` returns no sector.
    3. Take the brightest SIMBAD star (V < 9) within 10′ of that point, and confirm it also has no sector.
  - For each star, fill in all the YAML fields, including spellings: its name, its TIC ID, and its Gaia DR3 ID.
- [ ] **Step 2: Write the live tests.** Every test carries `@pytest.mark.live`.
  - `test_spellings_resolve`: every star × spelling resolves to the expected `tic_id` and `gaia_dr3_id`, including the J2000 coordinate spelling for Barnard's Star.
  - `test_report_no_llm[star]`:
    - `main([star, "--no-llm", "--no-open", "--out", tmp])` returns 0.
    - Every status is in the allowed set.
    - HD 209458 has ≥ 15 `ok` sources.
    - The faint star's `simbad` source is `skipped`.
    - The uncovered star's `tess_pointing` source is `empty`.
  - `test_cached_rerun_under_5s`: the second run of HD 209458 with `--no-llm` takes under 5 s (spec §13).
  - `test_no_llm_deterministic`: two warm-cache runs give identical `data.json` after removing `generated` and every `elapsed_s`.
  - `test_cold_run_time`: with an empty cache dir, a run with plots and the LLM prints its duration and asserts it takes under 150 s. The spec target is about 1 minute; the margin absorbs service jitter.
  - `test_llm_end_to_end`: skipped unless `ANTHROPIC_API_KEY` and `ADS_DEV_KEY` are set.
    - `llm.status == "ok"`.
    - `set(cited_bibcodes) <= set(bibcodes_sent)`.
    - The overview has 3–5 sentences.
- [ ] **Step 3: Run the live tests.**

Run: `pytest -m live -v`

Expected: all PASS. The LLM test is skipped when there are no credentials.

Fix any mismatch at its source module, with a new offline test. Don't loosen the live assertions.

- [ ] **Step 4: Commit.**

```bash
git add tests
git commit -m "test: live reference-star suite"
```

### Task 28: README and CLAUDE.md

**Files:**
- Create: `README.md`, `CLAUDE.md`

- [ ] **Step 1: Write `README.md`.** It covers:
  - What the tool does.
  - Installation: the venv and `pip install -e .`.
  - Credentials: `ADS_DEV_KEY`, and `ANTHROPIC_API_KEY` or `ant auth login`.
  - Usage examples:
    - `star-report "HD 209458"`
    - `star-report "TIC 150428135" --no-llm`
    - `star-report --from-file targets.txt --no-open`
  - The output layout.
  - Overriding config with a local `config.yaml` that contains only the changed keys.
  - Mirrors: the Harvard SIMBAD/VizieR mirrors and STScI DSS are used automatically when CDS is unreachable.
  - LLM cost: about $0.10–0.25 per report, with the model and effort in config.
  - Tests: `pytest`, `pytest -m live`, and re-recording fixtures.
- [ ] **Step 2: Write `CLAUDE.md`**, in the style of `~/Desktop/arxiv-agent/CLAUDE.md`. It covers:
  - Purpose and the researcher.
  - The layout.
  - The key commands.
  - **Rules:**
    - Tunables live only in `config.yaml`.
    - Secrets live only in the environment.
    - Unit tests never touch the network; record fixtures instead.
    - Respect services: no query loops, and honor the ADS rate-limit headers.
    - Never commit `reports/` or a local `config.yaml`.
  - **"Adding a source":** a `Source` subclass, then the registry, then a fixture, then a parser test, then a template partial and the terminal line.
- [ ] **Step 3: Verify.**

Run: `pytest && star-report --help`

Expected: the tests PASS, and the help lists every option from spec §3.

- [ ] **Step 4: Commit.**

```bash
git add README.md CLAUDE.md
git commit -m "docs: README and CLAUDE.md"
```
