import json
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
import logging
from sqlalchemy.exc import IntegrityError
from . import db
from .constants.features import FEATURE_COLUMNS, default_feature_plot_columns
from .models import UserSettings, UserObservatory

logger = logging.getLogger(__name__)
user_settings_bp = Blueprint('user_settings', __name__)

_MAX_DEFAULT_FEATURE_PLOT_COLUMNS = 10


def _normalize_feature_columns(columns):
    if not isinstance(columns, list):
        return default_feature_plot_columns()

    normalized = []
    for col in columns:
        if isinstance(col, str) and col in FEATURE_COLUMNS and col not in normalized:
            normalized.append(col)
        if len(normalized) >= _MAX_DEFAULT_FEATURE_PLOT_COLUMNS:
            break

    return normalized if normalized else default_feature_plot_columns()


def _load_user_settings(user_id):
    return UserSettings.query.filter_by(user_id=user_id).first()


def get_user_settings(user_id) -> UserSettings | None:
    """Load the user's settings row (or None if not present).

    Exposed so callers can load the row once and reuse it across multiple
    settings lookups (e.g. the main page), avoiding redundant DB queries.
    """
    return _load_user_settings(user_id)


def _normalize_last_selected_observatory(raw):
    if not isinstance(raw, dict):
        return None

    source = raw.get('source')
    if source == 'builtin':
        name = raw.get('name')
        if isinstance(name, str) and name.strip():
            return {'source': 'builtin', 'name': name.strip()}
        return None

    if source == 'custom':
        observatory_id = raw.get('id')
        if isinstance(observatory_id, int) and not isinstance(observatory_id, bool):
            return {'source': 'custom', 'id': observatory_id}
        return None

    return None


def get_saved_feature_plot_columns(user_id, settings: UserSettings | None = None):
    if settings is None:
        settings = _load_user_settings(user_id)
    if not settings or not settings.default_feature_plot_columns:
        return default_feature_plot_columns()

    try:
        columns = json.loads(settings.default_feature_plot_columns)
    except (TypeError, ValueError):
        return default_feature_plot_columns()

    return _normalize_feature_columns(columns)


def get_saved_last_selected_observatory(user_id, settings: UserSettings | None = None):
    if settings is None:
        settings = _load_user_settings(user_id)
    if not settings or not settings.last_selected_observatory_json:
        return None

    try:
        payload = json.loads(settings.last_selected_observatory_json)
    except (TypeError, ValueError):
        return None

    return _normalize_last_selected_observatory(payload)


def save_last_selected_observatory(user_id, observatory_payload):
    """Persist the last selected observatory to the current DB session.

    This helper mutates `db.session` by creating or updating the user's
    `UserSettings` row, but it does not commit the transaction.
    Callers are responsible for committing or rolling back the session.
    """
    normalized = _normalize_last_selected_observatory(observatory_payload)
    settings = _load_user_settings(user_id)
    if settings is None:
        settings = UserSettings(user_id=user_id)

    settings.last_selected_observatory_json = (
        json.dumps(normalized, sort_keys=True, separators=(',', ':')) if normalized else None
    )
    db.session.add(settings)


def _serialize_user_observatory(row):
    return {
        'id': row.id,
        'name': row.name,
        'latitude': row.latitude,
        'longitude': row.longitude,
        'timezone_name': row.timezone_name,
    }

@user_settings_bp.route('/settings', methods=['GET'])
@login_required
def settings():
    selected_features = get_saved_feature_plot_columns(current_user.id)
    observatories = (
        UserObservatory.query.filter_by(user_id=current_user.id)
        .order_by(UserObservatory.name.asc())
        .all()
    )
    return render_template(
        'user_settings.html',
        selected_features=selected_features,
        available_features=list(FEATURE_COLUMNS.keys()),
        custom_observatories=[_serialize_user_observatory(row) for row in observatories],
        max_observatory_name_len=UserObservatory.MAX_NAME_LENGTH,
    )

@user_settings_bp.route('/settings', methods=['POST'])
@login_required
def save_settings():
    # Accept both naming conventions used by forms/clients:
    # - name="default_feature_plot_columns[]"
    # - name="default_feature_plot_columns"
    selected_features = request.form.getlist('default_feature_plot_columns')
    if not selected_features:
        selected_features = request.form.getlist('default_feature_plot_columns[]')

    if not selected_features:
        flash('No features selected. Please select at least one feature.', 'danger')
        return redirect(url_for('user_settings.settings'))

    settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    if settings is None:
        settings = UserSettings(user_id=current_user.id)
    settings.default_feature_plot_columns = json.dumps(_normalize_feature_columns(selected_features))
    db.session.add(settings)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('Failed to save feature plot defaults. Please try if you have any unsaved changes.', 'warning')
        return redirect(url_for('user_settings.settings'))
    except Exception as e:
        db.session.rollback()
        logger.error(f'Database error during commit: {str(e)}', exc_info=True)
        flash('Failed to save feature plot defaults. Please try again later.', 'danger')
        return redirect(url_for('user_settings.settings'))

    flash('Your default feature plot columns have been saved.', 'success')
    return redirect(url_for('user_settings.settings'))