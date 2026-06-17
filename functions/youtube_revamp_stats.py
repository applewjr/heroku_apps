"""Per-surface "Top Today" queries for the dashboard, against the
youtube_trending_revamp table.

Unlike the legacy youtube_trending table (a single "Now" feed), the revamp
collector records videos under three trending_type "surfaces" — Now, Music and
Gaming — on the same day. get_surface_today builds the same rich, sortable view
(day-over-day movement plus engagement on every row) for any one of them.

It takes the latest and previous *available* collected_date values (not
CURDATE()), so the table reflects whatever day the ETL last landed and the
movement stays correct across gaps.
"""

from functions.youtube_stats import _fmt_count, _fmt_count_or_dash


def get_surface_today(cursor, latest_date, prev_date, surface, limit=50):
    """Today's full feed for one surface (Now / Music / Gaming) with day-over-day
    movement (previous rank, climb, views gained) and engagement rates (like %,
    comment %) on every row — the same rich, sortable shape for all three Top
    tables.

    The self-join to the previous available day is a LEFT JOIN so brand-new
    entrants still appear (with no prior rank and no view gain). Rates are
    computed in Python so hidden stats (the feed's -1 sentinel) become
    None -> '—' rather than a misleading negative percentage.
    """
    if latest_date is None:
        return []
    cursor.execute(
        """SELECT t.vid_rank, t.video, t.chnl, t.vid_views,
                  (t.vid_views - p.vid_views) AS gain,
                  t.vid_likes, t.vid_comments, p.vid_rank AS prev_rank,
                  t.vid_id, t.chnl_id
           FROM youtube_trending_revamp AS t
           LEFT JOIN youtube_trending_revamp AS p
                  ON p.vid_id = t.vid_id
                 AND p.collected_date = %s
                 AND p.trending_type = %s
           WHERE t.collected_date = %s AND t.trending_type = %s
           ORDER BY t.vid_rank ASC
           LIMIT %s""",
        (prev_date, surface, latest_date, surface, limit),
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
