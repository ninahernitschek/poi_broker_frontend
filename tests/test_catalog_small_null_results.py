"""Small mixed-value catalogs must never acquire a fictitious 100+ total."""

import re

from flask import template_rendered
import pytest

from poi_broker import db
from poi_broker.models import Classification, Ztf
from poi_broker.services.catalog_list import clear_catalog_total_cache


@pytest.mark.parametrize('missing_all_nullable', [False, True])
@pytest.mark.parametrize('direction', ['asc', 'desc'])
@pytest.mark.parametrize('filtered', [False, True])
def test_one_populated_row_and_ten_missing_rows_report_eleven(
    app, client, missing_all_nullable, direction, filtered,
):
    with app.app_context():
        clear_catalog_total_cache()
        ids = [f'ztf_candidate:{8900000000000000000 + i}' for i in range(11)]
        for i, alert_id in enumerate(ids):
            missing = i > 0
            all_missing = missing and missing_all_nullable
            db.session.add(Ztf(
                alert_id=alert_id, date_alert_mjd=61000.0 + i, locus_id=f'L{i}',
                ztf_object_id=None if all_missing else f'Z{i}',
                ant_passband=None if all_missing else 'g',
                locus_ra=None if all_missing else 10.0,
                locus_dec=None if all_missing else 20.0,
                ant_mag_corrected=None if missing else 18.0,
            ))
            if not all_missing:
                db.session.add(Classification(alert_id=alert_id, prob_class='sn'))
        db.session.commit()
    base = {'sort__ant_mag_corrected': direction}
    if filtered:
        base['date_alert_mjd'] = '>60000'
    rendered = []
    def record(sender, template, context, **extra):
        if template.name == 'main.html':
            rendered.append(context)
    template_rendered.connect(record, app)
    try:
        missing_ids = sorted(ids[1:], reverse=direction == 'desc')
        def check(params, expected_ids, has_prev, has_next):
            response = client.get('/', query_string=params)
            assert response.status_code == 200
            html = response.get_data(as_text=True)
            assert '<span id="catalog-rows-value">11</span>' in html
            assert '<span id="catalog-rows-value">100+</span>' not in html
            assert 'id="btn-catalog-count"' not in html
            context = rendered[-1]
            assert [row.alert_id for row in context['table']] == expected_ids
            assert context['has_prev'] is has_prev
            assert context['has_next'] is has_next
            return context, html
        first, html = check(base, [ids[0]] + missing_ids, False, False)
        assert '10 alerts have no magnitude' in html
        check({**base, 'last': '1'}, [ids[0]] + missing_ids, False, False)
        jumped, _ = check({**base, 'nulls': '1'}, missing_ids, True, False)
        # Follow the actual rendered cursor links back to the populated row,
        # then forwards across the boundary; counts remain total-result counts.
        prev = client.get(jumped['prev_href'])
        assert prev.status_code == 200
        prev_html = prev.get_data(as_text=True)
        assert '<span id="catalog-rows-value">11</span>' in prev_html
        previous = rendered[-1]
        assert [row.alert_id for row in previous['table']] == [ids[0]]
        assert not previous['has_prev']
        assert previous['has_next']
        next_response = client.get(previous['next_href'])
        assert next_response.status_code == 200
        assert '<span id="catalog-rows-value">11</span>' in next_response.get_data(as_text=True)
        assert [row.alert_id for row in rendered[-1]['table']] == missing_ids
    finally:
        template_rendered.disconnect(record, app)


@pytest.mark.parametrize('null_rows', [99, 100, 101])
def test_mixed_results_use_100_plus_only_when_more_than_100_exist(app, client, null_rows):
    with app.app_context():
        clear_catalog_total_cache()
        for i in range(null_rows + 1):
            db.session.add(Ztf(
                alert_id=f'ztf_candidate:{8910000000000000000 + i}',
                date_alert_mjd=61000.0 + i, locus_id=f'L{i}',
                ztf_object_id=f'Z{i}', ant_passband='g',
                locus_ra=10.0, locus_dec=20.0,
                ant_mag_corrected=18.0 if i == 0 else None,
            ))
        db.session.commit()
    expected_label = '100' if null_rows == 99 else '100+'
    base = {'sort__ant_mag_corrected': 'asc', 'ant_passband': 'g'}
    for navigation in ({}, {'last': '1'}, {'nulls': '1'}):
        response = client.get('/', query_string={**base, **navigation})
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert f'<span id="catalog-rows-value">{expected_label}</span>' in html
        displayed_ids = re.findall(r'<td>(ztf_candidate:\d+)</td>', html)
        expected_rows = min(null_rows if 'nulls' in navigation else null_rows + 1, 100)
        assert len(displayed_ids) == expected_rows
        assert len(set(displayed_ids)) == expected_rows
