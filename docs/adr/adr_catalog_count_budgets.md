# Catalog counts are optional metadata

Date: 2026-10-12. Supersedes the segment-count cache requirement in
`adr_catalog_null_segment_pagination.md`.

## Problem

`start()` previously requested exact present/null segment sizes before fetching
any nullable-sort page. Each cold per-process cache entry aggregated every
filtered match, despite the later decision to defer broad filtered totals.
On this machine the previous full first-page request did not finish within
45 seconds against the 9.43 GB database copy.

## Decision

Web pagination no longer calls `cached_segment_counts()`. That utility remains
for explicitly invoked migration/verifier tools, outside ordinary requests.
Cursor pages use separate present/null seeks and one sentinel row. Last reads
the segments backwards and needs neither a total nor an OFFSET. A missing-value
jump probes for preceding present rows with no ORDER BY: a sorted LIMIT 1 probe
was measured to choose the passband index plus a full temporary sort, exceeding
45 seconds even after the segment aggregate was removed.

Numbered pages retain their cap. If a present-segment OFFSET returns no rows,
the skipped present prefix is counted with LIMIT inside the subquery, capped at
that offset. The remainder is applied to the null segment. This remains OFFSET
pagination; it is not constant-cost at depth.

Exact totals come from a complete first page, a terminal numbered page, a
bounded sample for a short filtered cursor page, or a cheap automatic calculation.
Automatic calculations use a 100 ms SQLite VM execution budget. Unfiltered
COUNT(*) retains its explicit FROM and process-local display cache. Equality
filters may count exactly under the same budget. Other broad filters never
aggregate during rendering. Cached counts do not decide which rows are fetched.
The missing-value notice uses an exact number only when the complete result is
already present; otherwise a bounded existence probe controls its jump link.

A full unknown page displays `100+`. A shorter unknown tail displays the number
of rows actually present plus `+`; it must not claim that 100 rows exist.

The optional Count action has a default two-second SQLite VM execution budget.
An interrupted/locked count returns HTTP 422 with `count_unavailable`. The UI
removes the action and explains how to narrow the filters. That filter signature
is suppressed for five minutes in the current process; another filter remains
eligible. No slow-count scan continues after SQLite reports interruption.
`CATALOG_COUNT_BUDGET_SECONDS=0` hides and disables optional counting globally.

Budgeted metadata uses a separate SQLAlchemy connection, restores its busy
timeout, and clears its progress handler before returning to the pool. Progress
handlers run between SQLite VM steps; an individual I/O operation or SQLite's
unfiltered COUNT opcode can exceed the requested interval. These are execution
budgets, not strict end-to-end HTTP deadlines. Deployment settings should remain
conservative. Suppression is process-local, but each worker's count is budgeted.

## Verification and measurement

`tests/test_catalog_count_budgets.py` verifies actual SQL rather than mocking only
`count_matches()`. It covers all nullable sorts, First/Next/Last/null jumps,
numbered offsets across both segments, all-null results, stale cached sizes,
count disablement/suppression, SQLite interruption and pooled-connection reuse,
and truthful lower bounds after metadata timeout. Existing catalog and route
tests continue to cover cheap exact totals and historical URLs.

`tests/test_catalog_small_null_results.py` explicitly covers one fully populated
row followed by ten rows with missing magnitude (and a variant with all nullable
display values missing), in both sort directions, with and without a broad
filter. First, Last, the missing-values jump, and its Prev/Next round trip retain
the exact total of eleven. It also covers 99/100/101 missing rows alongside one
present row: an exact hundred is displayed as `100`, while larger results can
display `100+` without losing or duplicating rows.

`tests/test_catalog_navigation_edges.py` adds HTTP checks for classification
filters leaving empty, present-only, null-only, or mixed results; exact zero
counts; and full forward/backward walks of 201 rows with segment boundaries
at 99/100/101 present rows. Numeric zero and empty text remain valid cursor
values rather than null sentinels. Every walk is compared with an independent
ordering, including Last, null jumps, and numbered pages. The tests also verify
that slow-count suppression survives a sort change and expires after its TTL.

`tools/benchmark_catalog_counts.py` uses SQLite `mode=ro`, temporary users storage,
and the real HTTP route/template. It does not apply schema changes or ANALYZE.
Its optional comparison loads the previous start/total functions from a Git ref,
using the current template. Each measured page starts with cold application
metadata caches. OS disk caching is uncontrolled; server timing is unmeasured.

Recorded in `docs/catalog_count_benchmark_2026-10-12.json`:

| Request | Seconds |
| --- | ---: |
| Previous filtered magnitude first page | >45 (cancelled) |
| Fixed filtered magnitude first page, three trials | 0.009–0.118 |
| Fixed Next | 0.066 |
| Fixed Last, three trials | 0.010–0.015 |
| Fixed missing-value jump, three trials | 0.009–0.014 |
| Fixed numbered page 2, three trials | 0.011–0.013 |
| Optional broad count | 2.016 (422) |
| Repeated optional broad count | 0.002 (422, suppressed) |

The nullable-sort matrix exercised magnitude, object ID, RA, and Dec in both
directions. Successful first/last/null-jump requests took 0.008–0.262 seconds
and emitted zero aggregate queries. Empty null segments retained their 404
behavior. The default unfiltered page still reported the exact 3,178,670 rows.

Re-run on a server database copy before making server latency claims:

```text
python tools/benchmark_catalog_counts.py --db /path/to/copy.db --matrix --output timings.json
```

Arbitrary filter/sort combinations may still need additional indexes. LIMIT
bounds returned rows, not the number of candidates SQLite must inspect.
