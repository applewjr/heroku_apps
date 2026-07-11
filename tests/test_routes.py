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


# ---------------------------------------------------------------------------
# Junk input (scanner probes) in numeric form fields -> clean 400, never a 500.
# ---------------------------------------------------------------------------

ANY_WORD_FORM = {
    "must_have": "tr", "must_not_have": "z", "first_letter": "s",
    "sort_order": "Max-Min", "list_len": "10", "min_length": "1", "max_length": "100",
}

COMMON_DENOM_FORM = {
    "min_match_len": "3", "min_match_rate": "0.5", "beg_end_str_char": "|",
    "value_split_char": ",", "user_match_entry": "alpha, alphabet",
    "user_nope_match_entry": "",
}


def test_any_word_post_valid_returns_200(client):
    assert client.post("/any_word", data=ANY_WORD_FORM).status_code == 200


@pytest.mark.parametrize("field", ["list_len", "min_length", "max_length"])
def test_any_word_post_junk_numeric_returns_400(client, field):
    resp = client.post("/any_word", data={**ANY_WORD_FORM, field: "25AND 1=1"})
    assert resp.status_code == 400


def test_common_denominator_post_valid_returns_200(client):
    assert client.post("/common_denominator", data=COMMON_DENOM_FORM).status_code == 200


@pytest.mark.parametrize("field", ["min_match_len", "min_match_rate"])
def test_common_denominator_post_junk_numeric_returns_400(client, field):
    resp = client.post("/common_denominator", data={**COMMON_DENOM_FORM, field: "abc'--"})
    assert resp.status_code == 400


def test_espresso_baseline_junk_dose_returns_400(client):
    resp = client.post("/espresso/baseline/", data={"roast": "Medium", "dose": "2)--"})
    assert resp.status_code == 400


def test_espresso_explore_oversized_grid_returns_400(client):
    # The grid cap rejects a hostile huge-range/tiny-step request before the
    # meshgrid (and before the Google Sheets pull, which keeps this hermetic).
    resp = client.post("/espresso/explore/", data={
        "distance_grind_min": "0", "distance_grind_max": "1000000",
        "distance_grind_granularity": "0.001",
    })
    assert resp.status_code == 400


def test_espresso_explore_oversized_total_grid_returns_400(client):
    # Each axis alone is a modest 100 steps, but the *product* is 10^6 cells:
    # the budget is on the total, not per-dimension.
    resp = client.post("/espresso/explore/", data={
        "distance_grind_min": "0", "distance_grind_max": "99",
        "distance_grind_granularity": "1",
        "distance_coffee_g_min": "0", "distance_coffee_g_max": "99",
        "distance_coffee_g_granularity": "1",
        "distance_espresso_g_min": "0", "distance_espresso_g_max": "99",
        "distance_espresso_g_granularity": "1",
    })
    assert resp.status_code == 400


def test_espresso_explore_overflowing_ratio_returns_400(client):
    # Finite inputs whose range/granularity ratio overflows float to inf:
    # the budget check must reject this as too large, not crash on it.
    resp = client.post("/espresso/explore/", data={
        "distance_grind_min": "0", "distance_grind_max": "1e308",
        "distance_grind_granularity": "1e-300",
    })
    assert resp.status_code == 400


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
def test_espresso_explore_non_finite_returns_400(client, bad):
    # nan compares False against every guard (gran <= 0, mx < mn, step caps),
    # so parse_float must reject non-finite values outright.
    resp = client.post("/espresso/explore/", data={"distance_grind_min": bad})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Client-fault HTTP errors must keep their status code: the catch-all
# Exception handler used to convert 400/405/415 into logged-traceback 500s.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/fixer", "/any_word", "/feedback"])
def test_missing_form_fields_return_400_not_500(client, path):
    assert client.post(path, data={}).status_code == 400


def test_wrong_method_returns_405_not_500(client):
    assert client.delete("/wordle").status_code == 405


def test_wordiply_non_json_body_returns_415(client):
    resp = client.post("/wordiply", data="x=1",
                       content_type="application/x-www-form-urlencoded")
    assert resp.status_code == 415


def test_wordiply_json_null_body_returns_200(client):
    # A literal `null` JSON body falls back to the default (empty) search.
    resp = client.post("/wordiply", data="null", content_type="application/json")
    assert resp.status_code == 200


def test_wordiply_non_string_search_returns_400(client):
    assert client.post("/wordiply", json={"search_string": 123}).status_code == 400


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
