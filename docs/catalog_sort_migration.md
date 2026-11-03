# Deployment: catalog sort keysets + composite sort indexes

> **Recorded as applied to production 2026-10.** Both scripts below reported
> success. They are committed in this branch but not in original upstream
> `main` as checked on 2026-10-14, so they remain until upstream integration.
> See the [consolidated migration ledger](adr/adr_migration_completion_status.md).
> The commands below are the preserved runbook, not outstanding deployment work.
>
> Follow-on: the magnitude repair made it possible to list NULL-valued sort
> rows rather than hiding them — see
> `docs/adr/adr_catalog_null_segment_pagination.md`.

Alerts DB (`ztf_alerts_stream.db`) only. `users.db` is untouched. There is no
auto-migration: **the application never alters the database.** The code change
can deploy before this migration — every query stays correct either way, just
slow on the first page of a date or locus-id sort (see *Why* below).

## What is in the box

| File | Purpose |
|---|---|
| `tools/apply_fix_bad_magnitudes.sql` | 83 corrupt magnitudes: relocate the passband, NULL the magnitude |
| `tools/apply_catalog_sort_indexes.sql` | 6 single-column sort indexes → 6 `(column, alert_id, locus_id)` composites |
| `tools/rollback_catalog_sort_indexes.sql` | reverts the index migration only |
| `tools/apply_sql.py` | applies a `.sql` file (there is no `sqlite3` CLI on this host) |
| `tools/verify_catalog_pagination.py` | verifies the result; exits non-zero on failure |

## Before you start

Measured on production, read-only:

```
journal_mode   wal          <- readers never block; only the writer lock is held
page_size      4096
page_count     1,997,858
freelist_count 0
auto_vacuum    0            <- do NOT run VACUUM, see Rollback
sqlite         3.49.1
disk free      208 GB       <- need 1.3 GB
```

Two operations, in this order. Total ~80 s of writer lock; stop alert ingestion
first if you can. Step 1 first because it is 16 s and retires a live crash.

```powershell
cd C:\dev\python\poi_broker
$db = "C:\dev\python\_broker_db\ztf_alerts_stream.db"

# 0. BACKUP (7.62 GB, ~5-10 s). Do not skip this.
Copy-Item $db "$db.bak"

# 1. corrupt magnitudes        ~16 s   +5 pages
python tools/apply_sql.py tools/apply_fix_bad_magnitudes.sql --db $db --yes

# 2. composite sort indexes   ~60 s   +1252 MB
python tools/apply_sql.py tools/apply_catalog_sort_indexes.sql --db $db --yes

# 3. verify (single command; non-zero exit means something is wrong)
$env:VERIFY_DB = $db
python tools/verify_catalog_pagination.py
```

Step 0 note: the magnitude script's pre-flight `SELECT` prints the affected rows.
Run the script against the **backup** first if you want that record on disk
before touching production — the `UPDATE`s are harmless on a copy:

```powershell
python tools/apply_sql.py tools/apply_fix_bad_magnitudes.sql --db "$db.bak" --yes `
  > magnitude_before.txt
```

## Expected output

```
step 1  applied in 15.7s   8.18 GB -> 8.18 GB   (+0 MB)
step 2  applied in 60.5s   8.18 GB -> 9.43 GB   (+1252 MB, 92,721 free pages)

verify: composite sort indexes present: 6 of 6
        FAILURES: 0
```

Verifier sections, all of which must pass:

1. first and last page under 1 s for every sort (was 7.5–9.0 s)
2. a deep keyset walk at row 150,000 returns exactly the rows OFFSET returns
3. page cost does not grow with depth (page 20,000 as fast as page 1)
4. "Last" reaches the true final page and terminates
5. no storage-class filter, and the count covers every row (3,178,668 for every sort)
6. `zero non-numeric magnitudes` and `null + numeric == total`
7. every segment phase is index-ranged, never a full index SCAN
8. independent oracle: two-phase walk matches single-query ordering over the boundary window

## What changes for users

- **Every row of the catalog becomes reachable.** The page cap made 96.9% of it
  unreachable under any secondary sort (rows past 100,000). First/Prev/Next/Last
  are now cursor links with no ceiling.
- **Column headers now actually sort.** Previously `date_alert_mjd DESC` was the
  leading term for every sort, so "RA ascending" ordered RA only within one
  MJD — page 1 began at ra=250.66 and never went below ra=277.97, against a
  catalog minimum of 0.000253.
- **The default landing page goes from ~9 s to under 1 ms.** Pre-existing, and
  the reason step 2 exists.
- **Every row is now listed under every sort.** An earlier revision of this
  migration hid rows whose sort value was missing or non-numeric — 31.85% for
  magnitude. That exclusion has since been removed: a nullable sort column is
  walked as two segments so those rows appear, last, and the row count is the
  full catalog. See `docs/adr/adr_catalog_null_segment_pagination.md`.
- **One sort key per request.** A URL with two or three `sort__*` keys now uses
  the first one; the others could only break ties within equal values and never
  changed the rows shown.
- 83 alerts gain a usable passband and stop being able to 500 the page.

## Why step 2 is worth 1.25 GB

Without a `(sort_column, alert_id, locus_id)` index, SQLite cannot produce the
catalog's ORDER BY from an index. The keyset *steps* were already ~0 ms on a
single-column index, but the **first and last page are index scans, not seeks** —
a scan cannot stream a partial sort, so the temp b-tree spans all 3.18M rows.
Measured before → after, through the app, on the production table:

| sort | first page before | after |
|---|---|---|
| `date` (the default landing page) | 9.033 s | 0.000 s |
| `locus_id` | 8.931 s | 0.001 s |
| all others | ≤0.001 s | ≤0.001 s |

`alert_id` deliberately gets no composite: the PK autoindex is already
`(alert_id, locus_id, date_alert_mjd)`, which measured 275 MB and one build for
no gain.

## Operational notes

- Both scripts are **re-runnable** (`IF NOT EXISTS` / `IF EXISTS`, and `UPDATE`
  predicates that match nothing once applied). An interrupted run is recovered
  by running the same command again. Individual statements are atomic on their
  own, so a partial run leaves a state the same command completes.
- Step 2 creates all six composites **before** dropping any single-column index,
  so no column is ever left unindexed.
- `CREATE INDEX` holds SQLite's single writer lock for its duration (~60 s for
  all six). WAL readers are unaffected; writes queue behind it.
- Re-running step 2 afterwards costs ~5 s (the trailing `ANALYZE`) and changes
  0 bytes.

## Rollback

Indexes only — measured cost ~60 s:

```powershell
python tools/apply_sql.py tools/rollback_catalog_sort_indexes.sql --db $db --yes
```

That restores the six single-column indexes and drops the composites, returning
the catalog to its previous behaviour: still correct, but ~9 s on the first and
last page of a date or locus-id sort.

Everything at once:

```powershell
Copy-Item "$db.bak" $db -Force
```

The magnitude repair is not reversed by the rollback script. To reverse it,
move `ant_passband` back into `ant_mag_corrected` for those rows —
`magnitude_before.txt` (above) records the original values.

**Do not run `VACUUM`.** `auto_vacuum=0`, so it rewrites the whole 9.43 GB file
under an exclusive lock, and the only thing it could recover is the ~380 MB of
freed index pages — which it would not return to the filesystem anyway.

## Related

| Path | Role |
|---|---|
| `docs/adr/adr_composite_sort_indexes_and_keyset.md` | the decision |
| `docs/adr/catalog_sort_keyset_empirical_findings.md` | measurements behind it |
| `docs/adr/adr_db_schema_migration_strategy.md` | one-shot script policy |
| `docs/adr/adr_no_composite_sort_indexes.md` | superseded; kept for history |
| `tests/test_apply_catalog_scripts.py` | pins both scripts' invariants |
| `tools/alertsdb_schema.sql` | same indexes, for fresh installs |
