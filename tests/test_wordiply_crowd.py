"""Wordiply's word corrections (routes/wordiply.py, crowd.py): the ✗ on a
result, and /wordiply_admin's word tabs.

Every database call goes through a scripted fake: secret_pass points at the
production JawsDB, so nothing here may reach a real connection. Starters are
made-up strings, never a real Wordiply puzzle's.
"""

import json
import re
from datetime import datetime

import pytest

import routes.wordiply as wordiply
from data import words
from functions import all_words
from test_blossom import _admin_headers, _ScriptedDB

STARTER = "zzl"
LISTED = "bedazzlement"


def _votes(players, today=0, invalid_today=1):
    """Answers for the counts the vote path makes (see test_blossom._votes)."""
    return {
        "WHERE voter_hash = %s AND created_at": (today,),
        "WHERE voter_hash = %s AND vote = 'invalid'": (invalid_today,),
        "FROM wordiply_word_votes v WHERE v.word": (players,),
    }


@pytest.fixture
def wordiply_db(monkeypatch):
    import crowd

    # Start each test with no corrections cached, so the fake is read.
    monkeypatch.setattr(wordiply, "_corrections", {"value": None, "expires": 0.0})

    def install(answers=None, rows=None, rowcount=1, fail=None):
        db = _ScriptedDB(answers, rows, rowcount, fail)
        monkeypatch.setattr(wordiply, "db_cursor", db)
        monkeypatch.setattr(crowd, "db_cursor", db)
        return db

    return install


def _lists(removed=(), added=()):
    return {"SELECT word FROM wordiply_invalid_words": [(w,) for w in removed],
            "SELECT word FROM wordiply_added_words": [(w,) for w in added]}


def _vote(client, action="reject", word=LISTED, letters=STARTER, **kw):
    return client.post("/wordiply", json={"action": action, "word": word, "letters": letters}, **kw)


def _search(client, letters):
    resp = client.post("/wordiply", json={"search_string": letters})
    assert resp.status_code == 200
    return resp.get_json()["results"]


def test_the_fixture_words_are_what_they_claim():
    assert LISTED in words and STARTER in LISTED and "puzzle" in words


# --- the search ------------------------------------------------------------------------

def test_removed_words_leave_the_list_and_added_ones_join_it():
    plain = all_words.wordiply_solver(STARTER, words, 20)
    assert LISTED in plain
    out = all_words.wordiply_solver(STARTER, words, 20, {LISTED}, {"zzlablezzlables", "nothere"})
    assert LISTED not in out and "zzlablezzlables" in out and "nothere" not in out
    assert out == sorted(out, key=lambda w: (-len(w), w))


def test_the_starter_itself_is_never_listed():
    # Wordiply refuses the starter on its own.
    assert "puzzle" not in all_words.wordiply_solver("puzzle", words, 500)
    assert "puzzles" in all_words.wordiply_solver("puzzle", words, 500)


def test_a_search_applies_the_corrections(client, wordiply_db):
    wordiply_db(rows=_lists([LISTED], ["zzlablezzlables"]))
    out = _search(client, STARTER)
    assert LISTED not in out and "zzlablezzlables" in out


def test_added_wins_over_removed(client, wordiply_db):
    wordiply_db(rows=_lists([LISTED], [LISTED]))
    assert LISTED in _search(client, STARTER)


def test_without_the_tables_the_raw_list_serves(client, wordiply_db):
    wordiply_db(fail="wordiply_")
    assert _search(client, STARTER) == all_words.wordiply_solver(STARTER, words, 90)


def test_no_corrected_copy_of_the_word_list_is_held(wordiply_db):
    wordiply_db(rows=_lists([LISTED], ["zzlablezzlables"]))
    removed, added = wordiply.get_corrections()
    assert removed == {LISTED} and added == {"zzlablezzlables"}


# --- reports ---------------------------------------------------------------------------

def test_a_refused_word_is_one_vote_tagged_with_its_starter(client, wordiply_db):
    db = wordiply_db(_votes(players=1), rows=_lists())
    resp = _vote(client)
    assert resp.status_code == 200 and resp.get_json() == {"status": "success"}
    [(_sql, params)] = db.statements("INSERT INTO wordiply_word_votes")
    assert params[0] == LISTED and params[1] == "invalid" and params[3] == f"starter:{STARTER}"
    assert not db.statements("INSERT INTO wordiply_invalid_words")
    for other in ("ribbit_", "smush_", "blossom_", "chainle_", "typeshift_", "gtp_"):
        assert not db.statements(other), other


def test_enough_players_remove_it_and_the_reply_never_says_so(client, wordiply_db):
    import crowd
    needed = crowd.remove_votes_needed(LISTED)
    db = wordiply_db(_votes(players=needed), rows=_lists())
    before = _vote(client).get_json()
    assert db.statements("INSERT INTO wordiply_invalid_words")
    wordiply_db(_votes(players=1), rows=_lists())
    assert _vote(client).get_json() == before


def test_a_removal_shows_in_the_next_search(client, wordiply_db):
    import crowd
    db = wordiply_db(_votes(players=crowd.remove_votes_needed(LISTED)), rows=_lists())
    assert LISTED in _search(client, STARTER)
    _vote(client)
    db.rows.update(_lists([LISTED]))
    assert LISTED not in _search(client, STARTER)


@pytest.mark.parametrize("word, letters", [
    ("qqqzzlqq", STARTER),          # not a word we list
    ("banana", STARTER),            # a word, but not for this starter
    ("puzzle", "puzzle"),           # the starter itself, which is never listed
])
def test_only_a_word_this_starter_lists_counts(client, wordiply_db, word, letters):
    db = wordiply_db(_votes(players=1), rows=_lists())
    assert _vote(client, word=word, letters=letters).get_json() == {"status": "success"}
    assert not db.statements("INSERT INTO wordiply_word_votes")


def test_a_word_already_removed_takes_no_more_votes(client, wordiply_db):
    db = wordiply_db(_votes(players=1), rows=_lists([LISTED]))
    assert _vote(client).get_json() == {"status": "success"}
    assert not db.statements("INSERT INTO wordiply_word_votes")


def test_taking_it_back_takes_the_vote_back(client, wordiply_db):
    db = wordiply_db(_votes(players=0), rows=_lists())
    assert _vote(client, "restore").get_json() == {"status": "success"}
    [(_sql, params)] = db.statements("DELETE FROM wordiply_word_votes")
    assert params[0] == LISTED and params[1] == "invalid"


@pytest.mark.parametrize("body", [
    {"action": "reject"},
    {"action": "reject", "word": LISTED},
    {"action": "reject", "word": LISTED, "letters": "z"},
    {"action": "reject", "word": LISTED, "letters": 5},
    {"action": "reject", "word": "two words", "letters": STARTER},
    {"action": "reject", "word": ["x"], "letters": STARTER},
    {"action": "reject", "word": "a" * 51, "letters": STARTER},
    {"action": "reject", "word": "café", "letters": STARTER},
    {"action": "suggest_missing", "word": LISTED, "letters": STARTER},
])
def test_bad_reports_are_400s(client, wordiply_db, body):
    db = wordiply_db(_votes(players=1), rows=_lists())
    assert client.post("/wordiply", json=body).status_code == 400
    assert not db.statements("INSERT")


def test_a_failed_vote_never_breaks_the_page(client, wordiply_db, monkeypatch):
    import crowd
    alerts = []
    monkeypatch.setattr(crowd.alerts, "alert_throttled", lambda name, **kw: alerts.append(name))
    wordiply_db(_votes(players=1), rows=_lists(), fail="wordiply_word_votes")
    assert _vote(client).get_json() == {"status": "success"}
    assert alerts == ["wordiply_vote_failed"]


# --- the page --------------------------------------------------------------------------

def test_the_x_asks_before_it_reports(client):
    html = client.get("/wordiply").get_data(as_text=True)
    for hook in ('class="reject-btn"', "✗ Wordiply refused it", "✓ I played it", ">Cancel</button>",
                 'id="wp-hidden"', "sendReport('reject', word)", "sendReport('restore', w)"):
        assert hook in html, hook
    # Only "refused" reports: playing a word just hides it.
    played = html.split("answer.classList.contains('confirm-played')")[1].split("}")[0]
    assert "sendReport" not in played and "setHidden(word, 'played')" in played


def test_constructor_is_a_word_like_any_other(client):
    # A bare look in the hidden words' object would always find one.
    assert "constructor" in _search(client, "structor")
    html = client.get("/wordiply").get_data(as_text=True)
    assert "filter(word => !here[word])" not in html
    assert "filter(word => here[word] !== 'refused' && here[word] !== 'played')" in html


def test_faq_structured_data_matches_the_visible_faq(client):
    html = client.get("/wordiply").get_data(as_text=True)
    blocks = [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
    faq = next(b for b in blocks if b.get("@type") == "FAQPage")
    visible = html.split("Frequently Asked Questions")[1]
    for q in faq["mainEntity"]:
        assert q["name"] in visible
    said = next(q for q in faq["mainEntity"] if q["name"] == "What if Wordiply refuses a word?")
    assert said["acceptedAnswer"]["text"] in visible


def test_players_are_never_told_how_reports_count(client):
    html = client.get("/wordiply").get_data(as_text=True).lower()
    for told in ("players need", "enough players report", "votes", "reports needed", "threshold"):
        assert told not in html, told


# --- /wordiply_admin -------------------------------------------------------------------

REPORT = (LISTED, "invalid", 2, datetime(2026, 11, 24, 9, 30), "waiting", datetime(2026, 11, 24, 8, 0),
          f"starter:{STARTER}", datetime(2026, 11, 24).date())


def _admin(client, monkeypatch, query=""):
    resp = client.get("/wordiply_admin" + query, headers=_admin_headers(monkeypatch))
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_the_admin_opens_on_the_starter_with_word_tabs_beside_it(client, wordiply_db, monkeypatch):
    wordiply_db(rows={"WITH board_dates": [REPORT]})
    html = _admin(client, monkeypatch)
    assert 'class="tab-button active" onclick="showTab(event, \'starter\')"' in html
    for tab in ("starter-tab", "reports-tab", "invalid-tab", "added-tab"):
        assert f'id="{tab}"' in html
    assert html.count('class="tab-content active"') == 1
    assert 'id="starter-tab" class="tab-content active"' in html


def test_a_report_shows_its_starter_and_both_buttons(client, wordiply_db, monkeypatch):
    wordiply_db(rows={"WITH board_dates": [REPORT]})
    html = _admin(client, monkeypatch, "?tab=reports")
    row = html.split(f"<td><strong>{LISTED}</strong></td>")[1].split("</tr>")[0]
    assert ">ZZL<" in row and "Remove now" in row and "Keep it" in row and ">2<" in row
    assert '<strong>Open reports (7 days):</strong> 1' in html


@pytest.mark.parametrize("path, kind, sql", [
    ("add_word", "invalid", "INSERT INTO wordiply_invalid_words"),
    ("add_word", "missing", "INSERT INTO wordiply_added_words"),
    ("remove_word", "invalid", "DELETE FROM wordiply_invalid_words"),
    ("remove_word", "missing", "DELETE FROM wordiply_added_words"),
])
def test_admin_changes_write_wordiplys_own_tables(client, wordiply_db, monkeypatch, path, kind, sql):
    db = wordiply_db()
    wordiply._corrections.update(value=(frozenset(), frozenset()), expires=float("inf"))
    resp = client.post(f"/wordiply_admin/{path}", headers=_admin_headers(monkeypatch),
                       data={"word": LISTED.upper(), "word_type": kind, "tab": "reports"})
    assert resp.status_code == 302 and "success=" in resp.headers["Location"] and "tab=reports" in resp.headers["Location"]
    assert db.statements(sql)
    assert wordiply._corrections["expires"] == 0.0      # read again by the next search


def test_word_changes_need_the_password(client):
    for path in ("add_word", "remove_word"):
        assert client.post(f"/wordiply_admin/{path}", data={}).status_code == 401


def test_the_starter_tab_survives_missing_word_tables(client, wordiply_db, monkeypatch):
    wordiply_db(fail="wordiply_invalid_words")
    html = _admin(client, monkeypatch)
    assert "Has mysql_tables/wordiply_crowd.sql been run?" in html
    assert 'id="approve-entry"' in html


def test_the_word_tabs_survive_a_missing_starter_table(client, wordiply_db, monkeypatch):
    wordiply_db(rows={"WITH board_dates": [REPORT]}, fail="FROM wordiply_boards")
    html = _admin(client, monkeypatch, "?tab=reports")
    assert LISTED in html and "Has mysql_tables/wordiply_boards.sql been run?" in html


# --- the tables ------------------------------------------------------------------------

def test_the_sql_file_has_all_four_tables_wide_enough():
    sql = open("mysql_tables/wordiply_crowd.sql", encoding="utf-8").read()
    for table in ("wordiply_word_votes", "wordiply_invalid_words", "wordiply_added_words", "wordiply_vote_log"):
        assert f"CREATE TABLE {table} (" in sql and f"-- DROP TABLE {table};" in sql
    assert "ribbit" not in sql.replace("ribbit_crowd.sql", "").lower()
    widths = [int(n) for n in re.findall(r"puzzle\s+VARCHAR\((\d+)\)", sql)]
    assert len(widths) == 2 and min(widths) >= len("starter:") + wordiply.MAX_LEN
    assert min(int(n) for n in re.findall(r"word\s+VARCHAR\((\d+)\)", sql)) >= 50
