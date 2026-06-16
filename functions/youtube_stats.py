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


def get_biggest_climbers(cursor, latest_date, prev_date, limit=10):
    """Videos with the largest rank improvement vs. the previous day."""
    if prev_date is None:
        return []
    cursor.execute(
        f"""SELECT t.video, t.chnl, p.vid_rank, t.vid_rank, (p.vid_rank - t.vid_rank) AS climb,
                  t.vid_likes, t.vid_comments, t.vid_id, t.chnl_id
           FROM {NOW_FEED} AS t
           JOIN {NOW_FEED} AS p ON t.vid_id = p.vid_id AND p.collected_date = %s
           WHERE t.collected_date = %s AND (p.vid_rank - t.vid_rank) > 0
           ORDER BY climb DESC, t.vid_rank ASC
           LIMIT %s""",
        (prev_date, latest_date, limit),
    )
    return [
        {'video': v, 'chnl': c, 'prev_rank': pr, 'today_rank': tr, 'climb': climb,
         'likes': _fmt_count_or_dash(likes), 'comments': _fmt_count_or_dash(comments),
         'vid_id': vid_id, 'chnl_id': chnl_id}
        for (v, c, pr, tr, climb, likes, comments, vid_id, chnl_id) in cursor.fetchall()
    ]


def get_view_velocity(cursor, latest_date, prev_date, limit=10):
    """Videos gaining the most views vs. the previous day."""
    if prev_date is None:
        return []
    cursor.execute(
        f"""SELECT t.video, t.chnl, t.vid_views, (t.vid_views - p.vid_views) AS gain,
                  t.vid_likes, t.vid_comments, t.vid_id, t.chnl_id
           FROM {NOW_FEED} AS t
           JOIN {NOW_FEED} AS p ON t.vid_id = p.vid_id AND p.collected_date = %s
           WHERE t.collected_date = %s AND t.vid_views >= p.vid_views
           ORDER BY gain DESC
           LIMIT %s""",
        (prev_date, latest_date, limit),
    )
    return [
        {'video': v, 'chnl': c, 'views': _fmt_count(views), 'gain': _fmt_count(gain),
         'likes': _fmt_count_or_dash(likes), 'comments': _fmt_count_or_dash(comments),
         'vid_id': vid_id, 'chnl_id': chnl_id}
        for (v, c, views, gain, likes, comments, vid_id, chnl_id) in cursor.fetchall()
    ]


def get_engagement_leaders(cursor, latest_date, limit=10, min_views=50000):
    """Highest like-to-view ratio among videos with a meaningful view count.

    The revamp feed stores -1 for hidden likes, so the > 0 guard drops those.
    """
    cursor.execute(
        f"""SELECT video, chnl, vid_views, vid_likes, vid_comments,
                  ROUND(vid_likes / vid_views * 100, 1) AS like_pct,
                  vid_id, chnl_id
           FROM {NOW_FEED} AS yt
           WHERE collected_date = %s AND vid_views >= %s AND vid_likes > 0
           ORDER BY like_pct DESC
           LIMIT %s""",
        (latest_date, min_views, limit),
    )
    return [
        {'video': v, 'chnl': c, 'views': _fmt_count(views),
         'likes': _fmt_count_or_dash(likes), 'comments': _fmt_count_or_dash(comments),
         'like_pct': like_pct, 'vid_id': vid_id, 'chnl_id': chnl_id}
        for (v, c, views, likes, comments, like_pct, vid_id, chnl_id) in cursor.fetchall()
    ]


def get_most_discussed(cursor, latest_date, limit=10, min_views=50000):
    """Highest comment-to-view ratio among videos with a meaningful view count.

    Where like-rate measures approval, comment-rate measures discussion, so this
    surfaces the divisive/debate-driven videos (news, drama) that the like-rate
    panel misses. The revamp feed stores -1 for hidden comments, so the > 0 guard
    drops those.
    """
    cursor.execute(
        f"""SELECT video, chnl, vid_views, vid_likes, vid_comments,
                  ROUND(vid_comments / vid_views * 100, 2) AS comment_pct,
                  vid_id, chnl_id
           FROM {NOW_FEED} AS yt
           WHERE collected_date = %s AND vid_views >= %s AND vid_comments > 0
           ORDER BY comment_pct DESC
           LIMIT %s""",
        (latest_date, min_views, limit),
    )
    return [
        {'video': v, 'chnl': c, 'views': _fmt_count(views),
         'likes': _fmt_count_or_dash(likes), 'comments': _fmt_count(comments),
         'comment_pct': comment_pct, 'vid_id': vid_id, 'chnl_id': chnl_id}
        for (v, c, views, likes, comments, comment_pct, vid_id, chnl_id) in cursor.fetchall()
    ]


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
