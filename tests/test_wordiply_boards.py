"""Wordiply's today's starter word (routes/wordiply.py): the starter early
players type in, offered to the players after them, and /wordiply_admin.

Nothing here reaches a real database: secret_pass points at the production
JawsDB. Most tests swap the module's table functions for an in-memory
stand-in (MemoryStarters); the SQL itself is checked against test_blossom's
scripted fake. Each simulated player is a different X-Forwarded-For, which
is what helpers.client_ip reads: an IPv4 address each, or IPv6 ones where a
test needs players on one network. The clock is Wordiply's (Europe/London),
set through routes.wordiply._game_now. The starters are made-up strings,
never a real Wordiply puzzle's.
"""

import json
import re
from datetime import date, datetime, timedelta

import pytest
import pytz

import routes.wordiply as wordiply
from test_blossom import _admin_headers, _ScriptedDB

LONDON = pytz.timezone("Europe/London")
DAY = date(2026, 11, 23)
NOON = LONDON.localize(datetime(2026, 11, 23, 12, 0))


class MemoryStarters:
    """Stands in for wordiply_boards: the page's logs and the admin actions
    write here, and the reader reads back, rows in routes.wordiply._COLUMNS
    order. One row per (letters, voter, day), as the table's unique key
    keeps it."""

    def __init__(self):
        self.rows = {}
        self.clock = datetime(2026, 11, 23, 4, 0)
        self.reads = 0

    def store(self, day, voter, prefix, letters, source):
        self.clock += timedelta(minutes=1)
        old = self.rows.get((letters, voter, day))
        created = old[5] if old else self.clock
        self.rows[(letters, voter, day)] = (letters, source, voter, prefix, day, created, self.clock)

    def approve(self, day, letters):
        replaced = [key for key, row in self.rows.items()
                    if row[4] == day and row[1] == wordiply.APPROVAL and row[0] != letters]
        for key in replaced:
            del self.rows[key]
        self.store(day, wordiply.APPROVAL, None, letters, wordiply.APPROVAL)
        return len(replaced)

    def _delete(self, keep):
        gone = [key for key, row in self.rows.items() if not keep(row)]
        for key in gone:
            del self.rows[key]
        return len(gone)

    def unapprove(self, day):
        return self._delete(lambda row: not (row[4] == day and row[1] == wordiply.APPROVAL))

    def remove(self, letters, day):
        return self._delete(lambda row: not (row[0] == letters and row[4] == day
                                             and row[1] != wordiply.APPROVAL))

    def between(self, first, last):
        self.reads += 1
        return [row for row in self.rows.values() if first <= row[4] <= last]

    def players_of(self, letters, day=DAY):
        return sorted(row[1] for row in self.rows.values()
                      if row[0] == letters and row[4] == day and row[1] != wordiply.APPROVAL)


@pytest.fixture
def starters(monkeypatch):
    """The in-memory table, live on /wordiply, at noon in London on DAY,
    with no memo between reads, so these tests exercise the serve rules;
    the memo has tests of its own."""
    memory = MemoryStarters()
    monkeypatch.setattr(wordiply, "store_starter", memory.store)
    monkeypatch.setattr(wordiply, "store_approval", memory.approve)
    monkeypatch.setattr(wordiply, "remove_approval", memory.unapprove)
    monkeypatch.setattr(wordiply, "remove_entries", memory.remove)
    monkeypatch.setattr(wordiply, "rows_between", memory.between)
    monkeypatch.setattr(wordiply, "_game_now", lambda: NOON)
    monkeypatch.setattr(wordiply, "WORDIPLY_STARTER_LIVE", True)
    monkeypatch.setattr(wordiply, "CACHE_SECONDS", 0)
    return memory


def _player(n):
    """A simulated player: a number is an IPv4 address of their own, a
    string an address as given (IPv6 too)."""
    return {"X-Forwarded-For": f"198.51.100.{n}" if isinstance(n, int) else n}


def _log(client, player, letters="row", source="typed"):
    resp = client.post("/wordiply", headers=_player(player),
                       json={"action": "log_starter", "letters": letters, "source": source})
    assert resp.status_code == 200 and resp.get_json() == {"status": "success"}
    return resp


def _reply(client, player=99):
    resp = client.post("/wordiply", headers=_player(player), json={"action": "todays_starter"})
    assert resp.status_code == 200
    return resp.get_json()


def _served(client, **kw):
    return _reply(client, **kw)["starter"]


# ---------------------------------------------------------------------------
# The serve rules, through the real route
# ---------------------------------------------------------------------------

def test_two_players_wait_and_the_third_serves_the_starter(client, starters):
    _log(client, 1)
    _log(client, 2, "ROW")                     # any case: stored lowercase
    assert _served(client) is None
    _log(client, 3)
    assert _reply(client) == {"starter": "row", "day": "2026-11-23"}


def test_a_player_counts_once_however_often_they_use_it(client, starters):
    for _ in range(3):
        _log(client, 1)
    _log(client, 2)
    assert _served(client) is None
    assert len(starters.rows) == 2


def test_players_on_one_network_count_once(client, starters):
    for n in range(1, 4):
        _log(client, f"2001:db8:aa:{n}::1")    # three /64s, one /48
    assert _served(client) is None
    for n in range(1, 3):
        _log(client, f"2001:db8:{n}::1")       # two more networks: three in all
    assert _served(client) == "row"


def test_most_players_win_and_a_tie_goes_to_the_first_to_three(client, starters):
    for n in range(1, 4):
        _log(client, n, "row")
    for n in range(4, 7):
        _log(client, n, "cap")
    assert _served(client) == "row"
    _log(client, 7, "cap")                     # pulls ahead: the offer switches
    assert _served(client) == "cap"


def test_using_a_starter_again_cannot_flip_a_tie(client, starters):
    for n in range(1, 4):
        _log(client, n, "row")                 # ROW reaches three first
    for n in range(4, 7):
        _log(client, n, "cap")
    assert _served(client) == "row"
    _log(client, 1, "row")                     # player 1 again, from another tab
    _log(client, 2, "row")
    assert _served(client) == "row"            # ROW still got there first


def test_a_half_typed_starter_is_never_served(client, starters):
    # Three players caught on RO (they left for the game mid-word) and the
    # same three on ROW: the longer one is the starter.
    for n in range(1, 4):
        _log(client, n, "ro")
        _log(client, n, "row")
    assert _served(client) == "row"


def test_a_starter_tried_for_longer_words_doesnt_beat_the_starter(client, starters):
    for n in range(1, 6):
        _log(client, n, "row")
    for n in range(1, 4):
        _log(client, n, "rowing")
    assert _served(client) == "row"


def test_an_edited_starter_counts(client, starters):
    _log(client, 1)
    _log(client, 2)
    _log(client, 3, source="edited")
    assert _served(client) == "row"


def test_an_approval_wins_and_replaces_the_days_earlier_one(client, starters):
    for n in range(1, 4):
        _log(client, n, "cap")
    starters.approve(DAY, "row")
    assert _served(client) == "row"
    starters.approve(DAY, "bat")
    assert _served(client) == "bat"
    assert [row[0] for row in starters.rows.values() if row[1] == wordiply.APPROVAL] == ["bat"]


def test_only_the_days_own_entries_count(client, starters):
    yesterday = DAY - timedelta(days=1)
    for n in range(1, 4):
        starters.store(yesterday, f"p{n}", f"n{n}", "row", "typed")
    starters.approve(yesterday, "cap")
    assert _served(client) is None


def test_the_day_is_londons(client, starters, monkeypatch):
    # 23:59 in London is 15:59 in Los Angeles; a minute later both games
    # are on the next day, whatever the player's own clock says.
    monkeypatch.setattr(wordiply, "_game_now",
                        lambda: LONDON.localize(datetime(2026, 11, 22, 23, 59)))
    _log(client, 1, "cap")
    monkeypatch.setattr(wordiply, "_game_now",
                        lambda: LONDON.localize(datetime(2026, 11, 23, 0, 1)))
    _log(client, 2, "row")
    assert {(row[0], row[4]) for row in starters.rows.values()} == {
        ("cap", date(2026, 11, 22)), ("row", date(2026, 11, 23))}
    assert _reply(client)["day"] == "2026-11-23"


def test_an_edited_starter_that_is_the_one_on_offer_is_not_stored(client, starters):
    starters.approve(DAY, "row")
    _log(client, 1, "row", source="edited")    # changed, then changed back
    assert starters.players_of("row") == []
    _log(client, 1, "rows", source="edited")   # a real change
    assert starters.players_of("rows") == ["edited"]
    _log(client, 2, "row", source="typed")     # typed by hand: theirs
    assert starters.players_of("row") == ["typed"]


@pytest.mark.parametrize("body", [
    {"letters": "r", "source": "typed"},
    {"letters": "a" * 16, "source": "typed"},
    {"letters": "r0w", "source": "typed"},
    {"letters": "rów", "source": "typed"},
    {"letters": "ro w", "source": "typed"},
    {"letters": 123, "source": "typed"},
    {"source": "typed"},
    {"letters": "row", "source": "loaded"},
    {"letters": "row", "source": "admin"},
    {"letters": "row"},
])
def test_anything_but_a_starter_is_a_400(client, starters, body):
    resp = client.post("/wordiply", json={"action": "log_starter", **body})
    assert resp.status_code == 400
    assert not starters.rows


def test_an_unknown_action_is_a_400(client, starters):
    assert client.post("/wordiply", json={"action": "vote"}).status_code == 400


def test_searches_still_work_as_before(client, starters):
    resp = client.post("/wordiply", json={"search_string": "zz"})
    assert resp.status_code == 200 and "results" in resp.get_json()
    assert not starters.rows


def test_while_dark_starters_log_and_none_is_served(client, starters, monkeypatch):
    monkeypatch.setattr(wordiply, "WORDIPLY_STARTER_LIVE", False)
    for n in range(1, 4):
        _log(client, n)
    assert len(starters.rows) == 3
    assert _reply(client) == {"starter": None, "day": "2026-11-23"}
    html = client.get("/wordiply").get_data(as_text=True)
    assert 'id="wp-today"' not in html and 'id="wp-loaded-note"' not in html
    assert "Do I have to type today&#39;s letters?" not in html
    assert "Do I have to type today's letters?" not in html
    # The page still logs what players use.
    assert "action: 'log_starter'" in html


def test_starters_are_live_on_wordiply():
    assert wordiply.WORDIPLY_STARTER_LIVE is True


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def test_the_page_draws_the_starter_this_process_already_looked_up(client, starters):
    for n in range(1, 4):
        _log(client, n)
    html = client.get("/wordiply").get_data(as_text=True)
    # Nothing looked up yet: the bar waits for the page's own ask.
    assert '<div class="wp-today" id="wp-today" hidden>' in html
    assert "let starter = null;" in html
    assert _served(client) == "row"
    reads = starters.reads
    html = client.get("/wordiply").get_data(as_text=True)
    assert starters.reads == reads                         # the page never reads the table
    assert '<div class="wp-today" id="wp-today">' in html
    assert '<strong id="wp-today-letters">ROW</strong>' in html
    assert 'let starter = "row";' in html
    assert 'const STARTER_DAY = "2026-11-23";' in html


def test_an_admin_change_takes_the_starter_off_the_first_paint(client, starters, monkeypatch):
    starters.approve(DAY, "row")
    assert _served(client) == "row"
    client.post("/wordiply_admin/unapprove", headers=_admin_headers(monkeypatch),
                data={"played_on": "2026-11-23"})
    assert "let starter = null;" in client.get("/wordiply").get_data(as_text=True)
    assert _served(client) is None


def test_the_faq_answer_and_how_to_use_come_with_the_offer(client, starters):
    html = client.get("/wordiply").get_data(as_text=True)
    blocks = [json.loads(block) for block in
              re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
    faq = next(block for block in blocks if block.get("@type") == "FAQPage")
    assert "Do I have to type today's letters?" in [q["name"] for q in faq["mainEntity"]]
    assert ">Do I have to type today's letters?<" in html
    assert "tap Load it to see its longest words" in html
    assert "↩ Not my starter" in html


NEVER_SAID = ("different players", "another player", "players agreed", "for everyone",
              "2 players", "3 players", "two players", "three players", "serve_",
              "approv", "more players", "serve_crowd")


def test_players_are_never_told_how_a_starter_is_chosen(client, starters):
    for n in range(1, 4):
        _log(client, n)
    texts = [client.get("/wordiply").get_data(as_text=True), json.dumps(_reply(client)),
             json.dumps(_log(client, 4).get_json())]
    for text in texts:
        lowered = text.lower()
        for phrase in NEVER_SAID:
            assert phrase not in lowered, f"a player could read {phrase!r}"


# ---------------------------------------------------------------------------
# The memo
# ---------------------------------------------------------------------------

def test_the_starter_is_read_at_most_once_a_minute(client, starters, monkeypatch):
    monkeypatch.setattr(wordiply, "CACHE_SECONDS", 60)
    for n in range(1, 4):
        _log(client, n)
    _served(client)
    _served(client)
    assert starters.reads == 1
    # An approval is served at once all the same.
    client.post("/wordiply_admin/approve", headers=_admin_headers(monkeypatch),
                data={"letters": "cap", "played_on": "2026-11-23"})
    assert _served(client) == "cap"


def test_a_failed_read_keeps_the_last_starter(client, starters, monkeypatch):
    for n in range(1, 4):
        _log(client, n)
    assert _served(client) == "row"

    def broken(first, last):
        raise RuntimeError("Table 'wordiply_boards' doesn't exist")

    monkeypatch.setattr(wordiply, "rows_between", broken)
    assert _served(client) == "row"
    assert client.get("/wordiply").status_code == 200


# ---------------------------------------------------------------------------
# /wordiply_admin
# ---------------------------------------------------------------------------

def test_the_admin_needs_the_password(client, starters):
    assert client.get("/wordiply_admin").status_code == 401
    for action in ("approve", "unapprove", "remove"):
        assert client.post(f"/wordiply_admin/{action}", data={}).status_code == 401


def test_the_admin_shows_each_days_starters(client, starters, monkeypatch):
    for n in range(1, 4):
        _log(client, n, "row")
    _log(client, 4, "rwo")
    starters.store(DAY - timedelta(days=1), "p9", "n9", "cap", "typed")
    html = client.get("/wordiply_admin", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert "<strong>Game day (Europe/London):</strong> 2026-11-23" in html
    assert "2026-11-23 (today)" in html and "2026-11-22" in html
    assert 'serving <span class="starter-text">ROW</span>' in html
    assert "needs 2 more players" in html                  # RWO, a typo
    assert '<td class="starter-text">CAP</td>' in html
    assert 'value="2026-11-23"' in html                    # the approve form's date
    assert "logging only" not in html


def test_an_approval_from_the_admin_serves_at_once_and_undoes(client, starters, monkeypatch):
    headers = _admin_headers(monkeypatch)
    _log(client, 1, "cap")
    resp = client.post("/wordiply_admin/approve", headers=headers,
                       data={"letters": " Row ", "played_on": "2026-11-23"})
    assert resp.status_code == 302 and "success=Approved+ROW+for+11-23" in resp.headers["Location"]
    assert _served(client) == "row"
    resp = client.post("/wordiply_admin/approve", headers=headers,
                       data={"letters": "bat", "played_on": "2026-11-23"})
    assert "in+place+of+your+earlier+approval" in resp.headers["Location"]
    resp = client.post("/wordiply_admin/unapprove", headers=headers, data={"played_on": "2026-11-23"})
    assert "success=Approval+undone" in resp.headers["Location"]
    assert _served(client) is None
    assert len(starters.rows) == 1                         # the player's starter stays
    resp = client.post("/wordiply_admin/unapprove", headers=headers, data={"played_on": "2026-11-23"})
    assert "error=Nothing+was+approved" in resp.headers["Location"]


@pytest.mark.parametrize("form", [
    {"letters": "r", "played_on": "2026-11-23"},
    {"letters": "r0w", "played_on": "2026-11-23"},
    {"letters": "row", "played_on": "tomorrow"},
    {"letters": "row"},
])
def test_the_admin_refuses_anything_but_a_starter(client, starters, monkeypatch, form):
    for action in ("approve", "remove"):
        resp = client.post(f"/wordiply_admin/{action}", headers=_admin_headers(monkeypatch), data=form)
        assert resp.status_code == 302 and "error=" in resp.headers["Location"]
    assert not starters.rows


def test_remove_deletes_that_starters_player_entries_only(client, starters, monkeypatch):
    for n in range(1, 4):
        _log(client, n, "row")
    _log(client, 4, "cap")
    starters.approve(DAY, "row")
    resp = client.post("/wordiply_admin/remove", headers=_admin_headers(monkeypatch),
                       data={"letters": "row", "played_on": "2026-11-23"})
    assert "success=Removed+3+entries+of+ROW" in resp.headers["Location"]
    assert sorted((row[0], row[1]) for row in starters.rows.values()) == [
        ("cap", "typed"), ("row", wordiply.APPROVAL)]


def test_the_admin_says_when_the_table_is_missing(client, monkeypatch):
    monkeypatch.setattr(wordiply, "db_cursor", _ScriptedDB(fail="wordiply_boards"))
    resp = client.get("/wordiply_admin", headers=_admin_headers(monkeypatch))
    assert resp.status_code == 200
    assert "Has mysql_tables/wordiply_boards.sql been run?" in resp.get_data(as_text=True)


def test_jj_admin_links_the_wordiply_admin(client, monkeypatch):
    html = client.get("/jj_admin", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert "/wordiply_admin" in html


# ---------------------------------------------------------------------------
# The SQL, against the scripted fake
# ---------------------------------------------------------------------------

@pytest.fixture
def starter_db(monkeypatch):
    monkeypatch.setattr(wordiply, "_game_now", lambda: NOON)
    monkeypatch.setattr(wordiply, "WORDIPLY_STARTER_LIVE", True)

    def install(answers=None, rows=None, rowcount=1, fail=None):
        db = _ScriptedDB(answers, rows, rowcount, fail)
        monkeypatch.setattr(wordiply, "db_cursor", db)
        return db

    return install


def test_a_starter_is_stored_for_its_player_and_day(client, starter_db):
    db = starter_db(answers={"SELECT COUNT(*)": (0,)})
    _log(client, 1, "Row")
    (sql, params), = db.statements("INSERT INTO wordiply_boards")
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert params[0] == DAY and params[3:5] == ("row", "typed")
    assert len(params[1]) == 32 and len(params[2]) == 32   # player and network hashes
    assert db.commits == 1


def test_the_daily_cap_stops_a_flood(client, starter_db):
    db = starter_db(answers={"SELECT COUNT(*)": (wordiply.STARTERS_PER_PLAYER_PER_DAY,)})
    _log(client, 1)
    assert not db.statements("INSERT INTO wordiply_boards")


def test_the_reader_asks_for_the_days_rows(client, starter_db):
    db = starter_db()
    assert _served(client) is None
    (sql, params), = db.statements("FROM wordiply_boards")
    assert sql.startswith(f"SELECT {wordiply._COLUMNS} FROM wordiply_boards WHERE played_on BETWEEN")
    assert params == (DAY, DAY)


def test_a_missing_table_costs_the_starter_never_the_request(client, starter_db, capsys):
    from monitoring import alerts
    alerts.reset_throttle()
    starter_db(fail="wordiply_boards")
    _log(client, 1)
    assert _served(client) is None
    assert client.get("/wordiply").status_code == 200
    out = capsys.readouterr().out
    assert "check=wordiply_board_log_failed" in out and "check=wordiply_board_read_failed" in out


def test_the_admin_actions_write_the_right_rows(client, starter_db, monkeypatch):
    db = starter_db(rowcount=1)
    headers = _admin_headers(monkeypatch)
    client.post("/wordiply_admin/approve", headers=headers, data={"letters": "row", "played_on": "2026-11-23"})
    statements = [sql for sql, _ in db.executed]
    assert statements[0] == "START TRANSACTION"
    assert statements[1].startswith("DELETE FROM wordiply_boards WHERE played_on = %s AND source = %s")
    assert db.executed[1][1] == (DAY, wordiply.APPROVAL, "row")
    assert statements[2].startswith("INSERT INTO wordiply_boards (played_on, voter_hash, letters, source")
    assert db.executed[2][1][:4] == (DAY, wordiply.APPROVAL, "row", wordiply.APPROVAL)
    client.post("/wordiply_admin/unapprove", headers=headers, data={"played_on": "2026-11-23"})
    assert db.executed[-1] == ("DELETE FROM wordiply_boards WHERE played_on = %s AND source = %s",
                               (DAY, wordiply.APPROVAL))
    client.post("/wordiply_admin/remove", headers=headers, data={"letters": "row", "played_on": "2026-11-23"})
    assert db.executed[-1] == ("DELETE FROM wordiply_boards WHERE letters = %s AND played_on = %s "
                               "AND source <> %s", ("row", DAY, wordiply.APPROVAL))


def test_a_failed_approval_rolls_back_and_says_so(client, starter_db, monkeypatch):
    db = starter_db(fail="INSERT INTO wordiply_boards")
    resp = client.post("/wordiply_admin/approve", headers=_admin_headers(monkeypatch),
                       data={"letters": "row", "played_on": "2026-11-23"})
    assert "error=Database+error" in resp.headers["Location"]
    assert db.executed[-1][0] == "ROLLBACK" and db.commits == 0
