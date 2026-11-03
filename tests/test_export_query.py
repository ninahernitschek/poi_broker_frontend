"""C3/O3: bulk export projects Core columns; COUNT skips Classification when unused."""

import csv
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.dialects import sqlite as sqlite_dialect

from poi_broker import db
from poi_broker.models import Classification, ExportTask, User, Ztf
from poi_broker.services.filter_service import datetime_to_mjd, mjd_to_datetime
from poi_broker.services.query_service import (
    CLASSIFICATION_COLUMNS,
    ZTF_COLUMNS,
    build_count_query_from_rules,
    build_export_query_from_rules,
    build_export_row,
    build_query_from_rules,
    get_export_columns,
    get_query_match_count,
)

RULES = {
    'condition': 'AND',
    'rules': [
        {'field': 'featuretable.alert_id', 'operator': 'is_not_null'},
    ],
}

CLASSIFICATION_RULES = {
    'condition': 'AND',
    'rules': [
        {'field': 'classification.prob_class', 'operator': 'equal', 'value': 'sn'},
    ],
}

UNUSED_CLASSIFICATION_COLS = (
    'p_cvnova',
    'p_e',
    'p_lpv',
    'p_puls',
    'p_periodic_other',
    'p_quas',
    'p_sn',
    'p_yso',
)


def _seed_export_rows() -> None:
    classified_id = 'ztf_candidate:1000000000000000001'
    unclassified_id = 'ztf_candidate:1000000000000000002'
    db.session.add_all(
        [
            Ztf(
                alert_id=classified_id,
                date_alert_mjd=60000.0,
                locus_id='L-export-1',
                ztf_object_id='Z-export-1',
                locus_ra=10.0,
                locus_dec=20.0,
            ),
            Ztf(
                alert_id=unclassified_id,
                date_alert_mjd=60001.0,
                locus_id='L-export-2',
                ztf_object_id='Z-export-2',
                locus_ra=11.0,
                locus_dec=21.0,
            ),
            Classification(alert_id=classified_id, prob_class='sn', p_sn=0.9),
        ]
    )
    db.session.commit()


def test_get_export_columns_is_cached():
    # Called once per exported row (up to MAX_EXPORT_ROWS); the cached list
    # must be the same object every time.
    assert get_export_columns() is get_export_columns()


def test_export_query_selects_columns_not_full_classification(app):
    with app.app_context():
        _seed_export_rows()
        query, _ = build_export_query_from_rules(RULES, max_alert_mjd=99999.0)
        compiled = str(query.statement.compile(dialect=sqlite_dialect.dialect()))
        for unused in UNUSED_CLASSIFICATION_COLS:
            assert unused not in compiled
        assert 'prob_class' in compiled
        names = [desc['name'] for desc in query.column_descriptions]
        assert names == get_export_columns()
        assert len(names) == len(ZTF_COLUMNS) + len(CLASSIFICATION_COLUMNS)


def test_export_query_does_not_hydrate_orm_identity_map(app):
    with app.app_context():
        _seed_export_rows()
        db.session.expunge_all()
        query, _ = build_export_query_from_rules(RULES, max_alert_mjd=99999.0)
        rows = list(query.yield_per(1000))
        assert len(rows) == 2
        identities = list(db.session.identity_map.values())
        assert not any(isinstance(obj, (Ztf, Classification)) for obj in identities)
        for row in rows:
            assert not isinstance(row, Ztf)
            assert not isinstance(row[0], Ztf)


def test_export_row_null_classification_on_outer_join(app):
    with app.app_context():
        _seed_export_rows()
        db.session.expunge_all()
        query, _ = build_export_query_from_rules(RULES, max_alert_mjd=99999.0)
        by_alert = {}
        for row in query.yield_per(1000):
            row_dict = build_export_row(row)
            by_alert[row_dict['alert_id']] = row_dict

        classified = by_alert['ztf_candidate:1000000000000000001']
        assert classified['classification_alert_id'] == 'ztf_candidate:1000000000000000001'
        assert classified['classification_prob_class'] == 'sn'
        unclassified = by_alert['ztf_candidate:1000000000000000002']
        assert unclassified['classification_alert_id'] is None
        assert unclassified['classification_prob_class'] is None


def test_create_export_file_writes_projected_csv(app, monkeypatch):
    import poi_broker.tasks as tasks_mod

    with app.app_context():
        _seed_export_rows()
        user = User(
            email='exportc3@example.com',
            password='hashed',
            name='C3',
            email_verified=True,
        )
        db.session.add(user)
        db.session.commit()
        task = ExportTask(user_id=user.id, status='PENDING')
        db.session.add(task)
        db.session.commit()
        task_id = task.id
        user_id = user.id

    monkeypatch.setattr(tasks_mod, '_get_worker_app', lambda: app)
    tasks_mod.create_export_file.func(
        query_params=RULES,
        user_id=user_id,
        task_id=task_id,
    )

    with app.app_context():
        task = db.session.get(ExportTask, task_id)
        assert task is not None
        assert task.status == 'SUCCESS'
        path = Path(task.file_path)
        assert path.exists()
        with path.open(newline='', encoding='utf-8') as handle:
            reader = csv.DictReader(handle)
            assert reader.fieldnames == get_export_columns()
            rows = list(reader)

        assert len(rows) == 2
        by_id = {row['alert_id']: row for row in rows}
        assert by_id['ztf_candidate:1000000000000000001']['classification_prob_class'] == 'sn'
        assert by_id['ztf_candidate:1000000000000000002']['classification_prob_class'] == ''
        path.unlink(missing_ok=True)


def test_count_query_skips_classification_join_for_ztf_only_rules(app):
    """O3: Ztf-only COUNT is SELECT count(*) FROM featuretable with no join."""
    with app.app_context():
        count_query = build_count_query_from_rules(RULES)
        compiled = str(
            count_query.statement.compile(dialect=sqlite_dialect.dialect())
        ).lower()
        assert 'count(' in compiled
        assert 'from featuretable' in compiled
        assert 'classification' not in compiled
        assert 'join' not in compiled
        # Must not wrap a 280-column entity SELECT in a subquery.
        assert 'select *' not in compiled
        assert 'date_log' not in compiled


def test_count_query_joins_classification_when_needed(app):
    with app.app_context():
        count_query = build_count_query_from_rules(CLASSIFICATION_RULES)
        compiled = str(
            count_query.statement.compile(dialect=sqlite_dialect.dialect())
        ).lower()
        assert 'count(' in compiled
        assert 'classification' in compiled


def test_classification_rules_join_even_when_include_classification_false(app):
    with app.app_context():
        query, _ = build_query_from_rules(
            CLASSIFICATION_RULES,
            include_classification=False,
        )
        compiled = str(query.statement.compile(dialect=sqlite_dialect.dialect())).lower()
        assert 'join' in compiled
        assert 'classification' in compiled


def test_get_query_match_count_returns_int(app):
    with app.app_context():
        _seed_export_rows()
        assert get_query_match_count(RULES) == 2
        assert get_query_match_count(CLASSIFICATION_RULES) == 1


def test_snapshot_cutoff_bounds_count_and_export(app):
    """max_alert_mjd excludes rows at/after the cutoff from count and export."""
    with app.app_context():
        _seed_export_rows()  # alert MJDs 60000.0 and 60001.0
        # Boundary row exactly at the cutoff must be excluded (strict <).
        db.session.add(
            Ztf(
                alert_id='ztf_candidate:1000000000000000003',
                date_alert_mjd=60000.5,
                locus_id='L-export-3',
                ztf_object_id='Z-export-3',
                locus_ra=12.0,
                locus_dec=22.0,
            )
        )
        db.session.commit()
        cutoff = 60000.5

        assert get_query_match_count(RULES, max_alert_mjd=cutoff) == 1
        assert get_query_match_count(RULES) == 3  # no cutoff = unchanged

        query, _ = build_export_query_from_rules(RULES, max_alert_mjd=cutoff)
        alert_ids = sorted(
            build_export_row(row)['alert_id'] for row in query.yield_per(1000)
        )
        assert alert_ids == ['ztf_candidate:1000000000000000001']


def test_datetime_mjd_roundtrip():
    dt = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
    assert mjd_to_datetime(datetime_to_mjd(dt)) == dt
    # 2026-08-29 12:00:00 UTC = MJD 61281.5
    assert datetime_to_mjd(dt) == 61281.5


def test_create_export_file_applies_snapshot_from_task_row(app, monkeypatch):
    """Worker reads the cutoff from ExportTask.snapshot_mjd, not the payload."""
    import poi_broker.tasks as tasks_mod

    with app.app_context():
        _seed_export_rows()  # alert MJDs 60000.0 and 60001.0
        user = User(
            email='exportsnapshot@example.com',
            password='hashed',
            name='Snapshot',
            email_verified=True,
        )
        db.session.add(user)
        db.session.commit()
        task = ExportTask(
            user_id=user.id,
            status='PENDING',
            snapshot_mjd=60000.5,
        )
        db.session.add(task)
        db.session.commit()
        task_id = task.id
        user_id = user.id

    # Old-signature call on purpose: the queue payload must not need the cutoff.
    monkeypatch.setattr(tasks_mod, '_get_worker_app', lambda: app)
    tasks_mod.create_export_file.func(
        query_params=RULES,
        user_id=user_id,
        task_id=task_id,
    )

    with app.app_context():
        task = db.session.get(ExportTask, task_id)
        assert task.status == 'SUCCESS'
        with Path(task.file_path).open(newline='', encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle))
        assert [row['alert_id'] for row in rows] == [
            'ztf_candidate:1000000000000000001'
        ]
        Path(task.file_path).unlink(missing_ok=True)
