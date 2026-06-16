"""Analytics queries for the multi-surface YouTube dashboard, against the
youtube_trending_revamp table.

Unlike the legacy youtube_trending table (a single "Now" feed), the revamp
collector records the same video under multiple trending_type "surfaces"
(Now / Music / Gaming) on the same day. That makes cross-surface questions
answerable for the first time, so this is where the crossover, surface-overlap
and per-surface panels live.

Every function takes an open cursor plus the latest *available* collected_date.
The revamp ETL runs alongside the legacy one (both at 00:00 PT), so the legacy
table's latest date is reused here rather than re-deriving the revamp table's.
"""

from functions.youtube_stats import _fmt_count, _fmt_count_or_dash

SURFACES = ('Now', 'Music', 'Gaming')


def get_top_by_surface(cursor, latest_date, surface, limit=5):
    """Top videos by rank on a single surface (Now / Music / Gaming) for the
    latest day. Exposes the Music and Gaming feeds, which the legacy table
    never carried."""
    if latest_date is None:
        return []
    cursor.execute(
        """SELECT vid_rank, video, chnl, vid_views, vid_likes, vid_comments, vid_id, chnl_id
           FROM youtube_trending_revamp
           WHERE collected_date = %s AND trending_type = %s
           ORDER BY vid_rank ASC
           LIMIT %s""",
        (latest_date, surface, limit),
    )
    return [
        {'rank': rank, 'video': video, 'chnl': chnl, 'views': _fmt_count(views),
         'likes': _fmt_count_or_dash(likes), 'comments': _fmt_count_or_dash(comments),
         'vid_id': vid_id, 'chnl_id': chnl_id}
        for (rank, video, chnl, views, likes, comments, vid_id, chnl_id) in cursor.fetchall()
    ]
