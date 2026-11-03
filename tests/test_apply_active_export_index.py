"""The branch-only migration must enforce one active export per user."""

import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APPLY_SQL = ROOT / 'tools' / 'apply_active_export_index.sql'


@pytest.fixture
def conn():
    with sqlite3.connect(':memory:') as connection:
        connection.execute(
            'CREATE TABLE export_task (id INTEGER PRIMARY KEY, '
            'user_id INTEGER NOT NULL, status TEXT NOT NULL)'
        )
        yield connection


def test_migration_is_repeatable_and_preserves_terminal_tasks(conn):
    conn.executemany(
        'INSERT INTO export_task (user_id, status) VALUES (?, ?)',
        [(1, 'SUCCESS'), (1, 'FAILED'), (1, 'PENDING'), (2, 'RUNNING')],
    )
    script = APPLY_SQL.read_text(encoding='utf-8')
    conn.executescript(script)
    conn.executescript(script)
    assert conn.execute('SELECT count(*) FROM export_task').fetchone()[0] == 4
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO export_task (user_id, status) VALUES (1, 'RUNNING')")


@pytest.mark.parametrize('statuses', [('PENDING', 'PENDING'), ('RUNNING', 'RUNNING'), ('PENDING', 'RUNNING')])
def test_migration_rejects_existing_duplicate_active_tasks(conn, statuses):
    conn.executemany(
        'INSERT INTO export_task (user_id, status) VALUES (1, ?)',
        [(status,) for status in statuses],
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.executescript(APPLY_SQL.read_text(encoding='utf-8'))
    assert conn.execute('SELECT count(*) FROM export_task').fetchone()[0] == 2


def test_active_export_statuses_match_model_and_fresh_schema():
    from poi_broker.models import ExportTask

    predicate = f"status IN ({', '.join(repr(s) for s in ExportTask.ACTIVE_STATUSES)})"
    assert predicate in APPLY_SQL.read_text(encoding='utf-8')
    assert predicate in (ROOT / 'tools/usersdb_schema.sql').read_text(encoding='utf-8')
