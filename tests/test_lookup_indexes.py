"""Lookup indexes declared on alerts/users models must exist after create_all()."""

import sqlalchemy as sa

from poi_broker import db

EXPECTED_ALERTS_INDEXES = {
    "idx_featuretable_alert_id",
    "idx_featuretable_ant_passband",
    "idx_featuretable_sort_date_alert_mjd",
    "idx_featuretable_sort_ztf_object_id",
    "idx_featuretable_sort_locus_id",
    "idx_featuretable_sort_locus_ra",
    "idx_featuretable_sort_locus_dec",
    "idx_featuretable_sort_ant_mag_corrected",
    "idx_crossmatches_locus_id",
    "idx_classification_prob_class",
}

# The catalog keyset cursor is (sort_column, alert_id, locus_id); the index must
# match that column order exactly or SQLite sorts the table instead of scanning
# the index. Measured on 3.18M rows: first page 2.2 s without, 0.000 s with.
EXPECTED_SORT_INDEX_COLUMNS = {
    "idx_featuretable_sort_date_alert_mjd": ("date_alert_mjd", "alert_id", "locus_id"),
    "idx_featuretable_sort_ztf_object_id": ("ztf_object_id", "alert_id", "locus_id"),
    "idx_featuretable_sort_locus_id": ("locus_id", "alert_id", "locus_id"),
    "idx_featuretable_sort_locus_ra": ("locus_ra", "alert_id", "locus_id"),
    "idx_featuretable_sort_locus_dec": ("locus_dec", "alert_id", "locus_id"),
    "idx_featuretable_sort_ant_mag_corrected": (
        "ant_mag_corrected", "alert_id", "locus_id",
    ),
}

EXPECTED_USERS_INDEXES = {
    "uix_filter_bookmark_user_name",
    "idx_export_task_status_updated_at",
    "idx_export_task_status_created_at",
    "uix_export_task_one_active_per_user",
}


def test_alerts_lookup_indexes_exist_after_create_all(app):
    with app.app_context():
        rows = db.session.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='index'")
        ).fetchall()

    names = {row[0] for row in rows}
    missing = EXPECTED_ALERTS_INDEXES - names
    assert not missing, f"missing lookup indexes: {sorted(missing)}"


def _index_columns(sql: str):
    """Column list from a CREATE INDEX statement, in order.

    Naive substring search is wrong here: the locus_id index repeats 'locus_id'
    as both the sort column and a tiebreaker.
    """
    body = sql[sql.upper().rindex(' ON ') + 4:]
    cols = body[body.index('(') + 1:body.rindex(')')]
    return tuple(c.strip().strip('"').lower() for c in cols.split(','))


def test_sort_indexes_match_the_keyset_cursor_order(app):
    """Column order is load-bearing, not cosmetic."""
    with app.app_context():
        rows = db.session.execute(
            sa.text("SELECT name, sql FROM sqlite_master WHERE type='index'")
        ).fetchall()

    by_name = {row[0]: (row[1] or "") for row in rows}
    for name, expected in EXPECTED_SORT_INDEX_COLUMNS.items():
        actual = _index_columns(by_name[name])
        assert actual[:len(expected)] == expected, (
            f"{name}: index columns {actual} must start with {expected}"
        )


def test_every_sortable_column_has_an_index(app):
    """A new sortable column without an index silently reintroduces a full sort."""
    from poi_broker.services.catalog_query import SORT_COLUMN_ATTRS, SORT_KEYS

    with app.app_context():
        rows = db.session.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='index'")
        ).fetchall()
    names = {row[0] for row in rows}

    # alert_id is served by the PK autoindex (alert_id, locus_id, date_alert_mjd).
    assert any(n.startswith('sqlite_autoindex_featuretable') for n in names)
    for key in SORT_KEYS:
        attr = SORT_COLUMN_ATTRS[key]
        if key == 'alert_id':
            continue
        expected = f'idx_featuretable_sort_{attr}'
        assert expected in names, f'sort key {key} ({attr}) has no {expected}'


def test_users_housekeeping_indexes_exist_after_create_all(app):
    with app.app_context():
        rows = db.session.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='index'"),
            bind_arguments={"bind": db.engines["users"]},
        ).fetchall()

    names = {row[0] for row in rows}
    missing = EXPECTED_USERS_INDEXES - names
    assert not missing, f"missing users indexes: {sorted(missing)}"
