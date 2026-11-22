"""Circuits: the /circuits page and its solves, today's boards, and
/circuits_admin.

Self-contained: the word data ships in datasets/circuits_data.npz (see
datasets/CIRCUITS_DATA.md and functions/circuits.py), and nothing is fetched
at runtime. The solver never contacts Puzzmo or Yahoo.

Today's boards. Entering a Circuits board means typing every tile and
checking every arrow, so the first players each day do it for the players
after them, as on /ribbit (routes/ribbit.py, whose rules these copy). A
player's board is logged once they use the solver on a board they typed,
and once enough players agree on a board (its cells exactly, each arrow by
majority), later players are offered it. Nothing is guessed: every arrow
served was drawn by players, or by James.

James approves boards on /circuits_admin, one approval per site: Puzzmo and
Yahoo Games (powered by Puzzmo) each have their own daily board, so a day can
offer more than one, and the player picks theirs. He can also enter a
board's real answers. A solve of that board then shows them as checked, and
they measure how often the solver is right (the Accuracy tab). The words
players keep are stored for him to see, never served: anyone can type
anything.

The day is Puzzmo's: the date in America/Chicago, for every player. Puzzmo's
own page works out "today" in that zone (checked 2026-11-19, with a browser
clock set to Tokyo). It is the server's clock, never the player's.
"""

import hashlib
import json
import os
import re
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta

import numpy as np
import pytz
from flask import Blueprint, jsonify, redirect, render_template, request

import crowd
from data import data_folder
from extensions import (INTERACTIVE_LIMITS, auth, db_cursor, limiter, log_page_visit, log_solve,
                        pst_now_str)
from functions import circuits as cz
from functions.phrase_solver import is_blocked, load_blocklist
from helpers import ISO_DATE as _DATE
from helpers import ValidationError, json_object, make_schema_data, parse_iso_day, release_memory
from monitoring import alerts

bp = Blueprint('circuits', __name__)

PAGE = 'circuits.html'
# A box's word, as the page sends a kept one or the admin an answer.
_WORD = re.compile(r'[A-Za-z]{1,%d}' % cz.MAX_CELL_LEN)
# One side of an added pair: a word, or a tile's whole text (as cz.clean_cell
# allows, in lower case).
_PAIR_SIDE = re.compile(r"[a-z](?:[a-z '\-]{0,%d}[a-z])?" % (cz.MAX_CELL_LEN - 2))
# A box's cell as a JSON object's key: plain ASCII digits (str.isdigit would
# also pass '²', which int() then refuses).
_BOX_KEY = re.compile(r'[0-9]{1,3}')

# Puzzmo's calendar (see the module docstring).
GAME_TZ = pytz.timezone('America/Chicago')


def _game_today():
    """Today's date in Puzzmo's calendar. A name of this module's own, so
    tests can set the day."""
    return datetime.now(GAME_TZ).date()


##### solver data #####

# A module memo, not SimpleCache: SimpleCache pickles, so every get would
# copy ~16 MB of arrays (see routes/chainle.py).
_static = {'data': None, 'failed_at': 0.0, 'version': None}
_load_lock = threading.Lock()
# After a failed load, wait this long before trying again, so a broken file
# costs one attempt per window rather than one per request.
LOAD_RETRY_SECONDS = 300


def get_data():
    """The shipped word data, read once per process (~0.1 s, ~18 MB). None
    if it won't load; the page then says the solver is unavailable."""
    if _static['data'] is not None:
        return _static['data']
    with _load_lock:
        if _static['data'] is None and time.time() - _static['failed_at'] > LOAD_RETRY_SECONDS:
            try:
                _static['data'] = cz.Data.from_file(os.path.join(data_folder, 'circuits_data.npz'))
                # Decompressing the file goes through buffers glibc would
                # otherwise keep.
                release_memory()
            except Exception as e:
                _static['failed_at'] = time.time()
                alerts.alert_throttled('circuits_data_failed', sev='crit', exc=type(e).__name__, msg=e)
    return _static['data']


def solver_version():
    """A short code for the solver in this process: a hash of the engine's
    code and its word data, so it changes whenever either does. Stored with
    each logged board, to tell which version made the picks."""
    data = get_data()
    if data is None:
        return None
    if _static.get('version') is None:
        h = hashlib.sha1()
        with open(cz.__file__, 'rb') as fh:
            h.update(fh.read())
        for name in cz.FIELDS:
            a = np.ascontiguousarray(getattr(data, name))
            h.update(f'{name} {a.dtype.str} {a.shape}'.encode())
            h.update(a)
        _static['version'] = h.hexdigest()[:8]
    return _static['version']


def page_context():
    """What the page template needs besides the SEO blob. Rendering the page
    never touches the database."""
    data = get_data()
    return {'data_ok': data is not None, 'rows': cz.ROWS, 'cols': cz.COLS, 'max_side': cz.MAX_SIDE,
            'max_cell': cz.MAX_CELL_LEN, 'box': cz.BOX}


# The offensive-word list (datasets/phrase_blocklist.txt), read once. A board
# with a listed word on a tile is never logged, so it can never be offered.
_blocklist = {'value': None}


def _blocked(text):
    """Is a word, or a run of words, on this tile on the list? If the list
    won't load, every tile counts as listed: nothing is logged then."""
    if _blocklist['value'] is None:
        try:
            _blocklist['value'] = load_blocklist(os.path.join(data_folder, 'phrase_blocklist.txt'))
        except OSError as e:
            alerts.alert_throttled('circuits_blocklist_failed', exc=type(e).__name__, msg=e)
            return True
    key = ' '.join(re.findall(r'[a-z]+', text.lower()))
    return bool(key) and is_blocked(key, _blocklist['value'])


##### James's added pairs #####

# Pairs James adds on /circuits_admin (the compounds his answers needed that
# the data lacks) count as sure ones. Memoized like routes/chainle.py's
# corrections: read every 12 hours, or straight after Add or Remove.
ADDED_WEIGHT = cz.CAP
EXTRA_REFRESH_SECONDS = 12 * 3600
# How long a failed read waits before the next try.
READ_RETRY_SECONDS = 300
_extra = {'value': None, 'expires': 0.0, 'gen': 0}
_extra_lock = threading.Lock()


def _forget_extra():
    """After Add or Remove: the next solve reads the pairs again. A read
    already under way may predate the change, so it isn't kept (gen)."""
    with _extra_lock:
        _extra['gen'] += 1
        _extra['expires'] = 0.0


def added_pairs():
    """Every pair James added: (first word, second word, added_at), newest
    first."""
    return _fetch("SELECT first_word, second_word, added_at FROM circuits_pairs ORDER BY added_at DESC", ())


def get_extra(data):
    """{(first, second): weight} for the solver, each side a word id, or the
    text of a tile the data doesn't know (cz._Pairs). The pairs as last read
    if the table can't be read now (none, before the first good read)."""
    now = time.monotonic()
    if _extra['value'] is not None and now < _extra['expires']:
        return _extra['value']
    gen = _extra['gen']
    try:
        rows = added_pairs()
    except Exception as e:
        alerts.alert_throttled('circuits_pairs_read_failed', exc=type(e).__name__, msg=e)
        with _extra_lock:
            if _extra['value'] is None:
                _extra['value'] = {}
            if _extra['gen'] == gen:
                _extra['expires'] = now + READ_RETRY_SECONDS
            return _extra['value']
    value = {}
    for first, second, _when in rows:
        a, b = data.id(first), data.id(second)
        if a >= 0 or b >= 0:
            value[(a if a >= 0 else first, b if b >= 0 else second)] = ADDED_WEIGHT
    with _extra_lock:
        # An Add or Remove while this read ran: this solve uses it, but it
        # isn't kept, so the next one reads again.
        if _extra['gen'] == gen:
            _extra['value'] = value
            _extra['expires'] = now + EXTRA_REFRESH_SECONDS
    return value


##### boards: shape and storage #####

# When players' boards are served (copied from routes/ribbit.py, where the
# reasoning is): two identical boards, or three or more with each arrow kept
# if more than half drew it. Players are never told these numbers.
SERVE_IDENTICAL = 2
SERVE_MAJORITY = 3
# The most boards one day offers: approvals first, then the players' boards.
TODAYS_BOARDS_MAX = 3
# Boards one player can log on one day: a few typo fixes, with room to spare.
BOARDS_PER_PLAYER_PER_DAY = 10
# 'typed' = entered by the player; 'edited' = loaded from today's boards,
# then changed. Only these count toward a board. 'loaded' = loaded and left
# as it is: logged only to keep the words the player has (for James), and it
# never counts, so an offered board can't confirm itself.
BOARD_SOURCES = ('typed', 'edited')
LOG_SOURCES = BOARD_SOURCES + ('loaded',)
# Live on /circuits from launch (the plan James approved, 2026-11-19). False
# keeps logging boards but offers none (except on /circuits_admin/board).
CIRCUITS_BOARDS_LIVE = True
# An approval is a row of its own, with this as its source and APPROVAL:site
# in place of a player hash (a real one is 32 hex characters), so each site
# can approve a board once a day. Never a player.
APPROVAL = 'admin'
# The sites that run their own daily Circuits board, and their names.
SITE_NAMES = {'puzzmo': 'Puzzmo', 'yahoo': 'Yahoo'}
BOARD_SITES = tuple(SITE_NAMES)
# How long a day's boards (and the answers) are reused before they are read
# again. One gunicorn worker, so these memos are the only copies.
BOARDS_CACHE_SECONDS = 60

# The columns every read of circuits_boards returns, in this order.
_COLUMNS = ('board_key, cells, arrows, site, answers, entered, solver_top, source, voter_hash, '
            'played_on, updated_at, prefix_hash, shape')
(KEY, CELLS, ARROWS, SITE, ANSWERS, ENTERED, TOP, SOURCE, VOTER, PLAYED_ON, UPDATED,
 PREFIX, SHAPE) = range(13)


def board_key(cells, rows=cz.ROWS, cols=cz.COLS):
    """What makes two boards the same: the squares they use, exactly (where
    the boxes and tiles are, tiles as typed, upper case), wherever in the
    grid they were typed: the key is of the board cz.trim makes of them. One
    that fits in 5 by 3 is keyed by its cells alone, as boards always were;
    any other size's key starts with its size. Arrows are decided arrow by
    arrow."""
    board = cz.trim(cz.Board(list(cells), [], rows, cols))[0]
    text = '|'.join(board.cells)
    if (board.rows, board.cols) != (cz.ROWS, cz.COLS):
        text = cz.shape_name(board.rows, board.cols) + '|' + text
    return hashlib.sha1(text.encode()).hexdigest()


def key_of(board):
    return board_key(board.cells, board.rows, board.cols)


def _shape(row):
    """A stored board's (rows, cols)."""
    return cz.parse_shape(row[SHAPE]) or (cz.ROWS, cz.COLS)


def _split(text):
    """A stored '|'-joined list (cells, or a word per box)."""
    return (text or '').split('|')


def _arrows_text(arrows):
    return ','.join(f'{a}-{b}' for a, b in sorted(arrows))


def _parse_arrows(text):
    return tuple(sorted(tuple(int(x) for x in pair.split('-'))
                        for pair in (text or '').split(',') if pair))


def _complete(cells, arrows):
    """What a finished board looks like: a box, and every tile on an arrow."""
    touched = {c for pair in arrows for c in pair}
    return cz.BOX in cells and all(i in touched for i, c in enumerate(cells) if c)


def _boxes(cells):
    return [i for i, c in enumerate(cells) if c == cz.BOX]


def _fetch(sql, params):
    with db_cursor() as (conn, cursor):
        cursor.execute(sql, params)
        return cursor.fetchall()


def rows_with_key(key):
    """Every row for one board, any date."""
    return _fetch(f"SELECT {_COLUMNS} FROM circuits_boards WHERE board_key = %s", (key,))


def rows_logged_between(first, last):
    """Every row, any date, for each board logged between these days."""
    return _fetch(
        f"SELECT {_COLUMNS} FROM circuits_boards WHERE board_key IN "
        "(SELECT board_key FROM circuits_boards WHERE played_on BETWEEN %s AND %s)",
        (first, last))


def store_board(day, voter, prefix, key, cells, arrows, source, entered, top, version, shape='5x3'):
    """Log one player's board, replacing their earlier version of it that
    day. Inline, not on the write-behind queue: a missing table there would
    drop the rest of its batch, page visits included. Best-effort."""
    now = pst_now_str()
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute(
                "SELECT COUNT(*) FROM circuits_boards "
                "WHERE voter_hash = %s AND played_on = %s AND board_key <> %s",
                (voter, day, key))
            if cursor.fetchone()[0] >= BOARDS_PER_PLAYER_PER_DAY:
                return
            cursor.execute(
                """INSERT INTO circuits_boards (played_on, voter_hash, prefix_hash, board_key, shape,
                       cells, arrows, site, entered, solver_top, solver_version, source, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, '', %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE arrows = new.arrows, entered = new.entered,
                       solver_top = new.solver_top, solver_version = new.solver_version,
                       source = new.source, prefix_hash = new.prefix_hash, updated_at = new.updated_at""",
                (day, voter, prefix, key, shape, cells, _arrows_text(arrows), entered, top, version,
                 source, now, now))
            conn.commit()
    except Exception as e:
        alerts.alert_throttled('circuits_board_log_failed', exc=type(e).__name__, msg=e)


def store_approval(day, site, key, cells, arrows, answers=None, shape='5x3'):
    """Approve this board for `site` on `day`. A site has one board a day, so
    this takes the place of that site's approval of any other board that day
    (players' rows and other sites' approvals stay), and of its approval of
    this board that day; answers, if given, go in too (else the ones it had
    stay). Both writes happen or neither. Returns how many other boards'
    approvals it replaced. Raises, unlike store_board: the admin page says
    whether it worked."""
    now = pst_now_str()
    voter = f'{APPROVAL}:{site}'
    with db_cursor() as (conn, cursor):
        # The Heroku pool runs with autocommit on (config.py), so the two
        # writes need a transaction of their own to be one change.
        cursor.execute("START TRANSACTION")
        try:
            cursor.execute("DELETE FROM circuits_boards WHERE voter_hash = %s AND played_on = %s AND board_key <> %s",
                           (voter, day, key))
            replaced = cursor.rowcount
            _insert_approval(cursor, day, voter, site, key, cells, arrows, answers, shape, now)
            conn.commit()
        except Exception:
            try:
                cursor.execute("ROLLBACK")
            except Exception:
                pass
            raise
    return replaced


def _insert_approval(cursor, day, voter, site, key, cells, arrows, answers, shape, now):
    cursor.execute(
        """INSERT INTO circuits_boards (played_on, voter_hash, board_key, shape, cells, arrows,
               site, answers, source, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) AS new
           ON DUPLICATE KEY UPDATE arrows = new.arrows,
               answers = COALESCE(new.answers, circuits_boards.answers), updated_at = new.updated_at""",
        (day, voter, key, shape, cells, _arrows_text(arrows), site, answers, APPROVAL, now, now))


def save_answers(key, answers):
    """Store James's answers on every approval of this board. Returns how
    many approvals took them (0: the board isn't approved)."""
    with db_cursor() as (conn, cursor):
        cursor.execute("UPDATE circuits_boards SET answers = %s WHERE board_key = %s AND source = %s",
                       (answers, key, APPROVAL))
        changed = cursor.rowcount
        conn.commit()
    return changed


def remove_approval(key, site):
    """Take back `site`'s approvals of this board, any day; players' boards
    stay. Returns how many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM circuits_boards WHERE board_key = %s AND voter_hash = %s",
                       (key, f'{APPROVAL}:{site}'))
        removed = cursor.rowcount
        conn.commit()
    return removed


def remove_board(key, day):
    """Delete every entry of this board on `day`, approvals too: test boards,
    or junk. Returns how many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM circuits_boards WHERE board_key = %s AND played_on = %s", (key, day))
        removed = cursor.rowcount
        conn.commit()
    return removed


def store_pair(first, second):
    with db_cursor() as (conn, cursor):
        cursor.execute(
            """INSERT INTO circuits_pairs (first_word, second_word, added_at) VALUES (%s, %s, %s)
               ON DUPLICATE KEY UPDATE added_at = added_at""",
            (first, second, pst_now_str()))
        conn.commit()


def delete_pair(first, second):
    with db_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM circuits_pairs WHERE first_word = %s AND second_word = %s",
                       (first, second))
        removed = cursor.rowcount
        conn.commit()
    return removed


##### boards: what they add up to #####

def consensus(rows):
    """What the rows for one board add up to.

    rows -- circuits_boards rows (see _COLUMNS) sharing one board_key, from
            any dates. Each player's latest board counts, and players on one
            network (crowd.prefix_hash) count once (crowd.one_per_network).

    Returns a dict: cells, rows and cols (the board's shape), players
    (finished boards, one per network), half_drawn,
    agreed / tied (arrows more than half / exactly half of them drew),
    arrows (to serve, or None), why (None when served, else what it's
    waiting for), first_seen, last_update; approved, approved_at and sites
    (James's approvals; their arrows are served, whatever the players drew),
    answers (his, or None); latest_at, latest_finished and latest_arrows (the
    most recent player board, the one Add copies).
    """
    approvals = [row for row in rows if row[SOURCE] == APPROVAL]
    player_rows = [row for row in rows if row[SOURCE] in BOARD_SOURCES]
    cells = _split(rows[0][CELLS])
    n_rows, n_cols = _shape(rows[0])
    networks = crowd.one_per_network(player_rows, VOTER, PREFIX, UPDATED)
    boards = [(row, _parse_arrows(row[ARROWS])) for row in networks.values()]
    finished = [(row, arrows) for row, arrows in boards if _complete(cells, arrows)]
    players = len(finished)

    votes = Counter(pair for _row, arrows in finished for pair in arrows)
    agreed = tuple(sorted(pair for pair, n in votes.items() if 2 * n > players))
    tied = sum(1 for n in votes.values() if 2 * n == players)

    approval = max(approvals, key=lambda row: row[UPDATED]) if approvals else None
    arrows, why = None, None
    if approval is not None:
        arrows = _parse_arrows(approval[ARROWS])
    elif players >= SERVE_MAJORITY:
        if _complete(cells, agreed):
            arrows = agreed
        else:
            why = 'arrows disagree, needs another board'
    elif players == SERVE_IDENTICAL:
        if finished[0][1] == finished[1][1]:
            arrows = finished[0][1]
        else:
            why = 'arrows differ, needs a 3rd board'
    elif players == 1:
        why = 'needs 1 more matching board'
    else:
        why = 'no finished board yet'

    answered = [row for row in approvals if row[ANSWERS] is not None]
    newest = max(player_rows, key=lambda row: row[UPDATED]) if player_rows else None
    latest_arrows = _parse_arrows(newest[ARROWS]) if newest is not None else ()
    return {
        'cells': cells,
        'rows': n_rows,
        'cols': n_cols,
        'players': players,
        'half_drawn': len(boards) - players,
        'agreed': len(agreed),
        'tied': tied,
        'arrows': arrows,
        'why': why,
        'first_seen': min(row[PLAYED_ON] for row in rows),
        'last_update': max(row[UPDATED] for row in (player_rows or rows)),
        'approved': approval is not None,
        'approved_at': min(row[UPDATED] for row in approvals) if approvals else None,
        'sites': sorted({row[SITE] for row in approvals}),
        'answers': (_split(max(answered, key=lambda row: row[UPDATED])[ANSWERS]) if answered else None),
        'latest_at': newest[UPDATED] if newest is not None else None,
        'latest_finished': newest is not None and _complete(cells, latest_arrows),
        'latest_arrows': latest_arrows,
    }


def todays_boards(rows, day):
    """The boards to offer on `day`, and what the admin tab shows about it.

    rows -- every circuits_boards row (any date) for each board logged on
            `day`.

    James's approvals first, in the order he approved them, then the
    players' settled boards, most players first, at most TODAYS_BOARDS_MAX.
    A board first logged on an earlier day counts only `day`'s own entries
    (as on /ribbit: an archive replay plus entries from weeks ago is not
    today's board). Returns (boards, groups): boards is a list of {'cells',
    'rows', 'cols', 'arrows', 'sites'} for the page; groups is every board logged that day
    with its consensus, its 'key' and 'served', most players first.
    """
    by_key = {}
    for row in rows:
        by_key.setdefault(row[KEY], []).append(row)

    groups = []
    for key, group_rows in by_key.items():
        on_day = [row for row in group_rows if row[PLAYED_ON] == day]
        if not on_day:
            continue
        result = consensus(group_rows)
        if result['first_seen'] < day:
            whole = result
            result = consensus(on_day)
            for field in ('first_seen', 'latest_at', 'latest_finished', 'latest_arrows', 'answers'):
                result[field] = whole[field]
            if result['why']:
                result['why'] = f"first seen {whole['first_seen']:%m-%d}; {result['why']}"
        result['key'] = key
        result['served'] = False
        groups.append(result)
    groups.sort(key=lambda g: (-g['players'], g['key']))

    settled = [g for g in groups if g['arrows'] is not None]
    approved = sorted((g for g in settled if g['approved']), key=lambda g: g['approved_at'])
    offered = approved + [g for g in settled if not g['approved']]
    for g in offered[TODAYS_BOARDS_MAX:]:
        g['why'] = 'past the limit of boards a day'
    boards = []
    for g in offered[:TODAYS_BOARDS_MAX]:
        g['served'] = True
        boards.append({'cells': g['cells'], 'rows': g['rows'], 'cols': g['cols'],
                       'arrows': [list(pair) for pair in g['arrows']],
                       'sites': [SITE_NAMES.get(s, s) for s in g['sites']]})
    return boards, groups


# day -> (expires, boards): what _boards_for last offered, time.monotonic().
_boards_cache = {}
# The answers James entered, {board_key: [word per box]}, as _answers_by_key
# last read them.
_answers = {'value': {}, 'expires': 0.0}
# Bumped by every admin change: a read that started before one is used by
# its own request but not kept, since it may predate the change.
_boards_gen = {'n': 0}
_boards_lock = threading.Lock()


def _forget_boards():
    """After an admin change: the next request reads the boards again."""
    with _boards_lock:
        _boards_gen['n'] += 1
        _boards_cache.clear()
        _answers['expires'] = 0.0


def _boards_for(day):
    """Today's boards for `day`, as the page gets them. From the memo while
    fresh, else read again; never raises. When an entry goes stale, the
    first request pushes it out 15 seconds before reading, and the others
    keep the stale boards meanwhile (routes/chainle.py _sets_for); a failed
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
        alerts.alert_throttled('circuits_board_read_failed', exc=type(e).__name__, msg=e)
        boards = entry[1] if entry is not None else []
        expires = time.monotonic() + READ_RETRY_SECONDS
    with _boards_lock:
        if _boards_gen['n'] == gen:
            _boards_cache[day] = (expires, boards)
        for old in [d for d in list(_boards_cache) if abs((d - day).days) > 2]:
            _boards_cache.pop(old, None)
    return boards


def first_paint_boards():
    """Today's boards as the last lookup in this process found them, fresh
    or not, for the page to draw its offer as it arrives (an offer that came
    later would push the board down, a layout shift); None before any lookup
    of today's. Never reads the database: the page still asks for the newest."""
    entry = _boards_cache.get(_game_today())
    return entry[1] if entry is not None else None


def _answers_by_key():
    """{board_key: [answer per box, '' where none]} for every approved board
    James gave answers, the latest per board. Read at most once a minute
    (the same 15-second push as _boards_for); never raises."""
    now = time.monotonic()
    if now < _answers['expires']:
        return _answers['value']
    _answers['expires'] = now + 15
    gen = _boards_gen['n']
    try:
        rows = _fetch("SELECT board_key, answers, updated_at FROM circuits_boards "
                      "WHERE source = %s AND answers IS NOT NULL", (APPROVAL,))
    except Exception as e:
        alerts.alert_throttled('circuits_board_read_failed', exc=type(e).__name__, msg=e)
        with _boards_lock:
            if _boards_gen['n'] == gen:
                _answers['expires'] = time.monotonic() + READ_RETRY_SECONDS
        return _answers['value']
    value = {}
    for key, text, _when in sorted(rows, key=lambda row: row[2]):
        value[key] = _split(text)
    with _boards_lock:
        if _boards_gen['n'] == gen:
            _answers['value'] = value
            _answers['expires'] = time.monotonic() + BOARDS_CACHE_SECONDS
    return value


##### the page's requests #####

def _board_from(data):
    """A board from a POST body's cells, arrows, rows and cols (5 by 3 when
    left out), or ValidationError."""
    rows, cols = data.get('rows', cz.ROWS), data.get('cols', cz.COLS)
    if not cz.fits(rows, cols):
        raise ValidationError(f'a board is 1 to {cz.MAX_SIDE} rows by 1 to {cz.MAX_SIDE} columns')
    try:
        return cz.parse_board(data.get('cells'), data.get('arrows', []), rows, cols)
    except cz.BoardError as e:
        raise ValidationError(str(e))


def _words_by_box(raw, board, field):
    """{box cell: WORD} from a body's {"cell": "word"} object (the words a
    player keeps), or ValidationError."""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > len(board.boxes):
        raise ValidationError(f'{field} must map boxes to words')
    out = {}
    for k, v in raw.items():
        cell = int(k) if isinstance(k, str) and _BOX_KEY.fullmatch(k) else None
        if cell not in board.boxes or not isinstance(v, str) or not _WORD.fullmatch(v.strip()):
            raise ValidationError(f'{field}: one word of letters for a box')
        out[cell] = v.strip().upper()
    return out


def _box_words(board, words):
    """A word per box, '|'-joined in reading order, or None if there are none."""
    if not words:
        return None
    return '|'.join(words.get(c, '') for c in board.boxes)


def solve_request(data, serve_dark=False):
    """The JSON the page renders: the engine's answer (functions/circuits.py
    solve), with James's answers in place of its picks where he has entered
    them ('checked'), and the arrows other players drew for these exact
    cells today if the player's differ ('known_arrows'). `starts` holds the
    first letters a player knows for a box (a Bolt in the game)."""
    board = _board_from(data)
    locked = _words_by_box(data.get('locked'), board, 'locked')
    starts = _words_by_box(data.get('starts'), board, 'starts')
    solver = get_data()
    if solver is None:
        return jsonify(error='The solver is unavailable right now.'), 503
    result = cz.solve(solver, board, locked=locked, extra=get_extra(solver), starts=starts)
    result.pop('solutions', None)
    result['checked'] = _check(result, board)
    result['known_arrows'] = _known_arrows(board) if (serve_dark or CIRCUITS_BOARDS_LIVE) else None
    return jsonify(result)


def _check(result, board):
    """Put James's answers for this board in place of the solver's picks,
    but never over a word the player keeps. True if any went in."""
    answers = _answers_by_key().get(key_of(board))
    if not answers or len(answers) != len(board.boxes):
        return False
    checked = False
    for box, word in zip(result['boxes'], answers):
        if not word or box['locked']:
            continue
        box.update(word=word, confidence='checked', gap=cz.NO_RIVAL_GAP, letters=len(word),
                   alternatives=[])
        for arc in box['arcs']:
            arc['known'] = None
        checked = True
    return checked


def _known_arrows(board):
    """The arrows of an offered board (today's or yesterday's, for a game
    finished after midnight) with exactly these squares, wherever in their
    grid the player typed them, if the player's differ: moved onto the
    player's grid. Else None."""
    day = _game_today()
    mine, top, left = cz.trim(board)
    for d in (day, day - timedelta(days=1)):
        for b in _boards_for(d):
            served = cz.trim(cz.Board(b['cells'], sorted(tuple(p) for p in b['arrows']), b['rows'], b['cols']))[0]
            if served.cells == mine.cells and (served.rows, served.cols) == (mine.rows, mine.cols):
                if served.arrows == mine.arrows:
                    return None
                return cz.place(served.arrows, served.cols, board.cols, top, left)
    return None


def _arrows_to_log(arrows, rows, voter, day, source):
    """The arrows to store for a player's board, or None to store nothing:
    a logged board only ever gains arrows (routes/ribbit.py _links_to_log
    has why). The player's own earlier board of these cells that day keeps
    every arrow it had; a board loaded from today's boards (source
    'edited') that only lost arrows isn't logged, and one that gained some
    is logged as the arrows served for it plus those. A row that only keeps
    the words of a loaded board (source 'loaded') isn't a board of theirs:
    their edit is judged as any edit of an offered board, or a loaded board
    could vote for itself."""
    drawn = set(arrows)
    mine = [row for row in rows
            if row[VOTER] == voter and row[PLAYED_ON] == day and row[SOURCE] not in (APPROVAL, 'loaded')]
    if mine:
        return tuple(sorted(drawn | set(_parse_arrows(mine[0][ARROWS]))))
    if source == 'edited' and rows:
        served = consensus(rows)['arrows']
        if served is not None:
            if drawn <= set(served):
                return None
            return tuple(sorted(drawn | set(served)))
    return tuple(sorted(drawn))


def _solver_top(board):
    """The solver's own picks for a board as it's logged, '|'-joined per
    box: before the player's kept words, Bolt letters or James's answers, so
    the Accuracy tab's When logged column measures the solver alone. None if
    it can't solve now."""
    solver = get_data()
    if solver is None:
        return None
    try:
        result = cz.solve(solver, board, extra=get_extra(solver))
    except Exception:
        return None
    return '|'.join(b['word'] or '' for b in result['boxes'])


def log_board(data):
    """A board a player uses, sent once per version of it. Always the same
    reply, stored or not. Never stored: a board with a box and no arrow on
    some tile (still being typed), or with a listed word on a tile."""
    board = _board_from(data)
    source = data.get('source')
    if source not in LOG_SOURCES:
        raise ValidationError('source must be typed, edited or loaded')
    entered = _words_by_box(data.get('entered'), board, 'entered')
    tiles = [c for c in board.cells if c not in ('', cz.BOX)]
    if not _complete(board.cells, board.arrows) or any(_blocked(t) for t in tiles):
        return jsonify(status='success')
    if source == 'loaded' and not entered:
        return jsonify(status='success')
    day = _game_today()
    # Stored as the squares it uses (cz.trim), so the same board typed at
    # another size or place in the grid is the same row. Its boxes keep their
    # reading order, so the kept words still go box by box.
    trimmed = cz.trim(board)[0]
    key = key_of(trimmed)
    voter = crowd.player_hash('circuits')
    try:
        rows = rows_with_key(key)
    except Exception as e:
        alerts.alert_throttled('circuits_board_log_failed', exc=type(e).__name__, msg=e)
        return jsonify(status='success')
    mine = [row for row in rows
            if row[VOTER] == voter and row[PLAYED_ON] == day and row[SOURCE] != APPROVAL]
    if source == 'loaded':
        # Only the words: a board of theirs already logged keeps its source
        # and arrows, and a new row never counts toward the board.
        if mine:
            source, keep = mine[0][SOURCE], _parse_arrows(mine[0][ARROWS])
        else:
            keep = trimmed.arrows
    else:
        keep = _arrows_to_log(trimmed.arrows, rows, voter, day, source)
        if keep is None:
            return jsonify(status='success')
    store_board(day, voter, crowd.prefix_hash('circuits'), key, '|'.join(trimmed.cells), keep, source,
                _box_words(board, entered), _solver_top(trimmed), solver_version(),
                shape=cz.shape_name(trimmed.rows, trimmed.cols))
    return jsonify(status='success')


def handle_action(data, serve_dark=False, log=True):
    """A JSON body posted with an `action`: a board to log, or today's
    boards. `serve_dark` (only /circuits_admin/board) offers boards even
    while CIRCUITS_BOARDS_LIVE is off; without `log` nothing is stored."""
    action = data.get('action')
    if action == 'log_board':
        return log_board(data) if log else jsonify(status='success')
    if action == 'todays_boards':
        if not (serve_dark or CIRCUITS_BOARDS_LIVE):
            return jsonify(boards=[])
        return jsonify(boards=_boards_for(_game_today()))
    raise ValidationError('unknown action')


@bp.route('/circuits', methods=['GET', 'POST'])
@limiter.limit(INTERACTIVE_LIMITS)
def run_circuits():
    """The page, a solve, or an `action` (a board to log, today's boards)."""
    if request.method == 'POST':
        data = json_object()
        if 'action' in data:
            return handle_action(data)
        response = solve_request(data)
        log_solve(PAGE, data)
        return response
    schema_data = make_schema_data(
        "Circuits Answers & Hints - Today's Puzzmo & Yahoo Circuits Solver",
        "Stuck on Circuits from Puzzmo or Yahoo Games? Enter today's board and get help that fits each box: "
        "hints first, letter by letter, or every answer at once.",
        "https://www.jamesapplewhite.com/circuits"
    )
    log_page_visit(PAGE)
    return render_template(PAGE, schema_data=schema_data, boards_on=CIRCUITS_BOARDS_LIVE,
                           first_boards=first_paint_boards() if CIRCUITS_BOARDS_LIVE else None,
                           post_url='/circuits', approve=False, **page_context())


##### admin #####

ADMIN = '/circuits_admin'
ADMIN_TABS = ('boards', 'accuracy', 'pairs')
# The days the Today's Boards tab shows: the game's today and the two before.
ADMIN_DAYS_BACK = 2
# The days the Accuracy tab looks back over.
ACCURACY_DAYS = 60
# How the tab's Add sends back the time of the board it showed, so the
# approval can check that board is still the latest.
_STAMP = '%Y-%m-%d %H:%M:%S'
# Players (one per network) keeping the same word in a box, to pre-fill it as
# an answer on the admin page.
ENTERED_PREFILL = 2
# The solver's verdicts on approved boards, {(version, key, arrows): [(word,
# confidence)]}: the Accuracy tab re-runs only boards it hasn't seen.
_verdicts = {}
VERDICTS_MAX = 2000


def _back(**message):
    """Back to /circuits_admin as the admin left it, with a message."""
    return redirect(crowd.admin_return_url(ADMIN, request.form, **message))


def _is_key(value):
    return isinstance(value, str) and len(value) == 40 and all(c in '0123456789abcdef' for c in value)


def picture(cells, arrows, rows=cz.ROWS, cols=cz.COLS):
    """The board as the admin's mini grids draw it: its rows of cells with
    the gaps between them, each {'kind': 'tile'/'box'/'blank'/'gap', 'text'},
    a box's text its number and a gap's an arrow (or '')."""
    arrows = set(map(tuple, arrows))
    numbers = {c: n for n, c in enumerate(_boxes(cells), 1)}
    out = []
    for r in range(rows):
        row, below = [], []
        for c in range(cols):
            i = r * cols + c
            kind = 'box' if cells[i] == cz.BOX else ('tile' if cells[i] else 'blank')
            row.append({'kind': kind, 'text': str(numbers[i]) if i in numbers else cells[i]})
            below.append({'kind': 'gap', 'text': '↓' if (i, i + cols) in arrows else ''})
            if c < cols - 1:
                row.append({'kind': 'gap', 'text': '→' if (i, i + 1) in arrows else ''})
                below.append({'kind': 'gap', 'text': ''})
        out.append(row)
        if r < rows - 1:
            out.append(below)
    return out


def _verdict(solver, key, cells, arrows, extra, rows=cz.ROWS, cols=cz.COLS):
    """The solver's (word, confidence) per box for a board."""
    board = cz.Board(list(cells), sorted(arrows), rows, cols)
    result = cz.solve(solver, board, extra=extra)
    return [(b['word'], b['confidence']) for b in result['boxes']]


def _data_alone(solver, key, cells, arrows, rows=cz.ROWS, cols=cz.COLS):
    """_verdict on the shipped data alone (no added pairs), memoized per
    solver version: what the data gets right on its own, the fair measure,
    since added pairs come from these very boards."""
    memo_key = (solver_version(), key, _arrows_text(arrows))
    if memo_key not in _verdicts:
        if len(_verdicts) >= VERDICTS_MAX:
            _verdicts.clear()
        _verdicts[memo_key] = _verdict(solver, key, cells, arrows, {}, rows, cols)
    return _verdicts[memo_key]


def _entered_consensus(rows, boxes):
    """Per box, the word ENTERED_PREFILL or more networks kept (the most
    kept), else ''. Players' words are only ever a pre-fill for James."""
    per_box = [Counter() for _ in boxes]
    networks = crowd.one_per_network([r for r in rows if r[SOURCE] in LOG_SOURCES], VOTER, PREFIX, UPDATED)
    for row in networks.values():
        words = _split(row[ENTERED]) if row[ENTERED] else []
        for k, word in enumerate(words[:len(boxes)]):
            if word:
                per_box[k][word] += 1
    out = []
    for counts in per_box:
        best = min(counts.items(), key=lambda kv: (-kv[1], kv[0]), default=None)
        out.append(best[0] if best and best[1] >= ENTERED_PREFILL else '')
    return out


def missing_pairs(solver, extra, cells, arrows, answers):
    """For a board with answers: each arrow's pair, with whether the solver
    knows it as it solves (James's added pairs too). [{'first', 'second',
    'label', 'known', 'note'}], one per arrow: first and second are what Add
    stores, an answer or a tile's whole text (cz.tile_word: SHOW AND, never
    just its AND); note says why a pair can't be added."""
    words = dict(zip(_boxes(cells), answers))
    pairs = cz.lookup(solver, extra)
    out = []
    for i, j in sorted(arrows):
        sides, ids = [], []
        for cell, first in ((i, True), (j, False)):
            if cells[cell] == cz.BOX:
                word = (words.get(cell) or '').lower()
                k = solver.id(word) if word else -1
                sides.append(word if k >= 0 else None)
                ids.append(k)
            else:
                sides.append(cz.tile_word(solver, cells[cell]))
                ids.append(pairs.given_id(cells[cell], first)[0])
        a, b = sides
        names = [(words.get(c) or '?').upper() if cells[c] == cz.BOX else cells[c] for c in (i, j)]
        if a is None or b is None:
            unknown = names[0] if a is None else names[1]
            out.append({'first': a, 'second': b, 'label': ' + '.join(names), 'known': False,
                        'note': f"{unknown} isn't in the word data"})
            continue
        known = min(ids) >= 0 and pairs.weight(*ids) is not None
        out.append({'first': a, 'second': b, 'label': ' + '.join(names), 'known': known, 'note': None})
    return out


def _review(solver, extra, g, rows):
    """The answers form for an approved board: per box its number, the
    answer to show (saved, else what players kept, else the solver's pick)
    and the solver's pick now; plus, once answers are saved, its misses and
    the pairs the data lacks."""
    cells, arrows = g['cells'], g['arrows'] or g['latest_arrows']
    boxes = _boxes(cells)
    picks = (_verdict(solver, g['key'], cells, arrows, extra, g['rows'], g['cols'])
             if solver is not None else [(None, None)] * len(boxes))
    saved = g['answers'] if g['answers'] and len(g['answers']) == len(boxes) else None
    kept = _entered_consensus(rows, boxes)
    form = []
    for k, cell in enumerate(boxes):
        pick, conf = picks[k]
        value = saved[k] if saved else (kept[k] or pick or '')
        form.append({'n': k + 1, 'value': value, 'pick': pick, 'confidence': conf,
                     'right': bool(saved and saved[k] and pick == saved[k])})
    review = {'form': form, 'saved': saved is not None, 'misses': [], 'pairs': []}
    if saved and solver is not None:
        review['misses'] = [f for f in form if f['value'] and f['pick'] != f['value']]
        review['pairs'] = [p for p in missing_pairs(solver, extra, cells, arrows, saved) if not p['known']]
    return review


def _admin_days(solver, extra):
    """(days, error): the Today's Boards tab, today first."""
    today = _game_today()
    first = today - timedelta(days=ADMIN_DAYS_BACK)
    try:
        rows = rows_logged_between(first, today)
    except Exception as e:
        print(f"Error loading circuits admin boards: {e}")
        return [], f'{type(e).__name__}: {e}'
    by_key = {}
    for row in rows:
        by_key.setdefault(row[KEY], []).append(row)
    days = []
    day = today
    while day >= first:
        _boards, groups = todays_boards(rows, day)
        for g in groups:
            g['picture'] = picture(g['cells'], g['arrows'] or g['latest_arrows'], g['rows'], g['cols'])
            g['latest_stamp'] = g['latest_at'].strftime(_STAMP) if g['latest_at'] else ''
            g['review'] = _review(solver, extra, g, by_key[g['key']]) if g['approved'] else None
        if groups:
            days.append({'day': day, 'groups': groups})
        day -= timedelta(days=1)
    return days, None


def _accuracy(solver):
    """(boards, totals, error) for the Accuracy tab: every approved board with
    answers from the last ACCURACY_DAYS, newest first. Each board: its day,
    sites and picture; 'alone' (right, total) for the data alone now, with
    its misses, and 'shown' (right, total) for the solver's own picks when it
    was logged (_solver_top; the latest player row that has picks). Only
    approvals for days in the window count. totals add these up, and count
    right / total per confidence label (the data alone)."""
    today = _game_today()
    first = today - timedelta(days=ACCURACY_DAYS)
    try:
        rows = rows_logged_between(first, today)
    except Exception as e:
        print(f"Error loading circuits accuracy: {e}")
        return [], None, f'{type(e).__name__}: {e}'
    by_key = {}
    for row in rows:
        by_key.setdefault(row[KEY], []).append(row)
    boards = []
    totals = {'alone': [0, 0], 'shown': [0, 0], 'labels': {}}
    for key, group in by_key.items():
        approvals = [r for r in group if r[SOURCE] == APPROVAL and r[ANSWERS] is not None
                     and first <= r[PLAYED_ON] <= today]
        if not approvals:
            continue
        approval = max(approvals, key=lambda r: r[UPDATED])
        cells, arrows = _split(approval[CELLS]), _parse_arrows(approval[ARROWS])
        n_rows, n_cols = _shape(approval)
        answers = _split(approval[ANSWERS])
        if len(answers) != len(_boxes(cells)):
            continue
        entry = {'day': min(r[PLAYED_ON] for r in approvals),
                 'sites': [SITE_NAMES.get(r[SITE], r[SITE]) for r in approvals],
                 'picture': picture(cells, arrows, n_rows, n_cols), 'cols': n_cols,
                 'misses': [], 'alone': None, 'shown': None}
        if solver is not None:
            verdict = _data_alone(solver, key, cells, arrows, n_rows, n_cols)
            right = 0
            for n, ((word, conf), answer) in enumerate(zip(verdict, answers), 1):
                if not answer:
                    continue
                hit = word == answer
                right += hit
                label = totals['labels'].setdefault(conf, [0, 0])
                label[0] += hit
                label[1] += 1
                if not hit:
                    entry['misses'].append({'n': n, 'answer': answer, 'word': word, 'confidence': conf})
            total = sum(1 for a in answers if a)
            entry['alone'] = (right, total)
            totals['alone'][0] += right
            totals['alone'][1] += total
        logged = [r for r in group if r[SOURCE] in LOG_SOURCES and r[TOP]]
        if logged:
            top = _split(max(logged, key=lambda r: r[UPDATED])[TOP])
            pairs = [(w, a) for w, a in zip(top, answers) if a]
            entry['shown'] = (sum(w == a for w, a in pairs), len(pairs))
            totals['shown'][0] += entry['shown'][0]
            totals['shown'][1] += entry['shown'][1]
        boards.append(entry)
    boards.sort(key=lambda b: b['day'], reverse=True)
    return boards, totals, None


@bp.route(ADMIN)
@auth.login_required
def circuits_admin():
    """Today's boards to approve and answer, how often the solver is right,
    and the pairs James added."""
    tab = request.args.get('tab', 'boards')
    if tab not in ADMIN_TABS:
        tab = 'boards'
    solver = get_data()
    # Each tab is a page load of its own, and reads only what it shows: the
    # Accuracy tab re-solves every answered board the first time.
    days, load_error = [], None
    accuracy, totals, accuracy_error = [], None, None
    pairs, pairs_error = [], None
    if tab == 'boards':
        extra = get_extra(solver) if solver is not None else {}
        days, load_error = _admin_days(solver, extra)
    elif tab == 'accuracy':
        accuracy, totals, accuracy_error = _accuracy(solver)
    else:
        try:
            pairs = added_pairs()
        except Exception as e:
            print(f"Error loading circuits pairs: {e}")
            pairs_error = f'{type(e).__name__}: {e}'
    return render_template('circuits_admin.html', active_tab=tab, keep={'tab': tab},
                           days=days, load_error=load_error, accuracy=accuracy, totals=totals,
                           accuracy_error=accuracy_error, pairs=pairs, pairs_error=pairs_error,
                           data_ok=solver is not None, boards_live=CIRCUITS_BOARDS_LIVE,
                           game_today=_game_today(), sites=SITE_NAMES,
                           serve_identical=SERVE_IDENTICAL, serve_majority=SERVE_MAJORITY,
                           todays_boards_max=TODAYS_BOARDS_MAX, accuracy_days=ACCURACY_DAYS,
                           entered_prefill=ENTERED_PREFILL,
                           shape_cols=range(1, cz.MAX_SIDE + 1))


def editor_board(key):
    """The board /circuits_admin/board opens with when James clicks one on the
    Today's Boards tab: (board, note), or None if no row has this key. board
    is {'cells', 'rows', 'cols', 'arrows', 'answers'}; its arrows are the ones served (his
    approval, or what the players settled), else the latest player's."""
    rows = rows_with_key(key)
    if not rows:
        return None
    result = consensus(rows)
    if result['approved']:
        arrows, note = result['arrows'], f"your approval from {result['approved_at']:%m-%d %H:%M}"
    elif result['arrows'] is not None:
        arrows, note = result['arrows'], f"the arrows {result['players']} players agreed on"
    elif result['latest_at'] is not None:
        arrows = result['latest_arrows']
        note = f"the latest player's board, from {result['latest_at']:%m-%d %H:%M}"
        if not result['latest_finished']:
            note += ', not finished'
    else:
        arrows, note = (), 'its cells, with no arrows yet'
    return {'cells': result['cells'], 'rows': result['rows'], 'cols': result['cols'],
            'arrows': [list(pair) for pair in arrows], 'answers': result['answers']}, note


@bp.route(ADMIN + '/board', methods=['GET', 'POST'])
@auth.login_required
def circuits_admin_board():
    """James's board entry: /circuits's own editor and solver, with an Approve
    bar (a site and a date, and the answers) in place of logging. Its JSON
    posts are solves and today's boards; it never logs a board, so nothing
    here counts as a player. ?key= (from the Today's Boards tab) starts it
    with that board, and ?day= is the Approve date."""
    if request.method == 'POST':
        data = json_object()
        if 'action' in data:
            if data.get('action') != 'todays_boards':
                raise ValidationError('only todays_boards here')
            return handle_action(data, serve_dark=True, log=False)
        return solve_request(data, serve_dark=True)
    preload, load_error = None, None
    key = request.args.get('key', '')
    if key and not _is_key(key):
        load_error = "Not a board: open one from the Today's Boards tab"
    elif key:
        try:
            found = editor_board(key)
        except Exception as e:
            print(f"Error loading a circuits board for the editor: {e}")
            found, load_error = None, "Couldn't read the boards"
        if found is not None:
            board, note = found
            preload = dict(board, note=note)
        elif load_error is None:
            load_error = 'No board has that key'
    day = parse_iso_day(request.args.get('day', '')) or _game_today()
    return render_template(PAGE, schema_data=None, boards_on=True, post_url=ADMIN + '/board',
                           approve=True, approve_day=day, preload=preload, load_error=load_error,
                           sites=SITE_NAMES, **page_context())


def _form_answers(boxes, values):
    """James's answers from a form, one per box ('' for one he leaves out),
    upper case, '|'-joined, or None if any isn't one word of letters."""
    if len(values) != len(boxes):
        return None
    words = [v.strip().upper() for v in values]
    if not all(w == '' or _WORD.fullmatch(w) for w in words):
        return None
    return '|'.join(words) if any(words) else None


@bp.route(ADMIN + '/approve_board', methods=['POST'])
@auth.login_required
def circuits_approve_board():
    """Add: serve the latest player board with this key, for `site`, straight
    away: for a board James entered himself on /circuits and trusts. `latest`
    is that board's time as the page showed it; if a newer one has come in
    since, nothing is approved, so a stranger's board can't take its place."""
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
        arrows = _parse_arrows(newest[ARROWS])
        if not _complete(_split(newest[CELLS]), arrows):
            return _back(error="The latest board isn't finished: every tile needs an arrow")
        replaced = store_approval(day, site, key, newest[CELLS], arrows, shape=cz.shape_name(*_shape(newest)))
    except Exception as e:
        print(f"Error approving circuits board: {e}")
        return _back(error='Database error')
    _forget_boards()
    return _back(success=f"Approved for {SITE_NAMES[site]}: the board from "
                         f"{newest[UPDATED]:%m-%d %H:%M} is served now{_in_place(replaced, site)}. "
                         "Enter its answers below.")


def _in_place(replaced, site):
    return f", in place of your earlier {SITE_NAMES[site]} board that day" if replaced else ""


@bp.route(ADMIN + '/approve_typed_board', methods=['POST'])
@auth.login_required
def circuits_approve_typed_board():
    """Serve a board James entered on /circuits_admin/board, with any answers
    he typed, for `site` on `played_on`, straight away. `cells` is the
    cells as JSON, `shape` as cz.shape_name writes it, `arrows` as
    _arrows_text writes them, `answers` a JSON list of a word (or '') per
    box."""
    day = parse_iso_day(request.form.get('played_on', ''))
    site = request.form.get('site', '')
    try:
        cells = json.loads(request.form.get('cells', ''))
        n_rows, n_cols = cz.parse_shape(request.form.get('shape', '5x3')) or (0, 0)
        board = cz.parse_board(cells, [list(p) for p in _parse_arrows(request.form.get('arrows', ''))],
                               n_rows, n_cols)
        answers = json.loads(request.form.get('answers') or '[]')
    except (ValueError, TypeError, cz.BoardError):
        return _back(error='Not a board: check the tiles and arrows, then approve again')
    if day is None or site not in BOARD_SITES:
        return _back(error='Pick a date and a site')
    if not _complete(board.cells, board.arrows):
        return _back(error='Every tile needs an arrow: draw the rest, then approve again')
    if not isinstance(answers, list) or not all(isinstance(a, str) for a in answers):
        return _back(error='Not a list of answers')
    answer_text = _form_answers(board.boxes, answers) if answers else None
    if answers and answer_text is None and any(a.strip() for a in answers):
        return _back(error='Each answer is one word of letters')
    trimmed = cz.trim(board)[0]
    key = key_of(trimmed)
    try:
        replaced = store_approval(day, site, key, '|'.join(trimmed.cells), trimmed.arrows, answer_text,
                                  shape=cz.shape_name(trimmed.rows, trimmed.cols))
    except Exception as e:
        print(f"Error approving typed circuits board: {e}")
        return _back(error='Database error')
    _forget_boards()
    return _back(success=f"Approved your board for {SITE_NAMES[site]} on {day:%m-%d}: "
                         f"it's served now{_in_place(replaced, site)}")


@bp.route(ADMIN + '/save_answers', methods=['POST'])
@auth.login_required
def circuits_save_answers():
    """James's answers for an approved board, one per box: a solve of it then
    shows them as checked, and the Accuracy tab scores the solver on them."""
    key = request.form.get('key', '')
    if not _is_key(key):
        return _back(error='Not a board')
    try:
        rows = rows_with_key(key)
    except Exception as e:
        print(f"Error reading circuits board: {e}")
        return _back(error='Database error')
    if not rows:
        return _back(error='No board has that key')
    answers = _form_answers(_boxes(_split(rows[0][CELLS])), request.form.getlist('answer'))
    if answers is None:
        return _back(error='Each answer is one word of letters, and at least one is needed')
    try:
        changed = save_answers(key, answers)
    except Exception as e:
        print(f"Error saving circuits answers: {e}")
        return _back(error='Database error')
    if not changed:
        return _back(error='Approve the board first: answers go with an approval')
    _forget_boards()
    return _back(success='Answers saved: players see them as checked now')


@bp.route(ADMIN + '/unapprove_board', methods=['POST'])
@auth.login_required
def circuits_unapprove_board():
    """Undo one site's approval of a board, every day's: the players' boards
    decide again, and none of them is touched."""
    key = request.form.get('key', '')
    site = request.form.get('site', '')
    if not _is_key(key) or site not in BOARD_SITES:
        return _back(error='Not a board')
    try:
        removed = remove_approval(key, site)
    except Exception as e:
        print(f"Error undoing circuits approval: {e}")
        return _back(error='Database error')
    if not removed:
        return _back(error=f'That board was not approved for {SITE_NAMES[site]}')
    _forget_boards()
    return _back(success=f"{SITE_NAMES[site]} approval undone: the players' boards decide again")


@bp.route(ADMIN + '/remove_board', methods=['POST'])
@auth.login_required
def circuits_remove_board():
    """Delete one day's entries of a board, approvals too: test boards, or junk."""
    key = request.form.get('key', '')
    played_on = request.form.get('played_on', '')
    if not (_is_key(key) and _DATE.fullmatch(played_on)):
        return _back(error='Not a board')
    try:
        removed = remove_board(key, date.fromisoformat(played_on))
    except Exception as e:
        print(f"Error removing circuits board: {e}")
        return _back(error='Database error')
    _forget_boards()
    return _back(success=f"Removed {removed} entr{'y' if removed == 1 else 'ies'} of that board on {played_on}")


def _pair_from_form():
    """(first, second) from a form: each a word or a tile's text, lower
    case, or None."""
    first = ' '.join(request.form.get('first', '').lower().split())
    second = ' '.join(request.form.get('second', '').lower().split())
    if all(_PAIR_SIDE.fullmatch(side) and len(side) <= cz.MAX_CELL_LEN for side in (first, second)):
        return first, second
    return None


@bp.route(ADMIN + '/add_pair', methods=['POST'])
@auth.login_required
def circuits_add_pair():
    """Teach the solver a compound it lacks: first + second (first written
    first), as sure as any pair it knows. A side can be a tile's whole text
    the word data lacks (SHOW AND): the solver then knows that tile by it,
    but never offers it as an answer, so the other side must be a word it
    has."""
    pair = _pair_from_form()
    if pair is None:
        return _back(error="A pair is two words, or a word and a tile's text, in letters")
    solver = get_data()
    if solver is not None and all(solver.id(side) < 0 for side in pair):
        return _back(error=f"Neither {pair[0].upper()} nor {pair[1].upper()} is in the word data, "
                           "so the solver can't use the pair")
    try:
        store_pair(*pair)
    except Exception as e:
        print(f"Error adding circuits pair: {e}")
        return _back(error='Database error')
    _forget_extra()
    return _back(success=f"Added {pair[0].upper()} + {pair[1].upper()}: the solver uses it now")


@bp.route(ADMIN + '/remove_pair', methods=['POST'])
@auth.login_required
def circuits_remove_pair():
    """Take back a pair James added."""
    pair = _pair_from_form()
    if pair is None:
        return _back(error='Not a pair')
    try:
        removed = delete_pair(*pair)
    except Exception as e:
        print(f"Error removing circuits pair: {e}")
        return _back(error='Database error')
    _forget_extra()
    if not removed:
        return _back(error='That pair was not added')
    return _back(success=f"Removed {pair[0].upper()} + {pair[1].upper()}")
