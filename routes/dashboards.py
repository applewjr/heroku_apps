"""Data dashboards: YouTube trending, ETL status, MTG prices."""

import io
import threading
import time
from datetime import datetime

import pandas as pd
import pytz
import requests
from flask import Blueprint, abort, redirect, render_template, url_for

import config
from data import etl_dash_queries
from extensions import auth, db_cursor
from functions import plot_viz, youtube_stats, youtube_revamp_stats
from helpers import make_trending_jsonld

bp = Blueprint('dashboards', __name__)

# MTG price data is published once a day to a fixed CloudFront URL (the object is
# overwritten in place). We key freshness off the CSV's own date stamp rather than
# a timer, so the route never assumes *when* the daily refresh lands.
PST = pytz.timezone('America/Los_Angeles')
# Backstop only: the last good render survives an extended upstream outage this
# long. Normal freshness comes from the data-date check + conditional GET below.
MTG_CACHE_TIMEOUT = 60 * 60 * 48

# The last good render, {'data_date', 'etag', 'html'}, held in the module
# rather than SimpleCache: SimpleCache pickles, so every /mtg view copied the
# ~4.8 MB page, and that churn was part of the dyno's memory creep
# (2026-10-10). Reads are lock-free (one reference read); writes happen
# under _mtg_rebuild_lock.
_mtg = {'entry': None, 'expires': 0.0}


def _mtg_cached():
    return _mtg['entry'] if time.time() < _mtg['expires'] else None


def _mtg_store(entry):
    _mtg['entry'], _mtg['expires'] = entry, time.time() + MTG_CACHE_TIMEOUT

# Serializes the rebuild so a burst of concurrent misses produces one render, not
# one per thread. Gunicorn runs a single worker with 8 threads, and a rebuild is
# expensive in memory: ~3.7MB of CSV parsed into ~16.8k rows, then boxed into an
# object-dtype frame, a list of dicts, a JSON string and a ~4.8MB page. Eight of
# those at once is what took the dyno to 612MB (R14) on 2026-09-05, when the daily
# Bluesky post drew ~16 link-preview crawlers to /mtg within seconds of the
# upstream file republishing - i.e. exactly when the cache was cold.
_mtg_rebuild_lock = threading.Lock()


# The last good /youtube_trending render and the ETL day it shows, held in the
# module rather than SimpleCache: SimpleCache pickles, so every view
# (including the hourly health probe) copied the 1-3 MB page, and that churn
# was part of the dyno's memory creep (2026-10-10). One entry does both jobs
# the old date-keyed and 'latest' keys did: it serves while data_version
# matches, and it is the fallback when MySQL is unreachable. 48h ages it out.
YOUTUBE_CACHE_TIMEOUT = 60 * 60 * 48
# (data_version, html, expires), swapped as one tuple so a concurrent reader
# never pairs one render's html with another's data_version.
_youtube = {'render': None}


def _youtube_cached():
    """(data_version, html) of the last good render, or (None, None)."""
    held = _youtube['render']
    if held is None or time.time() >= held[2]:
        return None, None
    return held[0], held[1]


def _serve_youtube_fallback():
    """Serve the last good /youtube_trending render when the DB is unavailable,
    or a clean 503 if nothing has been rendered on this dyno."""
    _, stale = _youtube_cached()
    if stale is not None:
        return stale
    abort(503, description="The trending dashboard is temporarily unavailable. Please try again shortly.")


@bp.route("/youtube_trending", methods=["POST", "GET"])
def youtube_trending():

    # Freshness signal: the latest day the ETL has landed, so a held render is
    # replaced the moment new rows arrive. collected_date is a DATE, so this
    # one cheap query needs no session time zone. If MySQL is unreachable,
    # serve the last good render instead of 500ing.
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute("""SELECT MAX(collected_date) FROM youtube_trending_revamp;""")
            data_version = cursor.fetchone()[0]
    except Exception as e:
        print(f"youtube_trending: freshness query failed ({e}); serving cached fallback")
        return _serve_youtube_fallback()

    held_version, held = _youtube_cached()
    if held is not None and held_version == data_version:
        return held

    # A DB failure mid-render shouldn't 500 either: build best-effort and fall
    # back to the last good render on any error.
    try:
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
            # engagement metrics on every row, so one sortable table per surface
            # does the work of the old climbers / velocity / engagement /
            # discussed panels. All fetch the full day (~50) so the page shows the
            # top 10 while keeping every row in the DOM for the sort buttons.
            top_now = youtube_revamp_stats.get_surface_today(cursor, data_version, prev_date, 'Now', limit=50)
            top_music = youtube_revamp_stats.get_surface_today(cursor, data_version, prev_date, 'Music', limit=50)
            top_gaming = youtube_revamp_stats.get_surface_today(cursor, data_version, prev_date, 'Gaming', limit=50)

        # schema.org ItemList of today's ranked videos for richer search results,
        # built from the Now feed that drives the on-page "Top Now Today" panel.
        trending_jsonld = make_trending_jsonld(
            (row['rank'], row['vid_id'], row['video']) for row in top_now[:10]
        )

        # Inline the plots as base64 data URIs so the cached HTML is self-contained
        # and never points at PNGs on the ephemeral per-dyno filesystem.
        yt_video_scatter = plot_viz.fig_to_data_uri(plot_viz.yt_video_scatter(data_version))
        yt_chnl_scatter = plot_viz.fig_to_data_uri(plot_viz.yt_chnl_scatter(data_version))
        yt_stacked_bar_plot = plot_viz.fig_to_data_uri(plot_viz.yt_stacked_bar_plot())

        rendered = render_template("youtube_trending.html", \
            top_10_title=top_10_title, top_10_channel=top_10_channel, top_categories=top_categories, \
            yt_stacked_bar_plot=yt_stacked_bar_plot, yt_video_scatter=yt_video_scatter, yt_chnl_scatter=yt_chnl_scatter, \
            data_version=data_version, kpis=kpis, age_buckets=age_buckets, \
            top_now=top_now, top_music=top_music, \
            top_gaming=top_gaming, trending_jsonld=trending_jsonld
            )
    except Exception as e:
        print(f"youtube_trending: render failed ({e}); serving cached fallback")
        return _serve_youtube_fallback()

    _youtube['render'] = (data_version, rendered, time.time() + YOUTUBE_CACHE_TIMEOUT)
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
    ``today_price_date`` stamp - the value used to decide whether a cached copy is
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
    # the daily refresh lands - there is no hardcoded update time to forget about.
    today = datetime.now(PST).strftime('%Y-%m-%d')
    cached = _mtg_cached()  # {'data_date', 'etag', 'html'} or None

    # Fast path: we already hold today's data, so don't even contact the CDN.
    if cached and cached["data_date"] == today:
        return cached["html"]

    # Past here we may rebuild, so only one thread at a time gets through. The
    # rest queue, and the re-check below usually hands them the render the winner
    # just finished instead of starting another one.
    with _mtg_rebuild_lock:
        # Re-read under the lock: whoever held it may have landed today's data
        # while we were queued, which is the whole point of the lock.
        cached = _mtg_cached()
        if cached and cached["data_date"] == today:
            return cached["html"]

        # We're holding yesterday's data (the pre-refresh window) or nothing yet.
        # Ask the CDN whether the file changed since we last pulled it: a
        # conditional GET returns a bodyless 304 when nothing's new, so polling
        # stays cheap no matter how long upstream takes to publish. Any upstream
        # error falls back to the last good render, so the page keeps serving
        # through transient blips.
        headers = {}
        if cached and cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]

        try:
            resp = requests.get(config.MTG_PATH, headers=headers, timeout=10)
            if resp.status_code == 304 and cached:
                _mtg_store(cached)  # re-arm the backstop
                return cached["html"]
            resp.raise_for_status()
            # Decode from bytes so a missing charset header can't corrupt accented
            # card names (requests would otherwise fall back to ISO-8859-1 for .text).
            rendered, data_date = _render_mtg(io.BytesIO(resp.content))
            _mtg_store({
                "data_date": data_date,
                "etag": resp.headers.get("ETag"),
                "html": rendered,
            })
            return rendered
        except Exception:
            if cached:
                return cached["html"]  # ride out the blip on the last good copy
            raise                      # cold cache + upstream down: nothing to serve
