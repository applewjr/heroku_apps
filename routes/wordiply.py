"""Wordiply's today's starter word, its word corrections, and /wordiply_admin.

The /wordiply page is in routes/wordgames.py; its searches (search) and JSON
actions (a starter to log, today's starter to fetch, a word Wordiply refused)
come here.

Wordiply gives every player the same starter word each day, so the first
players each day do the typing for the players after them, as on /blossom:
a starter is logged once a player uses the solver on it, and once enough
players (one per network) have entered the same one, later players are
offered it ("Today's starter word:", with a Load it button that fills the
box and lists the words). Nothing is guessed: every starter served was
typed by players, or approved by James on /wordiply_admin. His approvals
are optional (James, 2026-11-22): the players' starters serve on their own,
so Wordiply needs no daily visit.

The day is Wordiply's: the date in Europe/London, for every player. The
game numbers its puzzles by London days (checked in its page code,
2026-11-22), so a new starter comes at London midnight, 4pm Pacific most of
the year. It is the server's clock, not the player's. Wordiply has no
archive, so there is no older-puzzle rule.

Players are never told how a starter is chosen (see crowd.py on why).
"""

import threading
import time
from datetime import datetime, timedelta

import pytz
from flask import Blueprint, jsonify, redirect, render_template, request

import crowd
from data import words
from extensions import auth, db_cursor, pst_now_str
from functions import all_words
from helpers import ValidationError, parse_iso_day
from monitoring import alerts

bp = Blueprint('wordiply', __name__)

# Wordiply's calendar (see the module docstring).
GAME_TZ = pytz.timezone('Europe/London')


def _game_now():
    """The time now in Wordiply's calendar. A name of this module's own, so
    tests can set the clock."""
    return datetime.now(GAME_TZ)


def _game_today():
    """Today's date in Wordiply's calendar."""
    return _game_now().date()


# Players (one per network) on one starter that serve it, when nothing is
# approved. Chainle's value: there was no Wordiply typing to replay before
# launch (the page logged none), so this is to recalibrate from
# wordiply_boards about two weeks after deploy.
SERVE_CROWD = 3
# Starters one player can log on one day: typo fixes and a few strings
# tried for longer words, with room to spare.
STARTERS_PER_PLAYER_PER_DAY = 10
# 'typed' = entered by the player; 'edited' = loaded from today's starter,
# then changed. A starter loaded and left as it is is never logged, so the
# starter on offer can't vote for itself.
BOARD_SOURCES = ('typed', 'edited')
# An approval is a row of its own, with this as its source and in place of
# a player hash (a real one is 32 hex characters). Never a player.
APPROVAL = 'admin'
# Live on /wordiply from launch. False keeps logging starters but offers
# none, even to a direct call.
WORDIPLY_STARTER_LIVE = True
# How long a day's starter is reused before it is read again, and how long
# a failed read waits before the next try. One gunicorn worker, so this
# memo is the only copy.
CACHE_SECONDS = 60
RETRY_SECONDS = 300
# The days /wordiply_admin shows: the game's today and the two before.
ADMIN_DAYS_BACK = 2
# What a starter can be: letters a-z, this many.
MIN_LEN, MAX_LEN = 2, 15
# What a JSON body to /wordiply asks for here, not a search.
ACTIONS = ('log_starter', 'todays_starter', 'reject', 'restore')
# How many words a search lists.
LIST_LEN = 90

# The columns every read of wordiply_boards returns, in this order.
# prefix_hash (crowd.prefix_hash) is NULL on approvals.
_COLUMNS = 'letters, source, voter_hash, prefix_hash, played_on, created_at, updated_at'
LETTERS, SOURCE, VOTER, PREFIX, PLAYED_ON, CREATED, UPDATED = range(7)


def parse_starter(value):
    """A starter word as the page or a form sends it, lowercase: MIN_LEN to
    MAX_LEN letters a-z. Anything else raises ValidationError (-> 400)."""
    if not isinstance(value, str):
        raise ValidationError('letters must be a starter word')
    letters = value.strip().lower()
    if not (MIN_LEN <= len(letters) <= MAX_LEN and letters.isascii() and letters.isalpha()):
        raise ValidationError(f'a starter word is {MIN_LEN}-{MAX_LEN} letters a-z')
    return letters


##### storage #####

def _fetch(sql, params):
    with db_cursor() as (conn, cursor):
        cursor.execute(sql, params)
        return cursor.fetchall()


def rows_between(first, last):
    """Every row logged for the days between these (Wordiply's calendar)."""
    return _fetch(f"SELECT {_COLUMNS} FROM wordiply_boards WHERE played_on BETWEEN %s AND %s",
                  (first, last))


def store_starter(day, voter, prefix, letters, source):
    """Log one player's use of a starter on `day`; the same starter again
    that day only updates their row. `voter` is their crowd.player_hash,
    `prefix` their network's crowd.prefix_hash. Inline, not on the
    write-behind queue: a missing table there would drop the rest of its
    batch, page visits included. Best-effort, like a vote."""
    now = pst_now_str()
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute(
                "SELECT COUNT(*) FROM wordiply_boards "
                "WHERE voter_hash = %s AND played_on = %s AND letters <> %s",
                (voter, day, letters))
            if cursor.fetchone()[0] >= STARTERS_PER_PLAYER_PER_DAY:
                return
            cursor.execute(
                """INSERT INTO wordiply_boards (played_on, voter_hash, prefix_hash, letters, source,
                       created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE source = new.source, updated_at = new.updated_at,
                       prefix_hash = new.prefix_hash""",
                (day, voter, prefix, letters, source, now, now))
            conn.commit()
    except Exception as e:
        alerts.alert_throttled('wordiply_board_log_failed', exc=type(e).__name__, msg=e)


def store_approval(day, letters):
    """Approve this starter for `day`, in place of any other approval that
    day: Wordiply has one starter a day. Both writes happen or neither.
    Returns how many other approvals it replaced. Raises, unlike
    store_starter: the admin page says whether it worked."""
    now = pst_now_str()
    with db_cursor() as (conn, cursor):
        # The Heroku pool runs with autocommit on (config.py), so the two
        # writes need a transaction of their own to be one change.
        cursor.execute("START TRANSACTION")
        try:
            cursor.execute(
                "DELETE FROM wordiply_boards WHERE played_on = %s AND source = %s AND letters <> %s",
                (day, APPROVAL, letters))
            replaced = cursor.rowcount
            cursor.execute(
                """INSERT INTO wordiply_boards (played_on, voter_hash, letters, source, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE updated_at = new.updated_at""",
                (day, APPROVAL, letters, APPROVAL, now, now))
            conn.commit()
        except Exception:
            try:
                cursor.execute("ROLLBACK")
            except Exception:
                pass
            raise
    return replaced


def remove_approval(day):
    """Take back `day`'s approval; players' starters stay. Returns how many
    rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM wordiply_boards WHERE played_on = %s AND source = %s",
                       (day, APPROVAL))
        removed = cursor.rowcount
        conn.commit()
    return removed


def remove_entries(letters, day):
    """Delete the players' entries of one starter on `day`: test entries
    (staging and local runs write this table too) or junk. An approval
    stays. Returns how many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute(
            "DELETE FROM wordiply_boards WHERE letters = %s AND played_on = %s AND source <> %s",
            (letters, day, APPROVAL))
        removed = cursor.rowcount
        conn.commit()
    return removed


##### what the rows add up to #####

def consensus(rows):
    """What one starter's rows on one day add up to.

    rows -- wordiply_boards rows (see _COLUMNS) sharing one `letters` and
            one day. Each player's latest row counts, and players on one
            network (crowd.prefix_hash) count once.

    Returns a dict: players (networks), reached_at (when its SERVE_CROWD-th
    network first entered it, or None), approved / approved_at, first_at and
    last_at.
    """
    approvals = [row for row in rows if row[SOURCE] == APPROVAL]
    player_rows = [row for row in rows if row[SOURCE] in BOARD_SOURCES]
    networks = crowd.one_per_network(player_rows, VOTER, PREFIX, UPDATED)
    # When each network first entered this starter: created_at, which a
    # later use of the same starter (from another tab, say) leaves alone,
    # unlike updated_at. So a repeat can't change which starter reached
    # SERVE_CROWD first, or flip a tie.
    first_in = {}
    for row in player_rows:
        network = row[PREFIX] or row[VOTER]
        if network in networks:
            first_in[network] = min(first_in.get(network, row[CREATED]), row[CREATED])
    times = sorted(first_in.values())
    return {
        'players': len(networks),
        'reached_at': times[SERVE_CROWD - 1] if len(times) >= SERVE_CROWD else None,
        'approved': bool(approvals),
        'approved_at': max(row[UPDATED] for row in approvals) if approvals else None,
        'first_at': min(row[CREATED] for row in rows),
        'last_at': max(row[UPDATED] for row in rows),
    }


def todays_starter(rows, day):
    """The starter word to offer on `day`, and what /wordiply_admin shows
    about that day.

    rows -- wordiply_boards rows; only `day`'s count.

    Wordiply has one starter a day, so one is served: `day`'s approval,
    whatever the players typed; otherwise the starter the most networks
    entered that day, once it has SERVE_CROWD of them, a tie going to the
    first to get there. A starter that a longer one at least as many
    networks entered begins with is never served on its own: it's
    half-typed (RO, when the players' starter is ROW), as on /typeshift.

    Returns (starter, groups): starter is its letters, or None; groups is
    every starter logged that day with its consensus, 'letters', 'served'
    and 'why' (None when served, else what it waits for; admin wording),
    most players first.
    """
    by_letters = {}
    for row in rows:
        if row[PLAYED_ON] == day:
            by_letters.setdefault(row[LETTERS], []).append(row)

    groups = []
    for letters, group_rows in by_letters.items():
        result = consensus(group_rows)
        result.update(letters=letters, served=False, why=None)
        groups.append(result)
    for g in groups:
        if not g['approved'] and any(
                len(other['letters']) > len(g['letters']) and other['letters'].startswith(g['letters'])
                and other['players'] >= g['players'] for other in groups):
            g['why'] = 'the start of a longer starter'
    # Most players first; among equals, the first to reach SERVE_CROWD.
    groups.sort(key=lambda g: (-g['players'], g['reached_at'] or datetime.max, g['letters']))

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
                g['why'] = 'another starter is served'
            else:
                short = SERVE_CROWD - g['players']
                g['why'] = f"needs {short} more player{'' if short == 1 else 's'}"
    return (chosen['letters'] if chosen is not None else None), groups


# day -> (expires, starter): what _starter_for last served for that date,
# or None. time.monotonic() clock.
_starter_cache = {}
# Bumped by every admin change: a read that started before one is used by
# its own request but not kept, since it may predate the change.
_cache_gen = {'n': 0}
_cache_lock = threading.Lock()


def _forget_starters():
    """After an admin change: the next request reads the starters again."""
    with _cache_lock:
        _cache_gen['n'] += 1
        _starter_cache.clear()


def _starter_for(day):
    """The starter offered for `day`, or None. From the memo while fresh,
    else read again; never raises. When an entry goes stale, the first
    request pushes it out 15 seconds before reading, and the others keep
    the stale starter meanwhile (as on /typeshift); a failed read keeps it
    too, and waits RETRY_SECONDS to try again. The look, the push and the
    generation it reads are one step under the lock, so an admin change
    can't land between them and have the entry it cleared put back."""
    with _cache_lock:
        now = time.monotonic()
        entry = _starter_cache.get(day)
        if entry is not None and now < entry[0]:
            return entry[1]
        if entry is not None:
            _starter_cache[day] = (now + 15, entry[1])
        gen = _cache_gen['n']
    try:
        starter = todays_starter(rows_between(day, day), day)[0]
        expires = time.monotonic() + CACHE_SECONDS
    except Exception as e:
        alerts.alert_throttled('wordiply_board_read_failed', exc=type(e).__name__, msg=e)
        starter = entry[1] if entry is not None else None
        expires = time.monotonic() + RETRY_SECONDS
    with _cache_lock:
        if _cache_gen['n'] == gen:
            _starter_cache[day] = (expires, starter)
        for old in [d for d in list(_starter_cache) if abs((d - day).days) > 2]:
            _starter_cache.pop(old, None)
    return starter


def first_paint_starter():
    """Today's starter as the last lookup in this process found it, fresh or
    not, for the page to draw its offer as it arrives (one that came later
    would push the box down, a layout shift), or None. Never reads the
    database: the page still asks for the newest."""
    if not WORDIPLY_STARTER_LIVE:
        return None
    entry = _starter_cache.get(_game_today())
    return entry[1] if entry is not None else None


def page_context():
    """What /wordiply's GET render needs from here."""
    return {'starter_on': WORDIPLY_STARTER_LIVE, 'first_starter': first_paint_starter(),
            'starter_day': _game_today().isoformat()}


##### word corrections #####

# Players flag a word Wordiply refused with the ✗ on its row, which asks
# first, as on /ribbit and /smush (James asked, 2026-11-24). The mechanics,
# the thresholds (fewer players for a rarer word) and the rule that players
# are never told the counts live in crowd.py, shared with the other word
# games; only Wordiply's own tables are touched here. Players can't suggest
# missing words. /wordiply_admin's Add ('missing' in crowd.py) puts a word in
# the list, or keeps one there whatever players say.

# (removed, added) word sets, memoized in the module. Not a corrected copy of
# the word list: the search applies them as it goes (memory).
_corrections = {'value': None, 'expires': 0.0}


def _forget_corrections():
    """Make the next get_corrections() re-read the lists."""
    _corrections['expires'] = 0.0


def get_corrections():
    """(removed, added): the words players or /wordiply_admin took off the
    list, and the ones the admin put on it, lower case. Refreshed every 12
    hours, or straight away after a change; none while the DB has never been
    reachable (see crowd.CrowdList.corrected)."""
    return WORDIPLY_CROWD.corrected(
        _corrections, lambda removed, added: (frozenset(removed) - frozenset(added), frozenset(added)))


WORDIPLY_CROWD = crowd.CrowdList('wordiply', on_change=_forget_corrections)


def search(search_string):
    """The words a search lists, longest first."""
    removed, added = get_corrections()
    return all_words.wordiply_solver(search_string, words, LIST_LEN, removed, added)


def _puzzle_tag(letters):
    """The starter a report came from, with a colon so the admin's board
    search finds it."""
    return f'starter:{letters}'


def handle_vote(data):
    """A ✗ on a word Wordiply refused ('reject'), or that ✗ taken back
    ('restore'). Every reply is the same whether or not it counted (see
    crowd.py). It only counts for a word this starter lists."""
    letters = parse_starter(data.get('letters'))
    word = data.get('word')
    if not isinstance(word, str) or len(word) > 50 or not (word.isascii() and word.isalpha()):
        raise ValidationError('word must be one word, letters only')
    word = word.lower()
    if data.get('action') == 'restore':
        WORDIPLY_CROWD.retract_vote(word, 'invalid', _puzzle_tag(letters))
        return jsonify(status='success')
    removed, added = get_corrections()
    listed = (word in words or word in added) and word not in removed
    if listed and letters in word and word != letters:
        WORDIPLY_CROWD.record_vote(word, 'invalid', _puzzle_tag(letters))
    return jsonify(status='success')


##### the page's actions #####

def log_starter(data):
    """A starter a player used, sent once per string. Always the same reply,
    stored or not."""
    letters = parse_starter(data.get('letters'))
    source = data.get('source')
    if source not in BOARD_SOURCES:
        raise ValidationError('source must be typed or edited')
    day = _game_today()
    if source == 'edited' and letters == _starter_for(day):
        # Changed and then changed back: still the starter on offer, which
        # can't vote for itself.
        return jsonify(status='success')
    store_starter(day, crowd.player_hash('wordiply'), crowd.prefix_hash('wordiply'), letters, source)
    return jsonify(status='success')


def handle_action(data):
    """A JSON body posted to /wordiply with an `action`: a starter to log,
    today's starter, or a word report. Today's starter is null while it is
    dark, even to a direct call."""
    action = data.get('action')
    if action in ('reject', 'restore'):
        return handle_vote(data)
    if action == 'log_starter':
        return log_starter(data)
    if action == 'todays_starter':
        day = _game_today()
        return jsonify(starter=_starter_for(day) if WORDIPLY_STARTER_LIVE else None,
                       day=day.isoformat())
    raise ValidationError('unknown action')


##### admin #####

ADMIN = '/wordiply_admin'
ADMIN_TABS = ('starter', 'reports', 'invalid', 'added')


def _back(**message):
    """Back to /wordiply_admin, with a message."""
    return redirect(crowd.admin_return_url(ADMIN, request.form, **message))


def _admin_days():
    """(days, error): each recent day's starters, today first, each with
    its players and what is served."""
    today = _game_today()
    first = today - timedelta(days=ADMIN_DAYS_BACK)
    try:
        rows = rows_between(first, today)
    except Exception as e:
        print(f"Error loading wordiply admin starters: {e}")
        return [], f'{type(e).__name__}: {e}'
    days = []
    day = today
    while day >= first:
        starter, groups = todays_starter(rows, day)
        if groups:
            days.append({'day': day, 'starter': starter, 'groups': groups})
        day -= timedelta(days=1)
    return days, None


@bp.route(ADMIN)
@auth.login_required
def wordiply_admin():
    """The recent days' starter words (what players entered, what is served,
    and James's approvals), what players reported, and the removed and added
    words. Each half still shows if the other's tables can't be read."""
    args = request.args.to_dict()
    args.setdefault('tab', 'starter')
    try:
        view, crowd_error = WORDIPLY_CROWD.admin_view(args, request.path, tabs=ADMIN_TABS), None
    except Exception as e:
        print(f"Error loading wordiply admin word lists: {e}")
        view = WORDIPLY_CROWD.empty_view(ADMIN, args['tab'] if args['tab'] in ADMIN_TABS else 'starter')
        crowd_error = f'{type(e).__name__}: {e}'
    days, load_error = _admin_days()
    return render_template('wordiply_admin.html', **view, crowd_error=crowd_error,
                           days=days, load_error=load_error,
                           starter_live=WORDIPLY_STARTER_LIVE, game_today=_game_today(),
                           serve_crowd=SERVE_CROWD, min_len=MIN_LEN, max_len=MAX_LEN)


def _form_starter():
    """(letters, day) from an admin form, or None if it isn't one."""
    day = parse_iso_day(request.form.get('played_on', ''))
    try:
        letters = parse_starter(request.form.get('letters', ''))
    except ValidationError:
        return None
    return (letters, day) if day is not None else None


@bp.route(ADMIN + '/approve', methods=['POST'])
@auth.login_required
def wordiply_approve():
    """Serve this starter to every player on `played_on`, straight away and
    whatever the players typed: the approve form, or Add on a starter
    players entered. Approving another starter that day replaces it."""
    form = _form_starter()
    if form is None:
        return _back(error=f'Not a starter: {MIN_LEN}-{MAX_LEN} letters a-z, and a date')
    letters, day = form
    try:
        replaced = store_approval(day, letters)
    except Exception as e:
        print(f"Error approving wordiply starter: {e}")
        return _back(error='Database error')
    _forget_starters()
    instead = ', in place of your earlier approval that day' if replaced else ''
    return _back(success=f"Approved {letters.upper()} for {day:%m-%d}{instead}")


@bp.route(ADMIN + '/unapprove', methods=['POST'])
@auth.login_required
def wordiply_unapprove():
    """Undo one day's approval: the players' starters decide again, and none
    of them is touched."""
    day = parse_iso_day(request.form.get('played_on', ''))
    if day is None:
        return _back(error='Not a date')
    try:
        removed = remove_approval(day)
    except Exception as e:
        print(f"Error undoing wordiply approval: {e}")
        return _back(error='Database error')
    _forget_starters()
    if not removed:
        return _back(error=f'Nothing was approved for {day:%m-%d}')
    return _back(success="Approval undone: the players' starters decide again")


@bp.route(ADMIN + '/remove', methods=['POST'])
@auth.login_required
def wordiply_remove():
    """Delete the players' entries of one starter on one day: test entries
    or junk. An approval stays."""
    form = _form_starter()
    if form is None:
        return _back(error='Not a starter')
    letters, day = form
    try:
        removed = remove_entries(letters, day)
    except Exception as e:
        print(f"Error removing wordiply starter: {e}")
        return _back(error='Database error')
    _forget_starters()
    return _back(success=f"Removed {removed} entr{'y' if removed == 1 else 'ies'} "
                         f"of {letters.upper()} on {day:%m-%d}")


@bp.route(ADMIN + '/add_word', methods=['POST'])
@auth.login_required
def wordiply_add_word():
    """Remove a word from Wordiply's list ('invalid') or add one, or keep one
    there ('missing')."""
    return crowd.admin_add_word(WORDIPLY_CROWD, _back)


@bp.route(ADMIN + '/remove_word', methods=['POST'])
@auth.login_required
def wordiply_remove_word():
    """Undo an entry in either list, and reset that word's votes."""
    return crowd.admin_undo_word(WORDIPLY_CROWD, _back)
