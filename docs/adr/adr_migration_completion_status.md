# Migration completion and current status

- **Status:** Accepted
- **Date:** 2026-10-14
- **Supersedes:** completion/retirement rules and current-status notes in
  `adr_db_schema_migration_strategy.md`. Its fresh-install and no-runtime-DDL
  decisions still apply.

## Decision

The maintainer defines **done/applied** as the migration already carried by
the original upstream `ninahernitschek/poi_broker_frontend` `main`. No separate
server inspection is required to retire that migration here. The fork's
`origin/main` and local `main` are not the completion reference.

Check the upstream script's contents as well as its filename. If this branch
added new SQL to a completed upstream script, retire the completed portion and
give the new SQL a separate `apply_*.sql` file. This avoids silently treating a
new constraint as already applied or rerunning completed work.

Once a new migration reaches original upstream `main`, remove its apply script
and script-specific tests here, and record completion below. Fresh-install
schemas retain the resulting schema. An upstream merge that reintroduces a
completed apply script does not reopen that work.

## Consolidated status

Compared with original upstream `main` at
`0aa045eec88fb307cd30c941a675b2d980ad3206` on 2026-10-14 (fetched directly;
no server changes).

| Migration | Status in this branch |
| --- | --- |
| Email verification expiry | Done in upstream; apply script already retired |
| Password-change watermark | Done in upstream; apply script already retired |
| Export snapshot MJD | Done in upstream; apply script already retired |
| Lookup indexes | Done in upstream; apply script already retired |
| Export housekeeping and unique bookmark-name indexes (`apply_users_indexes.sql`) | Done in upstream; retired here |
| One-active-export-per-user constraint | Awaiting upstream integration; isolated in `tools/apply_active_export_index.sql` |
| Magnitude data repair | Awaiting upstream integration; retain `tools/apply_fix_bad_magnitudes.sql` |
| Composite catalog sort indexes | Awaiting upstream integration; retain `tools/apply_catalog_sort_indexes.sql` and rollback companion |

The catalog runbook records an earlier successful production application of
the last two scripts. That observation is preserved; their remaining work is
upstream integration/retirement, not a request to apply them again.

## Validation

The active-export migration tests exercise reruns, existing duplicate active
tasks, terminal tasks, different users, and agreement with the model/fresh
schema. Catalog migration tests continue to cover the retained scripts.
Never run full schema files against existing databases or introduce automatic
DDL at application startup.
