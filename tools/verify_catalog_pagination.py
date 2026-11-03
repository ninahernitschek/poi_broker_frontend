"""Verify catalog sorting and keyset pagination against a real alerts database.

Run against a COPY of production, never production itself:

    VERIFY_DB=/path/to/copy.db python tools/verify_catalog_pagination.py

Without VERIFY_DB it uses ALERTS_DB_PATH, or falls back to the default
production path (and will then run read-only queries against it).

Checks, all via the app's own query-building code rather than hand-written SQL:

  1. no sort produces a full table sort
  2. a deep keyset walk returns exactly the rows the OFFSET path does
  3. page depth does not change the cost of a page
  4. the "Last" link reaches the true final page and terminates
  5. no storage-class filter is emitted, and the count covers every row
  6. data invariant: no non-numeric ant_mag_corrected
  7. every segment phase is index-ranged (never a full index SCAN)
  8. INDEPENDENT ORACLE: the two-phase walk matches a single-query ordering
     built with a leading ``col IS NULL`` term, checked across a 200-row
     window straddling the present/null boundary. This is the one assertion
     that is correct by construction rather than sharing the segmentation
     logic with the code under test -- it costs one full sort (~3.8 s) once.

Exit code is non-zero if any check fails.
"""

from __future__ import annotations

import os
import sys
import time

DEFAULT_DB = 'C:/dev/python/_broker_db/ztf_alerts_stream.db'
DEEP_OFFSET = 150_000
DEEP_PAGES = (1, 10_000, 20_000)
# A page of 100 rows over a 3.18M-row table is a sub-millisecond operation once
# the composite sort index exists. Generous enough for slow disks and a cold
# cache, tight enough that the pre-migration 9 s full sort fails.
FIRST_PAGE_BUDGET_S = 1.0
DEEP_PAGE_BUDGET_S = 0.05
# Rows either side of the present->null boundary the oracle check compares.
ORACLE_WINDOW = 100
ORACLE_SPAN = 200


def _offset_rows(list_q, offset: int, limit: int, build, counts):
    """Rows [offset, offset+limit) in presentation order, via the OFFSET path."""
    from poi_broker.services.catalog_list import fetch_page

    sort = build.sort
    if not sort.nullable or counts is None:
        return list_q.limit(limit).offset(offset).all()
    page_size = limit
    rows, _ = fetch_page(list_q, offset // page_size + 1, page_size=page_size,
                         sort_column=sort.column, sort_desc=sort.desc,
                         segment_counts=counts)
    return rows[offset % page_size:offset % page_size + limit]


def _oracle_rows(db, build, offset: int, span: int):
    """Ground truth for a window, from a single query.

    ``ORDER BY (col IS NULL), col, alert_id, locus_id`` puts non-NULL values
    first and NULLs last -- exactly the presentation order -- but cannot be
    served by the composite index, because the leading expression is not an
    index column. That is the point: it is correct by construction and shares
    no logic with the two-phase walk it is checking. Expect a full sort.
    """
    sort = build.sort
    column = sort.column
    table = column.class_
    stmt = (
        db.select(table.alert_id, table.locus_id)
        .order_by(
            column.is_(None),
            column.asc() if not sort.desc else column.desc(),
            table.alert_id.asc() if not sort.desc else table.alert_id.desc(),
            table.locus_id.asc() if not sort.desc else table.locus_id.desc(),
        )
        .limit(span)
        .offset(offset)
    )
    return db.session.execute(stmt).all()


def _two_phase_rows(list_q, build, counts, offset: int, span: int):
    """The same window, produced by the code under test (fetch_page)."""
    from poi_broker.services.catalog_list import fetch_page

    sort = build.sort
    if not sort.nullable:
        return list_q.limit(span).offset(offset).all()
    page_size = span
    start_page = offset // page_size + 1
    rows, _ = fetch_page(list_q, start_page, page_size=page_size,
                         sort_column=sort.column, sort_desc=sort.desc,
                         segment_counts=counts)
    within = offset % page_size
    return rows[within:within + span]


def _phase_sql(list_q, sort, direction: str):
    """The single SQL statement one segment phase compiles to."""
    from poi_broker.services.catalog_list import _segment_order_terms, _segment_predicate

    base = list_q.order_by(None)
    segment = 'null' if direction == 'nulls' else 'present'
    return base.filter(_segment_predicate(sort.column, segment)).order_by(
        *_segment_order_terms(sort.column, segment, False)).statement


def main() -> int:
    db_path = os.environ.get('VERIFY_DB') or os.environ.get('ALERTS_DB_PATH') or DEFAULT_DB
    if not os.path.exists(db_path):
        print(f'alerts database not found: {db_path}')
        return 2

    os.environ.setdefault('SECRET_KEY', 'verify-only')
    os.environ['FLASK_TESTING'] = '1'
    os.environ['FLASK_DEBUG'] = '0'
    os.environ['ALERTS_DB_PATH'] = db_path
    os.environ.setdefault(
        'USERS_DB_PATH', os.path.join(os.environ.get('TEMP', '.'), 'verify_catalog_users.db')
    )
    os.environ.setdefault('HUEY_BACKEND', 'memory')
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from poi_broker import create_app, db
    from poi_broker.services.catalog_list import (
        cached_segment_counts,
        count_matches,
        cursor_from_row,
        fetch_keyset,
        fetch_page,
    )
    from poi_broker.services.catalog_query import (
        SORT_KEYS,
        build_catalog_query,
        project_catalog_columns,
    )

    app = create_app()
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = '') -> None:
        print(f'  [{"PASS" if ok else "FAIL"}] {label}{(" - " + detail) if detail else ""}')
        if not ok:
            failures.append(label)

    def best(fn, n=2):
        out = None
        for _ in range(n):
            t = time.time()
            rows = fn()
            d = time.time() - t
            out = d if out is None else min(out, d)
        return out, rows

    with app.app_context():
        print(f'database: {db_path}')
        indexes = {
            r[0] for r in db.session.execute(
                db.text("SELECT name FROM sqlite_master WHERE type='index'")
            ).fetchall()
        }
        composites = sum(1 for n in indexes if n.startswith('idx_featuretable_sort_'))
        print(f'composite sort indexes present: {composites} of 6')
        if composites != 6:
            print('  (apply tools/apply_catalog_sort_indexes.sql first; checks 1-3')
            print('   will still pass for most sorts, but the first page will be slow)')
        print()

        print('1. first and last page are fast (not a scan-time full sort)')
        # Deliberately a timing check, not a plan-string check. Without the
        # composite indexes the plan reads
        #   SCAN ... USING INDEX idx_featuretable_date_alert_mjd
        #     | USE TEMP B-TREE FOR LAST 2 TERMS OF ORDER BY
        # -- no "TEMP B-TREE FOR ORDER BY", yet 9 s, because a scan cannot
        # stream a partial sort: the temp b-tree spans every row. Grepping the
        # plan for the full-sort marker passes on the broken system, so measure.
        for key in SORT_KEYS:
            build = build_catalog_query({f'sort__{key}': 'asc'})
            list_q = project_catalog_columns(build.list_query)
            sql = str(list_q.statement.compile(
                db.engine, compile_kwargs={'literal_binds': True}))
            plan = ' | '.join(str(r[-1]) for r in db.session.execute(
                db.text('EXPLAIN QUERY PLAN ' + sql)).fetchall())
            t_first, _ = best(lambda: fetch_keyset(
                list_q, sort_column=build.sort.column, cursor=None,
                direction='first', sort_desc=False, page_size=100))
            t_last, _ = best(lambda: fetch_keyset(
                list_q, sort_column=build.sort.column, cursor=None,
                direction='last', sort_desc=False, page_size=100))
            detail = f'first {t_first:.3f}s, last {t_last:.3f}s'
            if 'TEMP B-TREE' in plan:
                detail += '  [sorts at query time]'
            check(f'{key:20}', max(t_first, t_last) < FIRST_PAGE_BUDGET_S, detail)

        print()
        print(f'2. deep keyset walk == segmented OFFSET walk (offset {DEEP_OFFSET:,})')
        for key in SORT_KEYS:
            build = build_catalog_query({f'sort__{key}': 'asc'})
            list_q = project_catalog_columns(build.list_query)
            counts = cached_segment_counts(build)
            # Ground truth comes from fetch_page, not list_q.offset(): the raw
            # OFFSET inherits SQLite's native ordering (NULLs first ascending),
            # which is no longer the presentation order.
            truth = [
                (r.alert_id, r.locus_id)
                for r in _offset_rows(list_q, DEEP_OFFSET, 300, build, counts)
            ]
            if not truth:
                print(f'  [SKIP] {key:20} no rows at that offset')
                continue
            seed = _offset_rows(list_q, DEEP_OFFSET - 100, 100, build, counts)
            cur = cursor_from_row(seed[-1], build.sort.attr)
            walked = []
            for _ in range(3):
                page, has_next, _ = fetch_keyset(
                    list_q, sort_column=build.sort.column, cursor=cur,
                    direction='before', sort_desc=False, page_size=100,
                    segment_counts=counts)
                if not page:
                    break
                walked.extend((r.alert_id, r.locus_id) for r in page)
                cur = cursor_from_row(page[-1], build.sort.attr)
            walked = walked[:len(truth)]
            detail = f'{len(walked)} vs {len(truth)} rows'
            if walked and walked != truth:
                i = next(i for i, (a, b) in enumerate(zip(walked, truth)) if a != b)
                detail = f'first divergence @{i}: {walked[i]} vs {truth[i]}'
            check(f'{key:20}', walked == truth, detail)

        print()
        print('3. page depth does not change cost')
        for key in SORT_KEYS:
            build = build_catalog_query({f'sort__{key}': 'desc'})
            list_q = project_catalog_columns(build.list_query)
            timings = {}
            for page in DEEP_PAGES:
                seed = list_q.limit(100).offset((page - 1) * 100).all()
                if not seed:
                    continue
                cur = cursor_from_row(seed[-1], build.sort.attr)
                d, _ = best(lambda: fetch_keyset(
                    list_q, sort_column=build.sort.column, cursor=cur,
                    direction='before', sort_desc=True, page_size=100))
                timings[page] = d
            if timings:
                worst = max(timings.values())
                check(f'{key:20}', worst < DEEP_PAGE_BUDGET_S,
                      '  '.join(f'page {p:,}: {v:.4f}s' for p, v in timings.items()))

        print()
        print('4. "Last" reaches the true final page and terminates')
        for key in SORT_KEYS:
            build = build_catalog_query({f'sort__{key}': 'asc'})
            list_q = project_catalog_columns(build.list_query)
            last, _, _ = fetch_keyset(list_q, sort_column=build.sort.column, cursor=None,
                                      direction='last', sort_desc=False, page_size=100)
            if not last:
                print(f'  [SKIP] {key:20} empty result set')
                continue
            _, has_next, _ = fetch_keyset(
                list_q, sort_column=build.sort.column,
                cursor=cursor_from_row(last[-1], build.sort.attr),
                direction='before', sort_desc=False, page_size=100)
            check(f'{key:20}', not has_next)

        print()
        print('5. no storage-class filter, and the count covers every row')
        for key in SORT_KEYS:
            build = build_catalog_query({f'sort__{key}': 'asc'})
            sql = str(build.list_query.statement.compile(
                db.engine, compile_kwargs={'literal_binds': True}))
            count_sql = str(build.count_query.statement.compile(
                db.engine, compile_kwargs={'literal_binds': True}))
            filtered = 'TYPEOF' in sql.upper() or 'TYPEOF' in count_sql.upper()
            check(f'{key:20} nullable={build.sort.nullable}', not filtered,
                  'no TYPEOF predicate' if not filtered else 'STILL FILTERING')

        # Every sort must now report the same total: the null segment is
        # listed, not hidden.
        total = db.session.execute(db.text(
            'SELECT COUNT(*) FROM featuretable')).scalar_one()
        for key in SORT_KEYS:
            build = build_catalog_query({f'sort__{key}': 'asc'})
            reported = count_matches(build.count_query)
            check(f'{key:20} count == {total:,}', reported == total,
                  f'reported {reported:,}')

        # Data invariant owned by tools/apply_fix_bad_magnitudes.sql. Without
        # that migration 83 rows hold a passband token in ant_mag_corrected,
        # which is reachable from page 1 of a descending magnitude sort and
        # made mag_filter() raise TypeError (a 500 for the whole page).
        print()
        print('6. data invariant: no non-numeric ant_mag_corrected (apply_fix_bad_magnitudes.sql)')
        numeric = db.session.execute(db.text(
            "SELECT COUNT(*) FROM featuretable WHERE "
            "typeof(ant_mag_corrected) IN ('integer','real')")).scalar_one()
        nulls = db.session.execute(db.text(
            'SELECT COUNT(*) FROM featuretable '
            'WHERE ant_mag_corrected IS NULL')).scalar_one()
        bad = db.session.execute(db.text(
            "SELECT COUNT(*) FROM featuretable WHERE "
            "typeof(ant_mag_corrected) NOT IN ('integer','real') "
            "AND ant_mag_corrected IS NOT NULL")).scalar_one()
        total = db.session.execute(db.text(
            'SELECT COUNT(*) FROM featuretable')).scalar_one()
        check('zero non-numeric magnitudes', bad == 0, f'{bad} rows still bad')
        check('null + numeric == total', nulls + numeric == total,
              f'{nulls:,} + {numeric:,} vs {total:,}')
        print(f'       listable under a magnitude sort: {total:,} '
              f'(the {nulls:,} with no magnitude are listed last, not hidden)')

        print()
        print('7. every segment phase is index-ranged, never a full index SCAN')
        # The trap this guards: expressing both segments as one OR-ed predicate,
        # or adding a leading `col IS NULL` term to ORDER BY, both degrade to
        # SCAN ... USING COVERING INDEX and cost seconds per page.
        for key in SORT_KEYS:
            build = build_catalog_query({f'sort__{key}': 'asc'})
            if not build.sort.nullable:
                print(f'  [SKIP] {key:20} non-nullable, single segment')
                continue
            list_q = project_catalog_columns(build.list_query)
            worst, scanned = 0.0, []
            for direction in ('first', 'last', 'nulls'):
                sql = str(_phase_sql(list_q, build.sort, direction).compile(
                    db.engine, compile_kwargs={'literal_binds': True}))
                plan = ' | '.join(str(r[-1]) for r in db.session.execute(
                    db.text('EXPLAIN QUERY PLAN ' + sql)).fetchall())
                if 'SCAN' in plan and 'COVERING' in plan:
                    scanned.append(direction)
                t, _ = best(lambda d=direction: fetch_keyset(
                    list_q, sort_column=build.sort.column, cursor=None,
                    direction=d, sort_desc=False, page_size=100,
                    segment_counts=cached_segment_counts(build)))
                worst = max(worst, t)
            check(f'{key:20}', not scanned and worst < DEEP_PAGE_BUDGET_S,
                  f'slowest phase {worst:.4f}s'
                  + (f'  SCAN in {scanned}' if scanned else ''))

        print()
        print('8. INDEPENDENT ORACLE: two-phase walk == single-query ordering')
        print(f'   (window of {ORACLE_SPAN} rows straddling the boundary; '
              'one full sort, correct by construction)')
        for key in SORT_KEYS:
            build = build_catalog_query({f'sort__{key}': 'asc'})
            counts = cached_segment_counts(build)
            if not build.sort.nullable:
                print(f'  [SKIP] {key:20} non-nullable, single segment')
                continue
            present, nulls_total = counts
            if present == 0 or nulls_total == 0:
                print(f'  [SKIP] {key:20} only one segment present ({counts})')
                continue
            # Align to a whole page of the OFFSET path so the two-phase walk
            # and the oracle describe the same window, while still straddling
            # the present->null boundary.
            offset = ((present - ORACLE_WINDOW) // ORACLE_SPAN) * ORACLE_SPAN

            t0 = time.time()
            oracle = _oracle_rows(db, build, offset, ORACLE_SPAN)
            oracle_cost = time.time() - t0
            walked = _two_phase_rows(
                list_q := project_catalog_columns(build.list_query),
                build, counts, offset, ORACLE_SPAN)
            ids_oracle = [(r[0], r[1]) for r in oracle]
            ids_walked = [(r.alert_id, r.locus_id) for r in walked]
            if not ids_oracle:
                print(f'  [SKIP] {key:20} oracle returned nothing')
                continue
            ok = ids_oracle == ids_walked
            detail = f'{len(ids_oracle)} rows from offset {offset:,}'
            detail += f'  [oracle {oracle_cost:.1f}s]'
            if not ok and len(ids_oracle) == len(ids_walked):
                i = next(i for i, (a, b) in enumerate(zip(ids_oracle, ids_walked))
                         if a != b)
                detail += f'  first divergence @{i}'
            check(f'{key:20}', ok, detail)

    print()
    print(f'FAILURES: {len(failures)}')
    for name in failures:
        print(f'  - {name}')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
