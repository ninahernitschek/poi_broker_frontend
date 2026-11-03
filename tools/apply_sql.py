"""Run a tools/*.sql migration against an alerts database.

There is no sqlite3 CLI on this machine, so this is the supported way to apply
the one-shot migration scripts.

    python tools/apply_sql.py tools/apply_fix_bad_magnitudes.sql --db <path>
    python tools/apply_sql.py tools/apply_catalog_sort_indexes.sql --db <path>

Refuses to run without --db and without --yes, so a mistyped path cannot
silently modify the wrong file. Prints the file size and page count before and
after, plus elapsed time.

Both migration scripts are written to be re-runnable (IF NOT EXISTS / IF EXISTS,
and UPDATE predicates that match nothing once applied), so an interrupted run is
recovered by running the same command again. Individual DDL/DML statements are
atomic on their own; a partial run leaves the database in a state the same
command completes. Scripts with an explicit BEGIN/COMMIT are rolled back on
failure; scripts without them retain per-statement atomicity. SELECT results
are printed so pre-flight and post-check output can be captured.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path


def state(path: str) -> tuple[int, int, int]:
    """(bytes, page_count, freelist_count) without opening read-write."""
    size = os.path.getsize(path)
    con = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        pages = con.execute('PRAGMA page_count').fetchone()[0]
        free = con.execute('PRAGMA freelist_count').fetchone()[0]
    finally:
        con.close()
    return size, pages, free


def execute_script(con: sqlite3.Connection, sql: str) -> None:
    """Execute complete statements, preserving transactions and SELECT output.

    complete_statement handles quoted semicolons, comments and trigger bodies;
    splitting directly on semicolons would corrupt those SQL statements.
    """
    def execute(statement: str) -> None:
        cursor = con.execute(statement)
        if cursor.description:
            print('\t'.join(column[0] for column in cursor.description))
            for row in cursor:
                print('\t'.join('NULL' if value is None else str(value) for value in row))

    start = 0
    for end, character in enumerate(sql, 1):
        if character == ';' and sqlite3.complete_statement(sql[start:end]):
            execute(sql[start:end])
            start = end
    if sql[start:].strip():
        execute(sql[start:])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('script', help='path to the .sql file to apply')
    parser.add_argument('--db', required=True, help='alerts SQLite file to modify')
    parser.add_argument('--yes', action='store_true',
                        help='required: confirms you have a backup')
    args = parser.parse_args()

    if not os.path.exists(args.script):
        print(f'migration script not found: {args.script}')
        return 2
    if not os.path.exists(args.db):
        print(f'database not found: {args.db}')
        return 2
    if not args.yes:
        print('Refusing to modify the database without --yes.')
        print('Take a backup first:  Copy-Item <db> <db>.bak')
        return 2

    db = os.path.abspath(args.db)
    before_bytes, before_pages, before_free = state(db)
    print(f'database : {db}')
    print(f'script   : {args.script}')
    print(f'before   : {before_bytes/1e9:.2f} GB  '
          f'({before_pages:,} pages, {before_free:,} free)')

    sql = Path(args.script).read_text(encoding='utf-8')
    con = sqlite3.connect(db, isolation_level=None)
    con.execute('PRAGMA busy_timeout=120000')
    started = time.time()
    applied = False
    try:
        execute_script(con, sql)
        if con.in_transaction:
            raise sqlite3.ProgrammingError('SQL script left a transaction open; add COMMIT.')
        applied = True
    except Exception as exc:  # noqa: BLE001 - report and stop, do not mask
        con.rollback()
        print(f'\nFAILED after {time.time()-started:.1f}s: {exc}')
        print('Any open transaction was rolled back.')
        print('The scripts are re-runnable: fix the cause and run the same '
              'command again.')
        return 1
    finally:
        if applied:
            try:
                con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            except sqlite3.Error as exc:
                print(f'Checkpoint warning: {exc}')
        con.close()

    after_bytes, after_pages, after_free = state(db)
    print(f'\napplied in {time.time()-started:.1f}s')
    print(f'after    : {after_bytes/1e9:.2f} GB  '
          f'({after_pages:,} pages, {after_free:,} free)')
    print(f'delta    : {(after_bytes-before_bytes)/1e6:+.0f} MB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
