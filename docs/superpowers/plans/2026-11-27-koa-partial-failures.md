# KOA partial failures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A failed KOA instrument query is never cached and is retried on the next run. The
report, the terminal and the overview prompt say which instruments were not checked.

**Architecture:** A `Source.cached_fetch` hook in `sources/base.py` lets KOA cache one entry per
instrument, and a `Source.partial_error` hook puts a "not checked" note on an `ok` result.
Renderers and the writer read KOA's `errors` and `no_frames` lists.

**Tech Stack:** Python 3.11+, pydantic models, Jinja templates, rich, pytest (sockets blocked).

**Spec:** `docs/superpowers/specs/2026-11-27-koa-partial-failures-design.md`

## Global Constraints

- This worktree has no `.venv`. Wherever a step says `.venv/bin/pytest`, run
  `PYTHONPATH=src /Users/avishporer/Desktop/star-report/.venv/bin/python -m pytest` instead.
  `PYTHONPATH` makes the worktree's `src/` take precedence over the main checkout's editable install.
- Unit tests never touch the network. Patch `star_report.sources.koa.tap_rows`.
- No retries within a run. A failed instrument is retried on the next run only.
- Tunables stay in `config.yaml`. This change adds none.
- Python 3.11 syntax only. No nested-quote f-strings.
- Concurrent network work runs through `parallel_map`, in daemon threads.
- Fact sheets without KOA failures must not change. Do not bump `PROMPT_VERSION`.
- Exact copy: `"<names> not checked"`. `"No object frames: <names>."`.
  `"Not checked (Keck archive error, retried on the next run): <names>."`. Names are
  joined with `", "`.

## Review Focus

1. An instrument that times out, rather than raising, is reported as not checked. Test:
   `test_koa_timed_out_instrument_is_not_checked` (Task 2).
2. An unreadable per-instrument cache file triggers a new query for that instrument only. Test:
   `test_koa_unreadable_instrument_entry_is_queried_again` (Task 2).
3. Today's whole-list cache entry, holding a partial answer, is not served. Test:
   `test_koa_ignores_the_old_whole_list_entry` (Task 2).
4. `--refresh` with the same star twice in one batch queries each instrument once. Test:
   `test_koa_refresh_queries_each_instrument_once_per_run` (Task 2).
5. With frames in one instrument and another failed, the frames table and the "not checked"
   line both show, and the data line counts the frames. Tests:
   `test_inventory_koa_partial_with_frames` and `test_terminal_marks_a_partial_koa_answer`
   (Task 3).

---

### Task 1: Source hooks in the runner

**Files:**
- Modify: `src/star_report/sources/base.py` (class `Source`, `run_source`)
- Modify: `src/star_report/models.py` (`SourceResult.error`: a one-line comment that an `ok`
  result may carry a note on the part that failed)
- Test: `tests/test_runner.py`

**Interfaces:**
- Produces:
  - `Source.cached_fetch(self, ctx: SourceContext, query: dict) -> CacheEntry`. By default it
    returns `ctx.cache.fetch(self.group, self.name, query, lambda: self.fetch(ctx))`.
  - `Source.partial_error(self, data: Any) -> str | None`. By default it returns `None`.
  - `run_source` calls `src.cached_fetch(ctx, query)`. When the status is `"ok"`, it sets
    `SourceResult.error = src.partial_error(data)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_runner.py`.

```python
class Hooked(Fake):
    def cached_fetch(self, ctx, query):
        return CacheEntry(self.payload, datetime(2026, 1, 2, tzinfo=timezone.utc), True)

    def partial_error(self, data):
        return "part not checked"


def test_run_source_uses_cached_fetch_and_partial_error(ctx):
    r = run_source(Hooked("h", payload={"x": 1}), ctx)
    assert (r.status, r.data, r.error) == ("ok", {"x": 1}, "part not checked")
    assert r.provenance.from_cache and r.provenance.retrieved == datetime(2026, 1, 2, tzinfo=timezone.utc)


def test_partial_error_only_on_ok_results(ctx):
    r = run_source(Hooked("h", payload=[]), ctx)
    assert r.status == "empty" and r.error is None
```

Also add `assert res["a"].error is None` to `test_statuses`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `.venv/bin/pytest tests/test_runner.py -q`
Expected: the two new tests FAIL, because `run_source` ignores both hooks.

- [ ] **Step 3: Implement both hooks and use them in `run_source`.** Import `CacheEntry` from
  `star_report.cache`.

- [ ] **Step 4: Run them and confirm they pass**

Run: `.venv/bin/pytest tests/test_runner.py -q`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/star_report/sources/base.py src/star_report/models.py tests/test_runner.py
git commit -m "feat: source hooks for custom caching and partial-failure notes"
```

### Task 2: KOA caches one entry per instrument

**Files:**
- Modify: `src/star_report/sources/koa.py`
- Modify: `CLAUDE.md` (a "Things to remember" entry: the 2026-11-27 TOI-1091 incident, with
  per-instrument entries keyed `{ra, dec, radius, instrument}` and failures never cached)
- Test: `tests/test_source_archives.py`

**Interfaces:**
- Consumes: `Source.cached_fetch` and `Source.partial_error` (Task 1), plus `parallel_map`.
- Produces:
  - `KeckKoa.cached_fetch(ctx, query) -> CacheEntry`. One `ctx.cache.fetch("koa", "koa",
    {"ra", "dec", "radius", "instrument"}, ...)` per valid instrument, run through
    `parallel_map` with `self.timeout_s(ctx.config)`. The payload has the same shape as
    `fetch`'s. `retrieved` is the minimum over answered instruments; `from_cache` is true only
    if every answered instrument came from the cache. With no valid instruments it returns
    `CacheEntry([], ctx.now(), False)` and queries nothing.
  - `KeckKoa.fetch(ctx)` keeps its behaviour, uncached, for `scripts/record_fixtures.py`.
    One private helper turns `parallel_map` outcomes into the payload for both methods, and
    raises `RuntimeError("all KOA instrument queries failed: ...")` when every instrument failed.
  - `KeckKoa.parse` adds `"no_frames": [instrument, ...]` (answered with 0 object frames, in
    input order) only when `"errors"` is present.
  - `KeckKoa.partial_error(data)` returns `"nirc2, mosfire not checked"` for
    `data["errors"]` naming nirc2 and mosfire, and `None` otherwise. It must not raise on
    malformed data.

- [ ] **Step 1: Write the failing tests** in `tests/test_source_archives.py`. Use a shared fake
  and a context helper:

```python
def _koa_service(monkeypatch, failing, slow=(), release=None):
    calls = []

    def fake(url, adql, session):
        inst = re.search(r"FROM koa_(\w+)", adql).group(1)
        calls.append(inst)
        if inst in slow:
            release.wait(5)
        if inst in failing:
            raise RuntimeError("ORA-00955")
        return [obj("2010-01-02 00:00:00")] if inst == "hires" else []

    monkeypatch.setattr(koa_src, "tap_rows", fake)
    return calls


def _koa_ctx(tmp_path, now=None):
    ctx = make_ctx(cache_dir=tmp_path / "c", now=now)
    ctx.config.sources.koa_instruments[:] = ["hires", "kpf", "nirc2"]
    return ctx
```

These are the tests and their assertions:
- `test_koa_failed_instrument_is_retried_alone_on_the_next_run`.
  - Run 1, with nirc2 failing: `status == "ok"`, `error == "nirc2 not checked"`,
    `data["no_frames"] == ["kpf"]`, `data["errors"][0]["instrument"] == "nirc2"`, and all three
    instruments queried.
  - Run 2, with nothing failing: `calls == ["nirc2"]`, `error is None`, `"errors" not in data`,
    and `not provenance.from_cache`.
  - Run 3: no calls, `provenance.from_cache`, and the same data as run 2.
- `test_koa_retrieved_is_the_oldest_answer`. Run 1 at t0 with nirc2 failing, then run 2 at
  t0 + 1 day, on the same cache directory: `provenance.retrieved == t0`.
- `test_koa_all_failing_caches_nothing`. Run 1: `status == "error"` and
  `"all KOA instrument queries failed" in error`. Run 2, with nothing failing: all three queried.
- `test_koa_timed_out_instrument_is_not_checked`. kpf waits on `release` and
  `timeout_s = 0.3`; set `release` in a `finally`. Then `error == "kpf not checked"` and
  `data["errors"] == [{"instrument": "kpf", "error": "TimeoutError: timed out after 0.3 s"}]`.
- `test_koa_unreadable_instrument_entry_is_queried_again`. After one clean run there are 3 files
  in `tmp_path / "c" / "koa"`. Overwrite the one whose `query.instrument == "kpf"` with `"{"`.
  The next run gives `calls == ["kpf"]`.
- `test_koa_ignores_the_old_whole_list_entry`. Write
  `ctx.cache.fetch("koa", "koa", KeckKoa().query(ctx), lambda: [{"instrument": "nirc2", "error": "old"}])`.
  Then run: all three are queried and `"errors" not in data`.
- `test_koa_refresh_queries_each_instrument_once_per_run`. After one run, set
  `ctx.cache.refresh = True` and run twice. Each instrument is queried exactly once.
- `test_koa_partial_error`. `None` for `{}`, for `{"instruments": []}` and for
  `{"errors": "x"}`. `"nirc2, mosfire not checked"` for two error entries.
- Update `test_koa_parse_keeps_frames_and_errors`: expect `out["no_frames"] == ["kpf"]`.

Run them through `run_source(KeckKoa(), ctx)`, imported from `star_report.sources.base`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `.venv/bin/pytest tests/test_source_archives.py -q`
Expected: the new tests FAIL (the partial answer is cached, `no_frames` is missing, and
`error` is `None`). The existing `fetch` tests still PASS.

- [ ] **Step 3: Implement `cached_fetch`, the shared helper, `no_frames` and `partial_error`
  in `koa.py`, and add the CLAUDE.md entry.**

- [ ] **Step 4: Run them and confirm they pass**

Run: `.venv/bin/pytest tests/test_source_archives.py tests/test_runner.py -q`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/star_report/sources/koa.py tests/test_source_archives.py CLAUDE.md
git commit -m "fix: cache KOA per instrument so a failed instrument is retried"
```

### Task 3: Report, terminal and overview prompt name the instruments not checked

**Files:**
- Modify: `src/star_report/render/templates/_inventory.html.j2` (replace the
  "Keck instruments unavailable" line)
- Modify: `src/star_report/render/templates/_sources.html.j2` (`ok (<error>)` for an ok result
  with an error)
- Modify: `src/star_report/render/terminal.py` (`_data_line`; the source list, which shows
  `ok (<error>)` with mark `("!", "yellow")`)
- Modify: `src/star_report/writer.py` (`_inventory`: add `spectra["koa_not_checked"]` only when
  KOA errors exist)
- Test: `tests/test_inventory.py`, `tests/test_writer.py`

**Interfaces:**
- Consumes: the KOA data shape `{"instruments": [...], "no_frames": [str], "errors":
  [{"instrument", "error"}]}`, and `SourceResult.error` on an ok result (Tasks 1 and 2).

- [ ] **Step 1: Write the failing tests.** Add this helper to `tests/test_inventory.py`:
  `_koa_partial(report, instruments, no_frames, errors)` returns the report with
  `results["koa"]` replaced by `status="ok"`, that data, and
  `error=", ".join(errors) + " not checked"`.
  - `test_inventory_koa_partial_names_checked_and_unchecked`: no instruments,
    `no_frames=["hires", "kpf"]`, and `errors=["nirc2", "mosfire"]`.
    - The inventory contains `No object frames: hires, kpf.` and
      `Not checked (Keck archive error, retried on the next run): nirc2, mosfire.`
    - It does not contain `Keck instruments unavailable`.
    - The `id="sources"` section contains `ok (nirc2, mosfire not checked)`.
  - `test_inventory_koa_partial_with_frames`: hires with 5 frames, `no_frames=[]`, and
    `errors=["nirc2"]`.
    - The inventory has the Keck table row for hires and the "Not checked" line.
    - It has no `No object frames` text.
  - `test_terminal_marks_a_partial_koa_answer`: hires with 5 frames, and `errors=["nirc2",
    "mosfire"]`.
    - The `Data:` line contains `KOA 5 (nirc2, mosfire not checked)`.
    - A line matches `! koa  ok (nirc2, mosfire not checked)`.
  - In `tests/test_writer.py`, `test_fact_sheet_lists_unchecked_keck_instruments_only_when_some_failed`:
    `"koa_not_checked" not in build_fact_sheet(make_report())["inventory"]["spectra"]`. With KOA
    errors for nirc2 and mosfire, the value is `["nirc2", "mosfire"]`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `.venv/bin/pytest tests/test_inventory.py tests/test_writer.py -q`
Expected: the four new tests FAIL.

- [ ] **Step 3: Implement the template, terminal and writer changes.** Every list read from
  `data` goes through `as_list` (templates) or `isinstance` checks (Python), as the existing
  code does.

- [ ] **Step 4: Run them and confirm they pass**

Run: `.venv/bin/pytest tests/test_inventory.py tests/test_writer.py tests/test_render.py -q`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/star_report/render src/star_report/writer.py tests/test_inventory.py tests/test_writer.py
git commit -m "fix: report, terminal and overview name the Keck instruments not checked"
```

### Task 4: Full suite and PR

- [ ] **Step 1:** Run `.venv/bin/pytest -q`. Expected: all tests pass, 770 or more, with none
  failing.
- [ ] **Step 2:** Commit the spec and this plan. Push the branch, then open a PR against `main`
  that describes the incident, the design and the tests. CI must pass on Python 3.11 and 3.13.
