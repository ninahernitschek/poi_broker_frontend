"""
Background tasks for data export using Huey.
"""

import csv
import logging
import os
import threading
import time
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from huey import crontab

from . import create_app, db
from .extensions import huey
from .models import ExportTask
from .services.query_service import (
    EXPORT_YIELD_PER,
    build_export_query_from_rules,
    build_export_row,
    get_export_columns,
)

logger = logging.getLogger(__name__)

# One Flask app per Huey consumer process. Avoid create_app() per task (new
# engines + SQLite PRAGMA cache/mmap on every connect). Lazy so worker.py can
# import tasks without SECRET_KEY / DB paths at module load.
_worker_app = None
_worker_app_lock = threading.Lock()


def _get_worker_app():
    """Return the process-wide Flask app for Huey task handlers."""
    global _worker_app
    if _worker_app is None:
        with _worker_app_lock:
            if _worker_app is None:
                _worker_app = create_app()
    return _worker_app


def _env_int(name: str, default: int) -> int:
    """Read an integer env var, falling back to ``default`` on missing/invalid."""
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Seconds a task may stay PENDING/RUNNING before it is considered abandoned.
STALE_TASK_MAX_AGE = _env_int('EXPORT_STALE_MAX_AGE_SECONDS', 30 * 60)

# Exports (CSV files + ExportTask rows) older than this are removed by the
# periodic housekeeping task.
EXPORT_RETENTION_DAYS = _env_int('EXPORT_RETENTION_DAYS', 10)

# How often a live RUNNING export refreshes updated_at so the stale-task guard
# does not false-fail a healthy long (e.g. 1M-row) export.
EXPORT_HEARTBEAT_SECONDS = _env_int('EXPORT_HEARTBEAT_SECONDS', 60)


def _is_sqlite_lock_error(exc: BaseException) -> bool:
    """True when ``exc`` is a SQLite busy/locked OperationalError (any nesting)."""
    import sqlite3

    from sqlalchemy.exc import OperationalError as SAOperationalError

    cur: BaseException | None = exc
    while cur is not None:
        if isinstance(cur, (sqlite3.OperationalError, SAOperationalError)):
            msg = str(cur).lower()
            if 'locked' in msg or 'busy' in msg:
                return True
        cur = cur.__cause__ or cur.__context__
    return False


def _transition_export_task(task_id: int, from_statuses: Sequence[str], **values) -> bool:
    """
    Compare-and-swap update for ExportTask.

    Only updates when ``id == task_id`` and ``status IN from_statuses``.
    Always sets ``updated_at`` (Query.update does not fire ORM onupdate).

    Returns:
        True if exactly one row was updated.
        False only when the UPDATE committed and matched 0 rows (CAS miss).

    Raises:
        OperationalError (and other DB errors) after one lock retry — never
        collapses a lock into False (callers treat False as "already terminal").
    """
    values = dict(values)
    values['updated_at'] = datetime.now(timezone.utc)

    for attempt in range(2):
        try:
            n = ExportTask.query.filter(
                ExportTask.id == task_id,
                ExportTask.status.in_(from_statuses),
            ).update(values, synchronize_session=False)
            db.session.commit()
            return n == 1
        except Exception as exc:
            db.session.rollback()
            if _is_sqlite_lock_error(exc) and attempt == 0:
                logger.warning(
                    'ExportTask %s transition locked; retrying once', task_id
                )
                time.sleep(0.05)
                continue
            logger.exception(f'Failed to transition ExportTask {task_id}')
            raise
    # range(2) always returns or raises on the last attempt.
    raise RuntimeError('unreachable')  # pragma: no cover


def _unlink_export_file(file_path: Path | None) -> None:
    """Best-effort removal of a partial or abandoned export CSV."""
    if file_path is None:
        return
    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        logger.warning(f'Could not remove export file {file_path}')


@huey.task()
def create_export_file(query_params: dict, user_id: int, task_id: int):
    """
    Background task to export data to CSV.
    
    Args:
        query_params: Dictionary with 'rules' key containing filter rules
        user_id: User ID for the export task
        task_id: Integer ID of the ExportTask record
    
        Updates:
            ExportTask status from PENDING to RUNNING then to SUCCESS/FAILED
            Sets file_path as soon as the output file is created (so killed
            workers leave a reclaimable row); error_message on FAILED

    Status writes use compare-and-swap so a stale-guard FAILED cannot be
    overwritten back to RUNNING/SUCCESS by a still-running worker. A periodic
    heartbeat refreshes ``updated_at`` while writing so long exports are not
    false-failed.
    """
    app = _get_worker_app()

    with app.app_context():
        export_task = db.session.get(ExportTask, task_id)
        if not export_task:
            logger.exception(f'ExportTask {task_id} not found')
            return
        
        # Track any partially-written file so we can clean it up on failure.
        file_path: Path | None = None
        
        try:
            # Claim the row: PENDING|RUNNING -> RUNNING. Skip if already terminal
            # (e.g. stale guard FAILED the task before this worker started).
            if not _transition_export_task(
                task_id, ExportTask.ACTIVE_STATUSES, status='RUNNING'
            ):
                logger.info(
                    f'ExportTask {task_id} not claimed (already terminal); aborting'
                )
                return
            logger.info(f'ExportTask {task_id} set to RUNNING')
            
            # Build and execute query (Core columns, not ORM entities).
            # The snapshot cutoff lives on the task row (set at submit time),
            # not in the queue payload, so the CSV is reproducible regardless
            # of queue latency. Legacy rows (pre-snapshot_mjd) have NULL and
            # export without a cutoff, matching their original behavior.
            snapshot_mjd = export_task.snapshot_mjd
            export_query, rules_payload = build_export_query_from_rules(
                query_params, max_alert_mjd=snapshot_mjd
            )
            logger.info(f'ExportTask {task_id} query rules: {rules_payload}')
            
            # Create exports directory in instance path
            exports_dir = Path(app.instance_path) / 'exports'
            exports_dir.mkdir(parents=True, exist_ok=True)
            
            # Task identity prevents sequential same-second exports colliding.
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            filename = f'export_{user_id}_{task_id}_{timestamp}.csv'
            file_path = exports_dir / filename

            # Persist the path before writing: a worker killed mid-write then
            # leaves a FAILED row (via the stale guard) that still names the
            # partial CSV, so cleanup_expired_exports can reclaim the file.
            # A NULL path would orphan it forever. CAS miss = stale guard
            # already FAILED the task. A lock error is tolerated like the
            # heartbeat below: the SUCCESS transition re-records file_path,
            # and the stale window remains the backstop.
            try:
                if not _transition_export_task(
                    task_id, ['RUNNING'], status='RUNNING', file_path=str(file_path)
                ):
                    logger.info(
                        f'ExportTask {task_id} aborted before write '
                        '(status no longer RUNNING)'
                    )
                    _unlink_export_file(file_path)
                    return
            except Exception as persist_exc:
                if not _is_sqlite_lock_error(persist_exc):
                    raise
                logger.warning(
                    'ExportTask %s file_path persist skipped (DB locked); '
                    'continuing write',
                    task_id,
                )

            # Write CSV file
            all_columns = get_export_columns()

            row_count = 0
            last_heartbeat = time.monotonic()
            with open(file_path, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=all_columns)
                writer.writeheader()

                # Stream in batches instead of .all(): keeps ORM-less Row
                # construction bounded. The Python sqlite3 driver still buffers
                # the raw DBAPI result; yield_per does not make SQLite a true
                # server-side cursor.
                for row in export_query.yield_per(EXPORT_YIELD_PER):
                    writer.writerow(build_export_row(row))
                    row_count += 1

                    now = time.monotonic()
                    if now - last_heartbeat >= EXPORT_HEARTBEAT_SECONDS:
                        # Heartbeat: RUNNING -> RUNNING (updated_at only).
                        # False = CAS miss (stale guard already FAILED).
                        # Lock after retry raises; keep writing — stale window
                        # is the backstop if updated_at cannot refresh.
                        try:
                            if not _transition_export_task(
                                task_id, ['RUNNING'], status='RUNNING'
                            ):
                                logger.info(
                                    f'ExportTask {task_id} aborted mid-write '
                                    '(status no longer RUNNING)'
                                )
                                _unlink_export_file(file_path)
                                return
                        except Exception as heartbeat_exc:
                            if not _is_sqlite_lock_error(heartbeat_exc):
                                raise
                            logger.warning(
                                'ExportTask %s heartbeat skipped (DB locked); '
                                'continuing write',
                                task_id,
                            )
                        last_heartbeat = now
            
            logger.info(f'CSV file created at {file_path} with {row_count} rows')
            
            # RUNNING -> SUCCESS. Miss: leave FAILED, discard the CSV.
            if not _transition_export_task(
                task_id,
                ['RUNNING'],
                status='SUCCESS',
                file_path=str(file_path),
            ):
                logger.info(
                    f'ExportTask {task_id} finished writing but could not '
                    'transition to SUCCESS (already terminal); discarding file'
                )
                _unlink_export_file(file_path)
                return
            logger.info(f'ExportTask {task_id} completed successfully')
            
        except Exception:
            logger.exception(f'ExportTask {task_id} failed with an error.')
            # Remove any partially-written CSV now; the FAILED row keeps
            # file_path and cleanup_expired_exports deletes the row once it
            # passes the retention age.
            _unlink_export_file(file_path)
            # PENDING|RUNNING -> FAILED only; do not clobber a terminal row.
            try:
                _transition_export_task(
                    task_id,
                    ExportTask.ACTIVE_STATUSES,
                    status='FAILED',
                    error_message='Export failed. Please try again.',
                )
            except Exception:
                logger.exception(
                    'ExportTask %s could not be marked FAILED after error',
                    task_id,
                )


def reset_stale_export_tasks(
    max_age_seconds: int = STALE_TASK_MAX_AGE,
    *,
    ignore_age: bool = False,
    user_id: int | None = None,
) -> int:
    """
    Fail export tasks that are stuck in PENDING or RUNNING for too long.

    A crashed or restarted worker leaves tasks in PENDING/RUNNING forever, which
    permanently blocks the user's active-task slot (the API returns 409 while one
    exists). This resets such tasks to FAILED so the user can retry.

    The reset is a single compare-and-swap UPDATE (same pattern as
    ``_transition_export_task``), so overlapping runs of the consumer's periodic
    ``cleanup_stale_export_tasks`` cannot tear each other's writes: the first
    writer to match a row wins, the rest match zero rows and return 0.

    ``ignore_age=True`` fails every PENDING/RUNNING row regardless of
    ``updated_at``. That is the local unstick path (Flask CLI
    ``fail-stale-exports --force``) when there is no Huey consumer.

    ``user_id`` scopes the UPDATE to one user. ``POST /export`` uses that so a
    retry can free a stale slot without touching other users' rows. The consumer
    periodic task and the CLI omit it (global cleanup).

    Returns:
        int: number of tasks reset to FAILED.
    """
    from .models import ExportTask

    filters = [ExportTask.status.in_(ExportTask.ACTIVE_STATUSES)]
    if user_id is not None:
        filters.append(ExportTask.user_id == user_id)
    if not ignore_age:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        filters.append(ExportTask.updated_at < cutoff)
    if ignore_age:
        error_message = 'Export aborted. Please try again.'
    else:
        error_message = (
            f"Export aborted: no progress for over {max_age_seconds // 60} minute(s). "
            "Please try again."
        )
    try:
        n = ExportTask.query.filter(*filters).update(
            {
                'status': 'FAILED',
                'error_message': error_message,
                'updated_at': datetime.now(timezone.utc),
            },
            synchronize_session=False,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('Failed to commit stale export task resets')
        return 0
    if n:
        logger.info(f'Marked {n} stale export task(s) as FAILED')
    return int(n or 0)


def cleanup_expired_exports(max_age_days: int = EXPORT_RETENTION_DAYS) -> int:
    """
    Remove exports older than ``max_age_days``: delete the CSV file, then the
    ExportTask DB row. Files are removed first so a crash mid-cleanup cannot
    leave a DB row pointing at a missing file (the download route already guards
    against missing files, but this keeps the DB consistent).

    Covers both SUCCESS exports and FAILED rows: the latter may carry a
    file_path persisted before a worker died mid-write, whose partial CSV
    would otherwise never be reclaimed.

    Returns:
        int: number of exports removed.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    from .models import ExportTask

    expired = ExportTask.query.filter(
        ExportTask.created_at < cutoff,
        ExportTask.status.in_(('SUCCESS', 'FAILED')),
    ).all()

    removed = 0
    for task in expired:
        # 1) Remove the CSV file first (best-effort).
        if task.file_path:
            try:
                Path(task.file_path).unlink(missing_ok=True)
            except OSError:
                logger.warning(f'Could not remove export file {task.file_path}')
        # 2) Remove the DB row.
        db.session.delete(task)
        removed += 1

    if removed:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception('Failed to commit expired export cleanup')
            return 0
        logger.info(f'Removed {removed} expired export(s) older than {max_age_days} day(s)')
    return removed


@huey.periodic_task(crontab(minute='*/5'))
def cleanup_stale_export_tasks() -> int:
    """
    Periodic housekeeping (every 5 minutes), owned by the Huey consumer:
      - fail stale PENDING/RUNNING exports (reset_stale_export_tasks)
      - remove exports older than EXPORT_RETENTION_DAYS (cleanup_expired_exports)

    Web-app startup does not run this. Periodic scheduling is on by default in
    huey_consumer; do not start the consumer with ``--no-periodic``.
    Memory/development mode only executes this when a consumer is running.
    """
    app = _get_worker_app()
    with app.app_context():
        reset_stale_export_tasks()
        return cleanup_expired_exports()


@huey.periodic_task(crontab(hour='*/6'))
def prune_huey_results() -> int:
    """
    Periodic housekeeping for the Huey SQLite queue (every 6 hours).

    Huey stores task results in the ``taskresult`` table and never prunes them
    by default, so ``huey.db`` grows over time. This flushes stored results
    (and any stale schedule entries) to bound the queue database size.

    Safe because export status lives in ``ExportTask``, not Huey results.
    Do not add tasks that call ``Result.get()`` without changing this prune.

    Returns:
        int: number of result rows flushed.
    """
    try:
        flushed = huey.storage.flush_results()
        # Also drop stale periodic schedule entries to keep the schedule table small.
        huey.storage.flush_schedule()
        logger.info(f'Pruned {flushed} huey result(s)')
        return int(flushed or 0)
    except Exception:
        logger.exception('Failed to prune huey results')
        return 0
