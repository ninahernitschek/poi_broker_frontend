from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user

import matplotlib 
matplotlib.use('agg') # OR 'SVG'
import matplotlib.pyplot as plt
from matplotlib import dates
import io
import base64
import math
from functools import lru_cache

import numpy as np
from astropy.visualization import astropy_mpl_style, quantity_support
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body
from astropy.time import Time
import astropy.units as u
from astropy.wcs import WCS

#from astroquery.skyview import SkyView

from timezonefinder import TimezoneFinder
from datetime import datetime
from zoneinfo import ZoneInfo
import logging

from . import limiter
from .models import UserObservatory

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_timezone_finder() -> TimezoneFinder:
    """Shared TimezoneFinder instance.

    Construction loads all boundary polygon data (~0.5s), so it must not run
    per request. The instance is read-only after init (timezonefinder 8.x) and
    safe to share across threads.
    """
    return TimezoneFinder()

observing_tool_blueprint = Blueprint('observing_tool', __name__) 

# IDEA: Perhaps add a URL prefix observing_tool/
# @observing_tool.route('/observatories')
# def show():
#     site_names = EarthLocation.get_site_names()

def _resolve_zoneinfo(timezone_name):
    """Return ZoneInfo instance for a timezone string, tolerating minor formatting drift."""
    if not isinstance(timezone_name, str):
        return None

    raw = timezone_name.strip()
    if not raw:
        return None

    candidates = [raw]

    # Some values are stored/displayed with a label suffix, e.g. "Europe/Berlin (UTC+2)".
    if '(' in raw:
        base = raw.split('(', 1)[0].strip()
        if base:
            candidates.append(base)

    normalized = raw.replace('\\', '/').replace(' ', '_')
    if normalized not in candidates:
        candidates.append(normalized)

    seen = set()
    attempted_candidates = []
    last_error = None
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        attempted_candidates.append(candidate)
        try:
            return ZoneInfo(candidate)
        except Exception as exc:
            last_error = f'{type(exc).__name__}: {exc}'
            continue

    logger.warning(
        'Failed to resolve timezone via ZoneInfo: input=%r candidates=%s last_error=%s',
        raw,
        attempted_candidates,
        last_error,
    )

    return None


@observing_tool_blueprint.route('/query_observing_plot')
@limiter.limit(lambda: current_app.config.get('READ_RATE_LIMIT_LAX', '30 per minute'))
def calc_observing_plot():
    try:
        # Parse query parameters
        # Built-in site name or an authenticated user's saved custom:<id>.
        obs_loc = request.args.get('obs_loc')
        obs_date = request.args.get('obs_date')
        obs_tz = request.args.get('obs_tz')
        ra_value = request.args.get('ra')
        dec_value = request.args.get('dec')

        if not obs_loc or not obs_date or ra_value is None or dec_value is None:
            return jsonify({'error': 'Missing required query parameters: obs_loc, obs_date, ra, dec'}), 400

        try:
            parsed_date = datetime.strptime(obs_date, '%Y-%m-%d')
            year = f'{parsed_date.year:04d}'
            month = f'{parsed_date.month:02d}'
            day = f'{parsed_date.day:02d}'
            ra = float(ra_value)
            dec = float(dec_value)
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid parameter format. Expected obs_date=YYYY-MM-DD and numeric ra/dec.'}), 400

        # astropy neither bounds RA (it silently wraps) nor rejects NaN; dec
        # out of range raises a ValueError much later inside SkyCoord and 500s
        # the request. Validate here, at the same gate as the format check.
        if (
            not math.isfinite(ra)
            or not math.isfinite(dec)
            or not (0.0 <= ra < 360.0)
            or not (-90.0 <= dec <= 90.0)
        ):
            return jsonify({'error': 'Invalid parameter value. RA must be within [0, 360) and Dec within [-90, 90] degrees.'}), 400

        if obs_loc.startswith('custom:'):
            if not current_user.is_authenticated:
                return jsonify({'error': 'Authentication required for custom observatories.'}), 401

            custom_id = obs_loc.split(':', 1)[1].strip()
            if not custom_id.isdigit():
                return jsonify({'error': 'Invalid custom observatory identifier.'}), 400

            custom_row = UserObservatory.query.filter_by(
                id=int(custom_id),
                user_id=current_user.id,
            ).first()
            if custom_row is None:
                return jsonify({'error': 'Unknown custom observatory.'}), 400

            observatory = EarthLocation(
                lat=custom_row.latitude * u.deg,
                lon=custom_row.longitude * u.deg,
            )
            obs_lat = custom_row.latitude
            obs_lon = custom_row.longitude
            tz = _resolve_zoneinfo(custom_row.timezone_name)
            if tz is None:
                # Fallback by coordinates so malformed legacy values do not break observing plots.
                fallback_timezone_name = _get_timezone_finder().timezone_at(lng=obs_lon, lat=obs_lat)
                tz = _resolve_zoneinfo(fallback_timezone_name)
                if tz is None:
                    logger.warning('Invalid timezone for custom observatory id=%s', custom_row.id)
                    return jsonify({'error': 'Custom observatory timezone is invalid.'}), 400
                logger.warning(
                    'Recovered invalid timezone for custom observatory id=%s using coords fallback: %s -> %s',
                    custom_row.id,
                    custom_row.timezone_name,
                    fallback_timezone_name,
                )
        else:
            site_name = obs_loc.split(':', 1)[1].strip() if obs_loc.startswith('builtin:') else obs_loc
            # Observatory location
            try:
                observatory = EarthLocation.of_site(site_name)
            except Exception:
                logger.warning('Unknown observatory location: %s', site_name)
                return jsonify({'error': 'Unknown observatory location.'}), 400

            obs_lon, obs_lat = observatory.lon.value, observatory.lat.value
            timezone_name = _get_timezone_finder().timezone_at(lng=obs_lon, lat=obs_lat)
            if timezone_name is None:
                return jsonify({'error': 'Failed to determine timezone for selected observatory.'}), 400
            tz = _resolve_zoneinfo(timezone_name)
            if tz is None:
                logger.warning('Invalid timezone for built-in observatory %s (%s)', site_name, timezone_name)
                return jsonify({'error': 'Failed to determine timezone for selected observatory.'}), 400

        # Never-rises: max altitude is 90 - |lat - dec|. One-sided (lat - dec)
        # only catches southern objects from northern sites.
        if abs(obs_lat - dec) >= 90:
            return jsonify({
                'message': (
                    f'Object is not visible from your location: declination = {dec} degree, '
                    f'observatory latitude {obs_lat} degree'
                )
            })

        stellar_object = SkyCoord(ra=ra * u.deg, dec=dec * u.deg) #e.g. SkyCoord(ra=101.28715533*u.deg, dec=16.71611586*u.deg)

        # Time settings
        midnight_utc = Time(f'{year}-{month}-{day} 00:00:00', scale='utc')
        midnight_zone = midnight_utc.to_datetime(timezone=tz)
        delta_midnight = np.linspace(-12, 12, 1000) * u.hour
        times_range_utc = midnight_utc + delta_midnight
        times_range_zone = midnight_utc + delta_midnight + (tz.utcoffset(midnight_zone).total_seconds() / (60 * 60)) * u.hour

        frame = AltAz(obstime=times_range_utc, location=observatory)
        object_altazs = stellar_object.transform_to(frame)

        moon = get_body('moon', times_range_utc, location=observatory)
        moon_altazs = moon.transform_to(frame)
        moon_alt = moon_altazs.alt.value

        sun = get_body('sun', times_range_utc, location=observatory)
        sun_altazs = sun.transform_to(frame)
        sun_alt = sun_altazs.alt.value

        def observing_plot():
            plt.style.use(astropy_mpl_style)
            plt.figure(figsize=(8, 6.5))
            quantity_support()
            ax = plt.gca()

            if obs_tz == 'option_utc':
                timetoplot = times_range_utc
                ax.set_xlabel("Time starting {0} [UTC]".format(min(timetoplot).datetime.date()))
            else:
                timetoplot = times_range_zone
                utcoffset = tz.utcoffset(midnight_zone).total_seconds() / (60 * 60)
                ax.set_xlabel("Time starting {0} [{1}, UTC{2}]".format(min(timetoplot).datetime.date(), tz, f'{utcoffset:+.0f}'))

            # Format the time axis
            xlo, xhi = (timetoplot[0]), (timetoplot[-1])
            ax.set_xlim([xlo.plot_date, xhi.plot_date])
            date_formatter = dates.DateFormatter('%H:%M')
            ax.xaxis.set_major_formatter(date_formatter)
            plt.setp(ax.get_xticklabels(), rotation=30, ha='right')

            plt.fill_between(
                timetoplot.datetime,
                0 * u.deg,
                90 * u.deg,
                sun_altazs.alt < -0 * u.deg,
                color='0.5',
                zorder=0,
            )

            plt.fill_between(
                timetoplot.datetime,
                0 * u.deg,
                90 * u.deg,
                sun_altazs.alt < -18 * u.deg,
                color='k',
                zorder=0,
            )

            plt.plot(timetoplot.datetime, moon_altazs.alt.value, c='lightblue', label='moon')
            plt.plot(timetoplot.datetime, object_altazs.alt.value, c='orange', label='object')

            plt.legend(bbox_to_anchor=(1.0, 1.1), ncol=2)

            plt.ylabel('Altitude [deg]')
            ax.set_ylim(0, 90)
            airmass_ticks = np.array([1, 2, 3])
            altitude_ticks = 90 - np.degrees(np.arccos(1 / airmass_ticks))

            ax2 = ax.twinx()
            ax2.set_yticks(altitude_ticks)
            ax2.set_yticklabels(airmass_ticks)
            ax2.set_ylim(ax.get_ylim())
            ax2.set_ylabel('Airmass')
            plt.grid(color='grey', linestyle='--', linewidth=0.5)
            ax2.grid(None)

            # Create an in-memory buffer
            img_io = io.BytesIO()
            plt.savefig(img_io, format='png')
            img_io.seek(0)

            img_data = base64.b64encode(img_io.getvalue()).decode('utf-8')
            img_obs = f"data:image/png;base64,{img_data}"
            plt.close()
            return img_obs

        # 1: Create observing plot
        obs_img = observing_plot()

        # 2: Create moon panel. Moon-down is a plain label (moonMessage), not HTML.
        night_moon_alt = moon_alt[np.where(sun_alt < 0)]
        if night_moon_alt.size == 0:
            return jsonify({'image': obs_img, 'moonMessage': 'No nighttime hours on this date'})
        if np.max(night_moon_alt) < 0:
            return jsonify({'image': obs_img, 'moonMessage': 'Moon down'})

        moon_separation = moon.separation(stellar_object, origin_mismatch='ignore')
        moon_panel = get_moon_phase_panel(observatory, midnight_utc, moon_separation)
        return jsonify({'image': obs_img, 'moonHtml': moon_panel})
    except Exception:
        logger.exception('Unhandled error while generating observing plot')
        return jsonify({'error': 'Internal server error while generating observing plot. Check server logs for details.'}), 500

def get_moon_phase_panel(observatory, midnight_utc, moon_separation):
    #angle of the tilt of the Moon will be different as seen from different latitudes. 
    #https://astronomy.stackexchange.com/questions/24711/how-does-the-moon-look-like-from-different-latitudes-of-the-earth
    #Calculate lunar orbital phase in radians.

    sun_midnight = get_body("sun", midnight_utc, location=observatory)
    moon_midnight = get_body("moon", midnight_utc, location=observatory)

    elongation = sun_midnight.separation(moon_midnight)
    moon_phase_angle_inc = np.arctan2(sun_midnight.distance*np.sin(elongation),
                moon_midnight.distance - sun_midnight.distance*np.cos(elongation))

    fraction_illuminated = (1 + np.cos(moon_phase_angle_inc))/2.0
    fraction_illuminated_percentage = "{:.0%}".format(fraction_illuminated)
    angle = np.arctan2(np.cos(sun_midnight.dec) * np.sin(sun_midnight.ra - moon_midnight.ra), np.sin(sun_midnight.dec) * np.cos(moon_midnight.dec) -
                np.cos(sun_midnight.dec) * np.sin(moon_midnight.dec) * np.cos(sun_midnight.ra - moon_midnight.ra)) 
    phase = 0.5 + 0.5 * moon_phase_angle_inc.value * np.sign(angle.value) / np.pi

    phase_name=''
    phase_image=''
    if(phase == 0):
        phase_name='new moon'
        phase_image = 'Moon_new.png'
    if(0 < phase < 0.25):
        phase_name='waxing crescent'
        phase_image = 'Moon_waxingcrescent.png'
    if(phase == 0.25):
        phase_name='first quarter'
        phase_image = 'Moon_firstquarter.png'
    if(0.25 < phase < 0.5):
        phase_name='waxing gibbous'
        phase_image = 'Moon_waxinggibbous.png'
    if(phase == 0.5):
        phase_name='full'
        phase_image = 'Moon_full.png'
    if(0.5 < phase < 0.75):
        phase_name='waning gibbous'
        phase_image = 'Moon_waninggibbous.png'
    if(phase == 0.75):
        phase_name='last quarter'
        phase_image = 'Moon_lastquarter.png'
    if(0.75 < phase < 1):
        phase_name='waning crescent'
        phase_image = 'Moon_waningcrescent.png'
    if(phase == 1):
        phase_name='new moon'
        phase_image = 'Moon_new.png'

    #Rotate the moon picture counterclockwise by (lat_observatory).
    rotation = observatory.lat.value

    #html = '<div>'
    html = '<span class="moon-container-square">'
    html += f'<img src="/static/img/{phase_image}" width="96" height="96" style="transform: rotate({rotation}deg);">'
    html += '</span><br>'
    html += f'Moon Phase at {midnight_utc.strftime("%Y-%m-%d %H:%M:%S")} UTC<br>' #TODO/IDEA: Should we distinguish between UTC and local time here? I think UTC is more standard for astronomy, but we could also add the local time in parentheses
    html += f'Phase: {phase_name}<br>'
    html += f'Illumination: {fraction_illuminated_percentage}<br>'
    html += f'separation from moon <br>to object during night: {np.min(moon_separation.degree):.3f} to {np.max(moon_separation.degree):.3f} degree'
    #html += '</div>'
    #TODO: tilt based on latitude, where I show it on a larger black square
    #this is how it should look like: https://astronomy.stackexchange.com/questions/24711/how-does-the-moon-look-like-from-different-latitudes-of-the-earth
    return html
