import csv
import io
import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path

import bokeh as bokeh_pkg
from astropy.coordinates import EarthLocation
from astropy.time import Time
from flask import (
    Blueprint,
    Flask,
    Response,
    abort,
    current_app,
    jsonify,
    make_response,
    render_template,
    request,
    send_from_directory,
)
from flask_login import current_user, login_required

from . import db, limiter
from .constants.features import FEATURE_COLUMNS, default_feature_plot_columns
from .helpers import object_as_dict, result_to_dict, safe_serialize
from .models import Classification, Crossmatches, UserObservatory, Ztf
from .routes import (
    export_bp,
    favorites_bp,
    features_bp,
    filter_bookmarks_bp,
    lightcurve_bp,
    user_observatories_bp,
    visual_query_bp,
)
from .services.catalog_list import (
    MAX_PAGE,
    PAGE_SIZE,
    catalog_null_metadata,
    count_is_available,
    cursor_from_row,
    fetch_keyset,
    fetch_page,
    last_page_from_total,
    optional_catalog_count,
    parse_keyset_request,
    parse_legacy_date_cursor,
    resolve_catalog_total,
    should_use_keyset,
)
from .services.catalog_query import (
    SORT_LABELS,
    build_catalog_query,
    catalog_filter_query_string,
    catalog_href,
    catalog_query_string,
    project_catalog_columns,
)
from .user_settings import (
    UserSettings,
    get_saved_feature_plot_columns,
    get_saved_last_selected_observatory,
    get_user_settings,
    user_settings_bp,
)

bokeh_version = version("bokeh")
_BOKEH_JS_DIR = Path(bokeh_pkg.__file__).resolve().parent / "server" / "static" / "js"

logger = logging.getLogger(__name__)
main_blueprint = Blueprint('main', __name__)


@main_blueprint.route("/bokeh.min.js")
def bokeh_js() -> Response:
    """Serve the Bokeh JS that ships with the installed Python package."""
    return send_from_directory(_BOKEH_JS_DIR, "bokeh.min.js", max_age=86400)

@lru_cache(maxsize=8192)
def _format_mjd_cached(mjd_value: float) -> str:
    jdate = mjd_value + 2400000.5
    dt = Time(jdate, format='jd', scale='utc').to_datetime(timezone=timezone.utc) # Convert to timezone-aware datetime in UTC
    return  dt.strftime('%Y-%m-%d %H:%M:%S')


@lru_cache(maxsize=1)
def _get_builtin_observatory_options() -> list[dict[str, str]]:
    builtin_names = list(EarthLocation.get_site_names())
    return [{'value': f'builtin:{name}', 'label': name} for name in builtin_names]


def _resolve_selected_observatory(
    selected_meta: dict | None,
    builtin_values: set[str],
    custom_values: set[str]
) -> str | None:
    """Validate and resolve a saved observatory selection using O(1) lookup.
    
    Args:
        selected_meta: Dict from get_saved_last_selected_observatory() with 'source' key
                       ('builtin' or 'custom') and selection identifier (name or id).
        builtin_values: Set of valid builtin option values for efficient lookup.
        custom_values: Set of valid custom option values for efficient lookup.
    
    Returns:
        Valid option value (e.g., 'builtin:Palomar', 'custom:42') or None if selection
        is invalid, deleted, or doesn't match saved metadata.
    """
    if not isinstance(selected_meta, dict):
        return None
    
    source = selected_meta.get('source')
    
    if source == 'custom':
        selected_id = selected_meta.get('id')
        if isinstance(selected_id, int) and not isinstance(selected_id, bool):
            candidate = f'custom:{selected_id}'
            if candidate in custom_values:
                return candidate
    elif source == 'builtin':
        selected_name = selected_meta.get('name')
        if isinstance(selected_name, str):
            candidate = f'builtin:{selected_name}'
            if candidate in builtin_values:
                return candidate
    
    return None


def _build_observatory_context(settings_row: UserSettings | None = None) -> dict[str, list | str | None]:
    """Build observatory context for the main page.
    
    Constructs builtin and custom observatory options, restores the user's last
    selected observatory (if authenticated), and provides a fallback to the first
    available option if the saved selection is invalid or deleted.
    
    Args:
        settings_row: Optional pre-loaded UserSettings row to avoid a redundant
                      DB query when the caller already has it (e.g. start()).
    
    Returns:
        dict with keys:
            - 'builtin_options': list[dict] of {'value', 'label'} for astropy sites
            - 'custom_options': list[dict] of {'value', 'label'} for user observatories
                                (empty if not authenticated)
            - 'selected_value': str (e.g., 'builtin:Palomar') or None if no options available
    """
    builtin_options = _get_builtin_observatory_options()
    builtin_values = {opt['value'] for opt in builtin_options}

    custom_options = []
    custom_values = set()
    
    if current_user.is_authenticated:
        custom_rows = (
            UserObservatory.query.filter_by(user_id=current_user.id)
            .order_by(UserObservatory.name.asc())
            .all()
        )
        custom_options = [{'value': f'custom:{row.id}', 'label': row.name} for row in custom_rows]
        custom_values = {opt['value'] for opt in custom_options}

    # Attempt to restore saved selection; gracefully falls back if invalid/deleted
    selected_value = _resolve_selected_observatory(
        get_saved_last_selected_observatory(current_user.id, settings_row) if current_user.is_authenticated else None,
        builtin_values,
        custom_values
    )

    # Fallback to first available option if no valid saved selection
    if selected_value is None:
        if builtin_options:
            selected_value = builtin_options[0]['value']
        elif custom_options:
            selected_value = custom_options[0]['value']

    return {
        'custom_options': custom_options,
        'builtin_options': builtin_options,
        'selected_value': selected_value,
    }


@main_blueprint.route('/', methods=['GET'])
@limiter.limit(lambda: current_app.config.get('READ_RATE_LIMIT_LAX', '30 per minute'))
def start() -> str:
    logger.debug('Request with request_args: %s', json.dumps(request.args.to_dict(flat=False)))

    page = request.args.get('page', 1, type=int)
    if page is None or page < 1:
        abort(404)
    # Only the legacy `?page=N` OFFSET jump is bounded; every UI link is a
    # keyset cursor, whose cost is independent of depth.
    if page > MAX_PAGE:
        abort(404, description=(
            f'Page {page} is beyond the {MAX_PAGE}-page limit for direct page '
            f'numbers. Use the First / Prev / Next / Last buttons, or narrow the '
            f'catalog with a filter.'
        ))

    build = build_catalog_query(request.args)
    list_query = project_catalog_columns(build.list_query)
    sort = build.sort
    use_keyset = should_use_keyset(request.args)
    keyset_direction = 'first'

    if use_keyset:
        keyset_direction, cursor = parse_keyset_request(request.args, numeric=sort.numeric)
        if keyset_direction == 'nulls' and not sort.nullable:
            # Non-nullable sort columns have no null segment; treat ?nulls=1 as page 1.
            keyset_direction = 'first'
        if cursor is None and keyset_direction == 'first' and sort.key == 'date':
            # Pre-2026-10 bookmarks carried before_mjd/after_mjd, which only
            # ever held a date_alert_mjd value.
            legacy = parse_legacy_date_cursor(request.args, 'before')
            if legacy is not None:
                keyset_direction, cursor = 'before', legacy
            else:
                legacy = parse_legacy_date_cursor(request.args, 'after')
                if legacy is not None:
                    keyset_direction, cursor = 'after', legacy
        items, has_next, has_prev = fetch_keyset(
            list_query,
            sort_column=sort.column,
            cursor=cursor,
            direction=keyset_direction,
            sort_desc=sort.desc,
            page_size=PAGE_SIZE,
        )
        if keyset_direction == 'nulls' and not items:
            abort(404)
    else:
        items, has_next = fetch_page(
            list_query,
            page,
            page_size=PAGE_SIZE,
            sort_column=sort.column if sort.nullable else None,
            sort_desc=sort.desc,
        )
        if page > 1 and not items:
            abort(404)
        has_prev = page > 1

    total_queries, count_deferred = resolve_catalog_total(
        build,
        items=items,
        has_next=has_next,
        page=page,
        use_keyset=use_keyset,
        keyset_direction=keyset_direction,
        has_prev=has_prev,
    )
    sort_null_count, nulls_exist = catalog_null_metadata(
        build, items,
        complete=not has_next and (not has_prev if use_keyset else page == 1),
    )
    last_page = last_page_from_total(total_queries) if total_queries is not None else None

    query_string = catalog_query_string(request.args)
    filter_query_string = catalog_filter_query_string(request.args)
    first_href = catalog_href(query_string)
    if use_keyset:
        prev_href = (
            catalog_href(query_string, cursor_from_row(items[0], sort.attr).as_after_params())
            if items and has_prev else None
        )
        next_href = (
            catalog_href(query_string, cursor_from_row(items[-1], sort.attr).as_before_params())
            if items and has_next else None
        )
        last_href = catalog_href(query_string, {'last': '1'}) if has_next else None
    else:
        prev_href = catalog_href(query_string, {'page': str(page - 1)}) if has_prev else None
        next_href = catalog_href(query_string, {'page': str(page + 1)}) if has_next else None
        # The OFFSET path still applies MAX_PAGE, so a Last link past it would
        # 404 — hide it instead (hide_last).
        last_href = (
            catalog_href(query_string, {'page': str(last_page)})
            if last_page and has_next and last_page <= MAX_PAGE
            else None
        )

    hide_last = bool(has_next and last_href is None)

    # Load the user's settings row once and reuse it across lookups to avoid
    # redundant DB queries on every authenticated main-page request.
    settings_row = get_user_settings(current_user.id) if current_user.is_authenticated else None
    observatory_context = _build_observatory_context(settings_row)

    return render_template(
        "main.html",
        total_queries=total_queries,
        count_deferred=count_deferred,
        count_lower_bound=len(items),
        count_available=(
            count_is_available(build)
            and current_app.config.get('CATALOG_COUNT_BUDGET_SECONDS', 2.0) > 0
        ),
        table=items,
        page=page,
        has_next=has_next,
        has_prev=has_prev,
        last_page=last_page,
        query_string=query_string,
        filter_query_string=filter_query_string,
        first_href=first_href,
        prev_href=prev_href,
        next_href=next_href,
        last_href=last_href,
        hide_last=hide_last,
        page_size=PAGE_SIZE,
        max_page=MAX_PAGE,
        filter_warning=build.filter_warning,
        sort_label=SORT_LABELS.get(sort.key, sort.key),
        sort_nullable=sort.nullable,
        sort_null_count=sort_null_count,
        nulls_href=(
            catalog_href(query_string, {'nulls': '1'}) if nulls_exist else None
        ),
        custom_observatory_options=observatory_context['custom_options'],
        builtin_observatory_options=observatory_context['builtin_options'],
        selected_observatory_value=observatory_context['selected_value'],
        today_utc=datetime.now(timezone.utc).date(),
        bokeh_version=bokeh_version,
        available_feature_columns=FEATURE_COLUMNS,
        default_feature_plot_columns=(get_saved_feature_plot_columns(current_user.id, settings_row)
                                      if current_user.is_authenticated
                                      else default_feature_plot_columns()),
    )


@main_blueprint.route('/api/catalog-count', methods=['GET'])
@limiter.limit(lambda: current_app.config.get('READ_RATE_LIMIT_LAX', '30 per minute'))
def catalog_count() -> Response:
    """Exact match count for the current main-page filters (on demand)."""
    build = build_catalog_query(request.args, include_sort=False)
    total = optional_catalog_count(
        build, current_app.config.get('CATALOG_COUNT_BUDGET_SECONDS', 2.0),
    )
    if total is None:
        return jsonify({
            'error': 'count_unavailable',
            'message': 'Exact counting took too long. Narrow the filters and try again.',
        }), 422
    return jsonify({'count': total})

@main_blueprint.route('/help', methods=['GET'])
def help() -> str:
    return render_template(
        "help.html"
    )

@main_blueprint.route('/contact', methods=['GET'])
def contact() -> str:
    return render_template(
        "contact.html"
    )

@main_blueprint.route('/profile')
@login_required
def profile() -> str:
    """Show user profile; favorites/groups load via AJAX from /api/*."""
    return render_template('profile.html', name=current_user.name)

@main_blueprint.route('/download_alerts_csv', methods=['GET'])
@limiter.limit(lambda: current_app.config.get('READ_RATE_LIMIT_MEDIUM', '15 per minute'))
def download_alerts_csv() -> Response:
    """Download featuretable + classification fields for multiple alert_ids as CSV."""
    alert_ids = [x.strip() for x in request.args.getlist('alert_id') if x and x.strip()]
    if not alert_ids:
        return Response('Missing alert_id', status=400)
    if len(alert_ids) > PAGE_SIZE:
        return Response(f'Too many alert_id parameters (maximum {PAGE_SIZE})', status=400)

    try:
        csv_text, row_count = _build_alerts_csv(alert_ids)
        if row_count == 0:
            return Response('No feature records found for provided alert_ids', status=404)

        response = make_response(csv_text)
        response.headers['Content-Disposition'] = f'attachment; filename="alerts_{row_count}.csv"'
        response.mimetype = 'text/csv'
        return response
    except Exception as e:
        logger.exception(f'Error downloading CSV for alert_ids {alert_ids}: {type(e).__name__}')
        return Response('Error downloading CSV for selected alert ids!', status=500)


def _build_alerts_csv(alert_ids: list[str]) -> tuple[str, int]:
    """Build CSV text for alert IDs and return (csv_text, row_count)."""
    unique_alert_ids = list(dict.fromkeys(alert_ids))

    feature_rows = db.session.query(Ztf).filter(Ztf.alert_id.in_(unique_alert_ids)).all()
    if not feature_rows:
        return '', 0

    feature_by_alert_id = {row.alert_id: row for row in feature_rows}
    classification_rows = db.session.query(Classification).filter(Classification.alert_id.in_(unique_alert_ids)).all()
    classification_by_alert_id = {row.alert_id: row for row in classification_rows}

    ordered_feature_rows = [feature_by_alert_id[aid] for aid in unique_alert_ids if aid in feature_by_alert_id]

    first_feature_data = object_as_dict(ordered_feature_rows[0])
    classification_columns = Classification.__table__.columns.keys()

    fieldnames = list(first_feature_data.keys()) + [
        col_name for col_name in classification_columns if col_name != 'alert_id'
    ]

    csv_buffer = io.StringIO()
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
    writer.writeheader()

    for feature_row in ordered_feature_rows:
        merged_row = object_as_dict(feature_row)
        classification_row = classification_by_alert_id.get(feature_row.alert_id)
        if classification_row is not None:
            classification_data = object_as_dict(classification_row)
        else:
            classification_data = {col_name: None for col_name in classification_columns}

        for col_name in classification_columns:
            if col_name == 'alert_id':
                continue
            merged_row[col_name] = classification_data.get(col_name)

        writer.writerow(merged_row)

    return csv_buffer.getvalue(), len(ordered_feature_rows)


@main_blueprint.route('/query_crossmatches', methods=['GET'])
@limiter.limit(lambda: current_app.config.get('READ_RATE_LIMIT_LAX', '30 per minute'))
def query_crossmatches() -> Response:
    """Query crossmatches for a given locus id."""
    locus_id = request.args.get('locusId') # ex: locusname="ANT2018fywy2"
    if not locus_id:
        return Response('Missing locusId', status=400)

    try:
        #query all Crossmatches records from DB where locus id equals given id
        crossmatches_query = db.session.query(Crossmatches)
        crossmatches_query = crossmatches_query.filter(Crossmatches.locus_id == locus_id)
        crossmatches_list = result_to_dict(crossmatches_query.all())

        response = current_app.response_class(
            response=safe_serialize(crossmatches_list), #TODO? array[] with single row when using BootstrapTable?
            status=200,
            mimetype='application/json'
        )
        return response
    except Exception as e:
        logger.exception(f'Error querying crossmatches for locusId {locus_id}: {type(e).__name__}')
        # Text error body is intentional: front-end error handler displays xhr.responseText verbatim
        return Response('Error querying crossmatches for selected locus id!', status=500)

# Register Jinja filters
@main_blueprint.app_template_filter('format_number')
def format_number_filter(value) -> str:
    """Thousands separators, for row counts in the UI."""
    try:
        return f'{int(value):,}'
    except (TypeError, ValueError):
        return str(value)


@main_blueprint.app_template_filter('astro_filter')
def astro_filter(passband: str) -> str:
    if passband == "g":
        return "g"
    elif passband == "R":
        return "R"
    elif passband == "i":
        return "i"
    else:
        return ""

@main_blueprint.app_template_filter('mag_filter')
def mag_filter(num: float | str | None) -> float | str | None:
    # 83 production rows store ant_mag_corrected as TEXT ('R', 'g') instead of a
    # number. They are all from one ingest batch at MJD 60368.5-60369.3; the
    # newest 439,251 rows have none. round() raises TypeError on those, which
    # turns one bad row into a 500 for the whole page. Sorting by magnitude
    # descending puts them first (SQLite orders TEXT after REAL), so they are
    # reachable from page 1.
    if isinstance(num, (int, float)):
        return round(num, 3)
    return num

@main_blueprint.app_template_filter('format_mjd_readable')
def format_mjd_readable(value: float | None) -> str:
    if value is None:
        return ''

    try:
        mjd_value = float(value)
        return _format_mjd_cached(mjd_value) # Use Astropy for accurate conversion
    except (TypeError, ValueError, OverflowError):
        return ''

@main_blueprint.app_template_filter('epoch_utc_date')
def epoch_utc_date(value: int | None) -> str:
    """Render an epoch-seconds column (e.g. User.password_changed_at) as a UTC date."""
    if value is None:
        return ''
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime('%Y-%m-%d')
    except (TypeError, ValueError, OSError, OverflowError):
        return ''


def register_blueprints(app: Flask) -> None:
    """Register all blueprints with the Flask app."""
    app.register_blueprint(main_blueprint)
    app.register_blueprint(favorites_bp)
    app.register_blueprint(filter_bookmarks_bp)
    app.register_blueprint(visual_query_bp)
    app.register_blueprint(lightcurve_bp)
    app.register_blueprint(features_bp)
    app.register_blueprint(user_observatories_bp)
    app.register_blueprint(user_settings_bp)
    app.register_blueprint(export_bp)
