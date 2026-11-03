import json

import pytest

from poi_broker import db
from poi_broker.app import _get_builtin_observatory_options
from poi_broker.models import User, UserObservatory, UserSettings
from poi_broker.user_settings import (
    get_saved_last_selected_observatory,
    save_last_selected_observatory,
)


@pytest.mark.parametrize(
    'payload, expected_message',
    [
        ({'latitude': 10.0, 'longitude': 10.0}, 'name is required'),
        ({'name': '   ', 'latitude': 10.0, 'longitude': 10.0}, 'name is required'),
        ({'name': 'Test', 'latitude': 'not-a-number', 'longitude': 10.0}, 'latitude and longitude must be numeric'),
        ({'name': 'Test', 'latitude': True, 'longitude': 10.0}, 'latitude and longitude must be numeric'),
        ({'name': 'Test', 'latitude': 100.0, 'longitude': 10.0}, 'latitude must be between -90 and 90'),
        ({'name': 'Test', 'latitude': 10.0, 'longitude': 200.0}, 'longitude must be between -180 and 180'),
    ],
)
def test_create_user_observatory_validation_errors(auth_client, payload, expected_message):
    response = auth_client.post('/api/user-observatories', json=payload)
    assert response.status_code == 400

    payload = response.get_json()
    assert payload is not None
    assert expected_message in payload.get('error', '')


@pytest.mark.slow
def test_create_user_observatory_duplicate_returns_already_exists(auth_client):
    observatory = {
        'name': 'Duplicate Observatory',
        'latitude': 50.0,
        'longitude': 10.0,
    }

    first_response = auth_client.post('/api/user-observatories', json=observatory)
    assert first_response.status_code in (200, 201)
    first_payload = first_response.get_json()
    assert first_payload is not None
    assert first_payload['status'] == 'ok'
    assert 'id' in first_payload

    second_response = auth_client.post('/api/user-observatories', json=observatory)
    assert second_response.status_code == 200
    second_payload = second_response.get_json()
    assert second_payload is not None
    assert second_payload['status'] == 'ok'
    assert second_payload.get('already_exists') is True
    assert second_payload['id'] == first_payload['id']


def test_list_user_observatories_scopes_to_current_user_and_orders_by_name(auth_client, app, user_factory):
    with app.app_context():
        current_user = db.session.query(User).filter_by(email='smoketest@example.com').first()
        user_factory(email='other@example.com')
        other_user_id = db.session.query(User.id).filter_by(email='other@example.com').scalar()

        custom_for_current = [
            UserObservatory(user_id=current_user.id, name='Zoo Observatory', latitude=30.0, longitude=10.0, timezone_name='UTC'),
            UserObservatory(user_id=current_user.id, name='Alpha Observatory', latitude=20.0, longitude=10.0, timezone_name='UTC'),
        ]
        custom_for_other = UserObservatory(user_id=other_user_id, name='Other Observatory', latitude=0.0, longitude=0.0, timezone_name='UTC')

        db.session.add_all(custom_for_current + [custom_for_other])
        db.session.commit()
        current_ids = [row.id for row in custom_for_current]

    response = auth_client.get('/api/user-observatories')
    assert response.status_code == 200

    payload = response.get_json()
    assert payload is not None
    items = payload.get('userObservatories')
    assert isinstance(items, list)
    assert [item['name'] for item in items] == ['Alpha Observatory', 'Zoo Observatory']
    assert all(item['id'] in set(current_ids) for item in items)


def test_anonymous_api_access_is_rejected(client):
    anonymous_get = client.get('/api/user-observatories')
    assert anonymous_get.status_code == 401
    assert anonymous_get.get_json().get('error') == 'authentication required'

    anonymous_post = client.post('/api/user-observatories', json={'name': 'Foo', 'latitude': 0.0, 'longitude': 0.0})
    assert anonymous_post.status_code == 401
    assert anonymous_post.get_json().get('error') == 'authentication required'

    anonymous_delete = client.delete('/api/user-observatories/1')
    assert anonymous_delete.status_code == 401
    assert anonymous_delete.get_json().get('error') == 'authentication required'

    anonymous_last = client.post('/api/last-observatory', json={'source': 'builtin', 'name': 'Palomar'})
    assert anonymous_last.status_code == 401
    assert anonymous_last.get_json().get('error') == 'authentication required'


@pytest.mark.slow
def test_delete_selected_observatory_triggers_fallback(monkeypatch, auth_client, app):
    monkeypatch.setattr(
        'poi_broker.routes.user_observatories.EarthLocation.get_site_names',
        lambda: ['Builtin Observatory'],
    )

    create_response = auth_client.post(
        '/api/user-observatories',
        json={'name': 'Selected Observatory', 'latitude': 40.0, 'longitude': -74.0},
    )
    assert create_response.status_code in (200, 201)
    create_payload = create_response.get_json()
    assert create_payload is not None
    observatory_id = create_payload['id']

    with app.app_context():
        current_user = db.session.query(User).filter_by(email='smoketest@example.com').first()
        settings = UserSettings(user_id=current_user.id)
        settings.last_selected_observatory_json = json.dumps({'source': 'custom', 'id': observatory_id})
        db.session.add(settings)
        db.session.commit()

    delete_response = auth_client.delete(f'/api/user-observatories/{observatory_id}')
    assert delete_response.status_code == 200

    delete_payload = delete_response.get_json()
    assert delete_payload is not None
    assert delete_payload['status'] == 'ok'
    assert 'warning' in delete_payload
    assert delete_payload['fallback']['source'] == 'builtin'
    assert delete_payload['fallback']['name'] == 'Builtin Observatory'

    with app.app_context():
        current_user = db.session.query(User).filter_by(email='smoketest@example.com').first()
        saved = get_saved_last_selected_observatory(current_user.id)
        assert saved == {'source': 'builtin', 'name': 'Builtin Observatory'}


def test_save_last_observatory_api_persists_builtin_selection(auth_client, app, monkeypatch):
    monkeypatch.setattr(
        'poi_broker.routes.user_observatories.EarthLocation.get_site_names',
        lambda: ['Palomar'],
    )
    from poi_broker.routes.user_observatories import _builtin_site_names
    _builtin_site_names.cache_clear()

    response = auth_client.post('/api/last-observatory', json={'source': 'builtin', 'name': 'Palomar'})
    assert response.status_code == 200
    assert response.get_json()['status'] == 'ok'

    with app.app_context():
        current_user = db.session.query(User).filter_by(email='smoketest@example.com').first()
        assert get_saved_last_selected_observatory(current_user.id) == {'source': 'builtin', 'name': 'Palomar'}


def test_save_last_observatory_api_rejects_unknown_builtin(auth_client, app, monkeypatch):
    monkeypatch.setattr(
        'poi_broker.routes.user_observatories.EarthLocation.get_site_names',
        lambda: ['Palomar'],
    )
    from poi_broker.routes.user_observatories import _builtin_site_names
    _builtin_site_names.cache_clear()

    response = auth_client.post(
        '/api/last-observatory',
        json={'source': 'builtin', 'name': 'NotARealObservatory'},
    )
    assert response.status_code == 400
    assert 'Unknown builtin observatory' in response.get_json().get('error', '')

    with app.app_context():
        current_user = db.session.query(User).filter_by(email='smoketest@example.com').first()
        assert get_saved_last_selected_observatory(current_user.id) is None


def test_save_last_observatory_api_rejects_unowned_custom(auth_client):
    response = auth_client.post('/api/last-observatory', json={'source': 'custom', 'id': 999999})
    assert response.status_code == 400
    assert 'Unknown custom observatory' in response.get_json().get('error', '')


def test_save_last_observatory_api_rejects_invalid_payload(auth_client):
    response = auth_client.post('/api/last-observatory', json={'source': 'builtin'})
    assert response.status_code == 400
    assert response.get_json()['error'] == 'Invalid observatory selection'


def test_save_last_observatory_api_rejects_boolean_custom_id(auth_client):
    create = auth_client.post(
        '/api/user-observatories',
        json={'name': 'Bool Id Obs', 'latitude': 10.0, 'longitude': 10.0},
    )
    assert create.status_code in (200, 201)
    assert create.get_json()['id'] == 1

    response = auth_client.post('/api/last-observatory', json={'source': 'custom', 'id': True})
    assert response.status_code == 400
    assert response.get_json()['error'] == 'Invalid observatory selection'


def test_last_selected_observatory_serialization_roundtrip(auth_client, app):
    with app.app_context():
        current_user = db.session.query(User).filter_by(email='smoketest@example.com').first()

        save_last_selected_observatory(current_user.id, {'source': 'custom', 'id': 123})
        db.session.commit()
        assert get_saved_last_selected_observatory(current_user.id) == {'source': 'custom', 'id': 123}

        save_last_selected_observatory(current_user.id, None)
        db.session.commit()
        assert get_saved_last_selected_observatory(current_user.id) is None


def test_save_last_selected_observatory_does_not_commit_implicitly(auth_client, app, monkeypatch):
    with app.app_context():
        commit_called = {'value': False}

        def fake_commit():
            commit_called['value'] = True

        monkeypatch.setattr('poi_broker.user_settings.db.session.commit', fake_commit)

        current_user = db.session.query(User).filter_by(email='smoketest@example.com').first()
        save_last_selected_observatory(current_user.id, {'source': 'custom', 'id': 123})

        assert commit_called['value'] is False
        db.session.rollback()


def test_get_saved_last_selected_observatory_returns_none_for_invalid_data(auth_client, app):
    with app.app_context():
        current_user = db.session.query(User).filter_by(email='smoketest@example.com').first()
        settings = UserSettings(user_id=current_user.id)
        settings.last_selected_observatory_json = 'not a json string'
        db.session.add(settings)
        db.session.commit()

        assert get_saved_last_selected_observatory(current_user.id) is None

        settings.last_selected_observatory_json = json.dumps({'source': 'custom', 'id': 'not-an-int'})
        db.session.add(settings)
        db.session.commit()

        assert get_saved_last_selected_observatory(current_user.id) is None


def test_get_builtin_observatory_options_is_cached(monkeypatch):
    _get_builtin_observatory_options.cache_clear()

    call_count = {'value': 0}

    def fake_site_names():
        call_count['value'] += 1
        return ['Builtin Observatory']

    monkeypatch.setattr('poi_broker.app.EarthLocation.get_site_names', fake_site_names)

    try:
        first_result = _get_builtin_observatory_options()
        second_result = _get_builtin_observatory_options()

        assert call_count['value'] == 1
        assert first_result == second_result == [
            {'value': 'builtin:Builtin Observatory', 'label': 'Builtin Observatory'},
        ]
    finally:
        _get_builtin_observatory_options.cache_clear()


def test_delete_user_observatory_is_idempotent(auth_client):
    create_resp = auth_client.post(
        '/api/user-observatories',
        json={
            'name': 'Delete Me Twice',
            'latitude': 52.52,
            'longitude': 13.405,
        },
    )
    assert create_resp.status_code in (200, 201)
    payload = create_resp.get_json()
    assert payload is not None
    observatory_id = payload['id']

    first_delete = auth_client.delete(f'/api/user-observatories/{observatory_id}')
    assert first_delete.status_code == 200
    first_payload = first_delete.get_json()
    assert first_payload is not None
    assert first_payload.get('status') == 'ok'

    second_delete = auth_client.delete(f'/api/user-observatories/{observatory_id}')
    assert second_delete.status_code == 200
    second_payload = second_delete.get_json()
    assert second_payload is not None
    assert second_payload.get('status') == 'ok'
    assert second_payload.get('already_deleted') is True
