"""Analytics queries for the YouTube dashboard.

All panels read the site's primary "Now" feed, which now comes from the
youtube_trending_revamp table (filtered to trending_type='Now') rather than the
legacy youtube_trending table. NOW_FEED below is the single seam for that
choice, so re-pointing the dashboard to a different surface — or back to the
legacy table — is a one-line change here (and the same constant is imported by
functions/plot_viz.py for the charts).

Every function takes an open cursor plus the latest and previous *available*
collected_date values (not CURDATE()), so the panels reflect whatever day the
ETL last landed and stay correct across gaps or a not-yet-run daily load.
"""

# Single source of truth for the dashboard's primary feed: the "Now" surface of
# the revamp table, used as a derived table so every query reads
# `FROM {NOW_FEED} AS <alias>`. The legacy youtube_trending table is no longer
# referenced; swap this one line to change the feed everywhere.
NOW_FEED = "(SELECT * FROM youtube_trending_revamp WHERE trending_type = 'Now')"


def _fmt_count(value):
    """Human-readable big number: 1234567 -> '1.2M'."""
    value = value or 0
    if abs(value) >= 1_000_000_000:
        return f'{value / 1_000_000_000:.1f}B'
    if abs(value) >= 1_000_000:
        return f'{value / 1_000_000:.1f}M'
    if abs(value) >= 1_000:
        return f'{value / 1_000:.1f}K'
    return str(int(value))


def _fmt_count_or_dash(value):
    """Like _fmt_count, but renders hidden stats — the revamp feed's -1 sentinel
    or a NULL — as an em dash instead of a misleading number."""
    if value is None or value < 0:
        return '—'
    return _fmt_count(value)


def get_prev_date(cursor, latest_date):
    """The most recent collected_date strictly before latest_date (handles gaps)."""
    cursor.execute(
        f"SELECT MAX(collected_date) FROM {NOW_FEED} AS yt WHERE collected_date < %s",
        (latest_date,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def get_kpis(cursor, latest_date, prev_date):
    """Headline numbers for the latest available day."""
    cursor.execute(
        f"""SELECT COUNT(*), COUNT(DISTINCT chnl_id), MAX(vid_views)
           FROM {NOW_FEED} AS yt WHERE collected_date = %s""",
        (latest_date,),
    )
    videos_tracked, channels_tracked, max_views = cursor.fetchone()

    cursor.execute(
        f"""SELECT video FROM {NOW_FEED} AS yt
           WHERE collected_date = %s ORDER BY vid_views DESC LIMIT 1""",
        (latest_date,),
    )
    row = cursor.fetchone()
    top_video = row[0] if row else None

    cursor.execute(
        f"""SELECT cat.category, COUNT(*) AS c
           FROM {NOW_FEED} AS yt
           LEFT JOIN youtube_cat cat ON yt.vid_cat_id = cat.id
           WHERE yt.collected_date = %s
           GROUP BY cat.category ORDER BY c DESC LIMIT 1""",
        (latest_date,),
    )
    row = cursor.fetchone()
    top_category = row[0] if row and row[0] else None

    if prev_date is not None:
        cursor.execute(
            f"""SELECT COUNT(*) FROM (
                   SELECT DISTINCT vid_id FROM {NOW_FEED} AS yt WHERE collected_date = %s
               ) t
               WHERE NOT EXISTS (
                   SELECT 1 FROM {NOW_FEED} AS p
                   WHERE p.vid_id = t.vid_id AND p.collected_date = %s
               )""",
            (latest_date, prev_date),
        )
        new_entrants = cursor.fetchone()[0]
    else:
        new_entrants = videos_tracked

    return {
        'videos_tracked': videos_tracked or 0,
        'channels_tracked': channels_tracked or 0,
        'new_entrants': new_entrants or 0,
        'top_category': top_category or '—',
        'top_video': top_video or '—',
        'max_views': _fmt_count(max_views),
    }


def get_now_today(cursor, latest_date, prev_date, limit=50):
    """Today's full Now feed with day-over-day movement (previous rank, climb,
    views gained) and engagement rates (like %, comment %) on every row.

    One sortable table that does the work of the old climbers / velocity /
    engagement / discussed panels. The self-join to the previous available day
    is a LEFT JOIN so brand-new entrants still appear (with no prior rank and no
    view gain). Rates are computed in Python so hidden stats (the feed's -1
    sentinel) become None -> '—' rather than a misleading negative percentage.
    """
    cursor.execute(
        f"""SELECT t.vid_rank, t.video, t.chnl, t.vid_views,
                  (t.vid_views - p.vid_views) AS gain,
                  t.vid_likes, t.vid_comments, p.vid_rank AS prev_rank,
                  t.vid_id, t.chnl_id
           FROM {NOW_FEED} AS t
           LEFT JOIN {NOW_FEED} AS p
                  ON t.vid_id = p.vid_id AND p.collected_date = %s
           WHERE t.collected_date = %s
           ORDER BY t.vid_rank ASC
           LIMIT %s""",
        (prev_date, latest_date, limit),
    )
    rows = []
    for (rank, video, chnl, views, gain, likes, comments, prev_rank,
         vid_id, chnl_id) in cursor.fetchall():
        like_pct = round(likes / views * 100, 1) if likes and likes > 0 and views else None
        comment_pct = round(comments / views * 100, 2) if comments and comments > 0 and views else None
        rows.append({
            'rank': rank,
            'video': video,
            'chnl': chnl,
            'views': _fmt_count(views),
            'gain': _fmt_count(gain) if gain is not None else None,
            'likes': _fmt_count_or_dash(likes),
            'like_pct': like_pct,
            'comments': _fmt_count_or_dash(comments),
            'comment_pct': comment_pct,
            'prev_rank': prev_rank,
            'climb': (prev_rank - rank) if prev_rank is not None else None,
            'vid_id': vid_id,
            'chnl_id': chnl_id,
        })
    return rows


def get_trending_age_buckets(cursor, latest_date):
    """Distribution of how old videos are (days from upload) when trending today."""
    cursor.execute(
        f"""SELECT
               CASE
                   WHEN DATEDIFF(collected_date, DATE(vid_uploaded_dt)) <= 1 THEN 0
                   WHEN DATEDIFF(collected_date, DATE(vid_uploaded_dt)) <= 3 THEN 1
                   WHEN DATEDIFF(collected_date, DATE(vid_uploaded_dt)) <= 7 THEN 2
                   WHEN DATEDIFF(collected_date, DATE(vid_uploaded_dt)) <= 30 THEN 3
                   ELSE 4
               END AS bucket,
               COUNT(*) AS c
           FROM {NOW_FEED} AS yt
           WHERE collected_date = %s AND vid_uploaded_dt IS NOT NULL
           GROUP BY bucket ORDER BY bucket""",
        (latest_date,),
    )
    labels = {0: '0–1 days', 1: '2–3 days', 2: '4–7 days', 3: '8–30 days', 4: '30+ days'}
    rows = cursor.fetchall()
    total = sum(c for _, c in rows) or 1
    return [
        {'label': labels[b], 'count': c, 'pct': round(c / total * 100)}
        for (b, c) in rows
    ]


# --- 30-day leaderboards (ported from the retired vw_prod_youtube_* views) ---
# These return lists of dicts keyed to match the template's existing column
# labels, and window off the latest available day rather than CURDATE() so they
# stay correct across ETL gaps.

def get_top_videos_30d(cursor, latest_date, limit=10):
    """Videos with the most trending-days over the last 30 days."""
    cursor.execute(
        f"""SELECT video, chnl, COUNT(*) AS occurrences, MIN(vid_rank) AS best_vid_rank,
                  vid_id, chnl_id
           FROM {NOW_FEED} AS yt
           WHERE collected_date >= %s - INTERVAL 30 DAY
           GROUP BY vid_id
           ORDER BY occurrences DESC, best_vid_rank ASC
           LIMIT %s""",
        (latest_date, limit),
    )
    return [
        {'Video': v, 'Channel': c, 'Count of Days': occ, 'Best Video Rank': best,
         'vid_id': vid_id, 'chnl_id': chnl_id}
        for (v, c, occ, best, vid_id, chnl_id) in cursor.fetchall()
    ]


def get_top_channels_30d(cursor, latest_date, limit=10):
    """Channels with the most trending video-days over the last 30 days."""
    cursor.execute(
        f"""SELECT chnl, COUNT(*) AS occurrences, MIN(vid_rank) AS best_channel_rank, chnl_id
           FROM {NOW_FEED} AS yt
           WHERE collected_date >= %s - INTERVAL 30 DAY
           GROUP BY chnl_id
           ORDER BY occurrences DESC, best_channel_rank ASC
           LIMIT %s""",
        (latest_date, limit),
    )
    return [
        {'Channel': c, 'Count of Video Days': occ, 'Best Channel Rank': best, 'chnl_id': chnl_id}
        for (c, occ, best, chnl_id) in cursor.fetchall()
    ]


def get_top_categories_30d(cursor, latest_date):
    """Category presence in the Top 50 / 10 / 1 over the last 30 days."""
    cursor.execute(
        f"""SELECT category,
                  SUM(CASE WHEN vid_rank <= 50 THEN 1 ELSE 0 END) AS top_50_count,
                  SUM(CASE WHEN vid_rank <= 10 THEN 1 ELSE 0 END) AS top_10_count,
                  SUM(CASE WHEN vid_rank <= 1  THEN 1 ELSE 0 END) AS top_1_count
           FROM {NOW_FEED} AS yt
           LEFT JOIN youtube_cat AS cat ON yt.vid_cat_id = cat.id
           WHERE collected_date >= %s - INTERVAL 30 DAY
           GROUP BY category
           ORDER BY top_50_count DESC, top_10_count DESC, top_1_count DESC""",
        (latest_date,),
    )
    return [
        {'Category': cat, 'Top 50 Count': t50, 'Top 10 Count': t10, 'Top 1 Count': t1}
        for (cat, t50, t10, t1) in cursor.fetchall()
    ]
