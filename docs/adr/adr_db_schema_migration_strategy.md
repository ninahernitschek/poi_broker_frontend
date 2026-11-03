# ADR: SQLite schema migration strategy (one-shot apply scripts, no framework)

> Completion/retirement rules and status follow
> [Migration completion and current status](adr_migration_completion_status.md).
> Use that consolidated ledger for open work. Fresh-install-only schema files
> and the no-runtime-DDL decision remain in force.

- **Status:** Accepted
- **Date:** 2026-10
- **Scope:** all database schema changes (`users.db`, alerts DB); deployment docs
- **Disposition of:** auditSBA.md finding 4 (resolved by cleanup)

## Context

auditSBA.md finding 4 flagged that `tools/usersdb_schema.sql` is not
idempotent (bare `CREATE TABLE`s) while `docs/async_export/ubuntu_add_worker.md`
instructed applying it to an existing production `users.db`. The team's model
was already different: the full schema files are for **fresh installations**,
and schema changes for existing deployments travel as **one-shot
`apply_*.sql` scripts**. The original decision treated four of the five
then-existing apply scripts as completed based on the maintainer's production
migration statement. The later completion ledger supersedes that retirement
criterion and records which migrations are complete in original upstream
`ninahernitschek/poi_broker_frontend` `main`.

## Decision

1. **The application never alters the database.** No auto-migration at boot —
   pinned by `tests/test_security_regressions.py::test_boot_does_not_alter_legacy_users_db`.
2. **Fresh installations** are built from `tools/usersdb_schema.sql` and
   `tools/alertsdb_schema.sql`. Those files are **never** run against an
   existing database (they are intentionally not idempotent — plain DDL catches
   mistakes on a fresh DB instead of silently skipping).
3. **Existing deployments** are migrated by one-shot `apply_*.sql` scripts.
   Each script's lifecycle:
   - **created** together with the fix (schema-file update + script + tests),
   - **applied** to production when the fix deploys,
   - **removed** together with script-specific tests once the migration is
     carried by original upstream `main`, as defined in the
     [completion ledger](adr_migration_completion_status.md). No separate
     server inspection is required for retirement here.
4. The completed housekeeping and bookmark indexes from
   `tools/apply_users_indexes.sql` are retired. The branch's
   one-active-export-per-user constraint (`uix_export_task_one_active_per_user`)
   is isolated in **`tools/apply_active_export_index.sql`** until it reaches
   original upstream `main`. See the ledger for all current migration status.
5. **Caveat:** the parent repo also tracks these files, so an upstream merge
   may re-add deleted scripts — re-delete them.

## Alternatives considered

- **Migration framework** (Alembic etc.): rejected — against the project's
  SQLite-operations model (see `docs/async_export/adr_huey_sqlite.md`): two
  plain SQLite files, one web process, no migration state table to drift from
  reality.
- **Make the schema files idempotent** (`IF NOT EXISTS` everywhere) so they
  double as migrations: rejected — it blurs "fresh install DDL" (where plain
  DDL catches typos) with "migration" (where re-runnability matters), and
  hides partial-application states instead of failing loudly.
- **Delete all five apply scripts**: originally rejected because
  `apply_users_indexes.sql` also carried the branch's new active-export
  constraint. That constraint is now preserved in its own apply script while
  the completed portion is retired.

## Consequences

- Before retiring an apply script, compare its contents with original upstream
  `main` under the completion ledger's rule. Isolate any branch-only SQL in a
  separate script instead of retiring it with completed work.
- Historical script contents remain recoverable from git history.
- Deployment docs must describe *pending* migrations only; applied ones are
  removed from docs together with their scripts (historical mentions become
  past tense with a pointer here).

## Invariants (for implementers and coding agents)

1. Never run `usersdb_schema.sql` / `alertsdb_schema.sql` against an existing
   database.
2. Never make the app alter the database at boot or at runtime.
3. A new DB change must include the schema files, an apply script, tests, and a
   doc note together. Completion and script retirement follow the
   [completion ledger](adr_migration_completion_status.md).
4. Retain `tools/apply_active_export_index.sql` until the active-export
   constraint reaches original upstream `main`. Do not restore the retired
   `tools/apply_users_indexes.sql` or its script-specific tests.

## Related

| Path | Role |
|------|------|
| `tools/usersdb_schema.sql` | users.db schema — fresh installs |
| `tools/alertsdb_schema.sql` | alerts DB schema — fresh installs |
| `docs/adr/adr_migration_completion_status.md` | authoritative completion rules and current status |
| `tools/apply_active_export_index.sql` | retained: one-active-export-per-user constraint awaiting upstream integration |
| `tools/apply_fix_bad_magnitudes.sql` | retained: magnitude repair awaiting upstream integration |
| `tools/apply_catalog_sort_indexes.sql` | retained: sort indexes awaiting upstream integration |
| `docs/catalog_sort_migration.md` | runbook and recorded production application for the two scripts above |
| `tests/test_apply_active_export_index.py` | pins the retained active-export script's invariants |
| `docs/password_reset/deployment.md` | historical runbook, kept as record |
| `tests/test_security_regressions.py::test_boot_does_not_alter_legacy_users_db` | pins "the app never alters the DB" |
