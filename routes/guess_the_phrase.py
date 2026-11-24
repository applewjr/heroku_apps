"""Guess The Phrase solver data, today's board, players' added phrases, and the
admin page.

The solver route itself is run_guess_the_phrase in routes/wordgames.py; it
hands a board to solve_request here, a board to log or a request for today's
to handle_board_action, and an added phrase to handle_vote.

Self-contained (James, 2026-10-11): the phrase list ships in datasets/ (see
datasets/PHRASE_BANK.md) and nothing is fetched at runtime. Bored Panda's
answers are never fetched. The only phrases stored or added at runtime are
ones players type in after solving (gtp_word_votes, gtp_added_words).
"""

import os
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import pytz
from flask import Blueprint, jsonify, redirect, render_template, request

import crowd
from data import data_folder
from extensions import auth, db_cursor, pst_now_str
from functions import phrase_solver as ps
from helpers import ValidationError, parse_iso_day, release_memory
from monitoring import alerts

bp = Blueprint('guess_the_phrase', __name__)

# How long a player's added phrase keeps counting toward the two players it
# needs. Not crowd.py's 7 days: players work through old puzzles, so two who
# solve the same one can be weeks apart. monitoring/daily_digest.py repeats
# this number; a test keeps the two equal.
GTP_WINDOW_DAYS = 30

# Request limits. Bored Panda's longest answer so far is 10 words and 37
# letters; these leave room without letting a request scan far.
MAX_BOARD_CHARS = 80
MAX_WORDS = 12
MAX_WORD_LEN = 20


##### solver data #####

# Module memos, not SimpleCache: SimpleCache pickles, so every get would
# rebuild ~23 MB (see routes/smush.py and memory_creep_plan.md).
_static = {'data': None, 'failed_at': 0.0}
_overlay = {'value': None, 'expires': 0.0}
_load_lock = threading.Lock()
# After a failed load, wait this long before trying again, so a broken file
# costs one attempt per window rather than one per request.
LOAD_RETRY_SECONDS = 300


class _Solver:
    """What phrase_solver.solve() needs, plus the blocklist for added phrases."""

    def __init__(self, static, bank):
        self.bank = bank
        self.words = static.words
        self.bigrams = static.bigrams
        self.blocklist = static.blocklist


def _load_static():
    """The shipped files, read once per process (~1 s, ~23 MB). None if they
    won't load; the page then says the list is unavailable."""
    if _static['data'] is not None:
        return _static['data']
    with _load_lock:
        if _static['data'] is None and time.time() - _static['failed_at'] > LOAD_RETRY_SECONDS:
            try:
                data = ps.SolverData(os.path.join(data_folder, 'phrase_bank.tsv.gz'),
                                     os.path.join(data_folder, 'phrase_words.tsv.gz'),
                                     os.path.join(data_folder, 'phrase_bigrams.tsv.gz'))
                data.blocklist = ps.load_blocklist(os.path.join(data_folder, 'phrase_blocklist.txt'))
                _static['data'] = data
                # Loading parses ~1M lines; glibc keeps what they freed
                # unless asked (about 5 MB on Linux, measured 2026-10-11).
                release_memory()
            except Exception as e:
                _static['failed_at'] = time.time()
                alerts.alert_throttled('gtp_bank_failed', sev='crit', exc=type(e).__name__, msg=e)
    return _static['data']


def _forget_overlay():
    """Make the next get_solver() re-read players' and the admin's changes."""
    _overlay['expires'] = 0.0


def get_solver():
    """The shipped phrase list with the gtp_* corrections applied, or None
    if the shipped files won't load.

    Refreshed every 12 hours, or straight away after a change. A DB blip
    keeps the last good corrections (or none) and retries in 5 minutes,
    so the solver itself never depends on the database (see
    crowd.CrowdList.corrected).
    """
    static = _load_static()
    if static is None:
        return None
    return GTP_CROWD.corrected(
        _overlay, lambda removed, added: _Solver(static, ps.OverlayBank(static.bank, added, removed)),
        case=str.upper)


def page_context():
    """What the page template needs besides the SEO blob. The shipped list,
    and today's board as last looked up: rendering the page never touches the
    database."""
    static = _load_static()
    day, until = _game_clock()
    return {
        'boards_on': GTP_BOARDS_LIVE,
        'first_board': first_paint_board(day),
        'board_day': day.isoformat(),
        'board_until': until,
        'categories': [(cid, label) for cid, label, _, _ in ps.CATEGORIES],
        'bank_size': static.bank.size if static is not None else None,
    }


##### solving #####

def _parse_request(data):
    """(patterns, tried letters, category id) from a POST body."""
    board = data.get('board')
    if not isinstance(board, str) or len(board) > MAX_BOARD_CHARS:
        raise ValidationError(f'board must be text, at most {MAX_BOARD_CHARS} characters')
    try:
        patterns = ps.parse_board(board)
    except ValueError as e:
        raise ValidationError(f'board: {e}')
    if len(patterns) > MAX_WORDS or max(map(len, patterns)) > MAX_WORD_LEN:
        raise ValidationError(f'board must be at most {MAX_WORDS} words of {MAX_WORD_LEN} letters')
    tried = data.get('tried', '')
    if (not isinstance(tried, str) or len(tried) > 26
            or not all(c.isascii() and c.isalpha() for c in tried)):
        raise ValidationError('tried must be letters only')
    category = data.get('category', 'any')
    if category not in ps.CATEGORY_BY_ID:
        raise ValidationError('unknown category')
    return patterns, tried.upper(), category


def _unavailable():
    return jsonify(error='The phrase list is unavailable right now.'), 503


def solve_request(data):
    """The JSON the page renders for one board state."""
    patterns, tried, category = _parse_request(data)
    solver = get_solver()
    if solver is None:
        return _unavailable()
    _, _, tags, mashup = ps.CATEGORY_BY_ID[category]
    res = ps.solve(solver, ' '.join(patterns), tried, tags, mashup)
    return jsonify(
        total=res['total'],
        blanks=res['blanks'],
        advice=res['advice'],
        phrases=[(d, share) for d, _, share in res['candidates']],
        guesses=[(t, share) for t, _, share in res['fallback']],
        words=res['slots'],
    )


##### players' added phrases #####

# "Solved it and we didn't list the phrase? Add it." Two different players
# typing the same phrase adds it (crowd.py, shared with /blossom and /smush;
# never told to players). There is no player flag the other way: phrases are
# only removed by the admin page.
GTP_CROWD = crowd.CrowdList('gtp', on_change=_forget_overlay, window_days=GTP_WINDOW_DAYS)


def _puzzle_tag(category, patterns):
    """Which puzzle a phrase came from, without a date: the category id, a
    colon, then each word's length joined by dashes, so votes on an old
    puzzle still match."""
    return f"{category}:{'-'.join(str(len(p)) for p in patterns)}"


def handle_vote(data):
    """An added phrase posted to /guess_the_phrase. Replies never say
    whether the vote counted or tipped the phrase in (see crowd.py)."""
    if data.get('action') != 'suggest_missing':
        raise ValidationError('unknown action')
    patterns, tried, category = _parse_request(data)
    phrase = data.get('phrase')
    if not isinstance(phrase, str) or len(phrase) > MAX_BOARD_CHARS:
        raise ValidationError(f'phrase must be text, at most {MAX_BOARD_CHARS} characters')
    key = ps.phrase_key(phrase)
    if not key:
        raise ValidationError('phrase must be letters, spaces and punctuation only')
    # Only an answer this board allows: blocks most typos and all junk.
    if not ps.fits_board(key, patterns, ps.excluded_letters(patterns, tried)):
        return jsonify(status='success', result='not_on_board')
    solver = get_solver()
    if solver is None:
        return _unavailable()
    # Same reply as a counted vote, so the filter can't be probed.
    if ps.is_blocked(key, solver.blocklist):
        return jsonify(status='success', result='recorded')
    if key in solver.bank:
        return jsonify(status='success', result='already_listed')
    GTP_CROWD.record_vote(key, 'missing', _puzzle_tag(category, patterns), category)
    return jsonify(status='success', result='recorded')


##### today's board #####

# Everyone gets the same puzzle each day, so the first players tap in its word
# lengths for the players after them, as on /wordiply (James asked,
# 2026-11-24). A board is its word lengths and category, never letters and
# never the phrase: solving it stays the player's. It is logged once a player
# uses the solver on it, and served once James approves it on
# /guess_the_phrase_admin or enough players (one per network) have entered
# the same lengths that day. Players are never told how a board is chosen (see
# crowd.py on why).
#
# The day is the game's, for every player, from the server's clock: the date
# of the puzzle that is live now. Bored Panda's games page stamps each day's
# phrase puzzle with when it goes live: 23:30 in Vilnius on its own date,
# 1:30pm Pacific most of the year (read from the page once, 2026-11-24, for
# this rule only). One puzzle's stamp, so GO_LIVE is the one thing to change
# if it turns out to move.
#
# Every older puzzle has a page of its own, and players catch up on them, so a
# board first seen on an earlier day isn't offered unless James approves it
# for the day (as on /4x3).
GAME_TZ = pytz.timezone('Europe/Vilnius')
GO_LIVE = timedelta(hours=23, minutes=30)

# Players (one per network) on one board that serve it, when nothing is
# approved. Three, not the two /4x3 and /4x6 use: one person with an IPv4 and
# an IPv6 address counts as two.
SERVE_CROWD = 3
# A day counts toward a board's first day only once this many networks logged
# it then, so one person can't date today's board as an old one.
ARCHIVE_MIN_NETWORKS = 2
# How far back an older puzzle is recognised. Two puzzles can share their word
# lengths; the longer the window, the more often a new one is taken for old
# (and then only an approval serves it).
PAST_DAYS = 21
# Boards one player can log on one day: today's, a few typo fixes, and some
# older puzzles.
BOARDS_PER_PLAYER_PER_DAY = 10
# 'typed' = entered by the player; 'edited' = loaded from today's board, then
# changed. A board loaded and left as it is is never logged, so the board on
# offer can't vote for itself.
BOARD_SOURCES = ('typed', 'edited')
# An approval is a row of its own, with this as its source and in place of a
# player hash (a real one is 32 hex characters). Never a player.
APPROVAL = 'admin'
# Live on /guess_the_phrase from launch. False keeps logging boards but offers
# none, even to a direct call.
GTP_BOARDS_LIVE = True
# How long a day's board is reused before it is read again, and how long a
# failed read waits before the next try. One gunicorn worker, so this memo is
# the only copy.
BOARD_CACHE_SECONDS = 60
BOARD_RETRY_SECONDS = 300
# The days the admin tab shows: the game's today and the two before.
ADMIN_DAYS_BACK = 2
# What a JSON body to /guess_the_phrase asks for here, not a solve.
BOARD_ACTIONS = ('log_board', 'todays_board')
# The fewest and most letters a whole board can have.
MIN_TILES, MAX_TILES = 4, 68

# The columns every read of gtp_boards returns, in this order. prefix_hash
# (crowd.prefix_hash) is NULL on approvals.
_COLUMNS = 'shape, category, source, voter_hash, prefix_hash, played_on, created_at, updated_at'
SHAPE, CATEGORY, SOURCE, VOTER, PREFIX, PLAYED_ON, CREATED, UPDATED = range(8)


def _game_now():
    """The time now where the game is run. A name of this module's own, so
    tests can set the clock."""
    return datetime.now(GAME_TZ)


def _game_clock():
    """(the date of the puzzle that is live now, when the next one goes live),
    from one look at the clock, so the two always go together. The second is
    in milliseconds since the epoch, as the page's own clock counts: a page
    left open over it asks again before it offers or loads a board."""
    day = (_game_now().replace(tzinfo=None) - GO_LIVE).date()
    ends = GAME_TZ.localize(datetime.combine(day + timedelta(days=1), datetime.min.time()) + GO_LIVE)
    return day, int(ends.timestamp() * 1000)


def _game_today():
    """The date of the puzzle that is live now."""
    return _game_clock()[0]


def shape_text(lengths):
    """Word lengths as stored: '3-3-4'."""
    return '-'.join(str(n) for n in lengths)


def shape_lengths(shape):
    return [int(n) for n in shape.split('-')]


def _check_lengths(lengths):
    """The word lengths of a whole board, or raise ValidationError."""
    if (not isinstance(lengths, list) or not 1 <= len(lengths) <= MAX_WORDS
            or not all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= MAX_WORD_LEN for n in lengths)
            or not MIN_TILES <= sum(lengths) <= MAX_TILES):
        raise ValidationError(f'lengths must be 1 to {MAX_WORDS} word lengths, {MIN_TILES} to {MAX_TILES} letters in all')
    return lengths


def _check_category(category):
    if not isinstance(category, str) or category not in ps.CATEGORY_BY_ID:
        raise ValidationError('unknown category')
    return category


### storage ###

def _fetch(sql, params):
    with db_cursor() as (conn, cursor):
        cursor.execute(sql, params)
        return cursor.fetchall()


def rows_between(first, last):
    """Every row logged for the game days between these."""
    return _fetch(f"SELECT {_COLUMNS} FROM gtp_boards WHERE played_on BETWEEN %s AND %s", (first, last))


def store_board(day, voter, prefix, shape, category, source):
    """Log one player's use of a board on `day`; the same board again that
    day only updates their row. `voter` is their crowd.player_hash, `prefix`
    their network's crowd.prefix_hash. Inline, not on the write-behind queue:
    a missing table there would drop the rest of its batch, page visits
    included. Best-effort, like a vote."""
    now = pst_now_str()
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute(
                "SELECT COUNT(*) FROM gtp_boards WHERE voter_hash = %s AND played_on = %s AND shape <> %s",
                (voter, day, shape))
            if cursor.fetchone()[0] >= BOARDS_PER_PLAYER_PER_DAY:
                return
            cursor.execute(
                """INSERT INTO gtp_boards (played_on, voter_hash, prefix_hash, shape, category, source,
                       created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE category = new.category, source = new.source,
                       updated_at = new.updated_at, prefix_hash = new.prefix_hash""",
                (day, voter, prefix, shape, category, source, now, now))
            conn.commit()
    except Exception as e:
        alerts.alert_throttled('gtp_board_log_failed', exc=type(e).__name__, msg=e)


def store_approval(day, shape, category):
    """Approve this board for `day`, in place of any other approval that day:
    there is one puzzle a day. Both writes happen or neither. Returns how
    many other approvals it replaced. Raises, unlike store_board: the admin
    page says whether it worked."""
    now = pst_now_str()
    with db_cursor() as (conn, cursor):
        # The Heroku pool runs with autocommit on (config.py), so the two
        # writes need a transaction of their own to be one change.
        cursor.execute("START TRANSACTION")
        try:
            cursor.execute("DELETE FROM gtp_boards WHERE played_on = %s AND source = %s AND shape <> %s",
                           (day, APPROVAL, shape))
            replaced = cursor.rowcount
            cursor.execute(
                """INSERT INTO gtp_boards (played_on, voter_hash, shape, category, source, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE category = new.category, updated_at = new.updated_at""",
                (day, APPROVAL, shape, category, APPROVAL, now, now))
            conn.commit()
        except Exception:
            try:
                cursor.execute("ROLLBACK")
            except Exception:
                pass
            raise
    return replaced


def remove_approval(day):
    """Take back `day`'s approval; players' boards stay. Returns how many
    rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM gtp_boards WHERE played_on = %s AND source = %s", (day, APPROVAL))
        removed = cursor.rowcount
        conn.commit()
    return removed


def remove_entries(shape, day):
    """Delete the players' entries of one board on `day`: test entries
    (staging and local runs write this table too) or junk. An approval
    stays. Returns how many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM gtp_boards WHERE shape = %s AND played_on = %s AND source <> %s",
                       (shape, day, APPROVAL))
        removed = cursor.rowcount
        conn.commit()
    return removed


### what the rows add up to ###

def _networks(rows):
    """One row per network (crowd.one_per_network) of the players' rows."""
    return crowd.one_per_network([r for r in rows if r[SOURCE] in BOARD_SOURCES], VOTER, PREFIX, UPDATED)


def _board_category(rows):
    """The category to serve with a board: James's, else the one the most
    networks chose (not 'any'), a tie going to 'any'."""
    approvals = [r for r in rows if r[SOURCE] == APPROVAL]
    if approvals:
        return max(approvals, key=lambda r: r[UPDATED])[CATEGORY]
    chosen = Counter(r[CATEGORY] for r in _networks(rows).values() if r[CATEGORY] != 'any').most_common(2)
    if not chosen or (len(chosen) > 1 and chosen[0][1] == chosen[1][1]):
        return 'any'
    return chosen[0][0] if chosen[0][0] in ps.CATEGORY_BY_ID else 'any'


def first_seen(rows):
    """The first day one board was the day's puzzle: the earliest James
    approved it for, or that ARCHIVE_MIN_NETWORKS networks entered it on.
    None if neither yet."""
    by_day = defaultdict(list)
    for row in rows:
        by_day[row[PLAYED_ON]].append(row)
    for day in sorted(by_day):
        if (any(r[SOURCE] == APPROVAL for r in by_day[day])
                or len(_networks(by_day[day])) >= ARCHIVE_MIN_NETWORKS):
            return day
    return None


def _starts(shape, longer):
    """Is `shape` the first words of `longer`: half of a board being tapped
    in?"""
    return longer.startswith(shape + '-')


def todays_board(rows, day):
    """The board to offer on `day`, and what the admin tab shows about it.

    rows -- gtp_boards rows for `day` and the PAST_DAYS before it; any others
            are skipped (the admin tab reads several days at once).

    One puzzle a day, so one board is served: `day`'s approval, whatever the
    players entered; otherwise the board the most networks entered that day,
    once it has SERVE_CROWD of them, a tie going to the first to get there.
    Never the first words of a longer board at least as many networks entered
    (half-typed), nor an older puzzle.

    Returns (board, groups): board is {'lengths', 'category'} or None; groups
    is every board logged that day, most players first, each with 'shape',
    'category', 'players', 'approved', 'approved_at', 'first_at', 'last_at',
    'served' and 'why' (None when served, else what it waits for; admin
    wording).
    """
    by_shape = defaultdict(list)
    for row in rows:
        if day - timedelta(days=PAST_DAYS) <= row[PLAYED_ON] <= day:
            by_shape[row[SHAPE]].append(row)

    groups = []
    for shape, all_rows in by_shape.items():
        on_day = [r for r in all_rows if r[PLAYED_ON] == day]
        if not on_day:
            continue
        networks = _networks(on_day)
        # When each network first entered it: created_at, which a later use
        # of the same board leaves alone, so a repeat can't flip a tie.
        first_in = {}
        for r in on_day:
            network = r[PREFIX] or r[VOTER]
            if r[SOURCE] in BOARD_SOURCES and network in networks:
                first_in[network] = min(first_in.get(network, r[CREATED]), r[CREATED])
        times = sorted(first_in.values())
        approvals = [r for r in on_day if r[SOURCE] == APPROVAL]
        seen = first_seen(all_rows)
        why = None
        if not approvals and seen is not None and seen < day:
            why = f'an older puzzle, first seen {seen:%m-%d}'
        groups.append({
            'shape': shape, 'lengths': shape_lengths(shape), 'category': _board_category(on_day),
            'players': len(networks), 'approved': bool(approvals),
            'approved_at': max(r[UPDATED] for r in approvals) if approvals else None,
            'reached_at': times[SERVE_CROWD - 1] if len(times) >= SERVE_CROWD else None,
            'first_at': min(r[CREATED] for r in on_day), 'last_at': max(r[UPDATED] for r in on_day),
            'served': False, 'why': why})
    for g in groups:
        if g['why'] is None and not g['approved'] and any(
                _starts(g['shape'], other['shape']) and other['players'] >= g['players'] for other in groups):
            g['why'] = 'the start of a longer board'
    # Most players first; among equals, the first to reach SERVE_CROWD.
    groups.sort(key=lambda g: (-g['players'], g['reached_at'] or datetime.max, g['shape']))

    approved = [g for g in groups if g['approved']]
    if approved:
        chosen = max(approved, key=lambda g: g['approved_at'])
    else:
        ready = [g for g in groups if g['why'] is None and g['players'] >= SERVE_CROWD]
        chosen = ready[0] if ready else None
    for g in groups:
        if g is chosen:
            g['served'], g['why'] = True, None
        elif g['why'] is None:
            if g['approved'] or g['players'] >= SERVE_CROWD:
                g['why'] = 'another board is served'
            else:
                short = SERVE_CROWD - g['players']
                g['why'] = f"needs {short} more player{'' if short == 1 else 's'}"
    board = {'lengths': chosen['lengths'], 'category': chosen['category']} if chosen is not None else None
    return board, groups


# day -> (expires, board, read): what _board_for last served for that day, or
# None, and which read it came from (reads are numbered as they start).
# time.monotonic() clock.
_board_cache = {}
# n is bumped by every admin change: a read that started before one is used by
# its own request but not kept, since it may predate the change. reads counts
# them.
_board_gen = {'n': 0, 'reads': 0}
_board_lock = threading.Lock()


def _forget_boards():
    """After an admin change: the next request reads the boards again."""
    with _board_lock:
        _board_gen['n'] += 1
        _board_cache.clear()


def _board_for(day):
    """The board offered for `day`, or None. From the memo while fresh, else
    read again; never raises. When an entry goes stale, the first request
    pushes it out 15 seconds before reading, and the others keep the stale
    board meanwhile (as on /wordiply); a failed read keeps it too, and waits
    BOARD_RETRY_SECONDS to try again. The look, the push and the generation
    it reads are one step under the lock, so an admin change can't land
    between them and have the entry it cleared put back.

    With nothing known yet, requests that overlap each read rather than wait
    on a database that may be slow. Whichever order they finish in, the read
    that started last is kept, and a failed one never replaces another's
    board."""
    with _board_lock:
        now = time.monotonic()
        entry = _board_cache.get(day)
        if entry is not None and now < entry[0]:
            return entry[1]
        if entry is not None:
            entry = _board_cache[day] = (now + 15, entry[1], entry[2])
        gen = _board_gen['n']
        _board_gen['reads'] += 1
        read = _board_gen['reads']
    try:
        board = todays_board(rows_between(day - timedelta(days=PAST_DAYS), day), day)[0]
        failed = False
    except Exception as e:
        alerts.alert_throttled('gtp_board_read_failed', exc=type(e).__name__, msg=e)
        board = entry[1] if entry is not None else None
        failed = True
    with _board_lock:
        now = time.monotonic()
        there = _board_cache.get(day)
        if _board_gen['n'] == gen:
            if failed and there is not entry:
                # Another request got through meanwhile: serve what it found.
                board = there[1]
            elif failed:
                _board_cache[day] = (now + BOARD_RETRY_SECONDS, board, entry[2] if entry is not None else 0)
            elif there is None or there[2] < read:
                _board_cache[day] = (now + BOARD_CACHE_SECONDS, board, read)
        for old in [d for d in list(_board_cache) if abs((d - day).days) > 2]:
            _board_cache.pop(old, None)
    return board


def first_paint_board(day):
    """`day`'s board as the last lookup in this process found it, fresh or
    not, for the page to draw its offer as it arrives (one that came later
    would push the page down, a layout shift), or None. Never reads the
    database: the page still asks for the newest."""
    if not GTP_BOARDS_LIVE:
        return None
    entry = _board_cache.get(day)
    return entry[1] if entry is not None else None


### the page's actions ###

def log_board(data):
    """A board a player used, sent once per board and category. Always the
    same reply, stored or not."""
    shape = shape_text(_check_lengths(data.get('lengths')))
    category = _check_category(data.get('category', 'any'))
    source = data.get('source')
    if source not in BOARD_SOURCES:
        raise ValidationError('source must be typed or edited')
    day = _game_today()
    if source == 'edited':
        offered = _board_for(day)
        if offered is not None and shape_text(offered['lengths']) == shape:
            # Changed and then changed back: still the board on offer, which
            # can't vote for itself.
            return jsonify(status='success')
    store_board(day, crowd.player_hash('gtp'), crowd.prefix_hash('gtp'), shape, category, source)
    return jsonify(status='success')


def handle_board_action(data):
    """A board to log, or today's board. Today's board is null while it is
    dark, even to a direct call."""
    if data.get('action') == 'log_board':
        return log_board(data)
    day, until = _game_clock()
    board = _board_for(day) if GTP_BOARDS_LIVE else None
    now_day, now_until = _game_clock()
    if now_day != day:
        # The next puzzle went live during the lookup: its board, not the
        # last one's.
        day, until = now_day, now_until
        board = _board_for(day) if GTP_BOARDS_LIVE else None
    return jsonify(board=board, day=day.isoformat(), until=until)


##### admin #####

NO_CHOICE = 'Choose Remove phrase or Add phrase first'
ADMIN = '/guess_the_phrase_admin'
ADMIN_TABS = ('board', 'reports', 'invalid', 'added')


def _back(**message):
    """Back to the admin page as the admin left it, with a message."""
    return redirect(crowd.admin_return_url(ADMIN, request.form, **message))


def _phrase_status(query):
    """Where the phrase typed in the admin search box stands: is it in the
    solver's list, and where from. None for a search that isn't a phrase
    (empty, a board tag with a colon, or not letters).

    Read from the solver itself, so 'offered' is exactly what players get.
    """
    if not query or ':' in query:
        return None
    key = ps.phrase_key(query)
    if not key:
        return None
    solver = get_solver()
    if solver is None:
        return {'key': key, 'unavailable': True}
    patterns = tuple(key.split(' '))
    shipped = next((r for r in _load_static().bank.search(patterns, set(), limit=1)
                    if r[0] == key), None)
    status = {
        'key': key,
        'offered': key in solver.bank,
        'removed': key in solver.bank.removed,
        'added': any(r[0] == key for r in solver.bank.extra.search(patterns, set(), limit=1)),
        'shipped': None,
    }
    if shipped:
        _, display, pop, tags = shipped
        # Not 'pop': in the template, shipped.pop would be the dict's method.
        status['shipped'] = {
            'display': display if display != key else None,
            'popularity': pop,
            'tags': [t.lower() for t in ps.TAGS if tags & ps.TAG_BIT[t]],
        }
    return status


@bp.route(ADMIN)
@auth.login_required
def gtp_admin():
    """Today's board, players' added phrases and the admin's own changes to
    the list. The boards and the phrase lists each still show if the other's
    tables can't be read."""
    args = request.args.to_dict()
    # A search is for the phrase lists, so it opens on them.
    args.setdefault('tab', 'reports' if args.get('q') else 'board')
    try:
        view, crowd_error = GTP_CROWD.admin_view(args, request.path, tabs=ADMIN_TABS), None
    except Exception as e:
        print(f"Error loading guess the phrase admin lists: {e}")
        view = GTP_CROWD.empty_view(ADMIN, args['tab'] if args['tab'] in ADMIN_TABS else 'board')
        crowd_error = f'{type(e).__name__}: {e}'
    days, load_error = _admin_days()
    return render_template('guess_the_phrase_admin.html', **view, crowd_error=crowd_error,
                           phrase=_phrase_status(view['query']), days=days, load_error=load_error,
                           boards_live=GTP_BOARDS_LIVE, game_today=_game_today(), serve_crowd=SERVE_CROWD,
                           past_days=PAST_DAYS, categories=[(cid, label) for cid, label, _, _ in ps.CATEGORIES],
                           category_names={cid: label for cid, label, _, _ in ps.CATEGORIES})


def _admin_days():
    """(days, error): each recent day's boards, today first, each with its
    players and what is served."""
    today = _game_today()
    first = today - timedelta(days=ADMIN_DAYS_BACK)
    try:
        rows = rows_between(first - timedelta(days=PAST_DAYS), today)
    except Exception as e:
        print(f"Error loading guess the phrase admin boards: {e}")
        return [], f'{type(e).__name__}: {e}'
    days = []
    day = today
    while day >= first:
        board, groups = todays_board(rows, day)
        if groups:
            days.append({'day': day, 'board': board, 'groups': groups})
        day -= timedelta(days=1)
    return days, None


def _typed_lengths(text):
    """Word lengths from an admin form: '3 3 4' (or 3-3-4, or 3,3,4), or the
    board as it looks ('TH? B?G ?AME'), whose letters are dropped."""
    text = text.strip()
    if not text or len(text) > MAX_BOARD_CHARS:
        raise ValidationError('type each word length, like 3 3 4')
    if re.fullmatch(r'[0-9\s,\-]+', text):
        return _check_lengths([int(n) for n in re.findall(r'[0-9]+', text)])
    try:
        return _check_lengths([len(w) for w in ps.parse_board(text)])
    except ValueError:
        raise ValidationError('type each word length, like 3 3 4')


@bp.route(ADMIN + '/approve_board', methods=['POST'])
@auth.login_required
def gtp_approve_board():
    """Serve this board to every player on `played_on`, straight away and
    whatever the players entered: the approve form, or Approve on a board
    players entered. Approving another board that day replaces it."""
    day = parse_iso_day(request.form.get('played_on', ''))
    if day is None:
        return _back(error='Not a date')
    try:
        lengths = _typed_lengths(request.form.get('lengths', ''))
        category = _check_category(request.form.get('category', ''))
    except ValidationError as e:
        return _back(error=f'Not a board: {e}')
    try:
        replaced = store_approval(day, shape_text(lengths), category)
    except Exception as e:
        print(f"Error approving a guess the phrase board: {e}")
        return _back(error='Database error')
    _forget_boards()
    instead = ', in place of your earlier approval that day' if replaced else ''
    return _back(success=f"Approved {' '.join(map(str, lengths))} for {day:%m-%d}{instead}")


@bp.route(ADMIN + '/unapprove_board', methods=['POST'])
@auth.login_required
def gtp_unapprove_board():
    """Undo one day's approval: the players' boards decide again, and none of
    them is touched."""
    day = parse_iso_day(request.form.get('played_on', ''))
    if day is None:
        return _back(error='Not a date')
    try:
        removed = remove_approval(day)
    except Exception as e:
        print(f"Error undoing a guess the phrase approval: {e}")
        return _back(error='Database error')
    _forget_boards()
    if not removed:
        return _back(error=f'Nothing was approved for {day:%m-%d}')
    return _back(success="Approval undone: the players' boards decide again")


@bp.route(ADMIN + '/remove_board', methods=['POST'])
@auth.login_required
def gtp_remove_board():
    """Delete the players' entries of one board on one day: test entries or
    junk. An approval stays."""
    day = parse_iso_day(request.form.get('played_on', ''))
    try:
        lengths = _typed_lengths(request.form.get('lengths', ''))
    except ValidationError:
        lengths = None
    if day is None or lengths is None:
        return _back(error='Not a board')
    try:
        removed = remove_entries(shape_text(lengths), day)
    except Exception as e:
        print(f"Error removing a guess the phrase board: {e}")
        return _back(error='Database error')
    _forget_boards()
    return _back(success=f"Removed {removed} entr{'y' if removed == 1 else 'ies'} "
                         f"of {' '.join(map(str, lengths))} on {day:%m-%d}")


@bp.route(ADMIN + '/add_word', methods=['POST'])
@auth.login_required
def gtp_add_word():
    """Take a phrase out of the list ('invalid') or put one in ('missing')."""
    key, kind = crowd.admin_form(ps.phrase_key)
    if kind is None:
        return _back(error=NO_CHOICE)
    if not key:
        return _back(error='Phrase must be letters and spaces')
    if kind == 'missing':
        status = _phrase_status(key)
        # Adding a shipped phrase isn't a no-op: the added copy scores
        # ADDED_POP in every category, so it would also surface in
        # categories the phrase isn't tagged for.
        if status and status.get('shipped') and not status['removed']:
            return _back(error=f'{key} is already in the shipped phrase list - nothing changed')
    try:
        GTP_CROWD.admin_set(key, kind)
    except Exception as e:
        print(f"Error adding guess the phrase phrase: {e}")
        return _back(error='Database error')
    done = 'added to the phrase list' if kind == 'missing' else 'removed from the phrase list'
    return _back(success=f'{key} {done}')


@bp.route(ADMIN + '/remove_word', methods=['POST'])
@auth.login_required
def gtp_remove_word():
    """Undo an entry in either list, and reset that phrase's votes."""
    key, kind = crowd.admin_form(ps.phrase_key)
    if kind is None:
        return _back(error=NO_CHOICE)
    if not key:
        return _back(error='Phrase must be letters and spaces')
    try:
        GTP_CROWD.admin_remove(key, kind)
    except Exception as e:
        print(f"Error removing guess the phrase phrase: {e}")
        return _back(error='Database error')
    return _back(success=f'{key} undone')
