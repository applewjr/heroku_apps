"""Smoke tests: each key route should respond (not 500).

These catch the failures that take a whole page down after a deploy - broken
imports, missing templates, blueprint registration mistakes, renamed Jinja
variables - without needing the database.
"""

import re
from datetime import date

import pytest

# Pages that render straight from code/static data (no DB required).
GET_OK = [
    "/",
    "/wordle",
    "/antiwordle",
    "/quordle",
    "/wordiply",
    "/smush",
    "/ribbit",
    "/guess_the_phrase",
    "/chainle",
    "/circuits",
    "/typeshift",
    "/4x3",
    "/4x6",
    "/firsts",
    "/any_word",
    "/fixer",
    "/blossom",
    "/umbra",
    "/tiltconnect4",
    "/kintsugi",
    "/thread",
    "/dogs",
    "/about",
    "/privacy-policy",
    "/feedback",
    "/blossom_feedback",
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


def _new_until(monkeypatch, day):
    import helpers
    monkeypatch.setattr(helpers, "NEW_UNTIL", {"/chainle": day, "/guess_the_phrase": day})


# The heading of the solver cross-links (templates/_related_solvers.html).
STRIP = "More daily puzzle solvers"


def test_the_solver_links_run_biggest_first_with_new_tags(client, monkeypatch):
    # The cards keep one order, the games players most likely know first
    # (James, 2026-10-23), and the plain line runs busiest first, Guess The
    # Phrase last (2026-11-22); the NEW tags still mark the newest two,
    # cards or not.
    _new_until(monkeypatch, date(9999, 12, 31))
    html = client.get("/smush").get_data(as_text=True)
    links = html.split(STRIP)[1]
    spots = [links.index(f'href="{path}"')
             for path in ("/blossom", "/chainle", "/ribbit", "/wordiply", "/wordle", "/guess_the_phrase")]
    assert spots == sorted(spots)
    assert html.count(">NEW</span>") == 2
    assert client.get("/").get_data(as_text=True).count(">NEW</span>") == 2


def test_the_new_tag_drops_off_by_itself(client, monkeypatch):
    _new_until(monkeypatch, date(2020, 1, 1))
    for path in ("/smush", "/"):
        assert ">NEW</span>" not in client.get(path).get_data(as_text=True), path


def test_wordle_wears_a_grey_old_tag(client, monkeypatch):
    # James's joke counterpart to the NEW tags. Permanent: nothing expires.
    _new_until(monkeypatch, date(9999, 12, 31))
    for path in ("/smush", "/"):
        html = client.get(path).get_data(as_text=True)
        assert html.count(">OLD</span>") == 1, path
        assert html.count(">NEW</span>") == 2, path
    html = client.get("/smush").get_data(as_text=True)
    links = html.split(STRIP)[1]
    assert re.search(r'href="/wordle"[^>]*>Wordle<span [^>]*jj-more-old[^>]*>OLD</span>', links)
    assert ".jj-more-old { background: #6b7280;" in html
    # The Wordle page leaves itself out of its own solver links.
    assert ">OLD</span>" not in client.get("/wordle").get_data(as_text=True)


def test_unknown_path_returns_404(client):
    assert client.get("/this-page-does-not-exist").status_code == 404


def test_protected_dashboard_requires_auth(client):
    # No credentials -> 401, returned before any DB access.
    assert client.get("/etl_dash/1").status_code == 401


def _needs_password(view):
    """Is this view wrapped by extensions.auth.login_required? Its wrapper
    closes over the auth object; walk the functools.wraps chain to it."""
    from extensions import auth
    fn = view
    while fn is not None:
        for cell in getattr(fn, "__closure__", None) or ():
            try:
                if cell.cell_contents is auth:
                    return True
            except ValueError:      # an empty cell
                pass
        fn = getattr(fn, "__wrapped__", None)
    return False


def test_jj_admin_needs_the_password(client):
    assert client.get("/jj_admin").status_code == 401


def test_jj_admin_links_every_password_page(client, flask_app, monkeypatch):
    # So a new admin page that isn't linked from /jj_admin fails here.
    from data import etl_dash_queries
    from test_blossom import _admin_headers
    html = client.get("/jj_admin", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    rounds = sorted({d["round"] for d in etl_dash_queries.values() if "round" in d})
    # /espresso only redirects to its home page, which is the link.
    aliases = {"/espresso": "/espresso/home/"}
    pages = set()
    for rule in flask_app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.rule == "/jj_admin":
            continue
        if not _needs_password(flask_app.view_functions[rule.endpoint]):
            continue
        if rule.arguments == {"round"}:
            pages |= {rule.rule.replace("<int:round>", str(r)) for r in rounds}
        else:
            assert not rule.arguments, f"no way to link {rule.rule}"
            pages.add(aliases.get(rule.rule, rule.rule))
    # The walk itself must keep finding them: every admin page, each ETL
    # round and the espresso pages.
    assert {"/smush_admin", "/ribbit_admin",
            "/blossom_admin", "/chainle_admin", "/guess_the_phrase_admin", "/circuits_admin",
            "/typeshift_admin", "/typeshift_admin/board", "/4x3_admin", "/4x3_admin/board",
            "/4x6_admin", "/4x6_admin/board", "/firsts_admin", "/firsts_admin/board",
            "/etl_dash/1", "/espresso/home/"} <= pages
    for path in sorted(pages):
        assert f'href="{path}"' in html, f"/jj_admin doesn't link {path}"


def test_the_dev_pages_are_gone(client):
    # James never used them (removed 2026-11-22).
    for path in ("/smush_dev", "/chainle_dev"):
        assert client.get(path).status_code == 404, path
        # A GET-only catch-all route answers a POST with 405.
        assert client.post(path, json={}).status_code in (404, 405), path


def test_ribbit_clear_board_sits_right_of_give_feedback(client):
    html = client.get("/ribbit").get_data(as_text=True)
    controls = re.search(r'<div class="rb-board-controls">(.*?)</div>', html, re.S).group(1)
    assert 'id="clear-btn"' not in controls and 'id="rotate-btn"' in controls
    assert html.index("💬 Give Feedback") < html.index('id="clear-btn"') < html.index(STRIP)


def test_the_about_page_is_linked_from_the_menu_home_page_and_sitemap(client):
    # Findable but quiet (James, 2026-11-01): the menu on every page, one
    # small line under the home page's cards, and the sitemap.
    assert 'href="/about"' in client.get("/feedback").get_data(as_text=True)
    home = client.get("/").get_data(as_text=True)
    assert re.search(r'class="index-about">[^<]*<a href="/about">About</a>', home)
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    assert "<loc>https://www.jamesapplewhite.com/about</loc>" in sitemap


def test_the_about_page_names_its_author_and_never_hints_at_vote_counts(client):
    import json
    html = client.get("/about").get_data(as_text=True)
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    about = next(json.loads(block) for block in blocks if '"AboutPage"' in block)
    person = about["mainEntity"]
    assert person["@type"] == "Person" and person["name"] == "James Applewhite"
    assert set(person["sameAs"]) == {"https://www.linkedin.com/in/j-applewhite/",
                                     "https://github.com/applewjr"}
    # In the page's own text: the menu links both on every page anyway.
    prose = html.split('<div class="jj-prose">', 1)[1]
    for path in ("/privacy-policy", "/feedback"):
        assert f'href="{path}"' in prose, path
    # The rule every solver page follows (test_players_are_never_told_the_vote_counts).
    lowered = html.lower()
    for hint in ("crowd_remove_votes", "crowd_add_votes", "different players",
                 "another player", "players flag", "players agreed", "for everyone",
                 "5 players", "five players", "two players", "2 players"):
        assert hint not in lowered, f"page hints at the threshold: {hint!r}"


def test_the_privacy_policy_uses_the_site_layout(client):
    # A bare fragment until 2026-11-02: no menu, no title and no viewport
    # tag, so phones showed it zoomed out.
    html = client.get("/privacy-policy").get_data(as_text=True)
    assert '<nav class="jj-navbar">' in html
    assert "<title>Privacy Policy - JJ Apps</title>" in html
    assert 'name="viewport"' in html
    assert '<div class="jj-prose">' in html


# ---------------------------------------------------------------------------
# Junk input (scanner probes) in numeric form fields -> clean 400, never a 500.
# ---------------------------------------------------------------------------

# /any_word is now a JSON live-search endpoint (like /wordiply).
ANY_WORD_JSON = {
    "starts_with": "pre", "ends_with": "tion", "contains": "",
    "pattern": "", "contains_letters": "a", "excludes_letters": "z",
    "sort_order": "Max-Min", "min_length": "1", "max_length": "100",
}


def test_antiwordle_post_returns_picks(client):
    # the page posts every box; one played row, all gray
    boxes = [{"letter": letter, "position": str(pos), "color": "1", "row": "1"}
             for pos, letter in enumerate("IMMIX", 1)]
    resp = client.post("/antiwordle", json={"wordle_data": boxes})
    assert resp.status_code == 200
    body = resp.get_json()
    assert re.fullmatch(r"Pick 1: [a-z]{5}", body["final_out1"])
    assert body["final_out_end"].startswith("Options remaining: ")


def test_quordle_post_returns_picks(client):
    # the page posts every box; one played row, all gray on every puzzle
    boxes = [{"letter": letter, "position": str(pos), "color": "1", "row": "1", "puzzle": str(p)}
             for p in range(1, 5) for pos, letter in enumerate("TARES", 1)]
    for mode, puzzle2 in (("quordle", "Options remaining: "), ("sequence", "Not open yet"),
                          ("bogus", "Options remaining: ")):
        resp = client.post("/quordle", json={"quordle_data": boxes, "mode": mode})
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body) == 30
        assert re.fullmatch(r"Pick 1: [a-z]{5}", body["final_out_all_1"])
        assert re.fullmatch(r"Pick 1: [a-z]{5}", body["final_out1_1"])
        assert body["final_out_end2"].startswith(puzzle2)


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


def test_common_denominator_is_gone(client):
    # Decommissioned 2026-11-25 (James): a plain 404, and nothing links to it.
    assert client.get("/common_denominator").status_code == 404
    # The menu is on every page; the cards are on the home page.
    for page in ("/", "/any_word"):
        assert "common_denominator" not in client.get(page).get_data(as_text=True)


ESPRESSO_PAGES = ["/espresso", "/espresso/home/", "/espresso/recommendation/",
                  "/espresso/plot/", "/espresso/explore/", "/espresso/baseline/"]


@pytest.fixture
def espresso_client(client, monkeypatch):
    """The test client, logged in with the admin password."""
    import base64
    import config
    monkeypatch.setattr(config, "GOOGLE_FORM_PASS", "test-pass")
    token = base64.b64encode(b"james:test-pass").decode()
    client.environ_base["HTTP_AUTHORIZATION"] = f"Basic {token}"
    return client


@pytest.mark.parametrize("path", ESPRESSO_PAGES)
def test_espresso_needs_the_admin_password(client, monkeypatch, path):
    # The heavy work (Sheets pull, scikit-learn, matplotlib) must be
    # unreachable without the password: a 401 before any of it runs.
    import routes.espresso as esp

    def explode():
        raise AssertionError("an unauthenticated request reached the Sheets pull")

    monkeypatch.setattr(esp, "get_espresso_data", explode)
    assert client.get(path).status_code == 401
    # 405 for /espresso, which only redirects: refused before any code runs.
    assert client.post(path, data={"roast": "Medium", "dose": "2"}).status_code in (401, 405)


def test_espresso_rejects_a_wrong_password(client, monkeypatch):
    import base64
    import config
    monkeypatch.setattr(config, "GOOGLE_FORM_PASS", "test-pass")
    token = base64.b64encode(b"james:wrong").decode()
    resp = client.get("/espresso/baseline/", headers={"Authorization": f"Basic {token}"})
    assert resp.status_code == 401


def test_espresso_pages_open_with_the_password(espresso_client):
    for path in ESPRESSO_PAGES:
        resp = espresso_client.get(path)
        assert resp.status_code in (200, 302, 308), (path, resp.status_code)


def test_espresso_baseline_junk_dose_returns_400(espresso_client):
    resp = espresso_client.post("/espresso/baseline/", data={"roast": "Medium", "dose": "2)--"})
    assert resp.status_code == 400


def test_espresso_explore_oversized_grid_returns_400(espresso_client):
    # The grid cap rejects a hostile huge-range/tiny-step request before the
    # meshgrid (and before the Google Sheets pull, which keeps this hermetic).
    resp = espresso_client.post("/espresso/explore/", data={
        "distance_grind_min": "0", "distance_grind_max": "1000000",
        "distance_grind_granularity": "0.001",
    })
    assert resp.status_code == 400


def test_espresso_explore_oversized_total_grid_returns_400(espresso_client):
    # Each axis alone is a modest 100 steps, but the *product* is 10^6 cells:
    # the budget is on the total, not per-dimension.
    resp = espresso_client.post("/espresso/explore/", data={
        "distance_grind_min": "0", "distance_grind_max": "99",
        "distance_grind_granularity": "1",
        "distance_coffee_g_min": "0", "distance_coffee_g_max": "99",
        "distance_coffee_g_granularity": "1",
        "distance_espresso_g_min": "0", "distance_espresso_g_max": "99",
        "distance_espresso_g_granularity": "1",
    })
    assert resp.status_code == 400


def test_espresso_explore_overflowing_ratio_returns_400(espresso_client):
    # Finite inputs whose range/granularity ratio overflows float to inf:
    # the budget check must reject this as too large, not crash on it.
    resp = espresso_client.post("/espresso/explore/", data={
        "distance_grind_min": "0", "distance_grind_max": "1e308",
        "distance_grind_granularity": "1e-300",
    })
    assert resp.status_code == 400


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
def test_espresso_explore_non_finite_returns_400(espresso_client, bad):
    # nan compares False against every guard (gran <= 0, mx < mn, step caps),
    # so parse_float must reject non-finite values outright.
    resp = espresso_client.post("/espresso/explore/", data={"distance_grind_min": bad})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Client-fault HTTP errors must keep their status code: the catch-all
# Exception handler used to convert 400/405/415 into logged-traceback 500s.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/fixer", "/feedback", "/blossom_feedback"])
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


def test_smush_page_ships_the_played_word_entry_box(smush_client):
    # Players resume games mid-board, and the words they already used are
    # often past the 400-word display cutoff. The type-it-in box is their only
    # way to record those, so a rename that breaks its wiring should fail here.
    html = smush_client.get("/smush").get_data(as_text=True)
    for hook in ('id="board-tools"', 'id="we-toggle"', 'id="word-entry"',
                 'id="we-input"', 'id="we-add"', 'id="we-status"'):
        assert hook in html, f"smush.html lost {hook}"
    # The panel is collapsed until its toggle is tapped: the strip between the
    # board and the word list must stay clear on arrival.
    assert 'class="word-entry" id="word-entry"' in html, "entry panel starts expanded"


def test_smush_lists_show_rows_in_steps_but_all8_stays_whole(smush_client):
    # A sorted list shows WORDS_FIRST rows, and every reset (new results, a
    # new sort) goes back to that; Show more adds WORDS_STEP. The All 8 plan
    # is the exception, always shown whole: renderPlan never reads the count.
    import re
    html = smush_client.get("/smush").get_data(as_text=True)
    assert "const WORDS_FIRST = 25;" in html
    assert "const WORDS_STEP = 25;" in html
    resets = re.findall(r"visibleCount = (\w+);", html)
    assert resets and set(resets) == {"WORDS_FIRST"}
    assert "visibleCount += WORDS_STEP;" in html
    plan_fn = html.split("function renderPlan()")[1].split("\n    function ")[0]
    assert "visibleCount" not in plan_fn


def test_smush_accepts_a_played_word_we_do_not_suggest(smush_client):
    # The entry box takes any board-legal word, so the played pile can carry
    # words missing from our dictionary; the route must still solve.
    body = dict(SMUSH_BODY, outer_uses=dict.fromkeys("mguoaecf", 5),
                played=["flumagoce"])
    resp = smush_client.post("/smush", json=body)
    assert resp.status_code == 200
    assert resp.get_json()["total_playable"] > 0


def test_smush_non_json_body_returns_415(client):
    resp = client.post("/smush", data="x=1",
                       content_type="application/x-www-form-urlencoded")
    assert resp.status_code == 415


def test_smush_json_null_body_returns_400(client):
    resp = client.post("/smush", data="null", content_type="application/json")
    assert resp.status_code == 400


# ---------- Ribbit ----------

# Five tiles in a corner of the 4x4 grid, every neighbour linked: plenty of
# common 4-5 letter words. Cells 0 1 2 are the top row, 4 and 5 under 0 and 1.
_RIBBIT_LETTERS = {0: "a", 1: "r", 2: "t", 4: "e", 5: "s"}
RIBBIT_BODY = {
    "nodes": [{"id": i, "letter": l} for i, l in _RIBBIT_LETTERS.items()],
    "edges": [[0, 1], [0, 4], [0, 5], [1, 2], [1, 4], [1, 5], [2, 5], [4, 5]],
}


@pytest.fixture
def ribbit_client(client, monkeypatch):
    # Ribbit's corrections live in the DB (routes/ribbit.py); solve on the
    # committed word list so nothing reaches a real connection.
    from data import words
    monkeypatch.setattr("routes.ribbit.get_ribbit_words", lambda: words)
    return client


def test_ribbit_post_returns_traceable_words(ribbit_client):
    resp = ribbit_client.post("/ribbit", json=RIBBIT_BODY)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total_playable"] > 0
    lengths = [r["len"] for r in body["results"]]
    assert lengths == sorted(lengths, reverse=True)  # longest first by default
    for r in body["results"]:
        assert r["len"] >= 4
        assert set(r["word"]) <= set(_RIBBIT_LETTERS.values())
        # the representative path must spell the word on distinct, real tiles
        assert len(r["path"]) == len(r["word"])
        assert len(set(r["path"])) == len(r["path"])
        assert [_RIBBIT_LETTERS[i] for i in r["path"]] == list(r["word"])


def test_ribbit_disabled_tile_drops_its_words(ribbit_client):
    full = ribbit_client.post("/ribbit", json=RIBBIT_BODY).get_json()
    # disable the S tile (id 5): no remaining word may contain an s
    resp = ribbit_client.post("/ribbit", json=dict(RIBBIT_BODY, disabled=[5]))
    body = resp.get_json()
    assert body["total_playable"] < full["total_playable"]
    assert all("s" not in r["word"] for r in body["results"])


def test_ribbit_played_and_rejected_are_hidden(ribbit_client):
    body = ribbit_client.post("/ribbit", json=RIBBIT_BODY).get_json()
    a_word = body["results"][0]["word"]
    other = body["results"][-1]["word"]
    resp = ribbit_client.post("/ribbit", json=dict(RIBBIT_BODY, played=[a_word], rejected=[other]))
    words_out = {r["word"] for r in resp.get_json()["results"]}
    assert a_word not in words_out
    assert other not in words_out


def test_ribbit_no_edges_yields_nothing(ribbit_client):
    resp = ribbit_client.post("/ribbit", json=dict(RIBBIT_BODY, edges=[]))
    assert resp.status_code == 200
    assert resp.get_json()["total_playable"] == 0


@pytest.mark.parametrize("patch", [
    {"nodes": []},                                             # no tiles
    {"nodes": [{"id": 1, "letter": "ab"}]},                    # multi-char letter
    {"nodes": [{"id": 1, "letter": "1"}]},                     # not a letter
    {"nodes": [{"id": 1, "letter": "a"}, {"id": 1, "letter": "b"}]},  # dup id
    {"edges": [[1, 99]]},                                      # edge to missing tile
    {"edges": [[1, 2, 3]]},                                    # edge not a pair
    {"disabled": [99]},                                        # disable missing tile
], ids=["empty", "multichar", "nonletter", "dupid", "badedge", "triedge", "baddisable"])
def test_ribbit_junk_input_returns_400(ribbit_client, patch):
    body = dict(RIBBIT_BODY, **patch)
    assert ribbit_client.post("/ribbit", json=body).status_code == 400


def _ribbit_grid(cells=16, letter="e"):
    """`cells` tiles numbered from 0, each linked to the next."""
    return {"nodes": [{"id": i, "letter": letter} for i in range(cells)],
            "edges": [[i, i + 1] for i in range(cells - 1)]}


@pytest.mark.parametrize("patch", [
    _ribbit_grid(64),                                            # more tiles than the grid has
    _ribbit_grid(17),
    {"nodes": [{"id": 16, "letter": "a"}], "edges": []},         # off the grid
    {"nodes": [{"id": -1, "letter": "a"}], "edges": []},
    {"edges": [[0, 2]]},                                         # two cells apart
    {"edges": [[4, 2]]},                                         # a knight's move
    {"edges": [[0, 0]]},                                         # a tile with itself
], ids=["64tiles", "17tiles", "offgrid", "negative", "apart", "knight", "itself"])
def test_ribbit_solves_only_a_board_the_page_can_draw(ribbit_client, monkeypatch, patch):
    from functions import all_words

    def no_search(*a, **k):
        raise AssertionError("a board off the grid must never reach the solver")

    monkeypatch.setattr(all_words, "ribbit_solver", no_search)
    body = dict(RIBBIT_BODY, **patch)
    assert ribbit_client.post("/ribbit", json=body).status_code == 400


def test_ribbit_row_ends_are_not_neighbours(ribbit_client):
    # Cells 3 and 4 are next to each other in number only: the end of the
    # first row and the start of the second.
    body = dict(RIBBIT_BODY, nodes=[{"id": 3, "letter": "a"}, {"id": 4, "letter": "r"}])
    assert ribbit_client.post("/ribbit", json=dict(body, edges=[[3, 4]])).status_code == 400
    body = dict(RIBBIT_BODY, nodes=[{"id": 3, "letter": "a"}, {"id": 6, "letter": "r"}])
    assert ribbit_client.post("/ribbit", json=dict(body, edges=[[3, 6]])).status_code == 200


def test_ribbit_solver_stops_at_its_whole_solve_budget(monkeypatch):
    # The per-word cap alone lets every word in the dictionary spend its own.
    from functions import all_words
    spent = []
    real = all_words._ribbit_trace

    def counting(word, node_letter, adj, starts, step_budget):
        result = real(word, node_letter, adj, starts, step_budget)
        spent.append(result[2])
        return result

    monkeypatch.setattr(all_words, "_ribbit_trace", counting)
    n = 24
    nodes = {i: "es"[i % 2] for i in range(n)}
    edges = [(a, b) for a in range(n) for b in range(a + 1, n)]
    made_up = ["es" * 7 + "e", "se" * 7 + "s", "ee" + "se" * 6, "ss" + "es" * 6]
    all_words.ribbit_solver(nodes, edges, made_up, solve_budget=5000)
    assert len(spent) == 1                  # the first word used it all up
    assert sum(spent) <= 5001
    # A word that is cut short is simply not listed; a normal board is unaffected.
    spent.clear()
    results, total = all_words.ribbit_solver(
        {0: "r", 1: "e", 4: "s", 5: "t"}, [(0, 1), (1, 4), (4, 5)], ["rest", "tser"])
    assert [r["word"] for r in results] == ["rest", "tser"] and total == 2


def test_ribbit_non_json_body_returns_415(client):
    resp = client.post("/ribbit", data="x=1",
                       content_type="application/x-www-form-urlencoded")
    assert resp.status_code == 415


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


##### admin login: wrong passwords are capped per visitor #####

def _basic(password):
    import base64
    return {"Authorization": "Basic " + base64.b64encode(f"james:{password}".encode()).decode()}


def _through_cloudflare(visitor):
    # Heroku appends the edge (inside Cloudflare's 162.158.0.0/15); the
    # visitor is in CF-Connecting-IP. See test_client_ip in test_blossom.py.
    return {"X-Forwarded-For": f"{visitor}, 162.158.10.20", "CF-Connecting-IP": visitor}


@pytest.fixture
def login_cap(monkeypatch):
    """extensions.ADMIN_LOGIN_FAILURE_LIMITS switched on, with no counts
    either side of the test."""
    import config
    from extensions import limiter
    monkeypatch.setattr(config, "GOOGLE_FORM_PASS", "test-pass")
    limiter.enabled = True
    limiter.reset()
    yield
    limiter.enabled = False
    limiter.reset()


def test_wrong_passwords_lock_out_that_visitor(client, login_cap):
    scanner = _through_cloudflare("198.51.100.7")
    codes = [client.get("/espresso", headers={**scanner, **_basic(f"guess{i}")}).status_code
             for i in range(10)]
    assert codes == [401] * 10
    # Past the cap even the right password is refused, so guessing on learns nothing.
    assert client.get("/espresso", headers={**scanner, **_basic("test-pass")}).status_code == 401
    # Keyed on the visitor, not the shared Cloudflare edge: James still gets in.
    james = _through_cloudflare("203.0.113.5")
    assert client.get("/espresso", headers={**james, **_basic("test-pass")}).status_code == 302


def test_the_lockout_follows_a_network_not_one_address(client, login_cap):
    # An IPv6 visitor can pick a new address for every request, so the count
    # is per /48: here ten guesses from ten different /64s of one.
    for i in range(10):
        guesser = _through_cloudflare(f"2001:db8:77:{i:x}::{i + 1}")
        assert client.get("/espresso", headers={**guesser, **_basic(f"guess{i}")}).status_code == 401
    fresh = _through_cloudflare("2001:db8:77:ffff::1")
    assert client.get("/espresso", headers={**fresh, **_basic("test-pass")}).status_code == 401
    # Another network is not theirs.
    james = _through_cloudflare("2001:db8:78::5")
    assert client.get("/espresso", headers={**james, **_basic("test-pass")}).status_code == 302


def test_the_login_prompt_is_not_a_wrong_password(client, login_cap):
    # A browser's first request carries no credentials; that's what brings up
    # the prompt, so any number of them leaves the cap untouched.
    for _ in range(15):
        assert client.get("/espresso").status_code == 401
        assert client.get("/espresso", headers=_basic("")).status_code == 401
    assert client.get("/espresso", headers=_basic("test-pass")).status_code == 302


def test_a_few_typos_dont_lock_anyone_out(client, login_cap):
    for i in range(3):
        assert client.get("/espresso", headers=_basic(f"typo{i}")).status_code == 401
    assert client.get("/espresso", headers=_basic("test-pass")).status_code == 302


def test_an_unset_password_opens_nothing(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "GOOGLE_FORM_PASS", None)
    for password in ("", "None"):
        assert client.get("/espresso", headers=_basic(password)).status_code == 401


def test_validate_password_needs_the_login(client, monkeypatch):
    # Otherwise it would test guesses past the cap.
    import config
    monkeypatch.setattr(config, "GOOGLE_FORM_PASS", "test-pass")
    monkeypatch.setattr(config, "GOOGLE_FORM_URL", "https://example.com/form")
    form = {"password": "test-pass"}
    assert client.post("/validate_password", data=form).status_code == 401
    resp = client.post("/validate_password", data=form, headers=_basic("test-pass"))
    assert resp.status_code == 302
    assert resp.headers["Location"] == "https://example.com/form"
    resp = client.post("/validate_password", data={"password": "nope"},
                       headers=_basic("test-pass"))
    assert resp.status_code == 302
    assert resp.headers["Location"] != "https://example.com/form"


##### rate limits: each visitor has their own budget #####

@pytest.fixture
def limits_on(flask_app):
    """The route limits switched on, with no counts either side of the test."""
    from extensions import limiter
    limiter.enabled = True
    limiter.reset()
    yield
    limiter.enabled = False
    limiter.reset()


def _spend_404_budget(client, visitor):
    """Ask for a page that isn't there until NOT_FOUND_LIMITS says no."""
    headers = _through_cloudflare(visitor)
    codes = [client.get("/no-such-page", headers=headers).status_code for _ in range(61)]
    assert codes == [404] * 60 + [429]


def test_one_visitor_cannot_spend_everyone_elses_budget(client, limits_on):
    # Both come through the same Cloudflare edge, which is what the limits
    # counted until 2026-11-25.
    _spend_404_budget(client, "198.51.100.7")
    neighbour = _through_cloudflare("198.51.100.8")
    assert client.get("/no-such-page", headers=neighbour).status_code == 404


def test_one_home_is_one_visitor_however_many_addresses_it_has(client, limits_on):
    _spend_404_budget(client, "2001:db8:1:100::1")
    # Another address, and another /64 of the same /56: the same budget.
    for same_home in ("2001:db8:1:100::2", "2001:db8:1:1ff::9"):
        assert client.get("/no-such-page", headers=_through_cloudflare(same_home)).status_code == 429
    # The next /56 along is someone else.
    other = _through_cloudflare("2001:db8:1:200::1")
    assert client.get("/no-such-page", headers=other).status_code == 404


def test_a_made_up_visitor_header_changes_nothing(client, limits_on):
    # Straight to the herokuapp.com address there is no Cloudflare edge, so
    # CF-Connecting-IP is whatever the caller typed: the hop itself counts.
    def ask(claimed):
        return client.get("/no-such-page", headers={
            "X-Forwarded-For": "203.0.113.9", "CF-Connecting-IP": claimed}).status_code
    codes = [ask(f"198.51.100.{i}") for i in range(61)]
    assert codes == [404] * 60 + [429]


def test_a_visitor_whose_address_cannot_be_read_still_has_a_limit(flask_app):
    # An empty key would switch flask-limiter's limits off for that request.
    from extensions import visitor_key
    with flask_app.test_request_context("/", headers={"X-Forwarded-For": "not-an-address"},
                                        environ_base={"REMOTE_ADDR": "10.0.0.9"}):
        assert visitor_key() == "10.0.0.9"


##### feedback forms: bots are dropped, a person never loses a message #####

import contextlib
import importlib
import re

import helpers
from monitoring import alerts

# The two public forms that share helpers.bot_reason: path, route module, the
# name of its token salt there, the fields a person fills in, and the name of
# its flood alert.
FEEDBACK_FORMS = {
    "feedback": ("/feedback", "routes.misc", "FEEDBACK_TOKEN_SALT",
                 {"feedback_header": "a topic", "feedback_body": "the body"},
                 "feedback_flood"),
    "blossom": ("/blossom_feedback", "routes.blossom", "BLOSSOM_FEEDBACK_TOKEN_SALT",
                {"report_type": "missing", "feedback_body": "yatagan"},
                "blossom_feedback_flood"),
}


def _stub_feedback_db(monkeypatch, form="feedback", counts=(0, 0), count_fails=False,
                      insert_fails=False):
    """Capture the INSERT instead of writing to the live table, and answer
    the flood check's count with `counts` (rows in the last day, last hour).

    secret_pass.py points at production, and both forms write inline rather
    than through the write-behind queue, so an unstubbed POST here would put
    a real row in the real table.
    """
    module = importlib.import_module(FEEDBACK_FORMS[form][1])
    written = []

    class Cursor:
        def execute(self, sql, params=None):
            if "INSERT" in sql:
                if insert_fails:
                    raise RuntimeError("the table is gone")
                written.append(params)
            elif count_fails:
                raise RuntimeError("scripted failure")
        def fetchone(self):
            return counts
        def close(self):
            pass

    class Conn:
        def commit(self):
            pass

    @contextlib.contextmanager
    def fake_cursor():
        yield Conn(), Cursor()

    monkeypatch.setattr(module, "db_cursor", fake_cursor)
    return written


def _salt(form):
    _path, module, salt_name, _typed, _flood = FEEDBACK_FORMS[form]
    return getattr(importlib.import_module(module), salt_name)


def _post_feedback(client, flask_app, form="feedback", **fields):
    """Post a form the way a person does - a token from 30 seconds ago, an
    empty honeypot, a URL referrer - with `fields` overriding any of it."""
    path, _module, _salt_name, typed, _flood = FEEDBACK_FORMS[form]
    data = dict(typed, referrer="https://example.com/", feedback_extra="",
                feedback_token=_token_aged(flask_app, 30, _salt(form)))
    data.update(fields)
    return client.post(path, data=data)


@pytest.mark.parametrize("form", FEEDBACK_FORMS)
def test_the_form_carries_a_hidden_honeypot(client, form):
    html = client.get(FEEDBACK_FORMS[form][0]).get_data(as_text=True)
    assert 'name="feedback_extra"' in html
    # Off-screen rather than display:none or type=hidden, both of which
    # anything reading the DOM skips trivially.
    assert 'tabindex="-1"' in html
    assert 'aria-hidden="true"' in html
    # A field a password manager would fill for a real visitor would flag a
    # human as a bot, so the name has to stay semantically meaningless.
    for autofillable in ('name="name"', 'name="email"', 'name="url"',
                         'name="website"', 'name="phone"'):
        assert autofillable not in html


@pytest.mark.parametrize("form", FEEDBACK_FORMS)
def test_the_form_carries_a_signed_token(client, form):
    html = client.get(FEEDBACK_FORMS[form][0]).get_data(as_text=True)
    assert 'name="feedback_token"' in html
    # Signed, not a bare timestamp - an unsigned field is one a bot backdates.
    assert 'value=""' not in html.split('name="feedback_token"')[1][:40]


def test_feedback_notifies_on_a_real_submission(client, flask_app, monkeypatch, capsys):
    written = _stub_feedback_db(monkeypatch)
    alerts.reset_throttle()
    assert _post_feedback(client, flask_app).status_code == 200
    assert written == [("https://example.com/", "a topic", "the body")]
    out = capsys.readouterr().out
    assert "JJ_ALERT check=feedback_new" in out
    assert "dropped" not in out


def test_blossom_feedback_is_stored_and_never_emailed(client, flask_app, monkeypatch, capsys):
    # James, 2026-10-13: no email per report; they're on /blossom_admin.
    import smtplib

    def no_email(*args, **kwargs):
        raise AssertionError("Blossom feedback must not send email")

    monkeypatch.setattr(smtplib, "SMTP", no_email)
    written = _stub_feedback_db(monkeypatch, "blossom")
    alerts.reset_throttle()
    assert _post_feedback(client, flask_app, "blossom").status_code == 200
    assert written == [("https://example.com/", "missing", "yatagan")]
    out = capsys.readouterr().out
    assert "JJ_ALERT" not in out and "dropped" not in out


def test_a_field_is_cut_to_what_its_column_holds(client, flask_app, monkeypatch):
    written = _stub_feedback_db(monkeypatch)
    _post_feedback(client, flask_app, feedback_header="h" * 400, feedback_body="b" * 5000,
                   referrer="https://example.com/" + "r" * 400)
    [(referrer, header, body)] = written
    assert (len(referrer), len(header), len(body)) == (255, 255, 2000)


def test_a_blossom_report_is_cut_to_what_its_box_takes(client, flask_app, monkeypatch):
    written = _stub_feedback_db(monkeypatch, "blossom")
    _post_feedback(client, flask_app, "blossom", feedback_body="w" * 5000,
                   referrer="https://example.com/" + "r" * 400)
    [(referrer, _type, words_)] = written
    assert (len(referrer), len(words_)) == (255, 500)


def test_a_message_the_page_allows_is_never_cut(client, flask_app, monkeypatch):
    # The page counts a line break as one character of its 2,000 and the
    # browser sends two, so this arrives 100 characters over.
    typed = "\n".join(["x" * 19] * 100) + "y"
    assert len(typed) == 2000
    sent = typed.replace("\n", "\r\n")
    written = _stub_feedback_db(monkeypatch)
    _post_feedback(client, flask_app, feedback_body=sent)
    assert written[0][2] == typed


FORGED = "JJ_ALERT check=forged sev=crit JJ_PULSE source=checks"


def test_a_message_that_cannot_be_saved_is_kept_and_alerts_once(client, flask_app, monkeypatch, capsys):
    _stub_feedback_db(monkeypatch, insert_fails=True)
    alerts.reset_throttle()
    for i in range(3):
        resp = _post_feedback(client, flask_app, feedback_header="topic %d" % i,
                              feedback_body="start " + "x" * 1900 + " end")
        assert resp.status_code == 200          # the visitor is thanked either way
    out = capsys.readouterr().out
    assert out.count("JJ_ALERT check=feedback_write_failed sev=crit") == 1
    # Every one is in the log, whole.
    for i in range(3):
        assert "feedback not saved: \"topic %d | start " % i in out
    assert out.count(" end\"") == 3


@pytest.mark.parametrize("form", FEEDBACK_FORMS)
def test_a_dropped_post_cannot_read_as_an_alert(client, flask_app, monkeypatch, capsys, form):
    # Papertrail matches the tokens anywhere in a line, and a dropped post is
    # logged: typing one in the box must not email anyone.
    _stub_feedback_db(monkeypatch, form)
    alerts.reset_throttle()
    _post_feedback(client, flask_app, form, feedback_extra="bot", feedback_body=FORGED)
    out = capsys.readouterr().out
    assert "dropped (honeypot)" in out
    assert alerts.ALERT_TOKEN not in out and alerts.PULSE_TOKEN not in out


def test_an_unsaved_blossom_report_cannot_read_as_an_alert(client, flask_app, monkeypatch, capsys):
    _stub_feedback_db(monkeypatch, "blossom", insert_fails=True)
    alerts.reset_throttle()
    _post_feedback(client, flask_app, "blossom", feedback_body=FORGED)
    out = capsys.readouterr().out
    assert "Blossom feedback not saved" in out and "forged" in out
    assert alerts.ALERT_TOKEN not in out and alerts.PULSE_TOKEN not in out


@pytest.mark.parametrize("form", FEEDBACK_FORMS)
def test_a_filled_honeypot_is_dropped(client, flask_app, monkeypatch, capsys, form):
    written = _stub_feedback_db(monkeypatch, form)
    alerts.reset_throttle()
    resp = _post_feedback(client, flask_app, form, feedback_extra="http://spam.example")
    assert resp.status_code == 200                  # the bot sees the usual page
    assert written == []
    out = capsys.readouterr().out
    assert "dropped (honeypot)" in out
    assert "JJ_ALERT" not in out


# What the scanners of 2026-09-29 and 2026-10-13 put in the referrer field.
@pytest.mark.parametrize("referrer", ["1", "1'", '1")', "98766", "1('\",).abcd"])
@pytest.mark.parametrize("form", FEEDBACK_FORMS)
def test_a_junk_referrer_is_dropped(client, flask_app, monkeypatch, capsys, form, referrer):
    written = _stub_feedback_db(monkeypatch, form)
    resp = _post_feedback(client, flask_app, form, referrer=referrer)
    assert resp.status_code == 200
    assert written == []
    assert "dropped (referrer)" in capsys.readouterr().out


@pytest.mark.parametrize("form", FEEDBACK_FORMS)
def test_an_empty_referrer_and_honeypot_are_a_person(client, flask_app, monkeypatch, form):
    # A direct visit has no referrer, and a browser posts the honeypot empty.
    written = _stub_feedback_db(monkeypatch, form)
    _post_feedback(client, flask_app, form, referrer="", feedback_extra="   ")
    assert len(written) == 1


def test_a_form_without_the_honeypot_field_still_works(client, flask_app, monkeypatch):
    # Reading the honeypot with [] rather than .get() would 400 a page that
    # has no such field.
    written = _stub_feedback_db(monkeypatch)
    data = {"feedback_header": "t", "feedback_body": "b", "referrer": "",
            "feedback_token": _token_aged(flask_app, 30)}
    assert client.post("/feedback", data=data).status_code == 200
    assert len(written) == 1


@pytest.mark.parametrize("form", FEEDBACK_FORMS)
def test_an_instant_submission_is_dropped(client, flask_app, monkeypatch, capsys, form):
    written = _stub_feedback_db(monkeypatch, form)
    resp = _post_feedback(client, flask_app, form,
                          feedback_token=_token_aged(flask_app, 0, _salt(form)))
    assert resp.status_code == 200
    assert written == []
    assert "dropped (too fast)" in capsys.readouterr().out


def test_blossom_drops_a_report_type_its_page_cannot_send(client, flask_app, monkeypatch, capsys):
    written = _stub_feedback_db(monkeypatch, "blossom")
    _post_feedback(client, flask_app, "blossom", report_type="")
    assert written == []
    assert "dropped (report type)" in capsys.readouterr().out


@pytest.mark.parametrize("token", ["", "not-a-real-token", "abc.def.ghi", "expired"])
@pytest.mark.parametrize("form", FEEDBACK_FORMS)
def test_a_stale_token_shows_the_form_again(client, flask_app, monkeypatch, capsys, form, token):
    # An old copy of the page, a rotated secret or a tab left open past a
    # day is probably a person. Nothing is stored and nothing is lost: the
    # form comes back with what they typed and a token that works.
    if token == "expired":
        token = _token_aged(flask_app, helpers.FORM_TOKEN_MAX_AGE + 60, _salt(form))
    written = _stub_feedback_db(monkeypatch, form)
    resp = _post_feedback(client, flask_app, form, feedback_token=token,
                          feedback_body="my unsent words")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert written == []
    assert "press Submit" in html
    assert "my unsent words" in html
    assert 'value="https://example.com/"' in html     # where they came from, kept
    assert "dropped" not in capsys.readouterr().out


def test_the_reshown_form_keeps_the_report_type(client, flask_app, monkeypatch):
    _stub_feedback_db(monkeypatch, "blossom")
    html = _post_feedback(client, flask_app, "blossom", feedback_token="").get_data(as_text=True)
    assert re.search(r'value="missing"[^>]*\bchecked\b', html)
    assert not re.search(r'value="invalid"[^>]*\bchecked\b', html)


@pytest.mark.parametrize("form", FEEDBACK_FORMS)
def test_a_reshown_form_can_be_sent_straight_away(client, flask_app, monkeypatch, form):
    # Its visitor typed their message already, so its token is never "too
    # fast" - and it is the token the re-shown page really carries.
    written = _stub_feedback_db(monkeypatch, form)
    html = _post_feedback(client, flask_app, form, feedback_token="").get_data(as_text=True)
    token = html.split('name="feedback_token" value="')[1].split('"')[0]
    _post_feedback(client, flask_app, form, feedback_token=token)
    assert len(written) == 1


@pytest.mark.parametrize("counts", [(5, helpers.FEEDBACK_MAX_PER_HOUR),
                                    (helpers.FEEDBACK_MAX_PER_DAY, 0)])
@pytest.mark.parametrize("form", FEEDBACK_FORMS)
def test_a_flood_is_capped(client, flask_app, monkeypatch, capsys, form, counts):
    # The bound that holds even for a bot that passes every other check.
    written = _stub_feedback_db(monkeypatch, form, counts=counts)
    alerts.reset_throttle()
    assert _post_feedback(client, flask_app, form).status_code == 200
    assert written == []
    out = capsys.readouterr().out
    assert "dropped (flood)" in out
    assert f"JJ_ALERT check={FEEDBACK_FORMS[form][4]}" in out


def test_under_the_flood_cap_a_person_is_stored(client, flask_app, monkeypatch):
    written = _stub_feedback_db(monkeypatch, counts=(helpers.FEEDBACK_MAX_PER_DAY - 1,
                                                     helpers.FEEDBACK_MAX_PER_HOUR - 1))
    _post_feedback(client, flask_app)
    assert len(written) == 1


def test_a_flood_check_that_fails_lets_a_person_through(client, flask_app, monkeypatch, capsys):
    written = _stub_feedback_db(monkeypatch, count_fails=True)
    _post_feedback(client, flask_app)
    assert len(written) == 1
    assert "flood check failed" in capsys.readouterr().out


def test_a_flood_cannot_fill_the_inbox(client, flask_app, monkeypatch, capsys):
    # Even posts that pass every check cannot turn the form into a hundred
    # emails: feedback_new is throttled.
    _stub_feedback_db(monkeypatch)
    alerts.reset_throttle()
    for i in range(5):
        _post_feedback(client, flask_app, feedback_header="flood %d" % i)
    out = capsys.readouterr().out
    assert out.count("JJ_ALERT check=feedback_new") == 1
    assert out.count("feedback alert throttled") == 4


@pytest.mark.parametrize("form", FEEDBACK_FORMS)
def test_one_visitor_is_rate_limited(client, flask_app, monkeypatch, form):
    from extensions import limiter
    _stub_feedback_db(monkeypatch, form)
    limiter.enabled = True
    limiter.reset()
    try:
        codes = [_post_feedback(client, flask_app, form).status_code for _ in range(4)]
        # Viewing the page is not posting it.
        page = client.get(FEEDBACK_FORMS[form][0]).status_code
    finally:
        limiter.enabled = False
        limiter.reset()
    assert codes == [200, 200, 200, 429]
    assert page == 200


##### validation: scanner junk must be a 400, never a 500 #####

import time

from helpers import ValidationError, form_token_state, issue_form_token, parse_choice, parse_letters


def test_parse_letters_takes_a_to_z():
    assert parse_letters(' Train ', 'word') == 'Train'
    assert parse_letters(None, 'word') == parse_letters('  ', 'word') == ''


# isalpha() is true of every one of the first five: an accent, Cyrillic, the
# Kelvin sign (whose lower() is a plain k), a ligature and a fullwidth letter.
@pytest.mark.parametrize("value", ["caf\u00e9", "\u0442\u0435\u0430", "\u212a", "\ufb01n", "\uff41b",
                                   "tr4in", "a b", "a-b"])
def test_parse_letters_refuses_anything_else(value):
    with pytest.raises(ValidationError):
        parse_letters(value, 'word')


def test_parse_letters_caps_the_length():
    assert parse_letters('abc', 'word', max_len=3) == 'abc'
    with pytest.raises(ValidationError):
        parse_letters('abcd', 'word', max_len=3)


def test_parse_choice_accepts_a_listed_option():
    assert parse_choice('Dark', 'roast', ['Light', 'Dark'], default='Light') == 'Dark'


def test_parse_choice_defaults_on_blank():
    for blank in (None, '', '   '):
        assert parse_choice(blank, 'roast', ['Light', 'Dark'], default='Light') == 'Light'


def test_parse_choice_rejects_anything_else():
    with pytest.raises(ValidationError):
        parse_choice('98766', 'roast', ['Light', 'Dark'], default='Light')


def test_parse_choice_does_not_substitute_a_default_for_junk():
    # Unlike parse_int's clamping, a choice has no nearest valid neighbour.
    # Silently falling back would answer a question the visitor did not ask.
    with pytest.raises(ValidationError):
        parse_choice("1'", 'roast', ['Light', 'Dark'], default='Light')


@pytest.mark.parametrize("payload", ["98766", "1'", '1"', "1)", "1')", "1 UNION SELECT 1,2,3"])
def test_espresso_baseline_400s_on_scanner_junk(espresso_client, payload):
    # Regression, 2026-09-29. roast went straight into
    # espresso_points['roast_variable'][roast], so an unrecognised value was a
    # KeyError -> 500. A scanner walking this route produced eight http_500
    # alerts and eight rows in vw_prod_errors, none of them a real fault.
    assert espresso_client.post("/espresso/baseline/",
                       data={"roast": payload, "dose": "2"}).status_code == 400
    assert espresso_client.post("/espresso/baseline/",
                       data={"roast": "Medium", "dose": payload}).status_code == 400


def test_espresso_baseline_still_serves_every_real_option(espresso_client):
    for roast in ["Light", "Medium", "Medium Dark", "Dark"]:
        for dose in ["1", "2", "3"]:
            resp = espresso_client.post("/espresso/baseline/", data={"roast": roast, "dose": dose})
            assert resp.status_code == 200, (roast, dose)


def test_espresso_baseline_get_uses_the_defaults(espresso_client):
    assert espresso_client.get("/espresso/baseline/").status_code == 200


def test_espresso_plot_400s_before_it_reaches_google_sheets(espresso_client, monkeypatch):
    # The x/y/z values are DataFrame column names, so an unknown one was a
    # KeyError -> 500 the same way. Validating before get_espresso_data also
    # means junk never triggers the Sheets pull.
    import routes.espresso as esp

    def explode():
        raise AssertionError("junk input must never reach the Sheets pull")

    monkeypatch.setattr(esp, "get_espresso_data", explode)
    resp = espresso_client.post("/espresso/plot/", data={"espresso_x_col": "1' OR '1'='1"})
    assert resp.status_code == 400


##### feedback: the form token #####

def _token_aged(flask_app, seconds, salt=None, resubmit=False):
    """Mint a real form token that looks `seconds` old: /feedback's unless
    another salt is given."""
    from itsdangerous.timed import TimestampSigner
    import routes.misc as misc

    real = TimestampSigner.get_timestamp
    TimestampSigner.get_timestamp = lambda self: int(time.time()) - seconds
    try:
        with flask_app.test_request_context():
            return issue_form_token(salt or misc.FEEDBACK_TOKEN_SALT, resubmit=resubmit)
    finally:
        TimestampSigner.get_timestamp = real


@pytest.mark.parametrize("age, resubmit, expected", [
    (30, False, "ok"),
    (0, False, "too fast"),
    # From the future: clock skew across a restart, refused by itsdangerous,
    # so the form is shown again rather than the message lost.
    (-300, False, "stale"),
    (0, True, "ok"),                # a re-shown form, sent straight back
    (helpers.FORM_TOKEN_MAX_AGE + 60, False, "stale"),
])
def test_form_token_state(flask_app, age, resubmit, expected):
    import routes.misc as misc
    token = _token_aged(flask_app, age, resubmit=resubmit)
    with flask_app.test_request_context():
        assert form_token_state(token, salt=misc.FEEDBACK_TOKEN_SALT) == expected


@pytest.mark.parametrize("token", [None, "", "not-a-real-token", "abc.def.ghi"])
def test_an_unverifiable_token_is_stale(flask_app, token):
    import routes.misc as misc
    with flask_app.test_request_context():
        assert form_token_state(token, salt=misc.FEEDBACK_TOKEN_SALT) == "stale"


def test_a_token_only_works_on_its_own_form(flask_app):
    import routes.blossom as blossom
    token = _token_aged(flask_app, 30)              # minted for /feedback
    with flask_app.test_request_context():
        assert form_token_state(token, salt=blossom.BLOSSOM_FEEDBACK_TOKEN_SALT) == "stale"
