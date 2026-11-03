# ADR: No composite sort indexes for the catalog's secondary sorts

> **SUPERSEDED 2026-10 by
> [`adr_composite_sort_indexes_and_keyset.md`](adr_composite_sort_indexes_and_keyset.md).**
> Kept for history. Its conclusion is now measured to be wrong: a composite
> `(sort_column, alert_id, locus_id)` index takes the first page of any sort
> from 2.2 s (full sort) to 0.000 s, and the default landing page from 9.0 s to
> 0.000 s on the 3.18M-row production table. The claim below that a secondary
> sort costs "a partial sort bounded by tie group size" holds for keyset *steps*
> but not for the first and last pages, which are index *scans* and so temp-sort
> every row. Its revisit trigger had in any case already been met.

- **Status:** Superseded
- **Date:** 2026-10
- **Scope:** `featuretable` indexes; catalog pagination/sorting (`/`)
- **Disposition of:** auditSBA.md finding 9 (wontfix — measured not-a-bug)

## Context

auditSBA.md finding 9 claimed that any secondary sort (`sort__alert_id`,
`sort__locus_id`, `sort__ztf_object_id`, `sort__locus_ra`, `sort__locus_dec`,
`sort__ant_mag_corrected`) "materialises a full sort of `featuretable` per
page request" because the available indexes are single-column, and proposed
either composite `(date_alert_mjd, <col>)` indexes or extending the keyset
cursor to carry the active secondary sort column. It was never measured —
until 2026-10-04.

## Measurement (the evidence for this decision)

Synthetic SQLite DB at real row width (278 columns, 300k rows, the exact 8
single-column indexes from `alertsdb_schema.sql` / `models.py`), best of 3
per query, SQLite bundled with Python 3.12:

| query | time | plan |
|---|---|---|
| date only (default), page 2 | 0.2 ms | date index scan, no sort |
| date + each "unindexed" secondary column, page 2 | **0.2 ms** | date index scan + `TEMP B-TREE FOR LAST TERM OF ORDER BY` |
| heavy ties (300 distinct dates → 1000-row groups), page 2 | 2.8 ms | same partial sort |
| heavy ties, page 1000 | **314 ms** (vs 2.0 ms date-only) | same partial sort |

Conclusions:

1. **The audit's core premise is wrong for modern SQLite.** The planner walks
   the `date_alert_mjd` index for the primary term and temp-sorts only within
   tie groups of equal `date_alert_mjd` ("last term of ORDER BY" partial
   sort) — never a full-table sort. With realistic alert data (MJD essentially
   unique per alert), tie groups are size ~1 and every secondary sort costs
   the same as the default path.
2. **The audit's index-coverage table was wrong**: `(date, locus_id)` is not a
   leftmost prefix of the PK `(date, alert_id, locus_id)` — `locus_id` gets
   the same partial sort as the rest; and it missed mixed-direction sorts
   (`date DESC, alert_id ASC`), which no ASC-only composite index could serve
   anyway.

## Decision

Do **not** add composite sort indexes. Keep the 8 single-column indexes.
Secondary sorts share the date index and pay a partial sort bounded by tie
group size, which measured indistinguishable from the default path.

## Alternatives considered

- **Composite `(date_alert_mjd, <col>)` indexes** (audit suggestion): rejected.
  Four-to-five extra indexes on the widest, highest-ingest table tax every
  loader batch and require a production index build (long write transaction)
  to fix a path measured at ~0.2 ms; and ASC-only composites cannot serve the
  mixed-direction sorts users produce by clicking a column header while the
  date sort stays DESC.
- **Keyset cursor carrying the secondary sort column** (audit alternative):
  rejected for now. Real code risk (cursor format, direction handling, tests)
  for an unmeasured problem. Revisit only via the trigger below.

## Revisit trigger

Reopen with a new ADR only if **both** hold on production data:

1. Tie groups are large:
   `SELECT MAX(c) FROM (SELECT COUNT(*) c FROM featuretable GROUP BY date_alert_mjd)`
   returns thousands (batch-loaded rows sharing timestamps), **and**
2. deep pages under a secondary sort see real traffic (F8 caps pages at
   `MAX_PAGE = 1000`, which bounds — but does not eliminate — the walk).

In that case prefer the keyset-with-secondary-sort option over more indexes.

## Invariants (for implementers and coding agents)

1. Do not add `(date_alert_mjd, <col>)` composite indexes without a new ADR.
2. Do not "fix" the partial sort by removing the `date_alert_mjd` primary
   ORDER BY term — `_apply_catalog_sorts` must always keep a deterministic
   primary sort (see `catalog-invalid-sort-unordered` in `changelog.md`).

## Related

| Path | Role |
|------|------|
| `poi_broker/services/catalog_query.py` | `_apply_catalog_sorts` (ORDER BY shape) |
| `poi_broker/services/catalog_list.py` | keyset vs OFFSET selection, `MAX_PAGE` |
| `poi_broker/models.py` | single-column index set |
| `tools/alertsdb_schema.sql` | same indexes for fresh DBs |
| `docs/adr/adr_huey_queue_path_fail_fast.md` | sibling wontfix record |
