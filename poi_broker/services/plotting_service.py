"""Bokeh plotting service for lightcurve and feature visualizations."""

import html
import logging
from bokeh.plotting import figure
from bokeh.embed import components
from bokeh.models import Legend

logger = logging.getLogger(__name__)


def bokeh_script_body(script: str) -> str:
    """Return the JavaScript inside a Bokeh ``<script>`` wrapper, if present."""
    if not script:
        return ''
    stripped = script.strip()
    if stripped.lower().startswith('<script'):
        start = stripped.find('>')
        end = stripped.lower().rfind('</script>')
        if start != -1 and end != -1 and end > start:
            return stripped[start + 1:end]
    return script


def bokeh_json_payload(div: str, script: str) -> dict[str, str]:
    """JSON body for plot endpoints: Bokeh div HTML plus unwrapped script."""
    return {'div': div or '', 'script': bokeh_script_body(script)}


def bokeh_warning_payload(message: str) -> dict[str, str]:
    """HTTP 200 empty-state for plot tabs (yellow warning, no script).

    Do not replace this with 404/``{error}``. The catalog modal treats 4xx as a
    red load failure; missing classification/lightcurve/feature data must stay
    a warning inserted via ``insertBokehFromPayload``.
    """
    return bokeh_json_payload(
        f'<div class="no-data alert alert-warning">{html.escape(message)}</div>',
        '',
    )


# Color scheme for passbands (lightcurve)
PASSBAND_COLORS = {
    'i': 'darkkhaki',
    'R': 'indianred',
    'g': 'limegreen',
}

# Color scheme for features (up to 10 features)
FEATURE_COLORS = [
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


def create_bokeh_lightcurve_figure(lightcurve_data):
    """
    Create a Bokeh lightcurve scatter plot with data filtered by passband.
    
    Args:
        lightcurve_data: List of Ztf objects with date_alert_mjd, ant_mag_corrected, ant_passband
    
    Returns:
        tuple: (html_div, script) from bokeh.embed.components()
    """
    if not lightcurve_data or all(row.date_alert_mjd is None or row.ant_mag_corrected is None for row in lightcurve_data):
        return ('<div class="no-data alert alert-warning">No lightcurve data available!</div>', '') 

    p = figure(height=350, sizing_mode="stretch_width")
    p.xaxis.axis_label = 'date_alert_mjd'
    p.yaxis.axis_label = 'ant_mag_corrected'
    p.y_range.flipped = True  # Lower magnitudes are brighter and belong at the top.
    
    # Plot each passband separately
    for passband, color in PASSBAND_COLORS.items():
        x_coords = []
        y_coords = []
        
        for row in lightcurve_data:
            if row.ant_passband == passband:
                if row.date_alert_mjd is not None and row.ant_mag_corrected is not None:
                    x_coords.append(row.date_alert_mjd)
                    y_coords.append(row.ant_mag_corrected)
        
        if x_coords:  # Only plot if there's data
            p.scatter(x_coords, y_coords, size=5, color=color, alpha=0.5)

    if not p.renderers:
        return ('<div class="no-data alert alert-warning">There is no lightcurve data to plot!</div>', '') 
    
    script, div = components(p)
    return div, script


def create_bokeh_feature_plot(feature_data, feature_list):
    """
    Create a Bokeh scatter plot for multiple features with different colors.
    
    Args:
        feature_data: List of Ztf objects with date_alert_mjd, ant_mag_corrected, and feature columns
        feature_list: List of feature column names to plot (max 10)
    
    Returns:
        tuple: (html_div, script) from bokeh.embed.components()
    """
    if not feature_data or all(row.date_alert_mjd is None or row.ant_mag_corrected is None for row in feature_data):
        return ('<div class="no-data alert alert-warning">There is no feature data to plot available!</div>', '')
    
    if not feature_list:
        return ('<div class="no-data alert alert-warning">No features selected for plotting!</div>', '')

    p = figure(height=350, sizing_mode="stretch_width")
    p.xaxis.axis_label = 'date_alert_mjd'
    p.yaxis.axis_label = 'features'
    
    # Limit to 10 features for readability
    feature_list = feature_list[:10]
    
    legend_items = []
    for index, feature in enumerate(feature_list):
        x_coords = []
        y_coords = []
        
        for row in feature_data:
            # Only plot if we have both date and magnitude
            if row.date_alert_mjd is not None and row.ant_mag_corrected is not None:
                value = getattr(row, feature, None)
                if value is not None:
                    x_coords.append(row.date_alert_mjd)
                    y_coords.append(value)
        
        if x_coords:  # Only plot if there's data
            pc = p.scatter(
                x_coords,
                y_coords,
                size=5,
                color=FEATURE_COLORS[index],
                alpha=0.5
            )
            legend_items.append((feature, [pc]))
    
    # Add legend
    if legend_items:
        legend = Legend(items=legend_items)
        p.add_layout(legend, 'right')
        p.legend.label_text_font_size = '9px'
        p.legend.glyph_width = 12

    if not p.renderers:
        return ('<div class="no-data alert alert-warning">There is no features data to plot!</div>', '')        
    
    script, div = components(p)
    return div, script
