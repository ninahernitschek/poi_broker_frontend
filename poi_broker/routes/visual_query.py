"""Visual query builder routes blueprint."""

import logging
import json
import time
from flask import Blueprint, current_app, jsonify, request, render_template
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException
from .. import db, limiter
from ..models import Watchlist
from ..services.query_service import get_preview_sql, get_query_match_count

logger = logging.getLogger(__name__)

visual_query_bp = Blueprint('visual_query', __name__)


@visual_query_bp.route('/visual_query')
@login_required
def visual_query():
    """Show visual query interface."""
    return render_template('visual_query.html')


@visual_query_bp.route('/api/preview-query', methods=['POST'])
@login_required
# Same limit as /api/export-query: preview compiles (and now bounds) rule
# payloads per request, so it should not be cheaper to hammer than the
# counting endpoint.
@limiter.limit(lambda: current_app.config.get('READ_RATE_LIMIT_LAX', '30 per minute'))
def preview_query():
    """Get SQL preview for a query builder payload."""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'error': 'Invalid or missing JSON'}), 400

        # Accept either direct rules object or wrapped payload {'rules': {...}}.
        rules_payload = data
        if isinstance(data.get('rules'), dict) and 'condition' in data.get('rules', {}):
            rules_payload = data['rules']

        sql_preview = get_preview_sql(rules_payload)
        return jsonify({'sql': sql_preview})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400 # ValueError is raised for known issues like invalid rules or no filters built, so return the message to client
    except RecursionError:
        # Deeply nested rules exhaust the stack inside json.loads / rule
        # validation; the payload is unparseable, so this is a client error.
        return jsonify({'error': 'Request payload is too deeply nested'}), 400
    except HTTPException:
        # Let Flask serve real HTTP errors (e.g. 413 for an oversized body)
        # instead of masking them as a server error.
        raise
    except Exception as e:
        logger.error(f'Error in preview_query: {str(e)}', exc_info=True)
        return jsonify({'error': 'Error in preview_query'}), 500


@visual_query_bp.route('/api/export-query', methods=['POST'])
@login_required
@limiter.limit(lambda: current_app.config.get('READ_RATE_LIMIT_LAX', '30 per minute'))
def export_query():
    """Get match count for a query builder payload."""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'error': 'Invalid or missing JSON'}), 400

        # Accept either direct rules object or wrapped payload {'rules': {...}}.
        rules_payload = data
        if isinstance(data.get('rules'), dict) and 'condition' in data.get('rules', {}):
            rules_payload = data['rules']

        count = get_query_match_count(rules_payload)
        return jsonify({'count': count})
    except ValueError as e:
        logger.error(f'ValueError in export_query: {str(e)}', exc_info=True)
        return jsonify({'error': 'Invalid query rules provided.'}), 400
    except RecursionError:
        return jsonify({'error': 'Request payload is too deeply nested'}), 400
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error in export_query: {str(e)}', exc_info=True)
        return jsonify({'error': 'An error occurred while exporting.'}), 500

#NOTE: Watchlist data is strictly private per user and never shared
@visual_query_bp.route('/api/watchlist', methods=['POST'])
@login_required
def save_watchlist():
    """Save a watchlist from query builder rules."""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'error': 'Invalid or missing JSON'}), 400

        name = data.get('name', '')
        if not isinstance(name, str) or not name.strip():
            return jsonify({'error': 'Watchlist name is required'}), 400
        name = name.strip()
        if len(name) > 128:
            return jsonify({'error': 'Watchlist name must be 128 characters or fewer'}), 400

        rules_payload = data.get('rules')
        if isinstance(rules_payload, dict) and 'condition' in rules_payload:
            pass
        elif isinstance(data.get('rules'), dict):
            rules_payload = data['rules']
        else:
            return jsonify({'error': 'Invalid querybuilder rules payload'}), 400

        # Persist rules_json as the executable source of truth. sql_where is
        # a display preview only and must never be concatenated into SQL.
        sql_where = get_preview_sql(rules_payload)

        now_epoch = int(time.time())

        watchlist = Watchlist(
            user_id=current_user.id,
            name=name,
            rules_json=json.dumps(rules_payload),
            sql_where=sql_where,
            created_at=now_epoch,
        )
        db.session.add(watchlist)
        db.session.commit()

        return jsonify({'status': 'ok', 'id': watchlist.id, 'name': watchlist.name}), 201
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': f"A watchlist named '{name}' already exists!"}), 409
    except ValueError as e:
        db.session.rollback()
        logger.error(f"ValueError in save_watchlist: {str(e)}", exc_info=True)
        return jsonify({'error': 'Unable to save invalid querybuilder rules!'}), 400
    except RecursionError:
        db.session.rollback()
        return jsonify({'error': 'Request payload is too deeply nested'}), 400
    except HTTPException:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in save_watchlist: {str(e)}", exc_info=True)
        return jsonify({'error': 'An error occurred while saving the watchlist.'}), 500


@visual_query_bp.route('/api/watchlist', methods=['GET'])
@login_required
def list_watchlists():
    """List all watchlists for the current user."""
    try:
        rows = Watchlist.query.filter_by(user_id=current_user.id).order_by(Watchlist.created_at.desc()).all()
        result = [
            {'id': w.id, 'name': w.name, 'sql_where': w.sql_where, 'created_at': w.created_at}
            for w in rows
        ]
        return jsonify({'watchlists': result})
    except Exception as e:
        logger.error(f"Error in list_watchlists: {str(e)}", exc_info=True)
        return jsonify({'error': 'An error occurred while listing watchlists.'}), 500


@visual_query_bp.route('/api/watchlist/<int:wid>', methods=['DELETE'])
@login_required
def delete_watchlist(wid):
    """Delete a watchlist by ID."""
    try:
        watchlist = Watchlist.query.filter_by(id=wid, user_id=current_user.id).first()
        if watchlist is None:
            return jsonify({'error': 'Not found'}), 404
        db.session.delete(watchlist)
        db.session.commit()
        return jsonify({'status': 'ok'}), 200
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'Cannot delete watchlist because it is in use.'}), 409
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in delete_watchlist: {str(e)}", exc_info=True)
        return jsonify({'error': 'An error occurred while deleting the watchlist.'}), 500
