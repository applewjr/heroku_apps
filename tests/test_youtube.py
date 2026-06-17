"""Tests for the YouTube dashboard's pure logic: number formatting, the stats
query transforms (driven by a fake cursor, so no database is needed), and the
trending JSON-LD builder. These import ``functions`` / ``helpers`` directly and
touch no external services, so they run in CI without MySQL.
"""

from functions import youtube_stats, youtube_revamp_stats
from helpers import make_trending_jsonld


# --- _fmt_count ------------------------------------------------------------

def test_fmt_count_thresholds():
    assert youtube_stats._fmt_count(0) == "0"
    assert youtube_stats._fmt_count(999) == "999"
    assert youtube_stats._fmt_count(1000) == "1.0K"
    assert youtube_stats._fmt_count(1234) == "1.2K"
    assert youtube_stats._fmt_count(1_500_000) == "1.5M"
    assert youtube_stats._fmt_count(2_000_000_000) == "2.0B"


def test_fmt_count_handles_none():
    assert youtube_stats._fmt_count(None) == "0"


def test_fmt_count_or_dash_hides_sentinel_and_null():
    assert youtube_stats._fmt_count_or_dash(1500) == "1.5K"
    assert youtube_stats._fmt_count_or_dash(0) == "0"
    assert youtube_stats._fmt_count_or_dash(-1) == "—"   # hidden-stat sentinel
    assert youtube_stats._fmt_count_or_dash(None) == "—"


# --- fake cursor -----------------------------------------------------------

class FakeCursor:
    """Returns queued fetch results in order; ignores the SQL it is given."""

    def __init__(self, fetch_results):
        self._queue = list(fetch_results)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._queue.pop(0)

    def fetchall(self):
        return self._queue.pop(0)


# --- get_kpis --------------------------------------------------------------

def test_get_kpis_aggregates_and_formats():
    cur = FakeCursor([
        (50, 30, 12_400_000),   # COUNT(*), COUNT(DISTINCT chnl_id), MAX(vid_views)
        ("Top Video",),         # highest-viewed title
        ("Music", 12),          # top category + its count
        (8,),                   # new entrants vs previous day
    ])
    out = youtube_stats.get_kpis(cur, "2026-06-14", "2026-06-13")
    assert out["videos_tracked"] == 50
    assert out["channels_tracked"] == 30
    assert out["new_entrants"] == 8
    assert out["top_category"] == "Music"
    assert out["top_video"] == "Top Video"
    assert out["max_views"] == "12.4M"


def test_get_kpis_without_prev_day_uses_today_count():
    cur = FakeCursor([
        (50, 30, 12_400_000),
        ("Top Video",),
        ("Music", 12),
    ])
    out = youtube_stats.get_kpis(cur, "2026-06-14", None)
    assert out["new_entrants"] == 50  # falls back to videos_tracked


# --- get_trending_age_buckets ----------------------------------------------

def test_get_trending_age_buckets_labels_and_pct():
    cur = FakeCursor([
        [(0, 5), (2, 5), (4, 10)],  # (bucket index, count)
    ])
    out = youtube_stats.get_trending_age_buckets(cur, "2026-06-14")
    assert out == [
        {"label": "0–1 days", "count": 5, "pct": 25},
        {"label": "4–7 days", "count": 5, "pct": 25},
        {"label": "30+ days", "count": 10, "pct": 50},
    ]


# --- revamp: get_surface_today ---------------------------------------------

def test_get_surface_today_computes_movement_and_rates():
    cur = FakeCursor([
        # vid_rank, video, chnl, views, gain, likes, comments, prev_rank, vid_id, chnl_id
        [(3, "Vid A", "Chan A", 1_000_000, 200_000, 50_000, 4_000, 7, "aaa", "UCaaa")],
    ])
    out = youtube_revamp_stats.get_surface_today(cur, "2026-06-14", "2026-06-13", "Gaming")
    assert out == [{
        "rank": 3, "video": "Vid A", "chnl": "Chan A",
        "views": "1.0M", "gain": "200.0K",
        "likes": "50.0K", "like_pct": 5.0,
        "comments": "4.0K", "comment_pct": 0.4,
        "prev_rank": 7, "climb": 4,
        "vid_id": "aaa", "chnl_id": "UCaaa",
    }]


def test_get_surface_today_new_entrant_and_hidden_stats():
    # No prior-day match -> gain/prev_rank/climb are None; hidden -1 stats dash out.
    cur = FakeCursor([
        [(5, "Vid B", "Chan B", 800_000, None, -1, -1, None, "bbb", "UCbbb")],
    ])
    row = youtube_revamp_stats.get_surface_today(cur, "2026-06-14", "2026-06-13", "Music")[0]
    assert row["gain"] is None
    assert row["prev_rank"] is None
    assert row["climb"] is None
    assert row["likes"] == "—" and row["like_pct"] is None
    assert row["comments"] == "—" and row["comment_pct"] is None


def test_get_surface_today_no_date_skips_query():
    cur = FakeCursor([])
    assert youtube_revamp_stats.get_surface_today(cur, None, None, "Music") == []
    assert cur.executed == []


# --- 30-day leaderboards (ported from the retired views) -------------------

def test_get_top_videos_30d_shapes_rows():
    cur = FakeCursor([
        [("Vid A", "Chan A", 12, 3, "aaa", "UCaaa")],
    ])
    out = youtube_stats.get_top_videos_30d(cur, "2026-06-14")
    assert out == [{
        "Video": "Vid A", "Channel": "Chan A", "Count of Days": 12,
        "Best Video Rank": 3, "vid_id": "aaa", "chnl_id": "UCaaa",
    }]


def test_get_top_channels_30d_shapes_rows():
    cur = FakeCursor([
        [("Chan A", 20, 1, "UCaaa")],
    ])
    out = youtube_stats.get_top_channels_30d(cur, "2026-06-14")
    assert out == [{
        "Channel": "Chan A", "Count of Video Days": 20, "Best Channel Rank": 1,
        "chnl_id": "UCaaa",
    }]


def test_get_top_categories_30d_shapes_rows():
    cur = FakeCursor([
        [("Music", 120, 30, 5)],
    ])
    out = youtube_stats.get_top_categories_30d(cur, "2026-06-14")
    assert out == [{
        "Category": "Music", "Top 50 Count": 120, "Top 10 Count": 30, "Top 1 Count": 5,
    }]


# --- make_trending_jsonld --------------------------------------------------

def test_make_trending_jsonld_structure():
    out = make_trending_jsonld([(1, "aaa", "Vid A"), (2, "bbb", "Vid B")])
    assert out["@context"] == "https://schema.org"
    assert out["@type"] == "ItemList"
    assert len(out["itemListElement"]) == 2
    first = out["itemListElement"][0]
    assert first["@type"] == "ListItem"
    assert first["position"] == 1
    assert first["url"] == "https://www.youtube.com/watch?v=aaa"
    assert first["name"] == "Vid A"
