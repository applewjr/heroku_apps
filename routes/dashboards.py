"""Data dashboards: YouTube trending, ETL status, MTG prices."""

import pandas as pd
from flask import Blueprint, redirect, render_template, url_for

import config
from data import etl_dash_queries
from extensions import auth, cache, db_cursor
from functions import plot_viz, youtube_stats
from helpers import make_trending_jsonld

bp = Blueprint('dashboards', __name__)


@bp.route("/youtube_trending", methods=["POST", "GET"])
def youtube_trending():

    # Freshness signal: key the cache on the latest day the ETL has landed so
    # the cache invalidates the moment new rows arrive. collected_date is a
    # DATE, so this one cheap query needs no session time zone.
    with db_cursor() as (conn, cursor):
        cursor.execute("""SELECT MAX(collected_date) FROM youtube_trending;""")
        data_version = cursor.fetchone()[0]

    cache_key = f"youtube_trending::{data_version}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    with db_cursor() as (conn, cursor):
        cursor.execute("""SET time_zone = 'America/Los_Angeles';""")

        cursor.execute("""SELECT * FROM vw_prod_youtube_top_10_today;""")
        top_10_today = pd.DataFrame(cursor.fetchall(), columns=['Rank', 'Video', 'Channel', 'Best Video Rank', 'Video Rank Yesterday', 'vid_id', 'chnl_id'])

        cursor.execute("""SELECT * FROM vw_prod_youtube_top_10_title;""")
        top_10_title = pd.DataFrame(cursor.fetchall(), columns=['Video', 'Channel', 'Count of Days', 'Best Video Rank', 'vid_id', 'chnl_id'])

        cursor.execute("""SELECT * FROM vw_prod_youtube_top_10_channel;""")
        top_10_channel = pd.DataFrame(cursor.fetchall(), columns=['Channel', 'Count of Video Days', 'Best Channel Rank', 'chnl_id'])

        cursor.execute("""SELECT * FROM vw_prod_youtube_top_categories;""")
        top_categories = pd.DataFrame(cursor.fetchall(), columns=['Category', 'Top 50 Count', 'Top 10 Count', 'Top 1 Count'])

        # Analytics panels keyed off the latest available day (robust to ETL gaps).
        prev_date = youtube_stats.get_prev_date(cursor, data_version)
        kpis = youtube_stats.get_kpis(cursor, data_version, prev_date)
        climbers = youtube_stats.get_biggest_climbers(cursor, data_version, prev_date)
        velocity = youtube_stats.get_view_velocity(cursor, data_version, prev_date)
        engagement = youtube_stats.get_engagement_leaders(cursor, data_version)
        age_buckets = youtube_stats.get_trending_age_buckets(cursor, data_version)

    # schema.org ItemList of today's ranked videos for richer search results.
    trending_jsonld = make_trending_jsonld(
        (row['Rank'], row['vid_id'], row['Video']) for _, row in top_10_today.iterrows()
    )

    # Inline the plots as base64 data URIs so the cached HTML is self-contained
    # and never points at PNGs on the ephemeral per-dyno filesystem.
    yt_video_scatter = plot_viz.fig_to_data_uri(plot_viz.yt_video_scatter())
    yt_chnl_scatter = plot_viz.fig_to_data_uri(plot_viz.yt_chnl_scatter())
    yt_stacked_bar_plot = plot_viz.fig_to_data_uri(plot_viz.yt_stacked_bar_plot())

    rendered = render_template("youtube_trending.html", top_10_today=top_10_today, \
        top_10_title=top_10_title, top_10_channel=top_10_channel, top_categories=top_categories, \
        yt_stacked_bar_plot=yt_stacked_bar_plot, yt_video_scatter=yt_video_scatter, yt_chnl_scatter=yt_chnl_scatter, \
        data_version=data_version, kpis=kpis, climbers=climbers, velocity=velocity, \
        engagement=engagement, age_buckets=age_buckets, trending_jsonld=trending_jsonld
        )

    # 48h timeout ages out stale date-keys; fresh data lands a new key daily.
    cache.set(cache_key, rendered, timeout=60*60*48)
    return rendered


def get_valid_rounds():
    rounds = {details.get('round') for _, details in etl_dash_queries.items() if 'round' in details}
    return rounds


@bp.route("/etl_dash")
def etl_dash_redirect():
    return redirect(url_for('dashboards.etl_status_dash', round=1))


@bp.route("/etl_dash/<int:round>", methods=["POST", "GET"])
@auth.login_required
def etl_status_dash(round):

    valid_rounds = get_valid_rounds()

    if round not in valid_rounds:
        return f"Invalid round parameter. Return only: {valid_rounds}", 400

    with db_cursor() as (conn, cursor):
        query_dict = {}
        for name, details in etl_dash_queries.items():
            if details.get('round') == round:
                try:
                    cursor.execute(details['query'])
                    result = cursor.fetchall()
                    columns = [desc[0] for desc in cursor.description]
                    query_df = pd.DataFrame(result, columns=columns)

                    # Here we add the description to the dictionary entry for this name
                    query_dict[name] = {
                        'description': details['description'],
                        'data': query_df
                    }
                except Exception as e:
                    print(f'{name} not run due to error: {e}')

        return render_template("etl_dash.html", query_dict=query_dict, valid_rounds=sorted(list(valid_rounds)), round=round)


@bp.route("/mtg", methods=["POST", "GET"])
@cache.cached()  # Cache the entire view for the default timeout
def mtg_prices():
    df = pd.read_csv(config.MTG_PATH)

    today_price_date_str = df['today_price_date'].head(1).values[0]

    df = df[df['tcgplayer_id'].notnull()]
    df = df[df['1wk_diff'].notnull()]
    df = df[['name', 'set_name', 'set_type', 'released_at', 'today_price',
             '1wk_ago_price', '1wk_diff', '2wk_ago_price', '2wk_diff',
             '4wk_ago_price', '4wk_diff', 'tcgplayer_id']].copy()
    df.columns = ['name', 'set_name', 'set_type', 'released_at', 'today_price',
                  'p1wk', 'd1wk', 'p2wk', 'd2wk', 'p4wk', 'd4wk', 'tcg_url']

    # Round the numeric columns for display
    for col in ['today_price', 'p1wk', 'd1wk', 'p2wk', 'd2wk', 'p4wk', 'd4wk']:
        df[col] = pd.to_numeric(df[col], errors='coerce').round(2)

    df = df.sort_values(by='d1wk', ascending=False).reset_index(drop=True)

    # Replace NaN with None so the JSON payload is valid (NaN is not valid JSON).
    # astype(object) first, otherwise None is coerced back to NaN in float columns.
    mtg_data = df.astype(object).where(pd.notnull(df), None).to_dict(orient='records')

    return render_template('mtg_prices.html', mtg_data=mtg_data, today_price_date_str=today_price_date_str)
