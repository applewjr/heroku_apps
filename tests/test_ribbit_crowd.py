"""Ribbit crowd word corrections and /ribbit_admin's word tabs
(routes/ribbit.py, crowd.py).

Every database call goes through a scripted fake: secret_pass points at the
production JawsDB, so nothing here may reach a real connection.
"""

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from data import words
from test_blossom import _admin_headers, _ScriptedDB

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"

# A 4x4 board, row by row: FROG / LEAP / STIN / KYDU, linked along each row
# plus a few joins, so FROG traces left to right.
LETTERS = "frogleapstinkydu"
LINKS = [[0, 1], [1, 2], [2, 3], [4, 5], [5, 6], [6, 7], [0, 4], [8, 9], [9, 10],
         [10, 11], [12, 13], [13, 14], [14, 15], [3, 7], [8, 12], [11, 15]]
LISTED = "frog"             # in the word list, and traceable here
NOT_LISTED = "gorf"         # traceable here (FROG backwards), but not a word
UNTRACEABLE = "golf"        # a word, but its letters aren't linked that way here


def _nodes(letters=LETTERS):
    return [{"id": i, "letter": ch} for i, ch in enumerate(letters) if ch != "."]


def _votes(players, today=0, invalid_today=1):
    """Answers for the counts the vote path makes (see test_blossom._votes)."""
    return {
        "WHERE voter_hash = %s AND created_at": (today,),
        "WHERE voter_hash = %s AND vote = 'invalid'": (invalid_today,),
        "FROM ribbit_word_votes v WHERE v.word": (players,),
    }


@pytest.fixture
def ribbit_db(monkeypatch):
    import crowd
    import routes.ribbit as ribbit

    # Start each test with no word list cached, so the fake builds it.
    monkeypatch.setattr(ribbit, "_ribbit_words", {"value": None, "expires": 0.0})

    def install(answers=None, rows=None, rowcount=1, fail=None):
        db = _ScriptedDB(answers, rows, rowcount, fail)
        monkeypatch.setattr(ribbit, "db_cursor", db)
        monkeypatch.setattr(crowd, "db_cursor", db)
        return db

    return install


def _vote(client, action, word, letters=LETTERS, links=LINKS, mode=None, path="/ribbit", **kw):
    body = {"action": action, "word": word, "nodes": _nodes(letters), "edges": links}
    if mode is not None:
        body["mode"] = mode
    return client.post(path, json=body, **kw)


def _tag():
    from routes.ribbit import puzzle_tag
    return puzzle_tag(LETTERS)


def test_the_board_fixtures_are_what_they_claim():
    from routes.ribbit import _traceable
    links = tuple(tuple(p) for p in LINKS)
    assert LISTED in words and _traceable(LISTED, LETTERS, links)
    assert NOT_LISTED not in words and _traceable(NOT_LISTED, LETTERS, links)
    assert UNTRACEABLE in words and not _traceable(UNTRACEABLE, LETTERS, links)


def test_reject_records_a_vote_for_this_board(client, ribbit_db):
    db = ribbit_db(_votes(players=1))
    resp = _vote(client, "reject", LISTED)
    assert resp.status_code == 200 and resp.get_json() == {"status": "success"}
    [(_sql, params)] = db.statements("INSERT INTO ribbit_word_votes")
    assert params[0] == LISTED and params[1] == "invalid" and params[3] == _tag()


def test_the_puzzle_tag_is_the_board_in_four_rows(client):
    tag = _tag()
    assert re.fullmatch(r"[a-z]{4}(:[a-z]{4}){3}", tag)
    assert sorted(tag.replace(":", "")) == sorted(LETTERS)
    assert len(tag) == 19                   # puzzle VARCHAR(19)


def test_a_turned_board_votes_under_the_same_tag(client, ribbit_db):
    # Rotate leaves the board turned: (r, c) -> (c, 3 - r).
    move = [c * 4 + (3 - r) for r, c in (divmod(i, 4) for i in range(16))]
    turned = [""] * 16
    for i, ch in enumerate(LETTERS):
        turned[move[i]] = ch
    turned_links = [[move[a], move[b]] for a, b in LINKS]
    db = ribbit_db(_votes(players=1))
    _vote(client, "reject", LISTED, "".join(turned), turned_links)
    [(_sql, params)] = db.statements("INSERT INTO ribbit_word_votes")
    assert params[3] == _tag()


def test_a_board_still_being_typed_can_vote(client, ribbit_db):
    # A solve doesn't need all 16 letters, so a vote mustn't either.
    partial = LETTERS[:8] + "." * 8
    links = [p for p in LINKS if max(p) < 8]
    db = ribbit_db(_votes(players=1))
    _vote(client, "reject", LISTED, partial, links)
    [(_sql, params)] = db.statements("INSERT INTO ribbit_word_votes")
    assert "." in params[3]


def test_the_deciding_player_removes_the_word(client, ribbit_db):
    import routes.ribbit as ribbit
    from crowd import remove_votes_needed
    db = ribbit_db(_votes(players=remove_votes_needed(LISTED)))
    resp = _vote(client, "reject", LISTED)
    # Exactly the reply any other vote gets.
    assert resp.get_json() == {"status": "success"}
    [(sql, params)] = db.statements("INSERT INTO ribbit_invalid_words")
    assert "'crowd'" in sql and params[0] == LISTED
    # The next solve re-reads the corrections rather than serving the memo.
    assert ribbit._ribbit_words["expires"] == 0.0


def test_one_player_short_changes_nothing(client, ribbit_db):
    from crowd import remove_votes_needed
    db = ribbit_db(_votes(players=remove_votes_needed(LISTED) - 1))
    _vote(client, "reject", LISTED)
    assert not db.statements("INSERT INTO ribbit_invalid_words")


def test_a_rare_word_goes_at_three_players_a_common_one_does_not(client, ribbit_db, monkeypatch):
    import crowd
    monkeypatch.setattr(crowd, "word_pop", {LISTED: 1.2})
    db = ribbit_db(_votes(players=3))
    _vote(client, "reject", LISTED)
    assert db.statements("INSERT INTO ribbit_invalid_words")

    monkeypatch.setattr(crowd, "word_pop", {LISTED: 4.5})
    db = ribbit_db(_votes(players=4))
    _vote(client, "reject", LISTED)
    assert not db.statements("INSERT INTO ribbit_invalid_words")


def test_restoring_the_deciding_vote_puts_the_word_back(client, ribbit_db):
    from crowd import remove_votes_needed
    db = ribbit_db(_votes(players=remove_votes_needed(LISTED) - 1))   # what's left once this vote is gone
    assert _vote(client, "restore", LISTED).get_json() == {"status": "success"}
    assert db.statements("DELETE FROM ribbit_word_votes WHERE word = %s AND vote = %s AND voter_hash")
    assert db.statements("DELETE FROM ribbit_invalid_words WHERE word = %s AND source = 'crowd'")


def test_the_crowd_never_reverses_your_own_entry(client, ribbit_db):
    answers = _votes(players=9)
    answers["SELECT source FROM ribbit_added_words"] = ("admin",)
    db = ribbit_db(answers)
    _vote(client, "reject", LISTED)
    assert not db.statements("INSERT INTO ribbit_invalid_words")


@pytest.mark.parametrize("word", [UNTRACEABLE, NOT_LISTED], ids=["untraceable", "not_listed"])
def test_only_a_listed_word_traceable_on_this_board_gets_a_vote(client, ribbit_db, word):
    db = ribbit_db(_votes(players=99))
    assert _vote(client, "reject", word).get_json() == {"status": "success"}
    assert not db.statements("INSERT INTO ribbit_word_votes")


def test_a_player_crossing_words_off_does_not_count(client, ribbit_db):
    # ✗ sits next to ✓ Found and both hide the word.
    from crowd import CROWD_MAX_INVALID_PER_DAY
    db = ribbit_db(_votes(players=99, invalid_today=CROWD_MAX_INVALID_PER_DAY + 1))
    _vote(client, "reject", LISTED)
    assert db.statements("INSERT INTO ribbit_word_votes")
    assert not db.statements("INSERT INTO ribbit_invalid_words")


@pytest.mark.parametrize("word, players, expected, adds", [
    (UNTRACEABLE, 0, "not_on_board", False),
    (LISTED, 0, "already_listed", False),
    (NOT_LISTED, 1, "recorded", False),
    # The vote that adds the word gets the same reply as one that doesn't.
    (NOT_LISTED, 2, "recorded", True),
])
def test_suggest_missing_outcomes(client, ribbit_db, word, players, expected, adds):
    db = ribbit_db(_votes(players=players))
    resp = _vote(client, "suggest_missing", word)
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "success", "result": expected}
    assert bool(db.statements("INSERT INTO ribbit_added_words")) == adds


def test_a_word_you_removed_is_never_added_back_by_players(client, ribbit_db):
    answers = _votes(players=9)
    answers["SELECT source FROM ribbit_invalid_words"] = ("admin",)
    db = ribbit_db(answers)
    assert _vote(client, "suggest_missing", NOT_LISTED).get_json()["result"] == "recorded"
    assert not db.statements("INSERT INTO ribbit_added_words")


def test_ribbit_votes_touch_only_ribbit_tables(client, ribbit_db):
    db = ribbit_db(_votes(players=99))
    _vote(client, "reject", LISTED)
    _vote(client, "restore", LISTED)
    _vote(client, "suggest_missing", NOT_LISTED)
    assert db.executed
    assert not [sql for sql, _p in db.executed
                if re.search(r"\b(blossom|smush|gtp)_", sql)]


@pytest.mark.parametrize("payload", [
    {"action": "reject", "word": "fro", "nodes": _nodes(), "edges": LINKS},          # too short
    {"action": "reject", "word": "fr0g", "nodes": _nodes(), "edges": LINKS},
    {"action": "reject", "word": ["frog"], "nodes": _nodes(), "edges": LINKS},
    {"action": "suggest_missing", "word": "f" * 16, "nodes": _nodes(), "edges": LINKS},
    {"action": "reject", "word": LISTED, "nodes": _nodes() + [{"id": 16, "letter": "z"}], "edges": LINKS},
    {"action": "reject", "word": LISTED, "nodes": _nodes(), "edges": [[0, 2]]},     # not neighbours
    {"action": "reject", "word": LISTED, "edges": LINKS},                          # no board
    {"action": "drop_table", "word": LISTED, "nodes": _nodes(), "edges": LINKS},
], ids=["short", "digit", "list", "long", "offgrid", "farlink", "noboard", "unknown"])
def test_malformed_votes_are_a_400_and_touch_nothing(client, ribbit_db, payload):
    db = ribbit_db()
    assert client.post("/ribbit", json=payload).status_code == 400
    assert not db.executed


def test_the_word_list_uses_only_ribbit_corrections(ribbit_db):
    from routes.ribbit import get_ribbit_words
    db = ribbit_db(rows={
        "FROM ribbit_invalid_words": [(LISTED,)],
        "FROM ribbit_added_words": [(NOT_LISTED,)],
    })
    ribbit_words = get_ribbit_words()
    assert LISTED not in ribbit_words and NOT_LISTED in ribbit_words
    assert [sql for sql, _p in db.executed] == ["SELECT word FROM ribbit_invalid_words",
                                                "SELECT word FROM ribbit_added_words"]


def test_no_corrections_means_no_copy_of_the_word_list(ribbit_db):
    from routes.ribbit import get_ribbit_words
    ribbit_db()
    assert get_ribbit_words() is words


def test_a_db_blip_serves_the_raw_list_and_retries_soon(ribbit_db):
    import routes.ribbit as ribbit
    ribbit_db(fail="ribbit_invalid_words")
    assert ribbit.get_ribbit_words() is words
    assert ribbit._ribbit_words["expires"] > 0


def test_a_solve_uses_ribbits_corrections(client, ribbit_db):
    ribbit_db(rows={"FROM ribbit_invalid_words": [(LISTED,)]})
    resp = client.post("/ribbit", json={"nodes": _nodes(), "edges": LINKS})
    found = [r["word"] for r in resp.get_json()["results"]]
    assert LISTED not in found and "leap" in found


def test_the_same_visitor_is_one_player_across_ribbits_tables(flask_app):
    import crowd
    from routes.ribbit import RIBBIT_CROWD
    from routes.smush import SMUSH_CROWD
    with flask_app.test_request_context("/", environ_base={"REMOTE_ADDR": "198.51.100.7"}):
        assert RIBBIT_CROWD.voter_hash() == crowd.player_hash("ribbit")
        assert RIBBIT_CROWD.voter_hash() != SMUSH_CROWD.voter_hash()


# ---------------------------------------------------------------------------
# Vote log: the sort chip
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sent, logged", [
    ("len", "len"), ("pop", "pop"),
    (None, None),           # a page loaded before it started saying
    ("all8", None), (["pop"], None),
])
def test_the_sort_chip_a_vote_came_from_is_logged_never_refused(client, ribbit_db, sent, logged):
    db = ribbit_db(_votes(players=1))
    assert _vote(client, "suggest_missing", NOT_LISTED, mode=sent).get_json()["result"] == "recorded"
    assert db.statements("INSERT INTO ribbit_word_votes")
    [row] = db.log_rows("ribbit")
    assert (row["event"], row["vote"], row["mode"], row["puzzle"]) == ("vote", "missing", logged, _tag())


def test_the_logged_modes_are_the_pages_sort_chips():
    from routes.ribbit import RIBBIT_SORT_MODES
    source = (TEMPLATES / "ribbit.html").read_text(encoding="utf-8")
    chips = re.search(r"const chips = \[(.*?)\];", source).group(1)
    assert re.findall(r"\['(\w+)',", chips) == list(RIBBIT_SORT_MODES)
    # The longest mode still fits the log's VARCHAR(16).
    assert max(len(chip) for chip in RIBBIT_SORT_MODES) <= 16


# ---------------------------------------------------------------------------
# /ribbit_admin's word tabs
# ---------------------------------------------------------------------------

def test_admin_word_routes_need_the_password(client, ribbit_db):
    ribbit_db()
    assert client.post("/ribbit_admin/add_word", data={"word": "zarf"}).status_code == 401
    assert client.post("/ribbit_admin/remove_word", data={"word": "zarf"}).status_code == 401


def test_admin_page_shows_lists_and_reports(client, ribbit_db, monkeypatch):
    when = datetime(2026, 10, 17, 8, 0)
    ribbit_db(rows={
        "FROM ribbit_invalid_words": [("glop", when, "crowd"), ("zarf", when, "admin")],
        "FROM ribbit_word_votes v": [(LISTED, "invalid", 3, when, "waiting", when, _tag(), when),
                                     (NOT_LISTED, "missing", 2, when, "added (crowd)", when, _tag(), when)],
    })
    resp = client.get("/ribbit_admin?tab=reports", headers=_admin_headers(monkeypatch))
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert '<td class="by-crowd">crowd</td>' in html and '<td class="by-admin">you</td>' in html
    # An open report gets both buttons; the settled one is hidden by default.
    assert html.count("Remove now") == 1 and html.count("Add now") == 1
    assert "1 settled report hidden" in html
    # The board shows as its four rows.
    assert "<br>".join(row.upper() for row in _tag().split(":")) + "<br>" in html
    assert "5 at popularity 3.3 or above, 4 at popularity 2.0 or above, 3 below that or none" in html


def test_admin_opens_on_shared_boards_and_keeps_the_tab(client, ribbit_db, monkeypatch):
    ribbit_db()
    html = client.get("/ribbit_admin", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert 'class="tab-button active" onclick="showTab(event, \'boards\')"' in html
    assert '<input type="hidden" name="tab" value="boards" class="keep-tab">' in html


def test_admin_shared_boards_still_work_without_the_word_tables(client, ribbit_db, monkeypatch):
    # ribbit_crowd.sql and ribbit_boards.sql are separate; either can be run first.
    ribbit_db(fail="ribbit_word_votes")
    resp = client.get("/ribbit_admin", headers=_admin_headers(monkeypatch))
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Has mysql_tables/ribbit_crowd.sql been run?" in html
    assert "Couldn't read the boards" not in html
    assert "Shared Boards" in html


def test_your_add_is_final(client, ribbit_db, monkeypatch):
    import routes.ribbit as ribbit
    db = ribbit_db()
    resp = client.post("/ribbit_admin/add_word", data={"word": "Zarf", "word_type": "missing"},
                       headers=_admin_headers(monkeypatch))
    assert resp.status_code == 302
    [(sql, _params)] = db.statements("INSERT INTO ribbit_added_words")
    assert "ON DUPLICATE KEY UPDATE source = 'admin'" in sql
    assert ("DELETE FROM ribbit_invalid_words WHERE word = %s", ("zarf",)) in db.executed
    assert ("DELETE FROM ribbit_word_votes WHERE word = %s", ("zarf",)) in db.executed
    assert ribbit._ribbit_words["expires"] == 0.0


def test_admin_remove_is_a_clean_undo(client, ribbit_db, monkeypatch):
    db = ribbit_db()
    resp = client.post("/ribbit_admin/remove_word", data={"word": "glop", "word_type": "invalid"},
                       headers=_admin_headers(monkeypatch))
    assert resp.status_code == 302
    assert ("DELETE FROM ribbit_invalid_words WHERE word = %s", ("glop",)) in db.executed
    assert ("DELETE FROM ribbit_word_votes WHERE word = %s", ("glop",)) in db.executed


@pytest.mark.parametrize("form", [{"word": "zarf"}, {"word": "drop table", "word_type": "invalid"}],
                         ids=["nochoice", "notaword"])
def test_admin_refuses_without_a_choice_or_a_word(client, ribbit_db, monkeypatch, form):
    db = ribbit_db()
    resp = client.post("/ribbit_admin/add_word", data=form, headers=_admin_headers(monkeypatch))
    assert resp.status_code == 302 and "error=" in resp.headers["Location"]
    assert not db.executed


def test_admin_actions_come_back_to_the_same_tab(client, ribbit_db, monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    ribbit_db()
    resp = client.post("/ribbit_admin/remove_word", headers=_admin_headers(monkeypatch),
                       data={"word": "glop", "word_type": "invalid", "tab": "invalid", "q": "gl"})
    args = parse_qs(urlsplit(resp.headers["Location"]).query)
    assert args == {"tab": ["invalid"], "q": ["gl"], "success": ["glop undone"]}


# ---------------------------------------------------------------------------
# What players see
# ---------------------------------------------------------------------------

def test_ribbit_page_ships_the_crowd_hooks(client):
    html = client.get("/ribbit").get_data(as_text=True)
    for hook in ('id="suggest-box"', 'id="suggest-input"', 'id="suggest-btn"',
                 "Ribbit accepted a word that isn't listed?",
                 "action: 'reject'", "action: 'restore'", "action: 'suggest_missing'",
                 "mode: sortMode"):
        assert hook in html, f"ribbit.html lost {hook}"


def test_players_are_never_told_the_vote_counts(client):
    # James's rule for every crowd game: knowing how many votes a change
    # takes is the first thing anyone gaming it would want.
    html = client.get("/ribbit").get_data(as_text=True)
    lowered = html.lower()
    for hint in ("crowd_remove_votes", "crowd_add_votes", "different players",
                 "another player", "players flag", "players agreed", "for everyone",
                 "5 players", "five players", "two players", "2 players"):
        assert hint not in lowered, f"page hints at the threshold: {hint!r}"
    assert "take every report into account" in html
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    faq = next(b for b in map(json.loads, blocks) if b.get("@type") == "FAQPage")
    assert "take every report into account" in json.dumps(faq)
