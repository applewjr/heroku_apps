"""Analytics queries for the YouTube dashboard, against the youtube_trending
base table.

Every function takes an open cursor plus the latest and previous *available*
collected_date values (not CURDATE()), so the panels reflect whatever day the
ETL last landed and stay correct across gaps or a not-yet-run daily load.
"""


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


def get_prev_date(cursor, latest_date):
    """The most recent collected_date strictly before latest_date (handles gaps)."""
    cursor.execute(
        "SELECT MAX(collected_date) FROM youtube_trending WHERE collected_date < %s",
        (latest_date,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def get_kpis(cursor, latest_date, prev_date):
    """Headline numbers for the latest available day."""
    cursor.execute(
        """SELECT COUNT(*), COUNT(DISTINCT chnl_id), MAX(vid_views)
           FROM youtube_trending WHERE collected_date = %s""",
        (latest_date,),
    )
    videos_tracked, channels_tracked, max_views = cursor.fetchone()

    cursor.execute(
        """SELECT video FROM youtube_trending
           WHERE collected_date = %s ORDER BY vid_views DESC LIMIT 1""",
        (latest_date,),
    )
    row = cursor.fetchone()
    top_video = row[0] if row else None

    cursor.execute(
        """SELECT cat.category, COUNT(*) AS c
           FROM youtube_trending yt
           LEFT JOIN youtube_cat cat ON yt.vid_cat_id = cat.id
           WHERE yt.collected_date = %s
           GROUP BY cat.category ORDER BY c DESC LIMIT 1""",
        (latest_date,),
    )
    row = cursor.fetchone()
    top_category = row[0] if row and row[0] else None

    if prev_date is not None:
        cursor.execute(
            """SELECT COUNT(*) FROM (
                   SELECT DISTINCT vid_id FROM youtube_trending WHERE collected_date = %s
               ) t
               WHERE NOT EXISTS (
                   SELECT 1 FROM youtube_trending p
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
        """SELECT t.video, t.chnl, p.vid_rank, t.vid_rank, (p.vid_rank - t.vid_rank) AS climb,
                  t.vid_id, t.chnl_id
           FROM youtube_trending t
           JOIN youtube_trending p ON t.vid_id = p.vid_id AND p.collected_date = %s
           WHERE t.collected_date = %s AND (p.vid_rank - t.vid_rank) > 0
           ORDER BY climb DESC, t.vid_rank ASC
           LIMIT %s""",
        (prev_date, latest_date, limit),
    )
    return [
        {'video': v, 'chnl': c, 'prev_rank': pr, 'today_rank': tr, 'climb': climb,
         'vid_id': vid_id, 'chnl_id': chnl_id}
        for (v, c, pr, tr, climb, vid_id, chnl_id) in cursor.fetchall()
    ]


def get_view_velocity(cursor, latest_date, prev_date, limit=10):
    """Videos gaining the most views vs. the previous day."""
    if prev_date is None:
        return []
    cursor.execute(
        """SELECT t.video, t.chnl, t.vid_views, (t.vid_views - p.vid_views) AS gain,
                  t.vid_id, t.chnl_id
           FROM youtube_trending t
           JOIN youtube_trending p ON t.vid_id = p.vid_id AND p.collected_date = %s
           WHERE t.collected_date = %s AND t.vid_views >= p.vid_views
           ORDER BY gain DESC
           LIMIT %s""",
        (prev_date, latest_date, limit),
    )
    return [
        {'video': v, 'chnl': c, 'views': _fmt_count(views), 'gain': _fmt_count(gain),
         'vid_id': vid_id, 'chnl_id': chnl_id}
        for (v, c, views, gain, vid_id, chnl_id) in cursor.fetchall()
    ]


def get_engagement_leaders(cursor, latest_date, limit=10, min_views=50000):
    """Highest like-to-view ratio among videos with a meaningful view count."""
    cursor.execute(
        """SELECT video, chnl, vid_views, ROUND(vid_likes / vid_views * 100, 1) AS like_pct,
                  vid_id, chnl_id
           FROM youtube_trending
           WHERE collected_date = %s AND vid_views >= %s AND vid_likes IS NOT NULL
           ORDER BY like_pct DESC
           LIMIT %s""",
        (latest_date, min_views, limit),
    )
    return [
        {'video': v, 'chnl': c, 'views': _fmt_count(views), 'like_pct': like_pct,
         'vid_id': vid_id, 'chnl_id': chnl_id}
        for (v, c, views, like_pct, vid_id, chnl_id) in cursor.fetchall()
    ]


def get_trending_age_buckets(cursor, latest_date):
    """Distribution of how old videos are (days from upload) when trending today."""
    cursor.execute(
        """SELECT
               CASE
                   WHEN DATEDIFF(collected_date, DATE(vid_uploaded_dt)) <= 1 THEN 0
                   WHEN DATEDIFF(collected_date, DATE(vid_uploaded_dt)) <= 3 THEN 1
                   WHEN DATEDIFF(collected_date, DATE(vid_uploaded_dt)) <= 7 THEN 2
                   WHEN DATEDIFF(collected_date, DATE(vid_uploaded_dt)) <= 30 THEN 3
                   ELSE 4
               END AS bucket,
               COUNT(*) AS c
           FROM youtube_trending
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
