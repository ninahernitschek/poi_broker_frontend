# Test async export locally (app + Huey worker)

Run the Flask app and Huey worker as separate processes with the SQLite
backend — the same code path as production, without Nginx or systemd.

Both terminals need the same environment:

```bash
export SECRET_KEY=test-secret-key
export HUEY_BACKEND=sqlite
export HUEY_SQLITE_PATH="$(pwd)/instance/huey.db"
export ALERTS_DB_PATH=../_broker_db/ztf_alerts_stream.db
export USERS_DB_PATH=../_broker_db/users.db
```

Set `HUEY_SQLITE_PATH` to the same absolute path in both terminals. Relative
queue paths are rejected; create the `instance` directory first if needed.

**Database:** `users.db` is created from `tools/usersdb_schema.sql` — the app
never alters the database itself. If your dev `users.db` predates a schema
change (e.g. `export_task.snapshot_mjd`), recreate it:

```bash
sqlite3 ../_broker_db/users.db < tools/usersdb_schema.sql
```

Dev databases are disposable; recreate rather than migrate. (Historical
note: the one-shot `apply_export_snapshot_mjd.sql` backfill script was
removed after production was migrated — see
`docs/adr/adr_db_schema_migration_strategy.md`.)

**Terminal 1 — app**

```bash
python -m flask --app wsgi:app run
```

**Terminal 2 — worker** (target `poi_broker.worker.huey`, not `extensions.huey`)

```bash
python -m huey.bin.huey_consumer poi_broker.worker.huey --workers=2 --worker-type=thread --verbose
```

Log in, go to `/export`, build a query, click **Start Export**. The page should return immediately. The worker should log:

```
EXECUTING: poi_broker.tasks.create_export_file
```

The page then shows **Export Completed** with a download link.

Confirm the shared queue DB exists and has tables:

```bash
ls -la ./instance/huey.db
sqlite3 ./instance/huey.db .tables
```

## Stuck export after killing the app (memory backend)

Default `HUEY_BACKEND=memory` has no consumer, so periodic cleanup never runs.
A Ctrl+C during `POST /export` leaves a `PENDING`/`RUNNING` row that 409s
retries. Age-aware `POST /export` only frees rows past
`EXPORT_STALE_MAX_AGE_SECONDS` (default 30 minutes), so a just-interrupted
export still needs an immediate unstick.

Unstick with the **same environment** as the app (same `USERS_DB_PATH`):

```bash
python -m flask --app wsgi:app fail-stale-exports --force
```

`--force` fails every `PENDING`/`RUNNING` row now (aborts in-flight exports).
Without `--force`, only rows older than `EXPORT_STALE_MAX_AGE_SECONDS`
(default 30 minutes) are failed.

Do not put this in `tools/`: that directory has its own `.env` and would
point at the wrong `users.db`.

Production (existing Ubuntu site): `docs/async_export/ubuntu_add_worker.md`.
Decision record (Huey + SQLite vs Celery + Redis): `docs/async_export/adr_huey_sqlite.md`.
