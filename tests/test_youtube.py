"""Tests for the YouTube dashboard's pure logic: number formatting, the stats
query transforms (driven by a fake cursor, so no database is needed), and the
trending JSON-LD builder. These import ``functions`` / ``helpers`` directly and
touch no external services, so they run in CI without MySQL.
"""

from functions import youtube_stats
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


# --- get_biggest_climbers --------------------------------------------------

def test_get_biggest_climbers_shapes_rows():
    cur = FakeCursor([
        [("Vid A", "Chan A", 9, 4, 5, "aaa", "UCaaa"),
         ("Vid B", "Chan B", 6, 5, 1, "bbb", "UCbbb")],
    ])
    out = youtube_stats.get_biggest_climbers(cur, "2026-06-14", "2026-06-13")
    assert out[0] == {
        "video": "Vid A", "chnl": "Chan A", "prev_rank": 9, "today_rank": 4,
        "climb": 5, "vid_id": "aaa", "chnl_id": "UCaaa",
    }
    assert len(out) == 2


def test_get_biggest_climbers_no_prev_day_returns_empty():
    # No query should run when there is no previous day to compare against.
    cur = FakeCursor([])
    assert youtube_stats.get_biggest_climbers(cur, "2026-06-14", None) == []
    assert cur.executed == []


# --- get_view_velocity -----------------------------------------------------

def test_get_view_velocity_formats_numbers():
    cur = FakeCursor([
        [("Vid A", "Chan A", 2_500_000, 1_200_000, "aaa", "UCaaa")],
    ])
    out = youtube_stats.get_view_velocity(cur, "2026-06-14", "2026-06-13")
    assert out == [{
        "video": "Vid A", "chnl": "Chan A", "views": "2.5M", "gain": "1.2M",
        "vid_id": "aaa", "chnl_id": "UCaaa",
    }]


def test_get_view_velocity_no_prev_day_returns_empty():
    assert youtube_stats.get_view_velocity(FakeCursor([]), "2026-06-14", None) == []


# --- get_engagement_leaders ------------------------------------------------

def test_get_engagement_leaders_shapes_rows():
    cur = FakeCursor([
        [("Vid A", "Chan A", 1_000_000, 4.2, "aaa", "UCaaa")],
    ])
    out = youtube_stats.get_engagement_leaders(cur, "2026-06-14")
    assert out == [{
        "video": "Vid A", "chnl": "Chan A", "views": "1.0M", "like_pct": 4.2,
        "vid_id": "aaa", "chnl_id": "UCaaa",
    }]


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
