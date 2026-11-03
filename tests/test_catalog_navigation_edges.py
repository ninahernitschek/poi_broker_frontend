"""HTTP-level catalog edges after removing mandatory segment counts."""

from contextlib import contextmanager
import re

from flask import template_rendered
import pytest
from sqlalchemy import event

from poi_broker import db
from poi_broker.models import Classification, Ztf
from poi_broker.services import catalog_list
from poi_broker.services.catalog_query import build_catalog_query


@contextmanager
def capture(app):
    contexts, statements = [], []
    def rendered(sender, template, context, **extra):
        if template.name == 'main.html':
            contexts.append(context)
    def executed(conn, cursor, sql, params, context, many):
        statements.append(sql)
    with app.app_context():
        engine = db.engine
    template_rendered.connect(rendered, app)
    event.listen(engine, 'before_cursor_execute', executed)
    try:
        yield contexts, statements
    finally:
        template_rendered.disconnect(rendered, app)
        event.remove(engine, 'before_cursor_execute', executed)


@pytest.mark.parametrize('desc', [False, True])
@pytest.mark.parametrize('matches', ['empty', 'present', 'null', 'mixed'])
def test_classification_filter_can_leave_either_segment_or_neither(app, client, desc, matches):
    selected = {'empty': [], 'present': [0, 1], 'null': [4, 5], 'mixed': [0, 4, 5]}[matches]
    with app.app_context():
        catalog_list.clear_catalog_total_cache()
        for i in range(8):
            alert_id = f'ztf_candidate:{8920000000000000000 + i}'
            db.session.add(Ztf(alert_id=alert_id, date_alert_mjd=61000 + i,
                locus_id=f'L{i}', ztf_object_id=f'Z{i}', ant_passband='g',
                locus_ra=10.0, locus_dec=20.0, ant_mag_corrected=0.0 if i < 4 else None))
            if i != 7:
                db.session.add(Classification(alert_id=alert_id,
                    prob_class='sn' if i in selected else 'lpv'))
        db.session.commit()
    present = sorted((i for i in selected if i < 4), reverse=desc)
    null = sorted((i for i in selected if i >= 4), reverse=desc)
    ids = lambda indices: [f'ztf_candidate:{8920000000000000000 + i}' for i in indices]
    base = {'prob_class': 'sn', 'sort__ant_mag_corrected': 'desc' if desc else 'asc'}
    with capture(app) as (contexts, statements):
        for params in (base, {**base, 'last': '1'}):
            response = client.get('/', query_string=params)
            assert response.status_code == 200
            assert f'<span id="catalog-rows-value">{len(selected)}</span>' in response.get_data(as_text=True)
            ctx = contexts[-1]
            assert [row.alert_id for row in ctx['table']] == ids(present + null)
            assert not ctx['has_next'] and not ctx['has_prev']
            assert bool(ctx['nulls_href']) == bool(null)
        jumped = client.get('/', query_string={**base, 'nulls': '1'})
        assert jumped.status_code == (200 if null else 404)
        if null:
            assert [row.alert_id for row in contexts[-1]['table']] == ids(null)
            assert contexts[-1]['has_prev'] == bool(present)
            assert f'<span id="catalog-rows-value">{len(selected)}</span>' in jumped.get_data(as_text=True)
        assert not any(re.search(r'\bcount\s*\(', sql, re.I) for sql in statements)
    response = client.get('/api/catalog-count', query_string={'prob_class': 'sn'})
    assert response.status_code == 200
    assert response.get_json() == {'count': len(selected)}


@pytest.mark.parametrize('desc', [False, True])
@pytest.mark.parametrize('present_size', [99, 100, 101])
@pytest.mark.parametrize('sort', ['ant_mag_corrected', 'ztf_object_id'])
def test_http_round_trip_with_ties_zero_or_empty_text_at_page_boundary(app, client, desc, present_size, sort):
    with app.app_context():
        catalog_list.clear_catalog_total_cache()
        for i in range(201):
            fields = dict(alert_id=f'ztf_candidate:{8930000000000000000 + i}',
                date_alert_mjd=61000 + i, locus_id=f'L{i:03}',
                ztf_object_id='', ant_passband='g', locus_ra=10.0,
                locus_dec=20.0, ant_mag_corrected=0.0)
            if i >= present_size:
                fields[sort] = None
            db.session.add(Ztf(**fields))
        db.session.commit()
    ids = lambda indices: [f'ztf_candidate:{8930000000000000000 + i}' for i in indices]
    present = sorted(range(present_size), reverse=desc)
    missing = sorted(range(present_size, 201), reverse=desc)
    oracle = ids(present + missing)
    base = {'ant_passband': 'g', f'sort__{sort}': 'desc' if desc else 'asc'}
    with capture(app) as (contexts, statements):
        def request(url='/', params=None, numbered=False):
            statements.clear()
            response = client.get(url, query_string=params)
            assert response.status_code == 200
            ctx = contexts[-1]
            rows = [row.alert_id for row in ctx['table']]
            assert len(rows) == len(set(rows))
            label = re.search(r'id="catalog-rows-value">([^<]+)', response.get_data(as_text=True))[1]
            if label.endswith('+'):
                assert int(label[:-1]) < len(oracle)
            else:
                assert int(label) == len(oracle)
            if not numbered:
                assert not any(re.search(r'\bcount\s*\(', sql, re.I) for sql in statements)
            return ctx, rows
        ctx, rows = request(params=base)
        walked = list(rows)
        assert not ctx['has_prev']
        for _ in range(3):
            if not ctx['has_next']:
                break
            ctx, rows = request(ctx['next_href'])
            walked.extend(rows)
        assert not ctx['has_next']
        assert walked == oracle
        ctx, rows = request(params={**base, 'last': '1'})
        assert rows == oracle[-100:]
        backward = list(rows)
        for _ in range(3):
            if not ctx['has_prev']:
                break
            ctx, rows = request(ctx['prev_href'])
            backward = rows + backward
        assert not ctx['has_prev']
        assert backward == oracle
        ctx, rows = request(params={**base, 'nulls': '1'})
        null_walk = list(rows)
        assert ctx['has_prev']
        if ctx['has_next']:
            ctx, rows = request(ctx['next_href'])
            null_walk.extend(rows)
        assert not ctx['has_next']
        assert null_walk == ids(missing)
        for page in (1, 2, 3):
            _, rows = request(params={**base, 'page': str(page)}, numbered=True)
            assert rows == oracle[(page - 1) * 100:page * 100]


def test_slow_count_suppression_is_sort_independent_and_expires(app, monkeypatch):
    clock = {'now': 1000.0}
    calls = []
    def timeout(query, seconds, **kwargs):
        calls.append(seconds)
        return None
    monkeypatch.setattr(catalog_list.time, 'monotonic', lambda: clock['now'])
    monkeypatch.setattr(catalog_list, '_budgeted_query', timeout)
    with app.app_context():
        catalog_list.clear_catalog_total_cache()
        a = build_catalog_query({'ant_passband': 'g', 'sort__ant_mag_corrected': 'asc'})
        b = build_catalog_query({'ant_passband': 'g', 'sort__ztf_object_id': 'desc'})
        assert catalog_list.optional_catalog_count(a) is None
        assert not catalog_list.count_is_available(b)
        assert catalog_list.optional_catalog_count(b) is None
        assert calls == [2.0]
        clock['now'] += catalog_list.CATALOG_TOTAL_TTL_SECONDS + 1
        assert catalog_list.count_is_available(b)
        assert catalog_list.optional_catalog_count(b) is None
        assert calls == [2.0, 2.0]
