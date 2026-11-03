import logging
import pytest
from astropy import units as u
from astropy.coordinates import EarthLocation


def test_timezone_finder_accessor_is_cached(monkeypatch):
    import poi_broker.observing_tool as observing_tool

    observing_tool._get_timezone_finder.cache_clear()
    constructed = []

    class FakeTimezoneFinder:
        def __init__(self):
            constructed.append(self)

    monkeypatch.setattr(observing_tool, 'TimezoneFinder', FakeTimezoneFinder)

    first = observing_tool._get_timezone_finder()
    second = observing_tool._get_timezone_finder()

    assert first is second
    assert constructed == [first]

    observing_tool._get_timezone_finder.cache_clear()


def test_query_observing_plot_requires_all_parameters(client):
    response = client.get('/query_observing_plot')

    assert response.status_code == 400
    assert 'Missing required query parameters' in response.get_data(as_text=True)


def test_query_observing_plot_rejects_invalid_parameter_format(client):
    response = client.get(
        '/query_observing_plot',
        query_string={
            'obs_loc': 'Any Observatory',
            'obs_date': '2024-99-99',
            'obs_tz': 'option_utc',
            'ra': 'not-a-number',
            'dec': 'not-a-number',
        },
    )

    assert response.status_code == 400
    assert 'Invalid parameter format' in response.get_data(as_text=True)


def test_query_observing_plot_rejects_non_calendar_date_with_numeric_coords(client):
    response = client.get(
        '/query_observing_plot',
        query_string={
            'obs_loc': 'Palomar',
            'obs_date': 'not-a-date',
            'obs_tz': 'option_utc',
            'ra': '10',
            'dec': '20',
        },
    )
    assert response.status_code == 400
    assert response.is_json
    assert 'Invalid parameter format' in response.get_json().get('error', '')


def test_query_observing_plot_rejects_impossible_calendar_date(client):
    response = client.get(
        '/query_observing_plot',
        query_string={
            'obs_loc': 'Palomar',
            'obs_date': '2024-99-99',
            'obs_tz': 'option_utc',
            'ra': '10',
            'dec': '20',
        },
    )
    assert response.status_code == 400
    assert response.is_json
    assert 'Invalid parameter format' in response.get_json().get('error', '')


def test_query_observing_plot_not_visible_returns_message(client, monkeypatch):
    import poi_broker.observing_tool as observing_tool

    location = EarthLocation(lat=19.8261 * u.deg, lon=-155.4700 * u.deg, height=4145 * u.m)

    monkeypatch.setattr(observing_tool.EarthLocation, 'of_site', lambda site_name: location)
    monkeypatch.setattr(observing_tool.TimezoneFinder, 'timezone_at', lambda self, lng, lat: 'UTC')
    from datetime import timezone
    monkeypatch.setattr(observing_tool, 'ZoneInfo', lambda tz_name: timezone.utc)

    response = client.get(
        '/query_observing_plot',
        query_string={
            'obs_loc': 'Keck Observatory',
            'obs_date': '2025-01-01',
            'obs_tz': 'option_utc',
            'ra': '101.28715533',
            'dec': '-80',
        },
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert response.is_json
    assert 'not visible' in payload['message']
    assert 'image' not in payload
    assert 'moonHtml' not in payload


def test_query_observing_plot_not_visible_southern_site(client, monkeypatch):
    """Southern observatory + northern object never rises (|lat - dec| >= 90)."""
    import poi_broker.observing_tool as observing_tool

    location = EarthLocation(lat=-30.0 * u.deg, lon=-70.0 * u.deg, height=1000 * u.m)

    monkeypatch.setattr(observing_tool.EarthLocation, 'of_site', lambda site_name: location)
    monkeypatch.setattr(observing_tool.TimezoneFinder, 'timezone_at', lambda self, lng, lat: 'UTC')
    from datetime import timezone
    monkeypatch.setattr(observing_tool, 'ZoneInfo', lambda tz_name: timezone.utc)

    response = client.get(
        '/query_observing_plot',
        query_string={
            'obs_loc': 'CTIO',
            'obs_date': '2025-01-01',
            'obs_tz': 'option_utc',
            'ra': '101.28715533',
            'dec': '70',
        },
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert response.is_json
    assert 'not visible' in payload['message']
    assert 'image' not in payload
    assert 'moonHtml' not in payload


def test_query_observing_plot_returns_400_for_unknown_observatory(client, monkeypatch):
    import poi_broker.observing_tool as observing_tool

    def fake_of_site(_site_name):
        raise Exception('site unknown')

    monkeypatch.setattr(observing_tool.EarthLocation, 'of_site', fake_of_site)

    response = client.get(
        '/query_observing_plot',
        query_string={
            'obs_loc': 'Unknown Observatory',
            'obs_date': '2025-01-01',
            'obs_tz': 'option_utc',
            'ra': '101.28715533',
            'dec': '16.71611586',
        },
    )

    assert response.status_code == 400
    assert 'Unknown observatory location' in response.get_data(as_text=True)


@pytest.mark.slow
def test_query_observing_plot_generates_image_and_moon_panel(client, monkeypatch):
    import poi_broker.observing_tool as observing_tool

    location = EarthLocation(lat=19.8261 * u.deg, lon=-155.4700 * u.deg, height=4145 * u.m)

    monkeypatch.setattr(observing_tool.EarthLocation, 'of_site', lambda site_name: location)
    monkeypatch.setattr(observing_tool.TimezoneFinder, 'timezone_at', lambda self, lng, lat: 'UTC')
    from datetime import timezone
    monkeypatch.setattr(observing_tool, 'ZoneInfo', lambda tz_name: timezone.utc)

    response = client.get(
        '/query_observing_plot',
        query_string={
            'obs_loc': 'Keck Observatory',
            'obs_date': '2025-01-01',
            'obs_tz': 'option_utc',
            'ra': '101.28715533',
            'dec': '16.71611586',
        },
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert response.is_json
    assert payload['image'].startswith('data:image/png;base64,')
    assert payload.get('moonHtml') or payload.get('moonMessage') == 'Moon down'
    assert payload.get('moonHtml') != 'Moon down'
    assert 'message' not in payload


@pytest.mark.slow
def test_query_observing_plot_custom_observatory_recovers_invalid_timezone(auth_client, app, monkeypatch):
    from poi_broker import db
    from poi_broker.models import User, UserObservatory, UserSettings

    with app.app_context():
        user = User.query.filter_by(email='smoketest@example.com').first()
        assert user is not None
        row = UserObservatory(
            user_id=user.id,
            name='Custom Bad TZ',
            latitude=52.52,
            longitude=13.405,
            timezone_name='Bad/Timezone',
        )
        db.session.add(row)
        db.session.commit()
        observatory_id = row.id

    import poi_broker.observing_tool as observing_tool

    monkeypatch.setattr(observing_tool.TimezoneFinder, 'timezone_at', lambda self, lng, lat: 'Europe/Berlin')

    response = auth_client.get(
        '/query_observing_plot',
        query_string={
            'obs_loc': f'custom:{observatory_id}',
            'obs_date': '2025-01-01',
            'obs_tz': 'option_utc',
            'ra': '101.28715533',
            'dec': '16.71611586',
        },
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert response.is_json
    assert payload['image'].startswith('data:image/png;base64,')

    with app.app_context():
        user = User.query.filter_by(email='smoketest@example.com').first()
        settings = UserSettings.query.filter_by(user_id=user.id).first()
        assert settings is None or settings.last_selected_observatory_json is None


@pytest.mark.slow
def test_query_observing_plot_builtin_valid_iana_resolution_failure_logs_diagnostics(client, monkeypatch, caplog):
    import poi_broker.observing_tool as observing_tool

    location = EarthLocation(lat=-23.029 * u.deg, lon=-67.755 * u.deg, height=5000 * u.m)

    monkeypatch.setattr(observing_tool.EarthLocation, 'of_site', lambda site_name: location)
    monkeypatch.setattr(observing_tool.TimezoneFinder, 'timezone_at', lambda self, lng, lat: 'America/Santiago')

    def fake_zoneinfo(_timezone_name):
        raise RuntimeError('simulated zoneinfo resolution failure')

    monkeypatch.setattr(observing_tool, 'ZoneInfo', fake_zoneinfo)

    caplog.set_level(logging.WARNING, logger='poi_broker.observing_tool')

    response = client.get(
        '/query_observing_plot',
        query_string={
            'obs_loc': 'builtin:ALMA',
            'obs_date': '2025-01-01',
            'obs_tz': 'option_utc',
            'ra': '101.28715533',
            'dec': '16.71611586',
        },
    )

    text = response.get_data(as_text=True)
    logs = caplog.text

    assert response.status_code == 400
    assert 'Failed to determine timezone for selected observatory.' in text
    assert 'Failed to resolve timezone via ZoneInfo' in logs
    assert 'America/Santiago' in logs
    assert 'simulated zoneinfo resolution failure' in logs


@pytest.mark.slow
def test_query_observing_plot_polar_summer_returns_moon_message(client, monkeypatch):
    """Midnight-sun dates have no night samples; must not 500 on empty np.max."""
    import poi_broker.observing_tool as observing_tool
    from datetime import timezone

    location = EarthLocation(lat=78.0 * u.deg, lon=16.0 * u.deg, height=10 * u.m)
    monkeypatch.setattr(observing_tool.EarthLocation, 'of_site', lambda site_name: location)
    monkeypatch.setattr(observing_tool.TimezoneFinder, 'timezone_at', lambda self, lng, lat: 'UTC')
    monkeypatch.setattr(observing_tool, 'ZoneInfo', lambda tz_name: timezone.utc)

    response = client.get(
        '/query_observing_plot',
        query_string={
            'obs_loc': 'NyAlesund',
            'obs_date': '2025-06-21',
            'obs_tz': 'option_utc',
            'ra': '101.28715533',
            'dec': '70',
        },
    )
    payload = response.get_json()
    assert response.status_code == 200, response.get_data(as_text=True)
    assert 'error' not in payload
    assert payload['image'].startswith('data:image/png;base64,')
    assert payload.get('moonMessage') == 'No nighttime hours on this date'
