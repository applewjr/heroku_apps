"""Data dashboards: YouTube trending, ETL status, MTG prices."""

import io
from datetime import datetime

import pandas as pd
import pytz
import requests
from flask import Blueprint, redirect, render_template, url_for

import config
from data import etl_dash_queries
from extensions import auth, cache, db_cursor
from functions import plot_viz, youtube_stats, youtube_revamp_stats
from helpers import make_trending_jsonld

bp = Blueprint('dashboards', __name__)

# MTG price data is published once a day to a fixed CloudFront URL (the object is
# overwritten in place). We key freshness off the CSV's own date stamp rather than
# a timer, so the route never assumes *when* the daily refresh lands.
PST = pytz.timezone('America/Los_Angeles')
MTG_CACHE_KEY = "mtg_prices"
# Backstop only: the last good render survives an extended upstream outage this
# long. Normal freshness comes from the data-date check + conditional GET below.
MTG_CACHE_TIMEOUT = 60 * 60 * 48


@bp.route("/youtube_trending", methods=["POST", "GET"])
def youtube_trending():

    # Freshness signal: key the cache on the latest day the ETL has landed so
    # the cache invalidates the moment new rows arrive. collected_date is a
    # DATE, so this one cheap query needs no session time zone.
    with db_cursor() as (conn, cursor):
        cursor.execute("""SELECT MAX(collected_date) FROM youtube_trending_revamp;""")
        data_version = cursor.fetchone()[0]

    # Namespace bumped to v2 when the dashboard moved off the legacy
    # youtube_trending table onto the revamp Now feed: both tables share the same
    # latest collected_date, so without the bump a stale pre-migration render
    # could keep being served under an identical key.
    cache_key = f"youtube_trending::v2::{data_version}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    with db_cursor() as (conn, cursor):
        cursor.execute("""SET time_zone = 'America/Los_Angeles';""")

        # Analytics panels keyed off the latest available day (robust to ETL gaps).
        prev_date = youtube_stats.get_prev_date(cursor, data_version)
        top_10_title = youtube_stats.get_top_videos_30d(cursor, data_version)
        top_10_channel = youtube_stats.get_top_channels_30d(cursor, data_version)
        top_categories = youtube_stats.get_top_categories_30d(cursor, data_version)
        kpis = youtube_stats.get_kpis(cursor, data_version, prev_date)
        age_buckets = youtube_stats.get_trending_age_buckets(cursor, data_version)

        # The three 'Top <surface> Today' tables are consolidated power tables:
        # each is its full feed for the day with day-over-day movement and
        # engagement metrics on every row, so one sortable table per surface does
        # the work of the old climbers / velocity / engagement / discussed panels.
        # All fetch the full day (~50) so the page shows the top 10 while keeping
        # every row in the DOM for the sort buttons to surface.
        top_now = youtube_revamp_stats.get_surface_today(cursor, data_version, prev_date, 'Now', limit=50)
        top_music = youtube_revamp_stats.get_surface_today(cursor, data_version, prev_date, 'Music', limit=50)
        top_gaming = youtube_revamp_stats.get_surface_today(cursor, data_version, prev_date, 'Gaming', limit=50)

    # schema.org ItemList of today's ranked videos for richer search results,
    # built from the Now feed that now drives the on-page "Top Now Today" panel.
    trending_jsonld = make_trending_jsonld(
        (row['rank'], row['vid_id'], row['video']) for row in top_now[:10]
    )

    # Inline the plots as base64 data URIs so the cached HTML is self-contained
    # and never points at PNGs on the ephemeral per-dyno filesystem.
    yt_video_scatter = plot_viz.fig_to_data_uri(plot_viz.yt_video_scatter())
    yt_chnl_scatter = plot_viz.fig_to_data_uri(plot_viz.yt_chnl_scatter())
    yt_stacked_bar_plot = plot_viz.fig_to_data_uri(plot_viz.yt_stacked_bar_plot())

    rendered = render_template("youtube_trending.html", \
        top_10_title=top_10_title, top_10_channel=top_10_channel, top_categories=top_categories, \
        yt_stacked_bar_plot=yt_stacked_bar_plot, yt_video_scatter=yt_video_scatter, yt_chnl_scatter=yt_chnl_scatter, \
        data_version=data_version, kpis=kpis, age_buckets=age_buckets, \
        top_now=top_now, top_music=top_music, \
        top_gaming=top_gaming, trending_jsonld=trending_jsonld
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


def _render_mtg(source):
    """Render the MTG prices page from a CSV ``source`` (path or file-like).

    Returns ``(rendered_html, data_date)``, where ``data_date`` is the CSV's own
    ``today_price_date`` stamp — the value used to decide whether a cached copy is
    still current.
    """
    df = pd.read_csv(source)

    today_price_date_str = str(df['today_price_date'].head(1).values[0])

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

    rendered = render_template('mtg_prices.html', mtg_data=mtg_data,
                               today_price_date_str=today_price_date_str)
    return rendered, today_price_date_str


@bp.route("/mtg", methods=["POST", "GET"])
def mtg_prices():
    # Freshness is defined by the data's own date stamp, not by when the upstream
    # pipeline runs: the CSV is current iff it is stamped with today's date (PST,
    # the zone the pipeline stamps in). This keeps the route independent of *when*
    # the daily refresh lands — there is no hardcoded update time to forget about.
    today = datetime.now(PST).strftime('%Y-%m-%d')
    cached = cache.get(MTG_CACHE_KEY)  # {'data_date', 'etag', 'html'} or None

    # Fast path: we already hold today's data, so don't even contact the CDN.
    if cached and cached["data_date"] == today:
        return cached["html"]

    # We're holding yesterday's data (the pre-refresh window) or nothing yet. Ask
    # the CDN whether the file changed since we last pulled it: a conditional GET
    # returns a bodyless 304 when nothing's new, so polling stays cheap no matter
    # how long upstream takes to publish. Any upstream error falls back to the
    # last good render, so the page keeps serving through transient blips.
    headers = {}
    if cached and cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]

    try:
        resp = requests.get(config.MTG_PATH, headers=headers, timeout=10)
        if resp.status_code == 304 and cached:
            cache.set(MTG_CACHE_KEY, cached, timeout=MTG_CACHE_TIMEOUT)  # re-arm TTL
            return cached["html"]
        resp.raise_for_status()
        # Decode from bytes so a missing charset header can't corrupt accented
        # card names (requests would otherwise fall back to ISO-8859-1 for .text).
        rendered, data_date = _render_mtg(io.BytesIO(resp.content))
        cache.set(MTG_CACHE_KEY, {
            "data_date": data_date,
            "etag": resp.headers.get("ETag"),
            "html": rendered,
        }, timeout=MTG_CACHE_TIMEOUT)
        return rendered
    except Exception:
        if cached:
            return cached["html"]  # ride out the blip on the last good copy
        raise                      # cold cache + upstream down: nothing to serve
