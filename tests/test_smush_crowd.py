"""Smush crowd word corrections and /smush_admin (routes/smush.py, crowd.py).

Every database call goes through a scripted fake: secret_pass points at the
production JawsDB, so nothing here may reach a real connection.
"""

import json
import re

import pytest

from data import words
from test_blossom import _ScriptedDB, _admin_headers

# The board test_routes.py solves: gold L plus eight outer letters.
CENTER, OUTER = "l", "mguoaecf"
PUZZLE_TAG = "l:acefgmou"
LISTED = "flagellum"            # on that board, and in the word list
NOT_LISTED = "glumac"           # fits that board, but is not a word


def _votes(players, today=0, invalid_today=1):
    """Answers for the counts the vote path makes (see test_blossom._votes)."""
    return {
        "WHERE voter_hash = %s AND created_at": (today,),
        "WHERE voter_hash = %s AND vote = 'invalid'": (invalid_today,),
        "FROM smush_word_votes v WHERE v.word": (players,),
    }


@pytest.fixture
def smush_db(monkeypatch):
    import crowd
    import routes.smush as smush

    # Start each test with no word list cached, so the fake builds it.
    monkeypatch.setattr(smush, "_smush_words", {"value": None, "expires": 0.0})

    def install(answers=None, rows=None, rowcount=1):
        db = _ScriptedDB(answers, rows, rowcount)
        monkeypatch.setattr(smush, "db_cursor", db)
        monkeypatch.setattr(crowd, "db_cursor", db)
        return db

    return install


def _vote(client, action, word, center=CENTER, outer=OUTER):
    return client.post("/smush", json={
        "action": action, "word": word, "center": center, "outer": outer})


def test_the_board_fixtures_are_what_they_claim():
    assert LISTED in words
    assert NOT_LISTED not in words


def test_reject_records_a_vote_for_this_board(client, smush_db):
    db = smush_db(_votes(players=1))
    resp = _vote(client, "reject", LISTED)
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "success"}
    [(_sql, params)] = db.statements("INSERT INTO smush_word_votes")
    assert params[0] == LISTED and params[1] == "invalid" and params[3] == PUZZLE_TAG


def test_one_player_short_changes_nothing(client, smush_db):
    from crowd import CROWD_REMOVE_VOTES
    db = smush_db(_votes(players=CROWD_REMOVE_VOTES - 1))
    _vote(client, "reject", LISTED)
    assert not db.statements("INSERT INTO smush_invalid_words")


def test_the_deciding_player_removes_the_word(client, smush_db):
    import routes.smush as smush
    from crowd import CROWD_REMOVE_VOTES
    db = smush_db(_votes(players=CROWD_REMOVE_VOTES))
    resp = _vote(client, "reject", LISTED)
    # Exactly the reply any other vote gets.
    assert resp.get_json() == {"status": "success"}
    [(sql, params)] = db.statements("INSERT INTO smush_invalid_words")
    assert "'crowd'" in sql and params[0] == LISTED
    assert ("DELETE FROM smush_word_votes WHERE word = %s AND vote = %s",
            (LISTED, "missing")) in db.executed
    # The next solve re-reads the corrections rather than serving the memo.
    assert smush._smush_words["expires"] == 0.0


def test_the_crowd_never_reverses_your_own_entry(client, smush_db):
    answers = _votes(players=9)
    answers["SELECT source FROM smush_added_words"] = ("admin",)
    db = smush_db(answers)
    _vote(client, "reject", LISTED)
    assert not db.statements("INSERT INTO smush_invalid_words")


def test_restoring_the_deciding_vote_puts_the_word_back(client, smush_db):
    from crowd import CROWD_REMOVE_VOTES
    db = smush_db(_votes(players=CROWD_REMOVE_VOTES - 1))   # what's left once this vote is gone
    assert _vote(client, "restore", LISTED).get_json() == {"status": "success"}
    assert db.statements("DELETE FROM smush_word_votes WHERE word = %s AND vote = %s AND voter_hash")
    assert db.statements("DELETE FROM smush_invalid_words WHERE word = %s AND source = 'crowd'")


def test_restoring_a_vote_that_never_counted_changes_nothing(client, smush_db):
    db = smush_db(_votes(players=0), rowcount=0)     # no vote of theirs to delete
    _vote(client, "restore", LISTED)
    assert not db.statements("DELETE FROM smush_invalid_words")


@pytest.mark.parametrize("word", [
    "zebra",        # z, b, r aren't on this board
    "mug",          # no gold L
    NOT_LISTED,     # fits, but we never offered it
])
def test_only_a_listed_word_on_this_board_gets_a_vote(client, smush_db, word):
    db = smush_db(_votes(players=99))
    assert _vote(client, "reject", word).get_json() == {"status": "success"}
    assert not db.statements("INSERT INTO smush_word_votes")


def test_a_player_crossing_words_off_does_not_count(client, smush_db):
    # ✗ also re-plans the All 8 view, so some players will use it to steer.
    from crowd import CROWD_MAX_INVALID_PER_DAY
    db = smush_db(_votes(players=99, invalid_today=CROWD_MAX_INVALID_PER_DAY + 1))
    _vote(client, "reject", LISTED)
    assert db.statements("INSERT INTO smush_word_votes")
    assert not db.statements("INSERT INTO smush_invalid_words")


def test_votes_past_the_daily_cap_are_ignored(client, smush_db):
    from crowd import CROWD_MAX_VOTES_PER_DAY
    db = smush_db(_votes(players=99, today=CROWD_MAX_VOTES_PER_DAY))
    _vote(client, "reject", LISTED)
    assert not db.statements("INSERT INTO smush_word_votes")


@pytest.mark.parametrize("word, players, expected, adds", [
    ("zebra", 0, "not_in_puzzle", False),
    ("mug", 0, "not_in_puzzle", False),         # no gold L
    (LISTED, 0, "already_listed", False),
    (NOT_LISTED, 1, "recorded", False),
    # The vote that adds the word gets the same reply as one that doesn't.
    (NOT_LISTED, 2, "recorded", True),
])
def test_suggest_missing_outcomes(client, smush_db, word, players, expected, adds):
    db = smush_db(_votes(players=players))
    resp = _vote(client, "suggest_missing", word)
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "success", "result": expected}
    assert bool(db.statements("INSERT INTO smush_added_words")) == adds


def test_a_word_you_removed_is_never_added_back_by_players(client, smush_db):
    answers = _votes(players=9)
    answers["SELECT source FROM smush_invalid_words"] = ("admin",)
    db = smush_db(answers)
    assert _vote(client, "suggest_missing", NOT_LISTED).get_json()["result"] == "recorded"
    assert not db.statements("INSERT INTO smush_added_words")


def test_smush_votes_never_touch_blossom_tables(client, smush_db):
    db = smush_db(_votes(players=99))
    _vote(client, "reject", LISTED)
    _vote(client, "restore", LISTED)
    _vote(client, "suggest_missing", NOT_LISTED)
    assert db.executed
    assert not [sql for sql, _p in db.executed if "blossom" in sql]


@pytest.mark.parametrize("payload", [
    {"action": "reject", "word": LISTED, "center": "l", "outer": "mguoaec"},     # 7 outer
    {"action": "reject", "word": LISTED, "center": "l", "outer": "mguoaecl"},    # center repeated
    {"action": "reject", "word": LISTED, "center": "l", "outer": "mguoaecc"},    # duplicate outer
    {"action": "reject", "word": LISTED, "center": 7, "outer": OUTER},
    {"action": "reject", "word": "lm", "center": "l", "outer": OUTER},           # too short
    {"action": "suggest_missing", "word": "fl4g", "center": "l", "outer": OUTER},
    {"action": "suggest_missing", "word": ["flag"], "center": "l", "outer": OUTER},
    {"action": "suggest_missing", "word": "l" * 16, "center": "l", "outer": OUTER},
    {"action": "drop_table", "word": LISTED, "center": "l", "outer": OUTER},
])
def test_malformed_votes_are_a_400_and_touch_nothing(client, smush_db, payload):
    db = smush_db()
    assert client.post("/smush", json=payload).status_code == 400
    assert not db.executed


def test_the_word_list_uses_only_smush_corrections(smush_db):
    from routes.smush import get_smush_words
    db = smush_db(rows={
        "FROM smush_invalid_words": [(LISTED,)],
        "FROM smush_added_words": [(NOT_LISTED,)],
    })
    smush_words = get_smush_words()
    assert LISTED not in smush_words
    assert NOT_LISTED in smush_words
    assert not [sql for sql, _p in db.executed if "blossom" in sql]


def test_no_corrections_means_no_copy_of_the_word_list(smush_db):
    from routes.smush import get_smush_words
    smush_db()
    assert get_smush_words() is words


def test_ribbit_uses_the_plain_dictionary(client, monkeypatch):
    # Smush's and Blossom's corrections are about their own games' lists.
    import routes.wordgames as wordgames

    def no(*_a, **_k):
        raise AssertionError("ribbit read a corrected word list")

    monkeypatch.setattr(wordgames, "get_smush_words", no)
    resp = client.post("/ribbit", json={
        "nodes": [{"id": 1, "letter": "c"}, {"id": 2, "letter": "a"}, {"id": 3, "letter": "t"}],
        "edges": [[1, 2], [2, 3]],
        "min_len": 3,
    })
    assert resp.status_code == 200
    assert "cat" in [r["word"] for r in resp.get_json()["results"]]


def test_the_same_visitor_is_a_different_player_in_each_game(flask_app):
    from routes.blossom import BLOSSOM_CROWD
    from routes.smush import SMUSH_CROWD
    with flask_app.test_request_context("/", environ_base={"REMOTE_ADDR": "198.51.100.7"}):
        assert BLOSSOM_CROWD.voter_hash() != SMUSH_CROWD.voter_hash()


# ---------------------------------------------------------------------------
# /smush_admin
# ---------------------------------------------------------------------------

def test_admin_needs_the_password(client, smush_db):
    smush_db()
    assert client.get("/smush_admin").status_code == 401
    assert client.post("/smush_admin/add_word", data={"word": "zarf"}).status_code == 401
    assert client.post("/smush_admin/remove_word", data={"word": "zarf"}).status_code == 401


def test_admin_page_shows_lists_and_reports(client, smush_db, monkeypatch):
    from datetime import datetime
    when = datetime(2026, 10, 4, 8, 0)
    smush_db(rows={
        "FROM smush_invalid_words": [("glop", when, "crowd"), ("zarf", when, "admin")],
        "FROM smush_word_votes v": [("flagellum", "invalid", 3, when, "waiting", when, "l:acefgmou"),
                                    ("glumac", "missing", 2, when, "added (crowd)", when, "l:acefgmou")],
    })
    resp = client.get("/smush_admin", headers=_admin_headers(monkeypatch))
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert '<td class="by-crowd">crowd</td>' in html
    assert '<td class="by-admin">you</td>' in html
    # An open report gets both buttons; the settled one is hidden by default.
    assert html.count("Remove now") == 1
    assert html.count("Add now") == 1
    assert "added (crowd)" not in html
    assert "1 settled report hidden" in html
    assert "l:acefgmou" in html


def test_admin_page_shows_word_popularity(client, smush_db, monkeypatch):
    from datetime import datetime
    when = datetime(2026, 10, 4, 8, 0)
    monkeypatch.setattr("routes.smush.word_pop", {"glop": 1.23, "flagellum": 2.5})
    smush_db(rows={
        "FROM smush_invalid_words": [("glop", when, "crowd"), ("zarf", when, "admin")],
        "FROM smush_word_votes v": [("flagellum", "invalid", 3, when, "waiting", when, None)],
    })
    html = client.get("/smush_admin", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert "<th>Popularity</th>" in html
    assert '<td class="">2.5</td>' in html
    # Below 2 is what the solver tags rare; a word not in the list says so.
    assert '<td class="pop-rare">1.2</td>' in html
    assert '<td class="pop-none">none</td>' in html


def test_settled_reports_come_back_on_request(client, smush_db, monkeypatch):
    from datetime import datetime
    when = datetime(2026, 10, 4, 8, 0)
    smush_db(rows={
        "FROM smush_word_votes v": [("glumac", "missing", 2, when, "added (crowd)", when, None)],
    })
    resp = client.get("/smush_admin?settled=1", headers=_admin_headers(monkeypatch))
    html = resp.get_data(as_text=True)
    assert "added (crowd)" in html
    assert "Hide them" in html
    assert "Remove now" not in html


def test_admin_page_warns_about_a_word_in_both_lists(client, smush_db, monkeypatch):
    from datetime import datetime
    when = datetime(2026, 10, 4, 8, 0)
    smush_db(rows={
        "FROM smush_invalid_words": [("glop", when, "crowd")],
        "FROM smush_added_words": [("glop", when, "admin")],
    })
    html = client.get("/smush_admin", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert "In both lists: glop" in html


def test_admin_lists_page_and_clamp(client, smush_db, monkeypatch):
    from datetime import datetime
    import crowd
    when = datetime(2026, 10, 4, 8, 0)
    many = [(f"word{i:03d}", when, "crowd") for i in range(250)]
    smush_db(rows={"FROM smush_invalid_words": many})
    html = client.get("/smush_admin?tab=invalid&ipage=2", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert "Page 2 of 3" in html
    assert "<strong>word100</strong>" in html and "<strong>word199</strong>" in html
    assert "<strong>word099</strong>" not in html and "<strong>word200</strong>" not in html
    html = client.get("/smush_admin?tab=invalid&ipage=99", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert "Page 3 of 3" in html
    assert crowd._paginate(many, "junk")[1] == 1


def test_admin_search_filters_every_list(client, smush_db, monkeypatch):
    from datetime import datetime
    when = datetime(2026, 10, 4, 8, 0)
    smush_db(rows={
        "FROM smush_invalid_words": [("glop", when, "crowd"), ("zarf", when, "admin")],
    })
    html = client.get("/smush_admin?q=GL", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert "<strong>glop</strong>" in html
    assert "<strong>zarf</strong>" not in html


def test_admin_form_is_choose_then_submit(client, smush_db, monkeypatch):
    smush_db()
    html = client.get("/smush_admin", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert "<select" not in html
    assert 'data-choice="invalid"' in html and "Remove invalid word" in html
    assert 'data-choice="missing"' in html and "Add missing word" in html
    # Nothing is chosen on load, and Submit stays off until a list is picked.
    assert 'name="word_type" id="word-type" value=""' in html
    assert 'id="word-submit" disabled' in html
    assert 'target="_blank"' in html and "Open Smush Game" in html


def test_admin_add_without_a_choice_changes_nothing(client, smush_db, monkeypatch):
    db = smush_db()
    resp = client.post("/smush_admin/add_word", data={"word": "zarf"},
                       headers=_admin_headers(monkeypatch))
    assert resp.status_code == 302 and "error=Choose" in resp.headers["Location"]
    assert not db.executed


def test_admin_remove_without_a_choice_changes_nothing(client, smush_db, monkeypatch):
    db = smush_db()
    resp = client.post("/smush_admin/remove_word", data={"word": "glop"},
                       headers=_admin_headers(monkeypatch))
    assert resp.status_code == 302 and "error=Choose" in resp.headers["Location"]
    assert not db.executed


def test_your_add_is_final(client, smush_db, monkeypatch):
    import routes.smush as smush
    db = smush_db()
    resp = client.post("/smush_admin/add_word", data={"word": "Zarf", "word_type": "missing"},
                       headers=_admin_headers(monkeypatch))
    assert resp.status_code == 302
    [(sql, _params)] = db.statements("INSERT INTO smush_added_words")
    assert "ON DUPLICATE KEY UPDATE source = 'admin'" in sql
    assert ("DELETE FROM smush_invalid_words WHERE word = %s", ("zarf",)) in db.executed
    assert ("DELETE FROM smush_word_votes WHERE word = %s", ("zarf",)) in db.executed
    assert smush._smush_words["expires"] == 0.0


def test_admin_remove_is_a_clean_undo(client, smush_db, monkeypatch):
    db = smush_db()
    resp = client.post("/smush_admin/remove_word", data={"word": "glop", "word_type": "invalid"},
                       headers=_admin_headers(monkeypatch))
    assert resp.status_code == 302
    assert ("DELETE FROM smush_invalid_words WHERE word = %s", ("glop",)) in db.executed
    assert ("DELETE FROM smush_word_votes WHERE word = %s", ("glop",)) in db.executed


def test_admin_rejects_a_non_word(client, smush_db, monkeypatch):
    db = smush_db()
    resp = client.post("/smush_admin/add_word", data={"word": "drop table", "word_type": "invalid"},
                       headers=_admin_headers(monkeypatch))
    assert resp.status_code == 302 and "error=" in resp.headers["Location"]
    assert not db.executed


# ---------------------------------------------------------------------------
# What players see
# ---------------------------------------------------------------------------

def test_smush_page_ships_the_crowd_hooks(client):
    html = client.get("/smush").get_data(as_text=True)
    for hook in ('id="suggest-box"', 'id="suggest-input"', 'id="suggest-btn"',
                 "Smush accepted a word that isn't listed?",
                 "action: 'reject'", "action: 'restore'", "action: 'suggest_missing'"):
        assert hook in html, f"smush.html lost {hook}"


def test_players_are_never_told_the_vote_counts(client):
    # James's call: knowing how many votes a change takes is the first thing
    # anyone gaming this would want, so the page never says it - not the FAQ,
    # not the toasts or box messages, not a JS constant in the source.
    html = client.get("/smush").get_data(as_text=True)
    lowered = html.lower()
    for hint in ("crowd_remove_votes", "crowd_add_votes", "different players",
                 "another player", "players flag", "players agreed", "for everyone",
                 "5 players", "five players", "two players", "2 players"):
        assert hint not in lowered, f"page hints at the threshold: {hint!r}"
    assert "take every report into account" in html
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    parsed = [json.loads(block) for block in blocks]
    faq = next(block for block in parsed if block.get("@type") == "FAQPage")
    assert "take every report into account" in json.dumps(faq)
