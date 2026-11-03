# ADR: Composite sort indexes for the catalog, and one keyset cursor for every sort

- **Status:** Accepted (supersedes `adr_no_composite_sort_indexes.md`)
- **Date:** 2026-10
- **Scope:** `featuretable` indexes; catalog sorting and pagination (`/`)
- **Supersedes:** `adr_no_composite_sort_indexes.md` (accepted 2026-10). Its
  conclusion — "do not add composite sort indexes" — is now **measured to be
  wrong**, and its revisit trigger had already been met.
- **Evidence:** `docs/adr/catalog_sort_keyset_empirical_findings.md`

## Context

A code review of the `MAX_PAGE` cap turned up a chain of defects, each of which
had to be settled by measurement against the real 3,178,668-row
`ztf_alerts_stream.db` rather than by argument:

1. `MAX_PAGE = 1000` × `PAGE_SIZE = 100` capped OFFSET pagination at row 100,000.
   Any secondary sort fell back to OFFSET, so **96.9% of the catalog was
   unreachable** and both `?page=1001+` and the "Last" link refused to get there.
2. The cap's stated rationale ("a deep page jump must not make SQLite skip the
   whole index") does not hold for the query the app actually issued: that page
   cost **0.076 s**.
3. `?sort__locus_ra=asc` did **not sort by RA**. `_apply_catalog_sorts` chained
   `.order_by()` calls and SQLAlchemy *appends* them, so `date_alert_mjd DESC`
   stayed the leading term for every secondary sort. RA only ordered rows
   sharing one MJD. Page 1 of "RA ascending" began at ra=250.66; the lowest
   reachable RA (277.97) was nowhere near the catalog minimum (0.000253).
4. The resulting ORDER BY was not a total order, so OFFSET paging could
   duplicate or skip rows: `ant_mag_corrected` had 59,774 ambiguous groups
   covering 979,648 rows.

The previous ADR dismissed all of this as an "unmeasured problem" and deferred
the keyset-cursor alternative pending a trigger. Both trigger conditions were
now satisfiable, and the deferred alternative measured at ~0 ms.

## Measurement (this is the decision)

On the production table, using the app's own query (9 projected columns plus a
`LEFT JOIN classification`), best of 2–3:

| | single-column index | composite `(col, alert_id, locus_id)` |
|---|---|---|
| first page, any sort | **2.2 s** (full sort) | **0.000 s** (index scan) |
| keyset step at 1.5M rows deep | 0.000 s | 0.000 s |
| OFFSET 1.5M on a true sort | 23.9 s | 0.049 s |

Before/after on a byte-identical copy of the 7.62 GB database, via the app:

| sort | first page before | after |
|---|---|---|
| `date` (the default landing page) | **9.033 s** | **0.000 s** |
| `locus_id` | 8.931 s | 0.001 s |
| all other sorts | ≤0.001 s | ≤0.001 s |

The previous ADR's core claim — that a secondary sort costs "a partial sort
bounded by tie group size, which measured indistinguishable from the default
path" — was true for *keyset steps* and false for the *first and last pages*,
which are index **scans**, not seeks. A scan cannot stream a partial sort: the
temp b-tree covers every row. That is the whole gap.

## Decision

1. **One sort column per request.** A second and third `sort__*` key can only
   break ties *within* equal values of the first, so it never changed the rows
   the user saw — it only lengthened the ORDER BY and the cursor. Extra keys in
   a URL are accepted and ignored; the first key in `SORT_KEYS` order wins.
2. **The sort column leads the ORDER BY**, followed by `alert_id, locus_id` as
   tiebreakers. `(alert_id, locus_id)` is unique (verified: 0 duplicate pairs),
   so this is a *total* order — strictly more deterministic than before.
3. **Keyset pagination for every sort**, cursor `(sort_column, alert_id,
   locus_id)`, served by a composite index per sort column. In-app navigation
   (First/Prev/Next/Last) is entirely cursor-based and unbounded. `?page=N`
   remains only as a legacy OFFSET path for hand-typed and bookmarked URLs,
   bounded by `MAX_PAGE`, now with an error message that says what to do.
4. **Six composite indexes**, replacing the six single-column sort indexes
   (their leftmost prefix still serves equality and range filters):
   `date_alert_mjd`, `ztf_object_id`, `locus_id`, `locus_ra`, `locus_dec`,
   `ant_mag_corrected`. `tools/apply_catalog_sort_indexes.sql`.
5. **`alert_id` gets no composite**: the PK autoindex is already
   `(alert_id, locus_id, date_alert_mjd)`, so ordering by `(alert_id, locus_id)`
   is free. `ant_passband` is a filter column, never a sort column.
6. **Sort columns are restricted to values the cursor can order.** Columns that
   are not primary-key columns are filtered to a single storage class
   (`typeof(col) IN ('integer','real')` for numeric columns, `typeof(col) =
   'text'` for text columns). See below — this is a data-visibility decision.

## Why the sort column must be restricted

SQLite orders by storage class (NULL < numeric < text < blob), and a keyset
cursor has to reproduce that order exactly. Two hazards, both measured on
production:

**NULLs cannot be ordered at all.** `NULL > NULL` is NULL, i.e. false, so a
cursor inside a tie group of NULLs matches nothing and the walk stops dead.
`ant_mag_corrected` is NULL for 1,012,259 of 3,178,668 rows (31.8%, 10,123
pages); a step from 500,000 rows into that block returned 0 rows.

**A mismatched storage class silently returns the wrong rows.** 83 production
rows store `ant_mag_corrected` as TEXT (`'R'`, `'g'` — apparently a leaked
passband). Because TEXT sorts after REAL, a *descending* magnitude sort starts
with them, so the cursor lands on one. SQLite then compares that TEXT cursor
ascending while the ORDER BY is descending:

```
cursor from a TEXT row: mag='R' alert=ztf_candidate:2614536535115010031
rows returned            : 'g', 'g', 'g', 'g', 'R'    <- wrong
ground truth next 5      : 'R', 'R', 'R', 'R', 'R'
```

Keeping the value as a string does not help, because the mismatch is in
SQLite's comparison, not in the parse. Worse, if the cursor had been rejected as
unparseable instead, `parse_keyset_request` would have returned no cursor at
all and Next would silently re-render page 1 — an unbreakable loop.

Alternatives to restricting the column, all measured and rejected:

| approach | result |
|---|---|
| two-phase walk (non-NULL, then NULL block) | planner refuses the PK; 3.27 s per page |
| redundant leading `col` term in ORDER BY | 3.82 s — still a full sort of 1M rows |
| drop the join from the NULL-phase query | 3.07 s — not viable, the join supplies `prob_class` |
| `COALESCE(col, sentinel)` + expression index | NULLs land first in DESC, so page 1 is 1M unmagnitude'd alerts |

The `typeof` predicate is free: `EXPLAIN QUERY PLAN` is unchanged against the
composite sort index, and first/last/keyset timings are unchanged (the one
measurable effect is that an *ascending* magnitude first page now skips the
1M NULL rows in ~69 ms instead of seeking past them).

Primary-key columns (`date_alert_mjd`, `alert_id`, `locus_id`) are exempt: they
are the row's identity, so they are always present and consistently typed
(measured: 0 NULL and 0 type mismatches).

Because this hides rows, it is **surfaced in the UI** rather than silently
applied, and the count query carries the same restriction so "Rows: N" matches
the listing (a magnitude sort lists 2,166,326 of 3,178,668 rows). The rule is
schema-driven, so adding a sortable column needs no second hand-maintained list.

`mag_filter()` was also crashing on those 83 rows: it called `round()` on a
str, raising `TypeError` and failing the whole page with a 500. Unreachable
before, because a descending magnitude sort never actually applied.

## Costs, measured

| | |
|---|---|
| index build, all six, production table | **56.9 s** |
| disk, net | **+1,252 MB** (7.62 GB → 9.43 GB) |
| re-running the migration | 4.95 s, 0 bytes (idempotent) |

`DROP INDEX` does not return space to the filesystem without a `VACUUM`, so the
retired single-column indexes (~265 MB) remain inside the file. That is why the
net is +1,252 MB rather than the ~+990 MB the raw index sizes suggest.

## Operational notes

- `CREATE INDEX` is a single transaction: it completes or rolls back, leaving
  the table untouched. Readers are never blocked; the SQLite writer lock is held
  for the duration. Schedule off-peak if alert ingestion is running.
- All six CREATEs run before any DROP, so no column is ever left unindexed.
  Interrupting and re-running is safe.
- `ANALYZE` at the end refreshes `sqlite_stat1` so the planner prefers the
  composites. Skipping it leaves the planner guessing.

## Consequences

- Every sort is now a real sort, and every row of the catalog is reachable.
- The default landing page went from 9.0 s to under a millisecond.
- `sort__date` is no longer special. There is one pagination code path.
- Alerts whose sort column is missing or of the wrong storage class are not
  listed under that sort. This is shown to the user, and the count query agrees
  with the listing.
- Cursor URLs use `before_v`/`after_v` instead of `before_mjd`/`after_mjd`.
  Pre-2026-10 bookmarks carrying the old params still resolve.

## Invariants (for implementers and coding agents)

1. The index column order must be exactly `(sort_column, alert_id, locus_id)`.
   A different order reintroduces the full sort silently. Guarded by
   `test_sort_indexes_match_the_keyset_cursor_order`.
2. Every key in `SORT_KEYS` must have a matching entry in `SORT_COLUMNS` and
   `SORT_COLUMN_ATTRS`, and (except `alert_id`) an `idx_featuretable_sort_<attr>`
   index. Guarded by `test_every_sortable_column_has_an_index`.
3. The sort column must stay the **leading** ORDER BY term. Do not
   "restore" a `date_alert_mjd` primary term ahead of it.
4. Do not add a cursor term to the keyset filter without adding the matching
   index column and updating both invariants above.
5. Do not drop the orderability filter for a non-primary-key sort column; the
   cursor will strand on NULL tie groups or silently mis-page across a
   storage-class boundary.

## Related

| Path | Role |
|------|------|
| `poi_broker/services/catalog_list.py` | keyset fetch, cursor encode/parse, OFFSET legacy path |
| `poi_broker/services/catalog_query.py` | `SortSpec`, `resolve_sort`, NULL rule, projection |
| `poi_broker/app.py` | route wiring, cursor hrefs, `MAX_PAGE` guard |
| `poi_broker/models.py` | composite index declarations |
| `tools/apply_catalog_sort_indexes.sql` | migration for existing deployments |
| `tools/alertsdb_schema.sql` | same indexes for fresh DBs |
| `docs/adr/catalog_sort_keyset_empirical_findings.md` | full before/after evidence |
