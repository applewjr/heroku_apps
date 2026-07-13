"""Espresso optimizer routes. GET and POST share one code path: POSTed form
values override the defaults via request.form.get (form is empty on GET)."""

import base64
import io

import matplotlib.pyplot as plt
from flask import Blueprint, redirect, render_template, request, url_for

import config
from data import espresso_points
from extensions import cache
from functions import espresso
from helpers import ValidationError, parse_float, parse_int

bp = Blueprint('espresso', __name__)


def fig_to_data_uri(fig):
    """Render a matplotlib figure to an inline PNG data URI and close it.

    Embedding the bytes in the HTML response avoids writing a shared file to
    Heroku's ephemeral, per-dyno filesystem (where the follow-up image request
    can hit a different dyno) and sidesteps browser caching of a constant URL.
    """
    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    plt.close(fig)
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode('ascii')
    return f'data:image/png;base64,{encoded}'


def get_espresso_data():
    # Check if data is in cache
    espresso_data = cache.get('espresso_data')
    if espresso_data is None:
        # Data not in cache, so pull it from Google Sheets
        google_credentials = espresso.google_sheets_base(config.GOOGLE_SHEETS_JSON)
        df_profile = espresso.get_google_sheets_profile(google_credentials, config.GOOGLE_SHEETS_URL_PROFILE)
        df_espresso_initial = espresso.get_google_sheets_espresso(google_credentials, config.GOOGLE_SHEETS_URL_ESPRESSO)
        valid_user_name_list, valid_roast_list, valid_shots_list = espresso.get_user_roast_values(df_espresso_initial)
        scatter_espresso_col_labels = espresso.get_scatter_col_labels()
        espresso_data = {
            'df_profile': df_profile,
            'df_espresso_initial': df_espresso_initial,
            'valid_user_name_list': valid_user_name_list,
            'valid_roast_list': valid_roast_list,
            'valid_shots_list': valid_shots_list,
            'scatter_espresso_col_labels': scatter_espresso_col_labels
        }
        # Cache data for future use
        cache.set('espresso_data', espresso_data, timeout=600)  # Cache for 10 minutes
    return espresso_data


@bp.route('/validate_password', methods=['POST'])
def validate_password():
    user_password = request.form['password']

    if user_password == config.GOOGLE_FORM_PASS:
        return redirect(config.GOOGLE_FORM_URL)
    else:
        referrer = request.headers.get("Referer")
        return redirect(referrer if referrer else url_for('misc.run_index'))


@bp.route('/espresso/home/', methods=['GET', 'POST'])
def espresso_home():
    return render_template('espresso_home.html')


@bp.route('/espresso')
def espresso_home_redirect():
    return redirect(url_for('espresso.espresso_home'))


@bp.route('/espresso/recommendation/', methods=['GET', 'POST'])
def espresso_recommendation():

    # Bot gate: GET renders a cheap "click to continue" page and runs nothing.
    # The heavy sklearn pipeline only runs on the POST from the Continue/Calculate button.
    if request.method == 'GET':
        return render_template('espresso_gate.html', gate_title='KNN Predictive Recommendations')

    user_pred = request.form.get('user_pred', 'James')
    roast_pred = request.form.get('roast_pred', 'Medium')
    shots_pred = request.form.get('shots_pred', '2')
    # Validate-only (the template echo and clean_espresso_df both expect the
    # string): junk shots 400s here instead of 500ing on int() downstream.
    # Runs before get_espresso_data so bad input never triggers the Sheets pull.
    parse_int(shots_pred, 'shots_pred', default=2)

    espresso_data = get_espresso_data()

    df_analyze, _df_scatter = espresso.clean_espresso_df(
        user_pred, roast_pred, shots_pred,
        espresso_data['df_espresso_initial'], espresso_data['df_profile'], config.ESPRESSO_WATER_TEMP_NA_VAL
    )
    optimal_parameters_dict, good_run, performance_dict = espresso.find_optimal_espresso_parameters(df_analyze)

    return render_template('espresso_recommendation.html',
        valid_user_name_list=espresso_data['valid_user_name_list'],
        valid_roast_list=espresso_data['valid_roast_list'],
        valid_shots_list=espresso_data['valid_shots_list'],
        optimal_parameters_dict=optimal_parameters_dict, performance_dict=performance_dict, good_run=good_run,
        user_pred_val=user_pred, roast_pred_val=roast_pred, shots_pred_val=shots_pred)


@bp.route('/espresso/plot/', methods=['GET', 'POST'])
def espresso_plot():

    # Bot gate: GET renders a cheap "click to continue" page and runs nothing.
    # The matplotlib render only happens on the POST from the Continue/Calculate button.
    if request.method == 'GET':
        return render_template('espresso_gate.html', gate_title='Dynamic Scatter Plots')

    # Validate-only, before the Sheets pull (see espresso_recommendation).
    parse_int(request.form.get('shots_pred_scatter', '2'), 'shots_pred_scatter', default=2)

    espresso_data = get_espresso_data()

    espresso_x_col = request.form.get('espresso_x_col', 'flow_time_seconds')
    espresso_y_col = request.form.get('espresso_y_col', 'final_score')
    espresso_z_col = request.form.get('espresso_z_col', 'final_score')
    user_pred_scatter = request.form.get('user_pred_scatter', 'James')
    roast_pred_scatter = request.form.get('roast_pred_scatter', 'Medium')
    shots_pred_scatter = request.form.get('shots_pred_scatter', '2')

    _df_analyze, df_scatter = espresso.clean_espresso_df(
        user_pred_scatter, roast_pred_scatter, shots_pred_scatter,
        espresso_data['df_espresso_initial'], espresso_data['df_profile'], config.ESPRESSO_WATER_TEMP_NA_VAL
    )
    espresso_scatter_plot = espresso.espresso_dynamic_scatter(df_scatter, espresso_x_col, espresso_y_col, espresso_z_col)
    scatter_img = fig_to_data_uri(espresso_scatter_plot)

    return render_template('espresso_plot.html',
        valid_user_name_list=espresso_data['valid_user_name_list'],
        valid_roast_list=espresso_data['valid_roast_list'],
        valid_shots_list=espresso_data['valid_shots_list'],
        espresso_x_col_val=espresso_x_col, espresso_y_col_val=espresso_y_col, espresso_z_col_val=espresso_z_col,
        scatter_espresso_col_labels=espresso_data['scatter_espresso_col_labels'], scatter_img=scatter_img,
        user_pred_scatter_val=user_pred_scatter, roast_pred_scatter_val=roast_pred_scatter, shots_pred_scatter_val=shots_pred_scatter)


# Defaults for the explore form; POSTed values override per-key.
EXPLORE_DEFAULTS = {
    'distance_user': 'James',
    'distance_roast': 'Medium',
    'distance_shots': '2',
    'distance_grind_min': '11',
    'distance_grind_max': '13',
    'distance_grind_granularity': '0.5',
    'distance_coffee_g_min': '16',
    'distance_coffee_g_max': '18',
    'distance_coffee_g_granularity': '0.1',
    'distance_espresso_g_min': '32',
    'distance_espresso_g_max': '42',
    'distance_espresso_g_granularity': '0.1',
}


@bp.route('/espresso/explore/', methods=['GET', 'POST'])
def espresso_explore():

    # Bot gate: GET renders a cheap "click to continue" page and runs nothing.
    # The distance calc + 3D render only happen on the POST from the Continue/Calculate button.
    if request.method == 'GET':
        return render_template('espresso_gate.html', gate_title='Exploration Recommendations')

    p = {key: request.form.get(key, default) for key, default in EXPLORE_DEFAULTS.items()}

    # Validate the numeric inputs before doing anything expensive. Beyond the
    # junk-input 400s, the grid-size caps matter: the grid search meshgrids
    # min..max at the given granularity in all three dimensions at once, so the
    # budget must be on the *total* cell count - three axes of 1000 steps each
    # pass a per-axis check but multiply to ~10^9 mesh points (tens of GB).
    # Legit usage today is ~10k cells, so 100k leaves generous headroom.
    parse_int(p['distance_shots'], 'distance_shots', default=2)
    total_cells = 1
    for dim in ('grind', 'coffee_g', 'espresso_g'):
        mn = parse_float(p[f'distance_{dim}_min'], f'distance_{dim}_min', default=0.0)
        mx = parse_float(p[f'distance_{dim}_max'], f'distance_{dim}_max', default=0.0)
        gran = parse_float(p[f'distance_{dim}_granularity'], f'distance_{dim}_granularity', default=0.1)
        if gran <= 0 or mx < mn:
            raise ValidationError(f'distance_{dim} min/max/granularity is invalid')
        # Float math on purpose: an extreme range/granularity pair can overflow
        # the division to inf, which math.ceil would turn into an OverflowError
        # (-> 500). As a float, inf just propagates and fails the budget check.
        total_cells *= (mx - mn) / gran + 1
        if total_cells > 100_000:
            raise ValidationError('distance exploration grid is too large: '
                                  'use a smaller range or coarser granularity')

    espresso_data = get_espresso_data()

    _df_analyze, df_scatter = espresso.clean_espresso_df(
        p['distance_user'], p['distance_roast'], p['distance_shots'],
        espresso_data['df_espresso_initial'], espresso_data['df_profile'], config.ESPRESSO_WATER_TEMP_NA_VAL
    )
    df_furthest = df_scatter[['niche_grind_setting', 'ground_coffee_grams', 'espresso_out_grams']]
    furthest_point = espresso.get_furthest_point_multidimensional(df_furthest,
        p['distance_grind_min'], p['distance_grind_max'], p['distance_grind_granularity'],
        p['distance_coffee_g_min'], p['distance_coffee_g_max'], p['distance_coffee_g_granularity'],
        p['distance_espresso_g_min'], p['distance_espresso_g_max'], p['distance_espresso_g_granularity'])
    scatter_3d = espresso.plot_3d_scatter(df_scatter)
    scatter_3d_img = fig_to_data_uri(scatter_3d)

    return render_template('espresso_explore.html',
        valid_user_name_list=espresso_data['valid_user_name_list'],
        valid_roast_list=espresso_data['valid_roast_list'],
        valid_shots_list=espresso_data['valid_shots_list'],
        furthest_point=furthest_point, scatter_3d=scatter_3d_img,
        **{f'{key}_val': value for key, value in p.items()})


@bp.route('/espresso/baseline/', methods=['GET', 'POST'])
def espresso_baseline():

    roast_options = ["Light", "Medium", "Medium Dark", "Dark"]
    dose_options = ["1", "2", "3"]

    roast = request.form.get('roast', 'Medium')
    dose = request.form.get('dose', '2')
    # Validate-only (template compares dose_val against the string options):
    # junk dose 400s here instead of 500ing on int() in get_naive_espresso_points.
    parse_int(dose, 'dose', default=2)

    naive_espresso_info = espresso.get_naive_espresso_points(roast, dose, espresso_points)

    return render_template('espresso_baseline.html', naive_espresso_info=naive_espresso_info, roast_val=roast, dose_val=dose,
        roast_options=roast_options, dose_options=dose_options)
