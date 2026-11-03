"""
Huey configuration for both development and production environments.

This module provides a flexible Huey setup that supports:
- Development: MemoryHuey with immediate execution (synchronous, no external dependencies)
- Production: SqliteHuey with persistent queue (async, requires huey_consumer worker)

Usage:
    from poi_broker.huey_config import get_huey
    huey = get_huey()

Environment Variables:
    HUEY_BACKEND: 'memory' (default) or 'sqlite' for production
    HUEY_SQLITE_PATH: Absolute path to huey.db (required when there is no Flask
                      app context, e.g. the huey_consumer worker)
    HUEY_IMMEDIATE: 'true' or 'false' - run tasks synchronously (for development)

The production worker is started via ``poi_broker.worker.huey`` (imports tasks).
"""

import os
from pathlib import Path


def get_huey_backend():
    """
    Determine which Huey backend to use based on environment.
    
    Returns:
        str: 'memory' (development) or 'sqlite' (production)
    """
    backend = os.environ.get('HUEY_BACKEND', 'memory').lower()
    if backend not in ('memory', 'sqlite'):
        raise ValueError(f"Invalid HUEY_BACKEND: {backend}. Must be 'memory' or 'sqlite'")
    return backend


def get_huey_sqlite_path():
    """
    Get the SQLite database path for Huey.
    
    Returns:
        str: Absolute path to huey.db
    
    The resolved path is normalised to an absolute path so the web app and the
    worker always reference the same queue database regardless of their working
    directory. In production it is strongly recommended (and in worker mode
    required) to set ``HUEY_SQLITE_PATH`` explicitly so both processes share one
    queue.
    """
    # First check if explicitly provided
    explicit_path = os.environ.get('HUEY_SQLITE_PATH')
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if not path.is_absolute():
            raise ValueError(
                'HUEY_SQLITE_PATH must be an absolute path shared by the '
                'web app and worker.'
            )
        return str(path.resolve())

    # No explicit path: try the Flask app's instance folder first.
    try:
        from flask import current_app
        instance_path = current_app.instance_path
    except (RuntimeError, ImportError):
        # No Flask app context (e.g., in the huey_consumer worker). Do NOT
        # silently derive the path from cwd here: the worker typically runs from
        # a different directory than the Flask app, which would create a second,
        # empty queue database and silently drop tasks. Require an explicit path.
        raise RuntimeError(
            "HUEY_SQLITE_PATH must be set explicitly when there is no Flask app "
            "context (e.g. the huey_consumer worker). Set it to the same absolute "
            "path used by the Flask app so both processes share one task queue."
        )

    Path(instance_path).mkdir(parents=True, exist_ok=True)
    return str(Path(instance_path) / 'huey.db')


def create_huey():
    """
    Create and configure Huey instance based on environment.

    Returns:
        Huey: Configured Huey instance (MemoryHuey or SqliteHuey)

    Raises:
        RuntimeError: If HUEY_BACKEND=sqlite and HUEY_SQLITE_PATH is unset
            outside a Flask app context. Set it explicitly (same absolute
            path for web app and worker).
    """
    backend_type = get_huey_backend()
    
    if backend_type == 'sqlite':
        from huey import SqliteHuey
        db_path = get_huey_sqlite_path()
        return SqliteHuey(
            'poi_broker',
            filename=db_path,
            journal_mode='wal',
            timeout=5,
            cache_mb=8,
            # fsync=False is passed EXPLICITLY, and must stay explicit: it is
            # also huey's own default, and huey 3.x applies
            # `pragma synchronous=(2 if fsync else 0)` unconditionally, so
            # omitting this kwarg silently leaves the queue at
            # PRAGMA synchronous=OFF. Do not "clean up" this argument.
            #
            # Consequence, stated plainly: huey.db runs WITHOUT power-loss
            # corruption guarantees. A power loss or hard reset can lose
            # already-committed enqueues and can corrupt the WAL; the
            # documented recovery for a corrupt queue is deleting it.
            #
            # Why that is tolerable here: the queue is a work queue, not the
            # system of record. The durable intent for every export is the
            # ExportTask row in users.db, which stays PENDING if its enqueue is
            # lost, and the consumer's stale-task guard fails it after
            # EXPORT_STALE_MAX_AGE_SECONDS so the user can retry. Losing a
            # queue commit costs a replay, not data.
            #
            # Switching to fsync=True (PRAGMA synchronous=FULL) is a real
            # behavior change and is deliberately NOT done here: it needs
            # measured commit latency on the production queue plus the deployed
            # systemd unit config, neither of which is verifiable from the
            # source tree. See the "Queue durability" revisit trigger in
            # docs/async_export/adr_huey_sqlite.md.
            fsync=False,
            utc=True
        )
    else:
        # Default: MemoryHuey for development
        from huey import MemoryHuey
        immediate = os.environ.get('HUEY_IMMEDIATE', 'true').lower() == 'true'
        return MemoryHuey('poi_broker', immediate=immediate)
