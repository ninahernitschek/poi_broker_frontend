"""Counts must never be prerequisites for broad filtered pagination."""

import re

import pytest
from sqlalchemy import event, func

from poi_broker import db
from poi_broker.models import Ztf
from poi_broker.services import catalog_list
from poi_broker.services.catalog_query import build_catalog_query


def seed(n=8, present=3):
    for i in range(n):
        db.session.add(Ztf(
            alert_id=f'ztf_candidate:{8800000000000000000 + i}',
            date_alert_mjd=61000.0 + i, locus_id=f'L{i}',
            ztf_object_id=f'Z{i}', ant_passband='g',
            ant_mag_corrected=float(i % 2) if i < present else None,
        ))
    db.session.commit()


@pytest.mark.parametrize('suffix', ['', '&last=1', '&nulls=1',
    '&before_v=0&before_alert_id=ztf_candidate:8800000000000000000&before_locus_id=L0'])
@pytest.mark.parametrize('sort', ['ant_mag_corrected', 'locus_ra', 'locus_dec', 'ztf_object_id'])
def test_broad_nullable_requests_execute_no_counts(app, client, monkeypatch, suffix, sort):
    monkeypatch.setattr('poi_broker.app.PAGE_SIZE', 2)
    monkeypatch.setattr(catalog_list, 'PAGE_SIZE', 2)
    statements = []
    with app.app_context():
        catalog_list.clear_catalog_total_cache()
        seed()
        if sort == 'ztf_object_id':
            db.session.query(Ztf).filter(Ztf.locus_id.in_(['L3', 'L4'])).update({'ztf_object_id': None})
            db.session.commit()
        engine = db.engine
        def record(conn, cursor, statement, params, context, many):
            statements.append(statement)
        event.listen(engine, 'before_cursor_execute', record)
    try:
        url = f'/?ant_passband=g&sort__{sort}=asc' + suffix
        for _ in range(2):
            with app.app_context():
                catalog_list.clear_catalog_total_cache()
            response = client.get(url)
            assert response.status_code == 200
        assert not any(re.search(r'\bcount\s*\(', s, re.I) for s in statements)
        if suffix == '&nulls=1':
            probes = [s for s in statements if 'IS NOT NULL' in s]
            assert probes
            assert all('ORDER BY' not in s for s in probes)
    finally:
        event.remove(engine, 'before_cursor_execute', record)


@pytest.mark.parametrize('desc', [False, True])
def test_numbered_pages_without_segment_counts_match_independent_order(app, desc):
    with app.app_context():
        for present in (0, 1, 2, 3, 4, 8):
            db.session.query(Ztf).delete()
            seed(present=present)
            q = db.session.query(Ztf)
            order = lambda c: c.desc() if desc else c.asc()
            oracle = q.order_by(Ztf.ant_mag_corrected.is_(None),
                order(Ztf.ant_mag_corrected), order(Ztf.alert_id), order(Ztf.locus_id)).all()
            for page in range(1, 7):
                rows, has_next = catalog_list.fetch_page(q, page, page_size=2,
                    sort_column=Ztf.ant_mag_corrected, sort_desc=desc)
                offset = (page - 1) * 2
                assert [r.alert_id for r in rows] == [r.alert_id for r in oracle[offset:offset + 2]]
                assert has_next == (offset + 2 < len(oracle))


def test_all_null_jump_has_no_previous_page_without_counts(app):
    with app.app_context():
        seed(present=0)
        rows, _, has_prev = catalog_list.fetch_keyset(db.session.query(Ztf),
            sort_column=Ztf.ant_mag_corrected, cursor=None,
            direction='nulls', sort_desc=False, page_size=2)
        assert rows
        assert not has_prev


def test_expired_display_counts_do_not_suppress_new_null_rows(app, client, monkeypatch):
    monkeypatch.setattr('poi_broker.app.PAGE_SIZE', 2)
    monkeypatch.setattr(catalog_list, 'PAGE_SIZE', 2)
    with app.app_context():
        catalog_list.clear_catalog_total_cache()
        seed(n=2, present=2)
        build = build_catalog_query({'sort__ant_mag_corrected': 'asc'})
        assert catalog_list.cached_segment_counts(build) == (2, 0)
        db.session.add(Ztf(alert_id='ztf_candidate:9999999999999999999',
            locus_id='Lnew', date_alert_mjd=62000, ant_mag_corrected=None))
        db.session.commit()
    response = client.get('/?sort__ant_mag_corrected=asc&last=1')
    assert b'ztf_candidate:9999999999999999999' in response.data


def test_optional_timeout_is_remembered_and_button_hidden(app, client, monkeypatch):
    with app.app_context():
        catalog_list.clear_catalog_total_cache()
        seed(n=101)
    calls = []
    def timeout(query, seconds, **kwargs):
        calls.append(seconds)
        return None
    monkeypatch.setattr(catalog_list, '_budgeted_query', timeout)
    assert client.get('/api/catalog-count?ant_passband=g').status_code == 422
    assert client.get('/api/catalog-count?ant_passband=g').status_code == 422
    assert calls == [2.0]
    response = client.get('/?ant_passband=g')
    assert b'>100+</span>' in response.data
    assert b'id="btn-catalog-count"' not in response.data
    with app.app_context():
        assert catalog_list.count_is_available(build_catalog_query({'ant_passband': 'R'}))


def test_optional_counts_can_be_disabled(app, client):
    app.config['CATALOG_COUNT_BUDGET_SECONDS'] = 0
    with app.app_context():
        seed(n=101)
    assert b'id="btn-catalog-count"' not in client.get('/?ant_passband=g').data
    assert client.get('/api/catalog-count?ant_passband=g').status_code == 422


def test_budget_interrupts_sqlite_and_cleans_up_pooled_connection(app, monkeypatch):
    with app.app_context():
        seed(n=2000)
        query = db.session.query(Ztf).with_entities(func.count(Ztf.alert_id))
        ticks = iter([0.0])
        with monkeypatch.context() as clock:
            clock.setattr(catalog_list.time, 'perf_counter', lambda: next(ticks, 1.0))
            assert catalog_list._budgeted_query(query, 0.01, scalar=True) is None
        assert catalog_list._budgeted_query(query, 1.0, scalar=True) == 2000
        assert db.session.query(Ztf).count() == 2000


def test_small_null_tail_does_not_claim_100_plus_after_metadata_timeout(app, client, monkeypatch):
    with app.app_context():
        seed(n=2, present=1)
    monkeypatch.setattr(catalog_list, '_budgeted_query', lambda *a, **k: None)
    html = client.get('/?sort__ant_mag_corrected=asc&nulls=1').get_data(as_text=True)
    assert '<span id="catalog-rows-value">1+</span>' in html
    assert '<span id="catalog-rows-value">100+</span>' not in html


@pytest.mark.parametrize('direction', ['last', 'nulls'])
def test_complete_tail_is_exact_even_when_metadata_is_unavailable(app, client, monkeypatch, direction):
    with app.app_context():
        seed(n=100, present=0)
    monkeypatch.setattr(catalog_list, '_budgeted_query', lambda *a, **k: None)
    html = client.get(f'/?ant_passband=g&sort__ant_mag_corrected=asc&{direction}=1').get_data(as_text=True)
    assert '<span id="catalog-rows-value">100</span>' in html
    assert '100 alerts have no magnitude' in html
