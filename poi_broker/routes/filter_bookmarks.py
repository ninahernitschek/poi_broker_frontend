"""Saved main-table filter bookmarks API."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from sqlalchemy.exc import IntegrityError
from .. import db
from ..models import FilterBookmark
from ..services.catalog_query import FILTER_KEYS, SORT_KEYS

logger = logging.getLogger(__name__)

filter_bookmarks_bp = Blueprint('filter_bookmarks', __name__, url_prefix='/api')

MAX_BOOKMARK_JSON_BYTES = 16384
MAX_PARAM_VALUE_LEN = 512

# Filter + sort URL params a bookmark may store, derived from the
# authoritative key sets in catalog_query so the two cannot drift.
# Pagination keys (catalog_query.PAGINATION_KEYS) stay excluded — the
# bookmark-save JS strips them before posting (auditSBA F6).
ALLOWED_KEYS = frozenset(FILTER_KEYS) | {f'sort__{key}' for key in SORT_KEYS}


def _sanitize_params(raw: object) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(raw, dict):
        return None, 'params must be a JSON object'

    out: dict[str, str] = {}
    for key, value in raw.items():
        if key not in ALLOWED_KEYS:
            return None, f'unknown parameter: {key}'
        if value is None:
            continue
        s = str(value).strip()
        if not s:
            continue
        if len(s) > MAX_PARAM_VALUE_LEN:
            return None, f'value for {key} is too long'
        out[key] = s

    serialized = json.dumps(out, sort_keys=True, separators=(',', ':'))
    if len(serialized.encode('utf-8')) > MAX_BOOKMARK_JSON_BYTES:
        return None, 'filter bookmarkpayload too large'

    return out, None


def _params_to_query_path(params: dict[str, str]) -> str:
    if not params:
        return '/'
    return '/?' + urlencode(sorted(params.items()))


def _row_to_api_dict(row: FilterBookmark) -> dict:
    try:
        params = json.loads(row.query_json)
        if not isinstance(params, dict):
            params = {}
    except json.JSONDecodeError:
        logger.warning('Invalid query_json for filter_bookmark id=%s', row.id)
        params = {}

    # Legacy bookmarks may store sort__date_alert_mjd; catalog uses sort__date.
    if 'sort__date_alert_mjd' in params:
        params = dict(params)
        legacy_sort = params.pop('sort__date_alert_mjd')
        params.setdefault('sort__date', legacy_sort)

    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    else:
        created = created.astimezone(timezone.utc)

    return {
        'id': row.id,
        'name': row.name,
        'params': params,
        'path': _params_to_query_path({k: str(v) for k, v in params.items()}),
        'created_at': created.isoformat(),
    }


@filter_bookmarks_bp.route('/filter-bookmarks', methods=['GET'])
@login_required
def list_filter_bookmarks():
    rows = (
        FilterBookmark.query.filter_by(user_id=current_user.id)
        .order_by(FilterBookmark.created_at.desc())
        .all()
    )
    return jsonify({'filterBookmarks': [_row_to_api_dict(r) for r in rows]})


@filter_bookmarks_bp.route('/filter-bookmarks', methods=['POST'])
@login_required
def create_filter_bookmark():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid or missing JSON'}), 400

    raw_name = data.get('name')
    if not isinstance(raw_name, str):
        return jsonify({'error': 'name is required'}), 400
    name = raw_name.strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    if len(name) > 200:
        return jsonify({'error': 'name must be 200 characters or fewer'}), 400

    raw_params = data.get('params')
    if raw_params is None:
        raw_params = {}
    params, err = _sanitize_params(raw_params)
    if err:
        return jsonify({'error': err}), 400
    if params is None:
        return jsonify({'error': 'invalid params'}), 400

    row = FilterBookmark(
        user_id=current_user.id,
        name=name,
        query_json=json.dumps(params, sort_keys=True, separators=(',', ':')),
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'A bookmark with this name already exists.'}), 409
    except Exception as e:
        db.session.rollback()
        logger.error(f'Database error during commit: {str(e)}', exc_info=True)
        return jsonify({'error': 'Unable to create filter bookmark.'}), 500
    
    payload = _row_to_api_dict(row)
    return jsonify({'status': 'ok', **payload}), 201


@filter_bookmarks_bp.route('/filter-bookmarks/<int:bookmark_id>', methods=['DELETE'])
@login_required
def delete_filter_bookmark(bookmark_id: int):
    row = FilterBookmark.query.filter_by(id=bookmark_id, user_id=current_user.id).first()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    db.session.delete(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'Cannot delete bookmark because it is in use.'}), 409
    except Exception as e:
        db.session.rollback()
        logger.error(f'Database error during commit: {str(e)}', exc_info=True)
        return jsonify({'error': 'Unable to delete filter bookmark.'}), 500
    
    return jsonify({'status': 'ok'}), 200
