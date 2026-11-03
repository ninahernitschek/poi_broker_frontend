"""Favorites API routes blueprint."""

import logging
from flask import Blueprint, current_app, jsonify, request
from flask_login import login_required
from .. import limiter
from ..services.favorites_service import (
    get_favorite_status,
    get_user_favorites,
    toggle_favorite,
    update_favorite_group,
    get_favorite_groups,
    create_favorite_group,
    delete_favorite_group,
)

logger = logging.getLogger(__name__)

favorites_bp = Blueprint('favorites', __name__, url_prefix='/api')


@favorites_bp.route('/favorite', methods=['GET'])
@login_required
def api_favorite_get():
    """
    GET /api/favorite?locusId=...  -> returns {"fav": true/false}
    """
    locus_id = request.args.get('locusId')
    if not isinstance(locus_id, str) or not locus_id.strip():
        return jsonify({'error': 'Missing locusId'}), 400
    locus_id = locus_id.strip()
    if len(locus_id) > 128:
        return jsonify({'error': 'locusId is too long'}), 400
    fav = get_favorite_status(locus_id)
    return jsonify({'fav': fav})


@favorites_bp.route('/favorites', methods=['GET'])
@login_required
def api_favorites_get():
    """GET /api/favorites?groupId=<id> -> return favorites with IDs, optionally filtered by group."""
    raw_group_id = request.args.get('groupId')
    if raw_group_id is None:
        favs = get_user_favorites()
    elif raw_group_id == 'null':
        favs = get_user_favorites(None)
    else:
        try:
            group_id = int(raw_group_id)
        except (TypeError, ValueError):
            return jsonify({'error': 'groupId must be an integer or null'}), 400
        favs = get_user_favorites(group_id)

    return jsonify({'favorites': favs})


@favorites_bp.route('/favorite', methods=['POST'])
@login_required
@limiter.limit(lambda: current_app.config.get('READ_RATE_LIMIT_MEDIUM', '15 per minute'))
def api_favorite_post():
    """
    POST /api/favorite
    JSON body: { "locusId": "...", "fav": true, "groupId": null }
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid or missing JSON body'}), 400
    
    locus_id = data.get('locusId')
    if not isinstance(locus_id, str) or not locus_id.strip():
        return jsonify({'error': 'Missing locusId'}), 400
    locus_id = locus_id.strip()
    if len(locus_id) > 128:
        return jsonify({'error': 'locusId is too long'}), 400

    if 'fav' not in data or not isinstance(data['fav'], bool):
        return jsonify({'error': 'fav must be a boolean'}), 400

    group_id = data.get('groupId')
    if group_id is not None and (not isinstance(group_id, int) or isinstance(group_id, bool)):
        return jsonify({'error': 'groupId must be an integer or null'}), 400
    
    result, status_code = toggle_favorite(locus_id, data['fav'], group_id)
    return jsonify(result), status_code


@favorites_bp.route('/favorite/<int:favorite_id>/group', methods=['PATCH'])
@login_required
@limiter.limit(lambda: current_app.config.get('READ_RATE_LIMIT_MEDIUM', '15 per minute'))
def api_favorite_update_group(favorite_id):
    """PATCH /api/favorite/<id>/group -> move favorite to a group. Body: {"groupId": <id> or null}"""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON'}), 400

    group_id = data.get('groupId')
    if group_id is not None and (not isinstance(group_id, int) or isinstance(group_id, bool)):
        return jsonify({'error': 'groupId must be an integer or null'}), 400

    result, status_code = update_favorite_group(favorite_id, group_id)
    return jsonify(result), status_code


@favorites_bp.route('/favorite-groups', methods=['GET'])
@login_required
def api_favorite_groups_get():
    """GET /api/favorite-groups -> return all user's groups with favorites counts."""
    try:
        groups = get_favorite_groups()
        return jsonify({'groups': groups})
    except Exception as e:
        logger.error(f'Error listing favorite groups: {str(e)}', exc_info=True)
        return jsonify({'error': 'An error occurred while listing favorite groups.'}), 500


@favorites_bp.route('/favorite-groups', methods=['POST'])
@login_required
@limiter.limit(lambda: current_app.config.get('READ_RATE_LIMIT_MEDIUM', '15 per minute'))
def api_favorite_groups_post():
    """POST /api/favorite-groups -> create a new group. Body: {"name": "Group A"}"""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('name'), str) or not data.get('name'):
        return jsonify({'error': 'name required'}), 400
    
    result, status_code = create_favorite_group(data['name'])
    return jsonify(result), status_code


@favorites_bp.route('/favorite-groups/<int:group_id>', methods=['DELETE'])
@login_required
@limiter.limit(lambda: current_app.config.get('READ_RATE_LIMIT_MEDIUM', '15 per minute'))
def api_favorite_groups_delete(group_id):
    """DELETE /api/favorite-groups/<id> -> delete a group (orphans its favorites)."""
    result, status_code = delete_favorite_group(group_id)
    return jsonify(result), status_code
