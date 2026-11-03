"""Build the main-catalog filter/sort query from request args."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlencode

from sqlalchemy.orm import Query

from poi_broker import db
from poi_broker.models import Classification, Ztf
from poi_broker.querybuilder_translator import escape_like
from poi_broker.services.search_service import SearchService

COMPLETE_ALERT_ID_RE = re.compile(r'^(?:ztf_candidate|lsst):\d{18,}$')
ALERT_ID_PREFIX_RE = re.compile(r'^(?:ztf|lsst)\D*$')
VALID_PASSBANDS = frozenset({'g', 'R', 'i'})
VALID_PROB_CLASSES = ('cvnova', 'e', 'lpv', 'puls', 'periodic_other', 'quas', 'sn', 'yso')
FILTER_KEYS = (
    'date',
    'date_alert_mjd',
    'alert_id',
    'ztf_object_id',
    'ant_passband',
    'locus_id',
    'locus_ra',
    'locus_dec',
    'magpsf',
    'prob_class',
)
PAGINATION_KEYS = frozenset(
    {
        'page',
        'last',
        'before_v',
        'before_alert_id',
        'before_locus_id',
        'after_v',
        'after_alert_id',
        'after_locus_id',
        # Explicit "cursor sits in the null segment" flag, and the ?nulls=1
        # segment jump. Both are pagination state, never user filters, so they
        # are stripped from generated filter links and bookmarks.
        'before_null',
        'after_null',
        'nulls',
        # Legacy date-only cursor params, still emitted by pre-2026-10
        # bookmarks. Parsed for backward compatibility (see parse_keyset_cursor)
        # and stripped from generated links/bookmarks like the rest.
        'before_mjd',
        'after_mjd',
    }
)
# Sortable columns exposed via sort__<key> URL params ('asc'/'desc').
# Exactly one sort key is honoured per request (see resolve_sort);
# filter_bookmarks derives its ALLOWED_KEYS from FILTER_KEYS | SORT_KEYS —
# keep them in sync.
SORT_KEYS = (
    'date',
    'alert_id',
    'ztf_object_id',
    'locus_id',
    'locus_ra',
    'locus_dec',
    'ant_mag_corrected',
)

# Sort key -> model column. Every key in SORT_KEYS must appear here.
SORT_COLUMNS = {
    'date': Ztf.date_alert_mjd,
    'alert_id': Ztf.alert_id,
    'ztf_object_id': Ztf.ztf_object_id,
    'locus_id': Ztf.locus_id,
    'locus_ra': Ztf.locus_ra,
    'locus_dec': Ztf.locus_dec,
    'ant_mag_corrected': Ztf.ant_mag_corrected,
}

# Nullability is NOT hand-kept: SortSpec.nullable reads it off the model
# column. The primary-key members are declared primary_key=True, so they come
# back nullable=False and never need a second sort segment.

DEFAULT_SORT_KEY = 'date'

# Human-readable names for the "N alerts have no <X>" notice above the list.
SORT_LABELS = {
    'date': 'date',
    'alert_id': 'alert ID',
    'ztf_object_id': 'object ID',
    'locus_id': 'locus ID',
    'locus_ra': 'RA',
    'locus_dec': 'Dec',
    'ant_mag_corrected': 'magnitude',
}

# Base list projection, in display order. project_catalog_columns appends any
# sortable column missing from here.
_BASE_LIST_ATTRS = (
    'date_alert_mjd',
    'alert_id',
    'ztf_object_id',
    'ant_passband',
    'locus_id',
    'locus_ra',
    'locus_dec',
    'ant_mag_corrected',
)

# Sort key -> projected-row attribute name. Only 'date' differs from its key.
SORT_COLUMN_ATTRS = {
    'date': 'date_alert_mjd',
    'alert_id': 'alert_id',
    'ztf_object_id': 'ztf_object_id',
    'locus_id': 'locus_id',
    'locus_ra': 'locus_ra',
    'locus_dec': 'locus_dec',
    'ant_mag_corrected': 'ant_mag_corrected',
}

search_service = SearchService()


@dataclass(frozen=True)
class SortSpec:
    """The single sort column in force for a request."""

    key: str
    column: Any
    attr: str
    desc: bool
    nullable: bool
    numeric: bool

    def order_terms(self):
        """ORDER BY terms making the sort column PRIMARY and the order total.

        (column, alert_id, locus_id) is a total order because (alert_id,
        locus_id) is unique across the table (verified: 0 duplicate pairs in
        3,178,668 rows). Appending the tiebreakers as a partial sort of the
        column's own index keeps the scan streaming; putting the sort column
        first is what makes the ORDER BY match the index prefix instead of
        sorting the whole table.
        """
        direction = self.column.desc() if self.desc else self.column.asc()
        tiebreak = Ztf.alert_id.desc() if self.desc else Ztf.alert_id.asc()
        locus = Ztf.locus_id.desc() if self.desc else Ztf.locus_id.asc()
        return (direction, tiebreak, locus)


def _column_nullable(column: Any) -> bool:
    """Whether the model column admits NULL.

    Read off the declarative model rather than a hand-kept frozenset, so adding
    a key to SORT_KEYS cannot leave the pagination layer disagreeing with the
    schema. Primary-key members are declared primary_key=True and therefore
    report nullable=False.
    """
    return bool(column.property.columns[0].nullable)


def resolve_sort(args: Mapping) -> SortSpec:
    """Pick the one active sort. First key in SORT_KEYS order wins.

    Multi-sort URLs are accepted but collapsed to a single key: a second and
    third sort key can only break ties *within* equal values of the first, so
    they never change the row order the user sees — they just made the ORDER BY
    longer and the keyset cursor wider. 'asc' selects ascending; every other
    value (including 'desc' and junk) is descending, so an unrecognised value
    can never leave the OFFSET path without a total order.
    """
    chosen = DEFAULT_SORT_KEY
    desc = True
    for key in SORT_KEYS:
        raw = args.get(f'sort__{key}')
        if raw:
            chosen = key
            desc = raw.strip().lower() != 'asc'
            break
    return SortSpec(
        key=chosen,
        column=SORT_COLUMNS[chosen],
        attr=SORT_COLUMN_ATTRS[chosen],
        desc=desc,
        nullable=_column_nullable(SORT_COLUMNS[chosen]),
        numeric=SORT_COLUMNS[chosen].type.python_type is float,
    )


@dataclass(frozen=True)
class CatalogQueryBuild:
    list_query: Query
    count_query: Query
    filter_warning: str
    has_applied_filters: bool
    has_cheap_equality: bool
    sort: SortSpec


def _base_query(*, include_classification: bool) -> Query:
    query = db.session.query(Ztf)
    if include_classification:
        query = query.outerjoin(Classification, Ztf.alert_id == Classification.alert_id)
    return query


def _apply_catalog_filters(query: Query, args: Mapping) -> tuple[Query, str, bool, bool, bool]:
    """Apply main-page filters. Returns query, warning, has_filters, cheap_equality, prob_class."""
    filter_warning = ''
    has_applied_filters = False
    has_cheap_equality = False
    has_prob_class_filter = False

    if date_text := args.get('date'):
        parsed_date = search_service.parse_mjd_input(date_text)
        if parsed_date.values:
            query = search_service.apply_mjd_filter(query, Ztf.date_alert_mjd, parsed_date)
            has_applied_filters = True
        else:
            filter_warning += (
                'Date filter cannot be applied - Enter a valid ISO-date or 8-digit integer date '
                'of the form yyyymmdd, e.g. "20201207", or a range, e.g., "20201207 20201209".'
            )

    if mjd_text := args.get('date_alert_mjd'):
        parsed = search_service.parse_float_input(mjd_text)
        if parsed and parsed.values:
            query = search_service.apply_float_filter(query, Ztf.date_alert_mjd, parsed)
            has_applied_filters = True
        else:
            filter_warning += (
                'MJD filter cannot be applied - Enter a valid Modified Julian Date as a number, '
                'e.g. "59190.12", a range, e.g. "59190 59191", or a bound with > or <, e.g. ">59190".'
            )

    if args.get('alert_id'):
        alert_id = args.get('alert_id', '').strip()
        if COMPLETE_ALERT_ID_RE.match(alert_id):
            query = query.filter(Ztf.alert_id == alert_id)
            has_applied_filters = True
            has_cheap_equality = True
        elif ALERT_ID_PREFIX_RE.match(alert_id):
            query = query.filter(
                Ztf.alert_id.like(escape_like(alert_id) + '%', escape='\\')
            )
            has_applied_filters = True
        elif alert_id != '':
            filter_warning += (
                'Alert ID cannot be filter by partial IDs - Enter a full alert ID, e.g. '
                '"ztf_candidate:335155568501", or "lsst:170094456539709554", or just the catalog '
                'prefix, e.g. "ztf" or "lsst".'
            )

    if object_id_text := args.get('ztf_object_id'):
        query = query.filter(Ztf.ztf_object_id == object_id_text.strip())
        has_applied_filters = True
        has_cheap_equality = True

    if passband_text := args.get('ant_passband'):
        if passband_text in VALID_PASSBANDS:
            query = query.filter(Ztf.ant_passband == passband_text)
            has_applied_filters = True
        else:
            filter_warning += (
                'Passband filter cannot be applied - Enter a valid passband, e.g., "g", "R", or "i".'
            )

    if locus_id_text := args.get('locus_id'):
        query = query.filter(Ztf.locus_id == locus_id_text.strip())
        has_applied_filters = True
        has_cheap_equality = True

    if ra_text := args.get('locus_ra'):
        parsed = search_service.parse_float_input(ra_text, allowed_range=(0.0, 360.0))
        if parsed and parsed.values:
            query = search_service.apply_float_filter(query, Ztf.locus_ra, parsed, decimals=5)
            has_applied_filters = True
        else:
            filter_warning += (
                'Ra filter cannot be applied - Enter a valid number within the range 0° to 360°, '
                'e.g., "118.61421", or range, e.g., "80 90".'
            )

    if dec_text := args.get('locus_dec'):
        parsed = search_service.parse_float_input(dec_text, allowed_range=(-90.0, 90.0))
        if parsed and parsed.values:
            query = search_service.apply_float_filter(query, Ztf.locus_dec, parsed, decimals=5)
            has_applied_filters = True
        else:
            filter_warning += (
                'Dec filter cannot be applied - Enter a valid number within the range -90° to +90°, '
                'e.g., "-20.12345", or range, e.g., "14.5 29".'
            )

    if mag_text := args.get('magpsf'):
        parsed = search_service.parse_float_input(mag_text)
        if parsed and parsed.values:
            query = search_service.apply_float_filter(query, Ztf.ant_mag_corrected, parsed, decimals=3)
            has_applied_filters = True
        else:
            filter_warning += (
                'ant_mag_corrected filter cannot be applied - Enter a valid number, e.g., "18.84", '
                'or range, e.g., "18.8 19.4".'
            )

    if args.get('prob_class'):
        prob_class_value = args.get('prob_class', '').strip()
        if prob_class_value and prob_class_value in VALID_PROB_CLASSES:
            query = query.filter(Classification.prob_class == prob_class_value)
            has_applied_filters = True
            has_prob_class_filter = True
        elif prob_class_value:
            filter_warning += (
                'Classification filter cannot be applied - Enter a valid classification label, '
                f'e.g. "sn". Valid options are: {", ".join(VALID_PROB_CLASSES)}.'
            )

    return query, filter_warning, has_applied_filters, has_cheap_equality, has_prob_class_filter


def _apply_catalog_sort(query: Query, sort: SortSpec) -> Query:
    """Order by the sort column FIRST, with PK tiebreakers for a total order.

    The previous implementation chained `.order_by()` calls, and SQLAlchemy
    *appends* those, so `date_alert_mjd DESC` stayed the leading term for
    every secondary sort. Clicking "RA ascending" therefore ordered RA only
    within a single MJD: page 1 of `?sort__locus_ra=asc` began at ra=250.66
    and the lowest reachable RA (277.97 at the old row-100,000 boundary) was
    nowhere near the catalog minimum of 0.000253. The column headers were
    close to decorative.

    No NULL predicate belongs here. Rows whose sort value is NULL are listed,
    in a second segment after this one (see services.catalog_list), so the
    count query needs no matching restriction either and "Rows: N" is the true
    total. Note the ORDER BY must stay a bare index prefix: adding a leading
    `col IS NULL` term is what previously forced a full sort (3.82 s/page).
    """
    return query.order_by(*sort.order_terms())


def project_catalog_columns(query: Query) -> Query:
    """Project list columns including classification label.

    Every sortable column must be projected: `cursor_from_row` reads the sort
    column off the returned row to build the next page's cursor, so a sortable
    column missing here would raise AttributeError at pagination time rather
    than at import or query-build time. Adding a key to SORT_KEYS therefore
    appends its column to the projection automatically.
    """
    attrs = list(_BASE_LIST_ATTRS)
    for attr in dict.fromkeys(SORT_COLUMN_ATTRS.values()):
        if attr not in attrs:
            attrs.append(attr)
    return query.with_entities(
        *(getattr(Ztf, attr) for attr in attrs),
        Classification.prob_class.label('prob_class'),
    )


def build_catalog_query(args: Mapping, *, include_sort: bool = True) -> CatalogQueryBuild:
    """Build list and count queries from the same filter args."""
    list_query, warning, has_filters, cheap_equality, has_prob_class = _apply_catalog_filters(
        _base_query(include_classification=True),
        args,
    )
    count_query, _, _, _, _ = _apply_catalog_filters(
        _base_query(include_classification=has_prob_class),
        args,
    )

    sort = resolve_sort(args)
    if include_sort:
        list_query = _apply_catalog_sort(list_query, sort)
    # count_query is deliberately left free of any sort restriction. It once
    # carried the same orderability predicate as list_query so "Rows: N" would
    # not overstate a listing that hid 31.8% of the catalog. NULL-valued rows
    # are now listed in a trailing segment, so list and count agree at the full
    # 3,178,668 without either query being restricted.

    return CatalogQueryBuild(
        list_query=list_query,
        count_query=count_query,
        filter_warning=warning,
        has_applied_filters=has_filters,
        has_cheap_equality=cheap_equality,
        sort=sort,
    )


def _encode_args(args: Mapping, allowed_keys: tuple[str, ...] | None, excluded: frozenset[str]) -> str:
    pairs: list[tuple[str, str]] = []
    getlist = getattr(args, 'getlist', None)
    keys = allowed_keys if allowed_keys is not None else tuple(args.keys())
    seen: set[str] = set()
    for key in keys:
        if key in excluded or key in seen:
            continue
        seen.add(key)
        values = getlist(key) if getlist else [args.get(key)]
        for value in values:
            if value is None or value == '':
                continue
            pairs.append((key, str(value)))
    return urlencode(pairs)


def catalog_query_string(args: Mapping) -> str:
    """Filter + sort query string with pagination/cursor keys stripped."""
    return _encode_args(args, None, PAGINATION_KEYS)


def catalog_filter_query_string(args: Mapping) -> str:
    """Filter-only query string for /api/catalog-count."""
    return _encode_args(args, FILTER_KEYS, PAGINATION_KEYS)


def catalog_href(query_string: str, extra: Mapping[str, str] | None = None) -> str:
    extra_qs = urlencode(list((extra or {}).items()))
    if query_string and extra_qs:
        return f'/?{query_string}&{extra_qs}'
    if extra_qs:
        return f'/?{extra_qs}'
    if query_string:
        return f'/?{query_string}'
    return '/'
