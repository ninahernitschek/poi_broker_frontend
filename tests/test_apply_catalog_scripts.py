"""Invariants of the two alerts-DB scripts awaiting upstream integration.

Pins the properties that are load-bearing and easy to break by editing the SQL:
the magnitude repair is idempotent and relocates rather than discards the
passband; the index migration creates every composite before dropping anything;
and the composite column order still matches the keyset cursor.

See docs/catalog_sort_migration.md and the consolidated migration ledger in
docs/adr/adr_migration_completion_status.md, plus
docs/adr/adr_db_schema_migration_strategy.md.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / 'tools'

APPLY_SQL = TOOLS / 'apply_fix_bad_magnitudes.sql'
INDEX_SQL = TOOLS / 'apply_catalog_sort_indexes.sql'
ROLLBACK_SQL = TOOLS / 'rollback_catalog_sort_indexes.sql'
VERIFIER = TOOLS / 'verify_catalog_pagination.py'


def _schema(conn) -> None:
    # Includes every column the migration script's pre-flight SELECT reads, so
    # a future edit that touches another column fails here rather than in
    # production.
    conn.executescript(
        """
        CREATE TABLE featuretable (
            alert_id TEXT, locus_id TEXT, date_alert_mjd REAL,
            locus_ra REAL, locus_dec REAL, ant_mag_corrected REAL,
            ztf_object_id TEXT, ant_passband TEXT,
            num_alerts INTEGER, num_mag_values INTEGER,
            brightest_alert_magnitude_ztf REAL, is_corrected TEXT,
            PRIMARY KEY (alert_id, locus_id, date_alert_mjd)
        );
        """
    )


def _seed(conn) -> None:
    """Numeric and NULL magnitudes, g/R tokens, and an existing passband."""
    rows = [
        ('ztf_candidate:0000000000000000001', 'L1', 60000.0, 18.5, 'g'),
        ('ztf_candidate:0000000000000000002', 'L2', 60001.0, 19.5, 'R'),
        # token in the wrong column, and its own column already populated
        ('ztf_candidate:0000000000000000003', 'L3', 60002.0, 'R', 'R'),
        ('ztf_candidate:0000000000000000004', 'L4', 60003.0, None, None),
        ('ztf_candidate:0000000000000000005', 'L5', 60004.0, 'g', None),
        ('ztf_candidate:0000000000000000006', 'L6', 60005.0, 'R', None),
    ]
    conn.executemany(
        'INSERT INTO featuretable (alert_id, locus_id, date_alert_mjd, '
        'ant_mag_corrected, ant_passband) VALUES (?, ?, ?, ?, ?)', rows)
    conn.commit()


def _magnitude_state(conn) -> dict:
    return {
        row[0]: (row[1], row[2])
        for row in conn.execute(
            'SELECT alert_id, ant_passband, ant_mag_corrected FROM featuretable')
    }


def test_magnitude_script_relocates_the_passband_and_nulls_the_magnitude(tmp_path):
    conn = sqlite3.connect(tmp_path / 'alerts.db')
    _schema(conn)
    _seed(conn)

    conn.executescript(APPLY_SQL.read_text(encoding='utf-8'))
    state = _magnitude_state(conn)

    # The passband token is recovered, not discarded: this row ends up
    # filterable by passband, which it was not before.
    assert state['ztf_candidate:0000000000000000003'][0] == 'R'
    assert state['ztf_candidate:0000000000000000005'] == ('g', None)
    assert state['ztf_candidate:0000000000000000006'] == ('R', None)
    # ... and no non-numeric magnitude survives anywhere.
    assert all(
        isinstance(mag, (int, float)) or mag is None
        for _, mag in state.values()
    ), state
    # The genuinely-NULL magnitude is untouched.
    assert state['ztf_candidate:0000000000000000004'][1] is None
    # Numeric magnitudes are not disturbed.
    assert state['ztf_candidate:0000000000000000001'][1] == 18.5
    conn.close()


def _run_repair(path):
    return subprocess.run(
        [sys.executable, str(TOOLS / 'apply_sql.py'), str(APPLY_SQL),
         '--db', str(path), '--yes'], capture_output=True, text=True, timeout=30,
    )


def test_magnitude_repair_rejects_unknown_tokens_without_changing_other_rows(tmp_path):
    path = tmp_path / 'alerts copy #1%.db'
    conn = sqlite3.connect(path)
    _schema(conn)
    _seed(conn)
    conn.execute(
        'INSERT INTO featuretable (alert_id,locus_id,date_alert_mjd,ant_mag_corrected) '
        'VALUES (?,?,?,?)', ('unknown', 'L-unknown', 61000, 'UNKNOWN_TOKEN'),
    )
    conn.commit()
    before = _magnitude_state(conn)
    conn.close()
    result = _run_repair(path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'UNKNOWN_TOKEN' in result.stdout
    with sqlite3.connect(path) as conn:
        assert _magnitude_state(conn) == before


def test_magnitude_repair_preserves_numeric_strings_and_existing_passbands(tmp_path):
    path = tmp_path / 'numeric.db'
    conn = sqlite3.connect(path)
    _schema(conn)
    conn.executemany(
        'INSERT INTO featuretable (alert_id,locus_id,date_alert_mjd,ant_mag_corrected,ant_passband) '
        'VALUES (?,?,?,?,?)',
        [('numeric', 'L1', 61000, '18.5', None), ('populated', 'L2', 61001, 'R', 'g')],
    )
    conn.commit()
    conn.close()
    result = _run_repair(path)
    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(path) as conn:
        assert _magnitude_state(conn) == {'numeric': (None, 18.5), 'populated': ('g', None)}


def test_failed_repair_postcondition_rolls_back_prior_updates(tmp_path):
    path = tmp_path / 'postcondition.db'
    conn = sqlite3.connect(path)
    _schema(conn)
    _seed(conn)
    before = _magnitude_state(conn)
    conn.executescript("""
        CREATE TRIGGER preserve_token BEFORE UPDATE ON featuretable
        WHEN OLD.locus_id = 'L6' BEGIN SELECT RAISE(IGNORE); END;
    """)
    conn.close()
    result = _run_repair(path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'remaining_non_numeric_magnitudes' in result.stdout
    assert 'rolled back' in result.stdout
    with sqlite3.connect(path) as conn:
        assert _magnitude_state(conn) == before


def test_failure_in_second_repair_update_rolls_back_first_update(tmp_path):
    path = tmp_path / 'second-update.db'
    conn = sqlite3.connect(path)
    _schema(conn)
    _seed(conn)
    before = _magnitude_state(conn)
    conn.executescript("""
        CREATE TRIGGER fail_second BEFORE UPDATE ON featuretable
        WHEN OLD.locus_id = 'L3' BEGIN
            SELECT RAISE(ABORT, 'second update failed');
        END;
    """)
    conn.close()
    result = _run_repair(path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'second update failed' in result.stdout
    with sqlite3.connect(path) as conn:
        assert _magnitude_state(conn) == before


def test_sql_runner_handles_trigger_bodies_and_quoted_semicolons(capsys):
    from tools.apply_sql import execute_script

    conn = sqlite3.connect(':memory:', isolation_level=None)
    execute_script(conn, """
        BEGIN;
        CREATE TABLE data (value TEXT); CREATE TABLE audit (value TEXT);
        CREATE TRIGGER log_insert AFTER INSERT ON data BEGIN
            INSERT INTO audit VALUES ('trigger;one');
            INSERT INTO audit VALUES ('trigger;two');
        END;
        INSERT INTO data VALUES ('value;with;semicolons');
        COMMIT;
        SELECT value FROM data;
    """)
    assert conn.execute('SELECT value FROM audit').fetchall() == [('trigger;one',), ('trigger;two',)]
    assert 'value;with;semicolons' in capsys.readouterr().out
    assert not conn.in_transaction
    conn.close()


def test_sql_runner_rejects_and_rolls_back_unfinished_transaction(tmp_path):
    path = tmp_path / 'unfinished.db'
    conn = sqlite3.connect(path)
    _schema(conn)
    _seed(conn)
    before = _magnitude_state(conn)
    conn.close()
    script = tmp_path / 'unfinished.sql'
    script.write_text('BEGIN; UPDATE featuretable SET ant_mag_corrected=99;', encoding='utf-8')
    result = subprocess.run(
        [sys.executable, str(TOOLS / 'apply_sql.py'), str(script), '--db', str(path), '--yes'],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'transaction open' in result.stdout
    with sqlite3.connect(path) as conn:
        assert _magnitude_state(conn) == before


def test_magnitude_script_is_idempotent(tmp_path):
    conn = sqlite3.connect(tmp_path / 'alerts.db')
    _schema(conn)
    _seed(conn)
    script = APPLY_SQL.read_text(encoding='utf-8')

    conn.executescript(script)
    after_first = _magnitude_state(conn)
    conn.executescript(script)
    assert _magnitude_state(conn) == after_first
    conn.close()


def test_magnitude_script_leaves_no_non_numeric_magnitude(tmp_path):
    """The invariant verify_catalog_pagination.py check 6 asserts."""
    conn = sqlite3.connect(tmp_path / 'alerts.db')
    _schema(conn)
    _seed(conn)
    conn.executescript(APPLY_SQL.read_text(encoding='utf-8'))
    remaining = conn.execute(
        "SELECT COUNT(*) FROM featuretable WHERE "
        "typeof(ant_mag_corrected) NOT IN ('real','integer') "
        "AND ant_mag_corrected IS NOT NULL").fetchone()[0]
    assert remaining == 0
    conn.close()


def _statements(script: Path) -> list[str]:
    """Executable statements, with SQL comments stripped."""
    text = script.read_text(encoding='utf-8')
    text = re.sub(r'--[^\n]*', '', text)
    return [s.strip() for s in text.split(';') if s.strip()]


def test_index_migration_creates_every_composite_before_dropping_anything():
    """Safety ordering: no column is ever left unindexed."""
    statements = _statements(INDEX_SQL)
    last_create = max(i for i, s in enumerate(statements)
                      if s.upper().startswith('CREATE INDEX'))
    first_drop = min(i for i, s in enumerate(statements)
                     if s.upper().startswith('DROP INDEX'))
    assert last_create < first_drop, (
        'every CREATE INDEX must precede every DROP INDEX so an interrupted run '
        'never leaves a column unindexed'
    )


def test_index_migration_composite_columns_match_the_keyset_cursor():
    """(column, alert_id, locus_id) — a different order reintroduces a full sort."""
    from poi_broker.services.catalog_query import SORT_COLUMN_ATTRS, SORT_KEYS

    text = INDEX_SQL.read_text(encoding='utf-8')
    for key in SORT_KEYS:
        attr = SORT_COLUMN_ATTRS[key]
        if key == 'alert_id':
            # Served by the PK autoindex; deliberately no composite.
            assert f'sort_{attr}' not in text
            continue
        pattern = rf'CREATE INDEX IF NOT EXISTS idx_featuretable_sort_{attr}\s+'
        match = re.search(pattern + r'ON featuretable \(([^)]*)\)', text, re.I)
        assert match, f'no composite index for sort key {key} ({attr})'
        cols = tuple(c.strip() for c in match.group(1).split(','))
        assert cols == (attr, 'alert_id', 'locus_id'), cols


def test_index_migration_is_rerunnable_and_rollback_restores_previous_state(tmp_path):
    conn = sqlite3.connect(tmp_path / 'alerts.db')
    _schema(conn)
    _seed(conn)
    for col in ('date_alert_mjd', 'ztf_object_id', 'locus_id', 'locus_ra',
                'locus_dec', 'ant_mag_corrected'):
        conn.execute(f'CREATE INDEX idx_featuretable_{col} ON featuretable({col})')

    def names() -> set[str]:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}

    forward = INDEX_SQL.read_text(encoding='utf-8')
    conn.executescript(forward)
    after_first = names()
    assert any(n.startswith('idx_featuretable_sort_') for n in after_first)
    assert 'idx_featuretable_locus_ra' not in after_first, (
        'the single-column index must be dropped, its composite replaces it'
    )

    conn.executescript(forward)  # re-run must be a no-op
    assert names() == after_first

    conn.executescript(ROLLBACK_SQL.read_text(encoding='utf-8'))
    restored = names()
    assert not any(n.startswith('idx_featuretable_sort_') for n in restored)
    assert 'idx_featuretable_locus_ra' in restored
    conn.close()


def test_every_file_the_runbook_references_exists():
    """docs/catalog_sort_migration.md must not cite a path that was renamed."""
    doc = (Path(__file__).resolve().parents[1] / 'docs'
           / 'catalog_sort_migration.md').read_text(encoding='utf-8')
    referenced = set(re.findall(r'`(tools/[\w./-]+)`', doc))
    assert referenced, 'runbook cites no tools/ paths -- did it get rewritten?'
    for rel in referenced:
        assert (TOOLS.parent / rel).exists(), f'runbook references missing {rel}'
