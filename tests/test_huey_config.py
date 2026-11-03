"""Huey backend configuration regressions."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('path', ['huey.db', './instance/huey.db', '../huey.db'])
def test_sqlite_backend_rejects_relative_queue_paths(tmp_path, monkeypatch, path):
    from poi_broker.huey_config import create_huey

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('HUEY_BACKEND', 'sqlite')
    monkeypatch.setenv('HUEY_SQLITE_PATH', path)
    with pytest.raises(ValueError, match='absolute path'):
        create_huey()
    assert not list(tmp_path.rglob('huey.db'))


def test_absolute_queue_path_is_shared_across_working_directories(tmp_path, monkeypatch):
    from poi_broker.huey_config import create_huey

    web_dir = tmp_path / 'web'
    worker_dir = tmp_path / 'worker'
    web_dir.mkdir()
    worker_dir.mkdir()
    monkeypatch.setenv('HUEY_BACKEND', 'sqlite')
    monkeypatch.setenv('HUEY_SQLITE_PATH', str(tmp_path / 'shared.db'))
    monkeypatch.chdir(web_dir)
    sender = create_huey()
    sender.storage.enqueue(b'test-task')
    monkeypatch.chdir(worker_dir)
    consumer = create_huey()
    assert consumer.storage.dequeue() == b'test-task'


def test_sqlite_queue_runs_at_documented_sync_level(tmp_path, monkeypatch):
    """Pin the queue's real durability state AND the docs that describe it.

    History: a 2026-09-27 entry claimed the queue "no longer runs with
    synchronous=OFF" because the explicit ``fsync=False`` argument had been
    deleted from ``create_huey()``. That was a no-op — ``fsync=False`` is also
    huey's own default, and huey 3.x applies
    ``pragma synchronous=(2 if fsync else 0)`` unconditionally. Four doc sites
    were written to match the code comment instead of the runtime behaviour and
    claimed ``synchronous=FULL``/ACID. So this test asserts both halves: the
    pragma huey really uses, and the absence of the false durability claim.
    """
    from huey.storage import SqliteStorage

    from poi_broker.huey_config import create_huey

    monkeypatch.setenv('HUEY_BACKEND', 'sqlite')
    monkeypatch.setenv('HUEY_SQLITE_PATH', str(tmp_path / 'huey.db'))

    queue = create_huey()

    assert isinstance(queue.storage, SqliteStorage)

    # fsync=False is deliberate and explicitly passed (see huey_config.py).
    assert queue.storage._fsync is False

    # Read the pragma from a connection huey itself opens. `synchronous` is a
    # per-connection setting and is NOT stored in the database file: a fresh
    # sqlite3.connect() reports SQLite's compile-time default (2/FULL), which
    # would make this assertion pass no matter what huey does.
    conn = queue.storage._create_connection()
    try:
        assert conn.execute('pragma synchronous').fetchone()[0] == 0
    finally:
        conn.close()

    # Docs must not claim the durability the code does not provide.
    adr = REPO_ROOT / 'docs' / 'async_export' / 'adr_huey_sqlite.md'
    adr_text = adr.read_text(encoding='utf-8')
    assert 'synchronous=OFF' in adr_text, 'ADR must state the real pragma'
    assert 'synchronous=FULL`, which is ACID' not in adr_text, (
        'ADR must not claim the queue is ACID / synchronous=FULL'
    )


def test_sqlite_backend_requires_explicit_path_outside_app_context(monkeypatch):
    from poi_broker.huey_config import create_huey

    monkeypatch.setenv('HUEY_BACKEND', 'sqlite')
    monkeypatch.delenv('HUEY_SQLITE_PATH', raising=False)

    try:
        create_huey()
    except RuntimeError as exc:
        assert 'HUEY_SQLITE_PATH' in str(exc)
    else:
        # An app context exists under the test app; the instance-folder path is
        # then legitimate, so only assert fail-fast when there is no context.
        from flask import has_app_context

        assert has_app_context()


# --- Production boot matrix (auditSBA F2) -----------------------------------
#
# The suite runs every app under HUEY_BACKEND=memory, so the sqlite boot path
# that the Linux/systemd processes actually use is never exercised in-process
# (and the pytest process env even carries HUEY_BACKEND=memory, set by
# test_huey_tasks at import). These tests spawn clean interpreters with the
# env the systemd EnvironmentFiles set (docs/async_export/ubuntu_add_worker.md)
# and boot the real entrypoints:
#   web    -> gunicorn imports wsgi.py: import poi_broker + create_app()
#   worker -> huey_consumer load_huey('poi_broker.worker.huey')
# Everything involved (import-time create_huey, path resolution, SqliteHuey,
# WAL) is cross-platform Python, so this runs on Windows dev and Linux CI
# alike. The systemd/gunicorn layer itself stays covered by the runbook's
# Verify section on the server.

_PROD_ENV_VARS = {
    'HUEY_BACKEND': 'sqlite',
    'SECRET_KEY': 'prod-boot-matrix-test',
}


def _prod_boot_env(tmp_path, *, with_queue_path: bool) -> dict:
    """Env dict mirroring the systemd EnvironmentFiles for both units."""
    env = {
        **os.environ,
        **_PROD_ENV_VARS,
        'ALERTS_DB_PATH': str(tmp_path / 'alerts.db'),
        'USERS_DB_PATH': str(tmp_path / 'users.db'),
        'PYTHONPATH': str(REPO_ROOT),
    }
    if with_queue_path:
        env['HUEY_SQLITE_PATH'] = str(tmp_path / 'huey.db')
    else:
        env.pop('HUEY_SQLITE_PATH', None)
    # Dev-only setting; prod units never set it.
    env.pop('HUEY_IMMEDIATE', None)
    # Without this the child boots the real logging setup and appends to
    # <workspace root>/app.log, which is a TRACKED file — every test run would
    # dirty the tree and `git add -A` would commit log noise. conftest.py sets
    # FLASK_TESTING for in-process tests; these children need it explicitly.
    env['FLASK_TESTING'] = '1'
    return env


def _run_python(code: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, '-c', code],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_web_process_boots_with_sqlite_backend(tmp_path):
    """wsgi.py-faithful web boot: gunicorn imports wsgi, which imports
    poi_broker (creating the huey queue at import time, with no app context)
    and then calls create_app()."""
    code = (
        'from poi_broker import create_app\n'
        'app = create_app()\n'
        'from poi_broker.extensions import huey\n'
        'from poi_broker.huey_config import get_huey_sqlite_path\n'
        "print('BOOT-OK', type(huey).__name__, get_huey_sqlite_path())\n"
    )

    result = _run_python(code, _prod_boot_env(tmp_path, with_queue_path=True))

    assert result.returncode == 0, result.stderr
    expected_path = str((tmp_path / 'huey.db').resolve())
    assert f'BOOT-OK SqliteHuey {expected_path}' in result.stdout
    # SqliteHuey creates the queue file eagerly at construction.
    assert (tmp_path / 'huey.db').exists()


def test_worker_entrypoint_boots_with_sqlite_backend(tmp_path):
    """huey_consumer-faithful worker boot: load_huey is the exact loader the
    consumer binary applies to poi_broker.worker.huey (systemd ExecStart:
    python -m huey.bin.huey_consumer poi_broker.worker.huey ...)."""
    code = (
        'from huey.bin.huey_consumer import load_huey\n'
        "huey = load_huey('poi_broker.worker.huey')\n"
        'from poi_broker.huey_config import get_huey_sqlite_path\n'
        "print('WORKER-OK', type(huey).__name__, get_huey_sqlite_path())\n"
    )

    result = _run_python(code, _prod_boot_env(tmp_path, with_queue_path=True))

    assert result.returncode == 0, result.stderr
    expected_path = str((tmp_path / 'huey.db').resolve())
    assert f'WORKER-OK SqliteHuey {expected_path}' in result.stdout
    assert (tmp_path / 'huey.db').exists()


def test_web_and_worker_resolve_same_queue_path(tmp_path):
    """Under the prod env, web and worker must resolve ONE shared queue file:
    a second, empty queue (e.g. from a cwd-derived fallback) silently drops
    tasks."""
    web_code = (
        'import poi_broker.extensions\n'
        'from poi_broker.huey_config import get_huey_sqlite_path\n'
        "print(get_huey_sqlite_path())\n"
    )
    worker_code = (
        'import poi_broker.worker\n'
        'from poi_broker.huey_config import get_huey_sqlite_path\n'
        "print(get_huey_sqlite_path())\n"
    )
    env = _prod_boot_env(tmp_path, with_queue_path=True)

    web = _run_python(web_code, env)
    worker = _run_python(worker_code, env)

    assert web.returncode == 0, web.stderr
    assert worker.returncode == 0, worker.stderr
    assert web.stdout.strip().splitlines()[-1] == worker.stdout.strip().splitlines()[-1]


def test_sqlite_without_path_fails_fast_like_prod(tmp_path):
    """With HUEY_BACKEND=sqlite but no HUEY_SQLITE_PATH, both prod processes
    must crash at import with the documented error instead of silently
    creating a second, empty queue; under systemd Restart=always they then
    loop with a clear log line until the config is fixed."""
    env = _prod_boot_env(tmp_path, with_queue_path=False)

    for label, code in (('web', 'import poi_broker\n'), ('worker', 'import poi_broker.worker\n')):
        result = _run_python(code, env)
        assert result.returncode != 0, f'{label} boot unexpectedly succeeded'
        assert 'HUEY_SQLITE_PATH must be set explicitly' in result.stderr, result.stderr
