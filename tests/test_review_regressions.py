"""Regression tests from the 2026-09-27 branch review (repro-gate round).

Every test here asserts the *desired* behavior and failed before the matching
fix was applied. Entries are cross-referenced from changelog.md.
"""

import sqlite3

HUGE_INT = 99999999999999999999  # > 2**63-1: cannot bind as a SQLite INTEGER


def _make_nested_rules_json(depth: int) -> str:
    inner = '{"field": "featuretable.ant_mag_corrected", "operator": "greater", "value": 1}'
    return '{"condition": "AND", "rules": [' * depth + inner + ']}' * depth


# --- Integer overflow 500s (client sends an int beyond SQLite's INTEGER range) ---


def test_catalog_page_beyond_int64_is_rejected(client):
    """/ is public; a huge page used to raise OverflowError -> HTML 500."""
    response = client.get('/?page=99999999999999999999')
    assert response.status_code in (400, 404), f'got {response.status_code}'


def test_export_status_huge_task_id_is_rejected(auth_client):
    response = auth_client.get(f'/export/status/{HUGE_INT}')
    assert response.status_code in (400, 404), f'got {response.status_code}'


def test_export_download_huge_task_id_is_rejected(auth_client):
    response = auth_client.get(f'/export/download/{HUGE_INT}')
    assert response.status_code in (400, 404), f'got {response.status_code}'


def test_filter_bookmark_delete_huge_id_is_rejected(auth_client):
    response = auth_client.delete(f'/api/filter-bookmarks/{HUGE_INT}')
    assert response.status_code in (400, 404), f'got {response.status_code}'


def test_user_observatory_delete_huge_id_is_rejected(auth_client):
    response = auth_client.delete(f'/api/user-observatories/{HUGE_INT}')
    assert response.status_code in (400, 404), f'got {response.status_code}'


def test_favorites_huge_group_id_is_rejected(auth_client):
    response = auth_client.get(f'/api/favorites?groupId={HUGE_INT}')
    assert response.status_code < 500, f'got {response.status_code}'


# --- Query-builder payload robustness (login-required endpoints) ---


def test_preview_query_rejects_oversized_in_list(auth_client):
    """2000 items exceeds MAX_IN_LIST_ITEMS (1000) but stays under the 1 MiB
    body cap, so the response exercises the translator's cap -> 400."""
    rules = {
        'condition': 'AND',
        'rules': [
            {
                'field': 'featuretable.alert_id',
                'operator': 'in',
                'value': [f'ztf_candidate:{i:018d}' for i in range(2_000)],
            }
        ],
    }
    response = auth_client.post('/api/preview-query', json=rules)
    assert response.status_code == 400, f'got {response.status_code}'


def test_export_query_rejects_oversized_in_list(auth_client):
    rules = {
        'condition': 'AND',
        'rules': [
            {
                'field': 'featuretable.alert_id',
                'operator': 'not_in',
                'value': [f'ztf_candidate:{i:018d}' for i in range(2_000)],
            }
        ],
    }
    response = auth_client.post('/api/export-query', json=rules)
    assert response.status_code == 400, f'got {response.status_code}'


def test_preview_query_rejects_overly_nested_rules(auth_client):
    """Deeply nested rules made json.loads raise RecursionError -> 500."""
    response = auth_client.post(
        '/api/preview-query',
        data=_make_nested_rules_json(3000),
        content_type='application/json',
    )
    assert response.status_code == 400, f'got {response.status_code}'


def test_watchlist_rejects_overly_nested_rules(auth_client):
    response = auth_client.post(
        '/api/watchlist',
        data=_make_nested_rules_json(3000),
        content_type='application/json',
    )
    assert response.status_code == 400, f'got {response.status_code}'


# --- Request body size bound ---


def test_max_content_length_is_configured(app):
    assert app.config.get('MAX_CONTENT_LENGTH'), (
        'MAX_CONTENT_LENGTH must bound request bodies'
    )


def test_oversized_json_body_is_rejected(auth_client):
    """A multi-MB rules payload used to be accepted end-to-end (200)."""
    rules = {
        'condition': 'AND',
        'rules': [
            {
                'field': 'featuretable.alert_id',
                'operator': 'equal',
                'value': 'x' * (2 * 1024 * 1024),
            }
        ],
    }
    response = auth_client.post('/api/preview-query', json=rules)
    assert response.status_code in (400, 413), f'got {response.status_code}'


# --- Main-catalog sort robustness ---


def test_invalid_primary_sort_still_orders_by_date(app):
    """?sort__date=bogus used to produce a query with no ORDER BY at all."""
    from sqlalchemy.dialects import sqlite as sqlite_dialect

    from poi_broker import db
    from poi_broker.models import Ztf
    from poi_broker.services.catalog_query import _apply_catalog_sort, resolve_sort

    with app.app_context():
        spec = resolve_sort({'sort__date': 'bogus'})
        query = _apply_catalog_sort(db.session.query(Ztf), spec)
        compiled = str(
            query.statement.compile(
                dialect=sqlite_dialect.dialect(),
                compile_kwargs={'literal_binds': True},
            )
        )
        assert 'ORDER BY' in compiled.upper()
        assert spec.desc is True

        # Sanity: explicit asc/desc behavior is unchanged.
        assert resolve_sort({'sort__date': 'asc'}).desc is False
        # No sort params at all still defaults to newest-first.
        assert resolve_sort({}).key == 'date'
        assert resolve_sort({}).desc is True


# --- Observing plot coordinate validation (public endpoint) ---


def _create_observatory_for_auth_user(app):
    from werkzeug.security import generate_password_hash

    from poi_broker import db
    from poi_broker.models import User, UserObservatory

    with app.app_context():
        user = User(
            email='obstest@example.com',
            password=generate_password_hash('Password123!'),
            name='Obs Test User',
            email_verified=True,
        )
        db.session.add(user)
        db.session.commit()
        observatory = UserObservatory(
            user_id=user.id,
            name='Home',
            latitude=51.5,
            longitude=-1.0,
            timezone_name='UTC',
        )
        db.session.add(observatory)
        db.session.commit()
        return user.id, observatory.id


def _login(client, email='obstest@example.com', password='Password123!'):
    client.post('/login', data={'email': email, 'password': password})


def test_observing_plot_rejects_decination_out_of_range(app, client):
    _, obs_id = _create_observatory_for_auth_user(app)
    _login(client)

    response = client.get(
        '/query_observing_plot',
        query_string={
            'obs_loc': f'custom:{obs_id}',
            'obs_date': '2024-06-01',
            'obs_tz': 'option_utc',
            'ra': '10',
            'dec': '91',
        },
    )
    assert response.status_code == 400, f'got {response.status_code}'


def test_observing_plot_rejects_nan_coordinates(app, client):
    _, obs_id = _create_observatory_for_auth_user(app)
    _login(client)

    response = client.get(
        '/query_observing_plot',
        query_string={
            'obs_loc': f'custom:{obs_id}',
            'obs_date': '2024-06-01',
            'obs_tz': 'option_utc',
            'ra': 'nan',
            'dec': 'nan',
        },
    )
    assert response.status_code == 400, f'got {response.status_code}'


def test_observing_plot_rejects_ra_out_of_range(app, client):
    _, obs_id = _create_observatory_for_auth_user(app)
    _login(client)

    for ra in ('-5', '361'):
        response = client.get(
            '/query_observing_plot',
            query_string={
                'obs_loc': f'custom:{obs_id}',
                'obs_date': '2024-06-01',
                'obs_tz': 'option_utc',
                'ra': ra,
                'dec': '20',
            },
        )
        assert response.status_code == 400, f'ra={ra}: got {response.status_code}'


def test_observing_plot_accepts_valid_boundary_coordinates(app, client):
    """dec=+/-90 at a matching latitude is the never-rises path, not an error."""
    _, obs_id = _create_observatory_for_auth_user(app)
    _login(client)

    response = client.get(
        '/query_observing_plot',
        query_string={
            'obs_loc': f'custom:{obs_id}',
            'obs_date': '2024-06-01',
            'obs_tz': 'option_utc',
            'ra': '10',
            'dec': '-38.5',  # abs(51.5 - (-38.5)) == 90 -> never-rises message
        },
    )
    assert response.status_code == 200, f'got {response.status_code}'


# SQLite sanity for the threshold used by the overflow tests.
def test_huge_int_cannot_bind_to_sqlite():
    try:
        sqlite3.connect(':memory:').execute('SELECT ?', (HUGE_INT,)).fetchone()
    except OverflowError:
        pass
    else:
        raise AssertionError('test premise broken: HUGE_INT binds fine')


# --- auditSBA F8: OFFSET pages are bounded ---


def test_catalog_page_beyond_max_page_is_rejected(app, client, monkeypatch):
    """A page deep-jump beyond MAX_PAGE 404s before any OFFSET scan runs, and
    the Last button hides when the real last page exceeds the cap (same
    hide_last mechanism as the deferred-count case)."""
    import poi_broker.app as app_module
    from poi_broker import db
    from poi_broker.models import Ztf
    from poi_broker.services.catalog_list import clear_catalog_total_cache

    with app.app_context():
        clear_catalog_total_cache()
        for index in range(250):  # 3 pages at PAGE_SIZE=100
            db.session.add(
                Ztf(
                    alert_id=f'ztf_candidate:{index:019d}',
                    date_alert_mjd=60000.0 + index,
                    locus_id=f'L{index}',
                    ztf_object_id=f'Z{index}',
                    locus_ra=10.0 + index,
                    locus_dec=20.0 + index,
                )
            )
        db.session.commit()

    monkeypatch.setattr(app_module, 'MAX_PAGE', 2)

    try:
        # page=2 renders via the OFFSET branch (keyset is only used on bare
        # first pages): last_page (3) > MAX_PAGE (2), so its Last link is
        # suppressed and the button hides.
        page2_html = client.get('/?page=2').get_data(as_text=True)
        last_btn_attrs = page2_html.split('catalog-last-btn', 1)[1].split('>', 1)[0]
        assert 'd-none' in last_btn_attrs

        assert client.get('/?page=3').status_code == 404
    finally:
        with app.app_context():
            clear_catalog_total_cache()


# --- auditSBA F15: prob_class allow-list is rendered, not hand-copied ---


def test_prob_class_options_render_from_server_allowlist(client, auth_client):
    """main.html and the query-builder select must render their prob_class
    values from catalog_query.VALID_PROB_CLASSES instead of a hand-maintained
    copy that can drift from the server-side validation."""
    from poi_broker.services.catalog_query import VALID_PROB_CLASSES

    html = client.get('/').get_data(as_text=True)
    select_html = html.split('id="prob_class"', 1)[1].split('</select>', 1)[0]
    for prob_class_value in VALID_PROB_CLASSES:
        assert f'<option value="{prob_class_value}"' in select_html
    assert select_html.count('<option') == len(VALID_PROB_CLASSES) + 1

    # The query-builder filter (export page) mirrors the same allow-list.
    builder_html = auth_client.get('/export').get_data(as_text=True)
    for prob_class_value in VALID_PROB_CLASSES:
        assert f"{prob_class_value}: '{prob_class_value}'" in builder_html


# --- NULL coordinates on a catalog row used to 500 the whole page ---


def test_catalog_row_with_null_coordinates_renders(app, client):
    """Regression: a featuretable row with NULL locus_ra/locus_dec made
    main.html raise TypeError ('%.5f' % None) and 500 the entire catalog
    page. Coordinate cells must render empty instead (like every other
    None-safe filter on the page)."""
    from poi_broker import db
    from poi_broker.models import Ztf

    with app.app_context():
        db.session.add(
            Ztf(
                alert_id='ztf_candidate:0000000000000000100',
                date_alert_mjd=60000.0,
                locus_id='L-ok',
                ztf_object_id='Z-ok',
                locus_ra=10.0,
                locus_dec=20.0,
                ant_mag_corrected=18.5,
            )
        )
        db.session.add(
            Ztf(
                alert_id='ztf_candidate:0000000000000000200',
                date_alert_mjd=60001.0,
                locus_id='L-null',
                ztf_object_id='Z-null',
            )
        )
        db.session.commit()

    response = client.get('/')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '10.00000' in html
    assert '20.00000' in html


def test_overflow_and_recursion_handlers_log_exceptions(app, caplog):
    """OverflowError and RecursionError handlers must log at ERROR with exc_info."""
    import logging

    with app.test_request_context('/api/test-overflow', json={}):
        with caplog.at_level(logging.ERROR):
            try:
                raise OverflowError('Int out of range test')
            except OverflowError as exc:
                res = app.error_handler_spec[None][None][OverflowError](exc)
                assert res[1] == 400
            assert any(
                rec.levelno == logging.ERROR
                and 'Integer out of range' in rec.message
                and rec.exc_info
                for rec in caplog.records
            )

    caplog.clear()
    with app.test_request_context('/api/test-recursion', json={}):
        with caplog.at_level(logging.ERROR):
            try:
                raise RecursionError('Recursion limit test')
            except RecursionError as exc:
                res = app.error_handler_spec[None][None][RecursionError](exc)
                assert res[1] == 400
            assert any(
                rec.levelno == logging.ERROR
                and 'Recursion limit reached' in rec.message
                and rec.exc_info
                for rec in caplog.records
            )


def test_malformed_cursor_returns_400_not_500(client):
    """Client-supplied non-numeric cursor on numeric sort must 400, not 500."""
    response = client.get('/?before_v=not-a-number&before_alert_id=x&before_locus_id=y')
    assert response.status_code == 400
    assert 'the pagination cursor could not be parsed' in response.get_data(as_text=True)


def test_malformed_cursor_api_request_returns_json_400(client):
    """API request with malformed cursor must return JSON 400."""
    response = client.get(
        '/?before_v=not-a-number&before_alert_id=x&before_locus_id=y',
        headers={'Content-Type': 'application/json'},
    )
    assert response.status_code == 400
    assert response.is_json
    assert response.get_json() == {'error': 'Invalid cursor'}


