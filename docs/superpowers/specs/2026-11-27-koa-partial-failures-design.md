# KOA partial failures: design

- **Date:** 2026-11-27
- **Status:** Approach A chosen in chat; this short design awaits approval.
- **Owner:** Avi Shporer (MIT)

## Why

The KOA source queries one Keck Archive TAP table per instrument. On 2026-11-27, for TOI-1091
(HD 130101), the nirc2 and mosfire tables failed on the Keck side ("ORA-00955: name is already
used by an existing object") and the other five answered with no frames. The source was marked
`ok`, and the partial answer was cached for 7 days, so reruns before 2026-12-04 would not have
retried NIRC2, the AO imager used in TOI vetting. The report and terminal never made it clear that
the five answered tables had no frames while the two failed ones were not checked at all.

## Goals

- A failed instrument is never cached. The next run asks KOA again for that instrument only.
- Instruments that answered stay cached for `cache.expiry_days.koa`, as now.
- The HTML report, the terminal summary and the overview prompt say which instruments could not be
  checked, so "no Keck data" is not implied for them.

## Non-goals

- No retries within a run (rule 4). A failed instrument is retried on the next run only.
- No new source status (such as "partial"). KOA stays `ok` when at least one instrument answered,
  and stays an error when none did.
- No change for other sources. The Gaia queries already refuse to cache a partial answer.

## Approaches

- **A. One cache entry per instrument (chosen).** Each instrument has its own entry, and a
  failed query never writes one. Reruns query only the instruments that failed or expired.
  Adding an instrument to `koa_instruments` queries only the new one. Cost: KOA needs its own
  caching hook in the shared source runner.
- **B. Never cache a partial answer.** This is simpler, but every rerun during a Keck problem
  repeats all seven queries and throws away the five good answers.
- Dropped: a shorter expiry for partial answers (stale "not checked" results remain for that
  period), and failing all of KOA when any instrument fails (good Keck data would be lost).

## Design

**Caching (`sources/base.py`, `sources/koa.py`).**
- `Source` gets a `cached_fetch(ctx, query)` hook. By default it does what `run_source` does
  today (`ctx.cache.fetch(group, name, query, fetch)`), and `run_source` calls it.
- `KeckKoa.cached_fetch` runs one `ctx.cache.fetch` per instrument, concurrently through
  `parallel_map`. The keys are `{ra, dec, radius, instrument}` in the `koa` group. A query that
  raises or times out writes nothing and becomes an `{"instrument", "error"}` entry in the combined
  payload, as now. If every instrument fails, the source still raises.
- The combined result keeps today's payload shape. Its `retrieved` is the oldest retrieval among
  the answered instruments, and `from_cache` is true only when every answered instrument came
  from the cache.
- `KeckKoa.fetch` stays as the uncached query of all instruments, which
  `scripts/record_fixtures.py` uses.
- Today's 7-day entries (keyed on the full instrument list) are no longer read, so the next run
  queries every instrument once.

**Parse.** When some instruments failed, `parse` also returns `no_frames`: the instruments that
answered with no object frames.

**Display.**
- *HTML inventory.* When instruments failed: "No object frames: hires, kpf, nirspec, esi, nires.
  Not checked (Keck archive error, retried on the next run): nirc2, mosfire." This replaces the
  current "Keck instruments unavailable" line.
- *Source list (terminal and the report's "Data sources" table).* A new `Source.partial_error(data)`
  hook (default `None`) fills `SourceResult.error` on an `ok` result. KOA returns
  "nirc2, mosfire not checked". Both lists then show "ok (nirc2, mosfire not checked)", and the
  terminal marks the line with a yellow `!` instead of the green check.
- *Terminal data line.* `KOA 0 (nirc2, mosfire not checked)`.
- *Overview prompt.* The fact sheet gets `inventory.spectra.koa_not_checked: [...]` only when
  instruments failed. Fact sheets without failures are unchanged, so other stars' cached
  overviews stay valid. `PROMPT_VERSION` is not bumped.

**Tests (offline, test-first).**
- The first run with nirc2 failing caches only the answered instruments. A second run queries
  only nirc2 and combines the results.
- A failure followed by a success caches the success.
- When every instrument fails, the source raises and nothing is cached.
- `--refresh` queries each instrument once per run.
- `run_source` covers status, data, `error` note and provenance for KOA.
- The default `cached_fetch` behaves as before.
- Rendering of the HTML partial, the source list, the terminal data line and the fact-sheet key.

## Left to the builder

Exact helper names. Where the combine logic is shared between `fetch` and `cached_fetch`. The
CLAUDE.md "Things to remember" entry for this incident.
