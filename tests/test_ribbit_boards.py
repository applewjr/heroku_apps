"""Ribbit's shared boards (routes/ribbit.py): early players' boards, served
to the players after them.

Nothing here reaches a real database: secret_pass points at the production
JawsDB. Most tests swap the module's table functions for an in-memory
stand-in (MemoryBoards); the SQL itself is checked against test_blossom's
scripted fake. Each simulated player is a different X-Forwarded-For, which
is what helpers.client_ip reads: an IPv4 address each, or IPv6 ones where a
test needs players on one network.
"""

import json
import re
from datetime import date, datetime, timedelta

import pytest

from test_blossom import _admin_headers, _ScriptedDB

DAY = date(2026, 10, 17)

# A board with no symmetry: 16 letters, row by row, cell = row * 4 + col.
LETTERS = "ribbitfrogslilyp"
# A snake through every cell, plus two diagonals: every tile linked.
SNAKE = [(0, 1), (1, 2), (2, 3), (3, 7), (6, 7), (5, 6), (4, 5), (4, 8), (8, 9),
         (9, 10), (10, 11), (11, 15), (14, 15), (13, 14), (12, 13)]
LINKS = sorted(SNAKE + [(0, 5), (10, 15)])


def _turned(letters, links, times=1):
    """The board as the page's Rotate leaves it: (r, c) -> (c, 3 - r)."""
    for _ in range(times):
        move = [c * 4 + (3 - r) for r, c in (divmod(i, 4) for i in range(16))]
        out = [""] * 16
        for i, ch in enumerate(letters):
            out[move[i]] = ch
        letters = "".join(out)
        links = sorted(tuple(sorted((move[a], move[b]))) for a, b in links)
    return letters, links


class MemoryBoards:
    """Stands in for ribbit_boards: store_board and the approvals write here,
    and the two readers read back, rows in routes.ribbit._COLUMNS order."""

    def __init__(self):
        self.rows = {}
        self.clock = datetime(2026, 10, 17, 6, 0)

    def store(self, day, voter, prefix, letters, links, orientation, source):
        import routes.ribbit as ribbit
        self.clock += timedelta(minutes=1)
        self.rows[(letters, voter, day)] = (letters, ribbit._links_text(links), orientation,
                                            source, voter, day, self.clock, prefix)

    def approve(self, day, letters, links, orientation):
        import routes.ribbit as ribbit
        self.store(day, ribbit.APPROVAL, None, letters, links, orientation, ribbit.APPROVAL)

    def unapprove(self, letters):
        gone = [key for key, row in self.rows.items() if row[0] == letters and self._approval(row)]
        for key in gone:
            del self.rows[key]
        return len(gone)

    def approvals(self):
        return [row for row in self.rows.values() if self._approval(row)]

    @staticmethod
    def _approval(row):
        import routes.ribbit as ribbit
        return row[ribbit.SOURCE] == ribbit.APPROVAL

    def with_letters(self, letters):
        return [row for row in self.rows.values() if row[0] == letters]

    def logged_between(self, first, last):
        seen = {row[0] for row in self.rows.values() if first <= row[5] <= last}
        return [row for row in self.rows.values() if row[0] in seen]


@pytest.fixture
def boards(monkeypatch):
    """The in-memory table, with shared boards live on /ribbit, so these
    tests exercise the serve rules; the dark launch has tests of its own."""
    import crowd
    import routes.ribbit as ribbit
    # /ribbit_admin's word-list half reads through crowd.py: an empty fake,
    # never the real tables.
    monkeypatch.setattr(crowd, "db_cursor", _ScriptedDB())
    memory = MemoryBoards()
    monkeypatch.setattr(ribbit, "store_board", memory.store)
    monkeypatch.setattr(ribbit, "store_approval", memory.approve)
    monkeypatch.setattr(ribbit, "remove_approval", memory.unapprove)
    monkeypatch.setattr(ribbit, "rows_with_letters", memory.with_letters)
    monkeypatch.setattr(ribbit, "rows_logged_between", memory.logged_between)
    monkeypatch.setattr(ribbit, "_pacific_today", lambda: DAY)
    monkeypatch.setattr(ribbit, "SHARED_BOARDS_LIVE", True)
    return memory


def _player(n):
    """A simulated player: a number is an IPv4 address of their own, a
    string an address as given (IPv6 too)."""
    return {"X-Forwarded-For": f"198.51.100.{n}" if isinstance(n, int) else n}


def _nodes(letters):
    return [{"id": i, "letter": ch} for i, ch in enumerate(letters)]


def _log(client, player, letters=LETTERS, links=LINKS, source="typed", played_on=DAY):
    resp = client.post("/ribbit", headers=_player(player), json={
        "action": "log_board", "nodes": _nodes(letters), "edges": [list(p) for p in links],
        "played_on": played_on.isoformat(), "source": source})
    assert resp.status_code == 200 and resp.get_json() == {"status": "success"}
    return resp


def _todays(client, player=99, played_on=DAY):
    return client.post("/ribbit", headers=_player(player), json={
        "action": "shared_board", "played_on": played_on.isoformat()}).get_json()["board"]


def _links_for(client, letters, player=99):
    return client.post("/ribbit", headers=_player(player), json={
        "action": "board_links", "nodes": _nodes(letters)}).get_json()["edges"]


def _as_board(letters, links):
    return {"letters": list(letters), "edges": [list(p) for p in sorted(links)]}


# ---------------------------------------------------------------------------
# A day of players, through the real route
# ---------------------------------------------------------------------------

def test_a_day_of_players(client, boards):
    # Player 1 enters today's board: one board settles nothing.
    _log(client, 1)
    assert _todays(client) is None

    # Player 2 enters it with a slip - one diagonal missed, one link too many.
    slipped = [p for p in LINKS if p != (0, 5)] + [(5, 10)]
    _log(client, 2, links=slipped)
    assert _todays(client) is None          # two boards that differ: wait for a third

    # Player 3 enters it turned a quarter (the page's Rotate): the same board.
    _log(client, 3, *_turned(LETTERS, LINKS))
    # Served, links by majority: the diagonal 2 of 3 drew is in, the extra
    # link only player 2 drew is out. Shown the way most players entered it.
    assert _todays(client) == _as_board(LETTERS, LINKS)

    # Player 4 opens an empty board and is offered exactly that.
    assert _todays(client, player=4) == _as_board(LETTERS, LINKS)

    # Player 5 plays an archive puzzle today. Two players entered that board
    # on its own day, three days ago...
    import routes.ribbit as ribbit
    old = "zebracowsdognewt"
    old_letters, old_links, t = ribbit.canonical(old, tuple(LINKS))
    for voter in ("then-a", "then-b"):
        boards.store(DAY - timedelta(days=3), voter, None, old_letters, old_links, t, "typed")
    # ...so it never becomes today's board, even when more players replay it
    # today than entered today's.
    for p in (5, 6, 7, 8):
        _log(client, p, letters=old)
    assert _todays(client) == _as_board(LETTERS, LINKS)
    # ...but typing that older board's letters fills in its links, in the
    # player's own view.
    view_letters, view_links = _turned(old, LINKS, times=2)
    assert _links_for(client, view_letters) == [list(p) for p in view_links]


def test_two_identical_boards_serve_at_once(client, boards):
    _log(client, 1)
    _log(client, 2)
    assert _todays(client) == _as_board(LETTERS, LINKS)


def test_a_typo_is_its_own_board_and_blocks_nothing(client, boards):
    _log(client, 1)
    _log(client, 2)
    _log(client, 3, letters="ribbitfrogslilyq")     # last letter mistyped
    assert _todays(client) == _as_board(LETTERS, LINKS)
    assert _links_for(client, "ribbitfrogslilyq") is None


def test_a_tie_between_two_boards_serves_neither(client, boards):
    _log(client, 1)
    _log(client, 2)
    other = "abcdefghijklmnop"
    _log(client, 3, letters=other)
    _log(client, 4, letters=other)
    assert _todays(client) is None


def test_a_half_drawn_board_does_not_count(client, boards):
    _log(client, 1)
    _log(client, 2, links=[p for p in LINKS if 12 not in p])    # tile 12 still unlinked
    assert _todays(client) is None
    _log(client, 3)
    assert _todays(client) == _as_board(LETTERS, LINKS)


def test_loading_then_fixing_a_board_counts(client, boards):
    # The only way a loaded board is logged is after the player changed it.
    _log(client, 1)
    _log(client, 2, links=LINKS + [(5, 10)])
    _log(client, 3, source="edited")
    assert _todays(client) == _as_board(LETTERS, LINKS)


def test_one_player_is_one_board_whatever_they_resend(client, boards):
    for _ in range(3):
        _log(client, 1)
    assert _todays(client) is None
    assert len(boards.rows) == 1


def test_a_player_whose_date_is_far_off_is_not_stored(client, boards):
    _log(client, 1, played_on=DAY + timedelta(days=3))
    _log(client, 2, played_on=DAY - timedelta(days=2))
    assert boards.rows == {}
    assert _todays(client, played_on=DAY + timedelta(days=3)) is None


def test_a_board_with_no_links_is_not_stored(client, boards):
    _log(client, 1, links=[])
    assert boards.rows == {}


@pytest.mark.parametrize("patch", [
    {"nodes": _nodes(LETTERS)[:15]},                                  # a cell missing
    {"nodes": _nodes(LETTERS)[:15] + [{"id": 16, "letter": "p"}]},    # off the grid
    {"edges": [[0, 2]]},                                              # not neighbours
    {"source": "shared"},                                             # never logged
    {"source": "admin"},                                              # an approval's source
    {"source": None},
    {"played_on": "10/17/2026"},
    {"played_on": "2026-02-30"},
    {"nodes": _nodes("ribbitfrogslily1")},
], ids=["15cells", "offgrid", "farlink", "shared", "admin", "nosource", "usdate", "nodate",
        "digit"])
def test_malformed_boards_are_a_400_and_store_nothing(client, boards, patch):
    body = {"action": "log_board", "nodes": _nodes(LETTERS), "edges": [list(p) for p in LINKS],
            "played_on": DAY.isoformat(), "source": "typed"}
    body.update(patch)
    assert client.post("/ribbit", headers=_player(1), json=body).status_code == 400
    assert boards.rows == {}


def test_an_unknown_action_is_a_400(client, boards):
    assert client.post("/ribbit", json={"action": "drop_table"}).status_code == 400


# ---------------------------------------------------------------------------
# The canonical form and the serve rules, directly
# ---------------------------------------------------------------------------

def test_all_eight_turns_and_flips_are_one_board():
    import routes.ribbit as ribbit
    assert len(set(ribbit.TRANSFORMS)) == 8
    forms = set()
    for perm in ribbit.TRANSFORMS:
        letters, links = ribbit._apply(perm, LETTERS, LINKS)
        canon_letters, canon_links, t = ribbit.canonical(letters, links)
        forms.add((canon_letters, canon_links))
        # ...and the canonical board comes back exactly as this view saw it.
        assert ribbit.in_view(canon_letters, canon_links, t) == _as_board(letters, links)
    assert len(forms) == 1


def test_every_link_the_grid_can_draw_fits_the_column():
    import routes.ribbit as ribbit
    every = sorted({tuple(sorted((a, b))) for a in range(16) for b in range(16)
                    if ribbit._adjacent(a, b)})
    assert len(every) == 42
    assert len(ribbit._links_text(every)) <= 255          # links VARCHAR(255)
    assert ribbit._parse_links(ribbit._links_text(every)) == tuple(every)


def _row(voter, links=LINKS, orientation=0, played_on=DAY, minute=0, letters=LETTERS,
         prefix=None, source="typed"):
    """A ribbit_boards row. No prefix is a board from before prefix_hash."""
    import routes.ribbit as ribbit
    return (letters, ribbit._links_text(links), orientation, source, voter, played_on,
            datetime(2026, 10, 17, 8, minute), prefix)


def test_a_tied_link_is_left_out_until_the_next_board():
    import routes.ribbit as ribbit
    extra = LINKS + [(5, 10)]
    rows = [_row("a"), _row("b"), _row("c", extra), _row("d", extra)]
    result = ribbit.consensus(rows)
    assert result["players"] == 4 and result["tied"] == 1
    assert result["links"] == tuple(LINKS)
    rows.append(_row("e", extra))
    assert ribbit.consensus(rows)["links"] == tuple(sorted(extra))


def test_each_players_latest_board_is_the_one_that_counts():
    import routes.ribbit as ribbit
    fixed = [p for p in LINKS if p != (10, 15)]
    rows = [_row("a", LINKS, minute=1), _row("a", fixed, minute=5, played_on=DAY - timedelta(days=1)),
            _row("b", fixed)]
    result = ribbit.consensus(rows)
    assert result["players"] == 2 and result["links"] == tuple(fixed)


def test_the_board_is_shown_the_way_most_players_entered_it():
    import routes.ribbit as ribbit
    rows = [_row("a", orientation=3), _row("b", orientation=3), _row("c", orientation=1)]
    board, _groups = ribbit.todays_board(rows, DAY)
    assert board == ribbit.in_view(LETTERS, tuple(LINKS), 3)


def test_why_a_board_is_not_served_yet():
    import routes.ribbit as ribbit
    differ = LINKS + [(5, 10)]
    assert ribbit.consensus([_row("a")])["why"] == "needs 1 more matching board"
    assert ribbit.consensus([_row("a"), _row("b", differ)])["why"] == "links differ, needs a 3rd board"
    half = [p for p in LINKS if 12 not in p]
    assert ribbit.consensus([_row("a", half)])["why"] == "no finished board yet"
    # Three finished boards that each link the corner tile 12 differently:
    # no link to it has a majority, so the result would strand it.
    base = [p for p in SNAKE if p != (12, 13)]
    rows = [_row("a", base + [(12, 13)]), _row("b", base + [(8, 12)]), _row("c", base + [(9, 12)])]
    result = ribbit.consensus(rows)
    assert result["players"] == 3 and result["links"] is None
    assert result["why"] == "links disagree, needs another board"


# ---------------------------------------------------------------------------
# Players on one network count once
# ---------------------------------------------------------------------------

def test_many_64s_inside_one_48_count_as_one_player(client, boards):
    # Each /64 is a player hash of its own, and one /48 holds 65,536 of them.
    for subnet in range(1, 6):
        _log(client, f"2001:db8:a:{subnet}::1")
    assert len(boards.rows) == 5            # every board is logged...
    assert _todays(client) is None          # ...but the network is one player
    _log(client, "2001:db8:b:1::1")         # a second network agrees
    assert _todays(client) == _as_board(LETTERS, LINKS)


def test_distinct_48s_count_separately(client, boards):
    _log(client, "2001:db8:a:1::1")
    _log(client, "2001:db8:b:1::1")
    assert _todays(client) == _as_board(LETTERS, LINKS)


def test_one_network_cannot_outvote_the_real_board(client, boards):
    _log(client, 1)
    _log(client, 2)
    for subnet in range(1, 6):
        _log(client, f"2001:db8:a:{subnet}::1", letters="abcdefghijklmnop")
    assert _todays(client) == _as_board(LETTERS, LINKS)
    # ...nor take over the links of today's letters.
    for subnet in range(6, 11):
        _log(client, f"2001:db8:a:{subnet}::1", links=LINKS + [(5, 10)])
    assert _links_for(client, LETTERS) == [list(p) for p in LINKS]


def test_boards_from_before_the_network_column_count_by_player():
    import routes.ribbit as ribbit
    # Two players logged before prefix_hash existed: two players, as ever.
    result = ribbit.consensus([_row("a"), _row("b")])
    assert result["players"] == 2 and result["links"] == tuple(LINKS)
    # One player's board from before and one since: still one player.
    rows = [_row("a", minute=1),
            _row("a", minute=5, prefix="net-1", played_on=DAY + timedelta(days=1))]
    assert ribbit.consensus(rows)["players"] == 1
    # Old and new side by side: b and c share a network, a is its own.
    rows = [_row("a"), _row("b", prefix="net-1", minute=1), _row("c", prefix="net-1", minute=2)]
    assert ribbit.consensus(rows)["players"] == 2


def _keys(flask_app, ip):
    """(prefix_hash, player_hash) for a visitor at `ip`."""
    import crowd
    with flask_app.test_request_context("/", environ_base={"REMOTE_ADDR": ip}):
        return crowd.prefix_hash("ribbit"), crowd.player_hash("ribbit")


def test_prefix_hash_is_the_48_or_the_whole_ipv4_address(flask_app):
    network, player = _keys(flask_app, "2001:db8:a:1::5")
    assert _keys(flask_app, "2001:db8:a:ffff:1:2:3:4")[0] == network    # same /48
    assert _keys(flask_app, "2001:db8:b:1::5")[0] != network            # another /48
    assert _keys(flask_app, "::ffff:198.51.100.7")[0] == _keys(flask_app, "198.51.100.7")[0]
    assert _keys(flask_app, "198.51.100.7")[0] != _keys(flask_app, "198.51.100.8")[0]
    assert re.fullmatch(r"[0-9a-f]{32}", network) and network != player
    assert "198.51.100.7" not in _keys(flask_app, "198.51.100.7")[0]


def test_player_hash_is_unchanged_by_the_network_key(flask_app):
    # Every vote and board already stored is keyed on this exact value.
    import hashlib
    import hmac
    key = str(flask_app.secret_key).encode()
    expected = hmac.new(key, b"ribbit-vote|2001:db8:a:1::", hashlib.sha256).hexdigest()[:32]
    assert _keys(flask_app, "2001:db8:a:1::5")[1] == expected


# ---------------------------------------------------------------------------
# The SQL, against the scripted fake
# ---------------------------------------------------------------------------

@pytest.fixture
def board_db(monkeypatch):
    import crowd
    import routes.ribbit as ribbit
    monkeypatch.setattr(crowd, "db_cursor", _ScriptedDB())     # as in `boards`
    monkeypatch.setattr(ribbit, "_pacific_today", lambda: DAY)
    monkeypatch.setattr(ribbit, "SHARED_BOARDS_LIVE", True)

    def install(answers=None, rows=None, rowcount=1, fail=None):
        db = _ScriptedDB(answers, rows, rowcount, fail)
        monkeypatch.setattr(ribbit, "db_cursor", db)
        return db

    return install


def test_a_board_is_stored_canonical_with_its_view(client, board_db):
    import routes.ribbit as ribbit
    db = board_db({"SELECT COUNT(*) FROM ribbit_boards": (0,)})
    letters, links = _turned(LETTERS, LINKS)
    _log(client, 1, letters, links)
    [(sql, params)] = db.statements("INSERT INTO ribbit_boards")
    canon_letters, canon_links, t = ribbit.canonical(letters, tuple(links))
    assert params[0] == DAY and re.fullmatch(r"[0-9a-f]{32}", params[1])
    assert params[2:6] == (canon_letters, ribbit._links_text(canon_links), t, "typed")
    # The network: an HMAC too, never the address, and not the player hash.
    assert re.fullmatch(r"[0-9a-f]{32}", params[8]) and params[8] != params[1]
    assert not any("198.51.100" in str(p) for p in params)
    assert "ON DUPLICATE KEY UPDATE" in sql and "prefix_hash = new.prefix_hash" in sql
    assert db.commits == 1


def test_the_daily_cap_stops_a_flood(client, board_db):
    import routes.ribbit as ribbit
    db = board_db({"SELECT COUNT(*) FROM ribbit_boards": (ribbit.BOARDS_PER_PLAYER_PER_DAY,)})
    _log(client, 1)
    assert not db.statements("INSERT INTO ribbit_boards")


def test_a_missing_table_costs_the_board_never_the_request(client, board_db, capsys):
    from monitoring import alerts
    alerts.reset_throttle()
    board_db(fail="ribbit_boards")
    _log(client, 1)
    assert _todays(client) is None
    assert _links_for(client, LETTERS) is None
    out = capsys.readouterr().out
    assert "check=ribbit_board_log_failed" in out and "check=ribbit_board_read_failed" in out


def test_the_readers_ask_for_the_right_rows(client, board_db):
    import routes.ribbit as ribbit
    db = board_db()
    _todays(client)
    _links_for(client, LETTERS)
    (today_sql, today_params), (letters_sql, letters_params) = db.executed
    assert "WHERE letters IN (SELECT letters FROM ribbit_boards WHERE played_on BETWEEN" in today_sql
    assert today_params == (DAY, DAY)
    assert letters_sql.endswith("FROM ribbit_boards WHERE letters = %s")
    assert letters_params == (ribbit.canonical(LETTERS)[0],)


# ---------------------------------------------------------------------------
# /ribbit_admin
# ---------------------------------------------------------------------------

def test_admin_needs_the_password(client, boards):
    assert client.get("/ribbit_admin").status_code == 401
    for action in ("remove_board", "approve_board", "unapprove_board"):
        assert client.post(f"/ribbit_admin/{action}", data={}).status_code == 401


def test_admin_shows_each_board_and_why(client, boards, monkeypatch):
    _log(client, 1)
    _log(client, 2)
    _log(client, 3, letters="ribbitfrogslilyq")
    html = client.get("/ribbit_admin", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert "2026-10-17" in html
    assert "RIBB<br>ITFR<br>OGSL<br>ILYP<br>" in html        # shown as entered
    assert '<td class="status-served">Served</td>' in html
    assert "needs 1 more matching board" in html
    assert html.count('action="/ribbit_admin/remove_board"') == 2


def test_admin_says_when_the_table_is_missing(client, board_db, monkeypatch):
    board_db(fail="ribbit_boards")
    resp = client.get("/ribbit_admin", headers=_admin_headers(monkeypatch))
    assert resp.status_code == 200
    assert ("Have mysql_tables/ribbit_boards.sql and ribbit_boards_prefix.sql been run?"
            in resp.get_data(as_text=True))


def test_admin_remove_deletes_that_days_entries(client, board_db, monkeypatch):
    import routes.ribbit as ribbit
    db = board_db(rowcount=2)
    letters = ribbit.canonical(LETTERS, tuple(LINKS))[0]
    resp = client.post("/ribbit_admin/remove_board", headers=_admin_headers(monkeypatch),
                       data={"letters": letters, "played_on": "2026-10-17"})
    assert resp.status_code == 302 and "success=Removed+2+entries" in resp.headers["Location"]
    assert db.statements("DELETE FROM ribbit_boards") == [
        ("DELETE FROM ribbit_boards WHERE letters = %s AND played_on = %s", (letters, DAY))]


@pytest.mark.parametrize("form", [
    {"letters": "short", "played_on": "2026-10-17"},
    {"letters": "RIBBITFROGSLILYP", "played_on": "2026-10-17"},
    {"letters": "ribbitfrogslilyp", "played_on": "yesterday"},
])
def test_admin_remove_refuses_anything_but_a_board(client, board_db, monkeypatch, form):
    db = board_db()
    resp = client.post("/ribbit_admin/remove_board", headers=_admin_headers(monkeypatch), data=form)
    assert resp.status_code == 302 and "error=" in resp.headers["Location"]
    assert not db.executed


# ---------------------------------------------------------------------------
# Approve: James serves a board he entered himself
# ---------------------------------------------------------------------------

def _latest(boards, letters=LETTERS):
    """The time of the latest player board for these letters, as the
    Shared boards tab sends it back with Add."""
    import routes.ribbit as ribbit
    canon = ribbit.canonical(letters)[0]
    return max(row[ribbit.UPDATED] for row in boards.rows.values()
               if row[0] == canon and not boards._approval(row)).strftime("%Y-%m-%d %H:%M:%S")


def _approve(client, monkeypatch, boards, letters=LETTERS, latest=None):
    import routes.ribbit as ribbit
    return client.post("/ribbit_admin/approve_board", headers=_admin_headers(monkeypatch), data={
        "letters": ribbit.canonical(letters)[0], "played_on": DAY.isoformat(),
        "latest": latest if latest is not None else _latest(boards, letters)})


def test_approving_your_board_serves_it_at_once(client, boards, monkeypatch):
    _log(client, 1)                         # one board settles nothing...
    assert _todays(client) is None
    resp = _approve(client, monkeypatch, boards)
    assert resp.status_code == 302 and "success=Approved" in resp.headers["Location"]
    # ...until it's approved: then it's today's board, and typing its
    # letters fills in its links.
    assert _todays(client) == _as_board(LETTERS, LINKS)
    assert _links_for(client, LETTERS) == [list(p) for p in LINKS]


def test_an_approved_board_beats_more_players(client, boards, monkeypatch):
    for p in (1, 2, 3):
        _log(client, p, letters="abcdefghijklmnop")
    _log(client, 4)
    _approve(client, monkeypatch, boards)
    assert _todays(client) == _as_board(LETTERS, LINKS)


def test_an_approval_copies_the_latest_board_and_is_never_a_player(client, boards, monkeypatch):
    import routes.ribbit as ribbit
    _log(client, 1, links=LINKS + [(5, 10)])        # earlier, with a slip
    _log(client, 2)                                  # the latest: yours
    _approve(client, monkeypatch, boards)
    canon_letters, canon_links, _t = ribbit.canonical(LETTERS, tuple(LINKS))
    result = ribbit.consensus(boards.with_letters(canon_letters))
    assert result["approved"] and result["players"] == 2 and result["half_drawn"] == 0
    assert result["links"] == canon_links


def test_approve_refuses_once_a_newer_board_came_in(client, boards, monkeypatch):
    _log(client, 1)
    seen = _latest(boards)                           # what the page showed
    _log(client, 2, links=LINKS + [(5, 10)])         # someone else's, since
    resp = _approve(client, monkeypatch, boards, latest=seen)
    assert "error=A+newer+board" in resp.headers["Location"]
    assert boards.approvals() == []


def test_approve_refuses_a_half_drawn_board(client, boards, monkeypatch):
    _log(client, 1, links=[p for p in LINKS if 12 not in p])
    resp = _approve(client, monkeypatch, boards)
    assert "error=" in resp.headers["Location"] and boards.approvals() == []


@pytest.mark.parametrize("form", [
    {"letters": "short", "played_on": "2026-10-17", "latest": "x"},
    {"letters": "RIBBITFROGSLILYP", "played_on": "2026-10-17", "latest": "x"},
    {"letters": "ribbitfrogslilyp", "played_on": "2026-02-30", "latest": "x"},
])
def test_approve_refuses_anything_but_a_board(client, boards, monkeypatch, form):
    resp = client.post("/ribbit_admin/approve_board", headers=_admin_headers(monkeypatch), data=form)
    assert resp.status_code == 302 and "error=Not+a+board" in resp.headers["Location"]


def test_undo_hands_the_board_back_to_the_players(client, boards, monkeypatch):
    import routes.ribbit as ribbit
    _log(client, 1)
    _approve(client, monkeypatch, boards)
    resp = client.post("/ribbit_admin/unapprove_board", headers=_admin_headers(monkeypatch),
                       data={"letters": ribbit.canonical(LETTERS)[0]})
    assert resp.status_code == 302 and "success=Approval+undone" in resp.headers["Location"]
    assert _todays(client) is None                   # one player's board again
    assert len(boards.rows) == 1                     # and that board is still there
    again = client.post("/ribbit_admin/unapprove_board", headers=_admin_headers(monkeypatch),
                        data={"letters": ribbit.canonical(LETTERS)[0]})
    assert "error=" in again.headers["Location"]


def test_admin_approve_column(client, boards, monkeypatch):
    _log(client, 1)
    _log(client, 2, letters="abcdefghijklmnop", links=[p for p in LINKS if 12 not in p])
    headers = _admin_headers(monkeypatch)
    html = client.get("/ribbit_admin", headers=headers).get_data(as_text=True)
    assert "<th>Approve</th>" in html
    assert html.count('action="/ribbit_admin/approve_board"') == 1
    assert f'name="latest" value="{_latest(boards)}"' in html
    assert "latest board half-drawn" in html
    _approve(client, monkeypatch, boards)
    html = client.get("/ribbit_admin", headers=headers).get_data(as_text=True)
    assert '<span class="status-served">Approved</span>' in html
    assert 'action="/ribbit_admin/unapprove_board"' in html
    assert 'action="/ribbit_admin/approve_board"' not in html


def test_the_approval_sql(client, board_db, monkeypatch):
    import routes.ribbit as ribbit
    canon_letters, canon_links, t = ribbit.canonical(LETTERS, tuple(LINKS))
    newest = (canon_letters, ribbit._links_text(canon_links), t, "typed", "f" * 32, DAY,
              datetime(2026, 10, 17, 7, 30), "e" * 32)
    db = board_db(rows={"FROM ribbit_boards WHERE letters = %s": [newest]})
    resp = client.post("/ribbit_admin/approve_board", headers=_admin_headers(monkeypatch), data={
        "letters": canon_letters, "played_on": "2026-10-17", "latest": "2026-10-17 07:30:00"})
    assert "success=Approved" in resp.headers["Location"]
    [(sql, params)] = db.statements("INSERT INTO ribbit_boards")
    assert params[:6] == (DAY, "admin", canon_letters, ribbit._links_text(canon_links), t, "admin")
    assert "ON DUPLICATE KEY UPDATE" in sql and db.commits == 1
    client.post("/ribbit_admin/unapprove_board", headers=_admin_headers(monkeypatch),
                data={"letters": canon_letters})
    assert db.statements("DELETE FROM ribbit_boards") == [
        ("DELETE FROM ribbit_boards WHERE letters = %s AND source = %s", (canon_letters, "admin"))]


# ---------------------------------------------------------------------------
# Live on /ribbit, and the dark launch the flag goes back to: /ribbit logs
# boards, only /ribbit_dev serves them
# ---------------------------------------------------------------------------

def test_shared_boards_are_live_on_ribbit(client):
    # James's call, 2026-10-18. The other tests here set the flag; this one
    # reads it as the code ships, so turning it off is a deliberate change.
    html = client.get("/ribbit").get_data(as_text=True)
    assert 'id="rb-shared"' in html and "const SHARED_BOARDS = true;" in html

def test_while_dark_ribbit_logs_but_serves_nothing(client, boards, monkeypatch):
    import routes.ribbit as ribbit
    monkeypatch.setattr(ribbit, "SHARED_BOARDS_LIVE", False)
    _log(client, 1)
    _log(client, 2)
    assert len(boards.rows) == 2                     # logged...
    assert _todays(client) is None                   # ...but not served, even when asked directly
    assert _links_for(client, LETTERS) is None
    # /ribbit_dev serves it, behind the password.
    headers = {**_player(9), **_admin_headers(monkeypatch)}
    board = client.post("/ribbit_dev", headers=headers, json={
        "action": "shared_board", "played_on": DAY.isoformat()}).get_json()["board"]
    assert board == _as_board(LETTERS, LINKS)
    edges = client.post("/ribbit_dev", headers=headers, json={
        "action": "board_links", "nodes": _nodes(LETTERS)}).get_json()["edges"]
    assert edges == [list(p) for p in LINKS]


def test_while_dark_the_public_page_shows_none_of_it(client, monkeypatch):
    import routes.ribbit as ribbit
    monkeypatch.setattr(ribbit, "SHARED_BOARDS_LIVE", False)
    html = client.get("/ribbit").get_data(as_text=True)
    for hidden in ('id="rb-shared"', "Load today's board", "Do I have to type the board in?",
                   "/ribbit_dev", 'id="phase-clear"'):
        assert hidden not in html, f"/ribbit shows {hidden!r} before it's live"
    # It still logs: the page sends finished boards, to its own route.
    assert "action: 'log_board'" in html
    assert "const SHARED_BOARDS = false;" in html and 'const POST_URL = "/ribbit";' in html


def test_ribbit_dev_has_the_shared_board_on(client, monkeypatch):
    html = client.get("/ribbit_dev", headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    for hook in ('id="rb-shared"', "Load today's board", "Do I have to type the board in?",
                 "const SHARED_BOARDS = true;", 'const POST_URL = "/ribbit_dev";',
                 "only you can see this copy"):
        assert hook in html, f"/ribbit_dev lost {hook}"


def test_going_live_is_one_flag(client, monkeypatch):
    import routes.ribbit as ribbit
    monkeypatch.setattr(ribbit, "SHARED_BOARDS_LIVE", True)
    html = client.get("/ribbit").get_data(as_text=True)
    assert 'id="rb-shared"' in html and "Do I have to type the board in?" in html
    assert "const SHARED_BOARDS = true;" in html and "only you can see this copy" not in html


# ---------------------------------------------------------------------------
# What players see
# ---------------------------------------------------------------------------

def test_ribbit_page_ships_the_shared_board_hooks(client, monkeypatch):
    import routes.ribbit as ribbit
    monkeypatch.setattr(ribbit, "SHARED_BOARDS_LIVE", True)
    html = client.get("/ribbit").get_data(as_text=True)
    for hook in ('id="rb-shared"', 'id="rb-shared-load"', "Load today's board",
                 "Other players already entered today's board for you.",
                 "action: 'shared_board'", "action: 'board_links'", "action: 'log_board'"):
        assert hook in html, f"ribbit.html lost {hook}"
    # Right after loading, Clear sits next to Solve (hidden until then), and
    # the hint points at it.
    assert ('id="phase-solve">▶ Solve</button>\n    <button type="button" '
            'class="rb-phase-btn rb-phase-clear" id="phase-clear" hidden>🔄 Clear board</button>') in html
    assert "then hit ▶ Solve, or 🔄 Clear board if it doesn't." in html


def test_the_faq_explains_the_shared_board_without_numbers(client, monkeypatch):
    import routes.ribbit as ribbit
    monkeypatch.setattr(ribbit, "SHARED_BOARDS_LIVE", True)
    html = client.get("/ribbit").get_data(as_text=True)
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    faq = next(b for b in map(json.loads, blocks) if b.get("@type") == "FAQPage")
    answers = {q["name"]: q["acceptedAnswer"]["text"] for q in faq["mainEntity"]}
    assert "Load today's board" in answers["Do I have to type the board in?"]
    assert "Do I have to type the board in?" in html.split("Frequently Asked Questions")[1]
    lowered = html.lower()
    for hint in ("2 players", "3 players", "two players", "three players", "serve_"):
        assert hint not in lowered, f"page hints at how many boards it takes: {hint!r}"
