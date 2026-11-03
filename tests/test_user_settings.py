import json
import re

from poi_broker.constants.features import FEATURE_COLUMNS
from werkzeug.security import generate_password_hash
from unittest.mock import patch

from poi_broker import db
from poi_broker.models import User, UserSettings
from poi_broker.user_settings import (
    get_user_settings,
    get_saved_feature_plot_columns,
    get_saved_last_selected_observatory,
)


def _extract_csrf_token(page_html):
    """Extract CSRF token from rendered HTML form."""
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', page_html)
    return match.group(1) if match else None


def test_settings_requires_login(client):
    r = client.get('/settings', follow_redirects=False)
    assert r.status_code in (302, 303)
    assert '/login' in r.headers.get('Location', '')


def test_settings_page_loads(auth_client):
    r = auth_client.get('/settings')
    assert r.status_code == 200
    assert b'Feature Plot Defaults' in r.data
    assert b'default_feature_plot_columns' in r.data
    assert b'csrf_token' in r.data
    assert b"document.querySelector('input[name=\"csrf_token\"]')?.value" in r.data


def test_save_and_apply_default_feature_columns(auth_client):
    # Retrieve settings page to get CSRF token
    r = auth_client.get('/settings')
    assert r.status_code == 200
    page_html = r.get_data(as_text=True)
    csrf_token = _extract_csrf_token(page_html)
    assert csrf_token is not None, 'CSRF token not found in form'

    # Save a custom default feature selection
    feature_names = [
        'feature_amplitude_magn_r',
        'feature_beyond_1_std_magn_r',
    ]
    r = auth_client.post(
        '/settings',
        data={
            'default_feature_plot_columns': feature_names,
            'csrf_token': csrf_token,
        },
        follow_redirects=True,
    )
    assert r.status_code == 200

    # The settings page should reflect the saved values.
    r = auth_client.get('/settings')
    assert r.status_code == 200
    page_text = r.get_data(as_text=True)
    assert 'feature_amplitude_magn_r" selected' in page_text
    assert 'feature_beyond_1_std_magn_r" selected' in page_text

    # The main page should include the saved list in the rendered JavaScript context.
    r = auth_client.get('/')
    assert r.status_code == 200
    page_text = r.get_data(as_text=True)
    assert json.dumps(feature_names) in page_text


def test_save_truncates_to_ten_feature_columns(auth_client):
    r = auth_client.get('/settings')
    assert r.status_code == 200
    csrf_token = _extract_csrf_token(r.get_data(as_text=True))
    assert csrf_token is not None

    keys = list(FEATURE_COLUMNS.keys())[:11]
    assert len(keys) == 11

    r = auth_client.post(
        '/settings',
        data={
            'default_feature_plot_columns': keys,
            'csrf_token': csrf_token,
        },
        follow_redirects=True,
    )
    assert r.status_code == 200

    r = auth_client.get('/settings')
    page_text = r.get_data(as_text=True)
    for k in keys[:10]:
        assert f'{k}" selected' in page_text
    assert f'{keys[10]}" selected' not in page_text


class TestSingleLoadUserSettings:
    """Tests for the single-load UserSettings optimization.

    The main page (start()) loads the user's settings row once and reuses it
    across lookups to avoid redundant DB queries. These tests verify that:
    - get_user_settings() returns the raw row (or None).
    - the getters accept an optional pre-loaded settings row and skip the DB
      query when one is provided.
    """

    def test_get_user_settings_returns_none_when_absent(self, auth_client, app):
        with app.app_context():
            current_user = db.session.query(User).filter_by(email='smoketest@example.com').first()
            assert get_user_settings(current_user.id) is None

    def test_get_user_settings_returns_row_when_present(self, auth_client, app):
        with app.app_context():
            current_user = db.session.query(User).filter_by(email='smoketest@example.com').first()
            settings = UserSettings(user_id=current_user.id)
            settings.default_feature_plot_columns = json.dumps(['feature_amplitude_magn_r'])
            db.session.add(settings)
            db.session.commit()

            loaded = get_user_settings(current_user.id)
            assert loaded is not None
            assert loaded.user_id == current_user.id

    def test_get_saved_feature_plot_columns_uses_provided_settings(self, auth_client, app):
        """When a settings row is provided, no DB query should be issued."""
        with app.app_context():
            current_user = db.session.query(User).filter_by(email='smoketest@example.com').first()
            settings = UserSettings(user_id=current_user.id)
            settings.default_feature_plot_columns = json.dumps(['feature_amplitude_magn_r'])
            db.session.add(settings)
            db.session.commit()

            with patch('poi_broker.user_settings._load_user_settings') as mock_load:
                result = get_saved_feature_plot_columns(current_user.id, settings)
                mock_load.assert_not_called()
                assert result == ['feature_amplitude_magn_r']

    def test_get_saved_last_selected_observatory_uses_provided_settings(self, auth_client, app):
        """When a settings row is provided, no DB query should be issued."""
        with app.app_context():
            current_user = db.session.query(User).filter_by(email='smoketest@example.com').first()
            settings = UserSettings(user_id=current_user.id)
            settings.last_selected_observatory_json = json.dumps({'source': 'builtin', 'name': 'Palomar'})
            db.session.add(settings)
            db.session.commit()

            with patch('poi_broker.user_settings._load_user_settings') as mock_load:
                result = get_saved_last_selected_observatory(current_user.id, settings)
                mock_load.assert_not_called()
                assert result == {'source': 'builtin', 'name': 'Palomar'}

    def test_getters_fall_back_to_db_when_no_settings_provided(self, auth_client, app):
        """Without a provided row, the getters still load from the DB (backward compat)."""
        with app.app_context():
            current_user = db.session.query(User).filter_by(email='smoketest@example.com').first()
            settings = UserSettings(user_id=current_user.id)
            settings.default_feature_plot_columns = json.dumps(['feature_amplitude_magn_r'])
            db.session.add(settings)
            db.session.commit()

            with patch('poi_broker.user_settings._load_user_settings', return_value=settings) as mock_load:
                result = get_saved_feature_plot_columns(current_user.id)
                mock_load.assert_called_once_with(current_user.id)
                assert result == ['feature_amplitude_magn_r']

    def test_start_loads_settings_once_for_authenticated_user(self, auth_client, app):
        """The main page should issue exactly one UserSettings load for an authenticated user."""
        with app.app_context():
            current_user = db.session.query(User).filter_by(email='smoketest@example.com').first()
            user_id = current_user.id
            settings = UserSettings(user_id=user_id)
            settings.default_feature_plot_columns = json.dumps(['feature_amplitude_magn_r'])
            settings.last_selected_observatory_json = json.dumps({'source': 'builtin', 'name': 'Palomar'})
            db.session.add(settings)
            db.session.commit()

        with patch('poi_broker.app.get_user_settings', wraps=get_user_settings) as mock_get:
            r = auth_client.get('/')
            assert r.status_code == 200
            mock_get.assert_called_once_with(user_id)

    def test_start_does_not_load_settings_for_anonymous_user(self, app, client):
        """Anonymous users should not trigger a UserSettings load."""
        with patch('poi_broker.app.get_user_settings') as mock_get:
            r = client.get('/')
            assert r.status_code == 200
            mock_get.assert_not_called()


def test_feature_column_list_is_derived_from_feature_columns():
    # /settings validates saved defaults against FEATURE_COLUMNS while the
    # feature-plot route validates against FEATURE_COLUMN_LIST; they must be
    # one list so a column cannot be accepted by one and dropped by the other.
    from poi_broker.constants.features import FEATURE_COLUMN_LIST, FEATURE_COLUMNS

    assert FEATURE_COLUMN_LIST == list(FEATURE_COLUMNS)
