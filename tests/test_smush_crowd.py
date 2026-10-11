"""Smush crowd word corrections and /smush_admin (routes/smush.py, crowd.py).

Every database call goes through a scripted fake: secret_pass points at the
production JawsDB, so nothing here may reach a real connection.
"""

import json
import re
from pathlib import Path

import pytest

from data import words
from test_blossom import _ScriptedDB, _admin_headers

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"

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

    def install(answers=None, rows=None, rowcount=1, fail=None):
        db = _ScriptedDB(answers, rows, rowcount, fail)
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
    from crowd import remove_votes_needed
    db = smush_db(_votes(players=remove_votes_needed(LISTED) - 1))
    _vote(client, "reject", LISTED)
    assert not db.statements("INSERT INTO smush_invalid_words")


def test_the_deciding_player_removes_the_word(client, smush_db):
    import routes.smush as smush
    from crowd import remove_votes_needed
    db = smush_db(_votes(players=remove_votes_needed(LISTED)))
    resp = _vote(client, "reject", LISTED)
    # Exactly the reply any other vote gets.
    assert resp.get_json() == {"status": "success"}
    [(sql, params)] = db.statements("INSERT INTO smush_invalid_words")
    assert "'crowd'" in sql and params[0] == LISTED
    assert ("DELETE FROM smush_word_votes WHERE word = %s AND vote = %s",
            (LISTED, "missing")) in db.executed
    # The next solve re-reads the corrections rather than serving the memo.
    assert smush._smush_words["expires"] == 0.0


@pytest.mark.parametrize("pop, needed", [
    (7.59, 5), (3.3, 5), (3.29, 4), (2.0, 4), (1.99, 3), (1.01, 3), (None, 3),
])
def test_rarer_words_need_fewer_players_to_remove(monkeypatch, pop, needed):
    import crowd
    monkeypatch.setattr(crowd, "word_pop", {} if pop is None else {"zarf": pop})
    assert crowd.remove_votes_needed("zarf") == needed
    assert crowd.CrowdList.threshold("zarf", "invalid") == needed
    # Adding a word doesn't depend on how common it is.
    assert crowd.CrowdList.threshold("zarf", "missing") == crowd.CROWD_ADD_VOTES


def test_the_scale_never_goes_below_three_or_above_five():
    import crowd
    assert crowd.CROWD_REMOVE_VOTES_RARE == 3
    assert crowd.CROWD_REMOVE_VOTES_BY_POP[0][1] == crowd.CROWD_REMOVE_VOTES == 5
    floors = [floor for floor, _ in crowd.CROWD_REMOVE_VOTES_BY_POP]
    assert floors == sorted(floors, reverse=True)


def test_a_rare_word_goes_at_three_players_a_common_one_does_not(client, smush_db, monkeypatch):
    import crowd
    monkeypatch.setattr(crowd, "word_pop", {LISTED: 1.2})
    db = smush_db(_votes(players=3))
    _vote(client, "reject", LISTED)
    assert db.statements("INSERT INTO smush_invalid_words")

    monkeypatch.setattr(crowd, "word_pop", {LISTED: 4.5})
    db = smush_db(_votes(players=4))
    _vote(client, "reject", LISTED)
    assert not db.statements("INSERT INTO smush_invalid_words")


def test_taking_back_a_vote_uses_the_same_scale(client, smush_db, monkeypatch):
    import crowd
    # A rare word removed by 3 stays removed while 3 are left...
    monkeypatch.setattr(crowd, "word_pop", {})
    db = smush_db(_votes(players=3))
    _vote(client, "restore", LISTED)
    assert not db.statements("DELETE FROM smush_invalid_words")
    # ...and goes back at 2.
    db = smush_db(_votes(players=2))
    _vote(client, "restore", LISTED)
    assert db.statements("DELETE FROM smush_invalid_words WHERE word = %s AND source = 'crowd'")


def test_the_crowd_never_reverses_your_own_entry(client, smush_db):
    answers = _votes(players=9)
    answers["SELECT source FROM smush_added_words"] = ("admin",)
    db = smush_db(answers)
    _vote(client, "reject", LISTED)
    assert not db.statements("INSERT INTO smush_invalid_words")


def test_restoring_the_deciding_vote_puts_the_word_back(client, smush_db):
    from crowd import remove_votes_needed
    db = smush_db(_votes(players=remove_votes_needed(LISTED) - 1))   # what's left once this vote is gone
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


def test_admin_needed_column_shows_what_each_word_needs(client, smush_db, monkeypatch):
    from datetime import datetime
    import crowd
    when = datetime(2026, 10, 4, 8, 0)
    monkeypatch.setattr(crowd, "word_pop", {"flagellum": 2.22, "leaf": 4.35})
    smush_db(rows={
        "FROM smush_word_votes v": [("flagellum", "invalid", 2, when, "waiting", when, None),
                                    ("leaf", "invalid", 1, when, "waiting", when, None),
                                    ("glop", "invalid", 1, when, "waiting", when, None),
                                    ("glumac", "missing", 1, when, "waiting", when, None)],
    })
    html = client.get("/smush_admin", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert re.search(r"<td>2</td>\s*<td>4</td>", html)     # flagellum
    assert re.search(r"<td>1</td>\s*<td>5</td>", html)     # leaf
    assert re.search(r"<td>1</td>\s*<td>3</td>", html)     # glop, not in the frequency list
    assert re.search(r"<td>1</td>\s*<td>2</td>", html)     # glumac, a suggestion
    assert "<th>Players</th>" in html and "<th>Needed</th>" in html
    assert "5 at popularity 3.3 or above, 4 at popularity 2.0 or above, 3 below that or none" in html


def test_a_late_vote_on_your_own_change_counts_as_settled(client, smush_db, monkeypatch):
    # A player whose results predate your change can still vote on the word.
    from datetime import datetime
    when = datetime(2026, 10, 10, 7, 28)
    smush_db(rows={
        "FROM smush_word_votes v": [("iodid", "invalid", 1, when, "removed (admin)", when, None),
                                    ("oof", "missing", 1, when, "added (admin)", when, None)],
    })
    html = client.get("/smush_admin", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert "<strong>iodid</strong>" not in html and "<strong>oof</strong>" not in html
    assert "2 settled reports hidden" in html


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
# Vote log: every vote and decision, kept after the live tables move on
# ---------------------------------------------------------------------------

def _vote_from(client, action, word, mode):
    return client.post("/smush", json={
        "action": action, "word": word, "center": CENTER, "outer": OUTER, "mode": mode})


def test_a_vote_is_logged_with_its_board_tab_and_counts(client, smush_db, monkeypatch):
    import crowd
    monkeypatch.setattr(crowd, "word_pop", {LISTED: 4.567})
    db = smush_db(_votes(players=1))
    assert _vote_from(client, "reject", LISTED, "all8").get_json() == {"status": "success"}
    [row] = db.log_rows("smush")
    assert (row["event"], row["word"], row["vote"], row["puzzle"], row["mode"]) == (
        "vote", LISTED, "invalid", PUZZLE_TAG, "all8")
    assert re.fullmatch(r"[0-9a-f]{32}", row["voter_hash"])
    assert (row["players"], row["needed"]) == (1, crowd.remove_votes_needed(LISTED))
    assert row["popularity"] == 4.57 and row["detail"] is None
    # Logged in the vote's own transaction, so the two can't disagree.
    assert db.commits == 1


@pytest.mark.parametrize("rowcount", [2, 0])
def test_a_repeat_vote_is_logged_as_one(client, smush_db, rowcount):
    # The upsert says 2 when it refreshed this player's earlier vote, and 0
    # when a double tap inside the same second changed nothing.
    db = smush_db(_votes(players=1), rowcount=rowcount)
    _vote_from(client, "reject", LISTED, "pts")
    assert [row["event"] for row in db.log_rows("smush")] == ["repeat_vote"]


def test_a_capped_vote_is_logged_though_not_counted(client, smush_db):
    from crowd import CROWD_MAX_VOTES_PER_DAY
    db = smush_db(_votes(players=99, today=CROWD_MAX_VOTES_PER_DAY))
    assert _vote_from(client, "reject", LISTED, "eff").get_json() == {"status": "success"}
    assert not db.statements("INSERT INTO smush_word_votes")
    [row] = db.log_rows("smush")
    assert (row["event"], row["word"], row["vote"], row["puzzle"], row["mode"]) == (
        "capped", LISTED, "invalid", PUZZLE_TAG, "eff")
    assert row["voter_hash"]
    # That path returns early, so the log row needs its own commit.
    assert db.commits == 1


def test_the_deciding_vote_logs_the_change_too(client, smush_db):
    from crowd import remove_votes_needed
    needed = remove_votes_needed(LISTED)
    db = smush_db(_votes(players=needed))
    _vote_from(client, "reject", LISTED, "pts")
    vote, applied = db.log_rows("smush")
    assert vote["event"] == "vote" and vote["players"] == needed
    assert (applied["event"], applied["word"], applied["vote"]) == ("crowd_applied", LISTED, "invalid")
    assert (applied["players"], applied["needed"]) == (needed, needed)
    # A decision, not a player's act. detail: the opposite votes it cleared.
    assert applied["voter_hash"] is None and applied["mode"] is None
    assert applied["detail"] == "1"


def test_restoring_logs_the_retract_and_the_word_going_back(client, smush_db):
    from crowd import remove_votes_needed
    needed = remove_votes_needed(LISTED)
    db = smush_db(_votes(players=needed - 1))       # what's left once this vote is gone
    _vote_from(client, "restore", LISTED, "all8")
    retract, reverted = db.log_rows("smush")
    assert (retract["event"], retract["puzzle"], retract["mode"]) == ("retract", PUZZLE_TAG, "all8")
    assert retract["voter_hash"] and (retract["players"], retract["needed"]) == (needed - 1, needed)
    assert (reverted["event"], reverted["word"], reverted["voter_hash"]) == ("crowd_reverted", LISTED, None)


def test_restoring_while_enough_players_remain_logs_only_the_retract(client, smush_db):
    from crowd import remove_votes_needed
    db = smush_db(_votes(players=remove_votes_needed(LISTED)))
    _vote_from(client, "restore", LISTED, "pts")
    assert [row["event"] for row in db.log_rows("smush")] == ["retract"]


def test_restoring_a_vote_that_never_counted_logs_nothing(client, smush_db):
    db = smush_db(_votes(players=0), rowcount=0)     # no vote of theirs to delete
    _vote_from(client, "restore", LISTED, "pts")
    assert db.log_rows("smush") == []


def test_crossing_off_logs_the_vote_and_what_it_put_back(client, smush_db):
    from crowd import CROWD_MAX_INVALID_PER_DAY, remove_votes_needed
    db = smush_db(
        _votes(players=remove_votes_needed(LISTED) - 1, invalid_today=CROWD_MAX_INVALID_PER_DAY + 1),
        rows={"SELECT word FROM smush_word_votes WHERE voter_hash": [(LISTED,)]},
    )
    _vote_from(client, "reject", LISTED, "all8")
    vote, reverted = db.log_rows("smush")
    assert (vote["event"], vote["detail"], vote["mode"]) == ("vote", "crossing_off", "all8")
    assert vote["players"] is None        # nothing counts on that path
    assert (reverted["event"], reverted["word"], reverted["detail"]) == (
        "crowd_reverted", LISTED, "crossing_off")


@pytest.mark.parametrize("path, event, kind", [
    ("/smush_admin/add_word", "admin_set", "missing"),
    ("/smush_admin/remove_word", "admin_remove", "invalid"),
])
def test_admin_changes_are_logged_with_the_votes_they_cleared(client, smush_db, monkeypatch,
                                                               path, event, kind):
    db = smush_db(rowcount=3)
    resp = client.post(path, data={"word": "Zarf", "word_type": kind},
                       headers=_admin_headers(monkeypatch))
    assert resp.status_code == 302
    [row] = db.log_rows("smush")
    assert (row["event"], row["word"], row["vote"], row["detail"]) == (event, "zarf", kind, "3")
    assert row["voter_hash"] is None and row["mode"] is None


def test_a_failing_log_costs_neither_the_vote_nor_the_change(client, smush_db, capsys):
    from crowd import remove_votes_needed
    from monitoring import alerts
    alerts.reset_throttle()
    db = smush_db(_votes(players=remove_votes_needed(LISTED)), fail="INSERT INTO smush_vote_log")
    assert _vote_from(client, "reject", LISTED, "pts").get_json() == {"status": "success"}
    assert db.statements("INSERT INTO smush_word_votes")
    assert db.statements("INSERT INTO smush_invalid_words")
    assert db.commits == 1
    out = capsys.readouterr().out
    assert "check=smush_vote_log_failed" in out
    assert "check=smush_vote_failed" not in out


def test_a_failing_log_still_lets_an_admin_change_through(client, smush_db, monkeypatch, capsys):
    from monitoring import alerts
    alerts.reset_throttle()
    db = smush_db(fail="INSERT INTO smush_vote_log")
    resp = client.post("/smush_admin/add_word", data={"word": "zarf", "word_type": "missing"},
                       headers=_admin_headers(monkeypatch))
    assert resp.status_code == 302 and "error=" not in resp.headers["Location"]
    assert db.statements("INSERT INTO smush_added_words") and db.commits == 1
    assert "check=smush_vote_log_failed" in capsys.readouterr().out


@pytest.mark.parametrize("sent, logged", [
    ("all8", "all8"), ("pan", "pan"), ("pts", "pts"), ("eff", "eff"),
    (None, None),           # a page loaded before it started saying
    ("hack", None), (["all8"], None),
])
def test_the_tab_a_vote_came_from_is_logged_never_refused(client, smush_db, sent, logged):
    db = smush_db(_votes(players=1))
    body = {"action": "suggest_missing", "word": NOT_LISTED, "center": CENTER, "outer": OUTER}
    if sent is not None:
        body["mode"] = sent
    assert client.post("/smush", json=body).get_json() == {"status": "success", "result": "recorded"}
    assert db.statements("INSERT INTO smush_word_votes")
    [row] = db.log_rows("smush")
    assert (row["vote"], row["mode"]) == ("missing", logged)


@pytest.mark.parametrize("data, dev, logged", [
    ({"mode": "all8", "ice_cold": True}, True, "dev_all8_ice"),
    ({"mode": "pts", "ice_cold": False}, True, "dev_pts"),
    ({"mode": "pan"}, True, "dev_pan"),
    ({"mode": "all8", "ice_cold": "yes"}, True, "dev_all8"),
    ({"ice_cold": True}, True, None),
    # Only /smush_dev has the Ice Cold toggle.
    ({"mode": "all8", "ice_cold": True}, False, "all8"),
])
def test_smush_dev_votes_are_tagged_as_dev(data, dev, logged):
    from routes.smush import _vote_mode
    assert _vote_mode(data, dev) == logged


def test_the_logged_tabs_are_the_pages_tabs():
    from routes.smush import SMUSH_RESULT_TABS
    for page in ("smush.html", "smush_dev.html"):
        source = (TEMPLATES / page).read_text(encoding="utf-8")
        tabs = re.search(r"const SORT_MODES = \[(.*?)\];", source).group(1)
        assert sorted(re.findall(r"'(\w+)'", tabs)) == sorted(SMUSH_RESULT_TABS), page
    # The longest mode still fits the log's VARCHAR(16).
    assert max(len(f"dev_{tab}_ice") for tab in SMUSH_RESULT_TABS) <= 16


# ---------------------------------------------------------------------------
# What players see
# ---------------------------------------------------------------------------

def test_smush_page_ships_the_crowd_hooks(client):
    html = client.get("/smush").get_data(as_text=True)
    for hook in ('id="suggest-box"', 'id="suggest-input"', 'id="suggest-btn"',
                 "Smush accepted a word that isn't listed?",
                 "action: 'reject'", "action: 'restore'", "action: 'suggest_missing'",
                 "{mode: sortMode}"):
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
