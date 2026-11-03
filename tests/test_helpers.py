import json
from datetime import date, datetime, timezone

from poi_broker import db
from poi_broker.helpers import (
    object_as_dict,
    result_to_dict,
    safe_serialize,
    serialize_fallback,
)
from poi_broker.models import Ztf


def test_safe_serialize_falls_back_for_non_json_objects():
    payload = {'data': b'test-bytes'}
    result = safe_serialize(payload)

    assert isinstance(result, str)
    assert json.loads(result)['data'] == 'test-bytes'


def test_serialize_fallback_handles_nested_collections():
    obj = {
        'bytes': b'foo',
        'nested': [b'bar', {'inner': b'baz'}],
    }

    serialized = serialize_fallback(obj)
    assert serialized['bytes'] == 'foo'
    assert serialized['nested'][0] == 'bar'
    assert serialized['nested'][1]['inner'] == 'baz'


def test_safe_serialize_replaces_invalid_utf8_bytes():
    result = safe_serialize({'data': b'\xff\xfe'})
    parsed = json.loads(result)
    assert parsed['data'] == '\ufffd\ufffd'


def test_safe_serialize_handles_datetimes():
    payload = {
        'dt': datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc),
        'd': date(2026, 8, 29),
        'nested': [datetime(2026, 1, 1)],
    }

    result = safe_serialize(payload)

    assert json.loads(result) == {
        'dt': '2026-08-29T12:00:00+00:00',
        'd': '2026-08-29',
        'nested': ['2026-01-01T00:00:00'],
    }


def test_object_as_dict_and_result_to_dict_with_model_instance(app):
    sample = Ztf(alert_id='test-1', date_alert_mjd=59000.5)

    as_dict = object_as_dict(sample)
    assert as_dict['alert_id'] == 'test-1'
    assert as_dict['date_alert_mjd'] == 59000.5

    results = result_to_dict([sample])
    assert isinstance(results, list)
    assert results[0]['alert_id'] == 'test-1'
    assert results[0]['date_alert_mjd'] == 59000.5
