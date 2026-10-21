"""Ribbit's word list, crowd word corrections, shared boards, and
/ribbit_admin.

The solver routes themselves are run_ribbit and run_ribbit_dev in
routes/wordgames.py; they hand any JSON body carrying an `action` to
handle_action here.

Shared boards: entering a Ribbit board means typing 16 letters and drawing
every link, and the links can't be worked out from the letters (tried
2026-10-17: the best guess got about 3 links in 4 right on synthetic boards,
the whole board 3% of the time). So the first few players each day do it for
everyone after them. A player's board is logged when they solve it; once
enough players agree on a board, later players are offered it ("Load today's
board"), and anyone who types a known board's letters - an archive puzzle
too - gets its links filled in. Nothing is guessed: every link served was
drawn by players. /ribbit_admin can also approve the latest board for a set
of letters (James's own, entered just before), which serves it at once.

More than one Ribbit board can be real on one day: several players at a time
type boards, alike, that James's game on puzzmo.com never shows him (seen in
the logged boards, 2026-10-21). So a day can offer several boards, and the
player picks the one that matches their game: James's approval, plus any
board the players settled on their own.
"""

import re
import time
from collections import Counter
from datetime import date, datetime, timedelta

import pytz
from flask import Blueprint, jsonify, redirect, render_template, request

import crowd
from crowd import (CROWD_ADD_VOTES, CROWD_REMOVE_VOTES_BY_POP, CROWD_REMOVE_VOTES_RARE,
                   CROWD_WINDOW_DAYS)
from data import word_pop, words
from extensions import auth, db_cursor, pst_now_str
from functions import all_words
from helpers import ValidationError, parse_letters
from monitoring import alerts

bp = Blueprint('ribbit', __name__)

PACIFIC = pytz.timezone('America/Los_Angeles')


def parse_ribbit_board(data):
    """Validate the Ribbit board (tiles + connections + cleared tiles) in the
    POST body and return (nodes, edges, disabled).

    nodes    -- {node_id: letter} for every tile placed on the board
    edges    -- list of (id_a, id_b) connection pairs
    disabled -- set of node ids the player marked cleared (frogged)
    Board size is capped so the path search can never be handed a runaway
    graph: <=64 tiles and <=1024 connections.
    """
    raw_nodes = data.get('nodes')
    if not (isinstance(raw_nodes, list) and 1 <= len(raw_nodes) <= 64):
        raise ValidationError('nodes must be a list of 1 to 64 tiles')
    nodes = {}
    for n in raw_nodes:
        if not isinstance(n, dict):
            raise ValidationError('each node must be an object')
        nid, letter = n.get('id'), n.get('letter')
        if not (isinstance(nid, int) and not isinstance(nid, bool) and 0 <= nid <= 100000):
            raise ValidationError('node id must be a non-negative integer')
        if not (isinstance(letter, str) and len(letter) == 1 and 'a' <= letter.lower() <= 'z'):
            raise ValidationError('node letter must be a single letter a-z')
        if nid in nodes:
            raise ValidationError('node ids must be unique')
        nodes[nid] = letter.lower()

    raw_edges = data.get('edges', [])
    if not (isinstance(raw_edges, list) and len(raw_edges) <= 1024):
        raise ValidationError('edges must be a list of at most 1024 connections')
    edges = []
    for e in raw_edges:
        if not (isinstance(e, list) and len(e) == 2):
            raise ValidationError('each edge must be a pair of node ids')
        a, b = e
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in (a, b)):
            raise ValidationError('edge endpoints must be integers')
        if a not in nodes or b not in nodes:
            raise ValidationError('edge endpoints must reference placed tiles')
        edges.append((a, b))

    raw_disabled = data.get('disabled', [])
    if not (isinstance(raw_disabled, list) and len(raw_disabled) <= 64):
        raise ValidationError('disabled must be a list of at most 64 node ids')
    disabled = set()
    for d in raw_disabled:
        if not (isinstance(d, int) and not isinstance(d, bool) and d in nodes):
            raise ValidationError('disabled entries must reference placed tiles')
        disabled.add(d)

    return nodes, edges, disabled


##### word list #####

# Memoized in the module, as routes/smush.py does (SimpleCache would pickle
# the ~17MB set on every get). Holds at most one extra copy of the word
# list per process, and none at all while Ribbit's corrections are empty.
_ribbit_words = {'value': None, 'expires': 0.0}


def _forget_ribbit_words():
    """Make the next get_ribbit_words() re-read the corrections."""
    _ribbit_words['expires'] = 0.0


def get_ribbit_words():
    """The full word list with Ribbit's own corrections applied: words
    players or /ribbit_admin removed taken out, words added put in.

    Only ribbit_* tables: Smush's and Blossom's corrections are about those
    games' lists. Falls back to the raw list if the DB is unreachable.
    Refreshed every 12 hours, or straight away after a change.
    """
    if _ribbit_words['value'] is not None and time.time() < _ribbit_words['expires']:
        return _ribbit_words['value']

    invalid_words, added_words = set(), set()
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute("SELECT word FROM ribbit_invalid_words")
            invalid_words = {row[0].lower() for row in cursor.fetchall()}
            cursor.execute("SELECT word FROM ribbit_added_words")
            added_words = {row[0].lower() for row in cursor.fetchall()}
    except Exception as e:
        print(f"Error fetching ribbit word corrections: {e}")
        # A DB blip at refresh time must not evict the corrections for 12
        # hours: keep serving the last good list (or the raw list if there
        # has never been one) and retry soon.
        fallback = _ribbit_words['value'] if _ribbit_words['value'] is not None else words
        _ribbit_words['value'] = fallback
        _ribbit_words['expires'] = time.time() + 300
        return fallback

    if invalid_words or added_words:
        ribbit_words = (words - invalid_words) | added_words
    else:
        ribbit_words = words

    _ribbit_words['value'] = ribbit_words
    _ribbit_words['expires'] = time.time() + 43200
    return ribbit_words


##### shared boards #####

# When a board is served (James asked, 2026-10-17): once SERVE_MAJORITY
# players have entered the same 16 letters, with each link kept only if more
# than half of them drew it, or as soon as SERVE_IDENTICAL players' boards
# match exactly. Two people typing all 16 letters identically settles the
# letters; links are where the slips are (a missed line, a row drag that
# over-links), and a third board outvotes one player's slip. Assuming a 3%
# chance of missing any one link: a two-board intersection is fully right
# ~36% of the time, a three-board majority ~96%, two identical boards almost
# always. Players are never told these numbers (see crowd.py on why).
SERVE_IDENTICAL = 2
SERVE_MAJORITY = 3
# The most boards one day offers: an approval and the settled players'
# boards, approval first, then most players. Several can be real at once
# (see the module docstring); more than this is junk getting through.
TODAYS_BOARDS_MAX = 3
# Boards one player can log on one day: an archive binge plus typo fixes,
# with room to spare. Each typo is a new board (other letters), so this also
# bounds what one person can add to a day's groups.
BOARDS_PER_PLAYER_PER_DAY = 10
# 'typed' = entered by the player; 'edited' = loaded from other players,
# then changed. A board loaded and left as it is is never logged, so the
# shared board can't confirm itself; nor is one that only lost links (see
# _links_to_log).
BOARD_SOURCES = ('typed', 'edited')
# Live on /ribbit (James's call, 2026-10-18) after a dark launch from
# 2026-10-17, when /ribbit only logged boards and /ribbit_dev alone offered
# them back. False goes back to that: boards still logged, none offered.
SHARED_BOARDS_LIVE = True
# An approval from /ribbit_admin (the Shared Boards tab's Add) is a row of
# its own in ribbit_boards, with this as its source and in place of a player
# hash: a real hash is 32 hex characters, and players can only send
# BOARD_SOURCES. Its links are served whatever the players' boards say, and
# it never counts as a player.
APPROVAL = 'admin'

N = 4
CELLS = N * N


def _perm(move):
    """Where each cell lands under `move`, a (row, col) -> (row, col) map."""
    out = []
    for i in range(CELLS):
        r, c = move(*divmod(i, N))
        out.append(r * N + c)
    return tuple(out)


# The 8 ways to turn or flip the grid: TRANSFORMS[t][cell] is where `cell`
# lands under t. A player may enter the board turned (the page's Rotate) or
# even mirrored and still trace every word, so all 8 are the same board.
TRANSFORMS = tuple(_perm(move) for move in (
    lambda r, c: (r, c),                    # as entered
    lambda r, c: (c, N - 1 - r),            # quarter turn clockwise
    lambda r, c: (N - 1 - r, N - 1 - c),    # half turn
    lambda r, c: (N - 1 - c, r),            # quarter turn anticlockwise
    lambda r, c: (r, N - 1 - c),            # mirrored left to right
    lambda r, c: (N - 1 - r, c),            # mirrored top to bottom
    lambda r, c: (c, r),                    # flipped over the main diagonal
    lambda r, c: (N - 1 - c, N - 1 - r),    # flipped over the other diagonal
))


def _inverse(perm):
    inv = [0] * CELLS
    for i, j in enumerate(perm):
        inv[j] = i
    return tuple(inv)


INVERSES = tuple(_inverse(perm) for perm in TRANSFORMS)


def _apply(perm, letters, links):
    """The board after every cell moves as `perm` says: (letters, links)."""
    out = [''] * CELLS
    for i, ch in enumerate(letters):
        out[perm[i]] = ch
    moved = sorted(tuple(sorted((perm[a], perm[b]))) for a, b in links)
    return ''.join(out), tuple(moved)


def canonical(letters, links=()):
    """(letters, links, t): the smallest of the board's 8 turns and flips,
    and the transform t that gets there from this view. INVERSES[t] puts a
    canonical board back into this view."""
    best = None
    for t, perm in enumerate(TRANSFORMS):
        form = _apply(perm, letters, links)
        if best is None or form < best[:2]:
            best = form + (t,)
    return best


def _links_text(links):
    return ','.join(f'{a}-{b}' for a, b in links)


def _parse_links(text):
    return tuple(sorted(tuple(sorted(int(x) for x in pair.split('-')))
                        for pair in (text or '').split(',') if pair))


def _every_tile_linked(links):
    """What a finished Ribbit board looks like: each tile is in some word,
    so each has a link. A board short of that is still being drawn."""
    return len({cell for pair in links for cell in pair}) == CELLS


def _adjacent(a, b):
    (ra, ca), (rb, cb) = divmod(a, N), divmod(b, N)
    return max(abs(ra - rb), abs(ca - cb)) == 1


# The columns every read of ribbit_boards returns, in this order. prefix_hash
# (crowd.prefix_hash) is NULL on approvals, and on boards logged before
# mysql_tables/ribbit_boards_prefix.sql added it.
_COLUMNS = 'letters, links, orientation, source, voter_hash, played_on, updated_at, prefix_hash'
LETTERS, LINKS, ORIENTATION, SOURCE, VOTER, PLAYED_ON, UPDATED, PREFIX = range(8)


def consensus(rows):
    """What the boards for one set of letters add up to.

    rows -- ribbit_boards rows (see _COLUMNS) sharing one canonical letters,
            from any dates. Each player's latest board is the one that
            counts, and players on one network (crowd.prefix_hash) count
            once: the latest board among them. A row logged before
            prefix_hash existed is a network of its own.

    Returns a dict: players (finished boards, one per network), half_drawn,
    agreed / tied (links more than half / exactly half of them drew), links
    (the canonical links to serve, or None), why (None when served, else what
    it's waiting for), orientation (the view most of its players entered it
    in), first_seen and last_update; approved / approved_at (an APPROVAL
    row: its links are served, whatever the players drew) and latest_at /
    latest_finished (the most recent player board, the one an approval
    copies).
    """
    approvals = [row for row in rows if row[SOURCE] == APPROVAL]
    player_rows = [row for row in rows if row[SOURCE] != APPROVAL]
    latest = {}
    for row in player_rows:
        mine = latest.get(row[VOTER])
        if mine is None or row[UPDATED] > mine[UPDATED]:
            latest[row[VOTER]] = row
    # Then one per network. A second step, so a player with a board from
    # before prefix_hash and one since still counts once.
    networks = {}
    for row in latest.values():
        network = row[PREFIX] or row[VOTER]
        if network not in networks or row[UPDATED] > networks[network][UPDATED]:
            networks[network] = row
    boards = [(row, _parse_links(row[LINKS])) for row in networks.values()]
    finished = [(row, links) for row, links in boards if _every_tile_linked(links)]
    players = len(finished)

    votes = Counter(pair for _row, links in finished for pair in links)
    agreed = tuple(sorted(pair for pair, n in votes.items() if 2 * n > players))
    tied = sum(1 for n in votes.values() if 2 * n == players)

    approval = max(approvals, key=lambda row: row[UPDATED]) if approvals else None
    links, why = None, None
    if approval is not None:
        links = _parse_links(approval[LINKS])
    elif players >= SERVE_MAJORITY:
        if _every_tile_linked(agreed):
            links = agreed
        else:
            why = 'links disagree, needs another board'
    elif players == SERVE_IDENTICAL:
        if finished[0][1] == finished[1][1]:
            links = finished[0][1]
        else:
            why = 'links differ, needs a 3rd board'
    elif players == 1:
        why = 'needs 1 more matching board'
    else:
        why = 'no finished board yet'

    views = Counter(row[ORIENTATION] for row, _links in (finished or boards))
    if views:
        orientation = min(views, key=lambda t: (-views[t], t))
    else:
        orientation = approval[ORIENTATION] if approval is not None else 0
    newest = max(player_rows, key=lambda row: row[UPDATED]) if player_rows else None
    return {
        'players': players,
        'half_drawn': len(boards) - players,
        'agreed': len(agreed),
        'tied': tied,
        'links': links,
        'why': why,
        'orientation': orientation,
        'first_seen': min(row[PLAYED_ON] for row in rows),
        'last_update': max(row[UPDATED] for row in (player_rows or rows)),
        'approved': approval is not None,
        'approved_at': approval[UPDATED] if approval is not None else None,
        'latest_at': newest[UPDATED] if newest is not None else None,
        'latest_finished': (newest is not None
                            and _every_tile_linked(_parse_links(newest[LINKS]))),
    }


def todays_boards(rows, day):
    """The boards to offer players whose date is `day`.

    rows -- every ribbit_boards row (any date) for each set of letters
            logged on `day`.

    Today's boards are the letters logged on `day` that are settled (see
    consensus): the approved one first (of two approved, only the later
    approval), then the players' boards, most players first, at most
    TODAYS_BOARDS_MAX. Never letters first logged on an earlier day - that
    is an older puzzle, so an archive game. Returns (boards, groups): boards
    is a list of {'letters', 'edges'}, each in the view most of its players
    used; groups is every set of letters with its consensus and 'served',
    most players first, for the admin page.
    """
    by_letters = {}
    for row in rows:
        by_letters.setdefault(row[LETTERS], []).append(row)

    groups = []
    for letters, group_rows in by_letters.items():
        if not any(row[PLAYED_ON] == day for row in group_rows):
            continue
        result = consensus(group_rows)
        result['letters'] = letters
        result['served'] = False
        if result['first_seen'] < day:
            result['why'] = f"older puzzle, first seen {result['first_seen']:%m-%d}"
        groups.append(result)
    groups.sort(key=lambda g: (-g['players'], g['letters']))

    settled = [g for g in groups if g['first_seen'] >= day and g['links'] is not None]
    approved = [g for g in settled if g['approved']]
    latest = max(approved, key=lambda g: g['approved_at']) if approved else None
    for g in approved:
        if g is not latest:
            g['why'] = 'a later approval replaced it'
    offered = ([latest] if latest else []) + [g for g in settled if not g['approved']]
    for g in offered[TODAYS_BOARDS_MAX:]:
        g['why'] = 'past the limit of boards a day'
    boards = []
    for g in offered[:TODAYS_BOARDS_MAX]:
        g['served'] = True
        boards.append(in_view(g['letters'], g['links'], g['orientation']))
    return boards, groups


def in_view(letters, links, t):
    """A canonical board as seen from view t, ready for the page."""
    view_letters, view_links = _apply(INVERSES[t], letters, links)
    return {'letters': list(view_letters), 'edges': [list(pair) for pair in view_links]}


def _fetch(sql, params):
    with db_cursor() as (conn, cursor):
        cursor.execute(sql, params)
        return cursor.fetchall()


def rows_with_letters(letters):
    """Every row for one canonical set of letters, any date."""
    return _fetch(f"SELECT {_COLUMNS} FROM ribbit_boards WHERE letters = %s", (letters,))


def rows_logged_between(first, last):
    """Every row, any date, for each set of letters logged between these days."""
    return _fetch(
        f"SELECT {_COLUMNS} FROM ribbit_boards WHERE letters IN "
        "(SELECT letters FROM ribbit_boards WHERE played_on BETWEEN %s AND %s)",
        (first, last))


def store_board(day, voter, prefix, letters, links, orientation, source):
    """Log one player's board, replacing their earlier version of it that
    day. `voter` is their crowd.player_hash, `prefix` their network's
    crowd.prefix_hash. Inline, not on the write-behind queue: a missing
    table or column there drops the rest of its batch, page visits
    included. Best-effort, like a vote."""
    now = pst_now_str()
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute(
                "SELECT COUNT(*) FROM ribbit_boards "
                "WHERE voter_hash = %s AND played_on = %s AND letters <> %s",
                (voter, day, letters))
            if cursor.fetchone()[0] >= BOARDS_PER_PLAYER_PER_DAY:
                return
            cursor.execute(
                """INSERT INTO ribbit_boards (played_on, voter_hash, letters, links, orientation,
                       source, created_at, updated_at, prefix_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE links = new.links, orientation = new.orientation,
                       source = new.source, updated_at = new.updated_at,
                       prefix_hash = new.prefix_hash""",
                (day, voter, letters, _links_text(links), orientation, source, now, now, prefix))
            conn.commit()
    except Exception as e:
        alerts.alert_throttled('ribbit_board_log_failed', exc=type(e).__name__, msg=e)


def store_approval(day, letters, links, orientation):
    """Approve these links for these letters (see APPROVAL), replacing any
    approval of them that day. Raises, unlike store_board: the admin page
    says whether it worked."""
    now = pst_now_str()
    with db_cursor() as (conn, cursor):
        cursor.execute(
            """INSERT INTO ribbit_boards (played_on, voter_hash, letters, links, orientation,
                   source, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) AS new
               ON DUPLICATE KEY UPDATE links = new.links, orientation = new.orientation,
                   updated_at = new.updated_at""",
            (day, APPROVAL, letters, _links_text(links), orientation, APPROVAL, now, now))
        conn.commit()


def remove_approval(letters):
    """Take back every approval of these letters, any day; players' boards
    stay. Returns how many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM ribbit_boards WHERE letters = %s AND source = %s",
                       (letters, APPROVAL))
        removed = cursor.rowcount
        conn.commit()
    return removed


def _pacific_today():
    return datetime.now(PACIFIC).date()


_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')


def _played_on(data):
    """The player's own date, or None if it can't be today anywhere on Earth
    (more than a day from Pacific's) - a wrong clock, not a reason to 400."""
    value = data.get('played_on')
    if not (isinstance(value, str) and _DATE.fullmatch(value)):
        raise ValidationError('played_on must be a date, YYYY-MM-DD')
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise ValidationError('played_on must be a real date')
    return day if abs((day - _pacific_today()).days) <= 1 else None


def _grid_board(data, whole=True):
    """(letters, links) for a board on the page's 4x4 grid, in the player's
    view: 16 letters with '.' for an empty cell, and links between
    neighbouring cells only. `whole` needs all 16 cells filled."""
    nodes, edges, _disabled = parse_ribbit_board(data)
    if not set(nodes) <= set(range(CELLS)):
        raise ValidationError('cells must be on the 4x4 grid')
    if whole and len(nodes) != CELLS:
        raise ValidationError('a shared board needs all 16 cells of the 4x4 grid')
    letters = ''.join(nodes.get(i, '.') for i in range(CELLS))
    links = set()
    for a, b in edges:
        if not _adjacent(a, b):
            raise ValidationError('links must join neighbouring cells')
        links.add((a, b) if a < b else (b, a))
    return letters, tuple(sorted(links))


def _links_to_log(links, rows, voter, day, source):
    """The canonical links to store for a player's board, or None to store
    nothing: a logged board only ever gains links.

    Players keep the page in step with their game as it goes, and some do
    it by erasing links (James, 2026-10-21; most loaded boards logged that
    day had only lost links). A board is logged on every Solve, and each
    player's latest board is the one that counts, so their later boards
    would outvote their first. So:
    - a player's own earlier board of these letters that day keeps every
      link it had; links they add (a missed line) still count;
    - a board loaded from other players (source 'edited') that only lost
      links is not logged at all, and one that gained some is logged as the
      links served for it plus those.
    The cost: a player who erases a link they drew wrongly, after a Solve,
    keeps it. One player's slip is what the majority rule outvotes.

    rows -- every ribbit_boards row for these canonical letters, any date.
    """
    drawn = set(links)
    mine = [row for row in rows
            if row[VOTER] == voter and row[PLAYED_ON] == day and row[SOURCE] != APPROVAL]
    if mine:
        return tuple(sorted(drawn | set(_parse_links(mine[0][LINKS]))))
    if source == 'edited' and rows:
        served = consensus(rows)['links']
        if served is not None:
            if drawn <= set(served):
                return None
            return tuple(sorted(drawn | set(served)))
    return links


def log_board(data):
    """A board a player entered, sent when they solve it. Always the same
    reply, stored or not."""
    letters, links = _grid_board(data)
    source = data.get('source')
    if source not in BOARD_SOURCES:
        raise ValidationError('source must be typed or edited')
    day = _played_on(data)
    if day is not None and links:
        canon_letters, canon_links, t = canonical(letters, links)
        voter = crowd.player_hash('ribbit')
        try:
            rows = rows_with_letters(canon_letters)
        except Exception as e:
            alerts.alert_throttled('ribbit_board_log_failed', exc=type(e).__name__, msg=e)
            return jsonify(status='success')
        keep = _links_to_log(canon_links, rows, voter, day, source)
        if keep is not None:
            store_board(day, voter, crowd.prefix_hash('ribbit'), canon_letters, keep, t, source)
    return jsonify(status='success')


def shared_board(data):
    """Today's boards for the player's date, those other players have
    settled: a list, empty when there are none."""
    day = _played_on(data)
    if day is None:
        return jsonify(boards=[])
    try:
        rows = rows_logged_between(day, day)
    except Exception as e:
        alerts.alert_throttled('ribbit_board_read_failed', exc=type(e).__name__, msg=e)
        return jsonify(boards=[])
    boards, _groups = todays_boards(rows, day)
    return jsonify(boards=boards)


def board_links(data):
    """The links other players drew for these letters, any date (so an
    archive puzzle too), in the player's own view - or null."""
    letters, _links = _grid_board(data)
    canon_letters, _none, t = canonical(letters)
    try:
        rows = rows_with_letters(canon_letters)
    except Exception as e:
        alerts.alert_throttled('ribbit_board_read_failed', exc=type(e).__name__, msg=e)
        return jsonify(edges=None)
    result = consensus(rows) if rows else None
    if result is None or result['links'] is None:
        return jsonify(edges=None)
    return jsonify(edges=in_view(canon_letters, result['links'], t)['edges'])


##### crowd corrections #####

# Players fix Ribbit's word list themselves: the ✗ on a result ("Ribbit
# didn't accept this word") votes it out, and the "Ribbit accepted a word
# that isn't listed?" box under the results votes one in. Mechanics,
# thresholds and the never-tell-players rule live in crowd.py, shared with
# /blossom and /smush; only Ribbit's own tables are touched here.
#
# ✗ sits next to ✓ Found and both hide the word, so some players will ✗
# words they found. crowd.py's per-player daily limit catches the heavy
# cases; the first ✗ in a browser also asks players to keep it for refusals.

RIBBIT_CROWD = crowd.CrowdList('ribbit', on_change=_forget_ribbit_words)

# Ribbit's word lengths: 4 letters up, and the solver stops at 15.
RIBBIT_MIN_LEN, RIBBIT_MAX_LEN = 4, 15
VOTE_ACTIONS = ('reject', 'restore', 'suggest_missing')
# The page's sort chips (sortChipsHtml in ribbit.html), kept in the vote log.
RIBBIT_SORT_MODES = ('len', 'pop')


def _parse_word(value):
    word = parse_letters(value if isinstance(value, str) else None, 'word',
                         max_len=RIBBIT_MAX_LEN).lower()
    if len(word) < RIBBIT_MIN_LEN:
        raise ValidationError(f"word must be at least {RIBBIT_MIN_LEN} letters")
    return word


def _vote_mode(data, dev):
    """The sort chip the player voted from, for the vote log only:
    'dev_<chip>' from /ribbit_dev. None when the page didn't say or said
    something else - never a reason to refuse the vote."""
    chip = data.get('mode')
    if chip not in RIBBIT_SORT_MODES:
        return None
    return ('dev_' if dev else '') + chip


def puzzle_tag(letters):
    """Which board a vote came from: its letters in the shared-board
    canonical orientation (so a turned or mirrored entry matches), as four
    rows joined by colons - the colon is what makes the admin search look
    at boards. '.' is an empty cell."""
    canon = canonical(letters)[0]
    return ':'.join(canon[r * N:(r + 1) * N] for r in range(N))


def _traceable(word, letters, links):
    """Can `word` be traced on this board as the player has it?"""
    nodes = {i: ch for i, ch in enumerate(letters) if ch != '.'}
    found, _ = all_words.ribbit_solver(nodes, links, [word], min_len=RIBBIT_MIN_LEN)
    return bool(found)


def handle_vote(data, dev=False):
    """A crowd vote posted to /ribbit, or /ribbit_dev with `dev`. Every reply
    is the same whether or not the vote changed the list (see crowd.py)."""
    action = data.get('action')
    word = _parse_word(data.get('word'))
    letters, links = _grid_board(data, whole=False)
    tag, mode = puzzle_tag(letters), _vote_mode(data, dev)

    if action == 'reject':
        # Only a word actually on offer, on a board it can be traced on:
        # anything else is not a result this player could have been shown.
        if word in get_ribbit_words() and _traceable(word, letters, links):
            RIBBIT_CROWD.record_vote(word, 'invalid', tag, mode)
        return jsonify(status='success')

    if action == 'restore':
        # Tapping a hidden word back: takes back this player's ✗, if it
        # counted.
        RIBBIT_CROWD.retract_vote(word, 'invalid', tag, mode)
        return jsonify(status='success')

    # Only a word this board allows: blocks most typos and all junk.
    if not _traceable(word, letters, links):
        return jsonify(status='success', result='not_on_board')
    if word in get_ribbit_words():
        return jsonify(status='success', result='already_listed')
    RIBBIT_CROWD.record_vote(word, 'missing', tag, mode)
    return jsonify(status='success', result='recorded')


def handle_action(data, dev=False):
    """A JSON body posted to /ribbit, or /ribbit_dev with `dev`, carrying an
    `action`: a shared board or a crowd vote, not a solve."""
    action = data.get('action')
    if action == 'log_board':
        return log_board(data)
    if action in ('shared_board', 'board_links'):
        if not (dev or SHARED_BOARDS_LIVE):
            # The dark launch: /ribbit logs boards but serves none yet, even
            # to someone calling it directly.
            return jsonify(boards=[]) if action == 'shared_board' else jsonify(edges=None)
        return shared_board(data) if action == 'shared_board' else board_links(data)
    if action in VOTE_ACTIONS:
        return handle_vote(data, dev)
    raise ValidationError('unknown action')


##### admin #####

ADMIN = '/ribbit_admin'
ADMIN_TABS = ('boards', 'reports', 'invalid', 'added')
# The days the Shared boards tab shows: Pacific's last three, and tomorrow
# for players whose date is already ahead.
ADMIN_DAYS_BACK = 2
# How the tab's Add sends back the time of the board it showed, so the
# approval can check that board is still the latest.
_STAMP = '%Y-%m-%d %H:%M:%S'


def _back(**message):
    """Back to /ribbit_admin as the admin left it, with a message."""
    return redirect(crowd.admin_return_url(ADMIN, request.form, **message))


def _admin_days():
    """(days, error): each recent day's boards for the Shared boards tab,
    each with its consensus and a 4x4 grid in the view most players used."""
    today = _pacific_today()
    first, last = today - timedelta(days=ADMIN_DAYS_BACK), today + timedelta(days=1)
    try:
        rows = rows_logged_between(first, last)
    except Exception as e:
        print(f"Error loading ribbit admin boards: {e}")
        return [], f'{type(e).__name__}: {e}'
    days = []
    day = last
    while day >= first:
        _boards, groups = todays_boards(rows, day)
        for g in groups:
            letters, _links = _apply(INVERSES[g['orientation']], g['letters'], ())
            g['grid'] = [letters[r * N:(r + 1) * N].upper() for r in range(N)]
            g['latest_stamp'] = g['latest_at'].strftime(_STAMP) if g['latest_at'] else ''
        if groups:
            days.append({'day': day, 'groups': groups})
        day -= timedelta(days=1)
    return days, None


def _empty_view(active_tab):
    """The word-list half of the page when its tables can't be read, so the
    Shared boards tab still works (and says what's wrong)."""
    pager = {'page': 1, 'pages': 1, 'prev': None, 'next': None, 'numbers': []}
    return {'reports': [], 'invalid_words': [], 'added_words': [],
            'reports_pager': pager, 'invalid_pager': pager, 'added_pager': pager,
            'open_count': 0, 'settled_count': 0, 'invalid_total': 0, 'added_total': 0,
            'show_settled': False, 'settled_url': ADMIN, 'query': '', 'clear_url': ADMIN,
            'keep': {'tab': active_tab}, 'active_tab': active_tab, 'in_both': [],
            'votes_needed': RIBBIT_CROWD.threshold}


@bp.route(ADMIN)
@auth.login_required
def ribbit_admin():
    """Ribbit's corrected word list, what players have reported, and the
    boards players have entered lately."""
    args = request.args.to_dict()
    args.setdefault('tab', 'boards')
    try:
        view, crowd_error = RIBBIT_CROWD.admin_view(args, request.path, tabs=ADMIN_TABS), None
    except Exception as e:
        print(f"Error loading ribbit admin word lists: {e}")
        view = _empty_view(args['tab'] if args['tab'] in ADMIN_TABS else 'boards')
        crowd_error = f'{type(e).__name__}: {e}'
    days, load_error = _admin_days()
    return render_template('ribbit_admin.html', **view,
                           crowd_error=crowd_error, days=days, load_error=load_error,
                           shared_live=SHARED_BOARDS_LIVE,
                           serve_identical=SERVE_IDENTICAL, serve_majority=SERVE_MAJORITY,
                           todays_boards_max=TODAYS_BOARDS_MAX,
                           word_pop=word_pop,
                           crowd_remove_bands=CROWD_REMOVE_VOTES_BY_POP,
                           crowd_remove_rare=CROWD_REMOVE_VOTES_RARE,
                           crowd_add_votes=CROWD_ADD_VOTES,
                           crowd_window_days=CROWD_WINDOW_DAYS)


def _is_letters(value):
    """A canonical board's letters, as the Shared boards tab's forms send them."""
    return len(value) == CELLS and value.isalpha() and value.islower()


@bp.route(ADMIN + '/remove_board', methods=['POST'])
@auth.login_required
def ribbit_remove_board():
    """Delete one day's entries of a board: test boards, or junk."""
    letters = request.form.get('letters', '')
    played_on = request.form.get('played_on', '')
    if not (_is_letters(letters) and _DATE.fullmatch(played_on)):
        return _back(error='Not a board')
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute("DELETE FROM ribbit_boards WHERE letters = %s AND played_on = %s",
                           (letters, date.fromisoformat(played_on)))
            removed = cursor.rowcount
            conn.commit()
    except Exception as e:
        print(f"Error removing ribbit board: {e}")
        return _back(error='Database error')
    return _back(success=f"Removed {removed} entr{'y' if removed == 1 else 'ies'} of that board on {played_on}")


@bp.route(ADMIN + '/approve_board', methods=['POST'])
@auth.login_required
def ribbit_approve_board():
    """Serve the latest board for these letters to every player straight
    away, first of the day's boards and whatever the players drew for these
    letters: for a board James entered himself and trusts (James asked,
    2026-10-18). `latest` is that board's time as
    the page showed it; if a newer board has come in since, nothing is
    approved, so a stranger's board can't be approved in its place."""
    letters = request.form.get('letters', '')
    played_on = request.form.get('played_on', '')
    if not (_is_letters(letters) and _DATE.fullmatch(played_on)):
        return _back(error='Not a board')
    try:
        day = date.fromisoformat(played_on)
    except ValueError:
        return _back(error='Not a board')
    try:
        boards = [row for row in rows_with_letters(letters) if row[SOURCE] != APPROVAL]
        newest = max(boards, key=lambda row: row[UPDATED]) if boards else None
        if newest is None:
            return _back(error='No board to approve for those letters')
        if newest[UPDATED].strftime(_STAMP) != request.form.get('latest'):
            return _back(error='A newer board came in since the page loaded. Check it, then approve again')
        links = _parse_links(newest[LINKS])
        if not _every_tile_linked(links):
            return _back(error="The latest board isn't finished: every tile needs a link")
        store_approval(day, letters, links, newest[ORIENTATION])
    except Exception as e:
        print(f"Error approving ribbit board: {e}")
        return _back(error='Database error')
    return _back(success=f"Approved the board last updated {newest[UPDATED]:%m-%d %H:%M}: its links are served now")


@bp.route(ADMIN + '/unapprove_board', methods=['POST'])
@auth.login_required
def ribbit_unapprove_board():
    """Undo an approval, every day's of these letters: the players' boards
    decide again, and none of them is touched."""
    letters = request.form.get('letters', '')
    if not _is_letters(letters):
        return _back(error='Not a board')
    try:
        removed = remove_approval(letters)
    except Exception as e:
        print(f"Error undoing ribbit approval: {e}")
        return _back(error='Database error')
    if not removed:
        return _back(error='That board was not approved')
    return _back(success="Approval undone: the players' boards decide again")


def _admin_form():
    """The word and its list, or None for the list if no choice was sent.
    Nothing is assumed: a missing choice must not count as a removal."""
    word = request.form.get('word', '').strip().lower()
    kind = request.form.get('word_type')
    return word, kind if kind in ('invalid', 'missing') else None


NO_CHOICE = 'Choose Remove invalid word or Add missing word first'


@bp.route(ADMIN + '/add_word', methods=['POST'])
@auth.login_required
def ribbit_add_word():
    """Remove a word from Ribbit's list ('invalid') or add one ('missing')."""
    word, kind = _admin_form()
    if kind is None:
        return _back(error=NO_CHOICE)
    if not word.isalpha():
        return _back(error='Word must be letters only')
    try:
        RIBBIT_CROWD.admin_set(word, kind)
    except Exception as e:
        print(f"Error adding ribbit word: {e}")
        return _back(error='Database error')
    done = 'added to the word list' if kind == 'missing' else 'removed from the word list'
    return _back(success=f'{word} {done}')


@bp.route(ADMIN + '/remove_word', methods=['POST'])
@auth.login_required
def ribbit_remove_word():
    """Undo an entry in either list, and reset that word's votes."""
    word, kind = _admin_form()
    if kind is None:
        return _back(error=NO_CHOICE)
    if not word.isalpha():
        return _back(error='Word must be letters only')
    try:
        RIBBIT_CROWD.admin_remove(word, kind)
    except Exception as e:
        print(f"Error removing ribbit word: {e}")
        return _back(error='Database error')
    return _back(success=f'{word} undone')
