# Catalog NULL rows are listed, not hidden

Date: 2026-10
Status: **Accepted**
Supersedes: the NULL-exclusion decision in
[`adr_composite_sort_indexes_and_keyset.md`](adr_composite_sort_indexes_and_keyset.md)
("surfaced in the UI"). That record is immutable and stays as history; this
record replaces only that decision, and the rest of it (the composite indexes,
the single-sort rule) still stands.

## Context

Nullable sort columns were previously filtered to a single storage class:

```sql
WHERE typeof(ant_mag_corrected) IN ('integer','real')
```

That filter was forced by the keyset cursor. `NULL > NULL` is NULL — false —
so a cursor inside a NULL tie group matches nothing and the walk stops dead.
Measured at the time: `ant_mag_corrected` was NULL for 1,012,259 of 3,178,668
rows, and a step from 500,000 rows into that block returned 0 rows.

The fix cost real data. The filter also removed the mixed-storage-class rows,
so the "Rows: N" count had to be restricted identically to avoid overstating
the listing, and the UI carried a notice saying 31.8% of the catalog was not
listed under a magnitude sort.

## Decision

**List every row. Split a nullable sort into two segments, in Python.**

Presentation order is `present` segment then `null` segment, each internally
ordered by the requested direction. NULL therefore sorts **last in both
directions** rather than SQLite's native "NULLs first in ASC". The count query
carries no sort restriction, so it returns the true 3,178,668.

| direction | UI | phase 1 | top off from |
|---|---|---|---|
| `first` | First | present | null |
| `nulls` | quick jump | null | — |
| `last` | Last | null (reversed), then present (reversed) | — |
| `before` | **Next** | cursor's segment | null, when the cursor is in present |
| `after` | **Prev** | cursor's segment | present, when the cursor is in null |

`before` is Next and `after` is Prev: `prev_href` emits `after_*` cursor params
and `next_href` emits `before_*` (`app.py`).

## Why the earlier "two-phase walk" measurement was wrong

The prior record rejected this design, measuring the two-phase walk at **3.27 s
per page**, with the stated cause: *"the planner refuses the PK for `IS NULL`"*.

That measurement was taken **before** the composite sort indexes existed, so
the only index available to the NULL segment was the primary key. It no longer
is. Re-measured read-only against the 3.18M-row production table with
`idx_featuretable_sort_ant_mag_corrected` in place:

| query | plan | cost |
|---|---|---|
| NULL-block step | `SEARCH ... (ant_mag_corrected=? AND (alert_id,locus_id)>(?,?))` | **0.12 ms** |
| value-block step | `SEARCH ... ((ant_mag_corrected,alert_id,locus_id)>(?,?,?))` | 1.2 ms |
| null tail (reversed) | `SEARCH ... (ant_mag_corrected=?)` | 0.24 ms |

`IS NULL` and `IS NOT NULL` are contiguous key ranges on the index's leading
column, so each phase is a point seek. The rejection no longer holds; it was an
artefact of the index set at the time, and the number is now wrong by a factor
of ~27,000.

## Why not the two tempting alternatives

Both were measured and both are traps.

**One `OR`-ed predicate.** Combining the segments into
`(col IS NULL AND (alert_id,locus_id) > (?,?)) OR (col IS NOT NULL AND ...)`
yields `SCAN featuretable USING COVERING INDEX idx_...` — no range
constraint at all. It appears fast on page 1 only because `LIMIT 101`
short-circuits at the very start of the index, then degrades linearly with
depth. A shallow test would not catch it.

**A leading `col IS NULL` term in ORDER BY.** Expressing NULLs-last directly in
SQL gives `ORDER BY (col IS NULL), col, ...`. The leading expression is not an
index column, so the sort is not served: **3.82 s/page**. This is the fastest
looking option and the worst one.

The correct implementation keeps the segments in Python. A single query with the
leading `IS NULL` term is still used — but only in
`tools/verify_catalog_pagination.py` check 8, as an **oracle**: it is correct
by construction and shares no logic with the code under test. It costs one full
sort (measured 2.0 s) and is run over a 200-row window straddling the
present→null boundary. That check was confirmed to have teeth by mutating
`fetch_page` to emit the null segment first, which produced
`first divergence @74`.

## Consequences

- **No data is hidden.** Every sort reports 3,178,668 rows.
- **Cursor wire format gains `<prefix>_null=1`.** A cursor in the null segment
  carries no sort value, so it cannot be encoded as one. A dedicated flag is
  used rather than a sentinel string inside `<prefix>_v`, because the empty
  string is a legitimate value for a text sort column. `nulls`, `before_null`
  and `after_null` are all in `PAGINATION_KEYS` and stripped from generated
  filter links.
- **`?nulls=1` is new public URL surface.** It appears in the notice link and
  round-trips through bookmarks and share links. `has_prev` is true whenever
  the present segment is non-empty, so the jump is never a dead end; when a
  filter matches only NULL rows it correctly offers no Prev.
- **Nullability is derived, not hand-kept.** `SortSpec.nullable` reads the model
  column, replacing the parallel `PK_SORT_KEYS` frozenset that had to be
  updated by hand alongside `SORT_KEYS`.
- **Segment sizes come from a cache (superseded 2026-10-12).** See
  `adr_catalog_count_budgets.md`: web pagination now uses count-free seeks;
  this cache remains only for explicitly invoked verification tools.
  Originally, `cached_segment_counts()` did one
  `COUNT(*)` with a filtered `COUNT(col IS NULL)` per (filter signature, sort
  column), TTL-cached like the existing total. Without it `'last'` would have to
  offset blindly.
- **An unparseable cursor now raises `MalformedCursor`** instead of returning
  `None`. Returning `None` means "no cursor" and silently re-renders page 1, so
  a Next link carrying a wrong-storage-class value becomes an unbreakable loop.
  After `tools/apply_fix_bad_magnitudes.sql` this cannot arise from data, but it
  is the failure mode worth naming.
- **The legacy `?page=N` path had to change too.** It inherited SQLite's native
  ordering, so page N by number and page N by cursor would have disagreed. It
  now walks the same segments, splitting the global offset with the cached
  counts, and a page may straddle the boundary.

## Revisit triggers

- The composite sort indexes are dropped → re-measure before assuming the
  two-phase walk is still fast. This decision rests entirely on them.
- A sort column gains mixed storage classes → `MalformedCursor` becomes
  reachable in normal use; the repair script's invariant (verifier check 6) is
  what keeps it unreachable.
- A future sort column is nullable and large → its notice should show a real
  null count, which `cached_segment_counts` already provides.

## Related

| Path | Role |
|---|---|
| `poi_broker/services/catalog_list.py` | segment dispatch, cursor encoding, segment counts |
| `poi_broker/services/catalog_query.py` | `SortSpec.nullable`, no exclusion filter |
| `tools/verify_catalog_pagination.py` | check 7 (index-ranged phases), check 8 (oracle) |
| `catalog_sort_keyset_empirical_findings.md` | the superseded 3.27 s measurement |
| `adr_composite_sort_indexes_and_keyset.md` | composite indexes; NULL decision superseded |
| `tests/test_catalog_list.py` | boundary straddle, offset/cursor agreement, segment fallbacks |
