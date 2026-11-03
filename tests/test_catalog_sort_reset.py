"""Exercise sort URL changes using the rendered page's actual JavaScript."""
import json
import shutil
import subprocess

import pytest

from poi_broker import db
from poi_broker.models import Ztf
from poi_broker.services.catalog_query import PAGINATION_KEYS


def _sort_click(html, search):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required to execute the sort handler')
    handler = html.split("$sortable.on('click', function () {", 1)[1].split('\n      });', 1)[0]
    script = '''
const window = {location: {search: SEARCH}, URLSearchParams};
const element = {attr: () => 'ant_mag_corrected', hasClass: () => false,
                 addClass: () => element, removeClass: () => element};
const $sortable = element;
const $ = () => element;
(function() { HANDLER }).call(element);
console.log(window.location.search);
'''.replace('SEARCH', json.dumps(search)).replace('HANDLER', handler)
    result = subprocess.run([node, '-'], input=script, text=True,
                            capture_output=True, check=True, timeout=10)
    return result.stdout.strip()


def test_sort_change_clears_null_jump_and_renders_matching_rows(app, client):
    with app.app_context():
        for i in range(2):
            db.session.add(Ztf(
                alert_id=f'ztf_candidate:{i:019d}', locus_id=f'L{i}',
                date_alert_mjd=60000.0+i, ztf_object_id=None if i == 0 else 'SORT_CONTROL',
                ant_passband='g', locus_ra=10.0, locus_dec=20.0, ant_mag_corrected=18.0+i,
            ))
        db.session.commit()
    jump = client.get('/?sort__ztf_object_id=asc&nulls=1&ant_passband=g')
    assert jump.status_code == 200
    html = jump.get_data(as_text=True)
    assert _sort_click(html, '?sort__ztf_object_id=asc&ant_passband=g') == 'ant_passband=g&sort__ant_mag_corrected=asc'
    destination = _sort_click(html, '?sort__ztf_object_id=asc&nulls=1&ant_passband=g')
    response = client.get('/?' + destination)
    assert response.status_code == 200
    assert b'SORT_CONTROL' in response.data


@pytest.mark.parametrize('key', sorted(PAGINATION_KEYS))
def test_sort_change_clears_every_pagination_key_and_preserves_filters(client, key):
    html = client.get('/').get_data(as_text=True)
    destination = _sort_click(html, f'?sort__date=desc&{key}=stale&ant_passband=g')
    assert destination == 'ant_passband=g&sort__ant_mag_corrected=asc'
