"""Export data API routes blueprint."""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from .. import db, limiter
from ..models import ExportTask
from ..services.filter_service import datetime_to_mjd, mjd_to_datetime
from ..services.query_service import get_query_match_count
from ..tasks import create_export_file, reset_stale_export_tasks

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """Read an integer env var, falling back to ``default`` on missing/invalid."""
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Maximum number of rows a single export may return. Larger exports are declined.
MAX_EXPORT_ROWS = _env_int('EXPORT_MAX_ROWS', 1_000_000)

# Legacy export_task rows were backfilled by the one-shot
# tools/apply_export_snapshot_mjd.sql script (applied in production, since
# removed — see docs/adr/adr_db_schema_migration_strategy.md) with the
# "no cutoff" sentinel 1e9 (far above any real alert MJD, ~6e4). MJD 1e9 is
# ~year 2.7M, which astropy cannot convert to a datetime, so only real
# cutoffs are ever rendered as dates.
_SNAPSHOT_RENDER_MAX_MJD = 1e8


def _renderable_snapshot(snapshot_mjd) -> bool:
    """True when snapshot_mjd is a real cutoff, not NULL or the migration sentinel."""
    return snapshot_mjd is not None and snapshot_mjd < _SNAPSHOT_RENDER_MAX_MJD

export_bp = Blueprint('export', __name__, url_prefix='/export')


@export_bp.route('', methods=['GET'])
@login_required
def export_page():
    """
    Render the export page with query builder UI and current export status.
    
    GET /export -> HTML page with query builder and export status
    """
    # Get the most recent export task for the user
    recent_task = ExportTask.query.filter_by(user_id=current_user.id).order_by(
        ExportTask.created_at.desc()
    ).first()

    # Human-readable snapshot time for the last export.
    data_as_of = None
    if recent_task and _renderable_snapshot(recent_task.snapshot_mjd):
        data_as_of = mjd_to_datetime(recent_task.snapshot_mjd).strftime(
            '%Y-%m-%d %H:%M:%S UTC'
        )

    return render_template(
        'export.html',
        recent_task=recent_task,
        data_as_of=data_as_of,
        max_export_rows=MAX_EXPORT_ROWS
    )


@export_bp.route('', methods=['POST'])
@login_required
@limiter.limit(lambda: current_app.config.get('READ_RATE_LIMIT_LAX', '30 per minute'))
def export_submit():
    """
    Handle export form submission. Creates an ExportTask and enqueues the background job.
    
    POST /export with JSON: {rules: [...]} -> redirects to GET /export with message
    """
    data = request.get_json(silent=True) if request.is_json else {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid or missing JSON'}), 400

    # Accept either the raw query-builder object {condition, rules: [...]} (what
    # the frontend sends) or a wrapped payload {'rules': {...}}. The full rules
    # dict is what build_query_from_rules() expects.
    query_params = data
    if isinstance(data.get('rules'), dict) and 'condition' in data.get('rules', {}):
        query_params = data['rules']

    if not query_params or not isinstance(query_params.get('rules'), list):
        return jsonify({'error': 'No query parameters provided'}), 400
    
    # One active export per user. If the only blocker is past
    # EXPORT_STALE_MAX_AGE_SECONDS (consumer down / no periodic tick), fail that
    # user's stale row and continue. Fresh PENDING/RUNNING still 409s.
    active_task = ExportTask.query.filter_by(user_id=current_user.id).filter(
        ExportTask.status.in_(ExportTask.ACTIVE_STATUSES)
    ).first()

    if active_task:
        reset_stale_export_tasks(user_id=current_user.id)
        db.session.expire(active_task)
        active_task = ExportTask.query.filter_by(user_id=current_user.id).filter(
            ExportTask.status.in_(ExportTask.ACTIVE_STATUSES)
        ).first()

    if active_task:
        return jsonify({
            'error': 'You already have an active export task. Please wait for it to complete or download the file.',
            'task_id': active_task.id
        }), 409

    # Snapshot cutoff, computed once here and shared by the pre-count and the
    # background export: the CSV only contains alerts with date_alert_mjd below
    # this MJD, so re-running the same rules reproduces it regardless of how
    # long the task waits in the queue.
    snapshot_mjd = datetime_to_mjd(datetime.now(timezone.utc))

    # Enforce the maximum export size before enqueuing.
    try:
        match_count = get_query_match_count(query_params, max_alert_mjd=snapshot_mjd)
    except Exception:
        logger.exception('Failed to count matches for export')
        return jsonify({'error': 'Could not validate the export query.'}), 400

    if match_count > MAX_EXPORT_ROWS:
        return jsonify({
            'error': (
                f'This query matches {match_count:,} rows, which exceeds the '
                f'maximum of {MAX_EXPORT_ROWS:,} rows per export. Please narrow '
                'your query.'
            )
        }), 400

    try:
        # Create new ExportTask record
        export_task = ExportTask(
            user_id=current_user.id,
            status='PENDING',
            snapshot_mjd=snapshot_mjd,
        )
        db.session.add(export_task)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({
            'error': 'You already have an active export task. Please wait for it to complete or download the file.'
        }), 409
    logger.info(f'Created ExportTask {export_task.id} for user {current_user.id}')
    
    # Enqueue the background task. If enqueueing raises after the row is
    # committed, mark the task FAILED so the user's active-task slot is freed
    # immediately (the stale-task guard only fails PENDING rows after
    # EXPORT_STALE_MAX_AGE_SECONDS). IntegrityError for a concurrent insert is
    # already handled on the commit above.
    try:
        create_export_file(
            query_params=query_params,
            user_id=current_user.id,
            task_id=export_task.id,
        )
    except Exception:
        logger.exception(f'Failed to enqueue background task for ExportTask {export_task.id}')
        export_task.status = 'FAILED'
        export_task.error_message = 'Failed to enqueue export task'
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception(
                f'Could not mark ExportTask {export_task.id} as FAILED after enqueue error'
            )
        return jsonify({'error': 'Failed to start export task'}), 500
    logger.info(f'Enqueued background task for ExportTask {export_task.id}')
    
    return jsonify({
        'success': True,
        'message': 'Export started. This may take a few moments.',
        'task_id': export_task.id
    }), 202


@export_bp.route('/download/<int:task_id>', methods=['GET'])
@login_required
def export_download(task_id: int):
    """
    Download an exported CSV file.
    
    GET /export/download/<task_id> -> CSV file download
    """
    export_task = db.session.get(ExportTask, task_id)
    
    if not export_task:
        logger.warning(f'Export task {task_id} not found')
        flash('Export task not found', 'danger')
        return redirect(url_for('export.export_page'))
    
    if export_task.user_id != current_user.id:
        logger.warning(f'User {current_user.id} attempted to download task {task_id} from user {export_task.user_id}')
        flash('Unauthorized', 'danger')
        return redirect(url_for('export.export_page'))
    
    if export_task.status != 'SUCCESS':
        logger.warning(f'Export task {task_id} has status {export_task.status}, not ready for download')
        flash(f'Export task is not ready for download (status: {export_task.status})', 'warning')
        return redirect(url_for('export.export_page'))
    
    if not export_task.file_path or not Path(export_task.file_path).exists():
        logger.error(f'Export file not found at {export_task.file_path}')
        flash('Export file not found on disk', 'danger')
        return redirect(url_for('export.export_page'))
    
    try:
        return send_file(
            export_task.file_path,
            as_attachment=True,
            download_name=f'export_{task_id}.csv',
            mimetype='text/csv',
        )
    except Exception as e:
        logger.exception(f'Failed to serve export file {export_task.file_path}: {type(e).__name__}')
        flash('Failed to download file', 'danger')
        return redirect(url_for('export.export_page'))


@export_bp.route('/status/<int:task_id>', methods=['GET'])
@login_required
def export_status(task_id: int):
    """
    Get the status of an export task.
    
    GET /export/status/<task_id> -> JSON with task status
    """
    export_task = db.session.get(ExportTask, task_id)
    
    if not export_task:
        return jsonify({'error': 'Task not found'}), 404
    
    if export_task.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    return jsonify({
        'task_id': export_task.id,
        'status': export_task.status,
        'created_at': export_task.created_at.isoformat(),
        'updated_at': export_task.updated_at.isoformat(),
        # Alert-time snapshot cutoff (MJD) the export was taken at; rows with
        # date_alert_mjd >= snapshot_mjd are never included.
        'snapshot_mjd': export_task.snapshot_mjd,
        'data_as_of': (
            mjd_to_datetime(export_task.snapshot_mjd).isoformat()
            if _renderable_snapshot(export_task.snapshot_mjd) else None
        ),
        # NOTE: file_path is deliberately NOT included. It is a server
        # filesystem path and would leak instance structure to the client.
        # Downloading goes through /export/download/<id> instead.
        'download_url': url_for('export.export_download', task_id=export_task.id) if export_task.status == 'SUCCESS' else None,
        'error_message': export_task.error_message
    })
