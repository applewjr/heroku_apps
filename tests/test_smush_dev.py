"""/smush_dev (routes/smush_dev.py): James's private ICE COLD planner page.

Every database call goes through a scripted fake: secret_pass points at the
production JawsDB, so nothing here may reach a real connection. The shared
full-board table never really builds here - its thread is captured, not run.
"""

import pytest

from crowd import CROWD_MAX_INVALID_PER_DAY
from data import words
from functions import smush_ice_cold as ice
from test_blossom import _ScriptedDB, _admin_headers

# The /smush test board: gold L plus eight outer letters.
CENTER = "l"
TAG = "l:acefgmou"
FULL = dict.fromkeys("mguoaecf", 5)
# One use of each letter: 256 states, small enough to solve per request.
LATE = dict.fromkeys("mguoaecf", 1)


def _body(outer_uses, **extra):
    body = {"center": CENTER, "outer_uses": outer_uses, "spicy": "",
            "first_word": False, "plan": True, "ice_cold": True}
    body.update(extra)
    return body


class _Dev:
    def __init__(self, client, headers, jobs, db, module):
        self.client, self.headers, self.jobs, self.db, self.module = client, headers, jobs, db, module

    def post(self, body, headers=None):
        return self.client.post("/smush_dev", json=body,
                                headers=self.headers if headers is None else headers)


@pytest.fixture
def dev(client, monkeypatch):
    import routes.smush_dev as smush_dev
    import routes.wordgames as wordgames

    # Hermetic like smush_client: the committed word list, no DB curation.
    monkeypatch.setattr(wordgames, "get_smush_words", lambda: words)
    monkeypatch.setattr(smush_dev, "get_smush_words", lambda: words)
    db = _ScriptedDB()
    monkeypatch.setattr(smush_dev, "db_cursor", db)
    jobs = []
    monkeypatch.setattr(smush_dev, "TABLES", ice.TableCache(spawn=jobs.append))
    monkeypatch.setattr(smush_dev, "EXACT", ice.ExactCache())
    monkeypatch.setattr(smush_dev, "_vote_counts", {})
    monkeypatch.setattr(smush_dev, "_board_results", {})
    monkeypatch.setattr(smush_dev, "_played", {"day": None, "boards": {}})
    return _Dev(client, _admin_headers(monkeypatch), jobs, db, smush_dev)


def test_the_page_and_its_api_need_the_admin_password(dev):
    assert dev.client.get("/smush_dev").status_code == 401
    assert dev.post(_body(LATE), headers={}).status_code == 401


def test_the_page_renders_and_talks_to_its_own_route(dev):
    resp = dev.client.get("/smush_dev", headers=dev.headers)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "const API = '/smush_dev'" in html
    assert 'id="ice-cold-toggle"' in html and "DEV" in html
    # its own save, so a game here never overwrites the one on /smush
    assert "smushDevState_v1" in html


def test_a_late_game_is_solved_from_the_players_own_words(dev):
    resp = dev.post(_body(LATE, spicy="m", played=["flame"]))
    assert resp.status_code == 200
    body = resp.get_json()
    plan = body["plan"]
    assert plan["mode"] == "ice_cold" and plan["source"] == "exact"
    assert plan["status"] in ("ready", "impossible")
    assert dev.jobs == []                     # no full-board build needed
    # the list only offers safe words: no spicy M, no pangram
    assert body["results"] and all("m" not in r["cost"] and not r["pangram"] for r in body["results"])
    # this page adds its own points-per-use figure; /smush's rows don't carry one
    assert all(r["efficiency"] == round(r["pts"] / sum(r["cost"].values()), 1) for r in body["results"])
    for w in plan["next"]:
        assert "m" not in w["cost"] and w["word"] != "flame"


def test_the_full_board_starts_one_build_and_says_so(dev):
    first = dev.post(_body(FULL, spicy="g")).get_json()["plan"]
    assert first["status"] == "building" and first["eta"] > 0
    dev.post(_body(FULL, spicy="g"))
    assert len(dev.jobs) == 1                 # one thread, however many requests


def test_ice_cold_warms_the_table_before_all_8_is_opened(dev):
    resp = dev.post(_body(FULL, plan=False))
    assert resp.get_json()["plan"] is None
    assert len(dev.jobs) == 1


def test_played_words_count_as_accepted_for_the_board(dev):
    dev.post(_body(LATE, played=["flame"], plan=False))
    assert "flame" in dev.module.played_words(TAG)


def test_x_votes_are_read_once_per_board(dev, monkeypatch):
    db = _ScriptedDB(rows={"FROM smush_word_votes v": [("glum", 2)]})
    monkeypatch.setattr(dev.module, "db_cursor", db)
    dev.post(_body(LATE, spicy="m"))
    dev.post(_body(LATE, spicy="g"))
    [(sql, params)] = db.statements("FROM smush_word_votes v")
    assert sql.startswith("SELECT")
    assert params[-1] == CROWD_MAX_INVALID_PER_DAY
    assert dev.module.invalid_vote_counts(TAG, ["glum"]) == {"glum": 2}


def test_a_database_blip_just_means_no_x_evidence(dev, monkeypatch):
    def broken():
        raise RuntimeError("JawsDB is napping")
    monkeypatch.setattr(dev.module, "db_cursor", broken)
    resp = dev.post(_body(LATE, spicy="m"))
    assert resp.status_code == 200
    assert resp.get_json()["plan"]["mode"] == "ice_cold"


def test_votes_go_to_the_crowd_list(dev, monkeypatch):
    import routes.smush as smush
    seen = []

    def record(data):
        seen.append(data)
        return {"status": "success"}

    monkeypatch.setattr(smush, "handle_vote", record)
    resp = dev.post({"action": "reject", "word": "glum", "center": CENTER, "outer": "mguoaecf"})
    assert resp.status_code == 200
    assert seen and seen[0]["word"] == "glum"


def test_without_ice_cold_the_plan_is_the_one_smush_gives(dev):
    body = {"center": "l", "spicy": "g", "first_word": False, "plan": True,
            "outer_uses": {"m": 3, "g": 2, "u": 1, "o": 1, "a": 1, "e": 1, "c": 3, "f": 4}}
    ours = dev.post(dict(body, ice_cold=False)).get_json()
    theirs = dev.client.post("/smush", json=body).get_json()
    assert [r["word"] for r in ours["plan"]["words"]] == [r["word"] for r in theirs["plan"]["words"]]
    assert ours["plan"]["leftover"] == theirs["plan"]["leftover"]
    assert [r["word"] for r in ours["results"]] == [r["word"] for r in theirs["results"]]


@pytest.mark.parametrize("patch", [
    {"center": "ab"},
    {"outer_uses": {"m": 1}},
    {"ice_cold": "yes"},
    {"plan": 1},
    {"played": "flame"},
])
def test_junk_input_returns_400(dev, patch):
    assert dev.post(dict(_body(LATE), **patch)).status_code == 400


@pytest.mark.parametrize("spicy, uses, expected", [
    ("m", LATE, "m"),                         # tapped, alive
    ("", LATE, "unknown"),                    # not tapped since the last play
    ("", dict(dict.fromkeys("mguoaecf", 0), m=2), None),   # one tile left: the spice-free turn
    ("m", dict(dict.fromkeys("mguoaecf", 0), g=2), None),  # a dead tile can't be spicy
])
def test_planning_spice(dev, spicy, uses, expected):
    letters = tuple(sorted(uses))
    L = tuple(uses[l] for l in letters)
    got = dev.module.planning_spice(spicy, letters, L)
    assert (letters[got] if isinstance(got, int) else got) == expected
