# Catalog sort and pagination: empirical findings

Date: 2026-10
Decision: [`adr_composite_sort_indexes_and_keyset.md`](adr_composite_sort_indexes_and_keyset.md)
Supersedes: `adr_no_composite_sort_indexes.md`

Every number below was measured against the production table
`_broker_db/ztf_alerts_stream.db`, not a synthetic stand-in, because the
original decision was made on 300k synthetic rows and turned out to be wrong.

## Subject under test

```
featuretable          3,178,668 rows      7.62 GB (1,997,858 pages x 4 KB)
PRIMARY KEY           (alert_id, locus_id, date_alert_mjd)
classification        3,178,667 rows
ant_mag_corrected     NULL 1,012,259 (31.8%) | REAL 2,166,326 | TEXT 83
locus_ra / locus_dec  0 NULL
date_alert_mjd        0 NULL
```

## Findings

### 1. The page cap made 96.9% of the catalog unreachable

`MAX_PAGE = 1000` × `PAGE_SIZE = 100` capped OFFSET at row 100,000. Keyset was
date-only, so **any** secondary sort fell back to OFFSET and both `?page=1001+`
and the "Last" link (`last_page <= MAX_PAGE` guard) refused to continue.

```
reachable under a secondary sort   100,000 rows  (3.1%)
unreachable                       3,078,668 rows (96.9%)
MAX_EXPORT_ROWS default           1,000,000   -> export could see 10x more than the UI
```

The cap's stated rationale was that a deep page jump "must not make SQLite skip
the whole index". Measured, for the query the app actually issued
(`ORDER BY date_alert_mjd DESC, locus_ra ASC`):

```
page    1  offset         0   0.000 s
page  100  offset     9,900   0.008 s
page 1000  offset    99,900   0.076 s     <- worst reachable
[blocked] page 10000            2.554 s
[blocked] page 31787 (last)     8.292 s
```

The guard cost 0.076 s of exposure. It was the wrong trade.

### 2. Secondary sorts were not sorts at all

`_apply_catalog_sorts` chained `.order_by()` calls. SQLAlchemy **appends**
successive `order_by()` calls, so `date_alert_mjd DESC` remained the leading
term:

```sql
-- ?sort__locus_ra=asc actually produced:
ORDER BY featuretable.date_alert_mjd DESC, featuretable.locus_ra ASC
```

Rendered page 1 of "RA ascending":

```
mjd=61187.396297  ra=250.664349
mjd=61187.396297  ra=250.869534
...
mjd=61187.381737  ra=257.499980     <- "ascending" from 250.66 to 257.50
```

RA only ordered rows sharing one MJD. The lowest reachable RA at the old
row-100,000 boundary was 277.969790; the catalog minimum is 0.000253.

### 3. The ORDER BY was not a total order

Without PK tiebreakers, rows with equal sort values had no defined order, so
OFFSET paging could duplicate or skip rows.

| sort column | ambiguous groups | rows at risk |
|---|---|---|
| `ant_mag_corrected` | 59,774 | 979,648 |
| `locus_id` | 3,687 | 7,380 |
| `ztf_object_id` | 3,614 | 7,234 |
| `locus_ra` | 70 | 140 |
| `locus_dec` | 49 | 98 |
| `alert_id` | 0 | 0 |

`(alert_id, locus_id)` has **0 duplicate pairs** across all 3,178,668 rows, so
appending them as tiebreakers makes the order total.

### 4. Naive keyset would have stranded users on 10,123 pages

SQLite evaluates `NULL > NULL` as NULL, i.e. false. A cursor inside a tie group
of NULLs matches nothing and the walk stops dead:

```
cursor inside NULL block: mag=None  alert=ztf_candidate:3053457490215010003
rows returned by next keyset step: 0        (while 500,000+ rows remained)
```

`ant_mag_corrected` is NULL for 1,012,259 rows = 10,123 pages of 100.

Where the walk *does* work, it works correctly. Cross-boundary acceptance test
(keyset walk vs OFFSET walk, 300 rows, seeded at offset 150,000):

| seed position | keyset rows | matches OFFSET |
|---|---|---|
| inside REAL block, early | 1200 | yes |
| inside REAL block, mid | 1200 | yes |
| last REAL row -> crosses into TEXT | 83 | yes |
| last TEXT row -> terminates | 0 | yes |
| inside NULL block | **0** | yes (but wrongly) |

The 83 TEXT rows sort after every REAL in SQLite's type ordering; the cursor
crosses that boundary correctly in both directions **when the cursor value is
numeric**. When it is not, it does not:

```
cursor from a TEXT row: mag='R' alert=ztf_candidate:2614536535115010031
rows returned            : 'g', 'g', 'g', 'g', 'R'    <- wrong
ground truth next 5      : 'R', 'R', 'R', 'R', 'R'
```

Because TEXT sorts after REAL, a *descending* magnitude sort starts with those
rows, so the cursor lands on one — SQLite then compares the TEXT cursor
ascending while the ORDER BY is descending, and pages silently skip and repeat.
Keeping the value as a string does not help (the mismatch is in SQLite's
comparison, not the parse), and rejecting it as unparseable is worse: the cursor
would be dropped and "Next" would re-render page 1 forever.

On the production table the TEXT block happens to fit inside page 1 (83 rows <
100), so the cursor for that page lands on a REAL and the bug is latent. It
would fire as soon as the block spanned a boundary — for example if `PAGE_SIZE`
were reduced. The fix is the orderability filter in finding 6.

### 5. Keyset needs no composite index; the *first page* does

This was the finding that reversed the decision. Keyset steps were already
~0 ms on a plain single-column index:

```
locus_ra       SEARCH USING INDEX idx_featuretable_locus_ra        0.0000s @1.5M deep
locus_dec      SEARCH USING INDEX idx_featuretable_locus_dec       0.0000s
alert_id       SEARCH USING COVERING INDEX sqlite_autoindex_..._1  0.0000s
locus_id       SEARCH USING INDEX idx_featuretable_locus_id        0.0000s
ztf_object_id  SEARCH USING INDEX idx_featuretable_ztf_object_id   0.0000s
ant_mag        SEARCH USING INDEX idx_featuretable_ant_mag_...     0.0000s
```

(On a faithful copy: a single composite index also took OFFSET 1.5M on a true
sort from 23.905 s to 0.049 s, and a keyset step from 0.407 s to 0.000 s.)

But the first page is an index **scan**, not a seek, and a scan cannot stream a
partial sort — the temp b-tree spans every row. Measured before/after on a
byte-identical copy of the 7.62 GB database, through the app's own code path:

| sort | first page before | first page after | last page before | after |
|---|---|---|---|---|
| `date` (default landing page) | **9.033 s** | **0.000 s** | 8.910 s | 0.001 s |
| `locus_id` | 8.931 s | 0.001 s | 8.935 s | 0.001 s |
| `locus_ra` | 0.001 s | 0.000 s | 0.001 s | 0.001 s |
| `locus_dec` | 0.001 s | 0.001 s | 0.000 s | 0.001 s |
| `ztf_object_id` | 0.001 s | 0.001 s | 0.001 s | 0.001 s |
| `ant_mag_corrected` | 0.001 s | 0.000 s | 0.002 s | 0.001 s |
| `alert_id` | 0.001 s | 0.001 s | 0.001 s | 0.000 s |

A separate pre-existing latent bug surfaced here, not in the sort logic:
`mag_filter()` called `round()` on the 83 TEXT magnitudes and raised
`TypeError`, turning one bad row into a **500 for the whole page**. It had been
unreachable because a descending magnitude sort never actually applied. Making
the sort real exposed it from page 1.

### 6. Why the sort column had to be restricted to one storage class

`IS NOT NULL` was the first attempt. It handles NULLs but not the TEXT rows,
which are equally uncursable. Every alternative to restricting the column was
measured:

| approach | cost |
|---|---|
| two-phase walk, `ORDER BY (alert_id, locus_id)` | 3.27 s |
| redundant leading `col` term, `ORDER BY (col, alert_id, locus_id)` | 3.82 s |
| drop the `classification` join from the NULL-phase query | 3.07 s |
| `COALESCE(col, sentinel)` + expression index | index served, but NULLs sort first in DESC |

The planner insists on seeking `ant_mag_corrected IS NULL` (1M rows) and then
sorting, rather than using the PK. And the `COALESCE` option makes a
descending magnitude view open on 1M alerts that have no magnitude at all.

> **CORRECTION (2026-10). The 3.27 s figure above is obsolete, by a factor of
> ~27,000.** Every measurement in this table was taken *before* the composite
> sort indexes existed, so the only index available to the NULL segment was the
> primary key — hence "rather than using the PK". With
> `idx_featuretable_sort_ant_mag_corrected` applied, `IS NULL` is a contiguous
> key range on the leading column and the same two-phase walk costs **0.12 ms**:
>
> ```
> SEARCH featuretable USING INDEX idx_featuretable_sort_ant_mag_corrected
>   (ant_mag_corrected=? AND (alert_id,locus_id)>(?,?))     0.12 ms
> ```
>
> The two-phase design was adopted on that basis; see
> [`adr_catalog_null_segment_pagination.md`](adr_catalog_null_segment_pagination.md).
> The 3.82 s cost of a leading `col IS NULL` ORDER BY term still stands — that
> one is a genuine property of the expression, not of the index set.

Restricting the column to a single storage class fixes both hazards in one
predicate and costs nothing — `EXPLAIN QUERY PLAN` is unchanged against the
composite sort index:

| predicate on `ant_mag_corrected` | first page | plan |
|---|---|---|
| `IS NOT NULL` | 0.0000 s | `SEARCH ... (ant_mag_corrected>?)` |
| `typeof(col) IN ('integer','real')` | 0.0000 s | `SCAN ... USING INDEX idx_featuretable_sort_ant_mag_corrected` |
| keyset step, either predicate | 0.0000 s | row-value `SEARCH` |

The only measurable effect is that an *ascending* magnitude first page now skips
the 1M NULL rows in ~69 ms rather than seeking past them.

Primary-key columns are exempt: they are the row's identity, so always present
and consistently typed (0 NULL and 0 type mismatches measured).

Because this hides rows, it is stated in the UI and applied to the count query so
"Rows: N" still matches what is listed.

The `cached_catalog_total()` fast path initially ignored this and reported
3,178,668 for a list that can only reach the 2,166,326 rows that carry a numeric
magnitude (3,178,668 − 1,012,259 NULL − 83 TEXT). Fixed by keying the cache on
the active sort signature.

### 7. Index cost, measured on the real table

| | |
|---|---|
| build, each index | ~3 s on a 7-column copy; **56.9 s for all six** on the 7.62 GB table |
| net disk | 7.62 GB -> 9.43 GB = **+1,252 MB** |
| re-running the migration | 4.95 s, 0 bytes changed |
| index sizes (copy) | composites ~197-275 MB each; single-column ~44 MB each |

`DROP INDEX` does not shrink the file without a `VACUUM`, so the ~265 MB of
retired single-column indexes remain allocated. That is the gap between the
~990 MB the raw sizes suggest and the 1,252 MB actually observed.

`alert_id` was excluded: its composite would be identical to the PK autoindex
`(alert_id, locus_id, date_alert_mjd)`, which already serves `ORDER BY
(alert_id, locus_id)`. Measured saving: 275 MB and one index build.

## Verification performed after the change

Against the real table, before and after the migration, using the app's own
query-building code (not hand-written SQL) — `tools/verify_catalog_pagination.py`:

- **35/35 checks pass** on the migrated copy: 7 sorts x (first and last page
  under budget, deep keyset walk row-for-row identical to an OFFSET walk at
  offset 150,000, deep page under 50 ms, "Last" reaching the true final page
  with no dangling Next link, `IS NOT NULL` present exactly where the NULL rule
  says it should be).
- The verifier was validated against the **un-migrated** table as a negative
  control: it fails there, naming `date` and `locus_id` at ~9 s each, and exits
  non-zero.

  An earlier version of check 1 grepped the plan for `TEMP B-TREE FOR ORDER BY`
  and *passed* on the un-migrated database. That marker is wrong: without the
  composite index the plan reads `USE TEMP B-TREE FOR LAST 2 TERMS OF ORDER BY`,
  which looks like a bounded partial sort but spans all 3.18M rows because a
  scan cannot stream it. The check is now a timing assertion, because timing is
  the property that actually matters.
- **HTTP walk** following only the rendered First/Prev/Next/Last links for 6
  sorts: 100 rows on the Last page each, no overlap with the first page, no
  dangling Next, worst page 0.047 s.
- Legacy `before_mjd` cursor still pages; `?page=N` beyond the cap still 404s but
  with a message naming the alternative.
- 630 unit tests pass, including new guards for: cursor/offset equivalence with
  heavy ties, NULL exclusion reaching the count query, index column order,
  sort-key/index coverage, and the TEXT-magnitude 500.

## Things that were believed and turned out to be wrong

Recorded because each cost real time:

1. "`.order_by()` replaces in SQLAlchemy" — it **appends**. This is why every
   secondary sort silently inherited `date_alert_mjd` as its leading term.
2. "The composite index is only needed for deep OFFSET jumps" — it is mostly
   needed for the **first and last pages**, which scan.
3. "+990 MB net for the composites" — actually +1,252 MB; `DROP INDEX` without
   `VACUUM` does not return space.
4. "Keyset on a nullable column can order the NULLs" — `NULL > NULL` is NULL,
   so the walk terminates mid-catalog.
5. "The 9 s landing page is inherent" — it was a full sort caused by a missing
   index, not by the data volume.

## Reproducing

```bash
# behavioural checks against a copy of the production DB
VERIFY_DB=/path/to/copy.db python tools/verify_catalog_pagination.py

# apply the index migration
sqlite3 /path/to/ztf_alerts_stream.db < tools/apply_catalog_sort_indexes.sql
```

Never run the migration against production without a backup: `CREATE INDEX` is
atomic and safe, but it holds the writer lock for ~57 s.
