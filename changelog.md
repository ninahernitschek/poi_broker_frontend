# Changelog

Behavioral changes and fixes from the 2026-09/2026-10 review rounds, newest
first. Architecture decisions live in `docs/adr/`; regression coverage in
`tests/` (notably `tests/test_review_regressions.py`). Historical forensic
detail is in git history.

## 2026-10-07 — catalog NULL rows are listed, not hidden

ADR: `docs/adr/adr_catalog_null_segment_pagination.md`. Supersedes the
NULL-exclusion part of `adr_composite_sort_indexes_and_keyset.md`, whose
rejection of the two-phase walk was measured *before* the composite sort
indexes existed; the same walk now costs 0.12 ms, not 3.27 s.

- **Rows with a missing sort value are no longer hidden.** Sorting by magnitude
  previously omitted 31.8% of the catalog (1,012,342 rows), because a NULL
  cursor cannot advance. Every sort now reports and lists all 3,178,668 rows.
- **A nullable sort column is two segments, dispatched in Python**: rows with a
  value first, rows without last — in both directions, so an ascending magnitude
  sort no longer opens on a page of blanks. Each phase is a point seek on the
  composite index. Expressing it as one `OR`-ed predicate, or as a leading
  `col IS NULL` ORDER BY term, degrades to a full index scan and is avoided.
- **New `?nulls=1` link** jumps to the head of the no-value block, so those rows
  are reachable without paging past 2.16M rows. The UI notice now offers it
  instead of explaining an exclusion that no longer happens.
- **Fixed a pre-existing bug in `count_matches`**: `with_entities(func.count())`
  on a bare query emits `SELECT count(*)` with no `FROM`, which SQLite evaluates
  as the constant `1`. `GET /api/catalog-count` with no filters reported
  `{"count":1}` regardless of table size. It only looked correct because any
  filtered query puts a table reference in the `WHERE`.
- **Fixed a first/last-page regression** the new verifier check caught: "Last"
  offset 3,178,568 rows (3.6 s). It now reverse-seeks instead, which is the
  point of keyset pagination.
- An unparseable cursor raises `MalformedCursor` rather than silently
  re-rendering page 1, which would have made Next an unbreakable loop.
- 642 tests pass. `tools/verify_catalog_pagination.py` has two new checks: every
  segment phase must be index-ranged, and an **independent oracle** — a
  single-query ordering with a leading `IS NULL` term, correct by construction —
  must match the two-phase walk over a 200-row window straddling the boundary.

## 2026-10-05 — catalog sorting and pagination

Supersedes the 2026-10-04 "no composite sort indexes (measured not needed)"
decision; see `docs/adr/adr_composite_sort_indexes_and_keyset.md` for the ADR
and `docs/adr/catalog_sort_keyset_empirical_findings.md` for the measurements.
All figures are against the 3,178,668-row / 7.62 GB production table.

- **Column header sorts now actually sort.** `_apply_catalog_sorts` chained
  `.order_by()` calls and SQLAlchemy *appends* them, so `date_alert_mjd DESC`
  stayed the leading term for every secondary sort. Clicking "RA ascending"
  ordered RA only within a single MJD: page 1 began at ra=250.66 and the lowest
  reachable RA (277.97 at the old page ceiling) was far from the catalog minimum
  (0.000253). The clicked column now leads the ORDER BY, with `alert_id,
  locus_id` as tiebreakers.
- **Every row of the catalog is now reachable.** `MAX_PAGE` capped OFFSET
  pagination at row 100,000 and any secondary sort fell back to OFFSET, so
  3,078,668 rows (96.9%) were unreachable and both `?page=1001+` and the "Last"
  link refused to continue. In-app navigation (First/Prev/Next/Last) is now
  keyset-paginated for every sort, at a cost independent of depth. `?page=N`
  remains as a legacy OFFSET path only; its 404 now names the alternative.
- **The default catalog page went from 9.0 s to under a millisecond.** This was
  pre-existing: without a `(sort_column, alert_id, locus_id)` index the first
  and last pages are index *scans*, which cannot stream a partial sort, so the
  temp b-tree spanned all 3.18M rows. Six composite indexes replace the six
  single-column sort indexes (`tools/apply_catalog_sort_indexes.sql`; +1,252 MB,
  56.9 s, idempotent). `alert_id` needs none — the PK autoindex is already
  `(alert_id, locus_id, date_alert_mjd)`.
- **The ORDER BY is now a total order.** Previously rows with equal sort values
  had no defined order, so OFFSET paging could duplicate or skip rows —
  `ant_mag_corrected` had 59,774 ambiguous groups covering 979,648 rows.
  `(alert_id, locus_id)` has zero duplicate pairs, so the order is total.
- **Sort columns are restricted to values the cursor can order.** SQLite orders
  by storage class, and two production hazards broke the cursor: `NULL > NULL`
  is false, so a cursor inside a NULL tie group matches nothing (1,012,259 rows,
  10,123 pages), and 83 rows store `ant_mag_corrected` as TEXT (`'R'`, `'g'`),
  which a descending magnitude sort puts first — a cursor there returns the
  *wrong* rows, because SQLite compares the TEXT cursor ascending while the
  ORDER BY is descending. Non-primary-key sort columns are now filtered to one
  storage class, on both the list and count queries, and the page states which
  values are omitted. An unfiltered magnitude sort previously reported 3,178,668
  rows while the listing could only reach 2,166,326.
- **Fixed a 500 caused by 83 corrupt rows.** `mag_filter()` called `round()` on
  rows whose `ant_mag_corrected` is stored as TEXT (`'R'`, `'g'`), raising
  `TypeError` and failing the whole page. All 83 come from a single ingest
  batch at MJD 60368.5-60369.3; the newest 439,251 rows contain none. Unreachable
  before, because a descending magnitude sort never actually applied; it is now
  reachable from page 1 and renders the raw value. The upstream schema codes the
  passband 1:g, 2:r, 3:i, and all 83 bad values are exactly those tokens (79
  `R` = code 2, 4 `g` = code 1) sitting in the magnitude column while
  `ant_passband` was empty — an ingest column mix-up. `tools/apply_fix_bad_magnitudes.sql`
  relocates the passband into its own column and NULLs the unusable magnitude,
  so no information is lost.
- **Multi-sort URLs collapse to one key.** A second and third `sort__*` key could
  only break ties within equal values of the first, so they never changed the
  rows shown. First key in `SORT_KEYS` order wins; extras are ignored.
- Cursor URLs use `before_v`/`after_v` instead of `before_mjd`/`after_mjd`;
  pre-2026-10 bookmarks carrying the old params still page correctly.
- `tools/verify_catalog_pagination.py` checks sorting and pagination against a
  real database (plans, cursor/offset equivalence at depth, depth-independence,
  walk termination, NULL rule). Validated as a negative control against the
  un-migrated table, where it fails.

## 2026-10-04

- Catalog page: rows with missing coordinates (RA/Dec) render empty cells instead of crashing the whole page.
- Catalog pagination: page jumps beyond a hard ceiling (1000 pages) are rejected with 404 instead of forcing expensive database scans; the "Last" link hides beyond the ceiling.
- Filter bookmarks: saving from a keyset-paginated page no longer fails — pagination state is stripped and the bookmark restores as a pure filter URL; the accepted bookmark parameters are derived from the catalog's authoritative key sets.
- Catalog UI: the `prob_class` dropdown and query-builder values render from the server-side allow-list instead of hand-maintained copies.
- Observing tool: timezone lookup data loads once per process instead of on every request (~0.5 s per request saved).
- Exports: the CSV path is recorded on the task as soon as the file is created, so exports interrupted by a worker crash are reclaimed by retention cleanup instead of lingering forever; retention cleanup now covers failed exports as well as successful ones.
- Exports: the CSV column list is computed once instead of once per exported row (up to 1M rows per export).
- Schema tooling: four already-applied one-shot migration scripts removed; the schema/migration strategy is documented in `docs/adr/adr_db_schema_migration_strategy.md`.
- Dead code removed: `feature_service.get_available_features()` (no callers).
- Decisions recorded as ADRs in `docs/adr/`: the task-queue path fail-fast is kept (no implicit fallback), no composite sort indexes (measured not needed), `tools/insert_test_user.sql` kept as an internal testing tool.
- Task queue durability, corrected: the 2026-09-27 claim that the queue "no longer runs with `synchronous=OFF`" was false — removing the `fsync=False` argument changed nothing, because `fsync=False` is also huey's default and huey 3.x applies `pragma synchronous=(2 if fsync else 0)` unconditionally. The queue runs at `synchronous=OFF` in production and always has (upstream does the same). `fsync=False` is now passed explicitly, the four doc sites that claimed `synchronous=FULL`/ACID are corrected, and a test pins both the runtime pragma and the docs so they cannot drift apart again. Switching to `fsync=True` needs measured commit latency and the deployed systemd config, so it is recorded as a revisit trigger rather than done blind.

## 2026-09-27

- Query-builder driven endpoints (preview, export, watchlists) reject oversized `in`/`not_in` selections, overly deeply nested rule payloads, and request bodies above 1 MiB with 400/413 instead of failing with 500s.
- Client-supplied integers beyond SQLite's range no longer cause 500s on the catalog page, export status/download, bookmark and observatory deletion, or favorites group IDs.
- The observing plot rejects out-of-range RA/Dec and NaN coordinates with 400 instead of 500.
- The catalog's primary sort can no longer be dropped by an invalid `sort__date` value — offset pages are always deterministically ordered.
- `/api/preview-query` is now rate-limited like its sibling export endpoint.
- Task queue durability (CORRECTED 2026-10-04 — the 2026-09-27 entry below
  claiming this was fixed was wrong; see the 2026-10-04 section): the SQLite
  queue still runs at `PRAGMA synchronous=OFF`. `fsync=False` is now passed
  explicitly and deliberately, and the docs state the durability tradeoff
  instead of claiming ACID. `fsync=True` is proposed as a follow-up gated on
  production measurement — see the revisit trigger in
  `docs/async_export/adr_huey_sqlite.md`.
- Deployment: the users-DB index script now enforces "at most one active export task per user" at the database level.
- Catalog: keyset cursor serialization round-trips MJD values exactly — some page boundaries could previously duplicate or skip a row.
- `AUTH_RATE_LIMIT_VERIFY_EMAIL` is configurable via environment, like the other auth rate limits.
- Feature column allow-lists unified to a single source of truth (saved defaults and the feature plot can no longer disagree).
- `.env.example` ships an absolute `HUEY_SQLITE_PATH`, consistent with the deployment docs.

## 2026-09-26

- Saving a watchlist with invalid query-builder rules returns 400 (was 406).
- Multi-select filters combined with equality operators return actionable guidance (use `in`) instead of a bare 400.

## 2026-09-15

- Input validation hardening across endpoints: malformed JSON payloads (non-string names/IDs, booleans where numbers are expected), impossible calendar dates (including ISO-looking tokens and observing-plot dates), whitespace-only passwords on reset/change, and CSRF failures on the JSON export endpoint now return clean 400s instead of 500s or misleading redirects.
- The observing plot handles polar summer (no night) gracefully instead of erroring.
- The query builder rejects non-column fields (e.g. relationship attributes) with a clear 400.
