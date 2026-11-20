"""Typeshift: the /typeshift page and its solves, today's boards, word
reports, and /typeshift_admin.

The solver (functions/typeshift.py) works on the word list already in memory
(data.words, the ENABLE list) with Typeshift's own crowd corrections, ranked
by data.word_pop. Nothing is fetched at runtime: the solver never contacts
Puzzmo.

Today's boards. Entering a Typeshift board means typing every column, so the
first players each day do it for the players after them, as on /circuits
(whose rules these copy). A player's board is logged once they use the
solver on a board they typed, and once enough players (one per network)
have entered the same board, later players are offered it. Nothing is
guessed: every board served was typed by players, or by James, who approves
boards on /typeshift_admin or enters his own on /typeshift_admin/board.

The day is Puzzmo's: the date in America/Chicago, for every player. Puzzmo's
own page code works "today" out in that zone (checked 2026-11-20), so a new
board starts at midnight US Central everywhere. It is the server's clock,
not the player's. The page says which day a board was started on, which
counts only for today or yesterday: a player who started yesterday's puzzle
before midnight Central and finishes it after logs yesterday's board. For
the first YESTERDAY_HOURS of a day, yesterday's board is offered too.

Word reports work as on /ribbit (crowd.py): a ✗ on a word Typeshift refused,
and a box for a word it accepted that the list lacks. Typeshift's
corrections are its own (the typeshift_* tables), never another game's.
"""

import hashlib
import itertools
import json
import os
import re
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta

import pytz
from flask import Blueprint, jsonify, redirect, render_template, request

import crowd
from data import data_folder, word_pop, words
from extensions import (INTERACTIVE_LIMITS, auth, db_cursor, limiter, log_page_visit, log_solve,
                        pst_now_str)
from functions import typeshift as ts
from functions.phrase_solver import is_blocked, load_blocklist
from helpers import ISO_DATE as _DATE
from helpers import ValidationError, json_object, make_schema_data, parse_iso_day, release_memory
from monitoring import alerts

bp = Blueprint('typeshift', __name__)

PAGE = 'typeshift.html'

# Puzzmo's calendar (see the module docstring).
GAME_TZ = pytz.timezone('America/Chicago')


def _game_now():
    """The time now in Puzzmo's calendar. A name of this module's own, so
    tests can set the clock."""
    return datetime.now(GAME_TZ)


def _game_today():
    """Today's date in Puzzmo's calendar."""
    return _game_now().date()


##### the word list #####

# The index (functions/typeshift.py Index) of the word list with Typeshift's
# corrections, as CrowdList.corrected keeps it: read again every 12 hours,
# or straight after a change. A module memo, not SimpleCache, which would
# pickle a copy on every read.
_index = {'value': None, 'expires': 0.0}


def _forget_words():
    """Make the next get_index() re-read the corrections."""
    _index['expires'] = 0.0


TYPESHIFT_CROWD = crowd.CrowdList('typeshift', on_change=_forget_words)


def _build_index(invalid, added):
    """The index of data.words minus the removed words plus the added ones.
    It filters as it goes, so no corrected copy of the whole list is held."""
    source = (w for w in words if w not in invalid) if invalid else words
    index = ts.Index.build(itertools.chain(source, added))
    release_memory()
    return index


def get_index():
    """The full word list with Typeshift's own corrections applied: words
    players or /typeshift_admin removed taken out, words added put in. Only
    typeshift_* tables count. The raw list if the DB has never been
    reachable (see crowd.CrowdList.corrected)."""
    return TYPESHIFT_CROWD.corrected(_index, _build_index)


# The offensive-word list (datasets/phrase_blocklist.txt), read once: the plan
# and the hints use a word on it only when nothing else uses a letter.
_blocklist = {'value': None}


def _blocked(word):
    """Is this word on the list? If the list won't load, none is."""
    if _blocklist['value'] is None:
        try:
            _blocklist['value'] = load_blocklist(os.path.join(data_folder, 'phrase_blocklist.txt'))
        except OSError as e:
            alerts.alert_throttled('typeshift_blocklist_failed', exc=type(e).__name__, msg=e)
            _blocklist['value'] = frozenset()
    return is_blocked(word, _blocklist['value'])


def page_context():
    """What the page template needs besides the SEO blob. Rendering the page
    never touches the database."""
    return {'data_ok': bool(words), 'min_cols': ts.MIN_COLS, 'max_cols': ts.MAX_COLS,
            'max_letters': ts.MAX_LETTERS, 'yesterday_hours': YESTERDAY_HOURS}


##### the page's requests #####

# A word in a body: as long as a board can be wide.
_WORD = re.compile(r'[a-z]{%d,%d}' % (ts.MIN_COLS, ts.MAX_COLS))
# Words a player has hidden with ✗, sent with each solve so it leaves them out.
MAX_EXCLUDE = 200
# The page's modes, kept in the vote log only.
PAGE_MODES = ('help', 'solve')


def _board_from(data):
    """A board from a POST body's columns, or ValidationError."""
    try:
        return ts.parse_board(data.get('columns'))
    except ts.BoardError as e:
        raise ValidationError(str(e))


def _word_from(value, field='word'):
    word = value.strip().lower() if isinstance(value, str) else ''
    if not _WORD.fullmatch(word):
        raise ValidationError(f'{field} must be {ts.MIN_COLS} to {ts.MAX_COLS} letters')
    return word


def _exclude_from(data):
    raw = data.get('exclude')
    if raw is None:
        return frozenset()
    if not isinstance(raw, list) or len(raw) > MAX_EXCLUDE:
        raise ValidationError('exclude must list words')
    return frozenset(_word_from(w, 'exclude') for w in raw)


def solve_request(data):
    """The JSON the page renders: functions/typeshift.py solve() for the
    board, the tiles the player has marked solid and the words they hid."""
    board = _board_from(data)
    try:
        solid = ts.parse_solid(data.get('solid'), board)
    except ts.BoardError as e:
        raise ValidationError(str(e))
    result = ts.solve(get_index(), board, solid=solid, exclude=_exclude_from(data),
                      popularity=word_pop, blocked=_blocked)
    return jsonify(result)


##### boards: shape and storage #####

# When players' boards are served: this many players (one per network)
# entering the same board that day. Players are never told the number.
SERVE_PLAYERS = 2
# A board this many networks entered on an earlier day was that day's
# puzzle: not served as a later day's unless James approves it for that day.
PAST_MIN_NETWORKS = 2
# The most boards one day offers: approvals first, then the players' boards.
TODAYS_BOARDS_MAX = 3
# Boards one player can log on one day: a few typo fixes, with room to spare.
BOARDS_PER_PLAYER_PER_DAY = 10
# 'typed' = entered by the player; 'edited' = loaded from today's boards,
# then changed. A board loaded and left as it is is never logged, so an
# offered board can't confirm itself.
BOARD_SOURCES = ('typed', 'edited')
# Live on /typeshift from launch. False keeps logging boards but offers none
# (except on /typeshift_admin/board).
TYPESHIFT_BOARDS_LIVE = True
# An approval is a row of its own, with this as its source and APPROVAL:site
# in place of a player hash (a real one is 32 hex characters), so each site
# can approve a board once a day. Never a player.
APPROVAL = 'admin'
# The sites that run a daily Typeshift board. Yahoo Games has none (checked
# 2026-11-20); another site is one more entry.
SITE_NAMES = {'puzzmo': 'Puzzmo'}
BOARD_SITES = tuple(SITE_NAMES)
# How long a day's boards are reused before they are read again. One
# gunicorn worker, so this memo is the only copy.
BOARDS_CACHE_SECONDS = 60
# How long a failed read waits before the next try.
READ_RETRY_SECONDS = 300
# For this many hours after midnight Central, yesterday's boards are offered
# too, for players still finishing yesterday's puzzle (James, 2026-11-20).
YESTERDAY_HOURS = 3

# The columns every read of typeshift_boards returns, in this order.
_COLUMNS = 'board_key, letters, site, source, voter_hash, played_on, updated_at, prefix_hash'
(KEY, LETTERS, SITE, SOURCE, VOTER, PLAYED_ON, UPDATED, PREFIX) = range(8)


def canonical(board):
    """What makes two boards the same: each column's letters, in any order
    within the column, column by column."""
    return '|'.join(''.join(sorted(col.lower())) for col in board)


def board_key(board):
    return hashlib.sha1(canonical(board).encode()).hexdigest()


def letters_text(board):
    """A board as stored: its columns as typed, upper case, '|'-joined."""
    return '|'.join(col.upper() for col in board)


def _split(text):
    return (text or '').split('|')


def _fetch(sql, params):
    with db_cursor() as (conn, cursor):
        cursor.execute(sql, params)
        return cursor.fetchall()


def rows_with_key(key):
    """Every row for one board, any date."""
    return _fetch(f"SELECT {_COLUMNS} FROM typeshift_boards WHERE board_key = %s", (key,))


def rows_logged_between(first, last):
    """Every row, any date, for each board logged between these days."""
    return _fetch(
        f"SELECT {_COLUMNS} FROM typeshift_boards WHERE board_key IN "
        "(SELECT board_key FROM typeshift_boards WHERE played_on BETWEEN %s AND %s)",
        (first, last))


def store_board(day, voter, prefix, key, letters, source):
    """Log one player's board, replacing their earlier entry of it that day.
    Inline, not on the write-behind queue: a missing table there would drop
    the rest of its batch, page visits included. Best-effort."""
    now = pst_now_str()
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute(
                "SELECT COUNT(*) FROM typeshift_boards "
                "WHERE voter_hash = %s AND played_on = %s AND board_key <> %s",
                (voter, day, key))
            if cursor.fetchone()[0] >= BOARDS_PER_PLAYER_PER_DAY:
                return
            cursor.execute(
                """INSERT INTO typeshift_boards (played_on, voter_hash, prefix_hash, board_key, letters,
                       site, source, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, '', %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE letters = new.letters, source = new.source,
                       prefix_hash = new.prefix_hash, updated_at = new.updated_at""",
                (day, voter, prefix, key, letters, source, now, now))
            conn.commit()
    except Exception as e:
        alerts.alert_throttled('typeshift_board_log_failed', exc=type(e).__name__, msg=e)


def store_approval(day, site, key, letters):
    """Approve this board for `site` on `day`. A site has one board a day, so
    this takes the place of that site's approval of any other board that day
    (players' rows and other sites' approvals stay). Both writes happen or
    neither. Returns how many other boards' approvals it replaced. Raises,
    unlike store_board: the admin page says whether it worked."""
    now = pst_now_str()
    voter = f'{APPROVAL}:{site}'
    with db_cursor() as (conn, cursor):
        # The Heroku pool runs with autocommit on (config.py), so the two
        # writes need a transaction of their own to be one change.
        cursor.execute("START TRANSACTION")
        try:
            cursor.execute("DELETE FROM typeshift_boards WHERE voter_hash = %s AND played_on = %s AND board_key <> %s",
                           (voter, day, key))
            replaced = cursor.rowcount
            cursor.execute(
                """INSERT INTO typeshift_boards (played_on, voter_hash, board_key, letters, site, source,
                       created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE letters = new.letters, updated_at = new.updated_at""",
                (day, voter, key, letters, site, APPROVAL, now, now))
            conn.commit()
        except Exception:
            try:
                cursor.execute("ROLLBACK")
            except Exception:
                pass
            raise
    return replaced


def remove_approval(key, site):
    """Take back `site`'s approvals of this board, any day; players' boards
    stay. Returns how many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM typeshift_boards WHERE board_key = %s AND voter_hash = %s",
                       (key, f'{APPROVAL}:{site}'))
        removed = cursor.rowcount
        conn.commit()
    return removed


def remove_board(key, day):
    """Delete every entry of this board on `day`, approvals too: test boards,
    or junk. Returns how many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM typeshift_boards WHERE board_key = %s AND played_on = %s", (key, day))
        removed = cursor.rowcount
        conn.commit()
    return removed


##### boards: what they add up to #####

def _networks(rows):
    """One player row per network (crowd.one_per_network)."""
    return crowd.one_per_network([row for row in rows if row[SOURCE] in BOARD_SOURCES],
                                 VOTER, PREFIX, UPDATED)


def consensus(rows):
    """What the rows for one board add up to.

    rows -- typeshift_boards rows (see _COLUMNS) sharing one board_key. Each
            player's latest entry counts, and players on one network count
            once.

    Returns a dict: letters (as served: James's approval as he typed it,
    else the order most of its players typed, '|'-joined) and columns (the
    same, a list), canon (each column's letters sorted, a list), players,
    settled (an approval, or SERVE_PLAYERS players), why (None when settled,
    else what it's waiting for), last_update; approved, approved_at and
    sites (James's approvals); latest_at and latest_letters (the most recent
    player entry, the one Add copies).
    """
    approvals = [row for row in rows if row[SOURCE] == APPROVAL]
    player_rows = [row for row in rows if row[SOURCE] in BOARD_SOURCES]
    networks = _networks(rows)
    players = len(networks)
    approval = max(approvals, key=lambda row: row[UPDATED]) if approvals else None
    if approval is not None:
        letters = approval[LETTERS]
    elif networks:
        counts = Counter(row[LETTERS] for row in networks.values())
        first_at = {}
        for row in networks.values():
            first_at[row[LETTERS]] = min(first_at.get(row[LETTERS], row[UPDATED]), row[UPDATED])
        letters = min(counts, key=lambda text: (-counts[text], first_at[text], text))
    else:
        letters = rows[0][LETTERS]
    columns = _split(letters)
    settled = approval is not None or players >= SERVE_PLAYERS
    why = None
    if not settled:
        why = f'needs {SERVE_PLAYERS - players} more player{"s" if SERVE_PLAYERS - players != 1 else ""}'
    newest = max(player_rows, key=lambda row: row[UPDATED]) if player_rows else None
    return {
        'letters': letters,
        'columns': columns,
        'canon': [''.join(sorted(col.lower())) for col in columns],
        'players': players,
        'settled': settled,
        'why': why,
        'last_update': max(row[UPDATED] for row in (player_rows or rows)),
        'approved': approval is not None,
        'approved_at': min(row[UPDATED] for row in approvals) if approvals else None,
        'sites': sorted({row[SITE] for row in approvals}),
        'latest_at': newest[UPDATED] if newest is not None else None,
        'latest_letters': newest[LETTERS] if newest is not None else None,
    }


def first_seen(rows):
    """The first day this board was a day's puzzle: the earliest date James
    approved it for, or that PAST_MIN_NETWORKS networks entered it on. None
    if neither yet (one player alone can't date a board)."""
    by_day = {}
    for row in rows:
        by_day.setdefault(row[PLAYED_ON], []).append(row)
    for day in sorted(by_day):
        day_rows = by_day[day]
        if any(row[SOURCE] == APPROVAL for row in day_rows) or len(_networks(day_rows)) >= PAST_MIN_NETWORKS:
            return day
    return None


def todays_boards(rows, day):
    """The boards to offer on `day`, and what the admin tab shows about it.

    rows -- every typeshift_boards row (any date) for each board logged on
            `day`.

    Only `day`'s own entries count: James's approvals for that day, and the
    players who entered the board that day. Two kinds of board are not
    offered unless approved for the day: a past puzzle (first_seen before
    `day`: an archive replay, or yesterday's board logged late), and a
    board that is only the first columns of a bigger board at least as many
    players entered that day (half-typed). James's approvals come first, in
    the order he approved them, then the players' boards, most players
    first, at most TODAYS_BOARDS_MAX.

    Returns (boards, groups): boards is a list of {'columns', 'sites'} for
    the page; groups is every board logged that day with its consensus, its
    'key', 'first_seen', 'past' and 'served', most players first.
    """
    by_key = {}
    for row in rows:
        by_key.setdefault(row[KEY], []).append(row)

    groups = []
    for key, group_rows in by_key.items():
        on_day = [row for row in group_rows if row[PLAYED_ON] == day]
        if not on_day:
            continue
        result = consensus(on_day)
        seen = first_seen(group_rows)
        result['first_seen'] = seen or min(row[PLAYED_ON] for row in group_rows)
        result['past'] = seen is not None and seen < day
        if result['past'] and not result['approved']:
            result['why'] = f"a past puzzle, first seen {seen:%m-%d}"
        result['key'] = key
        result['served'] = False
        groups.append(result)

    for g in groups:
        if g['approved'] or g['why']:
            continue
        n = len(g['canon'])
        if any(len(other['canon']) > n and other['canon'][:n] == g['canon'] and other['players'] >= g['players']
               for other in groups if other is not g):
            g['why'] = 'the first columns of a bigger board'
    groups.sort(key=lambda g: (-g['players'], g['key']))

    ready = [g for g in groups if g['why'] is None]
    approved = sorted((g for g in ready if g['approved']), key=lambda g: g['approved_at'])
    offered = approved + [g for g in ready if not g['approved']]
    for g in offered[TODAYS_BOARDS_MAX:]:
        g['why'] = 'past the limit of boards a day'
    boards = []
    for g in offered[:TODAYS_BOARDS_MAX]:
        g['served'] = True
        boards.append({'columns': g['columns'], 'sites': [SITE_NAMES.get(s, s) for s in g['sites']]})
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
    the first request pushes it out 15 seconds before reading, and the
    others keep the stale boards meanwhile (routes/circuits.py); a failed
    read keeps them too, and waits READ_RETRY_SECONDS to try again."""
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
        alerts.alert_throttled('typeshift_board_read_failed', exc=type(e).__name__, msg=e)
        boards = entry[1] if entry is not None else []
        expires = time.monotonic() + READ_RETRY_SECONDS
    with _boards_lock:
        if _boards_gen['n'] == gen:
            _boards_cache[day] = (expires, boards)
        for old in [d for d in list(_boards_cache) if abs((d - day).days) > 2]:
            _boards_cache.pop(old, None)
    return boards


def todays_reply(serve_dark=False):
    """The `todays_boards` action: the boards for Puzzmo's today and, in the
    first YESTERDAY_HOURS after midnight Central, yesterday's, each list as
    _boards_for gives it; plus today's date."""
    now = _game_now()
    today = now.date()
    if not (serve_dark or TYPESHIFT_BOARDS_LIVE):
        return jsonify(boards=[], yesterday=[], day=today.isoformat())
    yesterday = []
    if now.hour < YESTERDAY_HOURS:
        offered = {canonical(b['columns']) for b in _boards_for(today)}
        yesterday = [b for b in _boards_for(today - timedelta(days=1)) if canonical(b['columns']) not in offered]
    return jsonify(boards=_boards_for(today), yesterday=yesterday, day=today.isoformat(),
                   yesterday_day=(today - timedelta(days=1)).isoformat())


def _board_day(value):
    """The day a logged board belongs to: the page's `day` (Puzzmo's date
    when the player started the board) if that is today or yesterday in
    Puzzmo's calendar, else today. Never a reason to refuse a log."""
    today = _game_today()
    day = parse_iso_day(value)
    return day if day is not None and today - timedelta(days=1) <= day <= today else today


def log_board(data):
    """A board a player uses, sent once per version of it. Always the same
    reply, stored or not."""
    board = _board_from(data)
    source = data.get('source')
    if source not in BOARD_SOURCES:
        raise ValidationError('source must be typed or edited')
    day = _board_day(data.get('day'))
    if source == 'edited' and canonical(board) in {canonical(b['columns']) for b in _boards_for(day)}:
        # Changed and then changed back: still the board on offer, which
        # can't vote for itself.
        return jsonify(status='success')
    store_board(day, crowd.player_hash('typeshift'), crowd.prefix_hash('typeshift'), board_key(board),
                letters_text(board), source)
    return jsonify(status='success')


##### word reports #####

VOTE_ACTIONS = ('reject', 'restore', 'suggest_missing')


def puzzle_tag(board):
    """Which board a vote came from: each column's letters sorted, joined by
    colons (the colon is what makes the admin search look at boards)."""
    return ':'.join(''.join(sorted(col)) for col in board)


def handle_vote(data):
    """A crowd vote posted to /typeshift. Every reply is the same whether or
    not the vote changed the list (see crowd.py)."""
    action = data.get('action')
    board = _board_from(data)
    word = _word_from(data.get('word'))
    tag = puzzle_tag(board)
    mode = data.get('mode') if data.get('mode') in PAGE_MODES else None

    if action == 'reject':
        # Only a word on offer for this board: anything else is not a result
        # this player could have been shown.
        if ts.fits_board(word, board) and word in get_index():
            TYPESHIFT_CROWD.record_vote(word, 'invalid', tag, mode)
        return jsonify(status='success')

    if action == 'restore':
        # Tapping a hidden word back: takes back this player's ✗, if it
        # counted.
        TYPESHIFT_CROWD.retract_vote(word, 'invalid', tag, mode)
        return jsonify(status='success')

    # Only a word this board allows: blocks most typos and all junk.
    if not ts.fits_board(word, board):
        return jsonify(status='success', result='not_on_board')
    if word in get_index():
        return jsonify(status='success', result='already_listed')
    TYPESHIFT_CROWD.record_vote(word, 'missing', tag, mode)
    return jsonify(status='success', result='recorded')


def handle_action(data, serve_dark=False):
    """A JSON body posted with an `action`: a board to log, today's boards,
    or a crowd vote. `serve_dark` (only /typeshift_admin/board) offers boards
    even while TYPESHIFT_BOARDS_LIVE is off."""
    action = data.get('action')
    if action == 'log_board':
        return log_board(data)
    if action == 'todays_boards':
        return todays_reply(serve_dark)
    if action in VOTE_ACTIONS:
        return handle_vote(data)
    raise ValidationError('unknown action')


@bp.route('/typeshift', methods=['GET', 'POST'])
@limiter.limit(INTERACTIVE_LIMITS)
def run_typeshift():
    """The page, a solve, or an `action` (a board to log, today's boards, a
    word report)."""
    if request.method == 'POST':
        data = json_object()
        if 'action' in data:
            return handle_action(data)
        response = solve_request(data)
        log_solve(PAGE, data)
        return response
    schema_data = make_schema_data(
        "Typeshift Answers & Hints - Solver for Today's Puzzmo Typeshift",
        "Stuck on Puzzmo's Typeshift? Type the columns and get a hint for each letter you have left, "
        "or the fewest words that use every letter.",
        "https://www.jamesapplewhite.com/typeshift"
    )
    log_page_visit(PAGE)
    return render_template(PAGE, schema_data=schema_data, boards_on=TYPESHIFT_BOARDS_LIVE,
                           post_url='/typeshift', approve=False, **page_context())


##### admin #####

ADMIN = '/typeshift_admin'
ADMIN_TABS = ('boards', 'reports', 'invalid', 'added')
# The days the Today's Boards tab shows: the game's today and the two before.
ADMIN_DAYS_BACK = 2
# How the tab's Add sends back the time of the board it showed, so the
# approval can check that board is still the latest.
_STAMP = '%Y-%m-%d %H:%M:%S'


def _back(**message):
    """Back to /typeshift_admin as the admin left it, with a message."""
    return redirect(crowd.admin_return_url(ADMIN, request.form, **message))


def _is_key(value):
    return isinstance(value, str) and len(value) == 40 and all(c in '0123456789abcdef' for c in value)


def _admin_days():
    """(days, error): the Today's Boards tab, today first."""
    today = _game_today()
    first = today - timedelta(days=ADMIN_DAYS_BACK)
    try:
        rows = rows_logged_between(first, today)
    except Exception as e:
        print(f"Error loading typeshift admin boards: {e}")
        return [], f'{type(e).__name__}: {e}'
    days = []
    day = today
    while day >= first:
        _boards, groups = todays_boards(rows, day)
        for g in groups:
            g['latest_stamp'] = g['latest_at'].strftime(_STAMP) if g['latest_at'] else ''
        if groups:
            days.append({'day': day, 'groups': groups})
        day -= timedelta(days=1)
    return days, None


@bp.route(ADMIN)
@auth.login_required
def typeshift_admin():
    """Today's boards to approve, what players have reported, and
    Typeshift's corrected word list."""
    args = request.args.to_dict()
    args.setdefault('tab', 'boards')
    try:
        view, crowd_error = TYPESHIFT_CROWD.admin_view(args, request.path, tabs=ADMIN_TABS), None
    except Exception as e:
        print(f"Error loading typeshift admin word lists: {e}")
        view = TYPESHIFT_CROWD.empty_view(ADMIN, args['tab'] if args['tab'] in ADMIN_TABS else 'boards')
        crowd_error = f'{type(e).__name__}: {e}'
    days, load_error = _admin_days()
    return render_template('typeshift_admin.html', **view,
                           crowd_error=crowd_error, days=days, load_error=load_error,
                           boards_live=TYPESHIFT_BOARDS_LIVE, game_today=_game_today(), sites=SITE_NAMES,
                           serve_players=SERVE_PLAYERS, todays_boards_max=TODAYS_BOARDS_MAX,
                           yesterday_hours=YESTERDAY_HOURS)


def editor_board(key):
    """The board /typeshift_admin/board opens with when James clicks one on
    the Today's Boards tab: (columns, note), or None if no row has this key.
    His approval as he typed it, else the order its players settled on,
    else the latest player's entry."""
    rows = rows_with_key(key)
    if not rows:
        return None
    result = consensus(rows)
    if result['approved']:
        letters, note = result['letters'], f"your approval from {result['approved_at']:%m-%d %H:%M}"
    elif result['settled']:
        letters, note = result['letters'], f"the board {result['players']} players entered"
    elif result['latest_letters']:
        letters = result['latest_letters']
        note = f"the latest player's board, from {result['latest_at']:%m-%d %H:%M}"
    else:
        letters, note = result['letters'], 'its letters'
    return _split(letters), note


@bp.route(ADMIN + '/board', methods=['GET', 'POST'])
@auth.login_required
def typeshift_admin_board():
    """James's board entry: /typeshift's own editor and solver, with an
    Approve bar (a site and a date) in place of logging. Its JSON posts are
    solves and today's boards; it never logs a board or a vote, so nothing
    here counts as a player. ?key= (from the Today's Boards tab) starts it
    with that board, and ?day= is the Approve date."""
    if request.method == 'POST':
        data = json_object()
        if 'action' in data:
            if data.get('action') != 'todays_boards':
                raise ValidationError('only todays_boards here')
            return handle_action(data, serve_dark=True)
        return solve_request(data)
    preload, load_error = None, None
    key = request.args.get('key', '')
    if key and not _is_key(key):
        load_error = "Not a board: open one from the Today's Boards tab"
    elif key:
        try:
            found = editor_board(key)
        except Exception as e:
            print(f"Error loading a typeshift board for the editor: {e}")
            found, load_error = None, "Couldn't read the boards"
        if found is not None:
            columns, note = found
            preload = {'columns': columns, 'note': note}
        elif load_error is None:
            load_error = 'No board has that key'
    day = parse_iso_day(request.args.get('day', '')) or _game_today()
    return render_template(PAGE, schema_data=None, boards_on=True, post_url=ADMIN + '/board',
                           approve=True, approve_day=day, preload=preload, load_error=load_error,
                           sites=SITE_NAMES, **page_context())


def _in_place(replaced, site):
    return f", in place of your earlier {SITE_NAMES[site]} board that day" if replaced else ""


@bp.route(ADMIN + '/approve_board', methods=['POST'])
@auth.login_required
def typeshift_approve_board():
    """Add: serve the latest player entry of this board, for `site`, straight
    away: for a board James entered himself on /typeshift and trusts.
    `latest` is that entry's time as the page showed it; if a newer one has
    come in since, nothing is approved, so a stranger's board can't take its
    place."""
    key = request.form.get('key', '')
    day = parse_iso_day(request.form.get('played_on', ''))
    site = request.form.get('site', '')
    if not _is_key(key) or day is None or site not in BOARD_SITES:
        return _back(error='Not a board')
    try:
        players = [row for row in rows_with_key(key) if row[SOURCE] in BOARD_SOURCES]
        newest = max(players, key=lambda row: row[UPDATED]) if players else None
        if newest is None:
            return _back(error='No player board to approve with that key')
        if newest[UPDATED].strftime(_STAMP) != request.form.get('latest'):
            return _back(error='A newer board came in since the page loaded. Check it, then approve again')
        replaced = store_approval(day, site, key, newest[LETTERS])
    except Exception as e:
        print(f"Error approving typeshift board: {e}")
        return _back(error='Database error')
    _forget_boards()
    return _back(success=f"Approved for {SITE_NAMES[site]}: the board from "
                         f"{newest[UPDATED]:%m-%d %H:%M} is served now{_in_place(replaced, site)}")


@bp.route(ADMIN + '/approve_typed_board', methods=['POST'])
@auth.login_required
def typeshift_approve_typed_board():
    """Serve a board James entered on /typeshift_admin/board, for `site` on
    `played_on`, straight away. `columns` is the columns as a JSON list."""
    day = parse_iso_day(request.form.get('played_on', ''))
    site = request.form.get('site', '')
    try:
        board = ts.parse_board(json.loads(request.form.get('columns', '')))
    except (ValueError, TypeError, ts.BoardError):
        return _back(error='Not a board: check the columns, then approve again')
    if day is None or site not in BOARD_SITES:
        return _back(error='Pick a date and a site')
    try:
        replaced = store_approval(day, site, board_key(board), letters_text(board))
    except Exception as e:
        print(f"Error approving typed typeshift board: {e}")
        return _back(error='Database error')
    _forget_boards()
    return _back(success=f"Approved your board for {SITE_NAMES[site]} on {day:%m-%d}: "
                         f"it's served now{_in_place(replaced, site)}")


@bp.route(ADMIN + '/unapprove_board', methods=['POST'])
@auth.login_required
def typeshift_unapprove_board():
    """Undo one site's approval of a board, every day's: the players' boards
    decide again, and none of them is touched."""
    key = request.form.get('key', '')
    site = request.form.get('site', '')
    if not _is_key(key) or site not in BOARD_SITES:
        return _back(error='Not a board')
    try:
        removed = remove_approval(key, site)
    except Exception as e:
        print(f"Error undoing typeshift approval: {e}")
        return _back(error='Database error')
    if not removed:
        return _back(error=f'That board was not approved for {SITE_NAMES[site]}')
    _forget_boards()
    return _back(success=f"{SITE_NAMES[site]} approval undone: the players' boards decide again")


@bp.route(ADMIN + '/remove_board', methods=['POST'])
@auth.login_required
def typeshift_remove_board():
    """Delete one day's entries of a board, approvals too: test boards, or junk."""
    key = request.form.get('key', '')
    played_on = request.form.get('played_on', '')
    if not (_is_key(key) and _DATE.fullmatch(played_on)):
        return _back(error='Not a board')
    try:
        removed = remove_board(key, date.fromisoformat(played_on))
    except Exception as e:
        print(f"Error removing typeshift board: {e}")
        return _back(error='Database error')
    _forget_boards()
    return _back(success=f"Removed {removed} entr{'y' if removed == 1 else 'ies'} of that board on {played_on}")


@bp.route(ADMIN + '/add_word', methods=['POST'])
@auth.login_required
def typeshift_add_word():
    """Remove a word from Typeshift's list ('invalid') or add one ('missing')."""
    return crowd.admin_add_word(TYPESHIFT_CROWD, _back)


@bp.route(ADMIN + '/remove_word', methods=['POST'])
@auth.login_required
def typeshift_remove_word():
    """Undo an entry in either list, and reset that word's votes."""
    return crowd.admin_undo_word(TYPESHIFT_CROWD, _back)
