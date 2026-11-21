"""4x3: the /4x3 page and its solves, today's boards, players' marks, and
/4x3_admin.

The solver (functions/four_by_three.py) works on data the app already holds
for two other pages: Chainle's word vectors and WordNet lists
(routes/chainle.py get_data) and Circuits' compound pairs
(routes/circuits.py get_data), both loaded once per process and shared.
Nothing is fetched at runtime: the solver never contacts the game.

A new 4x3 puzzle comes out at each player's own midnight: the game's page
builds today's date from the device's clock and asks for that date's puzzle
(read from its page code, 2026-11-20). So, as on /smush, every board is
logged and served by the player's own date (`played_on`), checked to be
within a day of Pacific's (helpers.parse_played_on). There is no game day
on the server.

Today's boards. The first players each day type the nine words for the
players after them, as on /typeshift (whose rules these copy). A board is
its nine words in any order, since the game shuffles them for each player,
and it is served in alphabetical order, never in a player's layout or
James's: a finished game's layout gives away the answer.

Answers. James approves a day's board on /4x3_admin/board, with its hub, its
four groups and, if he likes, their names: every player of that board sees
them as checked. Players mark what the game said about their guesses (right,
one away, not even close), and a group enough players marked right (one per
network) is served to later players of the board as confirmed by players;
one enough marked as a miss is never suggested. Players are never told how
many it takes.

Tuning. Each of James's answers is also an example for the solver: of its
board's 945 answers, his is the right one. From MIN_TUNING_BOARDS of them,
the weights are fit to make his answers most likely (four_by_three.fit) and
refit after every change, with no deploy. Players' marks never tune.
"""

import hashlib
import json
import threading
import time
from collections import defaultdict
from datetime import date, timedelta

from flask import Blueprint, jsonify, redirect, render_template, request

import crowd
from extensions import (INTERACTIVE_LIMITS, auth, db_cursor, limiter, log_page_visit, log_solve,
                        pst_now_str)
from functions import four_by_three as fbt
# _pacific_today is a name of this module's own so tests can set the day.
from helpers import ISO_DATE as _DATE
from helpers import ValidationError, json_object, make_schema_data, parse_iso_day, parse_played_on
from helpers import pacific_today as _pacific_today
from helpers import release_memory
from monitoring import alerts

bp = Blueprint('four_by_three', __name__)

PAGE = 'four_by_three.html'
PATH = '/4x3'


##### the word data #####

def get_words():
    """Chainle's word data (functions/chainle.py Data), loaded once per
    process and shared with /chainle; None if it won't load."""
    from routes import chainle
    return chainle.get_data()


def get_pairs():
    """Circuits' compound pairs (functions/circuits.py Data), shared with
    /circuits; None if they won't load (the solver then goes without hidden
    partners)."""
    from routes import circuits
    return circuits.get_data()


def page_context():
    """What the page template needs besides the SEO blob. Rendering the page
    never touches the database."""
    return {'data_ok': get_words() is not None, 'max_tile': fbt.MAX_TILE_LEN, 'max_marks': MAX_MARKS,
            'name_max': NAME_MAX}


##### tuning #####

# The newest approvals with answers the weights are fit on.
TUNING_WINDOW = 120
# Below this many, the solver keeps four_by_three.DEFAULT_WEIGHTS.
MIN_TUNING_BOARDS = 10
# Read the approvals again this often anyway (staging and local runs write
# the same table, and this process wouldn't hear of them).
TUNING_REFRESH_SECONDS = 6 * 3600
# False keeps the default weights whatever James has approved.
AUTO_TUNE = True
READ_RETRY_SECONDS = 300

# The weights in use: (weights, answers fit on), with when to look again. A
# module memo; one gunicorn worker, so the only copy.
_tuning = {'value': None, 'expires': 0.0}
_tuning_gen = {'n': 0}
_tuning_lock = threading.Lock()


def _forget_tuning():
    """After James approves or undoes an answer: the next solve refits."""
    with _tuning_lock:
        _tuning_gen['n'] += 1
        _tuning['expires'] = 0.0


def _example(row, words, pairs):
    """(answer features, index of James's answer) for one approval, or None
    if its answer doesn't fit its board."""
    try:
        tiles = fbt.parse_board(_split(row[WORDS]))
        answer = json.loads(row[ANSWERS])
        at = {t: i for i, t in enumerate(tiles)}
        right = fbt.answer_index(at[answer['hub']], [(at[a], at[b]) for a, b in answer['pairs']])
    except (fbt.BoardError, ValueError, TypeError, KeyError):
        return None
    if right is None:
        return None
    feats = fbt.Signals.build(tiles, words, pairs).answer_features().astype('float32')
    return feats, right


def _tune():
    """(weights, answers): the weights fit on James's newest answers, or the
    defaults below MIN_TUNING_BOARDS; and how many answers there were."""
    words = get_words()
    if words is None:
        return fbt.DEFAULT_WEIGHTS, 0
    pairs = get_pairs()
    examples, seen = [], set()
    for row in approved_rows(TUNING_WINDOW):
        if row[KEY] in seen:
            continue
        seen.add(row[KEY])
        example = _example(row, words, pairs)
        if example is not None:
            examples.append(example)
    n = len(examples)
    if n < MIN_TUNING_BOARDS:
        return fbt.DEFAULT_WEIGHTS, n
    weights = tuple(float(w) for w in fbt.fit(examples))
    del examples
    release_memory()
    return weights, n


def current_weights():
    """(weights, answers fit on): the solver's weights now. From the memo
    while fresh; when it goes stale the first request refits (well under a
    second) and the others keep the old weights meanwhile, as _boards_for
    does. Never raises: a failed read keeps the last weights, or the
    defaults."""
    if not AUTO_TUNE:
        return fbt.DEFAULT_WEIGHTS, 0
    with _tuning_lock:
        now = time.monotonic()
        entry = _tuning['value']
        if entry is not None and now < _tuning['expires']:
            return entry
        if entry is not None:
            _tuning['expires'] = now + 15
        gen = _tuning_gen['n']
    try:
        value = _tune()
        expires = time.monotonic() + TUNING_REFRESH_SECONDS
    except Exception as e:
        alerts.alert_throttled('four_by_three_tuning_failed', exc=type(e).__name__, msg=e)
        value = entry if entry is not None else (fbt.DEFAULT_WEIGHTS, 0)
        expires = time.monotonic() + READ_RETRY_SECONDS
    with _tuning_lock:
        if _tuning_gen['n'] == gen:
            _tuning['value'] = value
            _tuning['expires'] = expires
    return value


_versions = {}


def solver_version(weights):
    """A short code for the solver as it stands: the engine's code, both
    data files and the weights. Stored with each approval, to tell which
    version made its first pass."""
    code = fbt.weights_code(weights)
    if code not in _versions:
        from routes import chainle, circuits
        h = hashlib.sha1()
        with open(fbt.__file__, 'rb') as fh:
            h.update(fh.read())
        h.update(str(chainle.solver_version()).encode())
        h.update(str(circuits.solver_version()).encode())
        h.update(code.encode())
        _versions[code] = h.hexdigest()[:8]
    return _versions[code]


##### the page's requests #####

# Marks one board can carry: a game takes at most 3 misses and 4 groups, so
# this leaves room for a few undone and redone.
MAX_MARKS = 12
# What a mark is called in a request, and in the table (marks column).
_VERDICT_CODES = {'right': 'r', 'one_away': 'a', 'not_close': 'n', 'missed': 'm', 'hub': 'h'}
_CODE_VERDICTS = {v: k for k, v in _VERDICT_CODES.items()}
# A category name James types.
NAME_MAX = 60


def _board_from(data, field='tiles'):
    """The board's nine words from a POST body, or ValidationError."""
    try:
        return fbt.parse_board(data.get(field))
    except fbt.BoardError as e:
        raise ValidationError(str(e))


def _marks_from(data, board):
    """[(tiles, verdict)] from a body's marks: each on the board, three
    different words for a group verdict, one for 'hub'. ValidationError if
    not."""
    raw = data.get('marks')
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_MARKS:
        raise ValidationError(f'marks must be a list of at most {MAX_MARKS}')
    on_board = set(board)
    out = []
    for mark in raw:
        if (not isinstance(mark, dict) or not isinstance(mark.get('verdict'), str)
                or mark['verdict'] not in _VERDICT_CODES):
            raise ValidationError('each mark needs tiles and a verdict')
        tiles = mark.get('tiles')
        if not isinstance(tiles, list):
            raise ValidationError('each mark needs tiles and a verdict')
        tiles = tuple(fbt.clean_tile(t) for t in tiles)
        want = 1 if mark['verdict'] == 'hub' else 3
        if len(tiles) != want or len(set(tiles)) != want or not set(tiles) <= on_board:
            raise ValidationError('a mark is three words on the board, or one for the hub')
        out.append((tiles, mark['verdict']))
    return out


def solve_request(data, live):
    """The JSON the page renders: functions/four_by_three.py solve() for the
    board and the player's marks, with what James and other players have
    settled about this board when `live`."""
    board = _board_from(data)
    marks = _marks_from(data, board)
    words = get_words()
    if words is None:
        return jsonify(error='The solver is unavailable right now. Please try again in a few minutes.'), 503
    at = {t: i for i, t in enumerate(board)}
    fixed, refused, names, checked = {}, set(), {}, False
    if live:
        known = answers_for(fbt.board_key(board))
        # A stored answer or mark that names a word not on this board (a
        # damaged row) is left out, never a reason to fail the solve.
        answer = known['checked']
        if answer is not None and all(set(trio) <= set(at) for trio in answer['groups']):
            checked = True
            for trio, name in zip(answer['groups'], answer['names']):
                mask = fbt.mask_of(at[t] for t in trio)
                fixed[mask] = 'checked'
                if name:
                    names[mask] = name
        for trio in known['players']:
            if set(trio) <= set(at):
                fixed.setdefault(fbt.mask_of(at[t] for t in trio), 'players')
        refused = {fbt.mask_of(at[t] for t in trio) for trio in known['refused'] if set(trio) <= set(at)}
    weights, _n = current_weights()
    signals = fbt.Signals.build(board, words, get_pairs())
    result = fbt.solve(signals, weights, [(tuple(at[t] for t in tiles), verdict) for tiles, verdict in marks],
                       fixed=fixed, refused=refused, names=names)
    result['checked'] = checked
    return jsonify(result)


##### boards: shape and storage #####

# When players' boards are served: this many players (one per network)
# entering the same nine words on the same date. Players are never told.
SERVE_NETWORKS = 2
# A date counts toward a board's first_seen only once this many networks
# logged it then, so one device with a wrong clock can't date a board.
ARCHIVE_MIN_NETWORKS = 2
# The most boards one date offers: James's approval first, then the players'.
TODAYS_BOARDS_MAX = 3
# Rows one player can add on one date (boards logged, and boards marked): a
# few typo fixes and archive replays, with room to spare.
BOARDS_PER_PLAYER_PER_DAY = 10
# 'typed' = entered by the player; 'edited' = loaded from today's boards,
# then changed. A board loaded and left as it is is never logged as a board.
BOARD_SOURCES = ('typed', 'edited')
# A loaded board's row carries the player's marks only: it never counts
# toward serving its board.
LOADED = 'loaded'
# A group is confirmed by players, or refused, once this many networks
# marked it so, and more of them did than said the opposite.
CONFIRM_NETWORKS = 3
# Live on /4x3 from launch. False keeps logging boards and marks but offers
# none, and serves no answers (except on /4x3_admin/board).
FOUR_BY_THREE_BOARDS_LIVE = True
# James's approval: a row of its own, with this as its source and its voter
# hash (a real one is 32 hex characters). One a date. Never a player.
APPROVAL = 'admin'
# How long a date's boards, or a board's answers, are reused before they're
# read again. One gunicorn worker, so these memos are the only copies.
BOARDS_CACHE_SECONDS = 60
ANSWERS_CACHE_SECONDS = 60
ANSWERS_CACHE_MAX = 500

# The columns every read of four_by_three_boards returns, in this order.
_COLUMNS = ('board_key, words, source, voter_hash, played_on, updated_at, prefix_hash, marks, answers, '
            'solver_top')
(KEY, WORDS, SOURCE, VOTER, PLAYED_ON, UPDATED, PREFIX, MARKS, ANSWERS, TOP) = range(10)


def _split(text):
    return (text or '').split('|')


def words_text(board):
    """A board as stored: its nine words as typed, '|'-joined."""
    return '|'.join(board)


def _fetch(sql, params):
    with db_cursor() as (conn, cursor):
        cursor.execute(sql, params)
        return cursor.fetchall()


def rows_with_key(key):
    """Every row for one board, any date."""
    return _fetch(f"SELECT {_COLUMNS} FROM four_by_three_boards WHERE board_key = %s", (key,))


def rows_logged_between(first, last):
    """Every row, any date, for each board logged between these days."""
    return _fetch(
        f"SELECT {_COLUMNS} FROM four_by_three_boards WHERE board_key IN "
        "(SELECT board_key FROM four_by_three_boards WHERE played_on BETWEEN %s AND %s)",
        (first, last))


def approved_rows(limit):
    """James's newest approvals that carry an answer, newest first."""
    return _fetch(
        f"SELECT {_COLUMNS} FROM four_by_three_boards WHERE source = %s AND answers IS NOT NULL "
        "ORDER BY updated_at DESC LIMIT %s", (APPROVAL, limit))


def _under_cap(cursor, voter, day, key):
    cursor.execute(
        "SELECT COUNT(*) FROM four_by_three_boards WHERE voter_hash = %s AND played_on = %s AND board_key <> %s",
        (voter, day, key))
    return cursor.fetchone()[0] < BOARDS_PER_PLAYER_PER_DAY


def store_board(day, voter, prefix, key, words, source):
    """Log one player's board, replacing their earlier entry of it that
    date (their marks on it stay). Inline, not on the write-behind queue: a
    missing table there would drop the rest of its batch, page visits
    included. Best-effort."""
    now = pst_now_str()
    try:
        with db_cursor() as (conn, cursor):
            if not _under_cap(cursor, voter, day, key):
                return
            cursor.execute(
                """INSERT INTO four_by_three_boards (played_on, voter_hash, prefix_hash, board_key, words,
                       source, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE words = new.words, source = new.source,
                       prefix_hash = new.prefix_hash, updated_at = new.updated_at""",
                (day, voter, prefix, key, words, source, now, now))
            conn.commit()
    except Exception as e:
        alerts.alert_throttled('four_by_three_board_log_failed', exc=type(e).__name__, msg=e)


def store_marks(day, voter, prefix, key, words, marks):
    """Keep one player's marks on a board (a JSON list, see _marks_text),
    in place of their earlier ones. A board they haven't logged gets a
    'loaded' row, which carries marks and never counts toward serving the
    board; a logged one keeps its source. Best-effort."""
    now = pst_now_str()
    try:
        with db_cursor() as (conn, cursor):
            if not _under_cap(cursor, voter, day, key):
                return
            cursor.execute(
                """INSERT INTO four_by_three_boards (played_on, voter_hash, prefix_hash, board_key, words,
                       source, marks, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE marks = new.marks, prefix_hash = new.prefix_hash,
                       updated_at = new.updated_at""",
                (day, voter, prefix, key, words, LOADED, marks, now, now))
            conn.commit()
    except Exception as e:
        alerts.alert_throttled('four_by_three_board_log_failed', exc=type(e).__name__, msg=e)


def store_approval(day, key, words, answers, top, version):
    """Approve this board for players whose date is `day`, with James's
    answer (JSON, or None for the board alone). One board a date: this takes
    the place of his approval of any other board that date. The first pass
    stays from the board's first approval, so re-saving an answer can't
    grade a solver already tuned on it. Both writes happen or neither.
    Returns how many other boards' approvals it replaced. Raises: the admin
    page says whether it worked."""
    now = pst_now_str()
    with db_cursor() as (conn, cursor):
        # The Heroku pool runs with autocommit on (config.py), so the two
        # writes need a transaction of their own to be one change.
        cursor.execute("START TRANSACTION")
        try:
            cursor.execute("DELETE FROM four_by_three_boards WHERE voter_hash = %s AND played_on = %s "
                           "AND board_key <> %s", (APPROVAL, day, key))
            replaced = cursor.rowcount
            cursor.execute(
                """INSERT INTO four_by_three_boards (played_on, voter_hash, board_key, words, source, answers,
                       solver_top, solver_version, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE words = new.words, answers = new.answers,
                       solver_top = COALESCE(four_by_three_boards.solver_top, new.solver_top),
                       solver_version = COALESCE(four_by_three_boards.solver_version, new.solver_version),
                       updated_at = new.updated_at""",
                (day, APPROVAL, key, words, APPROVAL, answers, top, version, now, now))
            conn.commit()
        except Exception:
            try:
                cursor.execute("ROLLBACK")
            except Exception:
                pass
            raise
    return replaced


def remove_approval(key):
    """Take back James's approvals of this board, every date; players' rows
    stay. Returns how many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM four_by_three_boards WHERE board_key = %s AND voter_hash = %s",
                       (key, APPROVAL))
        removed = cursor.rowcount
        conn.commit()
    return removed


def remove_board(key, day):
    """Delete every entry of this board on `day`, approvals too: test boards,
    or junk. Returns how many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM four_by_three_boards WHERE board_key = %s AND played_on = %s", (key, day))
        removed = cursor.rowcount
        conn.commit()
    return removed


##### boards: what they add up to #####

def _networks(rows, sources=BOARD_SOURCES):
    """One row per network (crowd.one_per_network), from rows of `sources`."""
    return crowd.one_per_network([row for row in rows if row[SOURCE] in sources], VOTER, PREFIX, UPDATED)


def consensus(rows):
    """What one board's rows (one date's, or all) add up to: tiles (as
    served: alphabetical), players (networks), settled (an approval, or
    SERVE_NETWORKS players), why (None when settled), approved,
    approved_at, last_update, latest_at."""
    approvals = [row for row in rows if row[SOURCE] == APPROVAL]
    player_rows = [row for row in rows if row[SOURCE] in BOARD_SOURCES]
    players = len(_networks(rows))
    settled = bool(approvals) or players >= SERVE_NETWORKS
    why = None
    if not settled:
        short = SERVE_NETWORKS - players
        why = f'needs {short} more player{"s" if short != 1 else ""}' if player_rows else 'only marks so far'
    newest = max(player_rows, key=lambda row: row[UPDATED]) if player_rows else None
    return {
        'tiles': sorted(_split(rows[0][WORDS])),
        'players': players,
        'settled': settled,
        'why': why,
        'approved': bool(approvals),
        'approved_at': max(row[UPDATED] for row in approvals) if approvals else None,
        'last_update': max(row[UPDATED] for row in rows),
        'latest_at': newest[UPDATED] if newest is not None else None,
    }


def first_seen(rows):
    """The first date this board was a day's puzzle: the earliest James
    approved it for, or that ARCHIVE_MIN_NETWORKS networks entered it on.
    None if neither yet."""
    by_day = defaultdict(list)
    for row in rows:
        by_day[row[PLAYED_ON]].append(row)
    for day in sorted(by_day):
        day_rows = by_day[day]
        if any(row[SOURCE] == APPROVAL for row in day_rows) or len(_networks(day_rows)) >= ARCHIVE_MIN_NETWORKS:
            return day
    return None


def todays_boards(rows, day):
    """The boards to offer players whose date is `day`, and what the admin
    tab shows about that date.

    rows -- every four_by_three_boards row (any date) for each board logged
            on `day`.

    Only `day`'s own entries count: James's approval for that date, and the
    players who entered the board then. A past puzzle (first seen before
    `day`: an archive replay) is offered only if approved for `day`. His
    approval comes first, then the players' boards, most players first, at
    most TODAYS_BOARDS_MAX.

    Returns (boards, groups): boards is a list of {'tiles', 'approved'} for
    the page; groups is every board logged that date with its consensus,
    'key', 'first_seen', 'past' and 'served', most players first.
    """
    by_key = defaultdict(list)
    for row in rows:
        by_key[row[KEY]].append(row)

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
        result['rows'] = group_rows
        groups.append(result)
    groups.sort(key=lambda g: (-g['players'], g['key']))

    ready = [g for g in groups if g['why'] is None]
    offered = [g for g in ready if g['approved']] + [g for g in ready if not g['approved']]
    for g in offered[TODAYS_BOARDS_MAX:]:
        g['why'] = 'past the limit of boards a day'
    boards = []
    for g in offered[:TODAYS_BOARDS_MAX]:
        g['served'] = True
        boards.append({'tiles': g['tiles'], 'approved': g['approved']})
    return boards, groups


# day -> (expires, boards): what _boards_for last offered, time.monotonic().
_boards_cache = {}
# Bumped by every admin change: a read that started before one is used by
# its own request but not kept, since it may predate the change.
_boards_gen = {'n': 0}
_boards_lock = threading.Lock()


def _forget_boards():
    """After an admin change: the next request reads boards and answers
    again."""
    with _boards_lock:
        _boards_gen['n'] += 1
        _boards_cache.clear()
        _answers_cache.clear()


def _boards_for(day):
    """The boards offered for `day`, as the page gets them. From the memo
    while fresh, else read again; never raises. When an entry goes stale,
    the first request pushes it out 15 seconds before reading and the others
    keep the stale boards meanwhile; a failed read keeps them too, and waits
    READ_RETRY_SECONDS to try again (routes/typeshift.py)."""
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
        alerts.alert_throttled('four_by_three_board_read_failed', exc=type(e).__name__, msg=e)
        boards = entry[1] if entry is not None else []
        expires = time.monotonic() + READ_RETRY_SECONDS
    with _boards_lock:
        if _boards_gen['n'] == gen:
            _boards_cache[day] = (expires, boards)
        for old in [d for d in list(_boards_cache) if abs((d - day).days) > 2]:
            _boards_cache.pop(old, None)
    return boards


def _played_on(data):
    """The player's own date, or None if it can't be today anywhere on Earth
    (more than a day from Pacific's): a wrong clock, not a reason to 400."""
    return parse_played_on(data.get('played_on'), _pacific_today())


def todays_reply(data, serve_dark=False):
    """The `todays_boards` action: the boards for the player's date."""
    day = _played_on(data)
    if day is None or not (serve_dark or FOUR_BY_THREE_BOARDS_LIVE):
        return jsonify(boards=[], day=day.isoformat() if day else None)
    return jsonify(boards=_boards_for(day), day=day.isoformat())


def log_board(data):
    """A board a player uses, sent once per version of it. Always the same
    reply, stored or not."""
    board = _board_from(data)
    source = data.get('source')
    if source not in BOARD_SOURCES:
        raise ValidationError('source must be typed or edited')
    day = _played_on(data)
    if day is None:
        return jsonify(status='success')
    if source == 'edited' and sorted(board) in [b['tiles'] for b in _boards_for(day)]:
        # Changed and then changed back: still the board on offer, which
        # can't vote for itself.
        return jsonify(status='success')
    store_board(day, crowd.player_hash('four_by_three'), crowd.prefix_hash('four_by_three'),
                fbt.board_key(board), words_text(board), source)
    return jsonify(status='success')


def _marks_text(marks):
    """Marks as the table keeps them: [[the words '|'-joined, a code], ...]."""
    return json.dumps([['|'.join(tiles), _VERDICT_CODES[verdict]] for tiles, verdict in marks],
                      separators=(',', ':'))


def save_marks(data):
    """A player's marks on a board, all of them, each time they change.
    Always the same reply, stored or not."""
    board = _board_from(data)
    marks = _marks_from(data, board)
    day = _played_on(data)
    if day is not None:
        store_marks(day, crowd.player_hash('four_by_three'), crowd.prefix_hash('four_by_three'),
                    fbt.board_key(board), words_text(board), _marks_text(marks))
    return jsonify(status='success')


##### answers: James's, and what players' marks add up to #####

def _parse_marks(text):
    """[(tiles, verdict)] from a row's marks; [] if it has none or they're
    damaged."""
    try:
        raw = json.loads(text) if text else []
        return [(tuple(tiles.split('|')), _CODE_VERDICTS[code]) for tiles, code in raw]
    except (ValueError, TypeError, KeyError, AttributeError):
        return []


def parse_answer(text):
    """James's answer from an approval: {'hub', 'pairs', 'names', 'groups'
    (each pair with the hub, as sets)}, or None."""
    try:
        answer = json.loads(text) if text else None
        hub, pairs = answer['hub'], [tuple(p) for p in answer['pairs']]
        names = [str(n or '') for n in answer.get('names') or [''] * fbt.GROUPS][:fbt.GROUPS]
    except (ValueError, TypeError, KeyError):
        return None
    if (not isinstance(hub, str) or len(pairs) != fbt.GROUPS
            or any(len(p) != 2 or not all(isinstance(w, str) for w in p) for p in pairs)):
        return None
    names += [''] * (fbt.GROUPS - len(names))
    return {'hub': hub, 'pairs': pairs, 'names': names,
            'groups': [frozenset((hub,) + p) for p in pairs]}


def _consistent(trios):
    """The best-supported trios that could all be groups of one answer, in
    the order given: every two share exactly one word, the same one."""
    chosen = []
    for trio in trios:
        if all(len(trio & c) == 1 for c in chosen) and (not chosen or len(frozenset.intersection(trio, *chosen)) == 1):
            chosen.append(trio)
        if len(chosen) == fbt.GROUPS:
            break
    return chosen


def mark_counts(rows):
    """{trio: [networks that said right, networks that said it missed]} from
    one board's rows: each network's latest row, approvals left out."""
    counts = defaultdict(lambda: [0, 0])
    for row in _networks(rows, BOARD_SOURCES + (LOADED,)).values():
        said = {}
        for tiles, verdict in _parse_marks(row[MARKS]):
            if verdict != 'hub':
                said[frozenset(tiles)] = verdict
        for trio, verdict in said.items():
            counts[trio][0 if verdict == 'right' else 1] += 1
    return counts


def crowd_answers(rows):
    """What one board's rows (any date) settle: {'checked': James's newest
    answer or None, 'players': groups players confirmed (sets of words),
    'refused': groups they say the game refused}."""
    approvals = [row for row in rows if row[SOURCE] == APPROVAL and row[ANSWERS]]
    checked = None
    if approvals:
        checked = parse_answer(max(approvals, key=lambda row: row[UPDATED])[ANSWERS])
    counts = mark_counts(rows)
    confirmed = sorted((t for t, (right, miss) in counts.items() if right >= CONFIRM_NETWORKS and right > miss),
                       key=lambda t: (-(counts[t][0] - counts[t][1]), -counts[t][0], sorted(t)))
    refused = [t for t, (right, miss) in counts.items() if miss >= CONFIRM_NETWORKS and miss > right]
    return {'checked': checked, 'players': _consistent(confirmed), 'refused': refused}


# key -> (expires, answers): what answers_for last read, time.monotonic().
_answers_cache = {}


def answers_for(key):
    """crowd_answers for one board, from the memo while fresh; never raises
    (a failed read is an empty answer, retried after READ_RETRY_SECONDS).
    The entry and the generation are read together, as in _boards_for, so
    an entry an admin change has cleared can't come back as the fallback."""
    with _boards_lock:
        now = time.monotonic()
        entry = _answers_cache.get(key)
        if entry is not None and now < entry[0]:
            return entry[1]
        gen = _boards_gen['n']
    try:
        value = crowd_answers(rows_with_key(key))
        expires = time.monotonic() + ANSWERS_CACHE_SECONDS
    except Exception as e:
        alerts.alert_throttled('four_by_three_board_read_failed', exc=type(e).__name__, msg=e)
        value = entry[1] if entry is not None else {'checked': None, 'players': [], 'refused': []}
        expires = time.monotonic() + READ_RETRY_SECONDS
    with _boards_lock:
        if _boards_gen['n'] == gen:
            if len(_answers_cache) >= ANSWERS_CACHE_MAX:
                # Expired entries go first; if none has, the whole memo.
                expired = [k for k, (until, _v) in list(_answers_cache.items()) if until < now]
                for old in expired or list(_answers_cache):
                    _answers_cache.pop(old, None)
            _answers_cache[key] = (expires, value)
    return value


##### the page #####

def handle_action(data, serve_dark=False):
    """A JSON body posted with an `action`: a board to log, a player's marks,
    or today's boards. `serve_dark` (only /4x3_admin/board) offers boards
    even while FOUR_BY_THREE_BOARDS_LIVE is off."""
    action = data.get('action')
    if action == 'log_board':
        return log_board(data)
    if action == 'marks':
        return save_marks(data)
    if action == 'todays_boards':
        return todays_reply(data, serve_dark)
    raise ValidationError('unknown action')


@bp.route(PATH, methods=['GET', 'POST'])
@limiter.limit(INTERACTIVE_LIMITS)
def run_four_by_three():
    """The page, a solve, or an `action` (a board to log, marks, today's
    boards)."""
    if request.method == 'POST':
        data = json_object()
        if 'action' in data:
            return handle_action(data)
        response = solve_request(data, FOUR_BY_THREE_BOARDS_LIVE)
        log_solve(PAGE, data)
        return response
    schema_data = make_schema_data(
        "4x3 Answers & Hints - Solver for Hank Green's Daily 4x3 Puzzle",
        "Stuck on today's 4x3? Type the nine words and get hints for the hub and the four groups, "
        "or the answer, with how sure the solver is of each.",
        "https://www.jamesapplewhite.com/4x3"
    )
    log_page_visit(PAGE)
    return render_template(PAGE, schema_data=schema_data, boards_on=FOUR_BY_THREE_BOARDS_LIVE,
                           post_url=PATH, approve=False, **page_context())


##### admin #####

ADMIN = '/4x3_admin'
ADMIN_TABS = ('boards', 'accuracy')
# The dates the Boards tab shows: Pacific's last three, and tomorrow for
# players whose date is already ahead.
ADMIN_DAYS_BACK = 2
# The Accuracy tab's window.
ACCURACY_DAYS = 60


def _back(**message):
    """Back to /4x3_admin as the admin left it, with a message."""
    return redirect(crowd.admin_return_url(ADMIN, request.form, **message))


def _is_key(value):
    return isinstance(value, str) and len(value) == 40 and all(c in '0123456789abcdef' for c in value)


def _first_pass(tiles, words, weights):
    """The solver's own answer for a board: four_by_three.first_pass."""
    return fbt.first_pass(fbt.Signals.build(tiles, words, get_pairs()), weights)


def _marks_table(rows):
    """The players' marks on one board, for the admin: [(words, right,
    missed)], most marked first."""
    counts = mark_counts(rows)
    return [(' + '.join(sorted(t)), right, miss)
            for t, (right, miss) in sorted(counts.items(), key=lambda kv: (-sum(kv[1]), sorted(kv[0])))]


def _admin_days():
    """(days, error): the Boards tab, newest date first."""
    today = _pacific_today()
    first, last = today - timedelta(days=ADMIN_DAYS_BACK), today + timedelta(days=1)
    try:
        rows = rows_logged_between(first, last)
    except Exception as e:
        print(f"Error loading 4x3 admin boards: {e}")
        return [], f'{type(e).__name__}: {e}'
    words = get_words()
    weights, _n = current_weights()
    days = []
    day = last
    while day >= first:
        _boards, groups = todays_boards(rows, day)
        for g in groups:
            known = crowd_answers(g['rows'])
            g['answer'] = known['checked']
            g['confirmed'] = [' + '.join(sorted(t)) for t in known['players']]
            g['marks'] = _marks_table(g['rows'])
            g['first_pass'] = None
            if words is not None:
                try:
                    g['first_pass'] = _first_pass(fbt.parse_board(_split(g['rows'][0][WORDS])), words, weights)
                except fbt.BoardError:
                    pass
            del g['rows']
        if groups:
            days.append({'day': day, 'groups': groups})
        day -= timedelta(days=1)
    return days, None


def _accuracy():
    """(boards, totals, error): each board answered for a date in the last
    ACCURACY_DAYS, against the solver's first pass from just before James
    first answered it. Only that approval keeps one, whatever its date
    (four_by_three_approve_typed_board), so it's looked for among all of the
    board's approvals."""
    since = _pacific_today() - timedelta(days=ACCURACY_DAYS)
    try:
        rows = approved_rows(1000)
    except Exception as e:
        print(f"Error loading 4x3 approvals: {e}")
        return [], None, f'{type(e).__name__}: {e}'
    by_key = defaultdict(list)
    for row in rows:
        by_key[row[KEY]].append(row)
    boards = []
    totals = {'hub': [0, 0], 'groups': [0, 0], 'labels': {}}
    for key, key_rows in by_key.items():
        day = max(row[PLAYED_ON] for row in key_rows)
        if day < since:
            continue
        latest = max(key_rows, key=lambda row: row[UPDATED])
        answer = parse_answer(latest[ANSWERS])
        firsts = [row for row in key_rows if row[TOP]]
        if answer is None:
            continue
        entry = {'day': day, 'hub': answer['hub'],
                 'groups': [' + '.join((answer['hub'],) + p) for p in answer['pairs']],
                 'names': answer['names'], 'top': None}
        if firsts:
            try:
                top = json.loads(min(firsts, key=lambda row: row[UPDATED])[TOP])
            except (ValueError, TypeError):
                top = None
            if isinstance(top, dict):
                hub_right = top.get('hub') == answer['hub']
                rights = []
                for a, b, label in top.get('groups') or []:
                    right = hub_right and frozenset((top['hub'], a, b)) in answer['groups']
                    rights.append(right)
                    rec = totals['labels'].setdefault(label, [0, 0])
                    rec[0] += right
                    rec[1] += 1
                totals['hub'][0] += hub_right
                totals['hub'][1] += 1
                totals['groups'][0] += sum(rights)
                totals['groups'][1] += len(rights)
                entry['top'] = {'hub': top.get('hub'), 'hub_label': top.get('hub_label'), 'hub_right': hub_right,
                                'groups': [{'text': ' + '.join((top['hub'], a, b)), 'label': label, 'right': r}
                                           for (a, b, label), r in zip(top.get('groups') or [], rights)]}
        boards.append(entry)
    boards.sort(key=lambda b: b['day'], reverse=True)
    return boards, totals, None


@bp.route(ADMIN)
@auth.login_required
def four_by_three_admin():
    """Recent boards to approve and answer, and how the solver did."""
    tab = request.args.get('tab', 'boards')
    if tab not in ADMIN_TABS:
        tab = 'boards'
    days, load_error = [], None
    accuracy, totals, accuracy_error = [], None, None
    if tab == 'boards':
        days, load_error = _admin_days()
    else:
        accuracy, totals, accuracy_error = _accuracy()
    weights, tuned_on = current_weights()
    return render_template('four_by_three_admin.html', active_tab=tab, keep={'tab': tab},
                           days=days, load_error=load_error, accuracy=accuracy, totals=totals,
                           accuracy_error=accuracy_error, data_ok=get_words() is not None,
                           boards_live=FOUR_BY_THREE_BOARDS_LIVE, pacific_today=_pacific_today(),
                           serve_networks=SERVE_NETWORKS, confirm_networks=CONFIRM_NETWORKS,
                           todays_boards_max=TODAYS_BOARDS_MAX, accuracy_days=ACCURACY_DAYS,
                           weights=list(zip(fbt.FEATURES, weights, fbt.DEFAULT_WEIGHTS)),
                           tuned=weights != tuple(fbt.DEFAULT_WEIGHTS), tuned_on=tuned_on,
                           min_tuning=MIN_TUNING_BOARDS, auto_tune=AUTO_TUNE)


def editor_board(key, day=None):
    """What /4x3_admin/board opens with when James clicks a board on the
    Boards tab, or None if no row has this key: {'tiles', 'hub', 'pairs',
    'names', 'note'}. The tiles as he approved them, else as the latest
    player typed them; the answer his newest, from any date (the one
    players get), else the groups players confirmed filled out by the
    solver, else the solver's. The tiles from `day`'s rows when it has
    any."""
    every = rows_with_key(key)
    rows = every
    if day is not None:
        rows = [row for row in every if row[PLAYED_ON] == day] or every
    if not rows:
        return None
    approvals = [row for row in rows if row[SOURCE] == APPROVAL]
    newest = max(approvals or rows, key=lambda row: row[UPDATED])
    try:
        tiles = fbt.parse_board(_split(newest[WORDS]))
    except fbt.BoardError:
        return None
    known = crowd_answers(every)
    answer = known['checked']
    if answer is not None and set(answer['groups'][0]) <= set(tiles):
        # When that answer was saved, which may be another date's approval.
        saved = max(row[UPDATED] for row in every if row[SOURCE] == APPROVAL and row[ANSWERS])
        return {'tiles': list(tiles), 'hub': answer['hub'], 'pairs': [list(p) for p in answer['pairs']],
                'names': answer['names'], 'note': f"your answer from {saved:%m-%d %H:%M}"}
    preload = {'tiles': list(tiles), 'hub': '', 'pairs': [], 'names': [''] * fbt.GROUPS,
               'note': f"the board from {newest[UPDATED]:%m-%d %H:%M}"}
    words = get_words()
    if words is not None:
        at = {t: i for i, t in enumerate(tiles)}
        fixed = {fbt.mask_of(at[t] for t in trio): 'players' for trio in known['players'] if set(trio) <= set(tiles)}
        weights, _n = current_weights()
        result = fbt.solve(fbt.Signals.build(tiles, words, get_pairs()), weights, fixed=fixed)
        preload['hub'] = result['hub']['tile']
        preload['pairs'] = [g['pair'] for g in result['groups']]
        preload['note'] += (", with the groups players confirmed and the solver's for the rest"
                            if fixed else ", with the solver's groups: check them")
    return preload


@bp.route(ADMIN + '/board', methods=['GET', 'POST'])
@auth.login_required
def four_by_three_admin_board():
    """James's board entry: /4x3's own editor and solver, with his answer
    and an Approve bar in place of logging. Its JSON posts are solves and
    today's boards; it never logs a board or a mark, so nothing here counts
    as a player. ?key= (from the Boards tab) starts it with that board, and
    ?day= is the Approve date."""
    if request.method == 'POST':
        data = json_object()
        if 'action' in data:
            if data.get('action') != 'todays_boards':
                raise ValidationError('only todays_boards here')
            return handle_action(data, serve_dark=True)
        return solve_request(data, True)
    preload, load_error = None, None
    key = request.args.get('key', '')
    day = parse_iso_day(request.args.get('day', ''))
    if key and not _is_key(key):
        load_error = "Not a board: open one from the Boards tab"
    elif key:
        try:
            preload = editor_board(key, day)
        except Exception as e:
            print(f"Error loading a 4x3 board for the editor: {e}")
            load_error = "Couldn't read the boards"
        if preload is None and load_error is None:
            load_error = 'No board has that key'
    return render_template(PAGE, schema_data=None, boards_on=True, post_url=ADMIN + '/board',
                           approve=True, approve_day=day or _pacific_today(), preload=preload,
                           load_error=load_error, **page_context())


def _answer_from_form(tiles):
    """(answer JSON or None, error or None) from the approve form: no groups
    at all approves the board alone; otherwise a hub and four pairs that
    make an answer of this board, and up to four names."""
    raw_pairs = request.form.get('pairs', '').strip()
    if not raw_pairs or raw_pairs == '[]':
        return None, None
    try:
        pairs = [tuple(fbt.clean_tile(w) for w in p) for p in json.loads(raw_pairs)]
        names = json.loads(request.form.get('names', '') or '[]')
    except (ValueError, TypeError):
        return None, 'The groups did not come through: check them, then approve again'
    hub = fbt.clean_tile(request.form.get('hub', ''))
    at = {t: i for i, t in enumerate(tiles)}
    if (hub not in at or len(pairs) != fbt.GROUPS or any(len(p) != 2 or not set(p) <= set(at) for p in pairs)
            or fbt.answer_index(at[hub], [(at[a], at[b]) for a, b in pairs]) is None):
        return None, 'The four groups must share one hub and use every word once'
    if not isinstance(names, list):
        names = []
    names = [' '.join(str(n).split())[:NAME_MAX] if isinstance(n, str) else '' for n in names[:fbt.GROUPS]]
    names += [''] * (fbt.GROUPS - len(names))
    return json.dumps({'hub': hub, 'pairs': [list(p) for p in pairs], 'names': names}), None


def _answered_before(key):
    """Whether James has answered this board before, for any date: the
    solver may be tuned on that answer, so a first pass now would be no
    fair test. True if the rows can't be read (better none than an unfair
    one)."""
    try:
        return any(row[SOURCE] == APPROVAL and row[ANSWERS] for row in rows_with_key(key))
    except Exception as e:
        print(f"Error reading a 4x3 board's approvals: {e}")
        return True


@bp.route(ADMIN + '/approve_typed_board', methods=['POST'])
@auth.login_required
def four_by_three_approve_typed_board():
    """Serve a board James entered on /4x3_admin/board, for players whose
    date is `played_on`, with his answer if he gave one. `tiles` is the nine
    words as a JSON list; `hub`, `pairs` (JSON) and `names` (JSON) the
    answer. With his first answer to a board, the solver's own first pass on
    it is kept, for the Accuracy tab; approvals after that keep none."""
    day = parse_iso_day(request.form.get('played_on', ''))
    try:
        tiles = fbt.parse_board(json.loads(request.form.get('tiles', '')))
    except (ValueError, TypeError, fbt.BoardError):
        return _back(error='Not a board: check the nine words, then approve again')
    if day is None:
        return _back(error='Pick a date')
    answer, error = _answer_from_form(tiles)
    if error:
        return _back(error=error)
    key = fbt.board_key(tiles)
    top, version = None, None
    words = get_words()
    if answer is not None and words is not None and not _answered_before(key):
        weights, _n = current_weights()
        top = json.dumps(_first_pass(tiles, words, weights), separators=(',', ':'))
        version = solver_version(weights)
    try:
        replaced = store_approval(day, key, words_text(tiles), answer, top, version)
    except Exception as e:
        print(f"Error approving 4x3 board: {e}")
        return _back(error='Database error')
    _forget_boards()
    _forget_tuning()
    what = 'your board and answer' if answer else 'your board (no answer yet)'
    instead = ', in place of your earlier board that date' if replaced else ''
    return _back(success=f"Approved {what} for {day:%m-%d}: it's served now{instead}")


@bp.route(ADMIN + '/unapprove_board', methods=['POST'])
@auth.login_required
def four_by_three_unapprove_board():
    """Undo James's approval of a board, every date's: the players' boards
    and marks decide again, and none of them is touched."""
    key = request.form.get('key', '')
    if not _is_key(key):
        return _back(error='Not a board')
    try:
        removed = remove_approval(key)
    except Exception as e:
        print(f"Error undoing 4x3 approval: {e}")
        return _back(error='Database error')
    if not removed:
        return _back(error='That board was not approved')
    _forget_boards()
    _forget_tuning()
    return _back(success="Approval undone: the players' boards and marks decide again")


@bp.route(ADMIN + '/remove_board', methods=['POST'])
@auth.login_required
def four_by_three_remove_board():
    """Delete one date's entries of a board, approvals too: test boards, or junk."""
    key = request.form.get('key', '')
    played_on = request.form.get('played_on', '')
    if not (_is_key(key) and _DATE.fullmatch(played_on)):
        return _back(error='Not a board')
    try:
        removed = remove_board(key, date.fromisoformat(played_on))
    except Exception as e:
        print(f"Error removing 4x3 board: {e}")
        return _back(error='Database error')
    _forget_boards()
    _forget_tuning()
    return _back(success=f"Removed {removed} entr{'y' if removed == 1 else 'ies'} of that board on {played_on}")
