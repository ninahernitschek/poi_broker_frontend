import pytest
from types import SimpleNamespace

from poi_broker.services.plotting_service import (
    bokeh_json_payload,
    bokeh_script_body,
    bokeh_warning_payload,
    create_bokeh_feature_plot,
    create_bokeh_lightcurve_figure,
)


def test_bokeh_script_body_unwraps_script_tags():
    raw = '<script type="text/javascript">\nBokeh.embed.embed_item();\n</script>'
    assert bokeh_script_body(raw) == '\nBokeh.embed.embed_item();\n'
    assert bokeh_script_body('already-js') == 'already-js'
    assert bokeh_json_payload('<div id="p"></div>', raw) == {
        'div': '<div id="p"></div>',
        'script': '\nBokeh.embed.embed_item();\n',
    }
    warning = bokeh_warning_payload('No classification data found')
    assert warning['script'] == ''
    assert 'alert-warning' in warning['div']
    assert 'No classification data found' in warning['div']
    escaped = bokeh_warning_payload('<script>alert(1)</script>')
    assert '<script>' not in escaped['div']
    assert '&lt;script&gt;' in escaped['div']


@pytest.mark.slow
def test_create_bokeh_lightcurve_figure_returns_warning_for_empty_data():
    div, script = create_bokeh_lightcurve_figure([])

    assert "No lightcurve data available" in div
    assert script == ""


@pytest.mark.slow
def test_create_bokeh_lightcurve_figure_skips_invalid_rows_and_returns_components():
    rows = [
        SimpleNamespace(date_alert_mjd=None, ant_mag_corrected=None, ant_passband='g'),
        SimpleNamespace(date_alert_mjd=59000.1, ant_mag_corrected=19.2, ant_passband='g'),
        SimpleNamespace(date_alert_mjd=59001.2, ant_mag_corrected=None, ant_passband='g'),
        SimpleNamespace(date_alert_mjd=59002.3, ant_mag_corrected=18.9, ant_passband='R'),
    ]

    div, script = create_bokeh_lightcurve_figure(rows)

    assert div.startswith("<div")
    assert "No lightcurve data" not in div
    assert "<script" in script
    assert "Bokeh" in script
    assert '"flipped":true' in script


@pytest.mark.slow
def test_create_bokeh_feature_plot_returns_warning_when_no_feature_list_is_provided():
    rows = [SimpleNamespace(date_alert_mjd=59000.1, ant_mag_corrected=19.2, feature_a=1.2)]

    div, script = create_bokeh_feature_plot(rows, [])

    assert "No features selected for plotting" in div
    assert script == ""


@pytest.mark.slow
def test_create_bokeh_feature_plot_returns_warning_when_all_rows_invalid():
    rows = [SimpleNamespace(date_alert_mjd=None, ant_mag_corrected=None, feature_a=1.2)]

    div, script = create_bokeh_feature_plot(rows, ["feature_a"])

    assert "no feature data" in div.lower()
    assert script == ""


@pytest.mark.slow
def test_create_bokeh_feature_plot_returns_components_for_valid_features():
    rows = [
        SimpleNamespace(date_alert_mjd=59000.1, ant_mag_corrected=19.2, feature_x=1.1, feature_y=2.1),
        SimpleNamespace(date_alert_mjd=59001.2, ant_mag_corrected=19.0, feature_x=1.3, feature_y=None),
    ]

    div, script = create_bokeh_feature_plot(rows, ["feature_x", "feature_y"])

    assert div.startswith("<div")
    assert "<script" in script
    assert "feature_x" in script
    assert "feature_y" in script


@pytest.mark.slow
def test_create_bokeh_feature_plot_limits_feature_list_to_ten_entries():
    rows = [
        SimpleNamespace(
            date_alert_mjd=59000.1,
            ant_mag_corrected=19.2,
            **{f"f{i}": float(i) for i in range(11)},
        )
    ]
    feature_list = [f"f{i}" for i in range(11)]

    div, script = create_bokeh_feature_plot(rows, feature_list)

    assert div.startswith("<div")
    assert "<script" in script
    assert "f0" in script
    assert "f9" in script
    assert "f10" not in script
