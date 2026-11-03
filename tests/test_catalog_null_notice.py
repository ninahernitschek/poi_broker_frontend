"""Missing-value notices require confirmed NULL rows, not an unknown count."""
from contextlib import contextmanager

from flask import template_rendered
import pytest

from poi_broker import db
from poi_broker.models import Ztf
from poi_broker.services import catalog_list


def _seed(app, present=101, missing=0):
    with app.app_context():
        catalog_list.clear_catalog_total_cache()
        for i in range(present + missing):
            db.session.add(Ztf(
                alert_id=f'ztf_candidate:{9100000000000000000+i}',
                locus_id=f'NOTICE_L{i}', date_alert_mjd=61000.0+i,
                ztf_object_id=f'Z{i}', ant_passband='g',
                locus_ra=10.0, locus_dec=20.0,
                ant_mag_corrected=18.0+i/100 if i < present else None,
            ))
        db.session.commit()


@contextmanager
def _capture(app):
    contexts = []
    def record(sender, template, context, **extra):
        if template.name == 'main.html':
            contexts.append(context)
    template_rendered.connect(record, app)
    try:
        yield contexts
    finally:
        template_rendered.disconnect(record, app)


@pytest.mark.parametrize('filtered', [False, True])
def test_empty_probe_hides_notice_on_multi_page_results(app, client, filtered):
    _seed(app)
    params = {'sort__ant_mag_corrected': 'asc'}
    if filtered:
        params['magpsf'] = '>0'
    with _capture(app) as contexts:
        control = client.get('/', query_string={**params, 'ztf_object_id': 'Z0'})
        assert control.status_code == 200
        assert 'Alerts without magnitude' not in control.get_data(as_text=True)
        assert contexts[-1]['sort_null_count'] == 0
        response = client.get('/', query_string=params)
        assert response.status_code == 200
        assert contexts[-1]['has_next']
        assert len(contexts[-1]['table']) == 100
        assert contexts[-1]['nulls_href'] is None
        html = response.get_data(as_text=True)
        assert 'Alerts without magnitude are listed after the others' not in html
        assert 'jump to them' not in html


def test_timed_out_probe_hides_notice_and_preserves_unknown_count(app, client, monkeypatch):
    _seed(app)
    actual_query = catalog_list._budgeted_query
    probes = []
    def timeout_probe(query, seconds, *, scalar=False):
        if scalar:
            return actual_query(query, seconds, scalar=True)
        probes.append(query)
        return None
    monkeypatch.setattr(catalog_list, '_budgeted_query', timeout_probe)
    with _capture(app) as contexts:
        response = client.get('/', query_string={'sort__ant_mag_corrected': 'asc', 'magpsf': '>0'})
        assert response.status_code == 200
        assert probes
        assert contexts[-1]['sort_null_count'] is None
        assert contexts[-1]['nulls_href'] is None
        assert 'Alerts without magnitude are listed after the others' not in response.get_data(as_text=True)


@pytest.mark.parametrize('present', [1, 101])
def test_confirmed_missing_values_keep_notice_and_working_jump(app, client, present):
    _seed(app, present=present, missing=3)
    with _capture(app) as contexts:
        response = client.get('/?sort__ant_mag_corrected=asc')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'jump to them' in html
        href = contexts[-1]['nulls_href']
        assert href
        if present == 1:
            assert contexts[-1]['sort_null_count'] == 3
            assert '3 alerts have no magnitude' in html
        else:
            assert contexts[-1]['sort_null_count'] is None
            assert 'Alerts without magnitude are listed after the others' in html
        jumped = client.get(href)
        assert jumped.status_code == 200
        assert len(contexts[-1]['table']) == 3
        assert all(row.ant_mag_corrected is None for row in contexts[-1]['table'])
