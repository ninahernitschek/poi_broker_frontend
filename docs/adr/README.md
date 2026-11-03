# Architecture Decision Records

Decisions that shape how this codebase is built and operated. Each record is
self-contained; the format follows `docs/async_export/adr_huey_sqlite.md`.

## Index

| Record | Decision |
|--------|----------|
| [`docs/async_export/adr_huey_sqlite.md`](../async_export/adr_huey_sqlite.md) | Huey + SQLite as the async-export queue; one consumer process; no Redis. |
| [`docs/password_reset/adr_session_invalidation.md`](../password_reset/adr_session_invalidation.md) | Session invalidation on credential change via a `login_at` watermark. |
| [`adr_huey_queue_path_fail_fast.md`](adr_huey_queue_path_fail_fast.md) | Missing `HUEY_SQLITE_PATH` must crash at boot — no implicit path fallback (auditSBA finding 2, wontfix). |
| [`adr_no_composite_sort_indexes.md`](adr_no_composite_sort_indexes.md) | **Superseded** by `adr_composite_sort_indexes_and_keyset.md` — kept for history. |
| [`adr_composite_sort_indexes_and_keyset.md`](adr_composite_sort_indexes_and_keyset.md) | One keyset cursor for every catalog sort, and `(sort_column, alert_id, locus_id)` composite indexes — supersedes the record above, whose measurement was wrong. |
| [`catalog_sort_keyset_empirical_findings.md`](catalog_sort_keyset_empirical_findings.md) | Measurements behind the record above, including the five claims that turned out to be wrong. Not an ADR — evidence for one. |
| [`adr_catalog_null_segment_pagination.md`](adr_catalog_null_segment_pagination.md) | List NULL rows instead of hiding them: a nullable sort becomes two segments dispatched in Python. Supersedes the NULL-exclusion part of the record above, whose rejection of the two-phase walk was measured before the composite indexes existed. |
| [`adr_insert_test_user_sql.md`](adr_insert_test_user_sql.md) | Keep `tools/insert_test_user.sql` with its known credential — internal testing tool, accepted risk (auditSBA finding 17, wontfix). |
| [`adr_db_schema_migration_strategy.md`](adr_db_schema_migration_strategy.md) | One-shot `apply_*.sql` scripts for existing deployments, removed once applied; full schema files are fresh-install-only (auditSBA finding 4, resolved). |
| [`adr_migration_completion_status.md`](adr_migration_completion_status.md) | Current migration ledger; original upstream `main` defines done/applied. Supersedes older completion/retirement rules. |
| [`adr_catalog_count_budgets.md`](adr_catalog_count_budgets.md) | Counts are optional, budgeted metadata; supersedes the segment-count cache requirement. |

## Migration status

The [consolidated ledger](adr_migration_completion_status.md) is the source of
current completion status. The [catalog runbook](../catalog_sort_migration.md)
preserves its recorded application and verification steps.

## Convention

- One decision per record; immutable once Accepted — supersede, don't edit.
- Audit findings deliberately **not** fixed ("wontfix") get a record here so
  the rationale outlives the audit document; that document (`auditSBA.md`,
  a 2026-09 branch review) was removed on 2026-10-04 once all its findings
  were dispositioned — "Disposition of: auditSBA.md finding N" headers refer
  to it, and `changelog.md` records the outcomes.
- Revisit triggers recorded in an ADR are commitments: a trigger firing means
  a new ADR, not a silent code change.
