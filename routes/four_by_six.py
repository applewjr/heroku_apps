"""4x6: the /4x6 page and its solves, today's boards, and /4x6_admin.

The solver (functions/four_by_six.py) is a search, exact and fast (about 2
ms a board), with no data to load. Nothing is fetched at runtime: the solver
never contacts the game.

A new 4x6 comes out at each player's own midnight: the game's page builds
today's date from the device's clock and turns over at local midnight (read
from its page code, 2026-11-23). So, as on /4x3, every board is logged and
served by the player's own date (`played_on`), checked to be within a day of
Pacific's (helpers.parse_played_on). There is no game day on the server.

Today's boards. The first players each day enter the 24 tiles for the
players after them, as on /4x3 (whose rules these copy). Every player gets
the same board on a date, shelf for shelf, so a board is its 24 tiles in
order. A board that can't be finished is a mistyped one (the game's always
can), so it's never logged, offered or approved. Nor is a board from
partway through a game (tiles on the Stage or in Extra, or a shelf short):
the page solves it, but only a starting board can be a day's. There are no
answers to keep: the solver works them out.

Players are never told how many of them it takes (see crowd.py on why).
"""

import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import timedelta

from flask import Blueprint, abort, jsonify, redirect, render_template, request

import crowd
from extensions import (INTERACTIVE_LIMITS, auth, db_cursor, from_another_site, limiter, log_page_visit,
                        log_solve, pst_now_str)
from functions import four_by_six as fbs
# _pacific_today is a name of this module's own so tests can set the day.
from helpers import ISO_DATE as _DATE
from helpers import ValidationError, json_object, make_schema_data, parse_iso_day, parse_played_on
from helpers import pacific_today as _pacific_today
from monitoring import alerts

bp = Blueprint('four_by_six', __name__)

PAGE = 'four_by_six.html'
PATH = '/4x6'

# The tiles as the page draws them: (code, name, symbol), in the palette's
# order. The symbols are plain text ones that say which of the game's tiles
# each is; U+FE0E keeps a phone from drawing them as emoji.
TILES = tuple((code, name, symbol + '\ufe0e') for code, name, symbol in (
    ('K', 'Pink', '♥'), ('O', 'Orange', '♛'), ('P', 'Purple', '♦'),
    ('Y', 'Yellow', '★'), ('G', 'Green', '♣'), ('B', 'Blue', '♠')))
TILE_NAMES = {code: name for code, name, _symbol in TILES}
TILE_SYMBOLS = {code: symbol for code, _name, symbol in TILES}


def page_context():
    """What the page template needs besides the SEO blob. Rendering the page
    never touches the database."""
    return {'tiles': TILES, 'fbs_names': TILE_NAMES, 'fbs_symbols': TILE_SYMBOLS, 'shelves': fbs.SHELVES,
            'width': fbs.WIDTH, 'stage': fbs.STAGE, 'extra': fbs.EXTRA, 'spent': fbs.SPENT,
            'max_moves': fbs.MAX_MOVES, 'par_over': fbs.PAR_OVER_FEWEST,
            'scoring': {'solved': fbs.SOLVED, 'per_day': fbs.SOLVED_PER_DAY, 'no_tools': fbs.NO_TOOLS[0],
                        'no_undos': fbs.NO_UNDOS, 'under_par': fbs.UNDER_PAR, 'at_par': fbs.AT_PAR,
                        'fewest': fbs.FEWEST, 'streak_max': fbs.STREAK_MAX}}


##### the page's requests #####

def _board_from(data):
    """The position from a POST body (a starting board, or one from partway
    through a game), or ValidationError."""
    try:
        return fbs.parse_board(data.get('board'))
    except fbs.BoardError as e:
        raise ValidationError(str(e))


def _moves_from(data):
    """[(from, to)] from a body's moves. ValidationError if not."""
    raw = data.get('moves')
    if raw is None:
        return []
    if (not isinstance(raw, list) or len(raw) > fbs.MAX_MOVES
            or not all(isinstance(m, list) and len(m) == 2 for m in raw)):
        raise ValidationError(f'moves must be a list of at most {fbs.MAX_MOVES} pairs')
    return [(src, dst) for src, dst in raw]


def solve_request(data):
    """The JSON the page renders: functions/four_by_six.py plan() for the
    board (a starting one, or one from partway through a game) and the moves
    the player has made on it, with the rainbow route if `rainbow` is true."""
    position = _board_from(data)
    try:
        return jsonify(fbs.plan(position, _moves_from(data), with_rainbow=data.get('rainbow') is True))
    except fbs.BoardError as e:
        raise ValidationError(str(e))


##### boards: shape and storage #####

# When players' boards are served: this many players (one per network)
# entering the same board on the same date. Players are never told.
SERVE_NETWORKS = 2
# A date counts toward a board's first_seen only once this many networks
# logged it then, so one device with a wrong clock can't date a board.
PAST_MIN_NETWORKS = 2
# The most boards one date offers: James's approval first, then the players'.
TODAYS_BOARDS_MAX = 3
# Boards one player can log on one date: a few typo fixes and the game's
# random boards, with room to spare.
BOARDS_PER_PLAYER_PER_DAY = 10
# 'typed' = entered by the player; 'edited' = loaded from today's boards,
# then changed. A board loaded and left as it is is never logged.
BOARD_SOURCES = ('typed', 'edited')
# Live on /4x6 from launch. False keeps logging boards but offers none
# (except on /4x6_admin/board).
FOUR_BY_SIX_BOARDS_LIVE = True
# James's approval: a row of its own, with this as its source and its voter
# hash (a real one is 32 hex characters). One a date. Never a player.
APPROVAL = 'admin'
# How long a date's boards are reused before they're read again, and how long
# a failed read waits. One gunicorn worker, so the memo is the only copy.
BOARDS_CACHE_SECONDS = 60
READ_RETRY_SECONDS = 300

# The columns every read of four_by_six_boards returns, in this order.
_COLUMNS = 'board, source, voter_hash, played_on, updated_at, prefix_hash'
(BOARD, SOURCE, VOTER, PLAYED_ON, UPDATED, PREFIX) = range(6)


def _fetch(sql, params):
    with db_cursor() as (conn, cursor):
        cursor.execute(sql, params)
        return cursor.fetchall()


def rows_logged_between(first, last):
    """Every row, any date, for each board logged between these days."""
    return _fetch(
        f"SELECT {_COLUMNS} FROM four_by_six_boards WHERE board IN "
        "(SELECT board FROM four_by_six_boards WHERE played_on BETWEEN %s AND %s)",
        (first, last))


# How long a write waits for another of the same voter's to finish.
LOCK_SECONDS = 3


@contextmanager
def _one_at_a_time(cursor, voter, day):
    """Hold a database lock on one voter's rows for one date, so two of their
    requests (or James's two approvals) can't both count the same rows, or
    both find none, and then both insert. A lock by name, since there may be
    no row yet to lock, and it holds whatever the isolation level. Raises if
    another request keeps it LOCK_SECONDS. Closing the connection, or its
    reset on going back to the pool, lets go of it too."""
    name = f'four_by_six:{voter}:{day}'
    cursor.execute("SELECT GET_LOCK(%s, %s)", (name, LOCK_SECONDS))
    got = cursor.fetchone()
    if not got or got[0] != 1:
        raise RuntimeError('another write for the same voter and date is still running')
    try:
        yield
    finally:
        try:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (name,))
            cursor.fetchone()
        except Exception:
            pass


def store_board(day, voter, prefix, board, source):
    """Log one player's board; the same board again that date only updates
    their row. Inline, not on the write-behind queue: a missing table there
    would drop the rest of its batch, page visits included. Best-effort."""
    now = pst_now_str()
    try:
        with db_cursor() as (conn, cursor), _one_at_a_time(cursor, voter, day):
            cursor.execute(
                "SELECT COUNT(*) FROM four_by_six_boards "
                "WHERE voter_hash = %s AND played_on = %s AND board <> %s",
                (voter, day, board))
            if cursor.fetchone()[0] >= BOARDS_PER_PLAYER_PER_DAY:
                return
            cursor.execute(
                """INSERT INTO four_by_six_boards (played_on, voter_hash, prefix_hash, board, source,
                       created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE source = new.source, prefix_hash = new.prefix_hash,
                       updated_at = new.updated_at""",
                (day, voter, prefix, board, source, now, now))
            conn.commit()
    except Exception as e:
        alerts.alert_throttled('four_by_six_board_log_failed', exc=type(e).__name__, msg=e)


def store_approval(day, board):
    """Approve this board for players whose date is `day`. One board a date:
    this takes the place of James's approval of any other board that date.
    Both writes happen or neither. Returns how many other boards' approvals
    it replaced. Raises: the admin page says whether it worked."""
    now = pst_now_str()
    with db_cursor() as (conn, cursor), _one_at_a_time(cursor, APPROVAL, day):
        # The Heroku pool runs with autocommit on (config.py), so the two
        # writes need a transaction of their own to be one change.
        cursor.execute("START TRANSACTION")
        try:
            cursor.execute("DELETE FROM four_by_six_boards WHERE voter_hash = %s AND played_on = %s "
                           "AND board <> %s", (APPROVAL, day, board))
            replaced = cursor.rowcount
            cursor.execute(
                """INSERT INTO four_by_six_boards (played_on, voter_hash, board, source, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE updated_at = new.updated_at""",
                (day, APPROVAL, board, APPROVAL, now, now))
            conn.commit()
        except Exception:
            try:
                cursor.execute("ROLLBACK")
            except Exception:
                pass
            raise
    return replaced


def remove_approval(board):
    """Take back James's approvals of this board, every date; players' rows
    stay. Returns how many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM four_by_six_boards WHERE board = %s AND voter_hash = %s",
                       (board, APPROVAL))
        removed = cursor.rowcount
        conn.commit()
    return removed


def remove_board(board, day):
    """Delete every entry of this board on `day`, approvals too: test boards,
    or junk. Returns how many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM four_by_six_boards WHERE board = %s AND played_on = %s", (board, day))
        removed = cursor.rowcount
        conn.commit()
    return removed


##### boards: what they add up to #####

def _check(board):
    """(fewest moves, None) for a stored board, or (None, what's wrong): it
    has no solution (or isn't a board: a damaged row), or the search couldn't
    tell."""
    try:
        steps = fbs.solve(fbs.parse_board(board))
    except fbs.BoardError:
        steps = None
    except fbs.SearchLimit:
        return None, "couldn't be worked out"
    return (None, 'has no solution') if steps is None else (len(steps), None)


def _networks(rows):
    """One row per network (crowd.one_per_network), from players' rows."""
    return crowd.one_per_network([row for row in rows if row[SOURCE] in BOARD_SOURCES], VOTER, PREFIX, UPDATED)


def consensus(rows):
    """What one board's rows (one date's, or all) add up to: board, players
    (networks), why (None when settled by an approval or SERVE_NETWORKS
    players), approved, last_update."""
    approved = any(row[SOURCE] == APPROVAL for row in rows)
    players = len(_networks(rows))
    short = SERVE_NETWORKS - players
    return {
        'board': rows[0][BOARD],
        'players': players,
        'why': None if approved or short <= 0 else f'needs {short} more player{"s" if short != 1 else ""}',
        'approved': approved,
        'last_update': max(row[UPDATED] for row in rows),
    }


def first_seen(rows):
    """The first date this board was a day's puzzle: the earliest James
    approved it for, or that PAST_MIN_NETWORKS networks entered it on. None
    if neither yet."""
    by_day = defaultdict(list)
    for row in rows:
        by_day[row[PLAYED_ON]].append(row)
    for day in sorted(by_day):
        day_rows = by_day[day]
        if any(row[SOURCE] == APPROVAL for row in day_rows) or len(_networks(day_rows)) >= PAST_MIN_NETWORKS:
            return day
    return None


def todays_boards(rows, day):
    """The boards to offer players whose date is `day`, and what the admin
    page shows about that date.

    rows -- every four_by_six_boards row (any date) for each board logged on
            `day`.

    Only `day`'s own entries count: James's approval for that date, and the
    players who entered the board then. A past puzzle (first seen before
    `day`: a tab left open past midnight) is offered only if approved for
    `day`. His approval comes first, then the players' boards, most players
    first, at most TODAYS_BOARDS_MAX.

    Returns (boards, groups): boards is a list of {'board', 'approved'} for
    the page; groups is every board logged that date with its consensus,
    'fewest', 'first_seen' and 'served', most players first.
    """
    by_board = defaultdict(list)
    for row in rows:
        by_board[row[BOARD]].append(row)

    groups = []
    for board, board_rows in by_board.items():
        on_day = [row for row in board_rows if row[PLAYED_ON] == day]
        if not on_day:
            continue
        result = consensus(on_day)
        seen = first_seen(board_rows)
        result['first_seen'] = seen or min(row[PLAYED_ON] for row in board_rows)
        result['fewest'], problem = _check(board)
        if problem:
            result['why'] = problem
        elif seen is not None and seen < day and not result['approved']:
            result['why'] = f"a past puzzle, first seen {seen:%m-%d}"
        result['served'] = False
        groups.append(result)
    groups.sort(key=lambda g: (-g['players'], g['board']))

    ready = [g for g in groups if g['why'] is None]
    offered = [g for g in ready if g['approved']] + [g for g in ready if not g['approved']]
    for g in offered[TODAYS_BOARDS_MAX:]:
        g['why'] = 'past the limit of boards a day'
    boards = []
    for g in offered[:TODAYS_BOARDS_MAX]:
        g['served'] = True
        boards.append({'board': g['board'], 'approved': g['approved']})
    return boards, groups


# day -> (expires, boards): what _boards_for last offered, time.monotonic().
_boards_cache = {}
# Bumped by every admin change: a read that started before one is used by
# its own request but not kept, since it may predate the change.
_boards_gen = {'n': 0}
_boards_lock = threading.Lock()


def _forget_boards():
    """After an admin change: the next request reads the boards again."""
    with _boards_lock:
        _boards_gen['n'] += 1
        _boards_cache.clear()


def _boards_for(day):
    """The boards offered for `day`, as the page gets them. From the memo
    while fresh, else read again; never raises. When an entry goes stale,
    the first request pushes it out 15 seconds before reading and the others
    keep the stale boards meanwhile; a failed read keeps them too, and waits
    READ_RETRY_SECONDS to try again (routes/four_by_three.py)."""
    with _boards_lock:
        now = time.monotonic()
        entry = _boards_cache.get(day)
        if entry is not None and now < entry[0]:
            return entry[1]
        if entry is not None:
            _boards_cache[day] = (now + 15, entry[1])
        gen = _boards_gen['n']
    try:
        boards = todays_boards(rows_logged_between(day, day), day)[0]
        expires = time.monotonic() + BOARDS_CACHE_SECONDS
    except Exception as e:
        alerts.alert_throttled('four_by_six_board_read_failed', exc=type(e).__name__, msg=e)
        boards = entry[1] if entry is not None else []
        expires = time.monotonic() + READ_RETRY_SECONDS
    with _boards_lock:
        if _boards_gen['n'] == gen:
            _boards_cache[day] = (expires, boards)
        for old in [d for d in list(_boards_cache) if abs((d - day).days) > 2]:
            _boards_cache.pop(old, None)
    return boards


def first_paint_boards():
    """{date: boards} for each date near Pacific's today (within a day, as
    parse_played_on takes them) whose boards a lookup in this process found,
    fresh or not, for the page to draw its offer as it arrives, from the
    player's own date (an offer that came later would push the board down, a
    layout shift); None if there are none. Never reads the database: the page
    still asks for the newest."""
    if not FOUR_BY_SIX_BOARDS_LIVE:
        return None
    today = _pacific_today()
    with _boards_lock:
        entries = list(_boards_cache.items())
    days = {day.isoformat(): entry[1] for day, entry in entries if abs((day - today).days) <= 1}
    return days or None


def _played_on(data):
    """The player's own date, or None if it can't be today anywhere on Earth
    (more than a day from Pacific's): a wrong clock, not a reason to 400."""
    return parse_played_on(data.get('played_on'), _pacific_today())


def todays_reply(data, serve_dark=False):
    """The `todays_boards` action: the boards for the player's date."""
    day = _played_on(data)
    if day is None or not (serve_dark or FOUR_BY_SIX_BOARDS_LIVE):
        return jsonify(boards=[], day=day.isoformat() if day else None)
    return jsonify(boards=_boards_for(day), day=day.isoformat())


def log_board(data):
    """A board a player uses, sent once per version of it. Always the same
    reply, stored or not."""
    position = _board_from(data)
    source = data.get('source')
    if source not in BOARD_SOURCES:
        raise ValidationError('source must be typed or edited')
    day = _played_on(data)
    board = fbs.board_text(position)
    if day is None or not fbs.is_start(position) or _check(board)[1]:
        return jsonify(status='success')
    if source == 'edited' and board in [b['board'] for b in _boards_for(day)]:
        # Changed and then changed back: still the board on offer, which
        # can't vote for itself.
        return jsonify(status='success')
    store_board(day, crowd.player_hash('four_by_six'), crowd.prefix_hash('four_by_six'), board, source)
    return jsonify(status='success')


##### the page #####

def handle_action(data, serve_dark=False):
    """A JSON body posted with an `action`: a board to log, or today's
    boards. `serve_dark` (only /4x6_admin/board) offers boards even while
    FOUR_BY_SIX_BOARDS_LIVE is off."""
    action = data.get('action')
    if action == 'log_board':
        return log_board(data)
    if action == 'todays_boards':
        return todays_reply(data, serve_dark)
    raise ValidationError('unknown action')


@bp.route(PATH, methods=['GET', 'POST'])
@limiter.limit(INTERACTIVE_LIMITS)
def run_four_by_six():
    """The page, a solve, or an `action` (a board to log, today's boards)."""
    if request.method == 'POST':
        data = json_object()
        if 'action' in data:
            return handle_action(data)
        response = solve_request(data)
        log_solve(PAGE, data)
        return response
    schema_data = make_schema_data(
        "4x6 Answers & Hints Today - Hank Green's 4x6 Solver",
        "Stuck on today's 4x6, Hank Green's color sorting game? Enter the board for hints, or the solution "
        "in the fewest moves, for the most points.",
        "https://www.jamesapplewhite.com/4x6"
    )
    log_page_visit(PAGE)
    return render_template(PAGE, schema_data=schema_data, boards_on=FOUR_BY_SIX_BOARDS_LIVE,
                           first_boards=first_paint_boards(), post_url=PATH, approve=False, **page_context())


##### admin #####

ADMIN = '/4x6_admin'
# The dates the page shows: Pacific's last three, and tomorrow for players
# whose date is already ahead.
ADMIN_DAYS_BACK = 2


@bp.before_request
def _admin_posts_from_this_site():
    """A browser sends the saved admin password with a form another site
    posts here, so the password alone doesn't say James sent it."""
    if request.method == 'POST' and request.path.startswith(ADMIN) and from_another_site():
        abort(403)


def _back(**message):
    """Back to /4x6_admin, with a message."""
    return redirect(crowd.admin_return_url(ADMIN, request.form, **message))


def _admin_days():
    """(days, error): the boards of each date, newest date first."""
    today = _pacific_today()
    first, last = today - timedelta(days=ADMIN_DAYS_BACK), today + timedelta(days=1)
    try:
        rows = rows_logged_between(first, last)
    except Exception as e:
        print(f"Error loading 4x6 admin boards: {e}")
        return [], f'{type(e).__name__}: {e}'
    days = []
    day = last
    while day >= first:
        _boards, groups = todays_boards(rows, day)
        if groups:
            days.append({'day': day, 'groups': groups})
        day -= timedelta(days=1)
    return days, None


@bp.route(ADMIN)
@auth.login_required
def four_by_six_admin():
    """Recent boards to approve, with each one's players and par."""
    days, load_error = _admin_days()
    return render_template('four_by_six_admin.html', days=days, load_error=load_error,
                           boards_live=FOUR_BY_SIX_BOARDS_LIVE, pacific_today=_pacific_today(),
                           serve_networks=SERVE_NETWORKS, todays_boards_max=TODAYS_BOARDS_MAX,
                           par_over=fbs.PAR_OVER_FEWEST, names=TILE_NAMES, symbols=TILE_SYMBOLS, width=fbs.WIDTH)


def _text_board(value):
    """A starting board's 24 codes from a form or a link, checked, or None
    (a board from partway through a game is no day's board)."""
    try:
        position = fbs.parse_board(value)
    except fbs.BoardError:
        return None
    return fbs.board_text(position) if fbs.is_start(position) else None


@bp.route(ADMIN + '/board', methods=['GET', 'POST'])
@auth.login_required
def four_by_six_admin_board():
    """James's board entry: /4x6's own editor and solver, with an Approve bar
    in place of logging. Its JSON posts are solves and today's boards; it
    never logs a board, so nothing here counts as a player. ?board= (from
    /4x6_admin) starts it with that board, and ?day= is the Approve date."""
    if request.method == 'POST':
        data = json_object()
        if 'action' in data:
            if data.get('action') != 'todays_boards':
                raise ValidationError('only todays_boards here')
            return handle_action(data, serve_dark=True)
        return solve_request(data)
    preload, load_error = None, None
    raw = request.args.get('board', '')
    if raw:
        preload = _text_board(raw)
        if preload is None:
            load_error = 'Not a board: open one from the admin page'
    day = parse_iso_day(request.args.get('day', ''))
    return render_template(PAGE, schema_data=None, boards_on=True, first_boards=None, post_url=ADMIN + '/board',
                           approve=True, approve_day=day or _pacific_today(), preload=preload,
                           load_error=load_error, **page_context())


@bp.route(ADMIN + '/approve_typed_board', methods=['POST'])
@auth.login_required
def four_by_six_approve_typed_board():
    """Serve this board to players whose date is `played_on`: one James
    entered on /4x6_admin/board, or Approve on a player's board."""
    day = parse_iso_day(request.form.get('played_on', ''))
    board = _text_board(request.form.get('board', ''))
    if board is None:
        return _back(error='Not a board: check the 24 tiles, then approve again')
    if day is None:
        return _back(error='Pick a date')
    problem = _check(board)[1]
    if problem:
        return _back(error=f"That board {problem}, so it wasn't approved: check its tiles")
    try:
        replaced = store_approval(day, board)
    except Exception as e:
        print(f"Error approving 4x6 board: {e}")
        return _back(error='Database error')
    _forget_boards()
    instead = ', in place of your earlier board that date' if replaced else ''
    return _back(success=f"Approved the board for {day:%m-%d}: it's served now{instead}")


@bp.route(ADMIN + '/unapprove_board', methods=['POST'])
@auth.login_required
def four_by_six_unapprove_board():
    """Undo James's approval of a board, every date's: the players' boards
    decide again, and none of them is touched."""
    board = _text_board(request.form.get('board', ''))
    if board is None:
        return _back(error='Not a board')
    try:
        removed = remove_approval(board)
    except Exception as e:
        print(f"Error undoing 4x6 approval: {e}")
        return _back(error='Database error')
    if not removed:
        return _back(error='That board was not approved')
    _forget_boards()
    return _back(success="Approval undone: the players' boards decide again")


@bp.route(ADMIN + '/remove_board', methods=['POST'])
@auth.login_required
def four_by_six_remove_board():
    """Delete one date's entries of a board, approvals too: test boards, or
    junk. Takes the board as stored, even empty, so a damaged row can go
    too."""
    board = request.form.get('board')
    played_on = request.form.get('played_on', '')
    day = parse_iso_day(played_on) if _DATE.fullmatch(played_on) else None
    if day is None or board is None or len(board) > fbs.TILES:
        return _back(error='Not a board')
    try:
        removed = remove_board(board, day)
    except Exception as e:
        print(f"Error removing 4x6 board: {e}")
        return _back(error='Database error')
    _forget_boards()
    return _back(success=f"Removed {removed} entr{'y' if removed == 1 else 'ies'} of that board on {day}")
