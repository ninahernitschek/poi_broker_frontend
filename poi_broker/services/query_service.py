"""Query building and execution service for visual query builder."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from sqlalchemy.dialects import sqlite as _sqlite_dialect
from sqlalchemy.engine import Row
from sqlalchemy.orm import Query

from .. import db
from ..models import Classification, Ztf
from ..querybuilder_translator import Filter, TableNotFoundError

logger = logging.getLogger(__name__)


# Full set of Ztf (object) columns. Used for filtering, watchlists and the bulk
# export, so it lives once here as the authoritative list.
ZTF_COLUMNS = [col.name for col in Ztf.__table__.columns] # type: ignore

# Classification columns surfaced in the bulk export CSV header.
CLASSIFICATION_COLUMNS = ['classification_alert_id', 'classification_prob_class']

# Core column projection for bulk CSV export. Selecting columns (not entities)
# skips ORM identity-map hydration. Classification is limited to the two CSV
# fields instead of the full mapped table. Ztf still exports every mapped
# column so the CSV schema is unchanged.
EXPORT_SELECT_COLS = [getattr(Ztf, name) for name in ZTF_COLUMNS] + [
    Classification.alert_id.label('classification_alert_id'),
    Classification.prob_class.label('classification_prob_class'),
]

EXPORT_YIELD_PER = 1000

# Shared with Filter so table names and join detection cannot drift apart.
QUERY_MODELS = {'featuretable': Ztf, 'classification': Classification}
CLASSIFICATION_TABLE = next(
    name for name, model in QUERY_MODELS.items() if model is Classification
)


@lru_cache(maxsize=1)
def get_export_columns() -> list[str]:
    """
    Return the column names used when writing an export CSV row.

    The result is cached and shared across callers (build_export_row runs once
    per exported row, up to MAX_EXPORT_ROWS), so it must not be mutated.

    Returns:
        list[str]: Ztf object columns followed by classification columns.
    """
    return ZTF_COLUMNS + CLASSIFICATION_COLUMNS


def build_export_row(row: Row) -> dict[str, Any]:
    """
    Build a single CSV row dict from a Core column Row.

    Args:
        row: A SQLAlchemy Row from :func:`build_export_query_from_rules`.

    Returns:
        dict[str, Any]: Field names from get_export_columns() to values.
    """
    mapping = row._mapping
    return {name: mapping[name] for name in get_export_columns()}


def _rules_need_classification(rules_payload: dict) -> bool:
    """Return True if any rule field targets the classification table."""
    for cond in rules_payload.get('rules', []):
        if not isinstance(cond, dict):
            raise ValueError('Invalid querybuilder rules payload')
        if 'condition' in cond:
            if _rules_need_classification(cond):
                return True
            continue
        field_name = cond.get('field', '')
        if isinstance(field_name, str) and field_name.startswith(f'{CLASSIFICATION_TABLE}.'):
            return True
    return False


def _validate_rules_payload(rules_payload: dict, *, require_rules: bool = True) -> None:
    if not isinstance(rules_payload, dict) or 'rules' not in rules_payload:
        raise ValueError('Invalid querybuilder rules payload')

    rules = rules_payload.get('rules')
    if not isinstance(rules, list):
        raise ValueError('Invalid querybuilder rules payload')
    if require_rules and len(rules) == 0:
        raise ValueError('At least one filter rule is required')

    for cond in rules:
        if not isinstance(cond, dict):
            raise ValueError('Invalid querybuilder rules payload')
        if 'condition' in cond:
            _validate_rules_payload(cond, require_rules=False)


def build_query_from_rules(
    rules_payload: dict,
    *,
    include_classification: bool | None = None,
) -> tuple[Query, dict]:
    """
    Build an ORM query from querybuilder rules.

    Filter values stay as bind parameters. Do not compile this query with
    ``literal_binds=True`` for execution; persist ``rules_payload`` and rebuild.

    Args:
        rules_payload: Dict with 'rules' key containing filter rules.
        include_classification: When None, outerjoin Classification only if a
            rule field targets ``classification.*``. Pass True to always join
            (export SELECT needs classification columns). False cannot skip a
            join that classification rules require.

    Returns:
        tuple: (filtered_query, rules_payload)

    Raises:
        ValueError: If rules payload is invalid or builds no WHERE clause.
    """
    _validate_rules_payload(rules_payload)

    needs_classification = _rules_need_classification(rules_payload)
    if include_classification is None:
        include_classification = needs_classification
    else:
        # Never skip the join when rules filter classification.* (avoids
        # Filter adding Classification with no ON clause).
        include_classification = bool(include_classification) or needs_classification

    if include_classification:
        base_query = db.session.query(Ztf, Classification).outerjoin(
            Classification,
            Ztf.alert_id == Classification.alert_id,
        )
    else:
        base_query = db.session.query(Ztf)

    myfilter = Filter(QUERY_MODELS, base_query)
    try:
        filtered_query = myfilter.querybuilder(rules_payload)
    except TableNotFoundError as exc:
        raise ValueError(f'Unknown table: {exc}') from exc
    except NotImplementedError as exc:
        raise ValueError(str(exc)) from exc

    if filtered_query.whereclause is None:
        raise ValueError('No filter conditions could be built from the rules')

    return filtered_query, rules_payload


def build_export_query_from_rules(
    rules_payload: dict,
    *,
    max_alert_mjd: float | None = None,
) -> tuple[Query, dict]:
    """
    Build a column-only export query from querybuilder rules.

    Same filters as :func:`build_query_from_rules`, but the SELECT list is Core
    columns so iteration does not construct Ztf/Classification instances or
    populate the identity map. ``yield_per`` batches row construction; the
    sqlite3 driver still buffers DBAPI results, so this is not a true stream.

    Args:
        rules_payload: Dict with 'rules' key containing filter rules.
        max_alert_mjd: Snapshot cutoff. Only rows whose ``date_alert_mjd``
            (alert time) is strictly below this MJD are exported. The cutoff is
            computed once at submit time and shared with the pre-count, so the
            CSV is reproducible regardless of how long the task waits in the
            queue: rows ingested later with an older alert time can still slip
            in (bounded by ingest lag), but the export no longer drifts with
            queue latency. Row *updates* after the cutoff still appear with
            their new values.

    Returns:
        tuple: (export_query, rules_payload)
    """
    filtered_query, rules_payload = build_query_from_rules(
        rules_payload,
        include_classification=True,
    )
    if max_alert_mjd is not None:
        filtered_query = filtered_query.filter(Ztf.date_alert_mjd < max_alert_mjd)
    export_query = (
        filtered_query
        .enable_eagerloads(False)
        .with_entities(*EXPORT_SELECT_COLS)
        .execution_options(yield_per=EXPORT_YIELD_PER, stream_results=True)
    )
    return export_query, rules_payload


def get_preview_sql(rules_payload: dict) -> str:
    """
    Compile a display-only WHERE preview from rules.

    Inlines bind values for the UI / ``Watchlist.sql_where`` column. Never
    execute the returned string; re-run :func:`build_query_from_rules` instead.

    Args:
        rules_payload: Dict with 'rules' key containing filter rules.

    Returns:
        str: SQL WHERE clause as a human-readable preview.
    """
    filtered_query, _ = build_query_from_rules(rules_payload)
    compiled = filtered_query.whereclause.compile(
        dialect=_sqlite_dialect.dialect(),
        compile_kwargs={'literal_binds': True},
    )
    return str(compiled)


def build_count_query_from_rules(
    rules_payload: dict,
    *,
    max_alert_mjd: float | None = None,
) -> Query:
    """
    Build the COUNT query used by :func:`get_query_match_count`.

    Exposed for compile-SQL tests. Classification is joined only when needed.
    ``max_alert_mjd`` applies the same snapshot cutoff as
    :func:`build_export_query_from_rules`.
    """
    filtered_query, _ = build_query_from_rules(rules_payload)
    if max_alert_mjd is not None:
        filtered_query = filtered_query.filter(Ztf.date_alert_mjd < max_alert_mjd)
    return filtered_query.order_by(None).with_entities(db.func.count())


def get_query_match_count(
    rules_payload: dict,
    *,
    max_alert_mjd: float | None = None,
) -> int:
    """
    Get number of matching records for a query.

    Skips the Classification outerjoin when rules only touch featuretable.*
    so COUNT(*) is ``SELECT count(*) FROM featuretable WHERE …``.

    Args:
        rules_payload: Dict with 'rules' key containing filter rules.
        max_alert_mjd: When given, only rows with ``date_alert_mjd`` strictly
            below this MJD are counted, matching the export snapshot cutoff.

    Returns:
        int: Number of matching records.
    """
    match_count = build_count_query_from_rules(
        rules_payload, max_alert_mjd=max_alert_mjd
    ).scalar()
    return int(match_count or 0)


def execute_watchlist(
    rules_payload: dict,
    start_mjd: float,
    end_mjd: float,
    limit: int,
) -> list[str]:
    """
    Return alert IDs matching rules in ``[start_mjd, end_mjd)``.

    Rebuilds the ORM filter from ``rules_payload``. Never executes stored SQL.

    Args:
        rules_payload: Querybuilder rules dict persisted as ``rules_json``.
        start_mjd: Inclusive MJD lower bound.
        end_mjd: Exclusive MJD upper bound.
        limit: Maximum number of alert IDs to return.

    Returns:
        list[str]: Matching ``alert_id`` values, newest first.
    """
    query, _ = build_query_from_rules(rules_payload)
    rows = (
        query.with_entities(Ztf.alert_id)
        .filter(Ztf.date_alert_mjd >= start_mjd, Ztf.date_alert_mjd < end_mjd)
        .order_by(Ztf.date_alert_mjd.desc())
        .limit(limit)
        .all()
    )
    return [alert_id for (alert_id,) in rows if alert_id]
