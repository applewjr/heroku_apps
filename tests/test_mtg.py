"""Tests for the /mtg route's freshness + caching behavior.

The route reads a CSV published once a day to a fixed CloudFront URL. Instead of a
timer it keys freshness off the CSV's own ``today_price_date`` stamp, and uses a
conditional GET (ETag) so it only re-downloads when the file actually changes.
These tests drive that logic with a fake ``requests.get`` so they touch no network,
and exercise the route end to end so cache state is set up the way the app sets it.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

import routes.dashboards as dashboards
from extensions import cache


# --- fixtures / helpers ----------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_mtg_cache(flask_app):
    """SimpleCache lives for the whole session, so clear our key around each test."""
    def _clear():
        with flask_app.app_context():
            cache.delete(dashboards.MTG_CACHE_KEY)
    _clear()
    yield
    _clear()


@pytest.fixture
def fake_get(monkeypatch):
    """Replace requests.get with a queue-driven fake that records each call."""
    ctl = SimpleNamespace(calls=[], queue=[])

    def _get(url, headers=None, timeout=None):
        ctl.calls.append({"url": url, "headers": headers or {}})
        return ctl.queue.pop(0)

    monkeypatch.setattr(dashboards.requests, "get", _get)
    return ctl


def _today():
    return datetime.now(dashboards.PST).strftime('%Y-%m-%d')


def _csv_bytes(data_date):
    """A minimal but complete MTG CSV (one row) stamped with ``data_date``."""
    header = ("today_price_date,tcgplayer_id,name,set_name,set_type,released_at,"
              "today_price,1wk_ago_price,1wk_diff,2wk_ago_price,2wk_diff,"
              "4wk_ago_price,4wk_diff")
    row = (f"{data_date},12345,Black Lotus,Alpha,core,1993-08-05,"
           "100.0,90.0,1.11,80.0,1.25,70.0,1.43")
    return f"{header}\n{row}\n".encode()


class FakeResponse:
    def __init__(self, status_code=200, content=b"", etag=None):
        self.status_code = status_code
        self.content = content
        self.headers = {"ETag": etag} if etag else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# --- tests -----------------------------------------------------------------

def test_cold_cache_downloads_and_renders(client, fake_get):
    fake_get.queue.append(FakeResponse(200, _csv_bytes(_today()), etag='"v1"'))

    resp = client.get("/mtg")

    assert resp.status_code == 200
    assert b"Black Lotus" in resp.data
    assert len(fake_get.calls) == 1


def test_today_data_serves_from_cache_without_hitting_cdn(client, fake_get):
    # First request populates the cache with today-dated data...
    fake_get.queue.append(FakeResponse(200, _csv_bytes(_today()), etag='"v1"'))
    assert client.get("/mtg").status_code == 200

    # ...so the second request must not touch the CDN at all (fast path).
    resp = client.get("/mtg")
    assert resp.status_code == 200
    assert len(fake_get.calls) == 1  # still just the first request's call


def test_stale_cache_revalidates_with_conditional_get(client, fake_get):
    # First request lands an OLD-dated file (e.g. it's the pre-refresh window).
    fake_get.queue.append(FakeResponse(200, _csv_bytes("2000-01-01"), etag='"old"'))
    first = client.get("/mtg")

    # Second request: data isn't today's, so the route revalidates. Source
    # unchanged -> bodyless 304 -> serve the copy we already rendered.
    fake_get.queue.append(FakeResponse(304))
    second = client.get("/mtg")

    assert second.status_code == 200
    assert second.data == first.data
    assert fake_get.calls[1]["headers"].get("If-None-Match") == '"old"'


def test_new_file_replaces_stale_cache(client, fake_get):
    fake_get.queue.append(FakeResponse(200, _csv_bytes("2000-01-01"), etag='"old"'))
    client.get("/mtg")

    # The daily file lands: revalidation returns 200 with today's data + new ETag.
    fake_get.queue.append(FakeResponse(200, _csv_bytes(_today()), etag='"new"'))
    resp = client.get("/mtg")
    assert resp.status_code == 200
    assert b"Black Lotus" in resp.data

    # Now we hold today's data, so a third request takes the fast path (no call).
    client.get("/mtg")
    assert len(fake_get.calls) == 2


def test_upstream_error_falls_back_to_cached_copy(client, fake_get, monkeypatch):
    fake_get.queue.append(FakeResponse(200, _csv_bytes("2000-01-01"), etag='"old"'))
    first = client.get("/mtg")

    def _boom(url, headers=None, timeout=None):
        raise ConnectionError("CloudFront unreachable")

    monkeypatch.setattr(dashboards.requests, "get", _boom)
    resp = client.get("/mtg")

    assert resp.status_code == 200
    assert resp.data == first.data  # rode out the blip on the last good copy


def test_cold_cache_plus_upstream_down_returns_500(client, monkeypatch):
    # Nothing cached and the source is unreachable: the one case it can't serve.
    def _boom(url, headers=None, timeout=None):
        raise ConnectionError("CloudFront unreachable")

    monkeypatch.setattr(dashboards.requests, "get", _boom)
    assert client.get("/mtg").status_code == 500
