"""Measure complete catalog requests against an existing SQLite copy, read-only.

    python tools/benchmark_catalog_counts.py --db /path/to/copy.db --compare-ref HEAD

Users storage is temporary. No indexes, statistics, or database contents change.
The comparison loads only the old start/total functions from the given Git ref.
"""

import argparse
import ast
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--compare-ref')
    parser.add_argument('--output')
    parser.add_argument('--legacy-budget', type=float, default=45.0)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--matrix', action='store_true', help='Also exercise all nullable sorts in both directions.')
    args = parser.parse_args()
    path = Path(args.db).resolve(strict=True)
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    report = {'database_bytes': path.stat().st_size, 'sqlite': sqlite3.sqlite_version,
              'note': 'Cold application metadata caches; OS disk cache is uncontrolled.', 'requests': []}
    with tempfile.TemporaryDirectory(prefix='catalog-benchmark-', dir=root / '.pytest_cache') as temp, ExitStack() as cleanup:
        os.environ.update(SECRET_KEY='benchmark-only', FLASK_TESTING='1', FLASK_DEBUG='0',
            HUEY_BACKEND='memory', ALERTS_DB_PATH=str(path), USERS_DB_PATH=str(Path(temp) / 'users.db'))
        import poi_broker
        import poi_broker.app as routes
        from poi_broker import db
        from poi_broker.services import catalog_list
        original_config = poi_broker.build_app_config
        def readonly_config(base):
            config, alerts, users = original_config(base)
            config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///file:{path.as_posix()}?mode=ro&uri=true'
            return config, alerts, users
        poi_broker.build_app_config = readonly_config
        def read_pragmas(connection, record):
            connection.execute('PRAGMA cache_size = -64000')
            connection.execute('PRAGMA mmap_size = 268435456')
            connection.execute('PRAGMA temp_store = MEMORY')
        poi_broker._configure_sqlite_pragmas = read_pragmas
        app = poi_broker.create_app()
        def close_databases():
            with app.app_context():
                db.session.remove()
                for engine in db.engines.values():
                    engine.dispose()
        cleanup.callback(close_databases)
        # Stable built-in observatory data avoids external astropy site downloads.
        routes._get_builtin_observatory_options = lambda: []
        with app.app_context():
            db.create_all(bind_key='users')
        client = app.test_client()
        endpoint = 'main.start'
        current_start = app.view_functions[endpoint]
        old_start = None
        if args.compare_ref:
            namespace = dict(catalog_list.__dict__)
            namespace.update(routes.__dict__)
            namespace['cached_segment_counts'] = catalog_list.cached_segment_counts
            namespace['cached_catalog_total'] = catalog_list.cached_catalog_total
            for file, symbol in [('poi_broker/services/catalog_list.py', 'resolve_catalog_total'), ('poi_broker/app.py', 'start')]:
                source = subprocess.check_output(['git', '-c', f'safe.directory={root.as_posix()}',
                    'show', f'{args.compare_ref}:{file}'], cwd=root, text=True, encoding='utf-8')
                fn = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == symbol)
                fn.decorator_list = []
                exec(compile(ast.Module(body=[fn], type_ignores=[]), file, 'exec'), namespace)
            old_start = namespace['start']
        from sqlalchemy import event
        statements = []
        plans = {}
        def record(conn, cursor, sql, params, context, many):
            statements.append(sql)
            if sql.lstrip().upper().startswith('SELECT'):
                plans[sql] = [r[-1] for r in conn.connection.driver_connection.execute('EXPLAIN QUERY PLAN ' + sql, params).fetchall()]
        with app.app_context():
            event.listen(db.engine, 'before_cursor_execute', record)
        def measure(label, url, old=False):
            app.view_functions[endpoint] = old_start if old else current_start
            catalog_list.clear_catalog_total_cache()
            statements.clear()
            with app.app_context():
                connection = db.session.connection().connection.driver_connection
                start = time.perf_counter()
                connection.set_progress_handler(lambda: time.perf_counter() - start > args.legacy_budget, 1000)
                try:
                    response = client.get(url)
                    elapsed = time.perf_counter() - start
                    html = response.get_data(as_text=True)
                    rows = re.search(r'id="catalog-rows-value">([^<]*)', html)
                    entry = {'label': label, 'url': url, 'seconds': round(elapsed, 4),
                             'status': response.status_code, 'rows_label': rows[1] if rows else None,
                             'sql_statements': len(statements),
                             'aggregate_sql': sum(bool(re.search(r'\bcount\s*\(', s, re.I)) for s in statements)}
                    report['requests'].append(entry)
                    print(json.dumps(entry), flush=True)
                    return html
                except Exception as exc:
                    entry = {'label': label, 'url': url, 'seconds': round(time.perf_counter() - start, 4),
                             'error': type(exc).__name__, 'budget_exceeded': 'interrupted' in str(exc),
                             'queries': [{'sql': sql, 'plan': plans.get(sql)} for sql in statements]}
                    report['requests'].append(entry)
                    print(json.dumps(entry), flush=True)
                    return ''
                finally:
                    connection.set_progress_handler(None, 0)
        base = '/?ant_passband=g&sort__ant_mag_corrected=asc'
        if old_start:
            measure('before: first, cold metadata', base, old=True)
        html = measure('after: first, cold metadata', base)
        next_link = re.search(r'<a href="([^"]+)"[^>]*>Next ', html)
        if next_link:
            measure('after: next, cold metadata', next_link[1].replace('&amp;', '&'))
        measure('after: last, cold metadata', base + '&last=1')
        measure('after: missing values, cold metadata', base + '&nulls=1')
        measure('after: numbered page 2, cold metadata', base + '&page=2')
        measure('after: default, cold metadata', '/')
        measure('after: optional count', '/api/catalog-count?ant_passband=g')
        # Do not clear the circuit breaker: repeated optional calls should be immediate.
        start = time.perf_counter()
        response = client.get('/api/catalog-count?ant_passband=g')
        entry = {'label': 'after: repeated optional count', 'status': response.status_code,
                 'seconds': round(time.perf_counter() - start, 4)}
        report['requests'].append(entry)
        print(json.dumps(entry), flush=True)
        for trial in range(2, args.repeats + 1):
            for label, url in [('first', base), ('last', base + '&last=1'),
                               ('missing values', base + '&nulls=1'), ('numbered page 2', base + '&page=2'), ('default', '/')]:
                measure(f'after: {label}, trial {trial}, cold metadata', url)
        if args.matrix:
            for key in ('ant_mag_corrected', 'ztf_object_id', 'locus_ra', 'locus_dec'):
                for direction in ('asc', 'desc'):
                    url = f'/?ant_passband=g&sort__{key}={direction}'
                    for suffix in ('', '&last=1', '&nulls=1'):
                        measure(f'matrix: {key} {direction}{suffix}', url + suffix)
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
