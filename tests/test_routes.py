"""Smoke tests: each key route should respond (not 500).

These catch the failures that take a whole page down after a deploy - broken
imports, missing templates, blueprint registration mistakes, renamed Jinja
variables - without needing the database.
"""

import pytest

# Pages that render straight from code/static data (no DB required).
GET_OK = [
    "/",
    "/wordle",
    "/antiwordle",
    "/quordle",
    "/wordiply",
    "/any_word",
    "/fixer",
    "/common_denominator",
    "/blossom",
    "/umbra",
    "/tiltconnect4",
    "/kintsugi",
    "/dogs",
    "/privacy-policy",
    "/feedback",
    "/robots.txt",
    "/sitemap.xml",
    "/ads.txt",
]

# (legacy path, where it should 301 to)
REDIRECTS = [
    ("/hex", "/umbra"),
    ("/wordle_og", "/wordle"),
    ("/antiwordle_og", "/antiwordle"),
    ("/blossom_bee", "/blossom"),
    ("/quordle_mobile", "/quordle"),
]


@pytest.mark.parametrize("path", GET_OK)
def test_get_returns_200(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}"


@pytest.mark.parametrize("path,target", REDIRECTS)
def test_legacy_redirects(client, path, target):
    resp = client.get(path)
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith(target)


def test_unknown_path_returns_404(client):
    assert client.get("/this-page-does-not-exist").status_code == 404


def test_protected_dashboard_requires_auth(client):
    # No credentials -> 401, returned before any DB access.
    assert client.get("/etl_dash/1").status_code == 401


def test_wordle_post_returns_picks(client):
    resp = client.post("/wordle", json={"wordle_data": []})
    assert resp.status_code == 200
    body = resp.get_json()
    assert "final_out1" in body
    assert body["final_out1"].startswith("Pick")


def test_wordiply_post_returns_matches(client):
    resp = client.post("/wordiply", json={"search_string": "zz"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert "results" in body
    assert body["results"]  # non-empty
    assert all("zz" in word for word in body["results"])
