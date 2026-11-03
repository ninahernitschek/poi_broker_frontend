"""Tests for hybrid catalog pagination and cheap / on-demand counts."""

import pytest
from astropy.time import Time

from poi_broker import db
from poi_broker.models import Classification, Ztf
from poi_broker.services.catalog_list import (
    PAGE_SIZE,
    cached_catalog_total,
    clear_catalog_total_cache,
    count_matches,
    cursor_from_row,
    fetch_keyset,
    fetch_page,
    last_page_from_total,
    parse_keyset_request,
    resolve_catalog_total,
    should_use_keyset,
)
from poi_broker.services.catalog_query import (
    build_catalog_query,
    catalog_href,
    catalog_query_string,
    project_catalog_columns,
)


def _add_alert(*, alert_id: str, mjd: float, locus_id: str, object_id: str, passband: str = 'g'):
    row = Ztf(
        alert_id=alert_id,
        date_alert_mjd=mjd,
        locus_id=locus_id,
        ztf_object_id=object_id,
        ant_passband=passband,
        locus_ra=10.0,
        locus_dec=20.0,
        ant_mag_corrected=18.0,
        num_alerts=1,
        num_mag_values=1,
    )
    db.session.add(row)
    return row


def test_fetch_page_has_next_from_extra_row(app):
    with app.app_context():
        for index in range(5):
            _add_alert(
                alert_id=f'ztf_candidate:{index:019d}',
                mjd=60000.0 + index,
                locus_id=f'L{index}',
                object_id=f'Z{index}',
            )
        db.session.commit()
        query = db.session.query(Ztf).order_by(Ztf.date_alert_mjd.asc())
        page1, has_next = fetch_page(query, 1, page_size=2)
        assert len(page1) == 2
        assert has_next is True
        page3, has_next = fetch_page(query, 3, page_size=2)
        assert len(page3) == 1
        assert has_next is False


def test_fetch_keyset_no_overlap_with_tied_mjd(app):
    with app.app_context():
        _add_alert(alert_id='ztf_candidate:1000000000000000001', mjd=60000.0, locus_id='L-a', object_id='Za')
        _add_alert(alert_id='ztf_candidate:1000000000000000002', mjd=60000.0, locus_id='L-b', object_id='Zb')
        _add_alert(alert_id='ztf_candidate:1000000000000000003', mjd=60000.0, locus_id='L-c', object_id='Zc')
        db.session.commit()
        query = db.session.query(Ztf)
        first, has_next, has_prev = fetch_keyset(
            query, sort_column=Ztf.date_alert_mjd, cursor=None, direction='first',
            sort_desc=True, page_size=2,
        )
        assert has_prev is False
        assert has_next is True
        assert len(first) == 2
        nxt, has_next, has_prev = fetch_keyset(
            query,
            sort_column=Ztf.date_alert_mjd,
            cursor=cursor_from_row(first[-1], 'date_alert_mjd'),
            direction='before',
            sort_desc=True,
            page_size=2,
        )
        assert has_prev is True
        assert has_next is False
        first_ids = {row.alert_id for row in first}
        next_ids = {row.alert_id for row in nxt}
        assert first_ids.isdisjoint(next_ids)
        assert first_ids | next_ids == {
            'ztf_candidate:1000000000000000001',
            'ztf_candidate:1000000000000000002',
            'ztf_candidate:1000000000000000003',
        }


def test_cached_catalog_total_ttl(app, monkeypatch):
    with app.app_context():
        clear_catalog_total_cache()
        _add_alert(alert_id='ztf_candidate:2000000000000000001', mjd=60001.0, locus_id='Lc', object_id='Zc')
        db.session.commit()
        clock = {'now': 1000.0}
        monkeypatch.setattr('poi_broker.services.catalog_list.time.monotonic', lambda: clock['now'])
        assert cached_catalog_total(ttl_seconds=30) == 1
        _add_alert(alert_id='ztf_candidate:2000000000000000002', mjd=60002.0, locus_id='Ld', object_id='Zd')
        db.session.commit()
        assert cached_catalog_total(ttl_seconds=30) == 1
        clock['now'] = 1031.0
        assert cached_catalog_total(ttl_seconds=30) == 2
        clear_catalog_total_cache()


def test_resolve_catalog_total_defers_unselective_filter(app):
    with app.app_context():
        build = build_catalog_query({'ant_passband': 'g'})
        total, deferred = resolve_catalog_total(
            build,
            items=[object()],
            has_next=True,
            page=1,
            use_keyset=True,
            keyset_direction='first',
        )
        assert total is None
        assert deferred is True


def test_resolve_catalog_total_uses_cheap_equality(app):
    with app.app_context():
        _add_alert(
            alert_id='ztf_candidate:3000000000000000001',
            mjd=60010.0,
            locus_id='L-cheap',
            object_id='Zcheap',
        )
        db.session.commit()
        build = build_catalog_query({'locus_id': 'L-cheap'})
        assert build.has_cheap_equality is True
        total, deferred = resolve_catalog_total(
            build,
            items=[object(), object()],
            has_next=True,
            page=1,
            use_keyset=True,
            keyset_direction='first',
        )
        assert deferred is False
        assert total == 1


def test_should_use_keyset_falls_back_to_offset_for_page_jump():
    # Every sort is keyset-paged now; only an explicit ?page=N jump uses OFFSET.
    assert should_use_keyset({'page': '2'}) is False
    assert should_use_keyset({}) is True
    assert should_use_keyset({'page': '1'}) is True
    assert should_use_keyset({'sort__locus_ra': 'asc'}) is True
    assert should_use_keyset({'before_v': '1', 'before_alert_id': 'a', 'before_locus_id': 'l'}) is True


def test_parse_keyset_request_last_wins():
    direction, cursor = parse_keyset_request({'last': '1'}, numeric=True)
    assert direction == 'last'
    assert cursor is None


def test_last_page_from_total():
    assert last_page_from_total(0) == 1
    assert last_page_from_total(100) == 1
    assert last_page_from_total(101) == 2


def test_catalog_href_and_query_string_strip_pagination():
    qs = catalog_query_string({'ant_passband': 'g', 'page': '2', 'before_mjd': '1'})
    assert qs == 'ant_passband=g'
    assert catalog_href(qs, {'page': '3'}) == '/?ant_passband=g&page=3'
    assert catalog_href('', None) == '/'


def test_unfiltered_total_counts_every_row_for_every_sort(app):
    """A NULL-valued sort column must no longer shrink the reported total.

    This used to be the reverse: cached_catalog_total() counted all
    featuretable rows while the listing excluded NULL sort values, so an
    unfiltered magnitude sort reported 3,178,668 against a list that could only
    ever reach the 2,166,326 rows carrying a magnitude. NULL rows are now
    listed in a trailing segment, so both sides agree on the full total.
    """
    from poi_broker.services.catalog_list import clear_catalog_total_cache

    with app.app_context():
        clear_catalog_total_cache()
        for index in range(3):
            _add_alert(alert_id=f'ztf_candidate:{1500000000000000000 + index}',
                       mjd=60900.0 + index, locus_id=f'Ln{index}', object_id=f'Zn{index}')
        with_mag = _add_alert(alert_id='ztf_candidate:1500000000000000009', mjd=60910.0,
                              locus_id='Lnn', object_id='Znn')
        with_mag.ant_mag_corrected = None
        db.session.commit()

        build = build_catalog_query({'sort__ant_mag_corrected': 'asc'})
        total, deferred = resolve_catalog_total(
            build, items=[object()] * 3, has_next=True, page=1, use_keyset=True,
            keyset_direction='first',
        )
        assert deferred is False
        assert total == 4, 'the NULL-magnitude row is listed, so it must be counted'

        # ... and the listing really does reach it, in the trailing segment.
        rows, _has_next, _has_prev = fetch_keyset(
            db.session.query(Ztf),
            sort_column=build.sort.column,
            cursor=None,
            direction='first',
            sort_desc=False,
            page_size=10,
            segment_counts=(3, 1),
        )
        assert len(rows) == 4
        assert sum(row.ant_mag_corrected is None for row in rows) == 1

        # A non-nullable sort sees every row too.
        date_build = build_catalog_query({})
        date_total, _ = resolve_catalog_total(
            date_build, items=[object()] * 4, has_next=True, page=1, use_keyset=True,
            keyset_direction='first',
        )
        assert date_total == 4
        clear_catalog_total_cache()


def test_keyset_walk_matches_offset_walk_row_for_row(app, monkeypatch):
    """The cursor must reproduce the OFFSET order exactly, with no gaps/dupes.

    Ties on the sort column are what break naive cursors, so the fixture gives
    several rows the same RA and the same MJD.
    """
    monkeypatch.setattr('poi_broker.services.catalog_list.PAGE_SIZE', 3)
    with app.app_context():
        rows = []
        for index in range(40):
            row = _add_alert(
                alert_id=f'ztf_candidate:{1600000000000000000 + index:04d}',
                # 5 distinct RA values shared by 8 rows each
                mjd=61000.0 + (index % 5),
                locus_id=f'Lw{index:03d}',
                object_id=f'Zw{index:03d}',
            )
            row.locus_ra = float(index % 5)
            row.ant_mag_corrected = float(index % 7)
            rows.append(row)
        db.session.commit()

        for sort_args, attr in (
            ({'sort__locus_ra': 'asc'}, 'locus_ra'),
            ({'sort__locus_ra': 'desc'}, 'locus_ra'),
            ({'sort__ant_mag_corrected': 'asc'}, 'ant_mag_corrected'),
            ({'sort__ant_mag_corrected': 'desc'}, 'ant_mag_corrected'),
            ({}, 'date_alert_mjd'),
            ({'sort__date': 'asc'}, 'date_alert_mjd'),
            ({'sort__locus_id': 'asc'}, 'locus_id'),
            ({'sort__alert_id': 'desc'}, 'alert_id'),
        ):
            build = build_catalog_query(sort_args)
            list_q = project_catalog_columns(build.list_query)
            sort_desc = build.sort.desc
            truth = [
                (r.alert_id, r.locus_id)
                for r in list_q.limit(1000).offset(0).all()
            ]

            # Walk the whole result set with the cursor, 3 rows at a time.
            walked, cur = [], None
            direction = 'first'
            for _ in range(60):
                page, has_next, has_prev = fetch_keyset(
                    list_q, sort_column=build.sort.column, cursor=cur,
                    direction=direction, sort_desc=sort_desc, page_size=3,
                )
                walked.extend((r.alert_id, r.locus_id) for r in page)
                if not has_next:
                    break
                cur = cursor_from_row(page[-1], attr)
                direction = 'before'
            assert walked == truth, f'{sort_args}: cursor walk diverged from OFFSET'


def test_keyset_last_reaches_final_page_and_terminates(app, monkeypatch):
    monkeypatch.setattr('poi_broker.services.catalog_list.PAGE_SIZE', 3)
    with app.app_context():
        for index in range(7):
            _add_alert(alert_id=f'ztf_candidate:{1700000000000000000 + index}',
                       mjd=61100.0 + index, locus_id=f'Ll{index}', object_id=f'Zl{index}')
        db.session.commit()

        build = build_catalog_query({})
        list_q = project_catalog_columns(build.list_query)
        last, has_next, has_prev = fetch_keyset(
            list_q, sort_column=build.sort.column, cursor=None,
            direction='last', sort_desc=True, page_size=3,
        )
        assert has_next is False
        assert has_prev is True
        # Default sort is newest-first, so "Last" is the OLDEST page, shown in
        # descending order: 61102, 61101, 61100.
        assert [row.date_alert_mjd for row in last] == [61102.0, 61101.0, 61100.0]
        assert last[-1].date_alert_mjd == 61100.0

        beyond, has_next2, _ = fetch_keyset(
            list_q, sort_column=build.sort.column,
            cursor=cursor_from_row(last[-1], build.sort.attr),
            direction='before', sort_desc=True, page_size=3,
        )
        assert beyond == []
        assert has_next2 is False


def test_legacy_date_cursor_is_still_accepted(client, app, monkeypatch):
    """Pre-2026-10 bookmarks used before_mjd/after_mjd; they must keep working."""
    monkeypatch.setattr('poi_broker.app.PAGE_SIZE', 2)
    monkeypatch.setattr('poi_broker.services.catalog_list.PAGE_SIZE', 2)
    with app.app_context():
        added = []
        for index in range(5):
            added.append(_add_alert(
                alert_id=f'ztf_candidate:{1800000000000000000 + index}',
                mjd=Time(61200.0 + index, format='mjd').mjd,
                locus_id=f'Lg{index}',
                object_id=f'Zg{index}',
            ))
        db.session.commit()
        # Read inside the context: the session is unbound afterwards.
        boundary_mjd = repr(float(added[2].date_alert_mjd))
        boundary_alert = added[2].alert_id
        boundary_locus = added[2].locus_id

    response = client.get(
        '/?before_mjd={}&before_alert_id={}&before_locus_id={}'.format(
            boundary_mjd, boundary_alert, boundary_locus
        )
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    # The legacy cursor must have been honoured, not ignored: the newest row
    # (index 4) is newer than the boundary and must NOT appear.
    assert f'Lg{4}' not in body
    assert 'Lg0' in body


def test_page_beyond_max_page_explains_the_limit(client, app, monkeypatch):
    import poi_broker.app as app_module

    monkeypatch.setattr(app_module, 'MAX_PAGE', 2)
    with app.app_context():
        for index in range(250):
            _add_alert(alert_id=f'ztf_candidate:{1900000000000000000 + index:06d}',
                       mjd=61300.0 + index, locus_id=f'Lp{index}', object_id=f'Zp{index}')
        db.session.commit()

    response = client.get('/?page=99')
    assert response.status_code == 404
    body = response.get_data(as_text=True).lower()
    # A bare 404 tells the user nothing about why; point them at the way out.
    assert 'narrow the' in body or 'narrow the catalog' in body


def test_keyset_pagination_is_unbounded_by_page_cap(client, app, monkeypatch):
    """The cap guarded OFFSET only; cursor links must never 404 on depth."""
    import poi_broker.app as app_module

    monkeypatch.setattr(app_module, 'MAX_PAGE', 2)
    monkeypatch.setattr('poi_broker.app.PAGE_SIZE', 2)
    monkeypatch.setattr('poi_broker.services.catalog_list.PAGE_SIZE', 2)
    with app.app_context():
        for index in range(12):
            _add_alert(alert_id=f'ztf_candidate:{2000000000000000000 + index}',
                       mjd=61400.0 + index, locus_id=f'Lu{index}', object_id=f'Zu{index}')
        db.session.commit()

    body = client.get('/?sort__locus_ra=asc').get_data(as_text=True)
    assert 'before_v=' in body
    assert 'page=3' not in body  # no OFFSET links at all

    # Walk to the end using only the rendered cursor links, following whatever
    # href the page actually rendered rather than rebuilding one.
    import re as _re
    guard = 0
    while guard < 25:
        guard += 1
        match = _re.search(r'href="(/\?[^"]*before_v=[^"]*)"', body)
        if not match:
            break
        resp = client.get(match.group(1).replace('&amp;', '&'))
        assert resp.status_code == 200, f'cursor page {guard} 404ed with MAX_PAGE=2'
        body = resp.get_data(as_text=True)
    assert guard > 3, 'cursor walk should have advanced through several pages'


def test_nullable_sort_offers_a_quick_jump_to_the_null_segment(client, app):
    """Rows with no magnitude must stay reachable without paging past 2.16M rows."""
    with app.app_context():
        for index in range(3):
            row = _add_alert(alert_id=f'ztf_candidate:{2100000000000000000 + index}',
                             mjd=61500.0 + index, locus_id=f'Lq{index}',
                             object_id=f'Zq{index}')
            row.ant_mag_corrected = 18.0 + index
        for index in range(2):
            row = _add_alert(alert_id=f'ztf_candidate:{2100000000000000100 + index}',
                             mjd=61510.0 + index, locus_id=f'Lr{index}',
                             object_id=f'Zr{index}')
            row.ant_mag_corrected = None
        db.session.commit()

    body = client.get('/?sort__ant_mag_corrected=asc').get_data(as_text=True)
    assert '2 alerts have no magnitude' in body
    assert 'listed after the others' in body
    assert 'nulls=1' in body

    # The jump lands on the head of the null segment, and offers a way back
    # into the numeric rows.
    jumped = client.get('/?sort__ant_mag_corrected=asc&nulls=1')
    assert jumped.status_code == 200
    with app.app_context():
        # The jump page must be exactly the head of the null segment.
        build = build_catalog_query({'sort__ant_mag_corrected': 'asc'})
        rows, has_next, has_prev = fetch_keyset(
            project_catalog_columns(build.list_query),
            sort_column=build.sort.column, cursor=None, direction='nulls',
            sort_desc=False, page_size=100, segment_counts=(3, 2),
        )
        assert [r.ant_mag_corrected for r in rows] == [None, None]
        assert has_prev is True, 'a null-segment page must offer Prev back into the values'
        assert has_next is False
    # ... and the rendered page links backwards into the numeric rows. A cursor
    # inside the null segment carries the *_null flag, not a value.
    jumped_body = client.get('/?sort__ant_mag_corrected=asc&nulls=1').get_data(as_text=True)
    assert 'after_null=1' in jumped_body

    # A primary-key column can never be NULL, so it must not claim otherwise.
    body = client.get('/?sort__date=desc').get_data(as_text=True)
    assert 'nulls=1' not in body


def test_text_magnitude_block_cannot_break_the_cursor(client, app):
    """A TEXT 'magnitude' is a different storage class, so the cursor must refuse it."""
    with app.app_context():
        good = _add_alert(alert_id='ztf_candidate:2150000000000000001', mjd=61600.0,
                          locus_id='Lt1', object_id='Zt1')
        good.ant_mag_corrected = 19.0
        bad = _add_alert(alert_id='ztf_candidate:2150000000000000002', mjd=61601.0,
                         locus_id='Lt2', object_id='Zt2')
        bad.ant_mag_corrected = 'R'  # type: ignore[assignment]
        db.session.commit()

    resp = client.get('/?sort__ant_mag_corrected=asc')
    assert resp.status_code == 200
    # It renders (no 500) and the bad row is not offered as a cursor position.
    body = resp.get_data(as_text=True)
    assert 'before_v=R' not in body


def test_non_numeric_magnitude_does_not_500_the_page(client, app):
    """Regression: 83 production rows store ant_mag_corrected as TEXT.

    Values 'R'/'g' appear to be a passband leaked into the magnitude column.
    mag_filter() called round() on them and raised TypeError, turning one bad
    row into a 500 for the entire page. SQLite orders TEXT after REAL, so a
    descending magnitude sort puts those rows first and they are reachable
    from page 1.

    The row must be written with raw SQL: the column is declared Float, so the
    ORM refuses to bind a str, whereas the ingested table already contains them.
    """
    with app.app_context():
        for index in range(4):
            _add_alert(alert_id=f'ztf_candidate:{2200000000000000000 + index}',
                       mjd=61600.0 + index, locus_id=f'Lm{index}',
                       object_id=f'Zm{index}')
        db.session.commit()
        db.session.execute(db.text(
            "UPDATE featuretable SET ant_mag_corrected = 'R' "
            "WHERE alert_id = :a"
        ), {'a': 'ztf_candidate:2200000000000000001'})
        db.session.execute(db.text(
            "UPDATE featuretable SET ant_mag_corrected = 'g' "
            "WHERE alert_id = :a"
        ), {'a': 'ztf_candidate:2200000000000000002'})
        db.session.commit()
        stored = db.session.execute(db.text(
            "SELECT typeof(ant_mag_corrected) FROM featuretable "
            "WHERE alert_id = :a"
        ), {'a': 'ztf_candidate:2200000000000000001'}).scalar_one()
        assert stored == 'text', 'test premise broken: value did not store as TEXT'

    for query in ('/?sort__ant_mag_corrected=desc', '/?sort__ant_mag_corrected=asc'):
        response = client.get(query)
        assert response.status_code == 200, f'{query} returned {response.status_code}'
    # The bad values render as themselves rather than crashing or vanishing.
    body = client.get('/?sort__ant_mag_corrected=desc').get_data(as_text=True)
    assert '>R<' in body or '>g<' in body


def test_text_magnitude_block_cannot_break_the_cursor(app, monkeypatch):
    """The TEXT block must not be able to swallow the cursor mid-catalog.

    SQLite orders TEXT after REAL, so a descending magnitude sort would
    otherwise *start* with the corrupt 'R'/'g' rows. A cursor on one of those
    parses as no value at all, which silently reset pagination to page 1 -- an
    unbreakable Next loop. The orderability filter keeps them out of the sort
    entirely, so every cursor value round-trips.
    """
    monkeypatch.setattr('poi_broker.services.catalog_list.PAGE_SIZE', 2)
    with app.app_context():
        for index in range(4):
            _add_alert(alert_id=f'ztf_candidate:{2300000000000000000 + index}',
                       mjd=61700.0 + index, locus_id=f'Lt{index}', object_id=f'Zt{index}')
        db.session.commit()
        for raw, alert in (('g', 'ztf_candidate:2300000000000000002'),
                           ('R', 'ztf_candidate:2300000000000000001'),
                           (21.0, 'ztf_candidate:2300000000000000000'),
                           (20.0, 'ztf_candidate:2300000000000000003')):
            db.session.execute(
                db.text('UPDATE featuretable SET ant_mag_corrected = :m '
                        'WHERE alert_id = :a'),
                {'m': raw, 'a': alert})
        db.session.commit()

        build = build_catalog_query({'sort__ant_mag_corrected': 'desc'})
        list_q = project_catalog_columns(build.list_query)

        # Walk every page; every cursor must survive a URL round-trip and the
        # pages must cover each numeric row exactly once, in order.
        from poi_broker.services.catalog_list import parse_keyset_cursor

        from poi_broker.services.catalog_list import MalformedCursor

        walked, cursor, direction, guard = [], None, 'first', 0
        while guard < 10:
            guard += 1
            page, has_next, _ = fetch_keyset(
                list_q, sort_column=build.sort.column, cursor=cursor,
                direction=direction, sort_desc=True, page_size=2,
                segment_counts=(2, 0))
            walked.extend(page)
            if not has_next:
                break
            cursor = cursor_from_row(page[-1], build.sort.attr)
            direction = 'before'

        # DESC puts TEXT before REAL, so the corrupt rows lead the listing now
        # that no storage-class filter hides them.
        assert {r.ant_mag_corrected for r in walked[:2]} == {'R', 'g'}
        assert len({r.alert_id for r in walked}) == len(walked), 'no row twice'

        # A cursor that lands on one of them must fail loudly. Returning None
        # instead would re-render page 1 with a Next link that never advances.
        with pytest.raises(MalformedCursor):
            parse_keyset_cursor(
                {'before_v': 'R', 'before_alert_id': 'a', 'before_locus_id': 'b'},
                'before', numeric=True,
            )

        # With the corrupt rows gone (the state tools/apply_fix_bad_magnitudes.sql
        # leaves production in) every cursor round-trips again.
        parse_keyset_cursor(
            cursor_from_row(
                [r for r in walked if r.ant_mag_corrected == 21.0][0],
                build.sort.attr,
            ).as_before_params(),
            'before', numeric=True,
        )


def test_unfiltered_list_shows_exact_short_total(client, app):
    with app.app_context():
        clear_catalog_total_cache()
        _add_alert(alert_id='ztf_candidate:4000000000000000001', mjd=60020.0, locus_id='L1', object_id='Z1')
        db.session.commit()
    response = client.get('/')
    assert response.status_code == 200
    assert b'id="catalog-rows-value"' in response.data
    assert b'>1<' in response.data
    assert b'id="btn-catalog-count"' not in response.data


def test_cheap_locus_filter_shows_exact_count(client, app):
    with app.app_context():
        _add_alert(alert_id='ztf_candidate:5000000000000000001', mjd=60021.0, locus_id='L-only', object_id='Z1')
        _add_alert(alert_id='ztf_candidate:5000000000000000002', mjd=60022.0, locus_id='L-other', object_id='Z2')
        db.session.commit()
    response = client.get('/?locus_id=L-only')
    assert response.status_code == 200
    assert b'id="btn-catalog-count"' not in response.data
    assert b'>1<' in response.data


def test_unselective_filter_defers_count_when_page_is_full(client, app, monkeypatch):
    monkeypatch.setattr('poi_broker.app.PAGE_SIZE', 2)
    monkeypatch.setattr('poi_broker.services.catalog_list.PAGE_SIZE', 2)
    with app.app_context():
        for index in range(3):
            _add_alert(
                alert_id=f'ztf_candidate:{6000000000000000000 + index}',
                mjd=60100.0 + index,
                locus_id=f'Lp{index}',
                object_id=f'Zp{index}',
                passband='g',
            )
        db.session.commit()
    response = client.get('/?ant_passband=g')
    assert response.status_code == 200
    assert b'2+' in response.data
    assert b'id="btn-catalog-count"' in response.data


def test_catalog_count_api_returns_exact_match_count(client, app):
    with app.app_context():
        _add_alert(
            alert_id='ztf_candidate:7000000000000000001',
            mjd=60200.0,
            locus_id='Lc1',
            object_id='Zc1',
            passband='g',
        )
        _add_alert(
            alert_id='ztf_candidate:7000000000000000002',
            mjd=60201.0,
            locus_id='Lc2',
            object_id='Zc2',
            passband='R',
        )
        db.session.commit()
    response = client.get('/api/catalog-count?ant_passband=g&sort__date=desc&page=2')
    assert response.status_code == 200
    assert response.get_json() == {'count': 1}


def test_catalog_count_includes_classification_only_when_needed(client, app):
    with app.app_context():
        _add_alert(
            alert_id='ztf_candidate:8000000000000000001',
            mjd=60300.0,
            locus_id='Lsn',
            object_id='Zsn',
        )
        db.session.add(Classification(alert_id='ztf_candidate:8000000000000000001', prob_class='sn'))
        db.session.commit()
        build = build_catalog_query({'prob_class': 'sn'}, include_sort=False)
        assert count_matches(build.count_query) == 1


def test_keyset_next_link_uses_cursor_not_page(client, app, monkeypatch):
    monkeypatch.setattr('poi_broker.app.PAGE_SIZE', 2)
    monkeypatch.setattr('poi_broker.services.catalog_list.PAGE_SIZE', 2)
    with app.app_context():
        for index in range(3):
            _add_alert(
                alert_id=f'ztf_candidate:{9000000000000000000 + index}',
                mjd=Time(60400.0 + index, format='mjd').mjd,
                locus_id=f'Lk{index}',
                object_id=f'Zk{index}',
            )
        db.session.commit()
    response = client.get('/')
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'before_v=' in body
    assert 'before_alert_id=' in body
    assert 'page=2' not in body


def test_sort_locus_id_is_the_primary_sort(app):
    """The clicked column must lead the ORDER BY, not merely break MJD ties.

    The old implementation appended .order_by() calls, and SQLAlchemy appends,
    so date_alert_mjd DESC stayed the leading term for every secondary sort:
    ?sort__locus_id=asc returned rows ordered by date, with locus_id only
    ordering rows that happened to share an MJD.
    """
    from sqlalchemy.dialects import sqlite as sqlite_dialect

    with app.app_context():
        _add_alert(alert_id='ztf_candidate:1300000000000000003', mjd=60700.0, locus_id='L-c', object_id='Zc')
        _add_alert(alert_id='ztf_candidate:1300000000000000001', mjd=60700.0, locus_id='L-a', object_id='Za')
        _add_alert(alert_id='ztf_candidate:1300000000000000002', mjd=60900.0, locus_id='L-b', object_id='Zb')
        db.session.commit()

        # Newest alert has the alphabetically LAST locus_id: if date still led
        # the ORDER BY, 'L-b' (mjd 60900) would come first on an ascending sort.
        asc_build = build_catalog_query({'sort__locus_id': 'asc'})
        assert asc_build.sort.key == 'locus_id'
        assert asc_build.sort.desc is False
        asc_items, _ = fetch_page(asc_build.list_query, 1, page_size=10)
        assert [row.locus_id for row in asc_items] == ['L-a', 'L-b', 'L-c']

        compiled = str(
            asc_build.list_query.statement.compile(dialect=sqlite_dialect.dialect())
        )
        order_by = compiled[compiled.upper().rfind('ORDER BY'):]
        assert 'locus_id ASC' in order_by
        assert order_by.index('locus_id ASC') < order_by.index('alert_id ASC')

        desc_build = build_catalog_query({'sort__locus_id': 'desc'})
        assert desc_build.sort.desc is True
        desc_items, _ = fetch_page(desc_build.list_query, 1, page_size=10)
        assert [row.locus_id for row in desc_items] == ['L-c', 'L-b', 'L-a']


def test_multi_sort_url_collapses_to_one_key(app):
    """Extra sort keys cannot change row order, so exactly one is honoured."""
    with app.app_context():
        spec = build_catalog_query({
            'sort__locus_ra': 'asc',
            'sort__locus_dec': 'desc',
            'sort__alert_id': 'asc',
        }).sort
        assert spec.key == 'alert_id'  # first in SORT_KEYS order wins
        terms = spec.order_terms()
        assert len(terms) == 3  # (column, alert_id, locus_id) — always 3


def test_null_sort_rows_are_listed_in_a_trailing_segment(app):
    """NULL sort values must be listed, last, without a storage-class filter.

    In production ant_mag_corrected is NULL for 1,012,342 of 3,178,668 rows
    (31.8%). `NULL > NULL` is NULL, so a cursor inside that block matches
    nothing -- which is why they form their own segment rather than being
    filtered out. They sort last in both directions.
    """
    from poi_broker.services.catalog_list import count_matches

    with app.app_context():
        for index, mag in enumerate((19.5, 18.5, None, 20.5)):
            row = _add_alert(alert_id=f'ztf_candidate:{1400000000000000000 + index}',
                             mjd=60800.0 + index, locus_id=f'Lm{index}',
                             object_id=f'Zm{index}')
            row.ant_mag_corrected = mag
        db.session.commit()

        build = build_catalog_query({'sort__ant_mag_corrected': 'asc'})
        assert build.sort.nullable is True
        rows, _ = fetch_page(
            project_catalog_columns(build.list_query), 1, page_size=10,
            sort_column=build.sort.column, sort_desc=False,
        )
        assert [r.ant_mag_corrected for r in rows] == [18.5, 19.5, 20.5, None]

        # DESC puts the NULL row last too -- not first, which is what SQLite's
        # native ordering would do.
        rows, _ = fetch_page(
            project_catalog_columns(build.list_query), 1, page_size=10,
            sort_column=build.sort.column, sort_desc=True,
        )
        assert [r.ant_mag_corrected for r in rows] == [20.5, 19.5, 18.5, None]

        # Count agrees with the listing: nothing is filtered out any more.
        assert count_matches(build.count_query) == 4
        assert 'TYPEOF' not in str(build.list_query.statement).upper()
        assert build.has_applied_filters is False


def test_every_sort_key_nullability_is_derived_from_the_model(app):
    """Adding a sortable column must not need a second, hand-kept list.

    SortSpec.nullable reads the model column, so a new sort key is classified
    correctly the moment it is added to SORT_KEYS -- the previous design kept a
    parallel frozenset (PK_SORT_KEYS) that had to be updated by hand.
    """
    from poi_broker.models import Ztf as ZtfModel
    from poi_broker.services.catalog_query import (
        SORT_COLUMN_ATTRS,
        SORT_COLUMNS,
        SORT_KEYS,
        _column_nullable,
    )

    model_columns = {c.name: c for c in ZtfModel.__table__.columns}
    with app.app_context():
        for key in SORT_KEYS:
            attr = SORT_COLUMN_ATTRS[key]
            spec = build_catalog_query({f'sort__{key}': 'asc'}).sort
            assert key in SORT_COLUMNS
            assert spec.nullable == model_columns[attr].nullable, (
                f'{key}: attr={attr!r} model_nullable='
                f'{model_columns[attr].nullable} spec.nullable={spec.nullable}'
            )
            # ... and the helper agrees with SQLAlchemy's own flag.
            assert _column_nullable(SORT_COLUMNS[key]) == model_columns[attr].nullable

    # Primary-key sorts can never be NULL, so they take the single-segment path.
    for pk_key in ('date', 'alert_id', 'locus_id'):
        with app.app_context():
            assert build_catalog_query({f'sort__{pk_key}': 'asc'}).sort.nullable is False
    assert set(SORT_COLUMN_ATTRS) == set(SORT_KEYS)


def test_offset_page_straddling_the_segment_boundary(app):
    """A `?page=N` window spanning present->null must be gapless.

    The legacy OFFSET path used to inherit SQLite's native ordering, where
    NULLs sort first ascending -- so page N by number and page N by cursor
    disagreed. It now walks the same two segments and tops up across the
    boundary.
    """
    from poi_broker.services.catalog_query import project_catalog_columns

    with app.app_context():
        # 5 present rows then 5 null rows, so page 2 of size 4 spans the join.
        for index in range(5):
            row = _add_alert(alert_id=f'ztf_candidate:{1300000000000000000 + index}',
                             mjd=60700.0 + index, locus_id=f'Ls{index}',
                             object_id=f'Zs{index}')
            row.ant_mag_corrected = 20.0 - index
        for index in range(5):
            row = _add_alert(alert_id=f'ztf_candidate:{1300000000000000200 + index}',
                             mjd=60710.0 + index, locus_id=f'Lt{index}',
                             object_id=f'Zt{index}')
            row.ant_mag_corrected = None
        db.session.commit()

        build = build_catalog_query({'sort__ant_mag_corrected': 'asc'})
        query = project_catalog_columns(build.list_query)
        kwargs = {
            'sort_column': build.sort.column,
            'sort_desc': False,
            'segment_counts': (5, 5),
        }

        walked, ids = [], []
        for page in (1, 2, 3, 4):
            rows, has_next = fetch_page(query, page, page_size=4, **kwargs)
            walked.extend(rows)
            ids.extend(r.alert_id for r in rows)
            if not has_next:
                break
        # No gaps, no duplicates, and every row exactly once.
        assert len(ids) == len(set(ids)) == 10, ids
        # page 2 is the straddle: the final value, then the first three nulls.
        page_two, has_next = fetch_page(query, 2, page_size=4, **kwargs)
        assert [r.ant_mag_corrected for r in page_two] == [20.0, None, None, None]
        assert has_next is True
        # ASC still puts the nulls last overall.
        assert [r.ant_mag_corrected for r in walked] == [
            16.0, 17.0, 18.0, 19.0, 20.0, None, None, None, None, None,
        ]

        # DESC straddles identically.
        desc_kwargs = dict(kwargs, sort_desc=True)
        desc_walked = []
        for page in (1, 2, 3, 4):
            rows, has_next = fetch_page(query, page, page_size=4, **desc_kwargs)
            desc_walked.extend(rows)
            if not has_next:
                break
        assert [r.ant_mag_corrected for r in desc_walked] == [
            20.0, 19.0, 18.0, 17.0, 16.0, None, None, None, None, None,
        ]


def test_offset_page_boundary_has_next_when_null_rows_exist(app):
    """When a page ends exactly at the present/null boundary, has_next must be True."""
    from poi_broker.services.catalog_query import project_catalog_columns

    with app.app_context():
        # 5 present rows + 5 null rows, page_size=5 -> page 1 ends exactly at boundary
        for index in range(5):
            row = _add_alert(
                alert_id=f'ztf_candidate:{1360000000000000000 + index}',
                mjd=60740.0 + index, locus_id=f'La{index}', object_id=f'Za{index}'
            )
            row.ant_mag_corrected = 20.0 - index
        for index in range(5):
            row = _add_alert(
                alert_id=f'ztf_candidate:{1360000000000000200 + index}',
                mjd=60750.0 + index, locus_id=f'Lb{index}', object_id=f'Zb{index}'
            )
            row.ant_mag_corrected = None
        db.session.commit()

        build = build_catalog_query({'sort__ant_mag_corrected': 'desc'})
        query = project_catalog_columns(build.list_query)
        rows, has_next = fetch_page(
            query, 1, page_size=5,
            sort_column=build.sort.column, sort_desc=True,
            segment_counts=(5, 5),
        )
        assert len(rows) == 5
        assert has_next is True

        # Without counts, it should also detect that the null segment has rows
        rows_no_counts, has_next_no_counts = fetch_page(
            query, 1, page_size=5,
            sort_column=build.sort.column, sort_desc=True,
            segment_counts=None,
        )
        assert len(rows_no_counts) == 5
        assert has_next_no_counts is True



def test_offset_boundary_page_renders_next_and_correct_total(client, app, monkeypatch):
    """10 present + 3 null, PAGE_SIZE=5, GET /?sort__ant_mag_corrected=desc&page=2 -> Next enabled, Rows: 13."""
    import poi_broker.app as app_module
    monkeypatch.setattr(app_module, 'PAGE_SIZE', 5)
    monkeypatch.setattr('poi_broker.services.catalog_list.PAGE_SIZE', 5)
    with app.app_context():
        clear_catalog_total_cache()
        for index in range(10):
            row = _add_alert(
                alert_id=f'ztf_candidate:{1370000000000000000 + index}',
                mjd=60760.0 + index, locus_id=f'Lc{index}', object_id=f'Zc{index}'
            )
            row.ant_mag_corrected = 20.0 - index
        for index in range(3):
            row = _add_alert(
                alert_id=f'ztf_candidate:{1370000000000000200 + index}',
                mjd=60770.0 + index, locus_id=f'Ld{index}', object_id=f'Zd{index}'
            )
            row.ant_mag_corrected = None
        db.session.commit()

    resp = client.get('/?sort__ant_mag_corrected=desc&page=2')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'page=3' in body
    assert '>13<' in body


def test_offset_and_cursor_agree_on_the_same_position(app):
    """`?page=N` and a cursor must return identical rows for the same N."""
    from poi_broker.services.catalog_list import parse_keyset_cursor
    from poi_broker.services.catalog_query import project_catalog_columns

    monkey_page_size = 3
    with app.app_context():
        for index in range(7):
            row = _add_alert(alert_id=f'ztf_candidate:{1350000000000000000 + index}',
                             mjd=60720.0 + index, locus_id=f'Lo{index}',
                             object_id=f'Zo{index}')
            row.ant_mag_corrected = 20.0 - index
        for index in range(4):
            row = _add_alert(alert_id=f'ztf_candidate:{1350000000000000300 + index}',
                             mjd=60730.0 + index, locus_id=f'Lp{index}',
                             object_id=f'Zp{index}')
            row.ant_mag_corrected = None
        db.session.commit()

        build = build_catalog_query({'sort__ant_mag_corrected': 'asc'})
        list_q = project_catalog_columns(build.list_query)
        counts = (7, 4)

        offset_rows, _ = fetch_page(
            list_q, 3, page_size=monkey_page_size,
            sort_column=build.sort.column, sort_desc=False,
            segment_counts=counts)
        offset_ids = [r.alert_id for r in offset_rows]

        # Walk cursor pages of the same size to reach the same position.
        cursor, page_rows = None, []
        for step in range(3):
            direction = 'first' if cursor is None else 'before'
            page_rows, has_next, _p = fetch_keyset(
                list_q, sort_column=build.sort.column, cursor=cursor,
                direction=direction, sort_desc=False,
                page_size=monkey_page_size, segment_counts=counts)
            assert parse_keyset_cursor(
                cursor_from_row(page_rows[-1], build.sort.attr).as_before_params(),
                'before', numeric=True) is not None
            if not has_next:
                break
            cursor = cursor_from_row(page_rows[-1], build.sort.attr)

        assert [r.alert_id for r in page_rows] == offset_ids
        assert [r.ant_mag_corrected for r in offset_rows][-1] is None, (
            'page 3 must include the first null-segment row'
        )


def test_first_page_falls_back_to_null_segment_when_no_values_exist(app):
    """A filter matching only NULL magnitudes must not render an empty page."""
    from poi_broker.services.catalog_query import project_catalog_columns

    with app.app_context():
        for index in range(3):
            row = _add_alert(alert_id=f'ztf_candidate:{1380000000000000000 + index}',
                             mjd=60740.0 + index, locus_id=f'Ln{index}',
                             object_id=f'Zn{index}')
            row.ant_mag_corrected = None
        db.session.commit()

        build = build_catalog_query({'sort__ant_mag_corrected': 'asc'})
        rows, has_next, has_prev = fetch_keyset(
            project_catalog_columns(build.list_query),
            sort_column=build.sort.column, cursor=None, direction='first',
            sort_desc=False, page_size=10, segment_counts=(0, 3),
        )
        assert len(rows) == 3, 'must fall through to the null segment'
        assert has_prev is False
        assert has_next is False


def test_last_page_is_the_null_tail_when_a_null_segment_exists(app):
    from poi_broker.services.catalog_query import project_catalog_columns

    with app.app_context():
        for index, mag in enumerate((20.0, 19.0, None, None)):
            row = _add_alert(alert_id=f'ztf_candidate:{1390000000000000000 + index}',
                             mjd=60750.0 + index, locus_id=f'Lm{index}',
                             object_id=f'Zm{index}')
            row.ant_mag_corrected = mag
        db.session.commit()

        build = build_catalog_query({'sort__ant_mag_corrected': 'asc'})
        rows, has_next, has_prev = fetch_keyset(
            project_catalog_columns(build.list_query),
            sort_column=build.sort.column, cursor=None, direction='last',
            sort_desc=False, page_size=2, segment_counts=(2, 2),
        )
        assert [r.ant_mag_corrected for r in rows] == [None, None]
        assert has_next is False
        assert has_prev is True, (
            'the null segment has exactly 2 rows and page_size is 2, so the '
            '2 present rows precede it: has_prev must be True'
        )

        # With a page smaller than the null segment there IS a previous page,
        # detected from the extra row on the reversed seek.
        rows, _n, has_prev = fetch_keyset(
            project_catalog_columns(build.list_query),
            sort_column=build.sort.column, cursor=None, direction='last',
            sort_desc=False, page_size=1, segment_counts=(2, 2),
        )
        assert [r.ant_mag_corrected for r in rows] == [None]
        assert has_prev is True

        # When no present rows exist at all and the null segment exactly
        # fills the page, this page is the whole table so has_prev is False.
        _rows_no_pres, _n, has_prev_no_pres = fetch_keyset(
            project_catalog_columns(build.list_query),
            sort_column=build.sort.column, cursor=None, direction='last',
            sort_desc=False, page_size=2, segment_counts=(0, 2),
        )
        assert has_prev_no_pres is False

        # A page_size larger than the table returns the whole table, values
        # first -- "last" is the tail of presentation order, not just the null
        # segment.
        rows, _n, has_prev = fetch_keyset(
            project_catalog_columns(build.list_query),
            sort_column=build.sort.column, cursor=None, direction='last',
            sort_desc=False, page_size=50, segment_counts=(2, 2),
        )
        assert [r.ant_mag_corrected for r in rows] == [19.0, 20.0, None, None]
        assert has_prev is False


def test_last_page_has_prev_when_null_segment_fills_page(app):
    """3 present + 5 null, page_size=5, direction='last' -> has_prev must be True."""
    from poi_broker.services.catalog_query import project_catalog_columns

    with app.app_context():
        for index in range(3):
            row = _add_alert(
                alert_id=f'ztf_candidate:{1395000000000000000 + index}',
                mjd=60755.0 + index, locus_id=f'Ln{index}', object_id=f'Zn{index}',
            )
            row.ant_mag_corrected = 18.0 + index
        for index in range(5):
            row = _add_alert(
                alert_id=f'ztf_candidate:{1395000000000000100 + index}',
                mjd=60760.0 + index, locus_id=f'Lo{index}', object_id=f'Zo{index}',
            )
            row.ant_mag_corrected = None
        db.session.commit()

        build = build_catalog_query({'sort__ant_mag_corrected': 'asc'})
        rows, has_next, has_prev = fetch_keyset(
            project_catalog_columns(build.list_query),
            sort_column=build.sort.column, cursor=None, direction='last',
            sort_desc=False, page_size=5, segment_counts=(3, 5),
        )
        assert len(rows) == 5
        assert [r.ant_mag_corrected for r in rows] == [None] * 5
        assert has_next is False
        assert has_prev is True


def test_last_page_route_prev_button_enabled_when_present_rows_exist(client, app, monkeypatch):
    """End-to-end: Prev link on Last page must not be disabled when present rows exist."""
    import poi_broker.app as app_module
    monkeypatch.setattr(app_module, 'PAGE_SIZE', 5)
    monkeypatch.setattr('poi_broker.services.catalog_list.PAGE_SIZE', 5)
    with app.app_context():
        clear_catalog_total_cache()
        for index in range(3):
            row = _add_alert(
                alert_id=f'ztf_candidate:{1396000000000000000 + index}',
                mjd=60765.0 + index, locus_id=f'Lp{index}', object_id=f'Zp{index}',
            )
            row.ant_mag_corrected = 18.0 + index
        for index in range(5):
            row = _add_alert(
                alert_id=f'ztf_candidate:{1396000000000000100 + index}',
                mjd=60770.0 + index, locus_id=f'Lq{index}', object_id=f'Zq{index}',
            )
            row.ant_mag_corrected = None
        db.session.commit()

    resp = client.get('/?sort__ant_mag_corrected=asc&last=1')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Prev button must be active, not disabled
    import re
    prev_match = re.search(r'<a href="([^"]+)" class="btn btn-primary\s*([^"]*)">\s*<i class="fa fa-caret-left', body)
    assert prev_match is not None
    assert 'disabled' not in prev_match.group(2)
    assert 'after_null=1' in prev_match.group(1)



def test_offset_page_past_end_is_404(client, app):
    with app.app_context():
        _add_alert(alert_id='ztf_candidate:1100000000000000001', mjd=60500.0, locus_id='Lx', object_id='Zx')
        db.session.commit()
    response = client.get('/?sort__locus_ra=asc&page=9')
    assert response.status_code == 404


def test_unselective_list_does_not_call_count_matches(client, app, monkeypatch):
    called = []

    def _fail_count(_query):
        called.append('count')
        return 0

    monkeypatch.setattr('poi_broker.services.catalog_list.count_matches', _fail_count)
    with app.app_context():
        for index in range(3):
            _add_alert(
                alert_id=f'ztf_candidate:{1200000000000000000 + index}',
                mjd=60600.0 + index,
                locus_id=f'Ln{index}',
                object_id=f'Zn{index}',
                passband='g',
            )
        db.session.commit()
    monkeypatch.setattr('poi_broker.app.PAGE_SIZE', 2)
    monkeypatch.setattr('poi_broker.services.catalog_list.PAGE_SIZE', 2)
    response = client.get('/?ant_passband=g')
    assert response.status_code == 200
    assert called == []


def test_catalog_alert_id_prefix_escapes_like_metacharacters(app):
    with app.app_context():
        build = build_catalog_query({'alert_id': 'ztf_'})
        compiled = build.list_query.statement.compile()
        sql = str(compiled)
        assert 'LIKE' in sql.upper()
        assert 'ESCAPE' in sql.upper()
        assert any(v == 'ztf\\_%' for v in compiled.params.values())


def test_cursor_value_format_round_trips_exactly():
    from poi_broker.services.catalog_list import _format_cursor_value, _parse_cursor_value

    # The cursor threshold survives a URL round-trip (float(text)); a lossy
    # serialization shifts the page boundary and duplicates or skips the
    # boundary row. '.16g' loses 1 ulp on ~27% of MJD values.
    for value in (59000.000000500004, 60000.123456789012, 12345.678901234567):
        assert _parse_cursor_value(_format_cursor_value(value), numeric=True) == value

    # Text sort columns must survive unchanged, and must not be float-parsed.
    for value in ('ztf_candidate:123', 'ANT2020a4c66', '', '9.5'):
        assert _parse_cursor_value(_format_cursor_value(value), numeric=False) == value


def test_cached_segment_counts_differentiates_filters_with_same_structure(app):
    """Two queries with the same sort key and structurally identical filters
    with different values must not collide in the segment-count cache."""
    from poi_broker.services.catalog_list import cached_segment_counts, clear_catalog_total_cache

    with app.app_context():
        clear_catalog_total_cache()
        r1 = Ztf(
            alert_id='ztf_candidate:9900000000000000001',
            date_alert_mjd=60000.0,
            locus_id='L1_test_cache',
            ztf_object_id='Z1',
            ant_mag_corrected=18.0,
        )
        r2 = Ztf(
            alert_id='ztf_candidate:9900000000000000002',
            date_alert_mjd=60001.0,
            locus_id='L2_test_cache',
            ztf_object_id='Z2',
            ant_mag_corrected=None,
        )
        db.session.add_all([r1, r2])
        db.session.commit()

        b1 = build_catalog_query({'locus_id': 'L1_test_cache', 'sort__ant_mag_corrected': 'asc'})
        c1 = cached_segment_counts(b1)
        assert c1 == (1, 0)

        b2 = build_catalog_query({'locus_id': 'L2_test_cache', 'sort__ant_mag_corrected': 'asc'})
        c2 = cached_segment_counts(b2)
        assert c2 == (0, 1), f"Expected (0, 1) for L2, but got {c2} (cache collision with L1!)"

        # Keyset fetch on 'last' for L2 must return the 1 null row rather than 0
        rows, has_next, has_prev = fetch_keyset(
            b2.list_query,
            sort_column=b2.sort.column,
            cursor=None,
            direction='last',
            sort_desc=b2.sort.desc,
            segment_counts=c2,
        )
        assert len(rows) == 1
        assert rows[0].alert_id == 'ztf_candidate:9900000000000000002'


def test_cached_segment_counts_bounds_cache_size(app, monkeypatch):
    """When _segment_count_cache reaches capacity, expired entries are pruned."""
    from poi_broker.services import catalog_list
    from poi_broker.services.catalog_list import (
        _segment_count_cache,
        cached_segment_counts,
        clear_catalog_total_cache,
    )

    with app.app_context():
        clear_catalog_total_cache()
        monkeypatch.setattr(catalog_list, '_MAX_SEGMENT_COUNT_CACHE', 3)
        # Fill cache with 3 entries with expired TTL
        for i in range(3):
            build_i = build_catalog_query({'locus_id': f'L_cap_{i}', 'sort__ant_mag_corrected': 'asc'})
            cached_segment_counts(build_i, ttl_seconds=-1.0)
        assert len(_segment_count_cache) == 3

        # Next query triggers pruning of expired entries
        build_new = build_catalog_query({'locus_id': 'L_cap_new', 'sort__ant_mag_corrected': 'asc'})
        cached_segment_counts(build_new, ttl_seconds=300.0)
        assert len(_segment_count_cache) <= 3
        assert any('L_cap_new' in k for k in _segment_count_cache.keys())


def test_cached_segment_counts_preserves_join_with_prob_class_filter(app):
    """cached_segment_counts must not emit SAWarning (cartesian product) or
    produce wrong counts when a prob_class filter is active."""
    import warnings
    from sqlalchemy.exc import SAWarning
    from poi_broker.services.catalog_list import cached_segment_counts, clear_catalog_total_cache

    with app.app_context():
        clear_catalog_total_cache()
        a1 = Ztf(
            alert_id='ztf_candidate:8800000000000000001',
            date_alert_mjd=60000.0,
            locus_id='L_sn1',
            ztf_object_id='Z_sn1',
            ant_mag_corrected=18.5,
        )
        c1 = Classification(alert_id=a1.alert_id, prob_class='sn')
        a2 = Ztf(
            alert_id='ztf_candidate:8800000000000000002',
            date_alert_mjd=60001.0,
            locus_id='L_sn2',
            ztf_object_id='Z_sn2',
            ant_mag_corrected=None,
        )
        c2 = Classification(alert_id=a2.alert_id, prob_class='sn')
        a3 = Ztf(
            alert_id='ztf_candidate:8800000000000000003',
            date_alert_mjd=60002.0,
            locus_id='L_cv',
            ztf_object_id='Z_cv',
            ant_mag_corrected=19.0,
        )
        c3 = Classification(alert_id=a3.alert_id, prob_class='cv')

        db.session.add_all([a1, a2, a3, c1, c2, c3])
        db.session.commit()

        build = build_catalog_query({'prob_class': 'sn', 'sort__ant_mag_corrected': 'asc'})
        with warnings.catch_warnings():
            warnings.simplefilter('error', SAWarning)
            counts = cached_segment_counts(build)

        assert counts == (1, 1)


def test_nulls_direction_reports_full_total_not_null_segment_size(client, app):
    """?nulls=1 must report the full filtered/unfiltered row count, not just the null-segment size."""
    from poi_broker.services.catalog_list import clear_catalog_total_cache

    with app.app_context():
        clear_catalog_total_cache()
        for i in range(3):
            _add_alert(
                alert_id=f'ztf_candidate:770000000000000000{i}',
                mjd=61500.0 + i,
                locus_id=f'L_tot_{i}',
                object_id=f'Z_tot_{i}',
            )
        for i in range(3, 5):
            a = _add_alert(
                alert_id=f'ztf_candidate:770000000000000000{i}',
                mjd=61500.0 + i,
                locus_id=f'L_tot_{i}',
                object_id=f'Z_tot_{i}',
            )
            a.ant_mag_corrected = None
        db.session.commit()

    resp = client.get('/?sort__ant_mag_corrected=asc&nulls=1')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '<span id="catalog-rows-value">5</span>' in html


def test_nulls_direction_on_non_nullable_sort_renders_first_page(client, app):
    """GET /?nulls=1 on a non-nullable sort renders page 1, not an empty page with inert Prev."""
    with app.app_context():
        _add_alert(
            alert_id='ztf_candidate:7710000000000000001',
            mjd=61500.0,
            locus_id='L_non_null_1',
            object_id='Z_non_null_1',
        )
        db.session.commit()

    resp = client.get('/?nulls=1')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'ztf_candidate:7710000000000000001' in html
    assert 'class="btn btn-primary disabled"><i class="fa fa-caret-left fa-lg"' in html


def test_nulls_direction_with_no_null_rows_returns_404(client, app):
    """GET ?sort__col=asc&nulls=1 when no NULL rows exist returns 404."""
    with app.app_context():
        _add_alert(
            alert_id='ztf_candidate:7720000000000000001',
            mjd=61500.0,
            locus_id='L_no_null',
            object_id='Z_no_null',
        )
        db.session.commit()

    resp = client.get('/?sort__ant_mag_corrected=asc&locus_id=L_no_null&nulls=1')
    assert resp.status_code == 404


def test_nulls_direction_reports_filtered_total_with_unselective_filter(client, app):
    """?nulls=1 with an unselective filter (e.g. ant_passband) reports the exact
    filtered total from segment_counts (e.g. Rows: 4), not '100+'."""
    from poi_broker.services.catalog_list import clear_catalog_total_cache

    with app.app_context():
        clear_catalog_total_cache()
        # Seed 4 alerts matching ant_passband='g' (3 with magnitude, 1 without)
        for i in range(3):
            _add_alert(
                alert_id=f'ztf_candidate:773000000000000000{i}',
                mjd=61500.0 + i,
                locus_id=f'L_filt_{i}',
                object_id=f'Z_filt_{i}',
                passband='g',
            )
        a_null = _add_alert(
            alert_id='ztf_candidate:7730000000000000003',
            mjd=61503.0,
            locus_id='L_filt_3',
            object_id='Z_filt_3',
            passband='g',
        )
        a_null.ant_mag_corrected = None
        # Seed 2 alerts matching ant_passband='r' (should be excluded)
        for i in range(4, 6):
            _add_alert(
                alert_id=f'ztf_candidate:773000000000000000{i}',
                mjd=61500.0 + i,
                locus_id=f'L_filt_{i}',
                object_id=f'Z_filt_{i}',
                passband='r',
            )
        db.session.commit()

    # On page 1: exactly 4 matching rows
    resp_p1 = client.get('/?sort__ant_mag_corrected=asc&ant_passband=g')
    assert resp_p1.status_code == 200
    assert '<span id="catalog-rows-value">4</span>' in resp_p1.get_data(as_text=True)

    # On ?nulls=1: renders the 1 null row, and reports Rows: 4 (not '100+' or '1')
    resp_nulls = client.get('/?sort__ant_mag_corrected=asc&ant_passband=g&nulls=1')
    assert resp_nulls.status_code == 200
    html_nulls = resp_nulls.get_data(as_text=True)
    assert '<span id="catalog-rows-value">4</span>' in html_nulls
    assert '<span id="catalog-rows-value">100+</span>' not in html_nulls


def test_nulls_direction_defers_broad_unselective_filter(client, app, monkeypatch):
    """When a broad unselective filter spans multiple pages (> PAGE_SIZE), both
    page 1 and ?nulls=1 consistently defer the count."""
    from poi_broker.services.catalog_list import clear_catalog_total_cache

    monkeypatch.setattr('poi_broker.services.catalog_list.PAGE_SIZE', 2)
    monkeypatch.setattr('poi_broker.app.PAGE_SIZE', 2)
    with app.app_context():
        clear_catalog_total_cache()
        # Seed 3 alerts with passband 'g' (2 present + 1 null > PAGE_SIZE of 2)
        for i in range(2):
            _add_alert(
                alert_id=f'ztf_candidate:774000000000000000{i}',
                mjd=61500.0 + i,
                locus_id=f'L_broad_{i}',
                object_id=f'Z_broad_{i}',
                passband='g',
            )
        a_null = _add_alert(
            alert_id='ztf_candidate:7740000000000000002',
            mjd=61502.0,
            locus_id='L_broad_2',
            object_id='Z_broad_2',
            passband='g',
        )
        a_null.ant_mag_corrected = None
        db.session.commit()

    resp_p1 = client.get('/?sort__ant_mag_corrected=asc&ant_passband=g')
    assert '<span id="catalog-rows-value">2+</span>' in resp_p1.get_data(as_text=True)

    resp_nulls = client.get('/?sort__ant_mag_corrected=asc&ant_passband=g&nulls=1')
    # This jump displays one row; without a total we only know that more rows
    # precede it. Do not claim a full-page lower bound for an arbitrary tail.
    assert '<span id="catalog-rows-value">1+</span>' in resp_nulls.get_data(as_text=True)






