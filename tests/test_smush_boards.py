"""Smush's today's board (routes/smush.py): the board early players type in,
offered to the players after them, with hints for it.

Nothing here reaches a real database: secret_pass points at the production
JawsDB. Most tests swap the module's table functions for an in-memory
stand-in (MemoryBoards); the SQL itself is checked against test_blossom's
scripted fake. Each simulated player is a different X-Forwarded-For, which
is what helpers.client_ip reads: an IPv4 address each, or IPv6 ones where a
test needs players on one network. The board and the word list are made up,
so the hints can be checked word for word.
"""

import json
import re
import time
from datetime import date, datetime, timedelta

import pytest

import routes.smush as smush
from test_ad_slots import LIVE, slots  # noqa: F401
from test_blossom import _admin_headers, _ScriptedDB

# The real reader: conftest.py stands in an empty table for every test.
REAL_ROWS_LOGGED_BETWEEN = smush.rows_logged_between

DAY = date(2026, 10, 23)                      # a Friday
LETTERS = "eadilnrst"                         # gold E, then the other eight sorted
OTHER = "radeilnst"                           # another puzzle: gold R
WORDS = {"residential", "interlards", "tinsel", "lead", "rest", "eel",
         "esssssss",                          # 7 S: more uses than a fresh board has
         "ox", float("nan")}                  # off the board; and the raw list's one non-word


class MemoryBoards:
    """Stands in for smush_boards: store_board and the admin actions write
    here, and the readers read back, rows in routes.smush._COLUMNS order.
    One row per (letters, voter, day), as the table's unique key keeps it,
    and a later entry with no spicy letter keeps the one before (the SQL's
    COALESCE)."""

    def __init__(self):
        self.rows = {}
        self.clock = datetime(2026, 10, 23, 6, 0)
        self.reads = 0

    def store(self, day, voter, prefix, letters, spicy, source):
        self.clock += timedelta(minutes=1)
        before = self.rows.get((letters, voter, day))
        spicy = spicy or (before[1] if before and voter != smush.APPROVAL else None)
        self.rows[(letters, voter, day)] = (letters, spicy, source, voter, prefix, day, self.clock)

    def approve(self, day, letters, spicy):
        self.clock += timedelta(minutes=1)
        self.rows[(letters, smush.APPROVAL, day)] = (letters, spicy or None, smush.APPROVAL,
                                                     smush.APPROVAL, None, day, self.clock)

    def unapprove(self, letters, day):
        return 1 if self.rows.pop((letters, smush.APPROVAL, day), None) else 0

    def remove_puzzle(self, letters, day):
        gone = [key for key, row in self.rows.items()
                if row[0] == letters and row[5] == day and row[2] != smush.APPROVAL]
        for key in gone:
            del self.rows[key]
        return len(gone)

    def logged_between(self, first, last):
        self.reads += 1
        seen = {row[0] for row in self.rows.values() if first <= row[5] <= last}
        return [row for row in self.rows.values() if row[0] in seen]


@pytest.fixture
def boards(monkeypatch):
    """The in-memory table, with today's board live on /smush and no memo
    between reads, so these tests exercise the serve rules; the dark launch
    and the memo have tests of their own."""
    import crowd
    # The admin page's word lists and the vote paths: empty fakes, never the
    # real tables.
    monkeypatch.setattr(crowd, "db_cursor", _ScriptedDB())
    monkeypatch.setattr(smush, "db_cursor", _ScriptedDB())
    memory = MemoryBoards()
    monkeypatch.setattr(smush, "store_board", memory.store)
    monkeypatch.setattr(smush, "store_approval", memory.approve)
    monkeypatch.setattr(smush, "remove_approval", memory.unapprove)
    monkeypatch.setattr(smush, "remove_puzzle", memory.remove_puzzle)
    monkeypatch.setattr(smush, "rows_logged_between", memory.logged_between)
    monkeypatch.setattr(smush, "get_smush_words", lambda: set(WORDS))
    monkeypatch.setattr(smush, "_pacific_today", lambda: DAY)
    monkeypatch.setattr(smush, "SMUSH_BOARD_LIVE", True)
    monkeypatch.setattr(smush, "BOARD_CACHE_SECONDS", 0)
    return memory


def _player(n):
    """A simulated player: a number is an IPv4 address of their own, a
    string an address as given (IPv6 too)."""
    return {"X-Forwarded-For": f"198.51.100.{n}" if isinstance(n, int) else n}


def _log(client, player, letters=LETTERS, spicy="", source="typed", played_on=DAY,
         path="/smush", headers=None, outer=None):
    resp = client.post(path, headers={**_player(player), **(headers or {})}, json={
        "action": "log_board", "center": letters[0], "outer": outer or letters[1:],
        "spicy": spicy, "played_on": played_on.isoformat(), "source": source})
    assert resp.status_code == 200 and resp.get_json() == {"status": "success"}
    return resp


def _todays(client, played_on=DAY, player=99, path="/smush", headers=None):
    resp = client.post(path, headers={**_player(player), **(headers or {})}, json={
        "action": "todays_board", "played_on": played_on.isoformat()})
    assert resp.status_code == 200
    return resp.get_json()["board"]


def _served(client, **kw):
    """The board offered, as its center and other eight, or None."""
    board = _todays(client, **kw)
    return board and board["center"] + board["outer"]


# ---------------------------------------------------------------------------
# The serve rules, through the real route
# ---------------------------------------------------------------------------

def test_four_players_wait_and_the_fifth_serves_the_board(client, boards):
    for n in range(1, 5):
        _log(client, n)
    assert _served(client) is None
    _log(client, 5)
    board = _todays(client)
    assert board["center"] + board["outer"] == LETTERS
    assert board["date"] == "2026-10-23" and board["text"] == "E · A D I L N R S T"
    # What the page needs, and nothing about how it was chosen.
    assert set(board) == {"date", "center", "outer", "spicy", "text", "hints"}


def test_the_outer_letters_in_any_order_are_one_board(client, boards):
    # The game shuffles the eight for every player.
    for n, outer in enumerate(("adilnrst", "tsrnlida", "ndalirts", "rstadiln", "lindarst"), 1):
        _log(client, n, outer=outer)
    assert _served(client) == LETTERS
    assert len({row[0] for row in boards.rows.values()}) == 1


def test_players_on_one_network_count_once(client, boards):
    for n in range(1, 6):
        _log(client, f"2001:db8:aa:{n}::1")    # five /64s, one /48
    assert _served(client) is None
    for n in range(1, 5):
        _log(client, f"2001:db8:{n}::1")       # four more networks: five in all
    assert _served(client) == LETTERS


def test_a_players_second_entry_replaces_their_first(client, boards):
    for n in range(1, 5):
        _log(client, n)
    _log(client, 4, spicy="s")
    assert _served(client) is None and len(boards.rows) == 4


def test_an_approval_wins_and_the_latest_one_counts(client, boards):
    for n in range(1, 6):
        _log(client, n, OTHER)
    boards.approve(DAY, LETTERS, "t")
    assert _served(client) == LETTERS and _todays(client)["spicy"] == "t"
    boards.approve(DAY, OTHER, "")
    assert _served(client) == OTHER and _todays(client)["spicy"] == ""


def test_an_older_puzzle_is_never_todays_board_but_still_logs(client, boards):
    yesterday = DAY - timedelta(days=1)
    for n in range(1, 6):
        _log(client, n, played_on=yesterday)
    for n in range(6, 11):
        _log(client, n)
    assert _served(client) is None
    assert _served(client, played_on=yesterday) == LETTERS
    assert len(boards.rows) == 10


def test_one_wrong_clock_cannot_make_todays_puzzle_older(client, boards):
    _log(client, 1, played_on=DAY - timedelta(days=1))
    for n in range(2, 7):
        _log(client, n)
    assert _served(client) == LETTERS


def test_an_approval_serves_even_a_puzzle_first_seen_earlier(client, boards):
    for n in range(1, 4):
        _log(client, n, played_on=DAY - timedelta(days=1))
    boards.approve(DAY, LETTERS, "")
    assert _served(client) == LETTERS


def test_the_puzzle_most_players_entered_is_the_one_served(client, boards):
    for n in range(1, 6):
        _log(client, n, OTHER)                 # some other game's board: 5
    for n in range(6, 12):
        _log(client, n)                        # today's: 6
    assert _served(client) == LETTERS


def test_the_starting_spicy_letter_is_the_one_most_fresh_boards_set(client, boards):
    for n, spicy in ((1, "s"), (2, "s"), (3, "s"), (4, "t"), (5, "")):
        _log(client, n, spicy=spicy)
    assert _todays(client)["spicy"] == "s"
    # A player who has played on since sends no spicy letter: theirs stays.
    _log(client, 1)
    assert _todays(client)["spicy"] == "s"


def test_a_tie_on_the_spicy_letter_leaves_it_to_the_player(client, boards):
    for n, spicy in ((1, "s"), (2, "t"), (3, ""), (4, ""), (5, "")):
        _log(client, n, spicy=spicy)
    assert _served(client) == LETTERS and _todays(client)["spicy"] == ""


def test_an_edited_board_counts(client, boards):
    for n in range(1, 5):
        _log(client, n)
    _log(client, 5, source="edited")
    assert _served(client) == LETTERS


def test_a_player_whose_date_is_ahead_gets_their_own_days_board(client, boards):
    # Europe's evening: Pacific is still on DAY, those players are on DAY + 1.
    tomorrow = DAY + timedelta(days=1)
    for n in range(1, 6):
        _log(client, n, played_on=tomorrow)
    assert _served(client, played_on=tomorrow) == LETTERS
    assert _served(client) is None
    html = client.get("/smush").get_data(as_text=True)
    assert 'const BOARD_DATE = "2026-10-23";' in html
    assert '<div class="sm-today" id="sm-today" hidden>' in html


def test_a_wrong_clock_stores_and_serves_nothing(client, boards):
    far = DAY + timedelta(days=2)
    _log(client, 1, played_on=far)
    assert not boards.rows
    assert _todays(client, played_on=far) is None


@pytest.mark.parametrize("body", [
    {"center": "e", "outer": "adilnrst", "played_on": "2026-13-01", "source": "typed"},
    {"center": "e", "outer": "adilnrst", "source": "typed"},
    {"center": "e", "outer": "adilnrss", "played_on": "2026-10-23", "source": "typed"},
    {"center": "e", "outer": "adilnrse", "played_on": "2026-10-23", "source": "typed"},
    {"center": "e", "outer": "adilnrs", "played_on": "2026-10-23", "source": "typed"},
    {"center": "é", "outer": "adilnrst", "played_on": "2026-10-23", "source": "typed"},
    {"center": "e", "outer": "adilnrst", "spicy": "e", "played_on": "2026-10-23", "source": "typed"},
    {"center": "e", "outer": "adilnrst", "spicy": "x", "played_on": "2026-10-23", "source": "typed"},
    {"center": "e", "outer": "adilnrst", "played_on": "2026-10-23", "source": "loaded"},
    {"center": "e", "outer": "adilnrst", "played_on": "2026-10-23"},
])
def test_anything_but_a_whole_board_is_a_400(client, boards, body):
    resp = client.post("/smush", json={"action": "log_board", **body})
    assert resp.status_code == 400
    assert not boards.rows


def test_a_bad_date_asking_for_the_board_is_a_400(client, boards):
    resp = client.post("/smush", json={"action": "todays_board", "played_on": "soon"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def test_the_page_comes_with_the_pacific_dates_board_and_hints(client, boards):
    for n in range(1, 6):
        _log(client, n)
    html = client.get("/smush").get_data(as_text=True)
    assert '<div class="sm-today" id="sm-today">' in html
    assert "E · A D I L N R S T" in html
    assert "Today&#39;s Smush hints - Friday, October 23" in html
    assert ("<summary>Hint 1</summary><p>2 pangrams today: 10 and 11 letters. "
            "Longest word: 11 letters.</p>") in html
    assert "<summary>Show the pangrams</summary><p>INTERLARDS, RESIDENTIAL</p>" in html
    # It sits above the tiles, and the page hides it before it paints for a
    # player whose date differs.
    assert html.index('id="sm-today"') < html.index('id="smush-board"')
    assert 'const BOARD_DATE = "2026-10-23";' in html
    assert 'if (mine !== "2026-10-23")' in html


def test_no_board_yet_means_no_bar_and_no_hints(client, boards):
    for n in range(1, 5):
        _log(client, n)
    html = client.get("/smush").get_data(as_text=True)
    assert '<div class="sm-today" id="sm-today" hidden>' in html
    assert '<details class="sm-hints" id="sm-hints" hidden>' in html
    assert "<summary>Hint 1</summary>" not in html


def test_the_faq_answer_comes_with_the_board(client, boards, monkeypatch):
    def faq_names():
        html = client.get("/smush").get_data(as_text=True)
        blocks = [json.loads(block) for block in
                  re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
        faq = next(block for block in blocks if block.get("@type") == "FAQPage")
        return [q["name"] for q in faq["mainEntity"]], html

    names, html = faq_names()
    assert "Do I have to type today's letters?" in names
    assert ">Do I have to type today's letters?<" in html
    monkeypatch.setattr(smush, "SMUSH_BOARD_LIVE", False)
    names, html = faq_names()
    assert "Do I have to type today's letters?" not in names
    assert "Do I have to type today's letters?" not in html


# ---------------------------------------------------------------------------
# The hints
# ---------------------------------------------------------------------------

def _hints(monkeypatch, words, letters=LETTERS):
    monkeypatch.setattr(smush, "get_smush_words", lambda: set(words))
    return smush.smush_hints(letters, DAY)


def test_hints_lead_up_to_the_pangrams(monkeypatch):
    hints = _hints(monkeypatch, WORDS)
    assert hints["heading"] == "Today's Smush hints - Friday, October 23"
    assert hints["pangrams"] == ["interlards", "residential"]
    assert [step["summary"] for step in hints["steps"]] == ["Hint 1", "Hint 2", "Hint 3",
                                                            "Show the pangrams"]
    assert [step["text"] for step in hints["steps"]] == [
        "2 pangrams today: 10 and 11 letters. Longest word: 11 letters.",
        "The pangrams start with I and R.",
        "IN________ (10), RE_________ (11)",
        "INTERLARDS, RESIDENTIAL",
    ]


def test_hints_only_count_words_a_fresh_board_can_play(monkeypatch):
    # Seven S's is more uses than a game starts with; three letters is
    # Smush's shortest word.
    hints = _hints(monkeypatch, {"esssssss", "eel", "tee"})
    assert hints["longest"] == 3
    assert hints["steps"][0]["text"] == "No pangram today. Longest word: 3 letters."


def test_no_hints_when_no_word_fits(monkeypatch):
    assert _hints(monkeypatch, {"ox", float("nan")}) is None


# ---------------------------------------------------------------------------
# The memo, and a database that fails
# ---------------------------------------------------------------------------

def test_the_board_is_read_at_most_once_a_minute(client, boards, monkeypatch):
    monkeypatch.setattr(smush, "BOARD_CACHE_SECONDS", 60)
    for n in range(1, 6):
        _log(client, n)
    boards.reads = 0
    client.get("/smush")
    client.get("/smush")
    _todays(client)
    assert boards.reads == 1
    # An admin change shows at once.
    client.post("/smush_admin/approve_board", headers=_admin_headers(monkeypatch),
                data={"letters": OTHER, "spicy": "", "played_on": "2026-10-23"})
    assert _served(client) == OTHER and boards.reads == 2


def test_a_stale_board_is_kept_out_of_the_way_while_it_is_read_again(boards, monkeypatch):
    stale = {"date": "2026-10-23", "center": "e", "outer": "adilnrst"}
    smush._board_cache[DAY] = (0.0, stale)
    seen = []

    def read(first, last):
        seen.append(smush._board_cache[DAY])
        return []

    monkeypatch.setattr(smush, "rows_logged_between", read)
    assert smush._board_for(DAY) is None
    [(expires, during)] = seen
    assert during is stale and expires > time.monotonic()


def test_a_failed_read_costs_the_board_never_the_page(client, boards, monkeypatch, capsys):
    from monitoring import alerts
    alerts.reset_throttle()
    calls = []

    def broken(first, last):
        calls.append(first)
        raise RuntimeError("Table 'smush_boards' doesn't exist")

    monkeypatch.setattr(smush, "rows_logged_between", broken)
    resp = client.get("/smush")
    assert resp.status_code == 200
    assert '<div class="sm-today" id="sm-today" hidden>' in resp.get_data(as_text=True)
    assert _todays(client) is None
    assert len(calls) == 1                     # not tried again for BOARD_RETRY_SECONDS
    assert "check=smush_board_read_failed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Live on /smush, the dark launch the flag goes back to, and /smush_dev2
# ---------------------------------------------------------------------------

def test_todays_board_is_live_on_smush(client):
    # James's call, 2026-10-24. The other tests here set the flag; this one
    # reads it as the code ships, so going dark again is a deliberate change.
    html = client.get("/smush").get_data(as_text=True)
    assert 'id="sm-today"' in html and ">Do I have to type today's letters?<" in html


def test_while_dark_smush_logs_boards_and_serves_none(client, boards, monkeypatch):
    monkeypatch.setattr(smush, "SMUSH_BOARD_LIVE", False)
    for n in range(1, 6):
        _log(client, n)
    assert len(boards.rows) == 5
    boards.reads = 0
    html = client.get("/smush").get_data(as_text=True)
    assert 'id="sm-today"' not in html and 'id="sm-hints"' not in html
    assert boards.reads == 0                   # the public page never reads the table
    assert _todays(client) is None             # not even to a direct call


def test_smush_dev2_shows_the_board_and_hints_while_dark(client, boards, monkeypatch):
    monkeypatch.setattr(smush, "SMUSH_BOARD_LIVE", False)
    for n in range(1, 6):
        _log(client, n)
    headers = _admin_headers(monkeypatch)
    html = client.get("/smush_dev2", headers=headers).get_data(as_text=True)
    assert '<div class="sm-today" id="sm-today">' in html and "E · A D I L N R S T" in html
    assert "Today&#39;s Smush hints - Friday, October 23" in html
    assert ">Do I have to type today's letters?<" in html
    assert "/smush_dev2 - only you can see this copy" in html
    assert 'const POST_URL = "/smush_dev2";' in html
    # No ICE COLD: this is the public page, to be released.
    assert "ice" not in re.sub(r"[^a-z]", " ", html.lower()).split()
    assert _served(client, path="/smush_dev2", headers=headers) == LETTERS
    # Its own boards count like any player's.
    _log(client, 6, OTHER, path="/smush_dev2", headers=headers)
    assert OTHER in {row[0] for row in boards.rows.values()}


def test_smush_dev2_solves_and_its_votes_are_tagged_dev(client, monkeypatch):
    import crowd
    import routes.wordgames as wordgames
    from data import words
    monkeypatch.setattr(wordgames, "get_smush_words", lambda: words)
    headers = _admin_headers(monkeypatch)
    resp = client.post("/smush_dev2", headers=headers, json={
        "center": "e", "outer_uses": {ch: 5 for ch in LETTERS[1:]}, "spicy": ""})
    assert resp.status_code == 200 and resp.get_json()["results"]
    db = _ScriptedDB({"WHERE voter_hash = %s AND created_at": (0,),
                      "WHERE voter_hash = %s AND vote = 'invalid'": (1,),
                      "FROM smush_word_votes v WHERE v.word": (1,)})
    monkeypatch.setattr(crowd, "db_cursor", db)
    monkeypatch.setattr(smush, "db_cursor", db)
    monkeypatch.setattr(smush, "_smush_words", {"value": None, "expires": 0.0})
    client.post("/smush_dev2", headers=headers, json={
        "action": "reject", "word": "tinsel", "center": "e", "outer": LETTERS[1:],
        "mode": "pts"})
    [row] = db.log_rows("smush")
    assert row["mode"] == "dev_pts"


def test_with_the_switch_on_smush_matches_smush_dev2(client, boards):
    for n in range(1, 6):
        _log(client, n)
    html = client.get("/smush").get_data(as_text=True)
    assert '<div class="sm-today" id="sm-today">' in html
    assert ">Do I have to type today's letters?<" in html
    assert "only you can see this copy" not in html
    assert 'const POST_URL = "/smush";' in html


def test_smush_dev2_is_not_a_page_visit_and_carries_no_ad(client, boards, slots, monkeypatch):
    import extensions
    headers = _admin_headers(monkeypatch)
    html = client.get("/smush_dev2", base_url=LIVE, headers=headers).get_data(as_text=True)
    assert 'id="jj-ad"' not in html
    assert extensions._log_queue.qsize() == 0
    assert 'id="jj-ad"' in client.get("/smush", base_url=LIVE).get_data(as_text=True)
    assert extensions._log_queue.qsize() == 1


# ---------------------------------------------------------------------------
# /smush_admin's Today's Board tab
# ---------------------------------------------------------------------------

def test_admin_board_actions_need_the_password(client, boards):
    for action in ("approve_board", "unapprove_board", "remove_board"):
        assert client.post(f"/smush_admin/{action}", data={}).status_code == 401


def test_admin_opens_on_the_board_tab_and_shows_each_board(client, boards, monkeypatch):
    for n, spicy in ((1, "s"), (2, "s"), (3, "t"), (4, "")):
        _log(client, n, spicy=spicy)
    _log(client, 5, OTHER)
    html = client.get("/smush_admin", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert 'id="board-tab" class="tab-content active"' in html
    assert "<h3>2026-10-23 (today, Pacific)</h3>" in html
    assert "E · A D I L N R S T" in html and "R · A D E I L N S T" in html
    assert "S (2), T (1)" in html
    assert "needs 1 more player" in html
    assert f"served once {smush.SERVE_CROWD} of them have" in html
    assert "logging only; served on /smush_dev2" not in html     # live in this fixture


def test_an_approval_from_the_admin_tab_serves_at_once_and_undoes(client, boards, monkeypatch):
    headers = _admin_headers(monkeypatch)
    _log(client, 1, spicy="s")
    resp = client.post("/smush_admin/approve_board", headers=headers, data={
        "letters": LETTERS, "spicy": "s", "played_on": "2026-10-23", "tab": "board"})
    assert resp.status_code == 302 and "success=Approved" in resp.headers["Location"]
    assert "tab=board" in resp.headers["Location"]
    assert _served(client) == LETTERS and _todays(client)["spicy"] == "s"
    resp = client.post("/smush_admin/unapprove_board", headers=headers,
                       data={"letters": LETTERS, "played_on": "2026-10-23"})
    assert "success=Approval+undone" in resp.headers["Location"]
    assert _served(client) is None
    assert len(boards.rows) == 1               # the player's board stays


def test_the_approve_form_takes_the_eight_in_any_order(client, boards, monkeypatch):
    form = {"center": "E", "outer": "T S R N L I D A", "spicy": "N", "played_on": "2026-10-23"}
    resp = client.post("/smush_admin/approve_board", headers=_admin_headers(monkeypatch), data=form)
    assert "success=Approved" in resp.headers["Location"]
    assert _served(client) == LETTERS and _todays(client)["spicy"] == "n"


@pytest.mark.parametrize("form", [
    {"center": "E", "outer": "TSRNLID", "played_on": "2026-10-23"},
    {"center": "E", "outer": "TSRNLIDE", "played_on": "2026-10-23"},
    {"center": "E", "outer": "TSRNLIDA", "spicy": "E", "played_on": "2026-10-23"},
    {"letters": "eadilnrsx", "spicy": "q", "played_on": "2026-10-23"},
    {"letters": LETTERS, "played_on": "tomorrow"},
])
def test_the_admin_refuses_anything_but_a_board(client, boards, monkeypatch, form):
    resp = client.post("/smush_admin/approve_board", headers=_admin_headers(monkeypatch), data=form)
    assert resp.status_code == 302 and "error=" in resp.headers["Location"]
    assert not boards.rows


def test_remove_deletes_that_puzzles_player_entries_only(client, boards, monkeypatch):
    for n in range(1, 6):
        _log(client, n)
    _log(client, 6, OTHER)
    boards.approve(DAY, LETTERS, "")
    resp = client.post("/smush_admin/remove_board", headers=_admin_headers(monkeypatch),
                       data={"letters": LETTERS, "played_on": "2026-10-23"})
    assert "success=Removed+5+entries" in resp.headers["Location"]
    assert {(row[0], row[2]) for row in boards.rows.values()} == {(LETTERS, "admin"),
                                                                  (OTHER, "typed")}


def test_the_board_tab_works_when_the_word_lists_fail(client, boards, monkeypatch):
    import crowd
    for n in range(1, 6):
        _log(client, n)
    monkeypatch.setattr(crowd, "db_cursor", _ScriptedDB(fail="smush_"))
    resp = client.get("/smush_admin", headers=_admin_headers(monkeypatch))
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Couldn&#39;t read the word lists" in html or "Couldn't read the word lists" in html
    assert "E · A D I L N R S T" in html


def test_admin_says_when_the_board_table_is_missing(client, board_db, monkeypatch):
    board_db(fail="smush_boards")
    resp = client.get("/smush_admin", headers=_admin_headers(monkeypatch))
    assert resp.status_code == 200
    assert "Has mysql_tables/smush_boards.sql been run?" in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# The SQL, against the scripted fake
# ---------------------------------------------------------------------------

@pytest.fixture
def board_db(monkeypatch):
    import crowd
    monkeypatch.setattr(crowd, "db_cursor", _ScriptedDB())     # as in `boards`
    monkeypatch.setattr(smush, "rows_logged_between", REAL_ROWS_LOGGED_BETWEEN)
    monkeypatch.setattr(smush, "get_smush_words", lambda: set(WORDS))
    monkeypatch.setattr(smush, "_pacific_today", lambda: DAY)
    monkeypatch.setattr(smush, "SMUSH_BOARD_LIVE", True)

    def install(answers=None, rows=None, rowcount=1, fail=None):
        db = _ScriptedDB(answers, rows, rowcount, fail)
        monkeypatch.setattr(smush, "db_cursor", db)
        return db

    return install


def test_a_board_is_stored_as_its_letters_and_fresh_spicy(client, board_db):
    db = board_db({"SELECT COUNT(*) FROM smush_boards": (0,)})
    _log(client, 1, spicy="S", outer="TSRNLIDA")
    [(sql, params)] = db.statements("INSERT INTO smush_boards")
    assert params[0] == DAY and re.fullmatch(r"[0-9a-f]{32}", params[1])
    # The network: an HMAC too, never the address, and not the player hash.
    assert re.fullmatch(r"[0-9a-f]{32}", params[2]) and params[2] != params[1]
    assert params[3:6] == (LETTERS, "s", "typed")
    assert not any("198.51.100" in str(p) for p in params)
    # The row being replaced is named by its table: with the `new` alias, a
    # bare `spicy` is ambiguous (MySQL error 1052, caught by EXPLAIN on prod).
    assert "spicy = COALESCE(new.spicy, smush_boards.spicy)" in sql
    assert "prefix_hash = new.prefix_hash" in sql
    assert db.commits == 1
    # No spicy letter is stored as NULL, so it can't overwrite one.
    _log(client, 1)
    assert db.statements("INSERT INTO smush_boards")[-1][1][4] is None


def test_the_daily_cap_stops_a_flood(client, board_db):
    db = board_db({"SELECT COUNT(*) FROM smush_boards": (smush.BOARDS_PER_PLAYER_PER_DAY,)})
    _log(client, 1)
    assert not db.statements("INSERT INTO smush_boards")
    [(sql, params)] = db.statements("SELECT COUNT(*) FROM smush_boards")
    assert "AND letters <> %s" in sql and params[1:] == (DAY, LETTERS)


def test_the_reader_asks_for_every_row_of_the_days_puzzles(client, board_db):
    db = board_db()
    _todays(client)
    [(sql, params)] = db.executed
    assert "WHERE letters IN (SELECT letters FROM smush_boards WHERE played_on BETWEEN" in sql
    assert params == (DAY, DAY)


def test_a_missing_table_costs_the_board_never_the_request(client, board_db, capsys):
    from monitoring import alerts
    alerts.reset_throttle()
    board_db(fail="smush_boards")
    _log(client, 1)
    assert _todays(client) is None
    assert client.get("/smush").status_code == 200
    out = capsys.readouterr().out
    assert "check=smush_board_log_failed" in out and "check=smush_board_read_failed" in out


def test_the_admin_actions_write_the_right_rows(client, board_db, monkeypatch):
    db = board_db(rowcount=1)
    headers = _admin_headers(monkeypatch)
    client.post("/smush_admin/approve_board", headers=headers,
                data={"letters": LETTERS, "spicy": "s", "played_on": "2026-10-23"})
    [(sql, params)] = db.statements("INSERT INTO smush_boards")
    assert params[:5] == (DAY, "admin", LETTERS, "s", "admin")
    assert "ON DUPLICATE KEY UPDATE spicy = new.spicy" in sql
    client.post("/smush_admin/unapprove_board", headers=headers,
                data={"letters": LETTERS, "played_on": "2026-10-23"})
    client.post("/smush_admin/remove_board", headers=headers,
                data={"letters": LETTERS, "played_on": "2026-10-23"})
    assert [params for _sql, params in db.statements("DELETE FROM smush_boards")] == [
        (LETTERS, DAY, "admin"), (LETTERS, DAY, "admin")]
    assert db.statements("source <> %s")         # Remove never takes an approval


# ---------------------------------------------------------------------------
# What players see
# ---------------------------------------------------------------------------

# tests/test_smush_crowd.py's list, tests/test_ribbit_boards.py's, and this one's.
NEVER_SAID = ("crowd_remove_votes", "crowd_add_votes", "different players", "another player",
              "players flag", "players agreed", "for everyone",
              "2 players", "3 players", "two players", "three players", "serve_",
              "5 players", "five players", "approv", "more players")


def test_players_are_never_told_how_a_board_is_chosen(client, boards, monkeypatch):
    for n in range(1, 6):
        _log(client, n, spicy="s")
    texts = [
        client.get("/smush").get_data(as_text=True),
        client.get("/smush_dev2", headers=_admin_headers(monkeypatch)).get_data(as_text=True),
        json.dumps(_todays(client)),
        json.dumps(_todays(client, played_on=DAY + timedelta(days=1))),
        json.dumps(_log(client, 6).get_json()),
    ]
    for text in texts:
        lowered = text.lower()
        for phrase in NEVER_SAID:
            assert phrase not in lowered, f"a player could read {phrase!r}"
