"""Keyset (cursor) pagination for the catalog, for every sortable column.

The catalog list is paged with a keyset cursor over
``(sort_column, alert_id, locus_id)`` — a total order, because (alert_id,
locus_id) is unique across the table. OFFSET remains only as a legacy fallback
for explicit ``?page=N`` URLs (see ``MAX_PAGE``).

## Two segments: rows with a sort value, then rows without one

A nullable sort column splits the listing into two segments:

* **present** — ``sort_column IS NOT NULL``, ordered by the sort column;
* **null** — ``sort_column IS NULL``, ordered by (alert_id, locus_id) alone.

The present segment always comes first, so a NULL value sorts last in *both*
directions rather than SQLite's native "NULLs first in ASC".

This split is unavoidable, not cosmetic. A row-value comparison against a NULL
bound parameter is NULL, i.e. false, for *every* row — measured on the
production table, ``(c,a,b) > (NULL,'a','1')`` returns nothing at all, not
even the non-NULL rows past the block. A single-phase cursor therefore dies the
moment it enters the null segment.

Each segment is a separate point-seek on the composite sort index
``(sort_column, alert_id, locus_id)``: ``IS NULL`` and ``IS NOT NULL`` are
contiguous key ranges on the leading column. Measured against the 3.18M-row
production table: 0.24-1.40 ms per phase.

What must NOT be done instead: expressing both segments as one ``OR``-ed
predicate, or adding a leading ``col IS NULL`` term to ORDER BY. Both degrade
to ``SCAN USING COVERING INDEX`` — a full index scan — because no index
constraint survives. The former measured a plan with no range restriction at
all; the latter 3.82 s/page. Keep the segments in Python.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Query

from poi_broker import db
from poi_broker.models import Ztf
from poi_broker.services.catalog_query import CatalogQueryBuild

PAGE_SIZE = 100
# Segment identifiers for the NULL split described in the module docstring.
SEGMENT_PRESENT = 'present'
SEGMENT_NULL = 'null'
# Ceiling for the legacy OFFSET path, i.e. `?page=N` URLs typed or bookmarked by
# hand. In-app navigation is entirely keyset now (First/Prev/Next/Last), and
# every link the UI renders is a cursor, so this only bounds a direct OFFSET
# scan that can walk the whole index. Measured on the 3.18M-row production
# table, the app's own keyset query is ~0 ms per page at any depth, so the cap
# no longer restricts how much of the catalog a user can browse.
MAX_PAGE = 1000
CATALOG_TOTAL_TTL_SECONDS = 300.0
AUTO_COUNT_BUDGET_SECONDS = 0.1
OPTIONAL_COUNT_BUDGET_SECONDS = 2.0
_slow_count_filters: dict[str, float] = {}

_catalog_total_cache: dict[str, float | int | str | None] = {
    'value': None,
    'expires': 0.0,
    'signature': None,
}

# Exact segment sizes for explicit migration/verification tools. Web requests
# never call this cache: a miss aggregates the entire filtered result.
_MAX_SEGMENT_COUNT_CACHE = 1024
_segment_count_cache: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class KeysetCursor:
    """Position in the total order.

    ``sort_value is None`` means the cursor sits in the **null** segment, whose
    internal order is (alert_id, locus_id) with no sort value at all.
    """

    sort_value: Any
    alert_id: str
    locus_id: str

    @property
    def segment(self) -> str:
        return SEGMENT_NULL if self.sort_value is None else SEGMENT_PRESENT

    def as_before_params(self) -> dict[str, str]:
        return self._as_params('before')

    def as_after_params(self) -> dict[str, str]:
        return self._as_params('after')

    def _as_params(self, prefix: str) -> dict[str, str]:
        params = {
            f'{prefix}_alert_id': self.alert_id,
            f'{prefix}_locus_id': self.locus_id,
        }
        if self.sort_value is None:
            # A dedicated flag rather than a sentinel string inside *_v: an
            # empty string is a legitimate value for a text sort column, so
            # overloading it would make the two indistinguishable.
            params[f'{prefix}_null'] = '1'
        else:
            params[f'{prefix}_v'] = _format_cursor_value(self.sort_value)
        return params


def _format_cursor_value(value: Any) -> str:
    """Serialize a cursor value losslessly for a URL round-trip.

    repr() is the shortest string that round-trips a float exactly; '.16g'
    silently shifts ~27% of MJD values by 1 ulp on the way back, which moves
    the page boundary and duplicates or skips the boundary row. Text values
    pass through unchanged. None never reaches here -- it is carried by the
    companion *_null flag (see KeysetCursor._as_params).
    """
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _parse_cursor_value(text: str, numeric: bool) -> Any:
    if not numeric:
        return text
    try:
        return float(text)
    except ValueError:
        return None


def clear_catalog_total_cache() -> None:
    _catalog_total_cache['value'] = None
    _catalog_total_cache['expires'] = 0.0
    _catalog_total_cache['signature'] = None
    _segment_count_cache.clear()
    _slow_count_filters.clear()


def cached_catalog_total(
    ttl_seconds: float = CATALOG_TOTAL_TTL_SECONDS,
    *,
    signature: str = 'all',
) -> int:
    """Unfiltered featuretable COUNT(*), cached per process.

    The count is no longer restricted to rows the listing can order, so it is
    genuinely the catalog total (measured 3,178,668) for every sort. The
    signature stays because a filtered request must not read the unfiltered
    figure; it now varies only with the user's own filters.
    """
    now = time.monotonic()
    cached_value = _catalog_total_cache['value']
    expires = _catalog_total_cache['expires']
    if (
        cached_value is not None
        and _catalog_total_cache['signature'] == signature
        and now < float(expires)
    ):
        return int(cached_value)

    statement = select(func.count()).select_from(Ztf)
    total = db.session.execute(statement).scalar_one()
    _catalog_total_cache['value'] = int(total)
    _catalog_total_cache['expires'] = now + ttl_seconds
    _catalog_total_cache['signature'] = signature
    return int(total)


def _segment_filter_signature(whereclause) -> str:
    if whereclause is None:
        return 'all'
    try:
        return str(whereclause.compile(compile_kwargs={'literal_binds': True}))
    except Exception:
        return str(whereclause)


def cached_segment_counts(
    build: CatalogQueryBuild,
    *,
    ttl_seconds: float = CATALOG_TOTAL_TTL_SECONDS,
) -> tuple[int, int] | None:
    """(present, null) row counts for the current filters and sort column.

    Cached per (filter signature, sort column) because each miss costs a COUNT
    over the whole table, which is far too slow for the request path. Returns
    None when the sort column cannot be NULL, letting the caller skip the
    bookkeeping entirely -- that is the case for the three primary-key sorts.
    """
    sort = build.sort
    if not sort.nullable:
        return None
    whereclause = build.count_query.whereclause
    signature = _segment_filter_signature(whereclause)
    key = f'{sort.key}|{signature}'
    now = time.monotonic()
    entry = _segment_count_cache.get(key)
    if entry is not None and now < float(entry['expires']):
        return int(entry['present']), int(entry['null'])

    row = (
        build.count_query.order_by(None)
        .with_entities(
            func.count(Ztf.alert_id),
            func.count(Ztf.alert_id).filter(sort.column.is_(None)),
        )
        .one()
    )
    total = int(row[0])
    null_count = int(row[1])
    present = total - null_count

    if len(_segment_count_cache) >= _MAX_SEGMENT_COUNT_CACHE:
        expired_keys = [
            k for k, v in _segment_count_cache.items()
            if now >= float(v['expires'])
        ]
        for k in expired_keys:
            _segment_count_cache.pop(k, None)
        if len(_segment_count_cache) >= _MAX_SEGMENT_COUNT_CACHE:
            _segment_count_cache.clear()

    _segment_count_cache[key] = {
        'present': present,
        'null': null_count,
        'expires': now + ttl_seconds,
    }
    return present, null_count


def count_matches(query: Query) -> int:
    """COUNT(*) on an already-filtered query; strips ORDER BY.

    Counting the *primary entity* is deliberate. `with_entities(func.count())`
    on a bare Query emits ``SELECT count(*)`` with no FROM at all, which SQLite
    evaluates as the constant 1 -- so an unfiltered count silently reported 1
    regardless of table size. Naming an explicit entity keeps the FROM clause.
    Any caller that has replaced the entity list (the list query) must count a
    column instead, since there is no single entity left to name.
    """
    descriptions = query.column_descriptions
    first = descriptions[0] if descriptions else None
    entity = first.get('entity') if first else None
    if entity is not None:
        # `expr` is the mapped class here, not a column, so count one of its
        # primary-key columns -- which also forces the FROM clause back in.
        pk_columns = list(entity.__table__.primary_key.columns)
        target = func.count(pk_columns[0]) if pk_columns else func.count(Ztf.alert_id)
    else:
        # Projected query: count the first selected expression.
        target = func.count(first['expr']) if first else func.count(Ztf.alert_id)
    match_count = query.order_by(None).with_entities(target).scalar()
    return int(match_count or 0)


def _budgeted_query(query: Query, seconds: float, *, scalar: bool = False):
    """Run optional metadata on its own connection; None means budget exhausted.

    SQLite's progress handler cancels database work, rather than merely timing
    out the HTTP caller while leaving a scan running. Never install it on the
    request session's connection, and always clear it before returning to pool.
    """
    if seconds <= 0:
        return None
    engine = query.session.get_bind(mapper=Ztf)
    if engine.dialect.name != 'sqlite':
        return None
    deadline = time.perf_counter() + seconds
    with engine.connect() as connection:
        driver = connection.connection.driver_connection
        old_timeout = driver.execute('PRAGMA busy_timeout').fetchone()[0]
        driver.execute(f'PRAGMA busy_timeout = {max(1, int(seconds * 1000))}')
        driver.set_progress_handler(lambda: time.perf_counter() >= deadline, 1000)
        try:
            result = connection.execute(query.statement)
            return result.scalar_one() if scalar else result.all()
        except OperationalError as exc:
            if 'interrupted' not in str(exc.orig) and 'locked' not in str(exc.orig):
                raise
            return None
        finally:
            driver.set_progress_handler(None, 0)
            driver.execute(f'PRAGMA busy_timeout = {old_timeout}')


def count_is_available(build: CatalogQueryBuild) -> bool:
    key = _segment_filter_signature(build.count_query.whereclause)
    return time.monotonic() >= _slow_count_filters.get(key, 0.0)


def optional_catalog_count(
    build: CatalogQueryBuild, seconds: float = OPTIONAL_COUNT_BUDGET_SECONDS,
) -> int | None:
    """Exact count on demand, with a temporary circuit breaker for slow filters."""
    if not count_is_available(build):
        return None
    query = build.count_query.order_by(None).with_entities(func.count(Ztf.alert_id))
    if not build.has_applied_filters:
        query = build.count_query.session.query(func.count()).select_from(Ztf)
    total = _budgeted_query(query, seconds, scalar=True)
    if total is None:
        if len(_slow_count_filters) >= _MAX_SEGMENT_COUNT_CACHE:
            _slow_count_filters.clear()
        key = _segment_filter_signature(build.count_query.whereclause)
        _slow_count_filters[key] = time.monotonic() + CATALOG_TOTAL_TTL_SECONDS
    return int(total) if total is not None else None


def catalog_null_metadata(
    build: CatalogQueryBuild, items: list[Any], *, complete: bool,
) -> tuple[int | None, bool]:
    """Count missing values only in a complete page; otherwise probe existence."""
    if not build.sort.nullable:
        return 0, False
    observed = sum(getattr(row, build.sort.attr) is None for row in items)
    if complete:
        return observed, observed > 0
    if observed:
        return None, True
    sample = _budgeted_query(
        build.count_query.order_by(None).filter(build.sort.column.is_(None))
        .with_entities(Ztf.alert_id).limit(1), AUTO_COUNT_BUDGET_SECONDS,
    )
    return None, bool(sample)


def _segment_predicate(sort_column, segment: str):
    if segment == SEGMENT_NULL:
        return sort_column.is_(None)
    return sort_column.is_not(None)


def _segment_order_terms(sort_column, segment: str, desc: bool):
    """ORDER BY terms for one segment.

    The null segment has no sort value to order by, so it orders by the PK
    tiebreakers only -- which is exactly the index prefix
    (sort_column, alert_id, locus_id) restricted to the NULL key range.
    """
    alert = Ztf.alert_id.desc() if desc else Ztf.alert_id.asc()
    locus = Ztf.locus_id.desc() if desc else Ztf.locus_id.asc()
    if segment == SEGMENT_NULL:
        return (alert, locus)
    column = sort_column.desc() if desc else sort_column.asc()
    return (column, alert, locus)


def _segment_cursor_filter(sort_column, cursor: KeysetCursor, segment: str, toward_smaller: bool):
    """Keyset range predicate within one segment.

    Never binds the sort value when the cursor is in the null segment: a NULL
    bound makes the whole comparison NULL and matches nothing. In that segment
    the tiebreakers alone carry the position.
    """
    pair = tuple_(Ztf.alert_id, Ztf.locus_id)
    pair_values = (cursor.alert_id, cursor.locus_id)
    if segment == SEGMENT_NULL:
        predicate = pair < pair_values if toward_smaller else pair > pair_values
        return predicate
    columns = tuple_(sort_column, Ztf.alert_id, Ztf.locus_id)
    values = (cursor.sort_value, cursor.alert_id, cursor.locus_id)
    return columns < values if toward_smaller else columns > values


def fetch_page(
    query: Query,
    page: int,
    page_size: int | None = None,
    *,
    sort_column=None,
    sort_desc: bool = True,
    segment_counts: tuple[int, int] | None = None,
) -> tuple[list[Any], bool]:
    """Legacy OFFSET page; has_next comes from an extra row.

    Legacy ``?page=N`` path. When the sort column is nullable this must walk
    the same two segments the keyset path does, or a page reached by number
    would disagree with the same position reached by cursor -- one page can
    straddle the segment boundary, taking the tail of the present segment and
    topping up from the head of the null segment.

    Counts are optional. If an offset exhausts the present segment, count only
    a prefix capped at that offset to locate the null segment correctly.
    """
    if page_size is None:
        page_size = PAGE_SIZE
    if page < 1:
        raise ValueError('page must be >= 1')
    offset = (page - 1) * page_size

    if sort_column is None:
        # Non-nullable sort: the query's own ORDER BY (sort_column, alert_id,
        # locus_id) already describes the single segment, so apply the sort to
        # the entity query rather than re-deriving terms from the column.
        items = query.limit(page_size + 1).offset(offset).all()
        return items[:page_size], len(items) > page_size

    base = query.order_by(None)

    def fetch(segment: str, seg_offset: int, limit: int) -> list[Any]:
        if limit <= 0:
            return []
        return (
            base.filter(_segment_predicate(sort_column, segment))
            .order_by(*_segment_order_terms(sort_column, segment, sort_desc))
            .limit(limit)
            .offset(seg_offset)
            .all()
        )

    rows: list[Any] = []
    has_next = False
    if segment_counts is not None:
        present_total, null_total = segment_counts
        present_rows = max(0, min(present_total - offset, page_size))
        null_rows = page_size - present_rows
        # Whatever offset the present segment could not absorb falls to null.
        # Without this an offset past the end of the present segment restarts
        # the null segment at 0 and repeats its first rows.
        null_offset = max(0, offset - present_total)
        if present_rows:
            head = fetch(SEGMENT_PRESENT, offset, present_rows + 1)
            if len(head) > present_rows:
                has_next = True
            rows += head[:present_rows]
        if null_rows:
            head = fetch(SEGMENT_NULL, null_offset, null_rows + 1)
            if len(head) > null_rows:
                has_next = True
            rows += head[:null_rows]
        if present_rows == page_size and null_total > 0 and len(rows) == page_size:
            has_next = True
    else:
        rows = fetch(SEGMENT_PRESENT, offset, page_size + 1)
        if len(rows) <= page_size:
            null_offset = 0
            if not rows and offset:
                prefix = (
                    base.filter(sort_column.is_not(None))
                    .with_entities(Ztf.alert_id).limit(offset).subquery()
                )
                skipped = query.session.execute(select(func.count()).select_from(prefix)).scalar_one()
                null_offset = offset - int(skipped)
            rows += fetch(SEGMENT_NULL, null_offset, page_size + 1 - len(rows))
        has_next = len(rows) > page_size
    return rows[:page_size], has_next


def fetch_keyset(
    query: Query,
    *,
    sort_column,
    cursor: KeysetCursor | None,
    direction: str,
    sort_desc: bool,
    page_size: int | None = None,
    segment_counts: tuple[int, int] | None = None,
) -> tuple[list[Any], bool, bool]:
    """Return (items, has_next, has_prev) for one keyset page.

    direction: first | before | after | last | nulls

    Presentation order is always the present segment then the null segment, each
    internally ordered by ``sort_desc``. ``nulls`` is the deep link straight to
    the head of the null segment (the quick-jump offered by the UI notice).

    A page is built from up to two segment seeks. ``segment_counts`` is
    ``(present, null)`` under the current filters, taken from the caller's cache
    so neither branch below ever issues a COUNT; None means "unknown", which
    only ever costs a seek that returns nothing.
    """
    if page_size is None:
        page_size = PAGE_SIZE
    base = query.order_by(None)

    if segment_counts is None:
        present_total: int | None = None
        null_total: int | None = None
    else:
        present_total, null_total = segment_counts

    def seek(
        segment: str,
        *,
        cursor_for_segment: KeysetCursor | None = None,
        toward_smaller: bool = False,
        desc: bool = False,
        limit: int,
        offset: int = 0,
    ) -> list[Any]:
        segment_query = base.filter(_segment_predicate(sort_column, segment))
        if cursor_for_segment is not None:
            segment_query = segment_query.filter(
                _segment_cursor_filter(
                    sort_column, cursor_for_segment, segment, toward_smaller
                )
            )
        segment_query = segment_query.order_by(
            *_segment_order_terms(sort_column, segment, desc)
        )
        return segment_query.limit(limit).offset(offset).all()

    def has_rows(count: int | None) -> bool:
        return True if count is None else count > 0

    present_exists = has_rows(present_total)
    null_exists = has_rows(null_total)
    reverse_after_fetch = False
    rows: list[Any] = []

    if direction == 'first':
        # Always start in the present segment so a NULL value never leads the
        # listing; top off from the null segment when the present one is short.
        rows = seek(SEGMENT_PRESENT, desc=sort_desc, limit=page_size + 1)
        if len(rows) <= page_size and null_exists:
            rows += seek(SEGMENT_NULL, desc=sort_desc,
                         limit=page_size + 1 - len(rows))
        has_next = len(rows) > page_size
        has_prev = False
    elif direction == 'nulls':
        # Head of the null segment. has_prev is True when rows exist on this
        # page and the preceding present segment holds rows, so the user can
        # step back into it.
        rows = seek(SEGMENT_NULL, desc=sort_desc, limit=page_size + 1)
        has_next = len(rows) > page_size
        if rows and present_total is None:
            # Existence has no ordering requirement. A sorted LIMIT 1 can make
            # SQLite choose a filter index and sort millions of candidate rows.
            present_exists = (
                base.filter(sort_column.is_not(None))
                .with_entities(Ztf.alert_id).first() is not None
            )
        has_prev = bool(rows) and present_exists
    elif direction == 'last':
        if null_total is None or present_total is None:
            # Read presentation order backwards: null tail, then present tail.
            # One extra row detects Prev without knowing either segment size.
            if null_exists:
                rows = seek(SEGMENT_NULL, desc=not sort_desc, limit=page_size + 1)
                if len(rows) <= page_size and present_exists:
                    rows += seek(SEGMENT_PRESENT, desc=not sort_desc,
                                 limit=page_size + 1 - len(rows))
            else:
                rows = seek(SEGMENT_PRESENT, desc=not sort_desc, limit=page_size + 1)
            reverse_after_fetch = True
            has_next = False
            has_prev = len(rows) > page_size
        else:
            # "Last" is the final page_size rows of presentation order. Build
            # it from reversed seeks rather than an OFFSET: presentation is
            # [present][null], so its reverse is [null reversed][present
            # reversed] and the final page is the *leading* rows of that
            # reversed sequence.
            #
            # Both forms matter. Offsetting inside the segment is O(offset) --
            # a Last link on a 3.18M-row table measured 3.6 s, which is exactly
            # the scan keyset pagination exists to avoid.
            null_rows = min(page_size, null_total)
            present_rows = page_size - null_rows
            rows_in_reversed_order: list[Any] = []
            has_extra = False
            if null_rows:
                head = seek(SEGMENT_NULL, desc=not sort_desc, limit=null_rows + 1)
                has_extra = has_extra or len(head) > null_rows
                rows_in_reversed_order.extend(head[:null_rows])
            if present_rows:
                head = seek(SEGMENT_PRESENT, desc=not sort_desc,
                            limit=present_rows + 1)
                has_extra = has_extra or len(head) > present_rows
                rows_in_reversed_order.extend(head[:present_rows])
            # Left in reversed order; the shared reverse_after_fetch below flips
            # it into presentation order.
            rows = rows_in_reversed_order
            reverse_after_fetch = True
            # An extra row on either seek means rows exist before this page.
            # When the null segment exactly fills the page, present rows (if any)
            # precede it in presentation order even though present_rows was 0.
            has_prev = has_extra or (present_rows == 0 and present_total > 0 and len(rows) == page_size)
            has_next = False
    elif direction in ('before', 'after'):
        if cursor is None:
            raise ValueError(f'{direction} direction requires a cursor')
        # `before` is Next (forward, toward the null segment); `after` is Prev
        # (backward, toward the present segment).
        forward = direction == 'before'
        segment = cursor.segment
        desc = sort_desc if forward else not sort_desc
        toward_smaller = sort_desc if forward else not sort_desc
        rows = seek(
            segment,
            cursor_for_segment=cursor,
            toward_smaller=toward_smaller,
            desc=desc,
            limit=page_size + 1,
        )
        if len(rows) <= page_size:
            # Top off across the segment boundary when this one is exhausted:
            # forward out of present reaches the head of null, backward out of
            # null reaches the tail of present.
            if forward and segment == SEGMENT_PRESENT and null_exists:
                rows += seek(SEGMENT_NULL, desc=desc,
                             limit=page_size + 1 - len(rows))
            elif not forward and segment == SEGMENT_NULL and present_exists:
                rows += seek(SEGMENT_PRESENT, desc=desc,
                             limit=page_size + 1 - len(rows))
        reverse_after_fetch = not forward
        # Stepping Next guarantees a page behind you; stepping Prev guarantees a
        # page ahead. The flag on the side we came from is only meaningful.
        has_next = len(rows) > page_size if forward else True
        has_prev = True if forward else len(rows) > page_size
    else:
        raise ValueError(f'unknown keyset direction: {direction}')

    items = rows[:page_size]
    if reverse_after_fetch:
        items = list(reversed(items))
    return items, has_next, has_prev


def cursor_from_row(row: Any, sort_column_name: str) -> KeysetCursor:
    return KeysetCursor(
        sort_value=getattr(row, sort_column_name),
        alert_id=row.alert_id,
        locus_id=row.locus_id,
    )


class MalformedCursor(ValueError):
    """A cursor was present but its value could not be parsed.

    Raised instead of returning None, because None means "no cursor" and would
    silently re-render page 1: a Next link whose value cannot round-trip then
    produces an unbreakable loop.
    """


def parse_keyset_cursor(args, prefix: str, numeric: bool) -> KeysetCursor | None:
    """Rebuild a cursor from URL params.

    ``<prefix>_null=1`` selects the null segment, whose position is carried by
    the PK tiebreakers alone; ``<prefix>_v`` carries a sort value. The two are
    kept distinct rather than encoded in one string so an empty text value stays
    distinguishable from "no value".

    Raises MalformedCursor when a value is present but unusable, which happens
    if the sort column ever holds a value of the wrong storage class (e.g. a
    passband token in ant_mag_corrected). Silently ignoring it would strand the
    user on page 1 with a Next link that never advances.
    """
    alert_id = args.get(f'{prefix}_alert_id')
    locus_id = args.get(f'{prefix}_locus_id')
    if not alert_id or not locus_id:
        return None
    if args.get(f'{prefix}_null') == '1':
        return KeysetCursor(sort_value=None, alert_id=alert_id, locus_id=locus_id)
    raw = args.get(f'{prefix}_v')
    if raw is None:
        return None
    value = _parse_cursor_value(raw, numeric)
    if value is None and numeric:
        raise MalformedCursor(
            f'{prefix}_v={raw!r} is not a number; this sort column expects a '
            f'numeric value'
        )
    return KeysetCursor(sort_value=value, alert_id=alert_id, locus_id=locus_id)


def parse_legacy_date_cursor(args, prefix: str) -> KeysetCursor | None:
    """Read pre-2026-10 `before_mjd`/`after_mjd` cursors from old bookmarks."""
    raw = args.get(f'{prefix}_mjd')
    alert_id = args.get(f'{prefix}_alert_id')
    locus_id = args.get(f'{prefix}_locus_id')
    if not raw or not alert_id or not locus_id:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return KeysetCursor(sort_value=value, alert_id=alert_id, locus_id=locus_id)


def parse_keyset_request(args, numeric: bool) -> tuple[str, KeysetCursor | None]:
    """Return (direction, cursor).

    ``nulls`` is checked after the cursors so a stale ``*_null`` cursor still
    wins: it is a more specific position than a bare segment jump.
    """
    if args.get('last') == '1':
        return 'last', None
    before = parse_keyset_cursor(args, 'before', numeric)
    if before is not None:
        return 'before', before
    after = parse_keyset_cursor(args, 'after', numeric)
    if after is not None:
        return 'after', after
    if args.get('nulls') == '1':
        return 'nulls', None
    return 'first', None


def _request_page(args) -> int | None:
    if hasattr(args, 'get'):
        try:
            page = args.get('page', default=None, type=int)
            if page is not None:
                return page
        except TypeError:
            pass
        raw_page = args.get('page')
    else:
        raw_page = None
    if raw_page in (None, ''):
        return None
    try:
        return int(raw_page)
    except (TypeError, ValueError):
        return None


def should_use_keyset(args) -> bool:
    """Keyset for every request except an explicit OFFSET page jump.

    `?page=N` (N>1) is the only path that still uses OFFSET: it is not
    reachable from the UI, so it only serves hand-typed and legacy bookmarked
    URLs, and no cursor can express "jump to page N".
    """
    direction, _cursor = parse_keyset_request(args, numeric=False)
    if direction != 'first':
        return True
    page = _request_page(args)
    return page is None or page <= 1


def resolve_catalog_total(
    build: CatalogQueryBuild,
    *,
    items: list[Any],
    has_next: bool,
    page: int,
    use_keyset: bool,
    keyset_direction: str,
    segment_counts: tuple[int, int] | None = None,
    has_prev: bool | None = None,
) -> tuple[int | None, bool]:
    """Infer totals, or calculate cheap metadata under a strict execution budget.

    Segment counts are display hints only. Pagination never waits for them.
    """
    is_first = (not use_keyset and page == 1) or (use_keyset and keyset_direction == 'first')
    if not has_next and is_first:
        return len(items), False
    if use_keyset and not has_next and has_prev is False:
        # A count-free Last or all-null jump can contain the entire result too.
        return len(items), False
    if not has_next and not use_keyset:
        return (page - 1) * PAGE_SIZE + len(items), False
    if keyset_direction == 'nulls' and segment_counts is not None:
        total = segment_counts[0] + segment_counts[1]
        # Match page 1: report exact total when the result fits in one page or
        # is cheap/unfiltered; defer broad unselective filters spanning > 1 page.
        if total <= PAGE_SIZE or build.has_cheap_equality or not build.has_applied_filters:
            return total, False
        return None, True
    if not build.has_applied_filters or build.has_cheap_equality:
        if not build.has_applied_filters:
            value = _catalog_total_cache['value']
            if (
                value is not None and _catalog_total_cache['signature'] == 'all'
                and time.monotonic() < float(_catalog_total_cache['expires'])
            ):
                return int(value), False
        query = build.count_query.order_by(None).with_entities(func.count(Ztf.alert_id))
        if not build.has_applied_filters:
            # Explicit FROM keeps SQLite's fast, unfiltered COUNT(*) operation
            # without Query.with_entities accidentally dropping the table.
            query = build.count_query.session.query(func.count()).select_from(Ztf)
        total = _budgeted_query(query, AUTO_COUNT_BUDGET_SECONDS, scalar=True)
        if total is not None:
            if not build.has_applied_filters:
                _catalog_total_cache.update(
                    value=int(total), expires=time.monotonic() + CATALOG_TOTAL_TTL_SECONDS,
                    signature='all',
                )
            return int(total), False
    elif keyset_direction in ('nulls', 'last', 'before', 'after') and len(items) < PAGE_SIZE:
        # A short cursor page is not the whole result. Preserve small totals by
        # sampling at most one page plus a sentinel, without a full aggregate.
        sample = _budgeted_query(
            build.count_query.order_by(None).with_entities(Ztf.alert_id).limit(PAGE_SIZE + 1),
            AUTO_COUNT_BUDGET_SECONDS,
        )
        if sample is not None and len(sample) <= PAGE_SIZE:
            return len(sample), False
    return None, True


def last_page_from_total(total: int, page_size: int = PAGE_SIZE) -> int:
    if total <= 0:
        return 1
    return max(1, math.ceil(total / page_size))
