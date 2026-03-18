from flask import (render_template, abort, jsonify, request, Response,
                   redirect, url_for, make_response, Blueprint, current_app)
from flask_login import login_required, current_user
import logging
from functools import lru_cache
from datetime import timezone
from astropy.time import Time
from astropy.coordinates import EarthLocation
import re
import json

from .helpers import extract_numbers, extract_dates, extract_float_filter, extract_mjd_filter, safe_serialize, object_as_dict, result_to_dict
from . import db
from .models import Ztf, Crossmatches, User, Favorite, FavoriteGroup

from importlib.metadata import version
bokeh_version = version("bokeh")
from bokeh.embed import components
from bokeh.plotting import figure
from bokeh.models import Legend

#debug/set_trace breakpoints
# import pdb
# from pprint import pprint

logger = logging.getLogger(__name__)
main_blueprint = Blueprint('main', __name__)

@lru_cache(maxsize=8192)
def _format_mjd_cached(mjd_value: float) -> str:
    jdate = mjd_value + 2400000.5
    dt = Time(jdate, format='jd', scale='utc').to_datetime(timezone=timezone.utc) # Convert to timezone-aware datetime in UTC
    return  dt.strftime('%Y-%m-%d %H:%M:%S')


@main_blueprint.route('/', methods=['GET'])
def start():
    #app.logger.info('Info')
    #app.logger.warning('Warn')
    #logging.error('Exception occurred', exc_info=True) #or: logging.exception()
    logging.info('Request with request_args:' + json.dumps(request.args))
    #pdb.set_trace()
    page = request.args.get('page', 1, type=int)

    filter_warning_message = ''
    if request.method == 'GET':
        query = db.session.query(Ztf)

        if request.args.get('date'):
            date_input = extract_dates(request.args.get('date'))
            if date_input:
                query = extract_mjd_filter(date_input, Ztf.date_alert_mjd, query)
            else:
                filter_warning_message += 'Date filter cannot be applied - Enter a valid 8-digit integer date of the form yyyymmdd, e.g. "20201207", or range, e.g., "20201207 20201209". You can filter the columns by entering values and then click the "Filter" button.'
        
        if request.args.get('date_alert_mjd'):
            date_input = extract_numbers(request.args.get('date_alert_mjd'))
            print(date_input)
            if date_input != None:
                query = extract_float_filter(date_input, Ztf.date_alert_mjd, query)
            else:
                filter_warning_message += 'Date filter cannot be applied - Enter a valid 8-digit integer date of the form yyyymmdd, e.g. "20201207", or range, e.g., "20201207 20201209". You can filter the columns by entering values and then click the "Filter" button.'

        if request.args.get('alert_id'):
            alertId = request.args.get('alert_id', '').strip()
            if re.match(r'^(?:ztf_candidate|lsst):\d{18,}$', alertId): # if it contains 18+ digits, we got a complete alert_id and can query it directly.
                query = query.filter(Ztf.alert_id == alertId)
            elif re.match(r'^(?:ztf|lsst)\D*$', alertId): # otherwise, we allow for partial matching of alert_id to find alerts with a specific prefix, e.g. ztf / lsst
                search = "{}%".format(alertId)
                query = query.filter(Ztf.alert_id.like(search))
            elif alertId != '':
                filter_warning_message += 'Alert ID cannot be filter by partial IDs - Enter a full alert ID, e.g. "ztf_candidate:335155568501", or "lsst:170094456539709554", or just the catalog prefix, e.g. "ztf" or "lsst".'

        if request.args.get('ztf_object_id'):
            query = query.filter(Ztf.ztf_object_id == request.args.get('ztf_object_id'))

        #if request.args.get('jd'):
            #jd_input = extract_numbers(request.args.get('jd'))
            #if jd_input != None:
                #query = extract_float_filter(jd_input, Ztf.jd, query)
            #else:
                #filter_warning_message += 'Jd filter cannot be applied - Enter a valid number, e.g., "2459190.8746528", or range, e.g., "2459190.84 2459190.86". You can filter the columns by entering values and then click the "Filter" button.'

        #if request.args.get('filter'):
            #query = query.filter(Ztf.filter == int(request.args.get('filter'))) # 1:g, 2:r, 3:i
        if request.args.get('ant_passband'):
            ant_passband = request.args.get('ant_passband')
            query = query.filter(Ztf.ant_passband == ant_passband) # g, R, i


        if request.args.get('locus_id'):
            query = query.filter(Ztf.locus_id == request.args.get('locus_id'))

        if request.args.get('locus_ra'):
            ra_input = extract_numbers(request.args.get('locus_ra'))
            if ra_input != None:
                query = extract_float_filter(ra_input, Ztf.locus_ra, query, decimals=5)
            else:
                filter_warning_message += 'Ra filter cannot be applied - Enter a valid number, e.g., "118.61421", or range, e.g., "80 90". You can filter the columns by entering values and then click the "Filter" button.'

        if request.args.get('locus_dec'):
            dec_input = extract_numbers(request.args.get('locus_dec'))
            if dec_input != None:
                query = extract_float_filter(dec_input, Ztf.locus_dec, query, decimals=5)
            else:
                filter_warning_message += 'Dec filter cannot be applied - Enter a valid number, e.g., "-20.02131", or range, e.g., "18.8 19.4". You can filter the columns by entering values and then click the "Filter" button.'

        if request.args.get('magpsf'):
            magpsf_input = extract_numbers(request.args.get('magpsf'))
            if magpsf_input != None:
                query = extract_float_filter(magpsf_input, Ztf.ant_mag_corrected, query, decimals=3)
            else:
                filter_warning_message += 'ant_mag_corrected filter cannot be applied - Enter a valid number, e.g., "18.84", or range, e.g., "18.8 19.4". You can filter the columns by entering values and then click the "Filter" button.'

        #Sort order by date (still sorts by mjd column)
        if request.args.get('sort__date'):
            sort__date_order = request.args.get('sort__date')
            if sort__date_order == 'desc':
                query = query.order_by(Ztf.date_alert_mjd.desc())
            if sort__date_order == 'asc':
                query = query.order_by(Ztf.date_alert_mjd.asc())
        else:
            query = query.order_by(Ztf.date_alert_mjd.desc()) #default sort order

        #Sort order by candid
        if request.args.get('sort__candid'):
            sort__candid_order = request.args.get('sort__candid')
            if sort__candid_order == 'desc':
                query = query.order_by(Ztf.alert_id.desc())
            if sort__candid_order == 'asc':
                query = query.order_by(Ztf.alert_id.asc())
        
        #Sort order by objectId
        if request.args.get('sort__objectId'):
            sort__objectId_order = request.args.get('sort__objectId')
            if sort__objectId_order == 'desc':
                query = query.order_by(Ztf.ztf_object_id.desc())
            if sort__objectId_order == 'asc':
                query = query.order_by(Ztf.ztf_object_id.asc())

        #Sort order by jd
        #if request.args.get('sort__jd'):
            #sort__jd_order = request.args.get('sort__jd')
            #if sort__jd_order == 'desc':
                #query = query.order_by(Ztf.jd.desc())
            #if sort__jd_order == 'asc':
                #query = query.order_by(Ztf.jd.asc())

        #Sort order by ra
        if request.args.get('sort__ra'):
            sort__ra_order = request.args.get('sort__ra')
            if sort__ra_order == 'desc':
                query = query.order_by(Ztf.locus_ra.desc())
            if sort__ra_order == 'asc':
                query = query.order_by(Ztf.locus_ra.asc())

        #Sort order by dec
        if request.args.get('sort__dec'):
            sort__dec_order = request.args.get('sort__dec')
            if sort__dec_order == 'desc':
                query = query.order_by(Ztf.locus_dec.desc())
            if sort__dec_order == 'asc':
                query = query.order_by(Ztf.locus_dec.asc())

        ##Sort order by magpsf
        #if request.args.get('sort__magpsf'):
            #sort__magpsf_order = request.args.get('sort__magpsf')
            #if sort__magpsf_order == 'desc':
                #query = query.order_by(Ztf.magpsf.desc())
            #if sort__magpsf_order == 'asc':
                #query = query.order_by(Ztf.magpsf.asc())


        #latest = db.session.query(Ztf).order_by(Ztf.jd.desc()).first() # ? IDEA: show latest update date
        query = query.options(db.load_only(Ztf.alert_id, Ztf.ztf_object_id, Ztf.date_alert_mjd, Ztf.ant_passband, Ztf.locus_id, Ztf.locus_ra, Ztf.locus_dec, Ztf.ant_mag_corrected))
        #print(query.statement.compile(compile_kwargs={"literal_binds": True})) #DEBUG: print the resulting SQL query
        paginator = query.paginate(page=page, per_page=100, error_out=True)

        #pdb.set_trace()
        # response = {
        #     'has_next': paginator.has_next,
        #     'has_prev': paginator.has_prev,
        #     'results': Alert.serialize_list(paginator.items)
        # }
        #pdb.set_trace()
        #print(request.query_string.decode('ascii'))
        #print(re.sub('[&?]page=\\d+', '', request.query_string.decode('ascii')))

        site_names = EarthLocation.get_site_names() #c locacions are only retrieved once, then internally cached bt astropy
        current_date = Time.now().datetime.date()

    return render_template(
        "main.html",
        total_queries=paginator.total,
        table=paginator.items,
        page=paginator.page,
        has_next=paginator.has_next,
        last_page=paginator.pages,
        # ? TODO Pagination query-string re.sub may leave a trailing & in edge cases. TEST
        query_string=re.sub('[&?]?page=\\d+|&$', '', request.query_string.decode('ascii')), # ? b'' binary string 
        filter_warning = filter_warning_message,
        observatories = site_names,
        today_utc = current_date,
        bokeh_version = bokeh_version
    )

@main_blueprint.route('/help', methods=['GET'])
def help():
    return render_template(
        "help.html"
    )

@main_blueprint.route('/contact', methods=['GET'])
def contact():
    return render_template(
        "contact.html"
    )

@main_blueprint.route('/profile')
@login_required
def profile():
    """Show user profile and their favorites."""
    try:
        favs = [f.locus_id for f in Favorite.query.filter_by(user_id=current_user.id).order_by(Favorite.created_at.desc()).all()]
    except Exception:
        favs = []
    return render_template(
        'profile.html', name=current_user.name, favorites=favs
    )


# ----- Simple favorites API (Favorite persistence) -----
@main_blueprint.route('/api/favorite', methods=['GET'])
def api_favorite_get():
    """
    GET /api/favorite?locusId=...  -> returns {"fav": true/false}
    """
    locus_id = request.args.get('locusId')

    if current_user.is_authenticated: 
        if locus_id:
            fav = Favorite.query.filter_by(user_id=current_user.id, locus_id=locus_id).first()
            return jsonify({'fav': fav is not None})
    return jsonify({'fav': False})

@main_blueprint.route('/api/favorites', methods=['GET'])
def api_favorites_get():
    """GET /api/favorites?groupId=<id> -> return favorites with IDs, optionally filtered by group."""

    if not current_user.is_authenticated:
        return jsonify({'favorites': []})

    group_id = request.args.get('groupId', type=int)
    query = Favorite.query.filter_by(user_id=current_user.id)
    
    if group_id is not None:
        query = query.filter_by(group_id=group_id)
    else:
        # if groupId=null explicitly, show only ungrouped
        if 'groupId' in request.args and request.args.get('groupId') == 'null':
            query = query.filter_by(group_id=None)

        #if 'groupId' in request.args and request.args.get('groupId') == 'null':
        #    query = query.filter_by(group_id=None)
    
    favs = [{'id': r.id, 'locusId': r.locus_id} for r in query.all()]
    return jsonify({'favorites': favs})


@main_blueprint.route('/api/favorite', methods=['POST'])
def api_favorite_post():
    """
    POST /api/favorite
    JSON body: { "locusId": "...", "fav": true, "groupId": null }
    """
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({'status': 'Invalid or missing JSON body'}), 400
    locus_id = data.get('locusId')
    if not locus_id:
        return jsonify({'status': 'Missing locusId'}), 400
    
    fav_flag = bool(data.get('fav'))
    group_id = data.get('groupId')  # can be null (ungrouped) or a group id

    if current_user.is_authenticated:
        fav = Favorite.query.filter_by(user_id=current_user.id, locus_id=locus_id).first()
        if fav_flag:
            if not fav:
                fav = Favorite(user_id=current_user.id, locus_id=locus_id, group_id=group_id)
                db.session.add(fav)
            else: # update group if specified
                fav.group_id = group_id
            db.session.commit()
        else:
            if fav:
                db.session.delete(fav)
                db.session.commit() 
        # NOTE: If this endpoint is called in a loop or batch, consider batching commits for efficiency.
        return jsonify({'status': 'ok'})
    else:
        return jsonify({'status': 'authentication required'}), 401
    
@main_blueprint.route('/api/favorite/<int:favorite_id>/group', methods=['PATCH'])
def api_favorite_update_group(favorite_id):
    """PATCH /api/favorite/<id>/group -> move favorite to a group. Body: {"groupId": <id> or null}"""
    if not current_user.is_authenticated:
        return jsonify({'error': 'authentication required'}), 401
    
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({'error': 'Invalid JSON'}), 400
    
    fav = Favorite.query.filter_by(id=favorite_id, user_id=current_user.id).first()
    if not fav:
        return jsonify({'error': 'favorite not found'}), 404
    
    group_id = data.get('groupId')
    # Validate group_id if it's not null
    if group_id is not None:
        group = FavoriteGroup.query.filter_by(id=group_id, user_id=current_user.id).first()
        if not group:
            return jsonify({'error': 'group not found'}), 404
    
    fav.group_id = group_id
    db.session.commit()
    
    return jsonify({'status': 'ok', 'groupId': group_id})


@main_blueprint.route('/api/favorite-groups', methods=['GET'])
def api_favorite_groups_get():
    """GET /api/favorite-groups -> return all user's groups with favorites counts."""
    if not current_user.is_authenticated:
        return jsonify({'groups': []}), 401
    
    groups = FavoriteGroup.query.filter_by(user_id=current_user.id).order_by(FavoriteGroup.name).all()
    result = [
        {
            'id': g.id,
            'name': g.name,
            'count': Favorite.query.filter_by(group_id=g.id).count()
        }
        for g in groups
    ]
    # also include ungrouped count
    ungrouped_count = Favorite.query.filter_by(user_id=current_user.id, group_id=None).count()
    result.insert(0, {'id': None, 'name': 'Ungrouped', 'count': ungrouped_count})
    
    return jsonify({'groups': result})


@main_blueprint.route('/api/favorite-groups', methods=['POST'])
def api_favorite_groups_post():
    """POST /api/favorite-groups -> create a new group. Body: {"name": "Group A"}"""
    if not current_user.is_authenticated:
        return jsonify({'error': 'authentication required'}), 401
    
    data = request.get_json(silent=True)
    if not data or not data.get('name'):
        return jsonify({'error': 'name required'}), 400
    
    name = data['name'].strip()
    if not name:
        return jsonify({'error': 'name cannot be empty'}), 400
    
    # check if group already exists for this user
    existing = FavoriteGroup.query.filter_by(user_id=current_user.id, name=name).first()
    if existing:
        return jsonify({'error': 'group already exists'}), 409
    
    group = FavoriteGroup(user_id=current_user.id, name=name)
    db.session.add(group)
    db.session.commit()
    
    return jsonify({'id': group.id, 'name': group.name}), 201


@main_blueprint.route('/api/favorite-groups/<int:group_id>', methods=['DELETE'])
def api_favorite_groups_delete(group_id):
    """DELETE /api/favorite-groups/<id> -> delete a group (orphans its favorites)."""
    if not current_user.is_authenticated:
        return jsonify({'error': 'authentication required'}), 401
    
    group = FavoriteGroup.query.filter_by(id=group_id, user_id=current_user.id).first()
    if not group:
        return jsonify({'error': 'group not found'}), 404
    
    #Update favorites that belonged to this group to have group_id = null (ungrouped) instead of deleting them, so they are not lost and can be re-assigned to other groups by the user if desired.
    Favorite.query.filter_by(group_id=group_id).update({'group_id': None})
    db.session.delete(group)
    db.session.commit()
    
    return jsonify({'status': 'ok'})


@main_blueprint.route('/query_lightcurve_data', methods=['GET'])
def query_lightcurve_data():
    locusId = request.args.get('locusId')
    if not locusId:
        return Response('Missing locusId', status=400)

    #query where locus id equals selected id
    lightcurve_query = db.session.query(Ztf)
    lightcurve_query = lightcurve_query.filter(Ztf.locus_id == locusId)
    lightcurve_query = lightcurve_query.options(db.load_only(Ztf.date_alert_mjd, Ztf.ant_mag_corrected, Ztf.ant_passband))
    data = lightcurve_query.all()

    # Creating Plot Figure
    p = figure(height=350, sizing_mode="stretch_width") 
    p.xaxis.axis_label = 'date_alert_mjd'
    p.yaxis.axis_label = 'ant_mag_corrected'
    
    # Defining Plot to be a Scatter Plot
    x_coords = []
    y_coords = []
    #prepare x and y coordinates of rows matched by locus_id of the g band
    for row in (item for item in data if item.ant_passband == 'i'):
        if (row.date_alert_mjd != None and row.ant_mag_corrected != None):
            x_coords += [row.date_alert_mjd]
            y_coords += [row.ant_mag_corrected]
    p.scatter(
        # [i for i in range(10)],
        # [random.randint(1, 50) for j in range(10)],
        x_coords,
        y_coords,
        size=5,
        color="darkkhaki",
        alpha=0.5
    )
    x_coords = []
    y_coords = []
    #prepare x and y coordinates of rows matched by locus_id of the g band
    for row in (item for item in data if item.ant_passband == 'R'):
        if (row.date_alert_mjd != None and row.ant_mag_corrected != None):
            x_coords += [row.date_alert_mjd]
            y_coords += [row.ant_mag_corrected]
    p.scatter(
        # [i for i in range(10)],
        # [random.randint(1, 50) for j in range(10)],
        x_coords,
        y_coords,
        size=5,
        color="indianred",
        alpha=0.5
    )
    x_coords = []
    y_coords = []
    #prepare x and y coordinates of rows matched by locus_id of the g band
    for row in (item for item in data if item.ant_passband == 'g'):
        if (row.date_alert_mjd != None and row.ant_mag_corrected != None):
            x_coords += [row.date_alert_mjd]
            y_coords += [row.ant_mag_corrected]
    p.scatter(
        # [i for i in range(10)],
        # [random.randint(1, 50) for j in range(10)],
        x_coords,
        y_coords,
        size=5,
        color="limegreen",
        alpha=0.5
    )
 
    # Get lightcurve Chart Components
    script, div = components(p)
 
    # Return the components to the HTML template
    return f'{ div }{ script }'
    # NOTE: Make sure a matching bokeh js version is loaded in header of the displaying page, e.g. 
    # <script src="https://cdn.bokeh.org/bokeh/release/bokeh-3.6.1.min.js"></script>

@main_blueprint.route("/locus_plot_csv")
def get_locus_plot():
    locusId = request.args.get('locusId')
    if not locusId:
        return Response('Missing locusId', status=400)
    
    lightcurve_query = db.session.query(Ztf)
    lightcurve_query = lightcurve_query.filter(Ztf.locus_id == locusId)
    lightcurve_query = lightcurve_query.options(db.load_only(Ztf.locus_id, Ztf.date_alert_mjd, Ztf.ant_mag_corrected))
    data = lightcurve_query.all()
    csv = 'locus_id,date_alert_mjd,ant_mag_corrected\n'

    #prepare x and y coordinates of rows matched by locus_id
    for row in data:
        if (row.date_alert_mjd != None and row.ant_mag_corrected != None):
            csv += f'{row.locus_id},{row.date_alert_mjd},{row.ant_mag_corrected}\n'
    return Response(
        csv,
        mimetype="text/csv",
        headers={"Content-disposition":
                 "attachment; filename=myplot.csv"})


#NOTE: Features are filter by objectId & candid
@main_blueprint.route('/query_features', methods=['GET'])
def query_features():
    alert_id = request.args.get('alert_id')
    if not alert_id:
        return Response('Missing alert_id', status=400)

    feature_query = db.session.query(Ztf)
    feature_query = feature_query.filter(Ztf.alert_id == alert_id)
    feature_query = feature_query.options(db.load_only(Ztf.feature_amplitude_magn_r,
        Ztf.feature_anderson_darling_normal_magn_r,
        Ztf.feature_beyond_1_std_magn_r,
        Ztf.feature_beyond_2_std_magn_r,
        Ztf.feature_cusum_magn_r,
        Ztf.feature_eta_e_magn_r,
        Ztf.feature_inter_percentile_range_2_magn_r,
        Ztf.feature_inter_percentile_range_10_magn_r,
        Ztf.feature_inter_percentile_range_25_magn_r,
        Ztf.feature_kurtosis_magn_r,
        Ztf.feature_linear_fit_slope_magn_r,
        Ztf.feature_linear_fit_slope_sigma_magn_r,
        Ztf.feature_linear_fit_reduced_chi2_magn_r,
        Ztf.feature_linear_trend_magn_r,
        Ztf.feature_linear_trend_sigma_magn_r,
        Ztf.feature_magnitude_percentage_ratio_40_5_magn_r,
        Ztf.feature_magnitude_percentage_ratio_20_5_magn_r,
        Ztf.feature_maximum_slope_magn_r,
        Ztf.feature_mean_magn_r,
        Ztf.feature_median_absolute_deviation_magn_r,
        Ztf.feature_percent_amplitude_magn_r,
        Ztf.feature_percent_difference_magnitude_percentile_5_magn_r,
        Ztf.feature_percent_difference_magnitude_percentile_10_magn_r,
        Ztf.feature_median_buffer_range_percentage_10_magn_r,
        Ztf.feature_median_buffer_range_percentage_20_magn_r,
        Ztf.feature_period_0_magn_r,
        Ztf.feature_period_s_to_n_0_magn_r,
        Ztf.feature_period_1_magn_r,
        Ztf.feature_period_s_to_n_1_magn_r,
        Ztf.feature_period_2_magn_r,
        Ztf.feature_period_s_to_n_2_magn_r,
        Ztf.feature_period_3_magn_r,
        Ztf.feature_period_s_to_n_3_magn_r,
        Ztf.feature_period_4_magn_r,
        Ztf.feature_period_s_to_n_4_magn_r,
        Ztf.feature_periodogram_amplitude_magn_r,
        Ztf.feature_periodogram_beyond_2_std_magn_r,
        Ztf.feature_periodogram_beyond_3_std_magn_r,
        Ztf.feature_periodogram_standard_deviation_magn_r,
        Ztf.feature_chi2_magn_r,
        Ztf.feature_skew_magn_r,
        Ztf.feature_standard_deviation_magn_r,
        Ztf.feature_stetson_k_magn_r,
        Ztf.feature_weighted_mean_magn_r,
        Ztf.feature_anderson_darling_normal_flux_r,
        Ztf.feature_cusum_flux_r,
        Ztf.feature_eta_e_flux_r,
        Ztf.feature_excess_variance_flux_r,
        Ztf.feature_kurtosis_flux_r,
        Ztf.feature_mean_variance_flux_r,
        Ztf.feature_chi2_flux_r,
        Ztf.feature_skew_flux_r,
        Ztf.feature_stetson_k_flux_r,
        Ztf.feature_amplitude_magn_g,
        Ztf.feature_anderson_darling_normal_magn_g,
        Ztf.feature_beyond_1_std_magn_g,
        Ztf.feature_beyond_2_std_magn_g,
        Ztf.feature_cusum_magn_g,
        Ztf.feature_eta_e_magn_g,
        Ztf.feature_inter_percentile_range_2_magn_g,
        Ztf.feature_inter_percentile_range_10_magn_g,
        Ztf.feature_inter_percentile_range_25_magn_g,
        Ztf.feature_kurtosis_magn_g,
        Ztf.feature_linear_fit_slope_magn_g,
        Ztf.feature_linear_fit_slope_sigma_magn_g,
        Ztf.feature_linear_fit_reduced_chi2_magn_g,
        Ztf.feature_linear_trend_magn_g,
        Ztf.feature_linear_trend_sigma_magn_g,
        Ztf.feature_magnitude_percentage_ratio_40_5_magn_g,
        Ztf.feature_magnitude_percentage_ratio_20_5_magn_g,
        Ztf.feature_maximum_slope_magn_g,
        Ztf.feature_mean_magn_g,
        Ztf.feature_median_absolute_deviation_magn_g,
        Ztf.feature_percent_amplitude_magn_g,
        Ztf.feature_percent_difference_magnitude_percentile_5_magn_g,
        Ztf.feature_percent_difference_magnitude_percentile_10_magn_g,
        Ztf.feature_median_buffer_range_percentage_10_magn_g,
        Ztf.feature_median_buffer_range_percentage_20_magn_g,
        Ztf.feature_period_0_magn_g,
        Ztf.feature_period_s_to_n_0_magn_g,
        Ztf.feature_period_1_magn_g,
        Ztf.feature_period_s_to_n_1_magn_g,
        Ztf.feature_period_2_magn_g,
        Ztf.feature_period_s_to_n_2_magn_g,
        Ztf.feature_period_3_magn_g,
        Ztf.feature_period_s_to_n_3_magn_g,
        Ztf.feature_period_4_magn_g,
        Ztf.feature_period_s_to_n_4_magn_g,
        Ztf.feature_periodogram_amplitude_magn_g,
        Ztf.feature_periodogram_beyond_2_std_magn_g,
        Ztf.feature_periodogram_beyond_3_std_magn_g,
        Ztf.feature_periodogram_standard_deviation_magn_g,
        Ztf.feature_chi2_magn_g,
        Ztf.feature_skew_magn_g,
        Ztf.feature_standard_deviation_magn_g,
        Ztf.feature_stetson_k_magn_g,
        Ztf.feature_weighted_mean_magn_g,
        Ztf.feature_anderson_darling_normal_flux_g,
        Ztf.feature_cusum_flux_g,
        Ztf.feature_eta_e_flux_g,
        Ztf.feature_excess_variance_flux_g,
        Ztf.feature_kurtosis_flux_g,
        Ztf.feature_mean_variance_flux_g,
        Ztf.feature_chi2_flux_g,
        Ztf.feature_skew_flux_g,
        Ztf.feature_stetson_k_flux_g))
    row = feature_query.first()
    if row is None:
        return Response(jsonify({'error': 'No feature record found for alert_id'}), status=404)
    data = object_as_dict(row)
    
    response = current_app.response_class(
        response=json.dumps(data), 
        status=200,
        mimetype='application/json'
    )
    return response


@main_blueprint.route('/query_featureplot_data', methods=['GET'])
def query_featureplot_data():
    locusId = request.args.get('locusId')
    if not locusId:
        return Response('Missing locusId', status=400)
    selected_features = request.args.get('features') 
    #default selected feature list
    feature_list = ['feature_amplitude_magn_r',
        'feature_anderson_darling_normal_magn_r',
        'feature_beyond_1_std_magn_r',
        'feature_beyond_2_std_magn_r',
        'feature_cusum_magn_r']

    #Features from args.get() - if none: select defaults
    if selected_features:
        features_array = [f.strip() for f in selected_features.split(',') if f.strip()]
        if features_array and len(features_array) > 0:
            feature_list = features_array[:10] # limit to 10 features for plotting

    #query where locus id equals selected id - build column list dynamically
    columns_to_load = [Ztf.date_alert_mjd, Ztf.ant_mag_corrected] + [getattr(Ztf, feature) for feature in feature_list]
    featureplot_query = db.session.query(Ztf)
    featureplot_query = featureplot_query.filter(Ztf.locus_id == locusId)
    featureplot_query = featureplot_query.options(db.load_only(*columns_to_load))
    data = featureplot_query.all()

    # Creating Plot Figure
    p = figure(height=350, sizing_mode="stretch_width") 
    p.xaxis.axis_label = 'date_alert_mjd'
    p.yaxis.axis_label = 'features'
    # Defining Plot to be a Scatter Plot
    x_coords = []
    y_coords = []

    #10! colors
    data_colors = [
        "#1f77b4",  # blue
        "#ff7f0e",  # orange
        "#2ca02c",  # green
        "#d62728",  # red
        "#9467bd",  # purple
        "#8c564b",  # brown
        "#e377c2",  # pink
        "#7f7f7f",  # gray
        "#bcbd22",  # yellow-green
        "#17becf"   # cyan
    ]
    #prepare x and y coordinates of rows matched by locus_id
    legend_it = []
    for index, feature in enumerate(feature_list):
        x_coords = []
        y_coords = []
        for row in data:
            #NOTE: Only if we have a date anda  magnitude value for the row, we can work with a feature value
            if (row.date_alert_mjd is not None and row.ant_mag_corrected is not None): 
                value = getattr(row, feature, None)
                if value is not None:
                    x_coords += [row.date_alert_mjd]
                    y_coords += [value]

        pc = p.scatter(
            x_coords,
            y_coords,
            size=5,
            color=data_colors[index],
            alpha=0.5
            #,legend_label=feature
        )
        legend_it.append((feature, [pc]))

    legend = Legend(items=legend_it)  #, glyph_height=9, glyph_width=9)
    #legend.click_policy="mute"
    
    # Increasing the glyph height
    # p.legend.glyph_height = 5    
    # # increasing the glyph width
    # p.legend.glyph_width = 5   
    # # Increasing the glyph's label height
    # p.legend.label_height = 5 
    # # Increasing the glyph's label height
    # p.legend.text_font_size = '6px'
    p.add_layout(legend, 'right')
    p.legend.label_text_font_size = '9px'
    p.legend.glyph_width = 12
 
    # Get Features Chart Components
    script, div = components(p)
    #print(div)
 
    # Return the components to the HTML template
    return f'{ div }{ script }'

@main_blueprint.route('/query_crossmatches', methods=['GET'])
def query_crossmatches():
    """Query crossmatches for a given locus id."""
    locusId = request.args.get('locusId') # ex: locusname="ANT2018fywy2"
    if not locusId:
        return Response('Missing locusId', status=400)

    try:
        #query all Crossmatches records from DB where locus id equals given id
        crossmatches_query = db.session.query(Crossmatches)
        crossmatches_query = crossmatches_query.filter(Crossmatches.locus_id == locusId)
        crossmatches_list = result_to_dict(crossmatches_query.all())

        response = current_app.response_class(
            response=safe_serialize(crossmatches_list), #TODO? array[] with single row when using BootstrapTable?
            status=200,
            mimetype='application/json'
        )
        return response
    except Exception as e:
        logging.error(f"Error querying crossmatches: {e}", exc_info=True)
        return Response(f"{e}", status=500) # Internal Server Error

# Register Jinja filters
@main_blueprint.app_template_filter('astro_filter')
def astro_filter(str):
    if (str == "g"):
        return "g"
    elif (str == "R"):
        return "R"
    elif (str == "i"):
        return "i"
    else:
        return ""

@main_blueprint.app_template_filter('mag_filter')
def mag_filter(num):
    if num: 
        return round(num,3)
    # else:
    #     return ''

@main_blueprint.app_template_filter('format_mjd_readable')
def format_mjd_readable(value):
    if value is None:
        return ''
    
    try:
        mjd_value = float(value)
        return _format_mjd_cached(mjd_value) # Use Astropy for accurate conversion
    except (TypeError, ValueError, OverflowError):
        return ''