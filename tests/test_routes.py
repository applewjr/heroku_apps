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
    "/smush",
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

# /any_word is now a JSON live-search endpoint (like /wordiply).
ANY_WORD_JSON = {
    "starts_with": "pre", "ends_with": "tion", "contains": "",
    "pattern": "", "contains_letters": "a", "excludes_letters": "z",
    "sort_order": "Max-Min", "min_length": "1", "max_length": "100",
}

COMMON_DENOM_FORM = {
    "min_match_len": "3", "min_match_rate": "0.5", "beg_end_str_char": "|",
    "value_split_char": ",", "user_match_entry": "alpha, alphabet",
    "user_nope_match_entry": "",
}


def test_any_word_post_valid_returns_json(client):
    resp = client.post("/any_word", json=ANY_WORD_JSON)
    assert resp.status_code == 200
    body = resp.get_json()
    assert "results" in body and "total" in body
    assert isinstance(body["results"], list)
    # each match is enriched with word / popularity / scrabble-score
    for item in body["results"]:
        assert set(item) == {"w", "p", "s"}
        assert isinstance(item["w"], str)
        assert isinstance(item["p"], (int, float))
        assert isinstance(item["s"], int)


@pytest.mark.parametrize("field", ["min_length", "max_length"])
def test_any_word_post_junk_numeric_returns_400(client, field):
    resp = client.post("/any_word", json={**ANY_WORD_JSON, field: "25AND 1=1"})
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

@pytest.mark.parametrize("path", ["/fixer", "/feedback"])
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


def test_any_word_non_json_body_returns_415(client):
    resp = client.post("/any_word", data="x=1",
                       content_type="application/x-www-form-urlencoded")
    assert resp.status_code == 415


def test_any_word_json_null_body_returns_200(client):
    # A literal `null` JSON body falls back to the default (no-filter) search.
    resp = client.post("/any_word", data="null", content_type="application/json")
    assert resp.status_code == 200


def test_wordiply_non_string_search_returns_400(client):
    assert client.post("/wordiply", json={"search_string": 123}).status_code == 400


SMUSH_BODY = {
    "center": "l",
    "outer_uses": {"m": 3, "g": 2, "u": 1, "o": 1, "a": 1, "e": 1, "c": 3, "f": 4},
    "spicy": "g",
    "first_word": False,
}


@pytest.fixture
def smush_client(client, monkeypatch):
    # Hermetic like blossom_client: skip the DB-backed word curation and run
    # the solver on the committed in-memory list.
    import routes.wordgames as wordgames
    from data import words
    monkeypatch.setattr(wordgames, "get_smush_words", lambda: words)
    return client


def test_smush_post_returns_ranked_words(smush_client):
    resp = smush_client.post("/smush", json=SMUSH_BODY)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total_playable"] > 0
    assert body["pangram_status"] in ("found", "affordable", "out_of_reach", "none")
    pts = [r["pts"] for r in body["results"]]
    assert pts == sorted(pts, reverse=True)
    assert all("l" in r["word"] for r in body["results"])


_SMUSH_OUTER = SMUSH_BODY["outer_uses"]
_SMUSH_OUTER_MINUS_M = {k: v for k, v in _SMUSH_OUTER.items() if k != "m"}


@pytest.mark.parametrize("patch", [
    {"center": ""},                    # missing center
    {"center": "ab"},                  # too long
    {"center": "1"},                   # not a letter
    {"center": "é"},                   # non-ASCII letter
    {"outer_uses": {}},                # no outer letters
    {"outer_uses": _SMUSH_OUTER_MINUS_M},                  # only 7 letters
    {"outer_uses": {**_SMUSH_OUTER, "m": "x"}},            # junk uses value
    {"outer_uses": {**_SMUSH_OUTER, "m": 9}},              # uses out of range
    {"outer_uses": {**_SMUSH_OUTER, "m": True}},           # bool uses value
    {"outer_uses": {**_SMUSH_OUTER_MINUS_M, "mm": 3}},     # multi-char letter key
    {"outer_uses": {**_SMUSH_OUTER_MINUS_M, "G": 1}},      # dup letter after case-fold
    {"outer_uses": {**_SMUSH_OUTER_MINUS_M, "l": 1}},      # center repeated as outer
    {"spicy": "xy"},                   # junk spicy
    {"first_word": "false"},           # first_word must be a real boolean
    {"first_word": 1},                 # truthy non-boolean
    {"rejected": "flagellum"},         # rejected must be a list
    {"rejected": [123]},               # rejected entries must be words
    {"rejected": ["not a word!"]},     # non-alpha rejected entry
    {"played": "floccule"},            # played must be a list
    {"played": [123]},                 # played entries must be words
    {"plan": "true"},                  # plan must be a real boolean
    {"plan": 1},                       # truthy non-boolean plan
])
def test_smush_junk_input_returns_400(smush_client, patch):
    body = dict(SMUSH_BODY, **patch)
    assert smush_client.post("/smush", json=body).status_code == 400


def test_smush_rejected_words_are_hidden(smush_client):
    resp = smush_client.post("/smush", json=dict(SMUSH_BODY, rejected=["flagellum"]))
    assert resp.status_code == 200
    body = resp.get_json()
    assert all(r["word"] != "flagellum" for r in body["results"])


def test_smush_played_words_cannot_be_played_twice(smush_client):
    resp = smush_client.post("/smush", json=dict(SMUSH_BODY, played=["flagellum"]))
    assert resp.status_code == 200
    body = resp.get_json()
    assert all(r["word"] != "flagellum" for r in body["results"])


def test_smush_first_word_yields_to_a_non_empty_pile(smush_client):
    # first_word=True alongside played words is contradictory; the pile wins,
    # so the x5 first-word pangram multiplier can't be requested mid-game.
    body = dict(SMUSH_BODY, outer_uses=dict.fromkeys("mguoaecf", 5),
                spicy="", first_word=True, played=["mole"])
    resp = smush_client.post("/smush", json=body)
    assert resp.status_code == 200
    pangram = next(r for r in resp.get_json()["results"] if r["pangram"])
    assert pangram["mult"] == 3  # 1 + pangram +2, not the first-word +4


def test_smush_plan_defaults_to_null(smush_client):
    resp = smush_client.post("/smush", json=SMUSH_BODY)
    assert resp.status_code == 200
    assert resp.get_json()["plan"] is None


def test_smush_plan_flag_returns_a_reconciled_plan(smush_client):
    resp = smush_client.post("/smush", json=dict(SMUSH_BODY, plan=True))
    assert resp.status_code == 200
    plan = resp.get_json()["plan"]
    assert isinstance(plan["complete"], bool)
    plan_words = [r["word"] for r in plan["words"]]
    assert len(set(plan_words)) == len(plan_words)
    assert all("l" in w for w in plan_words)
    spent = {}
    for r in plan["words"]:
        for l, n in r["cost"].items():
            spent[l] = spent.get(l, 0) + n
    # every starting use is either spent by the plan or reported stranded
    for l, u in SMUSH_BODY["outer_uses"].items():
        assert spent.get(l, 0) + plan["leftover"].get(l, 0) == u
    assert plan["complete"] == (plan["leftover"] == {})


def test_smush_non_json_body_returns_415(client):
    resp = client.post("/smush", data="x=1",
                       content_type="application/x-www-form-urlencoded")
    assert resp.status_code == 415


def test_smush_json_null_body_returns_400(client):
    resp = client.post("/smush", data="null", content_type="application/json")
    assert resp.status_code == 400


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
