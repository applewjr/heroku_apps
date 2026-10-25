"""Blossom's today's board (routes/blossom.py): the board early players type
in, offered to the players after them, with hints for it.

Nothing here reaches a real database: secret_pass points at the production
JawsDB. Most tests swap the module's table functions for an in-memory
stand-in (MemoryBoards); the SQL itself is checked against test_blossom's
scripted fake. Each simulated player is a different X-Forwarded-For, which
is what helpers.client_ip reads: an IPv4 address each, or IPv6 ones where a
test needs players on one network. The word list is a small fake, so the
hints can be checked word for word.
"""

import json
import re
import time
from datetime import date, datetime, timedelta

import pytest

import routes.blossom as blossom
from test_ad_slots import LIVE, slots  # noqa: F401
from test_blossom import (CENTER, PETALS, _admin_headers, _ScriptedDB, _votes,  # noqa: F401
                          crowd_db, fresh_word_cache)

# The real reader: conftest.py stands in an empty table for every test.
REAL_ROWS_LOGGED_BETWEEN = blossom.rows_logged_between

DAY = date(2026, 10, 23)                      # a Friday
# Today's board as a game shows it: the center, then the petals in the
# solver's spots (top-left, top-right, middle-left, middle-right,
# bottom-left, bottom-right).
BOARD = "elction"
LETTERS = "ecilnot"                           # the puzzle: center, petals sorted
OTHER = "eltcion"                             # the same letters, two petals swapped
STRANGER = "rpaenst"                          # another puzzle altogether
WORDS = {"election", "lection", "notice", "tone", "lotion", "xylophone", "eel"}


class MemoryBoards:
    """Stands in for blossom_boards: store_board and the admin actions write
    here, and the readers read back, rows in routes.blossom._COLUMNS order.
    One row per (letters, voter, day), as the table's unique key keeps it."""

    def __init__(self):
        self.rows = {}
        self.clock = datetime(2026, 10, 23, 6, 0)
        self.reads = 0

    def store(self, day, voter, prefix, letters, board, source):
        self.clock += timedelta(minutes=1)
        self.rows[(letters, voter, day)] = (letters, board, source, voter, prefix, day, self.clock)

    def approve(self, day, letters, board):
        self.store(day, blossom.APPROVAL, None, letters, board, blossom.APPROVAL)

    def unapprove(self, letters, day):
        return 1 if self.rows.pop((letters, blossom.APPROVAL, day), None) else 0

    def remove_state(self, letters, board, day):
        gone = [key for key, row in self.rows.items()
                if row[0] == letters and row[1] == board and row[5] == day
                and row[2] != blossom.APPROVAL]
        for key in gone:
            del self.rows[key]
        return len(gone)

    def with_letters(self, letters):
        return [row for row in self.rows.values() if row[0] == letters]

    def logged_between(self, first, last):
        self.reads += 1
        seen = {row[0] for row in self.rows.values() if first <= row[5] <= last}
        return [row for row in self.rows.values() if row[0] in seen]


@pytest.fixture
def boards(monkeypatch):
    """The in-memory table, with today's board live on /blossom and no
    memo between reads, so these tests exercise the serve rules; the dark
    launch and the memo have tests of their own."""
    import crowd
    # The admin page's word lists and feedback, and the vote paths: empty
    # fakes, never the real tables.
    monkeypatch.setattr(crowd, "db_cursor", _ScriptedDB())
    monkeypatch.setattr(blossom, "db_cursor", _ScriptedDB())
    memory = MemoryBoards()
    monkeypatch.setattr(blossom, "store_board", memory.store)
    monkeypatch.setattr(blossom, "store_approval", memory.approve)
    monkeypatch.setattr(blossom, "remove_approval", memory.unapprove)
    monkeypatch.setattr(blossom, "remove_state", memory.remove_state)
    monkeypatch.setattr(blossom, "rows_with_letters", memory.with_letters)
    monkeypatch.setattr(blossom, "rows_logged_between", memory.logged_between)
    monkeypatch.setattr(blossom, "get_filtered_blossom_words", lambda: set(WORDS))
    monkeypatch.setattr(blossom, "_pacific_today", lambda: DAY)
    monkeypatch.setattr(blossom, "TODAYS_BOARD_LIVE", True)
    monkeypatch.setattr(blossom, "BOARD_CACHE_SECONDS", 0)
    return memory


def _player(n):
    """A simulated player: a number is an IPv4 address of their own, a
    string an address as given (IPv6 too)."""
    return {"X-Forwarded-For": f"198.51.100.{n}" if isinstance(n, int) else n}


def _log(client, player, board=BOARD, source="typed", played_on=DAY, path="/blossom", headers=None):
    resp = client.post(path, headers={**_player(player), **(headers or {})}, json={
        "action": "log_board", "center": board[0], "petals": board[1:],
        "played_on": played_on.isoformat(), "source": source})
    assert resp.status_code == 200 and resp.get_json() == {"status": "success"}
    return resp


def _todays(client, played_on=DAY, player=99, path="/blossom", headers=None):
    resp = client.post(path, headers={**_player(player), **(headers or {})}, json={
        "action": "todays_board", "played_on": played_on.isoformat()})
    assert resp.status_code == 200
    return resp.get_json()["board"]


def _served(client, **kw):
    """The board offered, as its center and petals in spot order, or None."""
    board = _todays(client, **kw)
    return board and board["center"] + board["petals"]


# ---------------------------------------------------------------------------
# The serve rules, through the real route
# ---------------------------------------------------------------------------

def test_four_players_wait_and_the_fifth_serves_the_board(client, boards):
    for n in range(1, 5):
        _log(client, n)
    assert _served(client) is None
    _log(client, 5)
    board = _todays(client)
    assert board["center"] + board["petals"] == BOARD
    assert board["date"] == "2026-10-23" and board["text"] == "E · L C T I O N"
    # What the page needs, and nothing about how it was chosen.
    assert set(board) == {"date", "center", "petals", "text", "hints"}


def test_the_same_letters_in_other_spots_are_another_board(client, boards):
    for n in range(1, 4):
        _log(client, n)
    for n in range(4, 6):
        _log(client, n, OTHER)
    # Five players on the puzzle, but no five on one board.
    assert _served(client) is None


def test_most_players_win_and_a_tie_goes_to_the_first_to_five(client, boards):
    for n in range(1, 6):
        _log(client, n)
    for n in range(6, 11):
        _log(client, n, OTHER)
    assert _served(client) == BOARD
    # The other spots pull ahead: the board on offer switches.
    _log(client, 11, OTHER)
    assert _served(client) == OTHER


def test_an_edited_board_counts(client, boards):
    for n in range(1, 5):
        _log(client, n)
    _log(client, 5, source="edited")
    assert _served(client) == BOARD


def test_a_players_new_spots_replace_their_old_ones(client, boards):
    for n in range(1, 5):
        _log(client, n)
    _log(client, 5, OTHER)
    assert _served(client) is None
    _log(client, 5)
    assert _served(client) == BOARD
    assert len(boards.rows) == 5


def test_players_on_one_network_count_once(client, boards):
    for n in range(1, 6):
        _log(client, f"2001:db8:aa:{n}::1")    # five /64s, one /48
    assert _served(client) is None
    for n in range(1, 5):
        _log(client, f"2001:db8:{n}::1")       # four more networks: five in all
    assert _served(client) == BOARD


def test_an_approval_wins_and_the_latest_one_counts(client, boards):
    for n in range(1, 6):
        _log(client, n, OTHER)
    boards.approve(DAY, LETTERS, BOARD)
    assert _served(client) == BOARD
    boards.approve(DAY, LETTERS, OTHER)       # the same puzzle, other spots: replaces it
    assert _served(client) == OTHER


def test_an_older_puzzle_is_never_todays_board_but_still_logs(client, boards):
    yesterday = DAY - timedelta(days=1)
    for n in range(1, 6):
        _log(client, n, played_on=yesterday)
    for n in range(6, 11):
        _log(client, n)
    assert _served(client) is None
    assert _served(client, played_on=yesterday) == BOARD
    assert len(boards.rows) == 10


def test_one_wrong_clock_cannot_make_todays_puzzle_older(client, boards):
    _log(client, 1, played_on=DAY - timedelta(days=1))
    for n in range(2, 7):
        _log(client, n)
    assert _served(client) == BOARD


def test_an_approval_serves_even_a_puzzle_first_seen_earlier(client, boards):
    for n in range(1, 4):
        _log(client, n, played_on=DAY - timedelta(days=1))
    boards.approve(DAY, LETTERS, BOARD)
    assert _served(client) == BOARD


def test_a_smaller_puzzle_never_stands_in_for_the_biggest(client, boards):
    for n in range(1, 5):
        _log(client, n)                        # today's puzzle: 4 on one board,
    for n in range(5, 8):
        _log(client, n, OTHER)                 # 3 on another, none settled
    for n in range(8, 14):
        _log(client, n, STRANGER)              # some other game's board: 6, settled
    assert _served(client) is None


def test_a_player_whose_date_is_ahead_gets_their_own_days_board(client, boards):
    # Europe's evening: Pacific is still on DAY, those players are on DAY + 1.
    tomorrow = DAY + timedelta(days=1)
    for n in range(1, 6):
        _log(client, n, played_on=tomorrow)
    assert _served(client, played_on=tomorrow) == BOARD
    assert _served(client) is None
    html = client.get("/blossom").get_data(as_text=True)
    assert 'const BOARD_DATE = "2026-10-23";' in html
    assert '<div class="bl-today" id="bl-today" hidden>' in html


def test_a_wrong_clock_stores_and_serves_nothing(client, boards):
    far = DAY + timedelta(days=2)
    _log(client, 1, played_on=far)
    assert not boards.rows
    assert _todays(client, played_on=far) is None


@pytest.mark.parametrize("body", [
    {"center": "e", "petals": "lction", "played_on": "2026-13-01", "source": "typed"},
    {"center": "e", "petals": "lction", "played_on": "23/10/2026", "source": "typed"},
    {"center": "e", "petals": "lction", "source": "typed"},
    {"center": "e", "petals": "lctioe", "played_on": "2026-10-23", "source": "typed"},
    {"center": "é", "petals": "lction", "played_on": "2026-10-23", "source": "typed"},
    {"center": "e", "petals": "lctio", "played_on": "2026-10-23", "source": "typed"},
    {"center": "e", "petals": "lction", "played_on": "2026-10-23", "source": "loaded"},
    {"center": "e", "petals": "lction", "played_on": "2026-10-23"},
])
def test_anything_but_a_whole_board_is_a_400(client, boards, body):
    resp = client.post("/blossom", json={"action": "log_board", **body})
    assert resp.status_code == 400
    assert not boards.rows


def test_a_bad_date_asking_for_the_board_is_a_400(client, boards):
    resp = client.post("/blossom", json={"action": "todays_board", "played_on": "soon"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def test_the_page_comes_with_the_pacific_dates_board_and_hints(client, boards):
    for n in range(1, 6):
        _log(client, n)
    html = client.get("/blossom").get_data(as_text=True)
    assert '<div class="bl-today" id="bl-today">' in html
    assert "E · L C T I O N" in html
    assert "Today&#39;s Blossom hints - Friday, October 23" in html
    assert ("<summary>Hint 1</summary><p>2 pangrams today: 7 and 8 letters. "
            "Longest word: 8 letters.</p>") in html
    assert "<summary>Show the pangrams</summary><p>LECTION, ELECTION</p>" in html
    # The page hides it before it paints for a player whose date differs.
    assert 'const BOARD_DATE = "2026-10-23";' in html
    assert 'if (mine !== "2026-10-23")' in html


def test_no_board_yet_means_no_bar_and_no_hints(client, boards):
    for n in range(1, 5):
        _log(client, n)
    html = client.get("/blossom").get_data(as_text=True)
    assert '<div class="bl-today" id="bl-today" hidden>' in html
    assert '<details class="bl-hints" id="bl-hints" hidden>' in html
    assert "<summary>Hint 1</summary>" not in html


def test_a_results_page_keeps_how_the_letters_came(client, boards):
    form = {"must_have": "e", "may_have": "lction", "petal_letter": "l"}
    html = client.post("/blossom", data={**form, "board_source": "loaded"}).get_data(as_text=True)
    assert '<input type="hidden" name="board_source" value="loaded">' in html
    html = client.post("/blossom", data={**form, "board_source": "<b>"}).get_data(as_text=True)
    assert '<input type="hidden" name="board_source" value="typed">' in html
    # No bar or hints on a results page.
    assert 'id="bl-today"' not in html


def test_the_faq_answer_comes_with_the_board(client, boards, monkeypatch):
    def faq_names():
        html = client.get("/blossom").get_data(as_text=True)
        blocks = [json.loads(block) for block in
                  re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
        faq = next(block for block in blocks if block.get("@type") == "FAQPage")
        return [q["name"] for q in faq["mainEntity"]], html

    names, html = faq_names()
    assert "Do I have to type today's letters?" in names
    assert ">Do I have to type today's letters?<" in html
    monkeypatch.setattr(blossom, "TODAYS_BOARD_LIVE", False)
    names, html = faq_names()
    assert "Do I have to type today's letters?" not in names
    assert "Do I have to type today's letters?" not in html


# ---------------------------------------------------------------------------
# The hints
# ---------------------------------------------------------------------------

def _hints(monkeypatch, words):
    monkeypatch.setattr(blossom, "get_filtered_blossom_words", lambda: set(words))
    return blossom.blossom_hints(BOARD[0], BOARD[1:], DAY)


def test_hints_lead_up_to_the_pangrams(monkeypatch):
    hints = _hints(monkeypatch, WORDS)
    assert hints["heading"] == "Today's Blossom hints - Friday, October 23"
    assert hints["pangrams"] == ["lection", "election"]
    assert [step["summary"] for step in hints["steps"]] == ["Hint 1", "Hint 2", "Hint 3",
                                                            "Show the pangrams"]
    assert [step["text"] for step in hints["steps"]] == [
        "2 pangrams today: 7 and 8 letters. Longest word: 8 letters.",
        "The pangrams start with L and E.",
        "LE_____ (7), EL______ (8)",
        "LECTION, ELECTION",
    ]


def test_one_pangram_is_one_pangram(monkeypatch):
    hints = _hints(monkeypatch, {"election", "tone"})
    assert [step["text"] for step in hints["steps"]][:2] == [
        "1 pangram today: 8 letters. Longest word: 8 letters.",
        "The pangram starts with E."]
    assert hints["steps"][3]["summary"] == "Show the pangram"


def test_many_pangrams_give_a_range(monkeypatch):
    hints = _hints(monkeypatch, WORDS | {"collection", "centillion"})
    assert hints["steps"][0]["text"] == ("4 pangrams today, 7 to 10 letters long. "
                                         "Longest word: 10 letters.")
    assert hints["steps"][1]["text"] == "The pangrams start with L, E, C and C."


def test_a_long_list_is_cut_to_the_shortest_but_all_are_shown(monkeypatch):
    # Letters as common as R P A E N S T give the real list 29 pangrams.
    hints = _hints(monkeypatch, {"lection" + "e" * k for k in range(12)})
    steps = [step["text"] for step in hints["steps"]]
    assert steps[0] == "12 pangrams today, 7 to 18 letters long. Longest word: 18 letters."
    assert steps[1] == ("The 10 shortest pangrams start with "
                        "L, L, L, L, L, L, L, L, L and L.")
    assert steps[2].count(" (") == 10 and steps[2].endswith(", and 2 more")
    assert len(steps[3].split(", ")) == 12


def test_no_pangram_leads_up_to_the_longest_words(monkeypatch):
    hints = _hints(monkeypatch, {"notice", "tone", "cent"})
    assert [step["text"] for step in hints["steps"]] == [
        "No pangram today. Longest word: 6 letters.",
        "The longest word starts with N.",
        "NO____ (6)",
        "NOTICE",
    ]
    assert hints["steps"][3]["summary"] == "Show the longest word"


def test_no_hints_when_no_word_fits(monkeypatch):
    assert _hints(monkeypatch, {"xylophone"}) is None


# ---------------------------------------------------------------------------
# The memo, and a database that fails
# ---------------------------------------------------------------------------

def test_the_board_is_read_at_most_once_a_minute(client, boards, monkeypatch):
    monkeypatch.setattr(blossom, "BOARD_CACHE_SECONDS", 60)
    for n in range(1, 6):
        _log(client, n)
    boards.reads = 0
    client.get("/blossom")
    client.get("/blossom")
    _todays(client)
    assert boards.reads == 1
    # An admin change shows at once.
    client.post("/blossom_admin/approve_board", headers=_admin_headers(monkeypatch),
                data={"letters": LETTERS, "board": OTHER, "played_on": "2026-10-23"})
    assert _served(client) == OTHER and boards.reads == 2


def test_a_stale_board_is_kept_out_of_the_way_while_it_is_read_again(boards, monkeypatch):
    stale = {"date": "2026-10-23", "center": "e", "petals": "lction"}
    blossom._board_cache[DAY] = (0.0, stale)
    seen = []

    def read(first, last):
        seen.append(blossom._board_cache[DAY])
        return []

    monkeypatch.setattr(blossom, "rows_logged_between", read)
    assert blossom._board_for(DAY) is None
    [(expires, during)] = seen
    # Other requests meanwhile get the stale board, not a second read.
    assert during is stale and expires > time.monotonic()


def test_a_failed_read_costs_the_board_never_the_page(client, boards, monkeypatch, capsys):
    from monitoring import alerts
    alerts.reset_throttle()
    calls = []

    def broken(first, last):
        calls.append(first)
        raise RuntimeError("Table 'blossom_boards' doesn't exist")

    monkeypatch.setattr(blossom, "rows_logged_between", broken)
    resp = client.get("/blossom")
    assert resp.status_code == 200
    assert '<div class="bl-today" id="bl-today" hidden>' in resp.get_data(as_text=True)
    assert _todays(client) is None
    assert len(calls) == 1                     # not tried again for BOARD_RETRY_SECONDS
    assert "check=blossom_board_read_failed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Live on /blossom, the dark launch the flag goes back to, and /blossom_dev
# ---------------------------------------------------------------------------

def test_todays_board_is_live_on_blossom(client):
    # James's call, 2026-10-24. The other tests here set the flag; this one
    # reads it as the code ships, so going dark again is a deliberate change.
    html = client.get("/blossom").get_data(as_text=True)
    assert 'id="bl-today"' in html and ">Do I have to type today's letters?<" in html


def test_while_dark_blossom_logs_boards_and_serves_none(client, boards, monkeypatch):
    monkeypatch.setattr(blossom, "TODAYS_BOARD_LIVE", False)
    for n in range(1, 6):
        _log(client, n)
    assert len(boards.rows) == 5
    boards.reads = 0
    html = client.get("/blossom").get_data(as_text=True)
    assert 'id="bl-today"' not in html and 'id="bl-hints"' not in html
    assert boards.reads == 0                   # the public page never reads the table
    assert _todays(client) is None             # not even to a direct call


def test_blossom_dev_shows_the_board_and_hints_while_dark(client, boards, monkeypatch):
    monkeypatch.setattr(blossom, "TODAYS_BOARD_LIVE", False)
    for n in range(1, 6):
        _log(client, n)
    headers = _admin_headers(monkeypatch)
    html = client.get("/blossom_dev", headers=headers).get_data(as_text=True)
    assert '<div class="bl-today" id="bl-today">' in html and "E · L C T I O N" in html
    assert "Today&#39;s Blossom hints - Friday, October 23" in html
    assert ">Do I have to type today's letters?<" in html
    assert "/blossom_dev - only you can see this copy" in html
    assert 'action="/blossom_dev"' in html
    assert 'const POST_URL = "/blossom_dev";' in html
    assert 'const RESET_URL = "/blossom/reset?to=dev";' in html
    assert _served(client, path="/blossom_dev", headers=headers) == BOARD
    # Its own boards count like any player's.
    _log(client, 6, OTHER, path="/blossom_dev", headers=headers)
    assert (LETTERS, OTHER) in {(row[0], row[1]) for row in boards.rows.values()}


def test_with_the_switch_on_blossom_matches_blossom_dev(client, boards):
    for n in range(1, 6):
        _log(client, n)
    html = client.get("/blossom").get_data(as_text=True)
    assert '<div class="bl-today" id="bl-today">' in html
    assert ">Do I have to type today's letters?<" in html
    assert "only you can see this copy" not in html
    assert 'const POST_URL = "/blossom";' in html


def test_blossom_dev_results_stay_on_dev_with_no_ad_and_no_logging(client, boards, slots,
                                                                   monkeypatch):
    import extensions
    headers = _admin_headers(monkeypatch)
    form = {"must_have": "e", "may_have": "lction", "petal_letter": "l"}
    html = client.post("/blossom_dev", base_url=LIVE, headers=headers,
                       data=form).get_data(as_text=True)
    assert 'action="/blossom_dev"' in html
    assert 'id="jj-ad"' not in html
    client.get("/blossom_dev", headers=headers)
    assert extensions._log_queue.qsize() == 0  # no solver click, no page visit
    # The public page still logs both.
    assert 'id="jj-ad"' in client.post("/blossom", base_url=LIVE, data=form).get_data(as_text=True)
    client.get("/blossom")
    assert extensions._log_queue.qsize() == 2


def test_dev_votes_are_tagged_dev_in_the_vote_log(client, crowd_db, fresh_word_cache, monkeypatch):
    db = crowd_db(_votes(players=1))
    resp = client.post("/blossom_dev", headers=_admin_headers(monkeypatch), json={
        "action": "toggle_invalid", "word": "nastier", "center": CENTER, "petals": PETALS,
        "mode": "helper"})
    assert resp.status_code == 200
    [row] = db.log_rows("blossom")
    assert row["mode"] == "dev_helper"


def test_reset_goes_back_to_the_page_it_came_from(client):
    assert client.get("/blossom/reset?to=dev").headers["Location"].endswith("/blossom_dev")
    assert client.get("/blossom/reset").headers["Location"].endswith("/blossom")
    assert client.get("/blossom/reset?to=//evil.example").headers["Location"].endswith("/blossom")


# ---------------------------------------------------------------------------
# /blossom_admin's Today's Board tab
# ---------------------------------------------------------------------------

def test_admin_board_actions_need_the_password(client, boards):
    for action in ("approve_board", "unapprove_board", "remove_board"):
        assert client.post(f"/blossom_admin/{action}", data={}).status_code == 401


def test_admin_opens_on_the_board_tab_and_shows_each_board(client, boards, monkeypatch):
    for n in range(1, 5):
        _log(client, n)
    _log(client, 5, OTHER)
    html = client.get("/blossom_admin", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert 'id="board-tab" class="tab-content active"' in html
    assert "<h3>2026-10-23 (today, Pacific)</h3>" in html
    assert "E · CILNOT" in html                              # the puzzle
    assert "E · L C T I O N" in html and "E · L T C I O N" in html   # its two boards
    assert "needs 1 more player on one board" in html
    assert f"once {blossom.SERVE_CROWD} players enter it exactly" in html
    assert "logging only; served on /blossom_dev" not in html     # live in this fixture


def test_an_approval_from_the_admin_tab_serves_at_once_and_undoes(client, boards, monkeypatch):
    headers = _admin_headers(monkeypatch)
    _log(client, 1, OTHER)
    resp = client.post("/blossom_admin/approve_board", headers=headers, data={
        "letters": LETTERS, "board": BOARD, "played_on": "2026-10-23", "tab": "board"})
    assert resp.status_code == 302 and "success=Approved" in resp.headers["Location"]
    assert "tab=board" in resp.headers["Location"]
    assert _served(client) == BOARD
    resp = client.post("/blossom_admin/unapprove_board", headers=headers,
                       data={"letters": LETTERS, "played_on": "2026-10-23"})
    assert "success=Approval+undone" in resp.headers["Location"]
    assert _served(client) is None
    assert len(boards.rows) == 1               # the player's board stays


def test_the_approve_form_takes_the_spots_as_typed(client, boards, monkeypatch):
    form = {"center": "E", "p0": "L", "p1": "C", "p2": "T", "p3": "I", "p4": "O", "p5": "N",
            "played_on": "2026-10-23"}
    resp = client.post("/blossom_admin/approve_board", headers=_admin_headers(monkeypatch), data=form)
    assert "success=Approved" in resp.headers["Location"]
    assert _served(client) == BOARD


@pytest.mark.parametrize("form", [
    {"center": "E", "p0": "L", "p1": "C", "p2": "T", "p3": "I", "p4": "O", "played_on": "2026-10-23"},
    {"center": "E", "p0": "E", "p1": "C", "p2": "T", "p3": "I", "p4": "O", "p5": "N",
     "played_on": "2026-10-23"},
    {"letters": "abcdefg", "board": BOARD, "played_on": "2026-10-23"},
    {"letters": LETTERS, "board": BOARD, "played_on": "tomorrow"},
])
def test_the_admin_refuses_anything_but_a_board(client, boards, monkeypatch, form):
    for action in ("approve_board", "remove_board"):
        resp = client.post(f"/blossom_admin/{action}", headers=_admin_headers(monkeypatch), data=form)
        assert resp.status_code == 302 and "error=" in resp.headers["Location"]
    assert not boards.rows


def test_remove_deletes_that_boards_player_entries_only(client, boards, monkeypatch):
    for n in range(1, 6):
        _log(client, n)
    _log(client, 6, OTHER)
    boards.approve(DAY, LETTERS, BOARD)
    resp = client.post("/blossom_admin/remove_board", headers=_admin_headers(monkeypatch),
                       data={"letters": LETTERS, "board": BOARD, "played_on": "2026-10-23"})
    assert "success=Removed+5+entries" in resp.headers["Location"]
    assert {(row[1], row[2]) for row in boards.rows.values()} == {(BOARD, "admin"), (OTHER, "typed")}


def test_the_board_tab_works_when_the_word_lists_fail(client, boards, monkeypatch):
    import crowd
    for n in range(1, 6):
        _log(client, n)
    monkeypatch.setattr(crowd, "db_cursor", _ScriptedDB(fail="blossom_"))
    resp = client.get("/blossom_admin", headers=_admin_headers(monkeypatch))
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Couldn&#39;t read the word lists" in html or "Couldn't read the word lists" in html
    assert "E · L C T I O N" in html


def test_admin_says_when_the_board_table_is_missing(client, board_db, monkeypatch):
    board_db(fail="blossom_boards")
    resp = client.get("/blossom_admin", headers=_admin_headers(monkeypatch))
    assert resp.status_code == 200
    assert "Has mysql_tables/blossom_boards.sql been run?" in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# The SQL, against the scripted fake
# ---------------------------------------------------------------------------

@pytest.fixture
def board_db(monkeypatch):
    import crowd
    monkeypatch.setattr(crowd, "db_cursor", _ScriptedDB())     # as in `boards`
    monkeypatch.setattr(blossom, "rows_logged_between", REAL_ROWS_LOGGED_BETWEEN)
    monkeypatch.setattr(blossom, "get_filtered_blossom_words", lambda: set(WORDS))
    monkeypatch.setattr(blossom, "_pacific_today", lambda: DAY)
    monkeypatch.setattr(blossom, "TODAYS_BOARD_LIVE", True)

    def install(answers=None, rows=None, rowcount=1, fail=None):
        db = _ScriptedDB(answers, rows, rowcount, fail)
        monkeypatch.setattr(blossom, "db_cursor", db)
        return db

    return install


def test_a_board_is_stored_as_its_puzzle_and_its_spots(client, board_db):
    db = board_db({"SELECT COUNT(*) FROM blossom_boards": (0,)})
    _log(client, 1, board="ELCTION")
    [(sql, params)] = db.statements("INSERT INTO blossom_boards")
    assert params[0] == DAY and re.fullmatch(r"[0-9a-f]{32}", params[1])
    # The network: an HMAC too, never the address, and not the player hash.
    assert re.fullmatch(r"[0-9a-f]{32}", params[2]) and params[2] != params[1]
    assert params[3:6] == (LETTERS, BOARD, "typed")
    assert not any("198.51.100" in str(p) for p in params)
    assert "ON DUPLICATE KEY UPDATE board = new.board" in sql and "prefix_hash = new.prefix_hash" in sql
    assert db.commits == 1


def test_the_daily_cap_stops_a_flood(client, board_db):
    db = board_db({"SELECT COUNT(*) FROM blossom_boards": (blossom.BOARDS_PER_PLAYER_PER_DAY,)})
    _log(client, 1)
    assert not db.statements("INSERT INTO blossom_boards")
    [(sql, params)] = db.statements("SELECT COUNT(*) FROM blossom_boards")
    assert "AND letters <> %s" in sql and params[1:] == (DAY, LETTERS)


def test_the_reader_asks_for_every_row_of_the_days_puzzles(client, board_db):
    db = board_db()
    _todays(client)
    [(sql, params)] = db.executed
    assert "WHERE letters IN (SELECT letters FROM blossom_boards WHERE played_on BETWEEN" in sql
    assert params == (DAY, DAY)


def test_a_missing_table_costs_the_board_never_the_request(client, board_db, capsys):
    from monitoring import alerts
    alerts.reset_throttle()
    board_db(fail="blossom_boards")
    _log(client, 1)
    assert _todays(client) is None
    assert client.get("/blossom").status_code == 200
    out = capsys.readouterr().out
    assert "check=blossom_board_log_failed" in out and "check=blossom_board_read_failed" in out


def test_the_admin_actions_write_the_right_rows(client, board_db, monkeypatch):
    db = board_db(rowcount=1)
    headers = _admin_headers(monkeypatch)
    client.post("/blossom_admin/approve_board", headers=headers,
                data={"letters": LETTERS, "board": BOARD, "played_on": "2026-10-23"})
    [(sql, params)] = db.statements("INSERT INTO blossom_boards")
    assert params[:5] == (DAY, "admin", LETTERS, BOARD, "admin")
    assert "ON DUPLICATE KEY UPDATE board = new.board" in sql
    client.post("/blossom_admin/unapprove_board", headers=headers,
                data={"letters": LETTERS, "played_on": "2026-10-23"})
    client.post("/blossom_admin/remove_board", headers=headers,
                data={"letters": LETTERS, "board": BOARD, "played_on": "2026-10-23"})
    assert [params for _sql, params in db.statements("DELETE FROM blossom_boards")] == [
        (LETTERS, DAY, "admin"), (LETTERS, BOARD, DAY, "admin")]
    assert db.statements("source <> %s")         # Remove never takes an approval


# ---------------------------------------------------------------------------
# What players see
# ---------------------------------------------------------------------------

# tests/test_blossom.py's list, tests/test_ribbit_boards.py's, and this one's.
NEVER_SAID = ("crowd_remove_votes", "crowd_add_votes", "different players", "another player",
              "players flag", "players agreed", "for everyone",
              "2 players", "3 players", "two players", "three players", "serve_",
              "5 players", "five players", "approv", "more players")


def test_players_are_never_told_how_a_board_is_chosen(client, boards, monkeypatch):
    for n in range(1, 6):
        _log(client, n)
    texts = [
        client.get("/blossom").get_data(as_text=True),
        client.get("/blossom_dev", headers=_admin_headers(monkeypatch)).get_data(as_text=True),
        client.post("/blossom", data={"must_have": "e", "may_have": "lction",
                                      "petal_letter": "l"}).get_data(as_text=True),
        json.dumps(_todays(client)),
        json.dumps(_todays(client, played_on=DAY + timedelta(days=1))),
        json.dumps(_log(client, 6).get_json()),
    ]
    for text in texts:
        lowered = text.lower()
        for phrase in NEVER_SAID:
            assert phrase not in lowered, f"a player could read {phrase!r}"
