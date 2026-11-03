# Add the Huey export worker on an existing Ubuntu site

This guide is for a **Linux production site that is already fully configured**
(Ubuntu 24.04+, Python + venv + Nginx + Gunicorn already running the POI Broker
web app). **Only the Huey/export feature is new.**

Nginx does not change. The existing web systemd unit does not change, except
for two new environment variables. The only new process is the Huey consumer,
supervised by systemd (`Restart=always`).

Why Huey + SQLite (not Celery/Redis): `docs/async_export/adr_huey_sqlite.md`.
Local check (app + worker, no systemd): `docs/async_export/local_app_and_worker.md`.

---

## What the feature adds

```
Browser
   │  HTTPS
   ▼
Nginx (unchanged)  ── proxy ──▶ Gunicorn (existing unit, unchanged except env)
                                    │  Flask app (wsgi:app)
                                    │  NEW: enqueues export jobs
                                    ▼
                            Huey SQLite queue  (NEW file)
                            HUEY_SQLITE_PATH, e.g. …/huey.db
                                    ▲
                                    │  polls & executes
                            Huey consumer  (NEW systemd: poi-broker-huey)
                                    │
                                    ▼
                            writes CSV under the app data directory
```

- **Gunicorn:** existing workers import the shared Huey instance and enqueue
  jobs onto **one** SQLite queue (`HUEY_SQLITE_PATH`).
- **Huey consumer (NEW):** a separate long-running process. systemd
  `Restart=always` restarts it if it exits.
- **ExportTask (NEW):** rows in `users.db` track PENDING → RUNNING →
  SUCCESS/FAILED. The Huey consumer's periodic stale-task guard fails stuck
  tasks after 30 minutes so a dead worker cannot permanently block a user’s
  export slot. The web app does not reset stale rows at startup.

---

## Environment variables

| Variable | Production value | Where |
|----------|------------------|--------|
| `HUEY_BACKEND` | `sqlite` | web env **and** `huey.env` |
| `HUEY_SQLITE_PATH` | absolute path, **identical** in both files | web env **and** `huey.env` |
| `HUEY_THREADS` | `2` (see sizing below) | `huey.env` only |
| `HUEY_LOGFILE` | e.g. `/var/log/poi_broker/huey.log` | `huey.env` only |
| `SECRET_KEY` | same as the web app | `huey.env` |
| `ALERTS_DB_PATH` | same as the web app | `huey.env` |
| `USERS_DB_PATH` | same as the web app | `huey.env` |

Optional (defaults are fine):

| Variable | Default | Purpose |
|----------|---------|---------|
| `EXPORT_STALE_MAX_AGE_SECONDS` | `1800` (30 min) | Fail stuck PENDING/RUNNING exports |
| `EXPORT_HEARTBEAT_SECONDS` | `60` | How often a live RUNNING export refreshes `updated_at` |
| `EXPORT_RETENTION_DAYS` | `10` | Delete old CSV files and `ExportTask` rows |

Keep `EXPORT_STALE_MAX_AGE_SECONDS >= 2 * EXPORT_HEARTBEAT_SECONDS` (defaults
1800 vs 60). If stale age is tuned near the heartbeat interval, a healthy long
export can be false-failed between heartbeats.

`HUEY_IMMEDIATE` applies only to the in-memory **development** backend. Do not
set it in production.

`HUEY_SQLITE_PATH` must be an **absolute** path. If the worker has no Flask app
context and the path is unset, it raises instead of silently creating a second
empty queue.

The SQLite queue uses WAL (`journal_mode=wal`) in `poi_broker/huey_config.py`.
It runs at **`PRAGMA synchronous=OFF`** (`fsync=False` is passed explicitly),
so `huey.db` has no power-loss corruption guarantee: a power loss or hard reset
can lose committed enqueues and can corrupt the queue. This matches upstream
and is deliberate. Recovery if the queue is ever corrupt is to delete it
(`rm "$HUEY_SQLITE_PATH"`); the next enqueue recreates it. Exports are not lost
with it — the `ExportTask` row in `users.db` stays `PENDING` and the consumer's
stale-task guard fails it after 30 minutes so the user can retry. Do not remove
the `fsync=False` argument (it is also huey's default, so deleting it changes
nothing but hides the intent). See `adr_huey_sqlite.md`, "Queue durability" and
its revisit trigger.

---

## 1. Update the code and dependencies

Pull the latest code and install `huey` (already in `requirements.txt`):

```bash
# use the existing app user, venv, and checkout path
git pull
.venv/bin/pip install -r requirements.txt
```

The alerts DB stays where it already is. Schema policy: the application never
alters the database — existing deployments are migrated with one-shot
`tools/apply_*.sql` scripts, and fresh installations are built from the full
schema files (`tools/usersdb_schema.sql`, `tools/alertsdb_schema.sql`), which
must never be run against an existing database. See
`docs/adr/adr_db_schema_migration_strategy.md`.

The original upstream `main` already contains the housekeeping and bookmark
indexes from the retired `apply_users_indexes.sql`. This branch's separate
one-active-export constraint is recorded in the
[migration ledger](../adr/adr_migration_completion_status.md). For an existing
database that does not yet have that constraint:

```bash
sqlite3 -bail "$USERS_DB_PATH" < tools/apply_active_export_index.sql
```

The script is safe to re-run (`IF NOT EXISTS` throughout) and fails loudly if
the data violates an invariant — e.g. two `PENDING`/`RUNNING` export rows for
one user. Inspect and resolve those tasks before retrying. Databases created
fresh from `tools/usersdb_schema.sql` already include
everything; all other historical one-shot scripts were removed after
production was migrated.

## 2. Add Huey variables to the existing web env

Add these two lines to the **existing** EnvironmentFile that Gunicorn already
reads (do not replace that file):

```bash
HUEY_BACKEND=sqlite
HUEY_SQLITE_PATH=/var/lib/poi_broker/huey.db
```

Adjust the path to the same data directory the live app already uses.

## 3. Create `/etc/poi-broker/huey.env` (NEW)

The worker needs the same secrets and DB paths as the web app, plus worker-only
settings. Use the **same** `HUEY_SQLITE_PATH` as in the web env:

```bash
SECRET_KEY="<same as the web env>"
ALERTS_DB_PATH=/var/lib/poi_broker/ztf_alerts_stream.db
USERS_DB_PATH=/var/lib/poi_broker/users.db
HUEY_BACKEND=sqlite
HUEY_SQLITE_PATH=/var/lib/poi_broker/huey.db
HUEY_THREADS=2
HUEY_LOGFILE=/var/log/poi_broker/huey.log
```

```bash
sudo chown root:<web-app-user> /etc/poi-broker/huey.env
sudo chmod 640 /etc/poi-broker/huey.env
```

## 4. Install the Huey systemd unit (NEW)

`docs/async_export/poi-broker-huey.service` is a template. Edit `User`, `Group`,
`WorkingDirectory`, `EnvironmentFile`, `ExecStart`, and `ReadWritePaths` so they
match the existing web service (same user, same venv, same data/log dirs).

The consumer **must** target `poi_broker.worker.huey`, not
`poi_broker.extensions.huey`. `worker.py` imports `poi_broker.tasks`, which
registers `create_export_file`. A worker started against `extensions.huey` runs
but never executes any task.

```bash
sudo cp docs/async_export/poi-broker-huey.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now poi-broker-huey
```

Restart the **existing** web/Gunicorn unit so it picks up the new env vars
(use the real unit name on the server):

```bash
sudo systemctl restart <existing-web-unit>
```

Verify:

```bash
sudo systemctl status <existing-web-unit>
sudo systemctl status poi-broker-huey
tail -f /var/log/poi_broker/huey.log
```

### Worker sizing

One Huey consumer process with `HUEY_THREADS` threads. Independent of the
Gunicorn worker count.

- **1 consumer × 2 threads** is the default: two concurrent exports, no SQLite
  lock contention from multiple consumer processes.
- Raise `HUEY_THREADS` to 3–4 only if exports queue under concurrent users.
- Do **not** run a second `huey_consumer` for the same queue.

### Supervision

Two separate mechanisms, both already in this repo / on Ubuntu:

1. **systemd** (`Restart=always` in `poi-broker-huey.service`) restarts the
   consumer if it exits. That is the process-level stall protection.
2. **Stale-task guard** (application code) marks `PENDING`/`RUNNING` exports
   older than 30 minutes as `FAILED`. The Huey consumer's periodic
   `cleanup_stale_export_tasks` (every 5 minutes) is the sole owner of that
   cleanup; the web app does not reset stale rows at startup. That frees the
   user’s active-export slot. It does **not** restart the worker; a live
   consumer with periodic scheduling enabled (Huey’s default; do not pass
   `--no-periodic`) is still required to run new jobs and to fail stuck ones.

## 5. Nginx — no change

The existing site already proxies `/export` to Gunicorn. Async export returns
immediately; no extra proxy timeout is required for job execution.

---

## 6. Verify the full flow

1. Open the site and log in.
2. Go to **/export**, build a query, click **Start Export**.
3. The page should return immediately.
4. Watch the worker:

```bash
tail -f /var/log/poi_broker/huey.log
# [..] EXECUTING: poi_broker.tasks.create_export_file
```

5. The page should show **Export Completed** with a download link.

```bash
# Task is registered (prints a task object, not None)
sudo -u <web-app-user> <venv>/bin/python -c \
  "from poi_broker.worker import huey; print(huey._registry.string_to_task('poi_broker.tasks.create_export_file'))"

ls -la /var/lib/poi_broker/huey.db
```

---

## Troubleshooting

### Export stays PENDING forever
1. Is the worker running? `sudo systemctl status poi-broker-huey`
2. Worker log: `tail -f /var/log/poi_broker/huey.log`
3. `HUEY_BACKEND=sqlite` in **both** env files?
4. `HUEY_SQLITE_PATH` the **same absolute path** in both files?
5. Consumer target is `poi_broker.worker.huey`?

### "database is locked"
- Only **one** Huey consumer: `ps aux | grep huey_consumer`
- Stale locks: `rm "$HUEY_SQLITE_PATH"-shm "$HUEY_SQLITE_PATH"-wal`
- `sudo systemctl restart poi-broker-huey`

### Exports block the HTTP request
- `HUEY_BACKEND` is not `sqlite`, or `HUEY_IMMEDIATE=true` is set on the web
  process. Check the existing web env.

### Stale exports blocking a user
- The consumer's periodic guard fails exports with no progress for 30 minutes
  (`EXPORT_STALE_MAX_AGE_SECONDS`). A live worker heartbeats `updated_at` while
  writing and only commits status transitions from expected states, so a long
  healthy export is not false-failed and cannot flip a stale `FAILED` row back
  to `SUCCESS`. After that stale window, `POST /export` itself fails the
  **current user’s** stale `PENDING`/`RUNNING` row and lets them enqueue again
  (even if the consumer is still down). Fresh active rows still 409 until they
  finish or go stale. To unstick **immediately** (aborts in-flight exports,
  including rows younger than 30 minutes):
  `python -m flask --app wsgi:app fail-stale-exports --force` with the web app
  env.
