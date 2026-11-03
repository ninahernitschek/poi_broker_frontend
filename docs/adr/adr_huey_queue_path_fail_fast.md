# ADR: Huey SQLite queue path must fail fast (no implicit fallback)

- **Status:** Accepted
- **Date:** 2026-10
- **Scope:** Task queue configuration (`HUEY_BACKEND=sqlite` / `HUEY_SQLITE_PATH`)
- **Disposition of:** auditSBA.md finding 2 (wontfix)

## Context

An audit (auditSBA.md, finding 2) flagged that with `HUEY_BACKEND=sqlite` and
no `HUEY_SQLITE_PATH`, both production processes crash at import:
`extensions.py` creates the shared `huey` instance at module import, and
`get_huey_sqlite_path()` raises `RuntimeError` when there is no Flask app
context to take the instance path from. The audit suggested resolving the path
lazily (or deriving it) so the web app could always boot.

The crash is real and reproducible — but it is the designed behavior, not a
defect. The failure mode it prevents is far worse than the failure it causes.

## Decision

Keep the fail-fast. Both processes **require** an explicit absolute
`HUEY_SQLITE_PATH` when `HUEY_BACKEND=sqlite`. Without it, they must crash at
import with the documented message — never fall back to a derived path.

Why: a web process and a worker that each derive the path from their own
working directory (or package location) each create their **own empty queue
database**. Nothing errors: enqueues succeed, the consumer polls happily, and
export tasks are silently dropped forever. A missing env var, by contrast,
fails loudly at boot and — under systemd `Restart=always` — loops every 5
seconds with a clear log line until the operator fixes the config. One is a
config typo; the other is silent data loss.

## Alternatives considered

- **Lazy resolution inside an app context** (audit suggestion): rejected. The
  web app would resolve the path, but the huey consumer has no app context
  during boot, so a second resolution path remains — and the misconfiguration
  now surfaces later and less clearly. Production sets the env var either
  way, so implicit boot buys nothing operationally.
- **cwd- or package-derived fallback at import**: rejected outright. This is
  the split-queue hazard itself; the two processes commonly run from different
  working directories (systemd `WorkingDirectory` vs dev shells).

## Consequences

- Operators must set `HUEY_SQLITE_PATH` (absolute, same value for web and
  worker) — documented in `docs/async_export/ubuntu_add_worker.md` and
  `docs/async_export/adr_huey_sqlite.md`.
- A web-only dev running `HUEY_BACKEND=sqlite` without the path cannot boot;
  local development belongs on the `memory` backend (see the Huey ADR).
- The instance-folder fallback branch in `get_huey_sqlite_path()` is reachable
  only when an app context exists (tests, tooling); it is intentionally
  **not** a boot-time fallback.

## Invariants (for implementers and coding agents)

1. Do not derive the queue path from `cwd`, `__file__`, or package location.
2. Do not make `extensions.huey` lazily created without a new ADR.
3. The production boot matrix is pinned by the subprocess tests in
   `tests/test_huey_config.py` (web `wsgi.py` boot, worker `load_huey` boot,
   shared-queue-path equality, fail-fast without a path). The in-process suite
   runs the `memory` backend and cannot exercise any of this — those
   subprocess tests are the sqlite-path regression gate. Keep them passing.

## Related

| Path | Role |
|------|------|
| `poi_broker/huey_config.py` | Path resolution + fail-fast |
| `poi_broker/extensions.py` | Import-time `create_huey()` |
| `poi_broker/worker.py` | Consumer entrypoint |
| `tests/test_huey_config.py` | Prod boot-matrix subprocess tests |
| `docs/async_export/adr_huey_sqlite.md` | Queue backend decision |
| `docs/async_export/ubuntu_add_worker.md` | Operator setup + env vars |
