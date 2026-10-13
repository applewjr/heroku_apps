"""Tests for the YouTube dashboard: number formatting, the stats query
transforms (driven by a fake cursor, so no database is needed), the trending
JSON-LD builder, and the route's render memo (DB, stats and plots stubbed).
None of it touches an external service, so it runs in CI without MySQL.
"""

import pytest

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
    assert youtube_stats._fmt_count_or_dash(-1) == "-"   # hidden-stat sentinel
    assert youtube_stats._fmt_count_or_dash(None) == "-"


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
        # vid_rank, video, chnl, views, gain, likes, comments, prev_rank,
        # vid_id, chnl_id, days_on_list
        [(3, "Vid A", "Chan A", 1_000_000, 200_000, 50_000, 4_000, 7, "aaa", "UCaaa", 3)],
    ])
    out = youtube_revamp_stats.get_surface_today(cur, "2026-06-14", "2026-06-13", "Gaming")
    assert out == [{
        "rank": 3, "video": "Vid A", "chnl": "Chan A",
        "views": "1.0M", "gain": "200.0K",
        "likes": "50.0K", "like_pct": 5.0,
        "comments": "4.0K", "comment_pct": 0.4,
        "prev_rank": 7, "climb": 4, "days_on_list": 3,
        "vid_id": "aaa", "chnl_id": "UCaaa",
    }]


def test_get_surface_today_new_entrant_and_hidden_stats():
    # No prior-day match -> gain/prev_rank/climb are None; hidden -1 stats dash out.
    cur = FakeCursor([
        [(5, "Vid B", "Chan B", 800_000, None, -1, -1, None, "bbb", "UCbbb", 1)],
    ])
    row = youtube_revamp_stats.get_surface_today(cur, "2026-06-14", "2026-06-13", "Music")[0]
    assert row["gain"] is None
    assert row["prev_rank"] is None
    assert row["climb"] is None
    assert row["likes"] == "-" and row["like_pct"] is None
    assert row["comments"] == "-" and row["comment_pct"] is None
    # A brand-new entrant still counts today, so days-on-list is at least 1.
    assert row["days_on_list"] == 1


def test_get_surface_today_no_date_skips_query():
    cur = FakeCursor([])
    assert youtube_revamp_stats.get_surface_today(cur, None, None, "Music") == []
    assert cur.executed == []


def test_get_surface_today_hidden_views_dash_out():
    # A video that hides its view count stores the -1 sentinel in vid_views. The
    # views cell must dash out (not show "-1"), and the rate/gain figures that
    # divide by or subtract views must be None rather than negative nonsense.
    cur = FakeCursor([
        # vid_rank, video, chnl, views(-1), gain, likes, comments, prev_rank,
        # vid_id, chnl_id, days_on_list
        [(2, "Vid C", "Chan C", -1, -1_000_001, 5_000, 200, 4, "ccc", "UCccc", 2)],
    ])
    row = youtube_revamp_stats.get_surface_today(cur, "2026-06-14", "2026-06-13", "Now")[0]
    assert row["views"] == "-"
    assert row["gain"] is None
    assert row["like_pct"] is None
    assert row["comment_pct"] is None
    # Movement (rank-based, view-independent) still resolves.
    assert row["climb"] == 2
    # Longevity is independent of hidden view counts.
    assert row["days_on_list"] == 2


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


# --- the /youtube_trending render memo ---------------------------------------
# One module-level render serves while the ETL day is unchanged, and is the
# fallback when MySQL is down. It replaced SimpleCache, whose pickling copied
# the 1-3 MB page on every view (part of the dyno's memory creep).

@pytest.fixture
def yt(monkeypatch):
    """The route with the DB, stats, plots and template stubbed out. Returns
    a controller: set .version (or .db_down) and count .renders."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    import routes.dashboards as dashboards

    ctl = SimpleNamespace(version="2026-10-11", db_down=False, render_fails=False, renders=0)
    monkeypatch.setattr(dashboards, "_youtube", {"render": None})

    @contextmanager
    def fake_db():
        if ctl.db_down:
            raise RuntimeError("db down")
        cursor = SimpleNamespace(execute=lambda *a, **k: None, fetchone=lambda: (ctl.version,))
        yield None, cursor

    def fake_render(template, **kw):
        if ctl.render_fails:
            raise RuntimeError("render failed")
        ctl.renders += 1
        return f"page for {kw['data_version']} #{ctl.renders}"

    nothing = lambda *a, **k: []  # noqa: E731
    monkeypatch.setattr(dashboards, "db_cursor", fake_db)
    monkeypatch.setattr(dashboards, "render_template", fake_render)
    monkeypatch.setattr(dashboards, "youtube_stats", SimpleNamespace(
        get_prev_date=nothing, get_top_videos_30d=nothing, get_top_channels_30d=nothing,
        get_top_categories_30d=nothing, get_kpis=nothing, get_trending_age_buckets=nothing))
    monkeypatch.setattr(dashboards, "youtube_revamp_stats", SimpleNamespace(get_surface_today=nothing))
    monkeypatch.setattr(dashboards, "plot_viz", SimpleNamespace(
        fig_to_data_uri=lambda fig: "data:,", yt_video_scatter=nothing, yt_chnl_scatter=nothing,
        yt_stacked_bar_plot=nothing))
    monkeypatch.setattr(dashboards, "make_trending_jsonld", nothing)
    return ctl


def test_youtube_same_day_serves_the_held_render(client, yt):
    first = client.get("/youtube_trending").get_data(as_text=True)
    second = client.get("/youtube_trending").get_data(as_text=True)
    assert first == second == "page for 2026-10-11 #1"
    assert yt.renders == 1


def test_youtube_new_etl_day_renders_again(client, yt):
    client.get("/youtube_trending")
    yt.version = "2026-10-12"
    assert client.get("/youtube_trending").get_data(as_text=True) == "page for 2026-10-12 #2"


def test_youtube_db_down_serves_the_last_render(client, yt):
    client.get("/youtube_trending")
    yt.db_down = True
    resp = client.get("/youtube_trending")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "page for 2026-10-11 #1"


def test_youtube_db_down_with_nothing_held_is_a_503(client, yt):
    yt.db_down = True
    assert client.get("/youtube_trending").status_code == 503


def test_youtube_failed_render_falls_back_to_the_last_one(client, yt):
    client.get("/youtube_trending")
    yt.version, yt.render_fails = "2026-10-12", True
    assert client.get("/youtube_trending").get_data(as_text=True) == "page for 2026-10-11 #1"


def test_youtube_held_render_expires(client, yt, monkeypatch):
    import routes.dashboards as dashboards
    client.get("/youtube_trending")
    version, html, _ = dashboards._youtube["render"]
    dashboards._youtube["render"] = (version, html, 0.0)   # 48h backstop passed
    yt.db_down = True
    assert client.get("/youtube_trending").status_code == 503
