"""C4: watchlist digest re-runs ORM filters from rules_json, never stored SQL."""

import importlib.util
import json
import os
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest
from sqlalchemy.dialects import sqlite as sqlite_dialect

from poi_broker import db
from poi_broker.models import User, Watchlist, Ztf
from poi_broker.services.query_service import (
    build_query_from_rules,
    execute_watchlist,
    get_preview_sql,
)

DIGEST_PATH = Path(__file__).resolve().parents[1] / 'tools' / 'watchlist_digest.py'


def _load_digest():
    if 'watchlist_digest' in sys.modules:
        return sys.modules['watchlist_digest']
    spec = importlib.util.spec_from_file_location('watchlist_digest', DIGEST_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules['watchlist_digest'] = module
    spec.loader.exec_module(module)
    return module

RULES_NOT_NULL = {
    'condition': 'AND',
    'rules': [
        {'field': 'featuretable.alert_id', 'operator': 'is_not_null'},
    ],
}

RULES_EQUAL_TARGET = {
    'condition': 'AND',
    'rules': [
        {
            'field': 'featuretable.alert_id',
            'operator': 'equal',
            'value': 'ztf_candidate:watch-target',
        },
    ],
}

INJECTED_SQL_WHERE = "1 = 1) OR 1 = 1 --"


def _seed_watchlist_rows() -> None:
    db.session.add_all(
        [
            Ztf(
                alert_id='ztf_candidate:watch-target',
                date_alert_mjd=60010.5,
                locus_id='L-watch-1',
                ztf_object_id='Z-watch-1',
                locus_ra=10.0,
                locus_dec=20.0,
            ),
            Ztf(
                alert_id='ztf_candidate:watch-other',
                date_alert_mjd=60010.8,
                locus_id='L-watch-2',
                ztf_object_id='Z-watch-2',
                locus_ra=11.0,
                locus_dec=21.0,
            ),
            Ztf(
                alert_id='ztf_candidate:watch-old',
                date_alert_mjd=60000.0,
                locus_id='L-watch-3',
                ztf_object_id='Z-watch-3',
                locus_ra=12.0,
                locus_dec=22.0,
            ),
        ]
    )
    db.session.commit()


def test_build_query_from_rules_keeps_bind_parameters(app):
    with app.app_context():
        query, payload = build_query_from_rules(RULES_EQUAL_TARGET)
        assert payload is RULES_EQUAL_TARGET
        compiled = str(query.statement.compile(dialect=sqlite_dialect.dialect()))
        assert 'ztf_candidate:watch-target' not in compiled
        assert '?' in compiled


def test_get_preview_sql_is_display_only(app):
    with app.app_context():
        preview = get_preview_sql(RULES_EQUAL_TARGET)
        assert 'ztf_candidate:watch-target' in preview
        assert 'alert_id' in preview


def test_execute_watchlist_filters_mjd_window_and_rules(app):
    with app.app_context():
        _seed_watchlist_rows()
        alert_ids = execute_watchlist(
            RULES_NOT_NULL,
            start_mjd=60010.0,
            end_mjd=60011.0,
            limit=10,
        )
        assert alert_ids == [
            'ztf_candidate:watch-other',
            'ztf_candidate:watch-target',
        ]

        targeted = execute_watchlist(
            RULES_EQUAL_TARGET,
            start_mjd=60010.0,
            end_mjd=60011.0,
            limit=10,
        )
        assert targeted == ['ztf_candidate:watch-target']


def test_execute_watchlist_respects_limit(app):
    with app.app_context():
        _seed_watchlist_rows()
        alert_ids = execute_watchlist(
            RULES_NOT_NULL,
            start_mjd=60010.0,
            end_mjd=60011.0,
            limit=1,
        )
        assert alert_ids == ['ztf_candidate:watch-other']


def test_digest_run_ignores_injected_sql_where(app, monkeypatch):
    """Stored sql_where must not be executed even if it was edited to 1=1."""
    digest = _load_digest()

    with app.app_context():
        _seed_watchlist_rows()
        user = User(
            email='digest@example.com',
            password='hashed',
            name='Digest',
            email_verified=True,
        )
        db.session.add(user)
        db.session.commit()
        db.session.add(
            Watchlist(
                user_id=user.id,
                name='Injected Watchlist',
                rules_json=json.dumps(RULES_EQUAL_TARGET),
                sql_where=INJECTED_SQL_WHERE,
                created_at=1711929600,
            )
        )
        db.session.commit()

    monkeypatch.setattr(
        digest,
        'utc_yesterday_mjd_range',
        lambda now_utc=None: (60010.0, 60011.0, '2023-06-01'),
    )
    monkeypatch.setattr(digest, 'create_app', lambda: app)

    sent: list[dict] = []

    def _capture_email(**kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr(digest, 'send_email', _capture_email)

    exit_code = digest.run(
        limit=10,
        dry_run=False,
        only_email='digest@example.com',
        skip_empty=False,
    )
    assert exit_code == 0
    assert len(sent) == 1
    body = sent[0]['message']
    assert 'ztf_candidate:watch-target' in body
    assert 'ztf_candidate:watch-other' not in body
    assert 'ztf_candidate:watch-old' not in body


def test_digest_run_rolls_back_session_after_orm_failure(app, monkeypatch):
    """A failed ORM query must not poison later watchlists in the same run."""
    from sqlalchemy import text

    digest = _load_digest()

    with app.app_context():
        _seed_watchlist_rows()
        user = User(
            email='rollback@example.com',
            password='hashed',
            name='Rollback',
            email_verified=True,
        )
        db.session.add(user)
        db.session.commit()
        db.session.add_all(
            [
                Watchlist(
                    user_id=user.id,
                    name='Broken First',
                    rules_json=json.dumps(RULES_NOT_NULL),
                    sql_where='preview',
                    created_at=1711929600,
                ),
                Watchlist(
                    user_id=user.id,
                    name='Healthy Second',
                    rules_json=json.dumps(RULES_EQUAL_TARGET),
                    sql_where='preview',
                    created_at=1711929601,
                ),
            ]
        )
        db.session.commit()

    calls = {'n': 0}
    real_execute = digest.execute_watchlist

    def _flaky_execute(*args, **kwargs):
        calls['n'] += 1
        if calls['n'] == 1:
            db.session.execute(text('SELECT * FROM this_table_does_not_exist'))
        return real_execute(*args, **kwargs)

    monkeypatch.setattr(digest, 'execute_watchlist', _flaky_execute)
    monkeypatch.setattr(
        digest,
        'utc_yesterday_mjd_range',
        lambda now_utc=None: (60010.0, 60011.0, '2023-06-01'),
    )
    monkeypatch.setattr(digest, 'create_app', lambda: app)

    sent: list[dict] = []

    def _capture_email(**kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr(digest, 'send_email', _capture_email)

    exit_code = digest.run(
        limit=10,
        dry_run=False,
        only_email='rollback@example.com',
        skip_empty=False,
    )
    assert exit_code == 0
    assert calls['n'] == 2
    assert len(sent) == 1
    body = sent[0]['message']
    assert 'Healthy Second' in body
    assert 'ztf_candidate:watch-target' in body
    assert 'Broken First' in body
    assert 'internal error' in body.lower()


@pytest.mark.parametrize('dry_run', [True, False])
def test_digest_processing_error_sends_no_email_in_dry_run(app, monkeypatch, caplog, dry_run):
    digest = _load_digest()
    with app.app_context():
        user = User(email='dry-run@example.com', password='hashed', email_verified=True)
        db.session.add(user)
        db.session.commit()
        db.session.add(Watchlist(
            user_id=user.id, name='Broken watchlist', rules_json='{}',
            sql_where='preview only', created_at=1711929600,
        ))
        db.session.commit()

    monkeypatch.setattr(digest, 'create_app', lambda: app)
    monkeypatch.setenv('ADMIN_EMAIL', 'admin@example.com')
    sent = []
    monkeypatch.setattr(digest, 'send_email', lambda **kwargs: sent.append(kwargs) or True)

    assert digest.run(
        limit=10, dry_run=dry_run, only_email='dry-run@example.com', skip_empty=False,
    ) == 0
    assert 'Failed watchlist id=' in caplog.text
    if dry_run:
        assert sent == []
    else:
        assert [mail['to_email'] for mail in sent] == ['admin@example.com', 'dry-run@example.com']


def test_load_watchlists_selects_rules_json(app):
    digest = _load_digest()

    users_db = Path(os.environ['USERS_DB_PATH'])
    with app.app_context():
        user = User(
            email='rules@example.com',
            password='hashed',
            name='Rules',
            email_verified=True,
        )
        db.session.add(user)
        db.session.commit()
        db.session.add(
            Watchlist(
                user_id=user.id,
                name='Rules Watchlist',
                rules_json=json.dumps(RULES_EQUAL_TARGET),
                sql_where='display only',
                created_at=1711929600,
            )
        )
        db.session.commit()

    rows = digest.load_watchlists(users_db, only_email='rules@example.com')
    assert len(rows) == 1
    assert json.loads(rows[0]['rules_json']) == RULES_EQUAL_TARGET
    assert rows[0]['sql_where'] == 'display only'


def test_ensure_digest_secret_key_falls_back_when_unset(monkeypatch):
    digest = _load_digest()
    monkeypatch.delenv('SECRET_KEY', raising=False)
    digest._ensure_digest_secret_key()
    assert os.environ.get('SECRET_KEY') == digest._DIGEST_SECRET_KEY_FALLBACK


def test_ensure_digest_secret_key_preserves_existing(monkeypatch):
    digest = _load_digest()
    monkeypatch.setenv('SECRET_KEY', 'already-set')
    digest._ensure_digest_secret_key()
    assert os.environ['SECRET_KEY'] == 'already-set'


def test_parse_watchlist_rules_rejects_invalid_json():
    digest = _load_digest()

    with pytest.raises(ValueError, match='Invalid watchlist rules_json'):
        digest.parse_watchlist_rules('not-json')
    with pytest.raises(ValueError, match='expected a querybuilder'):
        digest.parse_watchlist_rules('[]')


def test_digest_module_does_not_concatenate_sql_where():
    text = DIGEST_PATH.read_text(encoding='utf-8')
    assert 'WHERE (" + sql_where' not in text


class _EmailHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.text = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == 'a':
            self.links.append(dict(attrs))

    def handle_data(self, data):
        self.text.append(data)


def test_digest_html_preserves_dynamic_values_as_literal_text():
    digest = _load_digest()
    name = '<mark>User & "friends"</mark>'
    watchlist_name = '<mark>Bright & "nearby"</mark>'
    alert_id = '<mark>Alert & "one"</mark>'
    day_label = '<mark>2026-10-10</mark>'
    base_url = 'https://example.com/?label="night"&lang=en'
    result = digest.WatchlistResult(1, watchlist_name, 'preview', [alert_id])

    subject, plain_text, html_text = digest.build_email_content(
        base_url, name, day_label, [result]
    )
    parsed = _EmailHTMLParser()
    parsed.feed(html_text)

    assert 'mark' not in parsed.tags
    for value in (name, watchlist_name, alert_id, day_label):
        assert value in ''.join(parsed.text)
        assert value in plain_text
    assert day_label in subject
    assert len(parsed.links) == 1
    assert parsed.links[0] == {
        'href': digest.build_alert_link(base_url, alert_id),
        'style': 'color:#1d4ed8; text-decoration:none;',
    }


@pytest.mark.parametrize('user_name', ['<mark>User & "friends"</mark>', None])
def test_admin_error_html_preserves_dynamic_values_as_literal_text(monkeypatch, user_name):
    digest = _load_digest()
    user_email = '<mark>user@example.com</mark>'
    watchlist_name = '<mark>Bright & "nearby"</mark>'
    sql_where = 'featuretable.num_alerts < 10 AND label = \'<mark>value</mark>\''
    error = '<mark>Invalid & "missing" value</mark>'
    day_label = '<mark>2026-10-10</mark>'
    row = {'watchlist_id': 42, 'watchlist_name': watchlist_name, 'sql_where': sql_where}
    sent = []
    monkeypatch.setattr(digest, 'send_email', lambda **kwargs: sent.append(kwargs) or True)

    digest.send_admin_error_notification(
        'admin@example.com', user_email, user_name, row, error, day_label
    )

    assert len(sent) == 1
    parsed = _EmailHTMLParser()
    parsed.feed(sent[0]['html_text'])
    assert 'mark' not in parsed.tags
    for value in (user_email, user_name or '<none>', watchlist_name, sql_where, error):
        assert value in ''.join(parsed.text)
        assert value in sent[0]['message']
    assert day_label in ''.join(parsed.text)
    assert day_label in sent[0]['subject']
    assert sent[0]['to_email'] == 'admin@example.com'
