"""Firsts: the /firsts page and its solves, today's boards, players' marks,
and /firsts_admin.

The solver (functions/firsts.py) traces each clue's words on the board and
ranks them by the clue. Its word data (datasets/firsts_data.npz, about 12
MB) loads on the first solve; the word vectors are Chainle's
(routes/chainle.py get_data), in memory already for /chainle and /4x3.
Nothing is fetched at runtime: the solver never contacts the game.

The game's day. Firsts numbers its puzzles: No. N is 2026-11-25 + N days.
The site decides which number is today, the same on every device (No. 002
on devices set to Los Angeles, London, Tokyo and Kiritimati at 00:13 UTC on
2026-11-28), so the day is the game's and kept here (GAME_TZ), as on
/circuits. Past puzzles stay playable in the game and future ones open
early; the page offers today's, and the past ones James approved.

Today's boards, with /4x3's rules. A board is its 19 tiles (a picture as a
digit), which ones are green, and its 10 lengths; the title, the clue texts
and the pictures' names ride along with it. Only a starting board is logged
(every tile there, no clue solved, every length filled). James approves one
board a date, and can approve days ahead.

Answers. James checks answers clue by clue on /firsts_admin/board, and they
are served at once. Players' marks ("It worked", "Firsts refused it") add up
per clue: a word CONFIRM_NETWORKS networks say worked (more than say it was
refused) is served as players found; one as many say was refused is left
out of that clue's list.

Players are never told how many of them it takes (see crowd.py on why).
"""

import json
import os
import re
import threading
import time
from collections import Counter, OrderedDict, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import pytz
from flask import Blueprint, jsonify, redirect, render_template, request

import crowd
from data import data_folder
from extensions import (INTERACTIVE_LIMITS, auth, db_cursor, limiter, log_page_visit, log_solve,
                        pst_now_str)
from functions import firsts as fz
from helpers import ISO_DATE as _DATE
from helpers import ValidationError, json_object, make_schema_data, release_memory
from monitoring import alerts
from routes import chainle as chainle_route

bp = Blueprint('firsts', __name__)

PAGE = 'firsts.html'
PATH = '/firsts'


##### the game's day #####

# The calendar the site's "today" follows: the number turns over at
# midnight here.
GAME_TZ = pytz.timezone('America/New_York')
# No. N is LAUNCH + N days (No. 001 came out on 2026-11-26).
LAUNCH = date(2026, 11, 25)
# The game's own intro puzzles are No. -02 to 000.
FIRST_NUMBER = -2
# How far ahead James may approve: the game opens puzzles early.
AHEAD_DAYS = 60


def _game_now():
    """Now in the game's calendar (tests set this)."""
    return datetime.now(GAME_TZ)


def _game_today():
    return _game_now().date()


def number_of(day):
    return (day - LAUNCH).days


def day_of(n):
    return LAUNCH + timedelta(days=n)


def label_of(n):
    """A puzzle number as the game shows it: 002, -01."""
    return f'{n:03d}' if n >= 0 else f'-{-n:02d}'


##### solver data #####

# A module memo, not SimpleCache (which pickles a copy on every read).
_static = {'data': None, 'failed_at': 0.0}
_load_lock = threading.Lock()
# After a failed load, wait this long before trying again.
LOAD_RETRY_SECONDS = 300


def get_data():
    """The shipped word data, read once per process (~0.1 s, ~12 MB). None
    if it won't load; the page then says the solver is unavailable."""
    if _static['data'] is not None:
        return _static['data']
    with _load_lock:
        if _static['data'] is None and time.time() - _static['failed_at'] > LOAD_RETRY_SECONDS:
            try:
                _static['data'] = fz.Data.from_file(os.path.join(data_folder, 'firsts_data.npz'))
                release_memory()
            except Exception as e:
                _static['failed_at'] = time.time()
                alerts.alert_throttled('firsts_data_failed', sev='crit', exc=type(e).__name__, msg=e)
    return _static['data']


_vectors = {'source': None, 'value': None}


def get_vectors():
    """Numberbatch vectors from Chainle's data (one copy for both pages), or
    None: the solver then ranks without them."""
    source = chainle_route.get_data()
    if source is None:
        return None
    if _vectors['source'] is not source:
        _vectors['value'] = fz.Vectors(source)
        _vectors['source'] = source
    return _vectors['value']


def page_context():
    """What the page template needs besides the SEO blob. Rendering the page
    never touches the database."""
    today = number_of(_game_today())
    return {'cells': fz.hex_cells(), 'clue_count': fz.CLUES, 'today_n': today, 'today_label': label_of(today),
            'first_n': FIRST_NUMBER, 'max_letters': fz.MAX_LETTERS, 'max_pictures': fz.MAX_PICTURES}


##### the page's requests #####

MAX_CLUE_TEXT = 160
MAX_TITLE = 80
MAX_NAME = 40
# A board's key: tiles, greens and lengths (see board_key).
_KEY = re.compile(r'[A-Z1-4]{19}:[01]{19}:\d{1,2}(?:,\d{1,2})*(?:;\d{1,2}(?:,\d{1,2})*){%d}' % (fz.CLUES - 1))
_WORD = re.compile(r'[A-Z]{2,%d}' % fz.MAX_LETTERS)


def _bools(raw, name):
    if not isinstance(raw, list) or len(raw) != len(fz.hex_cells()) or not all(isinstance(b, bool) for b in raw):
        raise ValidationError(f'{name} must be {len(fz.hex_cells())} true/false values')
    return raw


def _short_text(raw, limit):
    return raw.strip()[:limit] if isinstance(raw, str) else ''


def _pictures_map(raw, limit):
    """{picture digit: text} from a body, cleaned."""
    if not isinstance(raw, dict):
        return {}
    return {k: _short_text(v, limit) for k, v in list(raw.items())[:fz.MAX_PICTURES]
            if k in ('1', '2', '3', '4') and _short_text(v, limit)}


def _clues_from(raw):
    """[(text, length)] for the clues, or ValidationError. A length left
    blank skips that clue."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= fz.CLUES:
        raise ValidationError(f'clues must be a list of up to {fz.CLUES}')
    out = []
    for c in raw:
        if not isinstance(c, list) or len(c) != 2 or not all(isinstance(x, str) for x in c):
            raise ValidationError('each clue is [text, length]')
        text, spec = _short_text(c[0], MAX_CLUE_TEXT), c[1].strip()[:20]
        if spec:
            try:
                fz.parse_spec(spec)
            except fz.BoardError as e:
                raise ValidationError(str(e))
        out.append((text, spec))
    return out


def _board_from(data):
    """(Board, clues) from a POST body, as the player sees them now, or
    ValidationError."""
    tiles = data.get('tiles')
    if not isinstance(tiles, list) or not all(isinstance(t, str) for t in tiles):
        raise ValidationError('tiles must be a list of letters')
    green = _bools(data.get('green'), 'green')
    gone = _bools(data.get('gone') if data.get('gone') is not None else [False] * len(green), 'gone')
    try:
        board = fz.Board(tiles, green, gone=gone, readings=_pictures_map(data.get('readings'), fz.PICTURE_MAX),
                         names=_pictures_map(data.get('names'), MAX_NAME),
                         title=_short_text(data.get('title'), MAX_TITLE), synced=data.get('synced') is True)
    except fz.BoardError as e:
        raise ValidationError(str(e))
    return board, _clues_from(data.get('clues'))


def board_key(board, clues):
    """A starting board as stored and matched: its tiles, greens and
    lengths (each written with commas). None if it isn't a whole starting
    board: a tile gone, no green, or a length missing."""
    if any(board.gone) or not any(board.green) or len(clues) != fz.CLUES:
        return None
    try:
        specs = [fz.spec_text(fz.parse_spec(spec)) for _text, spec in clues]
    except fz.BoardError:
        return None
    return board.key() + ':' + ';'.join(specs)


def details_text(board, clues):
    """What rides along with a board: title, clue texts (with each length as
    typed) and picture names, as JSON."""
    return json.dumps({'title': board.title, 'clues': [[t, s] for t, s in clues], 'names': board.names},
                      separators=(',', ':'), ensure_ascii=False)


def parse_details(text):
    """{'title', 'clues', 'names'} from a row, or None if damaged."""
    try:
        d = json.loads(text) if text else None
        clues = [[str(t)[:MAX_CLUE_TEXT], str(s)[:20]] for t, s in d['clues']][:fz.CLUES]
        return {'title': str(d.get('title') or '')[:MAX_TITLE], 'clues': clues,
                'names': {str(k): str(v)[:MAX_NAME] for k, v in (d.get('names') or {}).items()}}
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def board_from_key(key):
    """(tiles, green, specs) from a stored key, or None."""
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        return None
    tiles, green, specs = key.split(':')
    return list(tiles), [c == '1' for c in green], specs.split(';')


def _number_from(data):
    """The puzzle number a body asks about (today's if none), or
    ValidationError outside the game's numbers."""
    raw = data.get('n')
    today = number_of(_game_today())
    if raw is None:
        return today
    if not isinstance(raw, int) or isinstance(raw, bool) or not FIRST_NUMBER <= raw <= today + AHEAD_DAYS:
        raise ValidationError('n must be a puzzle number')
    return raw


def _index_map(raw, limit, value):
    """{clue index: value(x)} from a body's {"3": x}, for indexes on the
    board; entries value() rejects (None) are dropped."""
    out = {}
    if isinstance(raw, dict):
        for k, v in list(raw.items())[:limit]:
            if isinstance(k, str) and k.isdigit() and int(k) < fz.CLUES:
                got = value(v)
                if got is not None:
                    out[int(k)] = got
    return out


def _word(v):
    w = fz.letters_key(v) if isinstance(v, str) else ''
    return w if _WORD.fullmatch(w) else None


# Recent solves, newest last: a request's essentials -> its reply. Small
# JSON, so a few hundred KB at most.
SOLVE_MEMO_MAX = 16
_solve_memo = OrderedDict()
_solve_lock = threading.Lock()


def solve_request(data, live):
    """The JSON the page renders: functions/firsts.py solve() for the board
    as the player sees it, with what James and other players have settled
    about the starting board (`key`) when `live`, and the player's own
    solved clues (`solved`) and words that worked (`yours`)."""
    board, clues = _board_from(data)
    words = get_data()
    if words is None:
        return jsonify(error='The solver is unavailable right now. Please try again in a few minutes.'), 503
    key = data.get('key')
    known, refused, checked = {}, {}, False
    if live and isinstance(key, str) and _KEY.fullmatch(key):
        found = answers_for(key)
        for pic, reading in found['readings'].items():
            if pic in board.pictures():
                board.readings.setdefault(pic, reading)
        for i, w in found['checked'].items():
            known[i] = (w, 'checked')
            checked = True
        for i, w in found['players'].items():
            known.setdefault(i, (w, 'players'))
        refused = found['refused']
    for i, w in _index_map(data.get('yours'), fz.CLUES, _word).items():
        if i not in known or known[i][1] == 'players':
            known[i] = (w, 'yours')
    raw_solved = data.get('solved')
    solved = sorted({s for s in raw_solved if isinstance(s, int) and 0 <= s < len(clues)}) \
        if isinstance(raw_solved, list) else []
    memo_key = json.dumps([board.tiles, board.green, board.gone, board.readings, board.names, board.title,
                           board.synced, clues, sorted(known.items()), solved, sorted(refused.items()),
                           fz.SOLVER_VERSION], default=sorted)
    with _solve_lock:
        hit = _solve_memo.get(memo_key)
        if hit is not None:
            _solve_memo.move_to_end(memo_key)
    if hit is None:
        hit = fz.solve(words, board, clues, vectors=get_vectors(), known=known, solved=solved, refused=refused)
        hit['checked'] = checked
        with _solve_lock:
            _solve_memo[memo_key] = hit
            while len(_solve_memo) > SOLVE_MEMO_MAX:
                _solve_memo.popitem(last=False)
    return jsonify(hit)


##### boards: shape and storage #####

# When players' boards are served: this many players (one per network)
# entering the same board on the same date. Players are never told.
SERVE_NETWORKS = 2
# The most boards one date offers: James's approval first, then the players'.
TODAYS_BOARDS_MAX = 3
# Rows one player can add on one date (boards logged, and boards marked).
BOARDS_PER_PLAYER_PER_DAY = 10
# 'typed' = entered by the player; 'edited' = loaded from today's boards,
# then changed. A board loaded and left as it is is never logged as a board.
BOARD_SOURCES = ('typed', 'edited')
# A loaded board's row carries the player's marks only: it never counts
# toward serving its board.
LOADED = 'loaded'
# A word is served as players found, or left out as refused, once this many
# networks marked it so, and more of them did than said the opposite.
CONFIRM_NETWORKS = 3
# Live on /firsts from launch. False keeps logging boards and marks but
# offers none and serves no answers (except on /firsts_admin/board).
FIRSTS_BOARDS_LIVE = True
# James's approval: a row of its own, with this as its source and its voter
# hash (a real one is 32 hex characters). One a date. Never a player.
APPROVAL = 'admin'
# How long a date's boards, or a board's answers, are reused before they're
# read again, and how long a failed read waits. One gunicorn worker, so
# these memos are the only copies.
BOARDS_CACHE_SECONDS = 60
ANSWERS_CACHE_SECONDS = 60
ANSWERS_CACHE_MAX = 200
READ_RETRY_SECONDS = 300
# Words a player's marks may name per clue, and the past puzzles offered.
MARK_WORDS_MAX = 6
PAST_OFFERED = 60

# The columns every read of firsts_boards returns, in this order.
_COLUMNS = ('board_key, details, source, voter_hash, played_on, updated_at, prefix_hash, marks, answers, '
            'solver_top')
(KEY, DETAILS, SOURCE, VOTER, PLAYED_ON, UPDATED, PREFIX, MARKS, ANSWERS, TOP) = range(10)


def _fetch(sql, params):
    with db_cursor() as (conn, cursor):
        cursor.execute(sql, params)
        return cursor.fetchall()


def rows_with_key(key):
    """Every row for one board, any date."""
    return _fetch(f"SELECT {_COLUMNS} FROM firsts_boards WHERE board_key = %s", (key,))


def rows_logged_between(first, last):
    """Every row, any date, for each board logged between these days."""
    return _fetch(
        f"SELECT {_COLUMNS} FROM firsts_boards WHERE board_key IN "
        "(SELECT board_key FROM firsts_boards WHERE played_on BETWEEN %s AND %s)",
        (first, last))


def approved_days(before, limit):
    """[(played_on, details)] of James's approvals before `before`, newest
    first."""
    return _fetch("SELECT played_on, details FROM firsts_boards WHERE source = %s AND played_on < %s "
                  "ORDER BY played_on DESC LIMIT %s", (APPROVAL, before, limit))


# How long a write waits for another of the same voter's to finish.
LOCK_SECONDS = 3


@contextmanager
def _one_at_a_time(cursor, voter, day):
    """Hold a database lock on one voter's rows for one date, so two of
    their requests can't both count the same rows and then both insert (as
    routes/four_by_six.py)."""
    name = f'firsts:{voter}:{day}'
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


def _under_cap(cursor, voter, day, key):
    cursor.execute("SELECT COUNT(*) FROM firsts_boards WHERE voter_hash = %s AND played_on = %s "
                   "AND board_key <> %s", (voter, day, key))
    return cursor.fetchone()[0] < BOARDS_PER_PLAYER_PER_DAY


def store_board(day, voter, prefix, key, details, source):
    """Log one player's board, replacing their earlier entry of it that
    date (their marks on it stay). Inline, not on the write-behind queue.
    Best-effort."""
    now = pst_now_str()
    try:
        with db_cursor() as (conn, cursor), _one_at_a_time(cursor, voter, day):
            if not _under_cap(cursor, voter, day, key):
                return
            cursor.execute(
                """INSERT INTO firsts_boards (played_on, voter_hash, prefix_hash, board_key, details, source,
                       created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE details = new.details, source = new.source,
                       prefix_hash = new.prefix_hash, updated_at = new.updated_at""",
                (day, voter, prefix, key, details, source, now, now))
            conn.commit()
    except Exception as e:
        alerts.alert_throttled('firsts_board_log_failed', exc=type(e).__name__, msg=e)


def store_marks(day, voter, prefix, key, marks):
    """Keep one player's marks on a board (JSON, see save_marks) in place of
    their earlier ones. A board they haven't logged gets a 'loaded' row,
    which never counts toward serving the board. Best-effort."""
    now = pst_now_str()
    try:
        with db_cursor() as (conn, cursor), _one_at_a_time(cursor, voter, day):
            if not _under_cap(cursor, voter, day, key):
                return
            cursor.execute(
                """INSERT INTO firsts_boards (played_on, voter_hash, prefix_hash, board_key, source, marks,
                       created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE marks = new.marks, prefix_hash = new.prefix_hash,
                       updated_at = new.updated_at""",
                (day, voter, prefix, key, LOADED, marks, now, now))
            conn.commit()
    except Exception as e:
        alerts.alert_throttled('firsts_board_log_failed', exc=type(e).__name__, msg=e)


def store_approval(day, key, details, answers, top, version):
    """Approve this board for `day`, with James's checked answers (JSON, or
    None for the board alone). One board a date: this takes the place of
    his approval of any other board that date. The solver's first pass stays
    from the board's first approval, so re-saving answers can't grade a
    solver that has seen them. Both writes happen or neither. Returns how
    many other boards' approvals it replaced. Raises."""
    now = pst_now_str()
    with db_cursor() as (conn, cursor), _one_at_a_time(cursor, APPROVAL, day):
        # The Heroku pool runs with autocommit on (config.py), so the two
        # writes need a transaction of their own to be one change.
        cursor.execute("START TRANSACTION")
        try:
            cursor.execute("DELETE FROM firsts_boards WHERE voter_hash = %s AND played_on = %s "
                           "AND board_key <> %s", (APPROVAL, day, key))
            replaced = cursor.rowcount
            cursor.execute(
                """INSERT INTO firsts_boards (played_on, voter_hash, board_key, details, source, answers,
                       solver_top, solver_version, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE details = new.details, answers = new.answers,
                       solver_top = COALESCE(firsts_boards.solver_top, new.solver_top),
                       solver_version = COALESCE(firsts_boards.solver_version, new.solver_version),
                       updated_at = new.updated_at""",
                (day, APPROVAL, key, details, APPROVAL, answers, top, version, now, now))
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
        cursor.execute("DELETE FROM firsts_boards WHERE board_key = %s AND voter_hash = %s", (key, APPROVAL))
        removed = cursor.rowcount
        conn.commit()
    return removed


def remove_board(key, day):
    """Delete every entry of this board on `day`, approvals too: test boards,
    or junk. Returns how many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM firsts_boards WHERE board_key = %s AND played_on = %s", (key, day))
        removed = cursor.rowcount
        conn.commit()
    return removed


##### boards: what they add up to #####

def _networks(rows, sources=BOARD_SOURCES):
    """One row per network (crowd.one_per_network), from rows of `sources`."""
    return crowd.one_per_network([row for row in rows if row[SOURCE] in sources], VOTER, PREFIX, UPDATED)


def _norm(text):
    return re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).strip()


def merged_details(rows):
    """A board's title, clue texts and picture names as served: James's if
    he approved it, else per clue the text most of its players typed (case
    and punctuation aside), shown as most of them wrote it."""
    approvals = [row for row in rows if row[SOURCE] == APPROVAL and parse_details(row[DETAILS])]
    if approvals:
        return parse_details(max(approvals, key=lambda row: row[UPDATED])[DETAILS])
    parsed = [d for d in (parse_details(row[DETAILS]) for row in _networks(rows).values()) if d]
    if not parsed:
        return {'title': '', 'clues': [['', ''] for _ in range(fz.CLUES)], 'names': {}}

    def pick(values):
        counts = Counter(_norm(v) for v in values if _norm(v))
        if not counts:
            return ''
        order = [_norm(v) for v in values]
        best = max(counts, key=lambda k: (counts[k], -order.index(k)))
        forms = [v for v in values if _norm(v) == best]
        written = Counter(forms)
        return max(written, key=lambda v: (written[v], -forms.index(v)))
    clues = []
    for i in range(fz.CLUES):
        texts = [d['clues'][i][0] for d in parsed if i < len(d['clues'])]
        specs = [d['clues'][i][1] for d in parsed if i < len(d['clues'])]
        clues.append([pick(texts), pick(specs)])
    names = {}
    for p in ('1', '2', '3', '4'):
        got = pick([d['names'][p] for d in parsed if p in d['names']])
        if got:
            names[p] = got
    return {'title': pick([d['title'] for d in parsed]), 'clues': clues, 'names': names}


def consensus(rows):
    """What one board's rows (one date's) add up to: key, players
    (networks), why (None when settled by an approval or SERVE_NETWORKS
    players), approved, last_update."""
    approved = any(row[SOURCE] == APPROVAL for row in rows)
    player_rows = [row for row in rows if row[SOURCE] in BOARD_SOURCES]
    players = len(_networks(rows))
    short = SERVE_NETWORKS - players
    why = None
    if not approved and short > 0:
        why = f'needs {short} more player{"s" if short != 1 else ""}' if player_rows else 'only marks so far'
    return {'key': rows[0][KEY], 'players': players, 'why': why, 'approved': approved,
            'last_update': max(row[UPDATED] for row in rows)}


def todays_boards(rows, day):
    """The boards to offer for `day`, and what the admin page shows about it.

    rows -- every firsts_boards row (any date) for each board logged on `day`.

    Only `day`'s own entries count. James's approval comes first, then the
    players' boards, most players first, at most TODAYS_BOARDS_MAX. When
    SERVE_NETWORKS players agree on a board other than his approval, the
    date is flagged: the game may have changed the puzzle after he approved
    it. Returns (boards, groups, flagged).
    """
    by_key = defaultdict(list)
    for row in rows:
        if row[PLAYED_ON] == day:
            by_key[row[KEY]].append(row)
    groups = []
    for key, key_rows in by_key.items():
        result = consensus(key_rows)
        result['details'] = merged_details(key_rows)
        result['served'] = False
        groups.append(result)
    groups.sort(key=lambda g: (not g['approved'], -g['players'], g['key']))
    ready = [g for g in groups if g['why'] is None]
    for g in ready[TODAYS_BOARDS_MAX:]:
        g['why'] = 'past the limit of boards a day'
    boards = []
    for g in ready[:TODAYS_BOARDS_MAX]:
        g['served'] = True
        boards.append({'key': g['key'], 'details': g['details'], 'approved': g['approved']})
    flagged = any(g['approved'] for g in groups) and any(
        not g['approved'] and g['players'] >= SERVE_NETWORKS for g in groups)
    return boards, groups, flagged


# day -> (expires, boards): what _boards_for last offered, time.monotonic().
_boards_cache = {}
# Bumped by every admin change: a read that started before one is used by
# its own request but not kept, since it may predate the change.
_boards_gen = {'n': 0}
_boards_lock = threading.Lock()
# (expires, [(n, title)]): the approved past puzzles, newest first.
_past_cache = {'value': None, 'expires': 0.0}


def _forget_boards():
    """After an admin change: the next request reads boards and answers
    again, and the solve memo starts over."""
    with _boards_lock:
        _boards_gen['n'] += 1
        _boards_cache.clear()
        _answers_cache.clear()
        _past_cache.update(value=None, expires=0.0)
    with _solve_lock:
        _solve_memo.clear()


def _boards_for(day):
    """The boards offered for `day`, from the memo while fresh, else read
    again; never raises (see routes/four_by_six.py _boards_for)."""
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
        alerts.alert_throttled('firsts_board_read_failed', exc=type(e).__name__, msg=e)
        boards = entry[1] if entry is not None else []
        expires = time.monotonic() + READ_RETRY_SECONDS
    with _boards_lock:
        if _boards_gen['n'] == gen:
            _boards_cache[day] = (expires, boards)
        for old in [d for d in list(_boards_cache) if abs((d - day).days) > 2 and len(_boards_cache) > 8]:
            _boards_cache.pop(old, None)
    return boards


def past_puzzles():
    """[{'n', 'label', 'title'}] for the puzzles before today James approved,
    newest first, for the page's picker. From a memo; never raises."""
    with _boards_lock:
        if _past_cache['value'] is not None and time.monotonic() < _past_cache['expires']:
            return _past_cache['value']
        gen = _boards_gen['n']
    try:
        rows = approved_days(_game_today(), PAST_OFFERED)
        value = []
        for day, details in rows:
            n = number_of(day)
            if n < FIRST_NUMBER or (value and value[-1]['n'] == n):
                continue
            d = parse_details(details) or {'title': ''}
            value.append({'n': n, 'label': label_of(n), 'title': d['title']})
        expires = time.monotonic() + BOARDS_CACHE_SECONDS
    except Exception as e:
        alerts.alert_throttled('firsts_board_read_failed', exc=type(e).__name__, msg=e)
        value = _past_cache['value'] or []
        expires = time.monotonic() + READ_RETRY_SECONDS
    with _boards_lock:
        if _boards_gen['n'] == gen:
            _past_cache.update(value=value, expires=expires)
    return value


def first_paint_boards():
    """Today's boards if a lookup in this process found them, fresh or not,
    for the page to draw its offer as it arrives (one that came later would
    push the board down, a layout shift); None if there are none. Never
    reads the database: the page still asks for the newest."""
    if not FIRSTS_BOARDS_LIVE:
        return None
    today = _game_today()
    with _boards_lock:
        entry = _boards_cache.get(today)
    return entry[1] if entry is not None and entry[1] else None


def todays_reply(data, serve_dark=False):
    """The `todays_boards` action: the boards for puzzle `n` (today's if
    none), and the approved past puzzles. A past puzzle offers James's
    approval only; a future one, nothing (except on /firsts_admin/board)."""
    n = _number_from(data)
    day, today = day_of(n), _game_today()
    if not (serve_dark or FIRSTS_BOARDS_LIVE):
        return jsonify(boards=[], n=n, past=[])
    boards = _boards_for(day)
    if day < today and not serve_dark:
        boards = [b for b in boards if b['approved']]
    elif day > today and not serve_dark:
        boards = []
    return jsonify(boards=boards, n=n, past=past_puzzles())


def log_board(data):
    """A starting board a player uses, sent once per version of it. Always
    the same reply, stored or not."""
    board, clues = _board_from(data)
    source = data.get('source')
    if source not in BOARD_SOURCES:
        raise ValidationError('source must be typed or edited')
    n = _number_from(data)
    day = day_of(n)
    key = board_key(board, clues)
    if key is None or day > _game_today():
        return jsonify(status='success')
    if source == 'edited' and key in [b['key'] for b in _boards_for(day)]:
        # Changed and then changed back: still the board on offer, which
        # can't vote for itself.
        return jsonify(status='success')
    store_board(day, crowd.player_hash('firsts'), crowd.prefix_hash('firsts'), key,
                details_text(board, clues), source)
    return jsonify(status='success')


def _marks_from(raw):
    """{clue index: {word: 'right' or 'miss'}} from a body, or
    ValidationError."""
    if not isinstance(raw, dict) or len(raw) > fz.CLUES:
        raise ValidationError('marks must map clue numbers to words')
    out = {}
    for k, v in raw.items():
        if not (isinstance(k, str) and k.isdigit() and int(k) < fz.CLUES) or not isinstance(v, dict) \
                or len(v) > MARK_WORDS_MAX:
            raise ValidationError('marks must map clue numbers to words')
        words = {}
        for w, verdict in v.items():
            word = _word(w)
            if word is None or verdict not in ('right', 'miss'):
                raise ValidationError("each mark is a word and 'right' or 'miss'")
            words[word] = verdict
        if words:
            out[int(k)] = words
    return out


def save_marks(data):
    """A player's marks on a starting board (`key`), all of them, each time
    they change. Always the same reply, stored or not."""
    key = data.get('key')
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValidationError('key must be a board key')
    marks = _marks_from(data.get('marks'))
    day = day_of(_number_from(data))
    if day <= _game_today():
        store_marks(day, crowd.player_hash('firsts'), crowd.prefix_hash('firsts'), key,
                    json.dumps({str(i): w for i, w in sorted(marks.items())}, separators=(',', ':')))
    return jsonify(status='success')


##### answers: James's, and what players' marks add up to #####

def parse_answers(text):
    """James's checked answers from an approval: ({clue index: word},
    {picture: reading}); empty if none or damaged."""
    try:
        raw = json.loads(text) if text else {}
        words = {int(k): w for k, w in (raw.get('words') or {}).items()
                 if str(k).isdigit() and int(k) < fz.CLUES and _word(w) == w}
        readings = {str(k): v for k, v in (raw.get('readings') or {}).items()
                    if str(k) in ('1', '2', '3', '4') and isinstance(v, str) and v.isalpha() and v.isupper()}
        return words, readings
    except (ValueError, TypeError, AttributeError):
        return {}, {}


def _parse_marks(text):
    try:
        raw = json.loads(text) if text else {}
        return {int(k): {w: v for w, v in words.items() if v in ('right', 'miss')} for k, words in raw.items()}
    except (ValueError, TypeError, AttributeError):
        return {}


def mark_counts(rows):
    """{clue index: {word: [networks that said it worked, networks that said
    it was refused]}} from one board's rows: each network's latest row,
    approvals left out."""
    counts = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for row in _networks(rows, BOARD_SOURCES + (LOADED,)).values():
        for i, words in _parse_marks(row[MARKS]).items():
            for w, verdict in words.items():
                counts[i][w][0 if verdict == 'right' else 1] += 1
    return counts


def crowd_answers(rows, key):
    """What one board's rows (any date) settle: {'checked': James's newest
    answers, 'readings': his picture readings, 'players': {clue: word}
    players found, 'refused': {clue: [words]}, 'tallies': {clue: [(word,
    worked, refused)]}}. A players' word must fit its clue's length."""
    approvals = [row for row in rows if row[SOURCE] == APPROVAL and row[ANSWERS]]
    checked, readings = parse_answers(max(approvals, key=lambda row: row[UPDATED])[ANSWERS]) \
        if approvals else ({}, {})
    parsed = board_from_key(key)
    lengths = [sum(fz.parse_spec(s)) for s in parsed[2]] if parsed else []
    players, refused, tallies = {}, {}, {}
    for i, words in mark_counts(rows).items():
        tallies[i] = sorted(((w, r, m) for w, (r, m) in words.items()), key=lambda t: (-t[1], t[2], t[0]))
        if i >= len(lengths):
            continue
        sure = [(w, r, m) for w, r, m in tallies[i] if r >= CONFIRM_NETWORKS and r > m and len(w) == lengths[i]]
        if sure:
            players[i] = sure[0][0]
        out = [w for w, r, m in tallies[i] if m >= CONFIRM_NETWORKS and m > r]
        if out:
            refused[i] = out
    return {'checked': checked, 'readings': readings, 'players': players, 'refused': refused,
            'tallies': tallies}


# key -> (expires, answers): what answers_for last read, time.monotonic().
_answers_cache = {}


def answers_for(key):
    """crowd_answers for one board, from the memo while fresh; never raises
    (a failed read is no answers, retried after READ_RETRY_SECONDS)."""
    with _boards_lock:
        now = time.monotonic()
        entry = _answers_cache.get(key)
        if entry is not None and now < entry[0]:
            return entry[1]
        gen = _boards_gen['n']
    try:
        value = crowd_answers(rows_with_key(key), key)
        expires = time.monotonic() + ANSWERS_CACHE_SECONDS
    except Exception as e:
        alerts.alert_throttled('firsts_board_read_failed', exc=type(e).__name__, msg=e)
        value = entry[1] if entry is not None else crowd_answers([], key)
        expires = time.monotonic() + READ_RETRY_SECONDS
    with _boards_lock:
        if _boards_gen['n'] == gen:
            if len(_answers_cache) >= ANSWERS_CACHE_MAX:
                expired = [k for k, (until, _v) in list(_answers_cache.items()) if until < now]
                for old in expired or list(_answers_cache):
                    _answers_cache.pop(old, None)
            _answers_cache[key] = (expires, value)
    return value


##### the page #####

def handle_action(data, serve_dark=False):
    """A JSON body posted with an `action`: a board to log, a player's
    marks, or today's boards. `serve_dark` (only /firsts_admin/board) offers
    boards even while FIRSTS_BOARDS_LIVE is off."""
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
def run_firsts():
    """The page, a solve, or an `action` (a board to log, marks, today's
    boards)."""
    if request.method == 'POST':
        data = json_object()
        if 'action' in data:
            return handle_action(data)
        response = solve_request(data, FIRSTS_BOARDS_LIVE)
        log_solve(PAGE, data)
        return response
    schema_data = make_schema_data(
        "Firsts Answers & Hints Today - Bleppo's Firsts Puzzle Solver",
        "Stuck on today's Firsts, the hex crossword from Bleppo Games? Load the board in one tap for hints "
        "clue by clue, or the answers, traced on the tiles.",
        "https://www.jamesapplewhite.com/firsts"
    )
    log_page_visit(PAGE)
    return render_template(PAGE, schema_data=schema_data, boards_on=FIRSTS_BOARDS_LIVE,
                           first_boards=first_paint_boards(), post_url=PATH, approve=False, **page_context())


##### admin #####

ADMIN = '/firsts_admin'
# The dates the page shows: the game's last three, and any ahead with rows.
ADMIN_DAYS_BACK = 2


def _back(**message):
    """Back to /firsts_admin, with a message."""
    return redirect(crowd.admin_return_url(ADMIN, request.form, **message))


def _admin_days():
    """(days, error): each date's boards with their answers, newest date
    first."""
    today = _game_today()
    first, last = today - timedelta(days=ADMIN_DAYS_BACK), today + timedelta(days=AHEAD_DAYS)
    try:
        rows = rows_logged_between(first, last)
    except Exception as e:
        print(f"Error loading Firsts admin boards: {e}")
        return [], f'{type(e).__name__}: {e}'
    by_key = defaultdict(list)
    for row in rows:
        by_key[row[KEY]].append(row)
    days = []
    for day in sorted({row[PLAYED_ON] for row in rows if first <= row[PLAYED_ON] <= last}, reverse=True):
        _boards, groups, flagged = todays_boards(rows, day)
        for g in groups:
            found = crowd_answers(by_key[g['key']], g['key'])
            g['checked'] = found['checked']
            g['tallies'] = found['tallies']
            tops = [row[TOP] for row in by_key[g['key']] if row[SOURCE] == APPROVAL and row[TOP]]
            try:
                g['top'] = json.loads(tops[0]) if tops else None
            except ValueError:
                g['top'] = None
        days.append({'day': day, 'n': number_of(day), 'label': label_of(number_of(day)), 'groups': groups,
                     'flagged': flagged, 'today': day == today})
    return days, None


@bp.route(ADMIN)
@auth.login_required
def firsts_admin():
    """Boards to approve, with each one's players and answers."""
    days, load_error = _admin_days()
    return render_template('firsts_admin.html', days=days, load_error=load_error, boards_live=FIRSTS_BOARDS_LIVE,
                           today_n=number_of(_game_today()), today_label=label_of(number_of(_game_today())),
                           serve_networks=SERVE_NETWORKS, confirm_networks=CONFIRM_NETWORKS,
                           todays_boards_max=TODAYS_BOARDS_MAX, clue_count=fz.CLUES)


def editor_board(key):
    """The board /firsts_admin/board opens with: {'key', 'tiles', 'green',
    'details', 'answers', 'readings'} from a stored key, or None."""
    parsed = board_from_key(key)
    if parsed is None:
        return None
    tiles, green, specs = parsed
    try:
        rows = rows_with_key(key)
    except Exception as e:
        print(f"Error loading Firsts board: {e}")
        rows = []
    details = merged_details(rows) if rows else {'title': '', 'clues': [['', s] for s in specs], 'names': {}}
    for i, s in enumerate(specs):
        if i < len(details['clues']) and not details['clues'][i][1]:
            details['clues'][i][1] = s
    found = crowd_answers(rows, key)
    return {'key': key, 'tiles': tiles, 'green': green, 'details': details,
            'answers': {str(i): w for i, w in found['checked'].items()}, 'readings': found['readings']}


@bp.route(ADMIN + '/board', methods=['GET', 'POST'])
@auth.login_required
def firsts_admin_board():
    """James's board entry: /firsts's own editor and solver, with an Approve
    bar (and a way to check answers) in place of logging. Its JSON posts are
    solves and today's boards; it never logs a board or marks, so nothing
    here counts as a player. ?key= opens a stored board, ?n= sets the
    puzzle number."""
    if request.method == 'POST':
        data = json_object()
        if 'action' in data:
            if data.get('action') != 'todays_boards':
                raise ValidationError('only todays_boards here')
            return handle_action(data, serve_dark=True)
        return solve_request(data, True)
    preload, load_error = None, None
    raw = request.args.get('key', '')
    if raw:
        preload = editor_board(raw)
        if preload is None:
            load_error = 'Not a board: open one from the admin page'
    n = request.args.get('n', '')
    today = number_of(_game_today())
    approve_n = int(n) if re.fullmatch(r'-?\d{1,3}', n) and FIRST_NUMBER <= int(n) <= today + AHEAD_DAYS else today
    return render_template(PAGE, schema_data=None, boards_on=True, first_boards=None, post_url=ADMIN + '/board',
                           approve=True, approve_n=approve_n, preload=preload, load_error=load_error,
                           **page_context())


def _approval_from_form():
    """(n, key, details JSON, answers JSON or None, board, clues) from the
    Approve bar, or an error message."""
    n = request.form.get('n', '')
    today = number_of(_game_today())
    if not re.fullmatch(r'-?\d{1,3}', n) or not FIRST_NUMBER <= int(n) <= today + AHEAD_DAYS:
        return 'Pick a puzzle number'
    try:
        data = json.loads(request.form.get('board', ''))
        if not isinstance(data, dict):
            raise ValueError
        board, clues = _board_from(data)
    except (ValueError, ValidationError):
        return 'Not a board: check the tiles and clues, then approve again'
    key = board_key(board, clues)
    if key is None:
        return 'Approve a whole starting board: every tile there, a green, and all 10 lengths'
    answers = None
    try:
        raw = json.loads(request.form.get('answers') or '{}')
        words = _index_map(raw.get('words'), fz.CLUES, _word) if isinstance(raw, dict) else {}
        readings = {k: fz.letters_key(v) for k, v in (raw.get('readings') or {}).items()
                    if k in board.pictures() and fz.PICTURE_MIN <= len(fz.letters_key(v)) <= fz.PICTURE_MAX} \
            if isinstance(raw, dict) and isinstance(raw.get('readings'), dict) else {}
    except ValueError:
        return 'The answers were damaged: check them, then approve again'
    if words or readings:
        answers = json.dumps({'words': {str(i): w for i, w in sorted(words.items())}, 'readings': readings},
                             separators=(',', ':'))
    return int(n), key, details_text(board, clues), answers, board, clues


@bp.route(ADMIN + '/approve_typed_board', methods=['POST'])
@auth.login_required
def firsts_approve_typed_board():
    """Serve this board for puzzle `n`, with the answers James checked:
    one entered on /firsts_admin/board, or Approve on a player's board."""
    got = _approval_from_form()
    if isinstance(got, str):
        return _back(error=got)
    n, key, details, answers, board, clues = got
    top = None
    words = get_data()
    if words is not None:
        # The solver's first pass, before it sees any answer.
        fresh = fz.Board(board.tiles, board.green, names=board.names, title=board.title)
        try:
            result = fz.solve(words, fresh, clues, vectors=get_vectors())
            top = json.dumps([c['items'][0]['word'] if c['items'] else '' for c in result['clues']],
                             separators=(',', ':'))
        except Exception as e:
            print(f"Firsts first pass failed: {e}")
    try:
        replaced = store_approval(day_of(n), key, details, answers, top, fz.SOLVER_VERSION)
    except Exception as e:
        print(f"Error approving Firsts board: {e}")
        return _back(error='Database error')
    _forget_boards()
    instead = ', in place of your earlier board that date' if replaced else ''
    when = 'served now' if day_of(n) <= _game_today() else 'served from its day'
    return _back(success=f"Approved No. {label_of(n)}: {when}{instead}")


@bp.route(ADMIN + '/unapprove_board', methods=['POST'])
@auth.login_required
def firsts_unapprove_board():
    """Undo James's approval of a board, every date's: the players' boards
    decide again, and none of them is touched."""
    key = request.form.get('key', '')
    if not _KEY.fullmatch(key):
        return _back(error='Not a board')
    try:
        removed = remove_approval(key)
    except Exception as e:
        print(f"Error undoing Firsts approval: {e}")
        return _back(error='Database error')
    if not removed:
        return _back(error='That board was not approved')
    _forget_boards()
    return _back(success="Approval undone: the players' boards decide again")


@bp.route(ADMIN + '/remove_board', methods=['POST'])
@auth.login_required
def firsts_remove_board():
    """Delete one date's entries of a board, approvals too: test boards, or
    junk. Takes the key as stored, so a damaged row can go too."""
    key = request.form.get('key')
    played_on = request.form.get('played_on', '')
    day = None
    if _DATE.fullmatch(played_on):
        try:
            day = date.fromisoformat(played_on)
        except ValueError:
            day = None
    if day is None or key is None or len(key) > 120:
        return _back(error='Not a board')
    try:
        removed = remove_board(key, day)
    except Exception as e:
        print(f"Error removing Firsts board: {e}")
        return _back(error='Database error')
    _forget_boards()
    return _back(success=f"Removed {removed} entr{'y' if removed == 1 else 'ies'} of that board on {day}")
