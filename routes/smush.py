"""Smush word list, crowd word corrections, today's board, and /smush_admin.

The solver routes themselves are run_smush and run_smush_dev2 in
routes/wordgames.py; they hand any JSON body carrying an `action` to
handle_board_action or handle_vote here.
"""

import time
from collections import Counter
from datetime import date, timedelta

from flask import Blueprint, jsonify, redirect, render_template, request

import crowd
from crowd import (CROWD_ADD_VOTES, CROWD_REMOVE_VOTES_BY_POP, CROWD_REMOVE_VOTES_RARE,
                   CROWD_WINDOW_DAYS)
from data import word_pop, words
from extensions import auth, db_cursor, pst_now_str
from functions import all_words
from helpers import ISO_DATE as _DATE
from helpers import ValidationError, parse_letters, parse_played_on
# _pacific_today is a name of this module's own so tests can set the day.
from helpers import pacific_today as _pacific_today
from monitoring import alerts

bp = Blueprint('smush', __name__)


##### word list #####

# Memoized in the module (not SimpleCache: that pickles, so every cache.get
# would rebuild the ~17MB set per request). Holds at most one extra copy of
# the word list per process, and none at all while Smush's corrections are
# empty.
_smush_words = {'value': None, 'expires': 0.0}


def _forget_smush_words():
    """Make the next get_smush_words() re-read the corrections, and the next
    today's board recompute its hints, which come from them."""
    _smush_words['expires'] = 0.0
    _board_cache.clear()


def get_smush_words():
    """The full word list with Smush's own corrections applied: words removed
    by players or /smush_admin taken out, words added put in.

    Only smush_* tables. Blossom's corrections stay in Blossom: until
    2026-10-04 this read blossom_invalid_words / blossom_added_words, which
    James never meant to reach Smush. Falls back to the raw list if the DB is
    unreachable. Refreshed every 12 hours, or straight away after a change.
    """
    if _smush_words['value'] is not None and time.time() < _smush_words['expires']:
        return _smush_words['value']

    invalid_words, added_words = set(), set()
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute("SELECT word FROM smush_invalid_words")
            invalid_words = {row[0].lower() for row in cursor.fetchall()}
            cursor.execute("SELECT word FROM smush_added_words")
            added_words = {row[0].lower() for row in cursor.fetchall()}
    except Exception as e:
        print(f"Error fetching smush word corrections: {e}")
        # A DB blip at refresh time must not evict the corrections for 12
        # hours: keep serving the last good list (or the raw list if there
        # has never been one) and retry soon.
        fallback = _smush_words['value'] if _smush_words['value'] is not None else words
        _smush_words['value'] = fallback
        _smush_words['expires'] = time.time() + 300
        return fallback

    # data.py's word set is already lowercase; only build a corrected copy
    # when there is actually something to correct.
    if invalid_words or added_words:
        smush_words = (words - invalid_words) | added_words
    else:
        smush_words = words

    _smush_words['value'] = smush_words
    _smush_words['expires'] = time.time() + 43200
    return smush_words


##### crowd corrections #####

# Players fix the word list themselves: the ✗ on a result ("Smush didn't
# accept this word") votes it out, and the "Smush accepted a word that isn't
# listed?" box under the results votes one in. Mechanics, thresholds and the
# never-tell-players rule live in crowd.py, shared with /blossom; only Smush's
# own tables are touched here.
#
# ✗ was already there before it voted: it hides a word from this player's
# results at no cost, and the All 8 plan re-plans around it. So some players
# will use it to steer the plan rather than to report a refusal. The
# per-player daily limit in crowd.py catches the heavy cases; the first ✗ in a
# browser also asks players to keep it for refusals.

SMUSH_CROWD = crowd.CrowdList('smush', on_change=_forget_smush_words)

# Smush's own word lengths, same as the played-word box on the page.
SMUSH_MIN_LEN, SMUSH_MAX_LEN = 3, 15


def _parse_word(value):
    word = parse_letters(value if isinstance(value, str) else None, 'word',
                         max_len=SMUSH_MAX_LEN).lower()
    if len(word) < SMUSH_MIN_LEN:
        raise ValidationError(f"word must be at least {SMUSH_MIN_LEN} letters")
    return word


def _parse_board(data):
    """The board a vote came from: (center, outer), lowercase.

    The page sends its nine tiles, so anything other than one center letter
    and eight distinct others is not the page talking.
    """
    center, outer = data.get('center'), data.get('outer')
    center = parse_letters(center if isinstance(center, str) else None, 'center', max_len=1).lower()
    outer = parse_letters(outer if isinstance(outer, str) else None, 'outer', max_len=8).lower()
    if len(center) != 1 or len(outer) != 8 or len(set(outer)) != 8 or center in outer:
        raise ValidationError("center must be 1 letter and outer 8 other distinct letters")
    return center, outer


def _fits_board(word, center, outer):
    """Could this word be played on this board, by Smush's own rules?
    (Letter wear aside: a word can be refused or accepted at any point.)"""
    return center in word and set(word) <= set(center + outer)


def _puzzle_tag(center, outer):
    return center + ':' + ''.join(sorted(outer))


SMUSH_VOTE_ACTIONS = ('reject', 'restore', 'suggest_missing')
# The results tabs, as SORT_MODES in smush.html and smush_dev.html. A ✗ on
# the All 8 tab also re-plans, so some of those are steering, not refusals.
SMUSH_RESULT_TABS = ('pts', 'eff', 'all8', 'pan')


def _vote_mode(data, dev):
    """The tab the player voted from, for the vote log only: 'dev_<tab>'
    from /smush_dev, plus '_ice' with Ice Cold on. None when the page didn't
    say (one loaded before it started saying) or said something else - never
    a reason to refuse the vote."""
    tab = data.get('mode')
    if tab not in SMUSH_RESULT_TABS:
        return None
    if dev:
        return 'dev_' + tab + ('_ice' if data.get('ice_cold') is True else '')
    return tab


def handle_vote(data, dev=False):
    """A crowd vote posted to /smush, or to /smush_dev with `dev`. Every reply
    is the same whether or not the vote changed the list (see crowd.py)."""
    action = data.get('action')
    if action not in SMUSH_VOTE_ACTIONS:
        raise ValidationError('unknown action')
    word = _parse_word(data.get('word'))
    center, outer = _parse_board(data)
    mode = _vote_mode(data, dev)

    if action == 'reject':
        # Only a word actually on offer, on a board it fits: anything else is
        # not a result this player could have been shown.
        if _fits_board(word, center, outer) and word in get_smush_words():
            SMUSH_CROWD.record_vote(word, 'invalid', _puzzle_tag(center, outer), mode)
        return jsonify({'status': 'success'})

    if action == 'restore':
        # Tapping a hidden word back: takes back this player's ✗, if it
        # counted.
        SMUSH_CROWD.retract_vote(word, 'invalid', _puzzle_tag(center, outer), mode)
        return jsonify({'status': 'success'})

    if not _fits_board(word, center, outer):
        return jsonify({'status': 'success', 'result': 'not_in_puzzle'})
    if word in get_smush_words():
        return jsonify({'status': 'success', 'result': 'already_listed'})
    SMUSH_CROWD.record_vote(word, 'missing', _puzzle_tag(center, outer), mode)
    return jsonify({'status': 'success', 'result': 'recorded'})


##### admin #####

def _admin_form():
    """The word and its list, or None for the list if no choice was sent.
    Nothing is assumed: a missing choice must not count as a removal."""
    word = request.form.get('word', '').strip().lower()
    kind = request.form.get('word_type')
    return word, kind if kind in ('invalid', 'missing') else None


NO_CHOICE = 'Choose Remove invalid word or Add missing word first'


ADMIN = '/smush_admin'
# Today's Board first: approving the day's board is the daily visit.
ADMIN_TABS = ('board', 'reports', 'invalid', 'added')


def _back(**message):
    """Back to /smush_admin as the admin left it, with a message."""
    return redirect(crowd.admin_return_url(ADMIN, request.form, **message))


def _empty_view(active_tab):
    """The word-list half of the page when its tables can't be read, so the
    Today's Board tab still works (and says what's wrong)."""
    pager = {'page': 1, 'pages': 1, 'prev': None, 'next': None, 'numbers': []}
    return {'reports': [], 'invalid_words': [], 'added_words': [],
            'reports_pager': pager, 'invalid_pager': pager, 'added_pager': pager,
            'open_count': 0, 'settled_count': 0, 'invalid_total': 0, 'added_total': 0,
            'show_settled': False, 'settled_url': ADMIN, 'query': '', 'clear_url': ADMIN,
            'keep': {'tab': active_tab}, 'active_tab': active_tab, 'in_both': [],
            'votes_needed': SMUSH_CROWD.threshold}


@bp.route(ADMIN)
@auth.login_required
def smush_admin():
    """Today's board, Smush's corrected word list, and what players have
    reported. Each part reads its own tables, so one that fails leaves the
    others working."""
    args = request.args.to_dict()
    args.setdefault('tab', 'board')
    try:
        view, crowd_error = SMUSH_CROWD.admin_view(args, request.path, tabs=ADMIN_TABS), None
    except Exception as e:
        print(f"Error loading smush admin word lists: {e}")
        view = _empty_view(args['tab'] if args['tab'] in ADMIN_TABS else 'board')
        crowd_error = f'{type(e).__name__}: {e}'
    days, load_error = _admin_days()
    try:
        return render_template('smush_admin.html',
                               **view,
                               crowd_error=crowd_error,
                               days=days, load_error=load_error,
                               board_live=SMUSH_BOARD_LIVE,
                               serve_crowd=SERVE_CROWD,
                               pacific_today=_pacific_today(),
                               word_pop=word_pop,
                               crowd_remove_bands=CROWD_REMOVE_VOTES_BY_POP,
                               crowd_remove_rare=CROWD_REMOVE_VOTES_RARE,
                               crowd_add_votes=CROWD_ADD_VOTES,
                               crowd_window_days=CROWD_WINDOW_DAYS)
    except Exception as e:
        print(f"Error loading smush admin: {e}")
        return render_template('error.html', return_type='Admin Error'), 500


@bp.route('/smush_admin/add_word', methods=['POST'])
@auth.login_required
def smush_add_word():
    """Remove a word from Smush's list ('invalid') or add one ('missing')."""
    word, kind = _admin_form()
    if kind is None:
        return _back(error=NO_CHOICE)
    if not word.isalpha():
        return _back(error='Word must be letters only')
    try:
        SMUSH_CROWD.admin_set(word, kind)
    except Exception as e:
        print(f"Error adding smush word: {e}")
        return _back(error='Database error')
    done = 'added to the word list' if kind == 'missing' else 'removed from the word list'
    return _back(success=f'{word} {done}')


@bp.route('/smush_admin/remove_word', methods=['POST'])
@auth.login_required
def smush_remove_word():
    """Undo an entry in either list, and reset that word's votes."""
    word, kind = _admin_form()
    if kind is None:
        return _back(error=NO_CHOICE)
    if not word.isalpha():
        return _back(error='Word must be letters only')
    try:
        SMUSH_CROWD.admin_remove(word, kind)
    except Exception as e:
        print(f"Error removing smush word: {e}")
        return _back(error='Database error')
    return _back(success=f'{word} undone')


##### today's board #####

# Blossom's today's board (routes/blossom.py), for Smush: players who type
# today's board in feed it to everyone after them. Once it is known, later
# players are offered it ("Today's board:", with a Load it button) and a box
# of spoiler-free hints. James's rules (2026-10-23): his approval in
# /smush_admin always wins; otherwise SERVE_CROWD players, one per network,
# entering the same nine letters.
#
# Smush is the simple case. The game shuffles the eight outer tiles for each
# player and moves them as they play, and the solver never uses positions,
# so a board is just its letters: the gold center plus the other eight, in
# any order. Every player who typed today's board right agrees with every
# other, with nothing like Blossom's petal spots to split them.
#
# Only the starting state is shared. Every outer letter starts with
# START_USES, and the first spicy tile is the same for everyone, but it moves
# to a random tile after each word played. So a board's spicy letter comes
# from James's approval, or from players who logged it before playing a word
# (a fresh board): the letter most of them set, none on a tie.
#
# Smush rolls over at each player's own midnight (verified 2026-10-23), so
# everything keys on the player's own date, as on Blossom. The game also
# plays older boards, so a puzzle first seen on an earlier date is never
# served for a later one. On the rare day with no new board, the game shows
# the previous one again, and nothing is served.
#
# Live on /smush since 2026-10-24 (James's call), after a dark launch from
# 2026-10-23 in which /smush only logged boards and /smush_dev2, his
# password-only copy of the public page, served them. (/smush_dev, with ICE
# COLD, isn't for release.) Players are never told how a board is chosen
# (see crowd.py on why).

# Players on one board that serve it, when nothing is approved (James,
# 2026-10-23).
SERVE_CROWD = 5
# Boards one player can log on one day: an archive binge plus typo fixes,
# with room to spare. A typo is another puzzle (other letters), so this also
# bounds what one person can add to a day.
BOARDS_PER_PLAYER_PER_DAY = 10
# 'typed' = entered by the player; 'edited' = loaded from other players,
# then changed. A board loaded and left as it is is never logged, so the
# served board can't vote for itself.
BOARD_SOURCES = ('typed', 'edited')
# An approval from /smush_admin is a row of its own in smush_boards, with
# this as its source and in place of a player hash: a real hash is 32 hex
# characters, and players can only send BOARD_SOURCES. It never counts as a
# player.
APPROVAL = 'admin'
# Live on /smush (James's call, 2026-10-24). False goes back to the dark
# launch: /smush logs boards but serves none, even to a direct call, and only
# /smush_dev2 shows them.
SMUSH_BOARD_LIVE = True
# A date counts toward a puzzle's first_seen only once this many networks
# logged it then, so one device with a wrong clock can't make today's puzzle
# look like an older one.
ARCHIVE_MIN_NETWORKS = 2
# How long a day's board is reused before it is read again, and how long a
# failed read waits before the next try. Gunicorn runs one worker (Procfile),
# so this module's memo is the only copy.
BOARD_CACHE_SECONDS = 60
BOARD_RETRY_SECONDS = 300
# The days the admin tab shows: Pacific's last three, and tomorrow for
# players whose date is already ahead.
ADMIN_DAYS_BACK = 2
# Each outer letter's uses when a game starts; the center is free.
START_USES = 5
# What a JSON body to /smush (or /smush_dev2) asks for here, not a vote.
BOARD_ACTIONS = ('log_board', 'todays_board')

# The columns every read of smush_boards returns, in this order. spicy is
# NULL unless the board was logged fresh (or approved with one), and
# prefix_hash (crowd.prefix_hash) is NULL on approvals.
_COLUMNS = 'letters, spicy, source, voter_hash, prefix_hash, played_on, updated_at'
LETTERS, SPICY, SOURCE, VOTER, PREFIX, PLAYED_ON, UPDATED = range(7)

# day -> (expires, board): what _board_for last served for that date, hints
# included, or None. time.monotonic() clock.
_board_cache = {}


def _played_on(data):
    """The player's own date, or None if it can't be today anywhere on Earth
    (more than a day from Pacific's) - a wrong clock, not a reason to 400.
    See helpers.parse_played_on."""
    return parse_played_on(data.get('played_on'), _pacific_today())


def _parse_spicy(value, outer):
    """A board's starting spicy letter: one of the outer eight, or ''."""
    if value is None or value == '':
        return ''
    if not (isinstance(value, str) and len(value) == 1 and value.lower() in outer):
        raise ValidationError('spicy must be one of the eight outer letters')
    return value.lower()


def _parse_whole_board(data):
    """(letters, spicy) for a board a page or form sends: letters is the
    puzzle, the gold center and then the other eight sorted, all a-z
    (parse_letters on its own lets an accented letter through)."""
    center, outer = _parse_board(data)
    if not (center + outer).isascii():
        raise ValidationError("a board's letters must be a-z")
    return center + ''.join(sorted(outer)), _parse_spicy(data.get('spicy'), outer)


def _is_letters(value):
    """A puzzle's letters, as the Today's Board tab's forms send them."""
    return (len(value) == 9 and value.isascii() and value.isalpha() and value.islower()
            and len(set(value)) == 9)


def board_text(letters):
    """'E · A D I L N R S T': the gold center, then the other eight."""
    return letters[0].upper() + ' · ' + ' '.join(letters[1:].upper())


def _fetch(sql, params):
    with db_cursor() as (conn, cursor):
        cursor.execute(sql, params)
        return cursor.fetchall()


def rows_with_letters(letters):
    """Every row for one puzzle, any date."""
    return _fetch(f"SELECT {_COLUMNS} FROM smush_boards WHERE letters = %s", (letters,))


def rows_logged_between(first, last):
    """Every row, any date, for each puzzle logged between these days."""
    return _fetch(
        f"SELECT {_COLUMNS} FROM smush_boards WHERE letters IN "
        "(SELECT letters FROM smush_boards WHERE played_on BETWEEN %s AND %s)",
        (first, last))


def store_board(day, voter, prefix, letters, spicy, source):
    """Log one player's board, replacing their earlier entry of that puzzle
    that day. `voter` is their crowd.player_hash, `prefix` their network's
    crowd.prefix_hash. A spicy letter they logged before stays when a later
    entry has none (they have played on since). Inline, not on the
    write-behind queue: a missing table there would drop the rest of its
    batch, page visits included. Best-effort, like a vote."""
    now = pst_now_str()
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute(
                "SELECT COUNT(*) FROM smush_boards "
                "WHERE voter_hash = %s AND played_on = %s AND letters <> %s",
                (voter, day, letters))
            if cursor.fetchone()[0] >= BOARDS_PER_PLAYER_PER_DAY:
                return
            cursor.execute(
                """INSERT INTO smush_boards (played_on, voter_hash, prefix_hash, letters, spicy,
                       source, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE spicy = COALESCE(new.spicy, smush_boards.spicy),
                       source = new.source, updated_at = new.updated_at,
                       prefix_hash = new.prefix_hash""",
                (day, voter, prefix, letters, spicy or None, source, now, now))
            conn.commit()
    except Exception as e:
        alerts.alert_throttled('smush_board_log_failed', exc=type(e).__name__, msg=e)


def store_approval(day, letters, spicy):
    """Approve this board (see APPROVAL) for players whose date is `day`,
    replacing any approval of its puzzle that day. Raises, unlike
    store_board: the admin page says whether it worked."""
    now = pst_now_str()
    with db_cursor() as (conn, cursor):
        cursor.execute(
            """INSERT INTO smush_boards (played_on, voter_hash, letters, spicy, source,
                   created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s) AS new
               ON DUPLICATE KEY UPDATE spicy = new.spicy, updated_at = new.updated_at""",
            (day, APPROVAL, letters, spicy or None, APPROVAL, now, now))
        conn.commit()


def remove_approval(letters, day):
    """Take back the approval of this puzzle on `day`; players' boards stay.
    Returns how many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute(
            "DELETE FROM smush_boards WHERE letters = %s AND played_on = %s AND source = %s",
            (letters, day, APPROVAL))
        removed = cursor.rowcount
        conn.commit()
    return removed


def remove_puzzle(letters, day):
    """Delete the players' entries of one puzzle on `day`: test boards
    (staging writes this table too) or junk. An approval stays. Returns how
    many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute(
            "DELETE FROM smush_boards WHERE letters = %s AND played_on = %s AND source <> %s",
            (letters, day, APPROVAL))
        removed = cursor.rowcount
        conn.commit()
    return removed


def consensus(rows, day):
    """What one puzzle's rows add up to on `day`.

    rows -- smush_boards rows (see _COLUMNS) sharing one `letters`, from any
            dates. On `day`, each player's latest entry is the one that
            counts, and players on one network (crowd.prefix_hash) count
            once: the latest entry among them.

    Returns a dict: players (networks on `day`), reached_at (when the
    SERVE_CROWD-th came in), serves (whether it is served on its own merits),
    spicy (the starting spicy letter it is served with, or ''), spicy_counts
    (the fresh boards' spicy letters, most first), why (None when served,
    else what it waits for; admin wording), approved / approved_spicy /
    approved_at (`day`'s approval, served as it is) and first_seen (see
    ARCHIVE_MIN_NETWORKS).
    """
    approvals = [row for row in rows if row[SOURCE] == APPROVAL and row[PLAYED_ON] == day]
    player_rows = [row for row in rows if row[SOURCE] in BOARD_SOURCES]

    networks_by_day = {}
    for row in player_rows:
        networks_by_day.setdefault(row[PLAYED_ON], set()).add(row[PREFIX] or row[VOTER])
    busy_days = [d for d, nets in networks_by_day.items() if len(nets) >= ARCHIVE_MIN_NETWORKS]
    first_seen = min(busy_days) if busy_days else min(row[PLAYED_ON] for row in rows)

    latest = {}
    for row in player_rows:
        if row[PLAYED_ON] != day:
            continue
        mine = latest.get(row[VOTER])
        if mine is None or row[UPDATED] > mine[UPDATED]:
            latest[row[VOTER]] = row
    # Then one per network. A second step, so a player who moved between
    # networks still counts once.
    networks = {}
    for row in latest.values():
        network = row[PREFIX] or row[VOTER]
        if network not in networks or row[UPDATED] > networks[network][UPDATED]:
            networks[network] = row

    times = sorted(row[UPDATED] for row in networks.values())
    spicy_counts = Counter(row[SPICY] for row in networks.values() if row[SPICY]).most_common()
    crowd_spicy = ''
    if spicy_counts and (len(spicy_counts) == 1 or spicy_counts[0][1] > spicy_counts[1][1]):
        crowd_spicy = spicy_counts[0][0]

    approval = max(approvals, key=lambda row: row[UPDATED]) if approvals else None
    serves, why = False, None
    if approval is not None or len(networks) >= SERVE_CROWD:
        serves = True
    elif networks:
        short = SERVE_CROWD - len(networks)
        why = f"needs {short} more player{'' if short == 1 else 's'}"
    else:
        why = 'no player board on this day yet'
    return {
        'players': len(networks),
        'reached_at': times[SERVE_CROWD - 1] if len(times) >= SERVE_CROWD else None,
        'serves': serves,
        'spicy': ((approval[SPICY] or '') if approval is not None else crowd_spicy),
        'spicy_counts': spicy_counts,
        'crowd_spicy': crowd_spicy,
        'why': why,
        'approved': approval is not None,
        'approved_spicy': (approval[SPICY] or '') if approval is not None else '',
        'approved_at': approval[UPDATED] if approval is not None else None,
        'first_seen': first_seen,
    }


def todays_board(rows, day):
    """The board to offer players whose date is `day`, and what the admin tab
    shows about that day.

    rows -- every smush_boards row (any date) for each puzzle logged on `day`
            (see rows_logged_between).

    Smush has one board a day, so one is served: `day`'s latest approval,
    whatever the players typed (an approval always wins, even over a stray
    row that makes its puzzle look older); otherwise the puzzle most players
    logged that day, once it has SERVE_CROWD of them. Never a puzzle first
    seen on an earlier day, and never a smaller puzzle while the biggest
    isn't settled: that could be some other game's board. Returns (board,
    groups): board is {'date', 'center', 'outer', 'spicy', 'text'} or None;
    groups is every puzzle of the day with its consensus, 'archive' and
    'served', most players first.
    """
    by_letters = {}
    for row in rows:
        by_letters.setdefault(row[LETTERS], []).append(row)

    groups = []
    for letters, group_rows in by_letters.items():
        if not any(row[PLAYED_ON] == day for row in group_rows):
            continue
        result = consensus(group_rows, day)
        result['letters'] = letters
        result['text'] = board_text(letters)
        result['archive'] = result['first_seen'] < day
        result['served'] = False
        if result['archive'] and not result['approved']:
            result['why'] = f"older puzzle, first seen {result['first_seen']:%m-%d}"
        groups.append(result)
    groups.sort(key=lambda g: (-g['players'], g['letters']))

    approved = [g for g in groups if g['approved']]
    current = [g for g in groups if not g['archive']]
    if approved:
        chosen = max(approved, key=lambda g: g['approved_at'])
    elif current and current[0]['serves']:
        chosen = current[0]
    else:
        chosen = None
    for g in groups:
        if g is not chosen and g['serves'] and not g['archive']:
            g['why'] = ('a later approval is served' if g['approved']
                        else 'another board is served')
    if chosen is None:
        return None, groups
    chosen['served'] = True
    letters = chosen['letters']
    return {'date': day.isoformat(), 'center': letters[0], 'outer': letters[1:],
            'spicy': chosen['spicy'], 'text': chosen['text']}, groups


def smush_hints(letters, day):
    """Spoiler-free hints for one board (all_words.pangram_hints), from
    Smush's word list with its corrections (get_smush_words): the words a
    fresh board can play, so no outer letter more than START_USES times."""
    center, board = letters[0], set(letters)
    fits = [w for w in get_smush_words()
            if isinstance(w, str) and SMUSH_MIN_LEN <= len(w) <= SMUSH_MAX_LEN and center in w
            and set(w) <= board and all(w.count(l) <= START_USES for l in set(w) if l != center)]
    return all_words.pangram_hints(fits, board, day, 'Smush')


def _board_for(day):
    """Today's board for players whose date is `day`, with its hints, or
    None. Served from the memo while fresh (BOARD_CACHE_SECONDS), else read
    again; never raises. When an entry goes stale, the first request pushes
    it out 15 seconds before reading, and every other request keeps the
    stale board meanwhile, so a slow database holds up one request, not all
    of them. A failed read is remembered too, for BOARD_RETRY_SECONDS."""
    now = time.monotonic()
    entry = _board_cache.get(day)
    if entry is not None and now < entry[0]:
        return entry[1]
    if entry is not None:
        _board_cache[day] = (now + 15, entry[1])
    try:
        board, _groups = todays_board(rows_logged_between(day, day), day)
        if board is not None:
            board['hints'] = smush_hints(board['center'] + board['outer'], day)
        _board_cache[day] = (time.monotonic() + BOARD_CACHE_SECONDS, board)
    except Exception as e:
        alerts.alert_throttled('smush_board_read_failed', exc=type(e).__name__, msg=e)
        board = None
        _board_cache[day] = (time.monotonic() + BOARD_RETRY_SECONDS, None)
    for old in [d for d in list(_board_cache) if abs((d - day).days) > 2]:
        _board_cache.pop(old, None)
    return board


def _log_board(data):
    """A board a player entered, sent once its nine letters are in, and
    again if they set the spicy tile before playing a word. Always the same
    reply, stored or not."""
    letters, spicy = _parse_whole_board(data)
    source = data.get('source')
    if source not in BOARD_SOURCES:
        raise ValidationError('source must be typed or edited')
    day = _played_on(data)
    if day is not None:
        store_board(day, crowd.player_hash('smush'), crowd.prefix_hash('smush'),
                    letters, spicy, source)
    return jsonify(status='success')


def handle_board_action(data, dev=False):
    """A board-logging or today's-board JSON body posted to /smush, or to
    /smush_dev2 with `dev`. Today's board is null on a page where it is still
    dark, even to a direct call."""
    if data.get('action') == 'log_board':
        return _log_board(data)
    day = _played_on(data)
    if not (dev or SMUSH_BOARD_LIVE) or day is None:
        return jsonify(board=None)
    return jsonify(board=_board_for(day))


def page_board(show_board):
    """(todays_board, board_date) for a GET of the page: the Pacific date's
    board and that date, or (None, '') where today's board doesn't show.
    Never raises, so nothing about today's board can take the page down."""
    if not show_board:
        return None, ''
    today = _pacific_today()
    try:
        return _board_for(today), today.isoformat()
    except Exception as e:
        # _board_for doesn't raise; this guard is the page's own.
        alerts.alert_throttled('smush_board_read_failed', exc=type(e).__name__, msg=e)
        return None, today.isoformat()


def _admin_days():
    """(days, error): each recent day's puzzles for the Today's Board tab,
    newest first, each with its players and what is served."""
    today = _pacific_today()
    first, last = today - timedelta(days=ADMIN_DAYS_BACK), today + timedelta(days=1)
    try:
        rows = rows_logged_between(first, last)
    except Exception as e:
        print(f"Error loading smush admin boards: {e}")
        return [], f'{type(e).__name__}: {e}'
    days = []
    day = last
    while day >= first:
        _board, groups = todays_board(rows, day)
        if groups:
            days.append({'day': day, 'groups': groups})
        day -= timedelta(days=1)
    return days, None


def _board_form():
    """(letters, spicy, day) from a Today's Board form, or None if it isn't
    one: a puzzle row sends letters (its spicy and date too); the Approve
    form sends the center, the other eight in any order, an optional spicy
    letter and a date. The same rules as a player's board."""
    played_on = request.form.get('played_on', '')
    if not _DATE.fullmatch(played_on):
        return None
    try:
        day = date.fromisoformat(played_on)
    except ValueError:
        return None
    if 'letters' in request.form:
        letters = request.form.get('letters', '')
        center, outer = letters[:1], letters[1:]
    else:
        center = request.form.get('center', '').strip().lower()
        outer = ''.join(request.form.get('outer', '').split()).lower()
    try:
        letters, spicy = _parse_whole_board({
            'center': center, 'outer': outer,
            'spicy': request.form.get('spicy', '').strip().lower()})
    except ValidationError:
        return None
    return letters, spicy, day


@bp.route(ADMIN + '/approve_board', methods=['POST'])
@auth.login_required
def smush_approve_board():
    """Serve this board to every player whose date is `played_on`, straight
    away and whatever the players typed: James's own board, with its
    starting spicy letter (James's call, 2026-10-23). Approving the same
    puzzle again replaces it."""
    form = _board_form()
    if form is None:
        return _back(error=('Not a board: a center letter, the other eight (all different), '
                            'a spicy letter from those eight or none, and a date'))
    letters, spicy, day = form
    try:
        store_approval(day, letters, spicy)
    except Exception as e:
        print(f"Error approving smush board: {e}")
        return _back(error='Database error')
    _board_cache.clear()
    spice = f" with {spicy.upper()} spicy" if spicy else ''
    return _back(success=f"Approved {board_text(letters)}{spice} for {day:%m-%d}")


@bp.route(ADMIN + '/unapprove_board', methods=['POST'])
@auth.login_required
def smush_unapprove_board():
    """Undo the approval of one puzzle on one day: the players' boards
    decide again, and none of them is touched."""
    letters = request.form.get('letters', '')
    played_on = request.form.get('played_on', '')
    if not (_is_letters(letters) and _DATE.fullmatch(played_on)):
        return _back(error='Not a board')
    try:
        day = date.fromisoformat(played_on)
    except ValueError:
        return _back(error='Not a board')
    try:
        removed = remove_approval(letters, day)
    except Exception as e:
        print(f"Error undoing smush approval: {e}")
        return _back(error='Database error')
    _board_cache.clear()
    if not removed:
        return _back(error='That board was not approved')
    return _back(success="Approval undone: the players' boards decide again")


@bp.route(ADMIN + '/remove_board', methods=['POST'])
@auth.login_required
def smush_remove_board():
    """Delete the players' entries of one puzzle on one day: test boards
    (staging writes the same table) or junk. An approval stays."""
    letters = request.form.get('letters', '')
    played_on = request.form.get('played_on', '')
    if not (_is_letters(letters) and _DATE.fullmatch(played_on)):
        return _back(error='Not a board')
    try:
        day = date.fromisoformat(played_on)
    except ValueError:
        return _back(error='Not a board')
    try:
        removed = remove_puzzle(letters, day)
    except Exception as e:
        print(f"Error removing smush board: {e}")
        return _back(error='Database error')
    _board_cache.clear()
    return _back(success=f"Removed {removed} entr{'y' if removed == 1 else 'ies'} "
                         f"of {board_text(letters)} on {day:%m-%d}")
