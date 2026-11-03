"""User-scoped custom observatory API."""

from __future__ import annotations

from datetime import timezone
from functools import lru_cache
import logging

from astropy.coordinates import EarthLocation
from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from .. import db
from ..models import UserObservatory
from ..observing_tool import _get_timezone_finder
from ..user_settings import (
    _normalize_last_selected_observatory,
    get_saved_last_selected_observatory,
    save_last_selected_observatory,
)

logger = logging.getLogger(__name__)

user_observatories_bp = Blueprint('user_observatories', __name__, url_prefix='/api')


@lru_cache(maxsize=1)
def _builtin_site_names() -> frozenset[str]:
    return frozenset(EarthLocation.get_site_names())


def _validate_payload(raw: object) -> tuple[dict[str, float | str] | None, str | None]:
    if not isinstance(raw, dict):
        return None, 'Invalid or missing JSON'

    name = str(raw.get('name') or '').strip()
    if not name:
        return None, 'name is required'
    if len(name) > UserObservatory.MAX_NAME_LENGTH:
        return None, f'name must be {UserObservatory.MAX_NAME_LENGTH} characters or fewer'

    latitude_raw = raw.get('latitude')
    longitude_raw = raw.get('longitude')
    # bool is a subclass of int, so float(True) == 1.0; reject it explicitly.
    if isinstance(latitude_raw, bool) or isinstance(longitude_raw, bool):
        return None, 'latitude and longitude must be numeric'
    try:
        latitude = float(latitude_raw)
        longitude = float(longitude_raw)
    except (TypeError, ValueError):
        return None, 'latitude and longitude must be numeric'

    if not (-90.0 <= latitude <= 90.0):
        return None, 'latitude must be between -90 and 90'
    if not (-180.0 <= longitude <= 180.0):
        return None, 'longitude must be between -180 and 180'

    timezone_name = _get_timezone_finder().timezone_at(lng=longitude, lat=latitude)
    if not timezone_name:
        return None, 'Unable to resolve timezone for the provided coordinates'

    return {
        'name': name,
        'latitude': latitude,
        'longitude': longitude,
        'timezone_name': timezone_name,
    }, None


def _row_to_api_dict(row: UserObservatory) -> dict:
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    else:
        created = created.astimezone(timezone.utc)

    return {
        'id': row.id,
        'name': row.name,
        'latitude': row.latitude,
        'longitude': row.longitude,
        'timezone_name': row.timezone_name,
        'created_at': created.isoformat(),
    }


@user_observatories_bp.route('/user-observatories', methods=['GET'])
@login_required
def list_user_observatories():
    rows = (
        UserObservatory.query.filter_by(user_id=current_user.id)
        .order_by(UserObservatory.name.asc())
        .all()
    )
    return jsonify({'userObservatories': [_row_to_api_dict(row) for row in rows]})


@user_observatories_bp.route('/user-observatories', methods=['POST'])
@login_required
def create_user_observatory():
    payload, err = _validate_payload(request.get_json(silent=True))
    if err:
        return jsonify({'error': err}), 400
    if payload is None:
        return jsonify({'error': 'invalid payload'}), 400

    row = UserObservatory(user_id=current_user.id, **payload)
    db.session.add(row)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # Be tolerant to duplicate create requests: if the record exists for this user,
        # return it as a successful idempotent result instead of surfacing a hard error.
        existing = UserObservatory.query.filter_by(
            user_id=current_user.id,
            name=payload['name'],
        ).first()
        if existing is not None:
            return jsonify({'status': 'ok', 'already_exists': True, **_row_to_api_dict(existing)}), 200
        return jsonify({'error': 'An observatory with this name already exists.'}), 409
    except Exception as exc:
        db.session.rollback()
        logger.error('Database error during custom observatory create: %s', exc, exc_info=True)
        return jsonify({'error': 'Unable to create custom observatory.'}), 500

    return jsonify({'status': 'ok', **_row_to_api_dict(row)}), 201


@user_observatories_bp.route('/user-observatories/<int:observatory_id>', methods=['DELETE'])
@login_required
def delete_user_observatory(observatory_id: int):
    selected = get_saved_last_selected_observatory(current_user.id)
    selected_custom_id = selected.get('id') if isinstance(selected, dict) and selected.get('source') == 'custom' else None
    was_selected = selected_custom_id == observatory_id

    # Use a single SQL DELETE to avoid stale-instance rowcount warnings when the
    # row disappears between fetch and commit (e.g. duplicate clicks/races).
    deleted_count = UserObservatory.query.filter_by(
        id=observatory_id,
        user_id=current_user.id,
    ).delete(synchronize_session=False)
    if deleted_count == 0:
        # Idempotent delete: deleting an already-removed row should be treated as success.
        return jsonify({'status': 'ok', 'already_deleted': True}), 200

    fallback_site_name = None
    if was_selected:
        builtin_names = list(EarthLocation.get_site_names())
        fallback_site_name = builtin_names[0] if builtin_names else None
        if fallback_site_name:
            save_last_selected_observatory(
                current_user.id,
                {'source': 'builtin', 'name': fallback_site_name},
            )
        else:
            save_last_selected_observatory(current_user.id, None)

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('Database error during custom observatory delete: %s', exc, exc_info=True)
        return jsonify({'error': 'Unable to delete custom observatory.'}), 500

    payload: dict[str, object] = {'status': 'ok'}
    if was_selected:
        payload['warning'] = 'Selected custom observatory was deleted. Fallback selection has been applied.'
        if fallback_site_name:
            payload['fallback'] = {'source': 'builtin', 'name': fallback_site_name}

    return jsonify(payload), 200


@user_observatories_bp.route('/last-observatory', methods=['POST'])
@login_required
def save_last_observatory():
    """Persist last-selected observatory from an authenticated, CSRF-checked POST."""
    normalized = _normalize_last_selected_observatory(request.get_json(silent=True) or {})
    if normalized is None:
        return jsonify({'error': 'Invalid observatory selection'}), 400

    if normalized['source'] == 'custom':
        owned = UserObservatory.query.filter_by(
            id=normalized['id'],
            user_id=current_user.id,
        ).first()
        if owned is None:
            return jsonify({'error': 'Unknown custom observatory'}), 400
    elif normalized['source'] == 'builtin':
        if normalized['name'] not in _builtin_site_names():
            return jsonify({'error': 'Unknown builtin observatory'}), 400

    try:
        save_last_selected_observatory(current_user.id, normalized)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to save last-selected observatory for user_id=%s: %s', current_user.id, exc, exc_info=True)
        return jsonify({'error': 'Unable to save observatory selection'}), 500

    return jsonify({'status': 'ok'})
