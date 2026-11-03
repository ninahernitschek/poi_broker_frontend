"""
Integration test for Huey task queue in both development and production modes.

This test demonstrates:
1. Development mode (MemoryHuey, immediate=True, synchronous)
2. Production mode (SqliteHuey, async with background worker)

Usage:
    # Test development mode (default)
    python -m pytest tests/test_huey_tasks.py -v
    
    # Test production mode with SQLite backend
    export HUEY_BACKEND=sqlite
    export HUEY_SQLITE_PATH=./test_huey.db
    python -m pytest tests/test_huey_tasks.py -v
"""

import os
from datetime import datetime, timezone

import pytest

# Set test environment before imports
os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('HUEY_BACKEND', 'memory')


def test_huey_imports():
    """Test that Huey can be imported and configured."""
    from poi_broker.huey_config import create_huey, get_huey_backend
    
    backend = get_huey_backend()
    assert backend in ('memory', 'sqlite'), f"Invalid backend: {backend}"
    
    huey = create_huey()
    assert huey is not None, "Failed to create Huey instance"


def test_app_with_memory_huey(tmp_path):
    """Test app creation with MemoryHuey backend (development mode)."""
    os.environ['HUEY_BACKEND'] = 'memory'
    
    # Need to reimport to pick up new environment variable
    import importlib

    import poi_broker.extensions
    import poi_broker.huey_config
    importlib.reload(poi_broker.huey_config)
    importlib.reload(poi_broker.extensions)
    
    from poi_broker import create_app
    app = create_app()
    
    assert app is not None
    with app.app_context():
        from poi_broker.extensions import huey
        assert hasattr(huey, 'immediate'), "MemoryHuey should have 'immediate' attribute"


def test_app_with_sqlite_huey(tmp_path, monkeypatch):
    """Test SqliteHuey backend configuration through create_huey()."""
    # Regression: the sqlite branch previously passed utc_time=True (invalid) and
    # crashed at import. This test goes through create_huey() so the configured
    # path (huey_config) is exercised, not just a hand-built SqliteHuey.
    monkeypatch.setenv('HUEY_BACKEND', 'sqlite')
    monkeypatch.setenv('HUEY_SQLITE_PATH', str(tmp_path / 'test_huey.db'))

    from poi_broker.huey_config import create_huey
    test_huey = create_huey()

    assert test_huey is not None
    assert test_huey.name == 'poi_broker'
    # Database directory should exist (parent of db_path)
    assert (tmp_path / 'test_huey.db').parent.exists()
    # utc should be enabled at the Huey level
    assert test_huey.utc is True


def test_create_huey_requires_sqlite_path_without_app_context(tmp_path, monkeypatch):
    """Regression: worker (no Flask context) must not silently fall back to cwd."""
    monkeypatch.setenv('HUEY_BACKEND', 'sqlite')
    monkeypatch.delenv('HUEY_SQLITE_PATH', raising=False)

    from poi_broker.huey_config import get_huey_sqlite_path
    with pytest.raises(RuntimeError):
        get_huey_sqlite_path()


def test_worker_entrypoint_registers_tasks():
    """Regression: poi_broker.worker.huey must register create_export_file.

    huey_consumer only imports the module of the object passed to it. If the
    consumer targets poi_broker.extensions.huey, tasks are never imported and the
    worker runs but never executes anything. The worker entrypoint must import
    poi_broker.tasks so the registry is populated.

    Sibling tests reload `poi_broker.extensions`, which creates a *new* Huey
    instance whose registry is empty until tasks are re-registered against it.
    We re-import tasks + worker here so the assertion reflects the current shared
    instance rather than a stale one captured at collection time.
    """
    import importlib

    import poi_broker.extensions
    import poi_broker.tasks
    import poi_broker.worker

    # Reload extensions first so the registry is fresh (sibling tests may have
    # created a new Huey instance whose registry still holds the old task).
    importlib.reload(poi_broker.extensions)
    # Re-import so tasks register against the *current* shared Huey instance.
    importlib.reload(poi_broker.tasks)
    importlib.reload(poi_broker.worker)

    huey = poi_broker.extensions.huey
    # worker must be backed by the same instance as extensions
    assert poi_broker.worker.huey is huey
    task = huey._registry.string_to_task('poi_broker.tasks.create_export_file')
    assert task is not None, (
        "create_export_file not registered on the worker's Huey instance"
    )


def test_reset_stale_export_tasks(app):
    """Stale PENDING/RUNNING exports are reset to FAILED."""
    from datetime import timedelta

    from poi_broker import db
    from poi_broker.models import ExportTask, User
    from poi_broker.tasks import reset_stale_export_tasks

    with app.app_context():
        ExportTask.query.delete()
        User.query.filter_by(email='stale@example.com').delete()
        User.query.filter_by(email='stale2@example.com').delete()
        User.query.filter_by(email='fresh@example.com').delete()
        db.session.commit()

        user1 = User(email='stale@example.com', password='hashed', name='Stale', email_verified=True)
        user2 = User(email='stale2@example.com', password='hashed', name='Stale2', email_verified=True)
        user3 = User(email='fresh@example.com', password='hashed', name='Fresh', email_verified=True)
        db.session.add_all([user1, user2, user3])
        db.session.commit()

        def _make(user, status, age_seconds):
            t = ExportTask(user_id=user.id, status=status)
            t.updated_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
            db.session.add(t)
            return t

        stale_pending = _make(user1, 'PENDING', 60 * 60)   # 1h old -> stale
        stale_running = _make(user2, 'RUNNING', 60 * 60)   # 1h old -> stale
        fresh_pending = _make(user3, 'PENDING', 60)        # 1m old -> not stale
        done = _make(user1, 'SUCCESS', 60 * 60)            # old but terminal -> untouched
        db.session.commit()

        n = reset_stale_export_tasks(max_age_seconds=30 * 60)
        db.session.refresh(stale_pending)
        db.session.refresh(stale_running)
        db.session.refresh(fresh_pending)
        db.session.refresh(done)

        assert n == 2
        assert stale_pending.status == 'FAILED'
        assert stale_running.status == 'FAILED'
        assert fresh_pending.status == 'PENDING'
        assert done.status == 'SUCCESS'

        ExportTask.query.delete()
        User.query.delete()
        db.session.commit()


def test_reset_stale_export_tasks_user_id_scopes(app):
    """user_id= fails only that user's stale row (POST /export path)."""
    from datetime import timedelta

    from poi_broker import db
    from poi_broker.models import ExportTask, User
    from poi_broker.tasks import reset_stale_export_tasks

    with app.app_context():
        ExportTask.query.delete()
        User.query.filter_by(email='scope1@example.com').delete()
        User.query.filter_by(email='scope2@example.com').delete()
        db.session.commit()

        user1 = User(
            email='scope1@example.com', password='hashed', name='S1', email_verified=True
        )
        user2 = User(
            email='scope2@example.com', password='hashed', name='S2', email_verified=True
        )
        db.session.add_all([user1, user2])
        db.session.commit()

        old = datetime.now(timezone.utc) - timedelta(hours=1)
        t1 = ExportTask(user_id=user1.id, status='PENDING')
        t2 = ExportTask(user_id=user2.id, status='PENDING')
        t1.updated_at = old
        t2.updated_at = old
        db.session.add_all([t1, t2])
        db.session.commit()

        n = reset_stale_export_tasks(user_id=user1.id)
        db.session.refresh(t1)
        db.session.refresh(t2)

        assert n == 1
        assert t1.status == 'FAILED'
        assert t2.status == 'PENDING'

        ExportTask.query.delete()
        User.query.delete()
        db.session.commit()


def test_reset_stale_export_tasks_ignore_age(app):
    """ignore_age=True fails a fresh PENDING row (local --force unstick)."""
    from poi_broker import db
    from poi_broker.models import ExportTask, User
    from poi_broker.tasks import reset_stale_export_tasks

    with app.app_context():
        ExportTask.query.delete()
        User.query.filter_by(email='force@example.com').delete()
        db.session.commit()

        user = User(
            email='force@example.com', password='hashed', name='Force', email_verified=True
        )
        db.session.add(user)
        db.session.commit()

        fresh = ExportTask(user_id=user.id, status='PENDING')
        db.session.add(fresh)
        db.session.commit()

        n = reset_stale_export_tasks(ignore_age=True)
        db.session.refresh(fresh)
        assert n == 1
        assert fresh.status == 'FAILED'

        ExportTask.query.delete()
        User.query.delete()
        db.session.commit()


def test_fail_stale_exports_cli_force(app):
    """flask fail-stale-exports --force unsticks a just-created PENDING row."""
    from poi_broker import db
    from poi_broker.models import ExportTask, User

    with app.app_context():
        ExportTask.query.delete()
        User.query.filter_by(email='cli-force@example.com').delete()
        db.session.commit()

        user = User(
            email='cli-force@example.com',
            password='hashed',
            name='CLI Force',
            email_verified=True,
        )
        db.session.add(user)
        db.session.commit()

        fresh = ExportTask(user_id=user.id, status='PENDING')
        db.session.add(fresh)
        db.session.commit()
        task_id = fresh.id

    runner = app.test_cli_runner()
    without_force = runner.invoke(args=['fail-stale-exports'])
    assert without_force.exit_code == 0
    assert 'Marked 0 export task(s) FAILED' in without_force.output

    with app.app_context():
        assert db.session.get(ExportTask, task_id).status == 'PENDING'

    forced = runner.invoke(args=['fail-stale-exports', '--force'])
    assert forced.exit_code == 0
    assert 'Marked 1 export task(s) FAILED' in forced.output

    with app.app_context():
        assert db.session.get(ExportTask, task_id).status == 'FAILED'
        ExportTask.query.delete()
        User.query.delete()
        db.session.commit()


def test_export_task_creation(app):
    """Test creating an ExportTask and enqueuing a background job."""
    from poi_broker import db
    from poi_broker.models import ExportTask, User
    
    with app.app_context():
        # Clean up any existing test data
        ExportTask.query.delete()
        User.query.filter_by(email='test@example.com').delete()
        db.session.commit()
        
        # Create a test user
        test_user = User(
            email='test@example.com',
            password='hashed_password',
            name='Test User',
            email_verified=True
        )
        db.session.add(test_user)
        db.session.commit()
        
        # Create an export task
        task = ExportTask(
            user_id=test_user.id,
            status='PENDING'
        )
        db.session.add(task)
        db.session.commit()
        
        # Verify task was created
        retrieved_task = ExportTask.query.filter_by(id=task.id).first()
        assert retrieved_task is not None
        assert retrieved_task.status == 'PENDING'
        assert retrieved_task.user_id == test_user.id
        
        # Clean up
        ExportTask.query.delete()
        User.query.delete()
        db.session.commit()


def test_huey_task_execution_memory_backend(app):
    """Test Huey task execution in development mode (immediate/synchronous)."""
    from poi_broker.extensions import huey
    
    with app.app_context():
        # Test that tasks can be created and registered with Huey
        
        # Create a simple test task
        @huey.task()
        def test_task(x):
            return x * 2
        
        # Execute the task - it should return immediately in memory mode
        result = test_task(21)
        
        # Verify the task was executed and returned a Result object
        # (even in immediate mode, Huey returns Result objects)
        assert result is not None
        # If immediate mode is enabled, we can get the actual value
        if hasattr(result, 'id'):
            # This is a Result object, which is expected for Huey tasks
            assert result.id is not None
        else:
            # Or it returned the direct value
            assert result == 42


def test_export_routes_exist():
    """Test that export routes are registered."""
    from poi_broker import create_app
    
    app = create_app()
    
    with app.app_context():
        rules = [str(rule) for rule in app.url_map.iter_rules()]
        export_routes = [r for r in rules if 'export' in r]
        
        assert '/export' in export_routes, "GET /export route not found"
        assert '/export/download/<int:task_id>' in export_routes, "Download route not found"
        assert '/export/status/<int:task_id>' in export_routes, "Status route not found"


def test_export_task_status_transitions(app):
    """Test ExportTask status transitions through its lifecycle."""
    from poi_broker import db
    from poi_broker.models import ExportTask, User
    
    with app.app_context():
        # Clean up
        ExportTask.query.delete()
        User.query.filter_by(email='status_test@example.com').delete()
        db.session.commit()
        
        # Create user
        user = User(
            email='status_test@example.com',
            password='hashed',
            name='Status Test',
            email_verified=True
        )
        db.session.add(user)
        db.session.commit()
        
        # Create task and verify status transitions
        task = ExportTask(user_id=user.id, status='PENDING')
        db.session.add(task)
        db.session.commit()
        
        # PENDING -> RUNNING
        task.status = 'RUNNING'
        db.session.commit()
        running = db.session.get(ExportTask, task.id)
        assert running is not None
        assert running.status == 'RUNNING'
        
        # RUNNING -> SUCCESS
        task.status = 'SUCCESS'
        task.file_path = '/tmp/export_123.csv'
        db.session.commit()
        success = db.session.get(ExportTask, task.id)
        assert success is not None
        assert success.status == 'SUCCESS'
        
        # Verify timestamps updated
        assert task.updated_at is not None
        assert task.created_at is not None
        
        # Clean up
        ExportTask.query.delete()
        User.query.delete()
        db.session.commit()


def test_sequential_same_second_exports_keep_separate_files(app, tmp_path, monkeypatch):
    """The active-task slot is released before the next export starts."""
    import csv
    from datetime import datetime, timezone
    from pathlib import Path

    import poi_broker.tasks as tasks_mod
    from poi_broker import db
    from poi_broker.models import ExportTask, User, Ztf

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(tasks_mod, 'datetime', FixedDatetime)
    monkeypatch.setattr(tasks_mod, '_get_worker_app', lambda: app)
    monkeypatch.setattr(app, 'instance_path', str(tmp_path / 'instance'))

    with app.app_context():
        user = User(email='same-second@example.com', password='hashed', email_verified=True)
        db.session.add(user)
        db.session.add_all([
            Ztf(alert_id='export-first', locus_id='L-first', date_alert_mjd=60001.0),
            Ztf(alert_id='export-second', locus_id='L-second', date_alert_mjd=60002.0),
        ])
        db.session.commit()
        user_id = user.id

    paths = []
    for alert_id in ('export-first', 'export-second'):
        with app.app_context():
            task = ExportTask(user_id=user_id, status='PENDING', snapshot_mjd=70000.0)
            db.session.add(task)
            db.session.commit()
            task_id = task.id
        rules = {'condition': 'AND', 'rules': [
            {'field': 'featuretable.alert_id', 'operator': 'equal', 'value': alert_id},
        ]}
        tasks_mod.create_export_file.call_local(rules, user_id, task_id)
        with app.app_context():
            task = db.session.get(ExportTask, task_id)
            assert task.status == 'SUCCESS'
            paths.append(Path(task.file_path))

    assert paths[0] != paths[1]
    for path, expected_alert in zip(paths, ('export-first', 'export-second')):
        with path.open(newline='', encoding='utf-8') as csv_file:
            assert [row['alert_id'] for row in csv.DictReader(csv_file)] == [expected_alert]


class _StubSession:
    """Scoped session stub for unit-testing Huey export task execution seams."""

    def __init__(self, task=None, pk_expected: int = 42):
        self.task = task
        self.pk_expected = pk_expected

    def get(self, model, pk, *a, **kw):
        from poi_broker.models import ExportTask

        if model is ExportTask and pk == self.pk_expected:
            return self.task
        return None

    def commit(self, *a, **kw):
        return None

    def rollback(self, *a, **kw):
        return None

    def remove(self):
        return None


def _failing_export_build(*_a, **_kw):
    """Scaffolding for tests asserting failure handling during the CSV row-writing loop."""
    def _boom_iter():
        raise RuntimeError('simulated write failure')
        yield  # pragma: no cover -- makes this a generator so iteration raises

    class _BoomQuery:
        def yield_per(self, _n):
            return iter(_boom_iter())

    return _BoomQuery(), 'fake where'


def test_create_export_file_cleans_partial_file_on_failure(app, monkeypatch):
    """Regression (#5): a mid-write failure removes the partial CSV file and
    marks the ExportTask FAILED instead of leaving an orphan file on disk.

    We make build_query_from_rules succeed but the row iteration raise, so the
    file has been opened (header written) before the exception — exactly the
    "disk full / row serialization" scenario. The task's error handler must
    unlink the partial file.
    """
    from pathlib import Path

    import poi_broker.tasks as tasks_mod
    from poi_broker.models import ExportTask

    fake_task = ExportTask(user_id=1, status='RUNNING')

    # The task opens its own app context (create_app -> app_context), which uses
    # a fresh scoped Session keyed by app-context id, so patching db.session /
    # ExportTask.query would not reliably intercept the task's writes (Model.query
    # resolves through cls.__fsa__.session(), bypassing a patched db.session
    # attribute). We patch two seams instead:
    #   1. db.session.get -> return fake_task (the task's initial row lookup).
    #   2. _transition_export_task -> mutate fake_task directly (all status
    #      writes go through this single function).
    monkeypatch.setattr(tasks_mod.db, 'session', _StubSession(fake_task))

    def _fake_transition(task_id, from_statuses, **values):
        if task_id != 42:
            return False
        if fake_task.status not in from_statuses:
            return False
        for k, v in values.items():
            setattr(fake_task, k, v)
        return True

    monkeypatch.setattr(tasks_mod, '_transition_export_task', _fake_transition)

    # build_export_query_from_rules succeeds, but iterating the result raises
    # inside the CSV-write loop (after the file is opened and the header
    # written) — exactly the "disk full / row serialization" scenario. The
    # task's error handler must unlink the partial file.
    monkeypatch.setattr(tasks_mod, 'build_export_query_from_rules', _failing_export_build)
    # The task builds its own app via create_app(); use the fixture app so the
    # patched session_factory applies inside the task's new app context.
    monkeypatch.setattr(tasks_mod, 'create_app', lambda: app)

    # Snapshot the exports dir before the run. The fixture app's instance path
    # is the shared repo instance/ dir, which may already contain files from
    # earlier manual exports — so we assert that the task leaves no NEW file.
    exports_dir = Path(app.instance_path) / 'exports'
    exports_dir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in exports_dir.iterdir()}

    # Run the task synchronously (immediate mode). The task calls create_app()
    # which returns the fixture app. We invoke the underlying function directly
    # (not the huey wrapper) so we don't depend on the module's Huey backend
    # mode: at collection time a previous test may have left HUEY_BACKEND=sqlite,
    # making the wrapper enqueue instead of execute.
    tasks_mod.create_export_file.func(
        query_params={'rules': [{'x': 1}]}, user_id=1, task_id=42,
    )

    assert fake_task.status == 'FAILED'
    # The task's error handler writes a generic message (the exception detail
    # goes to the logs, not the DB row).
    assert fake_task.error_message == 'Export failed. Please try again.'

    # No NEW partial CSV may be left behind by the failed task.
    after = {p.name for p in exports_dir.iterdir()}
    assert after == before, f'failed task left a partial file: {after - before}'


def test_create_export_file_persists_file_path_even_on_failure(app, monkeypatch):
    """Regression (auditSBA F18): file_path is persisted to the task row as
    soon as the output file is created, BEFORE the row loop runs.

    A worker hard-killed mid-write cannot run the exception handler, so the
    row is only marked FAILED later by reset_stale_export_tasks. If file_path
    were only persisted on SUCCESS (the old behavior), that FAILED row would
    have a NULL path and cleanup_expired_exports could never reclaim the
    partial CSV.
    """
    from pathlib import Path

    import poi_broker.tasks as tasks_mod
    from poi_broker.models import ExportTask

    fake_task = ExportTask(user_id=1, status='RUNNING')

    monkeypatch.setattr(tasks_mod.db, 'session', _StubSession(fake_task))

    def _fake_transition(task_id, from_statuses, **values):
        if task_id != 42:
            return False
        if fake_task.status not in from_statuses:
            return False
        for k, v in values.items():
            setattr(fake_task, k, v)
        return True

    monkeypatch.setattr(tasks_mod, '_transition_export_task', _fake_transition)
    monkeypatch.setattr(tasks_mod, 'build_export_query_from_rules', _failing_export_build)
    monkeypatch.setattr(tasks_mod, 'create_app', lambda: app)

    exports_dir = Path(app.instance_path) / 'exports'
    exports_dir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in exports_dir.iterdir()}

    tasks_mod.create_export_file.func(
        query_params={'rules': [{'x': 1}]}, user_id=1, task_id=42,
    )

    assert fake_task.status == 'FAILED'
    # The path must be recorded on the row even though the write failed.
    assert fake_task.file_path is not None
    assert fake_task.file_path.endswith('.csv')
    after = {p.name for p in exports_dir.iterdir()}
    assert after == before, f'failed task left a partial file: {after - before}'


def test_cleanup_expired_exports_reclaims_success_task(app):
    """Expired SUCCESS exports are removed: CSV file first, then the row."""
    from datetime import timedelta
    from pathlib import Path

    from poi_broker import db
    from poi_broker.models import ExportTask, User
    from poi_broker.tasks import cleanup_expired_exports

    with app.app_context():
        ExportTask.query.delete()
        User.query.filter_by(email='cleanup-success@example.com').delete()
        db.session.commit()

        user = User(
            email='cleanup-success@example.com',
            password='hashed',
            name='Cleanup Success',
            email_verified=True,
        )
        db.session.add(user)
        db.session.commit()

        exports_dir = Path(app.instance_path) / 'exports'
        exports_dir.mkdir(parents=True, exist_ok=True)
        csv_file = exports_dir / 'export_cleanup_success_test.csv'
        csv_file.write_text('alert_id\nx\n', encoding='utf-8')

        task = ExportTask(user_id=user.id, status='SUCCESS', file_path=str(csv_file))
        task.created_at = datetime.now(timezone.utc) - timedelta(days=11)
        db.session.add(task)
        db.session.commit()
        task_id = task.id

        removed = cleanup_expired_exports()

        assert removed == 1
        assert not csv_file.exists()
        assert db.session.get(ExportTask, task_id) is None

        ExportTask.query.delete()
        User.query.delete()
        db.session.commit()


def test_cleanup_expired_exports_reclaims_failed_task_with_file(app):
    """Regression (auditSBA F18): FAILED rows that carry a file_path (worker
    killed mid-write, then reset by the stale guard) are cleaned up too —
    previously only status == 'SUCCESS' was selected, so the partial CSV
    referenced by a FAILED row was never reclaimed."""
    from datetime import timedelta
    from pathlib import Path

    from poi_broker import db
    from poi_broker.models import ExportTask, User
    from poi_broker.tasks import cleanup_expired_exports

    with app.app_context():
        ExportTask.query.delete()
        User.query.filter_by(email='cleanup-failed@example.com').delete()
        db.session.commit()

        user = User(
            email='cleanup-failed@example.com',
            password='hashed',
            name='Cleanup Failed',
            email_verified=True,
        )
        db.session.add(user)
        db.session.commit()

        exports_dir = Path(app.instance_path) / 'exports'
        exports_dir.mkdir(parents=True, exist_ok=True)
        csv_file = exports_dir / 'export_cleanup_failed_test.csv'
        csv_file.write_text('alert_id\npartial-row\n', encoding='utf-8')

        task = ExportTask(user_id=user.id, status='FAILED', file_path=str(csv_file))
        task.created_at = datetime.now(timezone.utc) - timedelta(days=11)
        db.session.add(task)
        db.session.commit()
        task_id = task.id

        removed = cleanup_expired_exports()

        assert removed == 1
        assert not csv_file.exists()
        assert db.session.get(ExportTask, task_id) is None

        ExportTask.query.delete()
        User.query.delete()
        db.session.commit()


def test_transition_export_task_retries_lock_once(app, monkeypatch):
    """A single SQLite lock error is retried; False is only a CAS miss."""
    import sqlite3

    import poi_broker.tasks as tasks_mod
    from poi_broker import db
    from poi_broker.models import ExportTask, User

    with app.app_context():
        ExportTask.query.delete()
        User.query.filter_by(email='lock-retry@example.com').delete()
        db.session.commit()

        user = User(
            email='lock-retry@example.com',
            password='hashed',
            name='Lock Retry',
            email_verified=True,
        )
        db.session.add(user)
        db.session.commit()

        task = ExportTask(user_id=user.id, status='PENDING')
        db.session.add(task)
        db.session.commit()
        task_id = task.id

        real_commit = db.session.commit
        calls = {'n': 0}

        def _commit_then_ok():
            calls['n'] += 1
            if calls['n'] == 1:
                raise sqlite3.OperationalError('database is locked')
            return real_commit()

        monkeypatch.setattr(db.session, 'commit', _commit_then_ok)
        monkeypatch.setattr(tasks_mod.time, 'sleep', lambda *_a, **_kw: None)

        assert tasks_mod._transition_export_task(
            task_id, ['PENDING'], status='RUNNING'
        ) is True
        assert calls['n'] == 2

        db.session.expire_all()
        assert db.session.get(ExportTask, task_id).status == 'RUNNING'

        ExportTask.query.delete()
        User.query.delete()
        db.session.commit()


def test_transition_export_task_lock_raises_not_false(app, monkeypatch):
    """Persistent lock re-raises; never returns False (False = CAS miss only)."""
    import sqlite3

    import pytest

    import poi_broker.tasks as tasks_mod
    from poi_broker import db
    from poi_broker.models import ExportTask, User

    with app.app_context():
        ExportTask.query.delete()
        User.query.filter_by(email='lock-raise@example.com').delete()
        db.session.commit()

        user = User(
            email='lock-raise@example.com',
            password='hashed',
            name='Lock Raise',
            email_verified=True,
        )
        db.session.add(user)
        db.session.commit()

        task = ExportTask(user_id=user.id, status='PENDING')
        db.session.add(task)
        db.session.commit()
        task_id = task.id

        real_commit = db.session.commit

        def _always_locked():
            raise sqlite3.OperationalError('database is locked')

        monkeypatch.setattr(db.session, 'commit', _always_locked)
        monkeypatch.setattr(tasks_mod.time, 'sleep', lambda *_a, **_kw: None)

        with pytest.raises(sqlite3.OperationalError, match='locked'):
            tasks_mod._transition_export_task(
                task_id, ['PENDING'], status='RUNNING'
            )

        monkeypatch.setattr(db.session, 'commit', real_commit)
        db.session.expire_all()
        assert db.session.get(ExportTask, task_id).status == 'PENDING'

        ExportTask.query.delete()
        User.query.delete()
        db.session.commit()


def test_transition_export_task_cas_miss_returns_false(app):
    """Committed 0-row UPDATE is the only False path (already terminal)."""
    import poi_broker.tasks as tasks_mod
    from poi_broker import db
    from poi_broker.models import ExportTask, User

    with app.app_context():
        ExportTask.query.delete()
        User.query.filter_by(email='cas-miss@example.com').delete()
        db.session.commit()

        user = User(
            email='cas-miss@example.com',
            password='hashed',
            name='CAS Miss',
            email_verified=True,
        )
        db.session.add(user)
        db.session.commit()

        task = ExportTask(user_id=user.id, status='FAILED')
        db.session.add(task)
        db.session.commit()
        task_id = task.id

        assert tasks_mod._transition_export_task(
            task_id, ['PENDING', 'RUNNING'], status='RUNNING'
        ) is False
        assert db.session.get(ExportTask, task_id).status == 'FAILED'

        ExportTask.query.delete()
        User.query.delete()
        db.session.commit()


def test_create_export_file_heartbeat_continues_on_lock(app, monkeypatch):
    """Heartbeat and pre-write file_path persist must not abort on a locked DB."""
    import sqlite3
    from pathlib import Path

    import poi_broker.tasks as tasks_mod
    from poi_broker.models import ExportTask

    fake_task = ExportTask(user_id=1, status='PENDING')

    monkeypatch.setattr(tasks_mod.db, 'session', _StubSession(fake_task))

    heartbeats = {'n': 0}
    persist_locks = {'n': 0}

    def _fake_transition(task_id, from_statuses, **values):
        if task_id != 42:
            return False
        # Claim: PENDING|RUNNING -> RUNNING
        if values.get('status') == 'RUNNING' and 'PENDING' in from_statuses:
            fake_task.status = 'RUNNING'
            return True
        if from_statuses == ['RUNNING'] and values.get('status') == 'RUNNING':
            # Pre-write file_path persist carries file_path; heartbeats don't.
            if 'file_path' in values:
                persist_locks['n'] += 1
                raise sqlite3.OperationalError('database is locked')
            # Heartbeat: RUNNING -> RUNNING — raise lock once, then succeed
            heartbeats['n'] += 1
            if heartbeats['n'] == 1:
                raise sqlite3.OperationalError('database is locked')
            return True
        # SUCCESS
        if values.get('status') == 'SUCCESS':
            for k, v in values.items():
                setattr(fake_task, k, v)
            return True
        return False

    monkeypatch.setattr(tasks_mod, '_transition_export_task', _fake_transition)
    monkeypatch.setattr(tasks_mod, 'EXPORT_HEARTBEAT_SECONDS', 0)

    class _Row:
        pass

    class _OkQuery:
        def yield_per(self, _n):
            return iter([_Row(), _Row()])

    monkeypatch.setattr(
        tasks_mod,
        'build_export_query_from_rules',
        lambda *_a, **_kw: (_OkQuery(), 'where'),
    )
    monkeypatch.setattr(
        tasks_mod, 'get_export_columns', lambda: ['alert_id']
    )
    monkeypatch.setattr(
        tasks_mod, 'build_export_row', lambda _row: {'alert_id': 1}
    )
    monkeypatch.setattr(tasks_mod, 'create_app', lambda: app)

    exports_dir = Path(app.instance_path) / 'exports'
    exports_dir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in exports_dir.iterdir()}

    tasks_mod.create_export_file.func(
        query_params={'rules': [{'x': 1}]}, user_id=1, task_id=42,
    )

    assert fake_task.status == 'SUCCESS'
    assert persist_locks['n'] == 1
    assert heartbeats['n'] >= 1
    after = {p.name for p in exports_dir.iterdir()}
    new_files = after - before
    assert len(new_files) == 1
    for name in new_files:
        (exports_dir / name).unlink(missing_ok=True)


# ============================================================================
# MANUAL TESTING INSTRUCTIONS
# ============================================================================
"""
To manually test the production Huey setup with SQLite backend:

## STEP 1: Start the Huey Worker

### On Linux/Mac:
```bash
export HUEY_BACKEND=sqlite
export HUEY_SQLITE_PATH=./instance/huey.db
export SECRET_KEY=test-secret-key
./run_huey_worker.sh
```

### On Windows:
```cmd
set HUEY_BACKEND=sqlite
set HUEY_SQLITE_PATH=.\\instance\\huey.db
set SECRET_KEY=test-secret-key
run_huey_worker.bat
```

## STEP 2: Start the Flask App (in another terminal)

### On Linux/Mac:
```bash
export HUEY_BACKEND=sqlite
export HUEY_SQLITE_PATH=./instance/huey.db
export SECRET_KEY=test-secret-key
python -m flask --app wsgi:app run --debug
```

### On Windows:
```cmd
set HUEY_BACKEND=sqlite
set HUEY_SQLITE_PATH=.\\instance\\huey.db
set SECRET_KEY=test-secret-key
python -m flask --app wsgi:app run --debug
```

## STEP 3: Test the Export Feature

1. Open http://localhost:5000/ in your browser
2. Login to your account
3. Navigate to /export
4. Build a query using the visual query builder
5. Click "Start Export"
6. In the worker terminal, you should see:
   ```
   [2026-06-08 10:30:45] EXECUTING: poi_broker.tasks.create_export_file
   ```
7. Monitor the task progress in real-time
8. Once complete, download the CSV file

## STEP 4: Verify SQLite Persistence

The Huey task database is stored at: ./instance/huey.db

Check task history:
```bash
sqlite3 ./instance/huey.db
.tables
SELECT * FROM task;
```

## STEP 5: Load Testing (Optional)

To test with multiple concurrent exports:

```python
import requests
import json

session = requests.Session()
session.post('http://localhost:5000/login', data={
    'email': 'your@email.com',
    'password': 'your_password'
})

# Submit multiple export tasks
for i in range(3):
    response = session.post('http://localhost:5000/export', json={
        'rules': [
            {'id': 'featuretable.ant_mag_corrected', 'field': 'feature_mean_magn_r', 
             'type': 'double', 'input': 'text', 'operator': '>', 'value': 15}
        ]
    })
    print(f'Export {i+1}: {response.json()}')
```

## TROUBLESHOOTING

### Huey consumer won't start:
- Check that SECRET_KEY environment variable is set
- Verify DATABASE paths are correct
- Check logs: tail -f huey.log

### Tasks not executing:
- Verify HUEY_BACKEND=sqlite is set
- Check that huey_consumer process is running
- Look for errors in the worker log

### SQLite database locked:
- Ensure only one huey_consumer is running
- Check for stale lock files in instance/
- Restart the worker process
"""
