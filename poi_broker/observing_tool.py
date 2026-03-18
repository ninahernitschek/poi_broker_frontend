from flask import Blueprint, render_template, abort, request, make_response

import matplotlib 
matplotlib.use('agg') # OR 'SVG'
import matplotlib.pyplot as plt
from matplotlib import dates
import io
import base64

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

observing_tool_blueprint = Blueprint('observing_tool', __name__) 

# IDEA: Perhaps add a URL prefix observing_tool/

# @observing_tool.route('/observatories')
# def show():
#     site_names = EarthLocation.get_site_names()

@observing_tool_blueprint.route('/query_observing_plot')
def calc_observing_plot():
    
    #http://localhost:5000/query_observing_plot?obs_loc=Rubin%20Observatory&obs_date=2024-11-14&obs_tz=option_utc&ra=101.28715533&dec=16.71611586
    #/query_observing_data?obs_loc=ALMA&obs_date=2025-01-13&obs_tz=option_utc&ra=15.7574139&dec=16.215272799999994

    obs_loc = request.args.get('obs_loc') #EarthLocation.of_site('Rubin Observatory')
    obs_date = request.args.get('obs_date')
    obs_tz = request.args.get('obs_tz')
    ra_value = request.args.get('ra')
    dec_value = request.args.get('dec')

    if not obs_loc or not obs_date or ra_value is None or dec_value is None:
        return '<p>Missing required query parameters: obs_loc, obs_date, ra, dec</p>', 400

    try:
        year, month, day = obs_date.split("-")
        ra = float(ra_value)
        dec = float(dec_value)
    except (ValueError, TypeError):
        return '<p>Invalid parameter format. Expected obs_date=YYYY-MM-DD and numeric ra/dec.</p>', 400

    # Observatory location
    try:
        observatory = EarthLocation.of_site(obs_loc)
    except Exception:
        return '<p>Unknown observatory location.</p>', 400

    obs_lon, obs_lat = observatory.lon.value, observatory.lat.value
    #0. do not generate any plots if the object is not visible from the observatory
    if (obs_lat - dec >=90):
        return f"<p>Object is not visible from your location: declination = {dec} degree, observatory latitude {obs_lat} degree</p>"

    tf = TimezoneFinder()
    timezone_name = tf.timezone_at(lng=obs_lon, lat=obs_lat)
    if timezone_name is None:
        return '<p>Failed to determine timezone for selected observatory.</p>', 400
    tz = ZoneInfo(timezone_name)

    #stellar_object = SkyCoord(ra=101.28715533*u.deg, dec=16.71611586*u.deg)
    stellar_object = SkyCoord(ra=ra*u.deg, dec=dec*u.deg)

    # Time settings
    midnight_utc = Time(f'{year}-{month}-{day} 00:00:00', scale='utc') #values are already in the correct format no :02d etc needed
    #t_utc = Time(midnight_utc, format='isot', scale='utc') # ? no use
    midnight_zone = midnight_utc.to_datetime(timezone=tz)
    delta_midnight = np.linspace(-12, 12, 1000) * u.hour
    times_range_utc = midnight_utc + delta_midnight
    times_range_zone = midnight_utc + delta_midnight + (tz.utcoffset(midnight_zone).total_seconds() / (60*60)  )*u.hour #potential bug?

    frame = AltAz(obstime=times_range_utc, location=observatory)
    
    object_altazs = stellar_object.transform_to(frame)
    #object_airmass = object_altazs.secz # ? Not used

    moon = get_body("moon", times_range_utc, location=observatory)
    moon_altazs = moon.transform_to(frame)
    moon_alt = moon_altazs.alt.value

    sun = get_body("sun", times_range_utc, location=observatory)
    sun_altazs = sun.transform_to(frame)
    sun_alt = sun_altazs.alt.value

    # (Nested) Observing Plot function (to structure code better)
    def observing_plot():
        plt.style.use(astropy_mpl_style)
        plt.figure(figsize=(8,6.5))
        #plt.margins(0.01, tight=True) #The default margins are rcParams["axes.xmargin"] (default: 0.05) and rcParams["axes.ymargin"] (default: 0.05).
        quantity_support()
        ax = plt.gca()
        
        if(obs_tz == 'option_utc'):
            timetoplot = times_range_utc
            ax.set_xlabel("Time starting {0} [UTC]".format(min(timetoplot).datetime.date()))
        else:
            timetoplot = times_range_zone
            utcoffset = tz.utcoffset(midnight_zone).total_seconds() / (60*60)
            ax.set_xlabel("Time starting {0} [{1}, UTC{2}]".format(min(timetoplot).datetime.date(), tz,f'{utcoffset:+.0f}'))
        
        
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
            color="0.5",
            zorder=0,
        )

        plt.fill_between(
        timetoplot.datetime,
        0 * u.deg,
        90 * u.deg,
        sun_altazs.alt < -18 * u.deg,
        color="k",
        zorder=0,
        )
                
                
        plt.plot(
            timetoplot.datetime,
            moon_altazs.alt.value,c="lightblue", label="moon")

        plt.plot(
            timetoplot.datetime,
            object_altazs.alt.value,c="orange", label="object")

        plt.legend(bbox_to_anchor=(1.0, 1.1),ncol=2)
        
    
        plt.ylim(0, )
        plt.ylabel("Altitude [deg]")
        ax.set_ylim(0,90)
        airmass_ticks = np.array([1, 2, 3])
        altitude_ticks = 90 - np.degrees(np.arccos(1/airmass_ticks))

        ax2 = ax.twinx()
        ax2.set_yticks(altitude_ticks)
        ax2.set_yticklabels(airmass_ticks)
        ax2.set_ylim(ax.get_ylim())
        ax2.set_ylabel('Airmass')
        plt.grid(color = 'grey', linestyle = '--', linewidth = 0.5)
        #print(altitude_ticks)
        ax2.grid(None)

        #plt.show()
        # Create an in-memory buffer
        img_io = io.BytesIO()
        plt.savefig(img_io, format='png')
        img_io.seek(0)

        # Option a: Create a response with the image data
        #response = make_response(img_io.read())
        #response.headers['Content-Type'] = 'image/png'
        # Option b : Encode image to base64
        img_data = base64.b64encode(img_io.getvalue()).decode('utf-8')
        img_obs = f"data:image/png;base64,{img_data}"
        # Close plot
        plt.close()
        return img_obs

    #1: Create an observing plot
    obs_img = observing_plot()

    #2: Create a finder chart (obsolete: downloading fits files from NASA SkyView is too slow/does not work reliablely)
    #finder_img = plot_finder_image(stellar_object)

    #3: Create Moon Panel
    moon_panel = ''
    # 1st: Is the moon up at night?
    night_moon_alt = moon_alt[np.where(sun_alt<0)]
    #print(night_moon_alt)
    if(np.max(night_moon_alt) < 0):
        moon_panel = 'Moon down'
    else: 
        #Moon up
        moon_separation = moon.separation(stellar_object, origin_mismatch="ignore")
        moon_panel = get_moon_phase_panel(observatory, midnight_utc, moon_separation)

    #messier1 = FixedTarget.from_name("M1")
    # ra=101.28715533
    # dec=16.71611586
    # target = SkyCoord(ra=ra*u.deg, dec=dec*u.deg)

    #return response
    return f'''<hr><div class="row">
        <div class="col-md-7"><img src="{obs_img}"></div>
        <div class="col-md-5">{moon_panel}</div>
    </div>'''

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
    angle = np.arctan(np.cos(sun_midnight.dec) * np.sin(sun_midnight.ra - moon_midnight.ra), np.sin(sun_midnight.dec) * np.cos(moon_midnight.dec) -
                np.cos(sun_midnight.dec) * np.sin(moon_midnight.dec) * np.cos(sun_midnight.ra - moon_midnight.ra)) #technically, the 2nd parameter calls for arctan2, but we are only interested in the sign of the angle, so it does not matter if we use arctan or arctan2 here.
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

'''
def plot_finder_image(coord, survey='DSS', fov_radius=10*u.arcmin,
                      log=False, ax=None, grid=False, reticle=False,
                      style_kwargs=None, reticle_style_kwargs=None):
    """
    Plot survey image centered on ``target``.

    Survey images are retrieved from NASA Goddard's SkyView service via
    ``astroquery.skyview.SkyView``.

    If a `~matplotlib.axes.Axes` object already exists, plots the finder image
    on top. Otherwise, creates a new `~matplotlib.axes.Axes`
    object with the finder image.

    Parameters
    ----------
    target : `~astroplan.FixedTarget`, `~astropy.coordinates.SkyCoord`
        Coordinates of celestial object

    survey : string
        Name of survey to retrieve image from. For dictionary of
        available surveys, use
        ``from astroquery.skyview import SkyView; SkyView.list_surveys()``.
        Defaults to ``'DSS'``, the Digital Sky Survey.

    fov_radius : `~astropy.units.Quantity`
        Radius of field of view of retrieved image. Defaults to 10 arcmin.

    log : bool, optional
        Take the natural logarithm of the FITS image if `True`.
        False by default.

    ax : `~matplotlib.axes.Axes` or None, optional.
        The `~matplotlib.axes.Axes` object to be drawn on.
        If None, uses the current `~matplotlib.axes.Axes`.

    grid : bool, optional.
        Grid is drawn if `True`. `False` by default.

    reticle : bool, optional
        Draw reticle on the center of the FOV if `True`. Default is `False`.

    style_kwargs : dict or `None`, optional.
        A dictionary of keywords passed into `~matplotlib.pyplot.imshow`
        to set plotting styles.

    reticle_style_kwargs : dict or `None`, optional
        A dictionary of keywords passed into `~matplotlib.pyplot.axvline` and
        `~matplotlib.pyplot.axhline` to set reticle style.

    Returns
    -------
    ax : `~matplotlib.axes.Axes`
        Matplotlib axes with survey image centered on ``target``

    hdu : `~astropy.io.fits.PrimaryHDU`
        FITS HDU of the retrieved image


    Notes
    -----
    Dependencies:
        In addition to Matplotlib, this function makes use of astroquery.
    """

    #coord = target if not hasattr(target, 'coord') else target.coord
    #print(coord)
    position = coord.icrs
    coordinates = 'icrs'
    #print(position)
    #target_name = None if isinstance(target, SkyCoord) else target.name

    hdu = SkyView.get_images(position=position, coordinates=coordinates,survey=survey, radius=fov_radius)[0][0]
    # Use a multiprocessing to fetch the SkyView image with a timeout to handle the case where the request takes too long
    #hdu = fetch_skyview_with_timeout(position=position, coordinates=coordinates,survey=survey, radius=fov_radius)
    if hdu:
        print("Image fetched successfully!")
    else:
        print("Failed to fetch image or request timed out.")
        #return fallback placeholder image: "Finder chart not available."
        return "data:image/png;base64,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"
    
    wcs = WCS(hdu.header)
    
    plt.figure(figsize=(6.5,6.5))
    #plt.subplots_adjust(right=0.4,left=1.0)
    #plt.margins(10)
    # Set up axes & plot styles if needed.
    if ax is None:
        ax = plt.gcf().add_subplot(projection=wcs) # this makes the coordinates
    if style_kwargs is None:
        style_kwargs = {}
    style_kwargs = dict(style_kwargs)
    style_kwargs.setdefault('cmap', 'Greys')
    style_kwargs.setdefault('origin', 'lower')
    
    
    plt.subplots_adjust(left=0.2, bottom=0.1, right=0.95, top=0.9)

    
    
    lon = ax.coords[0]
    lat = ax.coords[1]

    lon.set_major_formatter('dd:mm:ss.s')
    lat.set_major_formatter('dd:mm')

    
    #other option

    #lon.set_major_formatter('d.d')#('dd:mm:ss.s')
    #lat.set_major_formatter('d.d')#('dd:mm')
    # https://docs.astropy.org/en/stable/visualization/wcsaxes/ticks_labels_grid.html
    
    
    if log:
        image_data = np.log(hdu.data)
    else:
        image_data = hdu.data
    ax.imshow(image_data, **style_kwargs)
    
    

    # Draw reticle
#    if reticle:
#        pixel_width = image_data.shape[0]
#        inner, outer = 0.03, 0.08

#        if reticle_style_kwargs is None:
#            reticle_style_kwargs = {}
#        reticle_style_kwargs.setdefault('linewidth', 2)
#        reticle_style_kwargs.setdefault('color', 'm')

#        ax.axvline(x=0.5*pixel_width, ymin=0.5+inner, ymax=0.5+outer,
#                   **reticle_style_kwargs)
#        ax.axvline(x=0.5*pixel_width, ymin=0.5-inner, ymax=0.5-outer,
#                   **reticle_style_kwargs)
#        ax.axhline(y=0.5*pixel_width, xmin=0.5+inner, xmax=0.5+outer,
#                   **reticle_style_kwargs)
#        ax.axhline(y=0.5*pixel_width, xmin=0.5-inner, xmax=0.5-outer,
#                   **reticle_style_kwargs)

    # Labels, title, grid
    ax.set(xlabel='RA', ylabel='Dec')
    #if target_name is not None:
    #    ax.set_title(target_name)
#    ax.grid(grid)
    
    
    # add marker
    ax.scatter(coord.ra,coord.dec,marker="+",c='r',s=150) ####### for this, use a symbol instead that is a cross that is empty at the center; color red

    # Redraw the figure for interactive sessions.
#    ax.figure.canvas.draw()

    # Create an in-memory buffer
    img_io = io.BytesIO()
    plt.savefig(img_io, format='png')
    img_io.seek(0)

    # Option a: Create a response with the image data
    #response = make_response(img_io.read())
    #response.headers['Content-Type'] = 'image/png'
    # Option b : Encode image to base64
    img_data = base64.b64encode(img_io.getvalue()).decode('utf-8')
    img_finder = f"data:image/png;base64,{img_data}"

    # Close plot
    plt.close()

    return img_finder


import multiprocessing
def fetch_skyview_with_timeout(position, coordinates, survey, radius, timeout=120):
    ctx = multiprocessing.get_context("spawn")  # More robust than "fork" on some platforms
    queue = ctx.Queue()
    proc = ctx.Process(target=fetch_image_worker,
                       args=(queue, position, coordinates, survey, radius))
    proc.start()
    proc.join(timeout)

    if proc.is_alive():
        print("Timeout reached. Terminating hung fetch.")
        proc.terminate()
        proc.join()
        return None

    result = queue.get()
    if isinstance(result, Exception):
        print(f"SkyView raised an error: {result}")
        return None

    return result

def fetch_image_worker(queue, position, coordinates, survey, radius):
    try:
        hdu = SkyView.get_images(position=position,
                                 coordinates=coordinates,
                                 survey=survey,
                                 radius=radius)[0][0]
        queue.put(hdu)  # Send result back
    except Exception as e:
        queue.put(e)
'''