# ADR: Huey + SQLite for async CSV export

- **Status:** Accepted
- **Date:** 2026-06
- **Feature:** Visual-query-builder CSV export (`/export`)

## Context

Exporting alert rows matching a query-builder filter can take long enough to
block a Gunicorn worker. The site already runs on Ubuntu with Nginx + Gunicorn
and stores data in SQLite (`ALERTS_DB_PATH`, `USERS_DB_PATH`). The team is
small; adding infrastructure that needs its own server process is costly.

The queue had to:

- Run export jobs off the HTTP request path.
- Persist jobs across process restarts.
- Fit the existing SQLite operations model.
- Avoid a new networked service (Redis, RabbitMQ, etc.).

## Decision

Use **Huey** with the **SQLite** backend (`SqliteHuey`) in production, and
**MemoryHuey** (immediate/synchronous) for local development and pytest.

Production adds **one** extra process: the Huey consumer, supervised by
**systemd** (`Restart=always`). See `docs/async_export/ubuntu_add_worker.md`.

## Alternatives considered

### Celery + Redis (rejected)

Celery is the usual Flask choice. It would require Redis (or RabbitMQ) as a
broker: another package, another daemon, another failure domain, and ongoing
maintenance. That extra server process was the deal-breaker. Celery’s footprint
is also larger than needed for a single job type (CSV export) at modest
concurrency.

### Huey + Redis (rejected)

Huey can use Redis. That still adds a Redis process. The only gain over
Huey + SQLite is higher throughput, which this feature does not need.

### In-request / thread pool only (rejected)

A thread or `multiprocessing` pool inside Gunicorn does not survive worker
restarts, does not give a durable job record, and still ties export CPU to the
web process.

## Consequences

- **Smaller footprint:** `huey[sqlite]` in `requirements.txt`; queue file is
  another SQLite DB (`HUEY_SQLITE_PATH`). No Redis.
- **One new process, not a new service:** the consumer is a Python process
  started with `python -m huey.bin.huey_consumer poi_broker.worker.huey`.
  systemd is enough; do not add supervisord, wrapper `.sh`/`.bat` scripts, or
  a second consumer on the same queue.
- **SQLite limits:** one consumer (default 2 threads). WAL is enabled in
  `poi_broker/huey_config.py`. App and worker must share the **same absolute**
  `HUEY_SQLITE_PATH` or they silently use two empty queues.
- **Queue durability:** the queue file runs at
  **`PRAGMA synchronous=OFF`**, because `poi_broker/huey_config.py` passes
  `fsync=False` to `SqliteHuey` explicitly. This is deliberate and matches
  upstream, but it means `huey.db` has **no power-loss corruption guarantee**:
  a power loss or hard reset can lose already-committed enqueues and can
  corrupt the WAL. The documented recovery for a corrupt queue is deleting it
  (`rm "$HUEY_SQLITE_PATH"`) and letting the next enqueue recreate it.
  Losing a queue commit is benign here, because the queue is a work queue and
  not the system of record: the durable intent for every export is the
  `ExportTask` row in `users.db`, which stays `PENDING` if its enqueue is lost,
  and the consumer's stale-task guard fails it after
  `EXPORT_STALE_MAX_AGE_SECONDS` so the user retries. A lost commit costs a
  replay, not data.
  `fsync=False` **must stay an explicit argument** even though it is also
  huey's own default: huey 3.x applies
  `pragma synchronous=(2 if fsync else 0)` unconditionally, so deleting the
  argument leaves the queue at `OFF` silently rather than reverting to
  SQLite's default. See the revisit trigger below before changing it.
- **Dev vs prod:** default `HUEY_BACKEND=memory` runs tasks in-process (no
  worker). In that mode `POST /export` blocks until the CSV is written; keep
  local queries small or lower `EXPORT_MAX_ROWS`. There is no consumer, so
  periodic stale-cleanup never runs: unstick a Ctrl+C with
  `python -m flask --app wsgi:app fail-stale-exports --force` (same env as
  the app). Production sets `HUEY_BACKEND=sqlite`. `HUEY_IMMEDIATE` applies
  only to the memory backend.
- **User-facing state** lives in `ExportTask` (`users.db`), not in Huey’s
  result table. The consumer’s periodic stale-task guard
  (`cleanup_stale_export_tasks`, every 5 minutes) fails all stale
  `PENDING`/`RUNNING` rows after 30 minutes so a dead worker cannot block a
  user’s export slot forever. Independently, `POST /export` may fail **the
  current user’s** stale active row (same age window) before returning 409, so
  a down consumer cannot strand a user forever. Web processes do not run
  global cleanup at startup. The worker only transitions from expected
  statuses (compare-and-swap) and heartbeats `updated_at` while `RUNNING`, so
  a live long export is not false-failed and cannot be resurrected after a
  stale `FAILED`. Keep `EXPORT_STALE_MAX_AGE_SECONDS >= 2 *
  EXPORT_HEARTBEAT_SECONDS` (defaults 1800 vs 60) so a healthy export is not
  false-failed between heartbeats.
- **One Flask app per consumer process:** Huey handlers reuse a lazy
  process-wide app (`_get_worker_app` in `tasks.py`) instead of calling
  `create_app()` on every task.

## Invariants (for implementers and coding agents)

These are easy to get wrong; the consumer will look "fine" while doing nothing.

1. Point the consumer at **`poi_broker.worker.huey`**, never
   `poi_broker.extensions.huey`. `worker.py` imports `poi_broker.tasks` so
   `@huey.task` handlers register. `extensions.huey` only creates the instance.
2. Start the consumer with **`python -m huey.bin.huey_consumer`**. The name
   `huey_consumer` is a console script, not an importable module.
3. Do not introduce Celery, Redis, or a second queue backend without a new ADR.
4. Do not run export work in the request thread when `HUEY_BACKEND=sqlite`.
5. Do not call `reset_stale_export_tasks()` from `create_app()` / `init_huey()`.
   The Huey consumer’s periodic `cleanup_stale_export_tasks` is the **global**
   owner. `POST /export` may call it with `user_id=` (age-aware only, never
   `ignore_age`) to free that user’s stale slot. Leave periodic scheduling
   enabled (do not pass `--no-periodic`).
6. Automated tests: `tests/test_huey_tasks.py` (memory backend via `conftest.py`).
   Manual two-process check: `docs/async_export/local_app_and_worker.md`.
7. Do not delete the `fsync=False` argument from `SqliteHuey(...)` as a
   "cleanup", and do not describe `huey.db` as ACID / `synchronous=FULL`
   anywhere. The queue runs at `synchronous=OFF`; see "Queue durability" above.
   `tests/test_huey_config.py` pins both the code state and the docs.

## Revisit trigger: queue durability (`fsync`)

**Current decision:** keep `fsync=False` (`PRAGMA synchronous=OFF`).

**Proposed change:** pass `fsync=True` to `SqliteHuey` in
`poi_broker/huey_config.py`, which yields `PRAGMA synchronous=FULL` — ACID in
WAL mode, so a power loss can no longer corrupt `huey.db`.

**Why it is not done in the source tree:** this is a real write-path behavior
change, and the evidence needed to make it safely is not available from the
repository — it requires the deployed systemd unit configuration and measured
commit latency on the production queue. Committing it on the strength of a
micro-benchmark would be an unverified change to a deployed path.

**Measure before deciding (on the production host):**

1. Confirm the actual runtime state of both units, not the config:
   ```bash
   sqlite3 "$HUEY_SQLITE_PATH" "PRAGMA journal_mode;"   # persisted in the header
   ```
   `journal_mode` is stored in the database header, but `synchronous` is
   **per-connection** and is not stored anywhere. Read it from a live huey
   connection (`huey.storage._create_connection().execute('pragma
   synchronous')`) or from a `huey_consumer` log line; a fresh `sqlite3`
   connection will misleadingly report SQLite's compile-time default.
2. Measure enqueue commit latency with and without `fsync=True` under the real
   `POST /export` rate. The argument for FULL is that the write rate is
   human-scale (one enqueue per rate-limited `POST /export`) so commit-time
   fsync should cost milliseconds per real queue event and nothing on idle
   consumer polls. That argument needs confirming against actual traffic.
3. If FULL is adopted, update "Queue durability" above, invariant 7,
   `docs/async_export/ubuntu_add_worker.md`, and `changelog.md` in the same
   change — and relax the assertions in `tests/test_huey_config.py` that
   currently pin the queue at `synchronous=OFF`.

Note the deliberate asymmetry with the alerts database, which runs at
`synchronous=NORMAL` (`PRAGMA synchronous = NORMAL` in
`_configure_sqlite_pragmas`, `poi_broker/__init__.py`; pinned as NFR-007 in
`docs/spec.md`). That is a separate, intentional choice for a re-loadable
database and is **not** a reason to weaken the queue further. The two pragmas
cannot interfere: `_configure_sqlite_pragmas` is bound to SQLAlchemy
`connect` events for `db.engine` / `db.engines['users']`, while huey opens
`huey.db` through its own raw `sqlite3` connection that never goes through
SQLAlchemy.

## Related code

| Path | Role |
|------|------|
| `poi_broker/huey_config.py` | Memory vs SQLite instance |
| `poi_broker/extensions.py` | Shared `huey` object |
| `poi_broker/worker.py` | Consumer entrypoint (registers tasks) |
| `poi_broker/tasks.py` | `create_export_file`, stale-task / retention jobs |
| `poi_broker/cli.py` | `fail-stale-exports` one-off (local `--force` unstick) |
| `poi_broker/routes/export.py` | Enqueue + download; age-aware per-user stale fail |
| `docs/async_export/poi-broker-huey.service` | systemd unit template |
