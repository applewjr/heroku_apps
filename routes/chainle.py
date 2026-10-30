"""Chainle solver data, the solve request, players' reports of refused words,
today's rounds, and /chainle_admin.

The solver route itself is run_chainle in routes/wordgames.py (and James's
copy, /chainle_dev); it hands a round to solve_request here, a round to log or
a request for today's rounds to handle_rounds_action, and a report (any other
JSON body with an `action`) to handle_vote.

Self-contained: the word data ships in datasets/ (see datasets/CHAINLE_DATA.md)
and nothing is fetched at runtime. The solver never contacts any Chainle site.

/chainle_admin has the players' reports, the words taken off or kept on the
solver's list, and a log of rounds the admin plays, to tune the ranking in
functions/chainle.py against. Logged rounds stay in the database: the solver
never shows them and never uses them as answers.
"""

import hashlib
import os
import re
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta
from itertools import permutations
from urllib.parse import urlencode

import numpy as np
from flask import Blueprint, jsonify, redirect, render_template, request

import crowd
from data import data_folder
from extensions import auth, db_cursor, pst_now_str
from functions import chainle as ch
from helpers import ValidationError, parse_iso_day, parse_played_on, release_memory
from helpers import pacific_today as _today
from monitoring import alerts

bp = Blueprint('chainle', __name__)

# Chainle's clues are single words: a bridge round has two or three, the
# closest-meaning round one.
CLUES_PER_KIND = {'bridge': (2, 3), 'meaning': (1, 1)}
MAX_CLUE_LEN = 20
_CLUE = re.compile(r'[A-Za-z]{1,%d}' % MAX_CLUE_LEN)


##### solver data #####

# A module memo, not SimpleCache: SimpleCache pickles, so every get would
# copy ~14 MB of arrays (see routes/guess_the_phrase.py).
_static = {'data': None, 'failed_at': 0.0}
_load_lock = threading.Lock()
# After a failed load, wait this long before trying again, so a broken file
# costs one attempt per window rather than one per request.
LOAD_RETRY_SECONDS = 300


def get_data():
    """The shipped word data, read once per process (~0.1 s, ~14 MB). None
    if it won't load; the page then says the solver is unavailable."""
    if _static['data'] is not None:
        return _static['data']
    with _load_lock:
        if _static['data'] is None and time.time() - _static['failed_at'] > LOAD_RETRY_SECONDS:
            try:
                _static['data'] = ch.Data.from_file(os.path.join(data_folder, 'chainle_data.npz'))
                # Decompressing the file goes through buffers glibc would
                # otherwise keep.
                release_memory()
            except Exception as e:
                _static['failed_at'] = time.time()
                alerts.alert_throttled('chainle_data_failed', sev='crit', exc=type(e).__name__, msg=e)
    return _static['data']


def solver_version():
    """A short code for the solver in this process: a hash of the engine's
    code and its word data, so it changes whenever either does. The admin
    saves it with each round, to tell which version made the picks. None if
    the data won't load."""
    data = get_data()
    if data is None:
        return None
    if _static.get('version') is None:
        h = hashlib.sha1()
        with open(ch.__file__, 'rb') as fh:
            h.update(fh.read())
        for name in ch.FIELDS:
            a = np.ascontiguousarray(getattr(data, name))
            h.update(f'{name} {a.dtype.str} {a.shape}'.encode())
            h.update(a)
        _static['version'] = h.hexdigest()[:8]
    return _static['version']


def page_context():
    """What the page template needs besides the SEO blob. Rendering the page
    never touches the database."""
    data = get_data()
    return {'vocab_size': data.size if data is not None else None}


##### solving #####

def _clue_list(raw, kind):
    """The clue words, lower case, or raise ValidationError."""
    lo, hi = CLUES_PER_KIND[kind]
    if not isinstance(raw, list) or not lo <= len(raw) <= hi:
        count = f'{lo} or {hi} words' if lo != hi else f'{lo} word'
        raise ValidationError(f'a {kind} round takes {count}')
    clues = []
    for clue in raw:
        if not isinstance(clue, str) or not _CLUE.fullmatch(clue.strip()):
            raise ValidationError(f'each clue must be one word of at most {MAX_CLUE_LEN} letters')
        clues.append(clue.strip().lower())
    if len(set(clues)) != len(clues):
        raise ValidationError('the clues must be different words')
    return clues


def parse_request(data):
    """(clues, kind) from a POST body."""
    kind = data.get('kind', 'bridge')
    if kind not in CLUES_PER_KIND:
        raise ValidationError('kind must be bridge or meaning')
    return _clue_list(data.get('clues'), kind), kind


def solve_request(data):
    """The JSON the page renders for one round."""
    clues, kind = parse_request(data)
    solver = get_data()
    if solver is None:
        return jsonify(error='The solver is unavailable right now.'), 503
    removed, kept = get_corrections()
    return jsonify(ch.solve(solver, clues, kind, removed=removed, kept=kept))


##### word corrections #####

# Players report a suggestion the game refused with the box under the
# answers, which votes it off the list (James asked, 2026-10-25). The box
# starts with the last word they copied, and they can type another. The
# mechanics, the thresholds (fewer players for a rarer word) and the rule
# that players are never told the counts live in crowd.py, shared with the
# other word games; only Chainle's own tables are touched here.
#
# /chainle_admin's Remove takes a word off the solver's suggestions, and its
# Allow ('missing' in crowd.py) keeps one on that the -s filter would drop
# (PHYSICS), and players can't vote it off. One list for every Chainle site
# (James's call, 2026-10-22): the copies of the game share its dictionary.
# There is no box for missing words: the solver ranks every word it knows,
# so none can be missing.

# (removed, kept) word sets, memoized in the module like routes/smush.py's
# word list. The memo of how the solver rates logged rounds is dropped with
# it, since corrections change those ranks.
_corrections = {'value': None, 'expires': 0.0}


def _forget_corrections():
    """Make the next get_corrections() re-read the lists."""
    _corrections['expires'] = 0.0
    _assessed.clear()


def get_corrections():
    """(removed, kept): the words players or /chainle_admin took off the
    list, and the words the admin keeps on it, lower case. Refreshed every
    12 hours, or straight away after a change; none while the DB has never
    been reachable (see crowd.CrowdList.corrected)."""
    return CHAINLE_CROWD.corrected(
        _corrections, lambda removed, kept: (frozenset(removed), frozenset(kept)))


CHAINLE_CROWD = crowd.CrowdList('chainle', on_change=_forget_corrections)

CHAINLE_VOTE_ACTIONS = ('refuse',)
MAX_WORD_LEN = 40
_WORD = re.compile(r'[A-Za-z]{1,%d}' % MAX_WORD_LEN)


def _puzzle_tag(clues):
    """The round a report came from: its clues in alphabetical order, joined
    by ':', so the admin's colon search finds it."""
    return ':'.join(sorted(clues))


def _offered(clues, kind):
    """The words the page lists for this round, lower case."""
    solver = get_data()
    if solver is None:
        return set()
    removed, kept = get_corrections()
    rows = ch.solve(solver, clues, kind, removed=removed, kept=kept)['suggestions']
    return {r['word'].lower() for r in rows}


def handle_vote(data, dev=False):
    """A report from the box on /chainle, or on /chainle_dev with `dev`: the
    game refused this word.

    It only counts for a word this round lists, the top 10 for its clues:
    anything else is no suggestion of ours, and counting it would let anyone
    vote words off the list. The reply never says whether the report changed
    the list (see crowd.py): 'recorded' either way, or 'not_suggested' for a
    word the round doesn't list. A word already taken off is 'recorded' too,
    for a player whose list was loaded before it went. The vote log's mode is
    the round's kind, 'dev_' first from /chainle_dev (as /smush_dev's votes).
    """
    if data.get('action') not in CHAINLE_VOTE_ACTIONS:
        raise ValidationError('unknown action')
    clues, kind = parse_request(data)
    word = data.get('word')
    if not isinstance(word, str) or not _WORD.fullmatch(word.strip()):
        raise ValidationError('word must be one word, letters only')
    word = word.strip().lower()
    removed, _kept = get_corrections()
    if word in removed:
        return jsonify(status='success', result='recorded')
    if word not in _offered(clues, kind):
        return jsonify(status='success', result='not_suggested')
    CHAINLE_CROWD.record_vote(word, 'invalid', _puzzle_tag(clues), ('dev_' + kind) if dev else kind)
    return jsonify(status='success', result='recorded')


##### today's rounds #####

# Players who type a round's clues feed it to the players after them (James
# asked, 2026-10-28), as on /smush and /ribbit. Chainle runs on several sites,
# each with its own daily rounds (none of the rounds James logged on two sites
# on one day matched), so a day can have several real sets, as on Ribbit.
# Players are never asked which site they play on (James: no friction for
# players). Under an empty round the page lists the clues entered for that
# round today, and the player taps the one in their game; that fills every
# empty round of that day's set.
#
# A round is its clue words. Once SERVE_CROWD players, one per network, have
# typed the same words, the round counts; two rounds belong to one day's set
# once LINK_MIN players typed both, since a player's rounds come from one
# game. Each set keeps a round only where one set of clues has more than half
# of its players, and a players' set is offered only once all five of its
# rounds are settled (James, 2026-10-29: a set of one round was a few players
# still on the day before's puzzle). James's approvals in /chainle_admin come
# first, labelled with their site, and the players' set that shares a round
# with one fills its blank rounds, complete or not.
#
# Everything keys on the player's own date, as on /smush. A site that rolls
# over at a fixed time (chainle.io at midnight Eastern, per third-party
# pages; chainle.org at 00:00 UTC, as its own page says) only means an extra
# set for a few hours, yesterday's or tomorrow's beside today's: players pick
# by the clues on their screen, so there is no rule about older puzzles. Each round keeps the player's UTC offset, to learn
# when each site rolls over. Players are never told how a round is chosen
# (see crowd.py on why).
#
# Live on /chainle since 2026-10-29 (James's call), after a dark launch from
# 2026-10-28 in which /chainle only logged rounds and /chainle_dev, his
# password-only copy, offered them.

# The sites an approval can be for (James's three, 2026-10-28): one Approve
# button each on /chainle_admin, which also links each game. The players'
# sets need none: they offer whatever rounds players type.
BOARD_SITES = ('chainle.io', 'chainle.org', 'chaindle.org')
# Players (one per network) typing the same clues for a round to count (James
# asked for a number, 2026-10-28). Two strangers typing the same words is
# strong evidence on its own, but one person on two networks makes 2, and an
# offer shows players' words to other players. /chainle's traffic is thin
# (about 126 real views a day, 3-7 an hour), so 5 would rarely settle the
# smaller sites. Replayed on the dark logs on 2026-10-29, the first full
# day (84 players): at every value from 2 to 5 the players settled one set,
# the five rounds James approved for chainle.io, and nothing else was ever
# offered. It was first offered at 00:58 Pacific with 2 or 3, 02:37 with 4
# and 05:02 with 5, and 56, 51 and 42 of its 69 players came after that. A
# round of the day before's puzzle, typed by players whose date had rolled
# over, reached 3 players too; its set never had all five rounds.
SERVE_CROWD = 3
# Players who typed both of two rounds, to make them one day's set.
LINK_MIN = 2
# The most sets one day offers: approvals first, then the players' sets.
TODAYS_SETS_MAX = 3
# 'typed' = entered by the player; 'edited' = loaded from today's rounds,
# then changed. A round loaded and left as it is is never logged, so an offer
# can't vote for itself.
ROUND_SOURCES = ('typed', 'edited')
# An approval from /chainle_admin is rows of its own in chainle_boards, with
# this as their source and in place of a player hash: a real hash is 32 hex
# characters, and players can only send ROUND_SOURCES. Never a player.
APPROVAL = 'admin'
# Live on /chainle (James's call, 2026-10-29). False goes back to the dark
# launch: /chainle logs rounds but offers none, even to a direct call.
# /chainle_dev offers them either way.
CHAINLE_ROUNDS_LIVE = True
# How long a day's sets are reused before they are read again, and how long a
# failed read waits before the next try (one gunicorn worker, so this
# module's memo is the only copy).
ROUNDS_CACHE_SECONDS = 60
ROUNDS_RETRY_SECONDS = 300
# The days the admin tab shows: Pacific's last three, and tomorrow for
# players whose date is already ahead.
ADMIN_DAYS_BACK = 2
# What a JSON body to /chainle asks for here, not a report.
ROUND_ACTIONS = ('log_round', 'todays_rounds')
ROUND_SLOTS = (1, 2, 3, 4, 5)
# A real clock is at most 14 hours from UTC.
MAX_UTC_OFFSET = 14 * 60

# The columns every read of chainle_boards returns, in this order. site is ''
# on players' rows, prefix_hash NULL on approvals.
_ROUND_COLUMNS = 'played_on, voter_hash, prefix_hash, site, round_no, clues, source, updated_at'
PLAYED_ON, VOTER, PREFIX, SITE, ROUND_NO, CLUES, SOURCE, UPDATED = range(8)
_CLUE_TEXT = re.compile(r'[A-Z]{1,%d}( [A-Z]{1,%d}){0,2}' % (MAX_CLUE_LEN, MAX_CLUE_LEN))

# day -> (expires, sets): what _sets_for last offered for that date, as the
# page gets them. time.monotonic() clock.
_sets_cache = {}


def clue_text(clues):
    """A round as stored and shown: its clue words upper case, one space
    apart, in the order typed, which is the game's order as players see it
    (James, 2026-10-28: DOLLAR then CONSUMPTION must stay that way round)."""
    return ' '.join(c.upper() for c in clues)


def clue_key(text):
    """What makes two rounds the same: their words in any order, so players
    who typed them the other way round still agree."""
    return ' '.join(sorted(text.split()))


def _most_typed(texts):
    """The order most of them typed a round in; on a tie, the first
    alphabetically, so the tab and the page always agree."""
    counts = Counter(texts)
    return min(counts, key=lambda text: (-counts[text], text))


def _any_clues(raw):
    """One to three different clue words, lower case (see _clue_list)."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= 3:
        raise ValidationError('clues must be a list of 1 to 3 words')
    return _clue_list(raw, 'meaning' if len(raw) == 1 else 'bridge')


def _known(clues):
    """Every clue word is one the solver knows. The solver's list leaves out
    offensive words (datasets/CHAINLE_DATA.md), so they never reach an offer."""
    solver = get_data()
    return solver is not None and all(solver.id(c) >= 0 for c in clues)


def _utc_offset(value):
    """The player's clock in minutes ahead of UTC, or None for anything else."""
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) <= MAX_UTC_OFFSET:
        return value
    return None


def _fetch(sql, params):
    with db_cursor() as (conn, cursor):
        cursor.execute(sql, params)
        return cursor.fetchall()


def rows_on(first, last):
    """Every row, players' and approvals, dated between these days."""
    return _fetch(f"SELECT {_ROUND_COLUMNS} FROM chainle_boards WHERE played_on BETWEEN %s AND %s",
                  (first, last))


def store_round(day, voter, prefix, round_no, clues, utc_offset, source):
    """Log one player's round, replacing their earlier entry in that tab that
    day. `voter` is their crowd.player_hash, `prefix` their network's
    crowd.prefix_hash. Inline, not on the write-behind queue: a missing table
    there would drop the rest of its batch, page visits included.
    Best-effort, like a vote."""
    now = pst_now_str()
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute(
                """INSERT INTO chainle_boards (played_on, voter_hash, prefix_hash, site, round_no,
                       clues, utc_offset, source, created_at, updated_at)
                   VALUES (%s, %s, %s, '', %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE clues = new.clues, utc_offset = new.utc_offset,
                       source = new.source, prefix_hash = new.prefix_hash,
                       updated_at = new.updated_at""",
                (day, voter, prefix, round_no, clues, utc_offset, source, now, now))
            conn.commit()
    except Exception as e:
        alerts.alert_throttled('chainle_board_log_failed', exc=type(e).__name__, msg=e)


def store_approval(day, site, rounds):
    """Make these rounds, {round_no: clue text}, `site`'s set for players
    whose date is `day`. An approval is a whole set (James, 2026-10-28): any
    rounds approved for that site and day before are replaced, not merged.
    Raises, unlike store_round: the admin page says whether it worked."""
    now = pst_now_str()
    with db_cursor() as (conn, cursor):
        cursor.execute(
            "DELETE FROM chainle_boards WHERE played_on = %s AND site = %s AND source = %s",
            (day, site, APPROVAL))
        for round_no, clues in sorted(rounds.items()):
            cursor.execute(
                """INSERT INTO chainle_boards (played_on, voter_hash, site, round_no, clues,
                       source, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE clues = new.clues, updated_at = new.updated_at""",
                (day, APPROVAL, site, round_no, clues, APPROVAL, now, now))
        conn.commit()


def remove_approval(day, site):
    """Take back every approved round of `site` on `day`; players' rounds
    stay. Returns how many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute(
            "DELETE FROM chainle_boards WHERE played_on = %s AND site = %s AND source = %s",
            (day, site, APPROVAL))
        removed = cursor.rowcount
        conn.commit()
    return removed


def remove_rounds(day, clue_texts):
    """Delete every player's entry of these rounds on `day`, whichever order
    each player typed the words in: a players' set James rejects, or test
    rounds (staging writes this table too). Approvals stay. Returns how many
    rows went."""
    texts = sorted({' '.join(p) for text in clue_texts for p in permutations(text.split())})
    marks = ', '.join(['%s'] * len(texts))
    with db_cursor() as (conn, cursor):
        cursor.execute(
            f"DELETE FROM chainle_boards WHERE played_on = %s AND source <> %s AND clues IN ({marks})",
            (day, APPROVAL, *texts))
        removed = cursor.rowcount
        conn.commit()
    return removed


def remove_players(day, voters):
    """Delete these players' entries on `day`: a set they typed that James
    rejects. Approvals stay. Returns how many rows went."""
    marks = ', '.join(['%s'] * len(voters))
    with db_cursor() as (conn, cursor):
        cursor.execute(
            f"DELETE FROM chainle_boards WHERE played_on = %s AND source <> %s AND voter_hash IN ({marks})",
            (day, APPROVAL, *voters))
        removed = cursor.rowcount
        conn.commit()
    return removed


def _round_of(clues, tabs):
    """Which round a round's players say it is. Three clues are round 4 and
    one is round 5: every site's day is three pairs, a triple, then the
    closest meaning. A pair goes in the latest of tabs 1-3 that at least two
    of its players typed it in, else the latest any did: players who arrive
    mid-game type a later round into tab 1, never an earlier round into a
    later tab. `tabs` counts the tabs its players used."""
    words = clues.count(' ') + 1
    if words != 2:
        return 4 if words == 3 else 5
    pair_tabs = Counter()
    for tab, n in tabs.items():
        pair_tabs[min(tab, 3)] += n
    shared = [tab for tab, n in pair_tabs.items() if n >= 2]
    return max(shared or pair_tabs)


def round_groups(rows, day):
    """The players' rounds on `day`, one group per set of clue words in any
    order (clue_key), most players first: each {'key', 'clues' (in the order
    most of its players typed them: _most_typed), 'label' (as the admin tab
    shows it), 'networks' (who typed it, one per network:
    crowd.one_per_network), 'players', 'tabs' ([(tab, players)]), 'round'
    (_round_of), 'last_at'}."""
    by_key = {}
    for row in rows:
        if row[PLAYED_ON] == day and row[SOURCE] in ROUND_SOURCES:
            by_key.setdefault(clue_key(row[CLUES]), []).append(row)
    groups = []
    for key, group_rows in by_key.items():
        networks = crowd.one_per_network(group_rows, VOTER, PREFIX, UPDATED)
        tabs = Counter(row[ROUND_NO] for row in networks.values())
        clues = _most_typed(row[CLUES] for row in networks.values())
        groups.append({'key': key, 'clues': clues, 'label': clues.replace(' ', ' + '),
                       'networks': set(networks), 'players': len(networks),
                       'tabs': sorted(tabs.items()), 'round': _round_of(clues, tabs),
                       'last_at': max(row[UPDATED] for row in group_rows)})
    groups.sort(key=lambda g: (-g['players'], g['round'], g['key']))
    return groups


def _players_sets(groups):
    """The players' sets for one day, most players first. The groups that
    count (SERVE_CROWD players) are joined wherever LINK_MIN players typed
    both, and each joined lot keeps a round where one group has more than
    half of that round's players in it; a tie leaves the round blank. Each
    set is {'rounds': {round: group}, 'groups', 'players'}."""
    counted = [g for g in groups if g['players'] >= SERVE_CROWD]
    parent = list(range(len(counted)))

    def top(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(counted):
        for j in range(i + 1, len(counted)):
            if len(a['networks'] & counted[j]['networks']) >= LINK_MIN:
                parent[top(i)] = top(j)
    joined = {}
    for i, g in enumerate(counted):
        joined.setdefault(top(i), []).append(g)

    sets = []
    for members in joined.values():
        rounds = {}
        for slot in ROUND_SLOTS:
            here = [g for g in members if g['round'] == slot]
            if not here:
                continue
            best = max(here, key=lambda g: (g['players'], g['key']))
            players = len(set().union(*(g['networks'] for g in here)))
            if 2 * best['players'] > players:
                rounds[slot] = best
        if rounds:
            sets.append({'rounds': rounds, 'groups': members,
                         'players': len(set().union(*(g['networks'] for g in members)))})
    sets.sort(key=lambda s: (-s['players'], min(s['rounds'])))
    return sets


def set_numbers(sets):
    """{clue_key: the 1-based number of the first set it's offered in}."""
    where = {}
    for n, s in enumerate(sets, 1):
        for clues in s['rounds'].values():
            where.setdefault(clue_key(clues), n)
    return where


def todays_sets(rows, day):
    """The sets to offer players whose date is `day`, and what the admin tab
    shows about that day.

    rows -- chainle_boards rows (players' and approvals) dated `day`, and
            any others (they're skipped).

    James's approvals come first, one set per site, in the order he first
    approved them; a players' set that shares a round with an approval fills
    that approval's blank rounds and isn't offered on its own. Then the
    players' sets with all five rounds, most players first, at most
    TODAYS_SETS_MAX in all.
    Returns (sets, groups): sets is a list of {'site' ('' unless approved),
    'rounds' ({round: clue text}), 'from' ({round: 'approval' or
    'players'}), 'players' (None on an approval)}; groups is every players'
    round of the day (round_groups) with 'why' (None when offered) and
    'set_no' (1-based, the set it is offered in, or None).
    """
    groups = round_groups(rows, day)
    players_sets = _players_sets(groups)

    approvals = {}
    for row in rows:
        if row[PLAYED_ON] == day and row[SOURCE] == APPROVAL:
            a = approvals.setdefault(row[SITE], {'site': row[SITE], 'rounds': {}, 'from': {},
                                                 'players': None, 'first': row[UPDATED]})
            a['rounds'][row[ROUND_NO]] = row[CLUES]
            a['from'][row[ROUND_NO]] = 'approval'
            a['first'] = min(a['first'], row[UPDATED])
    offered = sorted(approvals.values(), key=lambda a: (a['first'], a['site']))
    covered = {}
    for a in offered:
        approved = {clue_key(clues) for clues in a['rounds'].values()}
        for s in players_sets:
            if id(s) in covered or not approved & {g['key'] for g in s['rounds'].values()}:
                continue
            covered[id(s)] = a['site']
            for slot, g in s['rounds'].items():
                if slot not in a['rounds']:
                    a['rounds'][slot] = g['clues']
                    a['from'][slot] = 'players'
    for s in players_sets:
        if id(s) not in covered and len(s['rounds']) == len(ROUND_SLOTS):
            offered.append({'site': '', 'players': s['players'],
                            'rounds': {slot: g['clues'] for slot, g in s['rounds'].items()},
                            'from': {slot: 'players' for slot in s['rounds']}})

    sets = offered[:TODAYS_SETS_MAX]
    where = set_numbers(sets)
    in_players_set = {g['key']: s for s in players_sets for g in s['groups']}
    for g in groups:
        g['set_no'] = where.get(g['key'])
        s = in_players_set.get(g['key'])
        if g['set_no'] is not None:
            g['why'] = None
        elif g['players'] < SERVE_CROWD:
            short = SERVE_CROWD - g['players']
            g['why'] = f"needs {short} more player{'' if short == 1 else 's'}"
        elif s is None or s['rounds'].get(g['round']) is not g:
            g['why'] = f"tied with, or behind, another round {g['round']}"
        elif id(s) in covered:
            g['why'] = f"your {covered[id(s)]} approval has its own round {g['round']}"
        elif len(s['rounds']) < len(ROUND_SLOTS):
            g['why'] = f"its set has only {len(s['rounds'])} of {len(ROUND_SLOTS)} rounds"
        else:
            g['why'] = 'past the limit of sets a day'
    for s in sets:
        s.pop('first', None)
    return sets, groups


def typed_sets(rows, day):
    """What players typed on `day`, each player's rounds as one set, the same
    set from several players together (word order aside), most players
    first: each {'rounds' ({tab: clue text}, as the latest of them typed
    it), 'keys' ({tab: clue_key}), 'voters', 'players' (networks),
    'last_at'}. The admin tab's unit for approving or removing what players
    typed."""
    mine = {}
    for row in rows:
        if row[PLAYED_ON] == day and row[SOURCE] in ROUND_SOURCES:
            mine.setdefault(row[VOTER], []).append(row)
    alike = {}
    for voter, voter_rows in mine.items():
        keys = {row[ROUND_NO]: clue_key(row[CLUES]) for row in voter_rows}
        latest = max(voter_rows, key=lambda row: row[UPDATED])
        entry = alike.setdefault(tuple(sorted(keys.items())),
                                 {'keys': keys, 'voters': [], 'networks': set(), 'last_at': None})
        entry['voters'].append(voter)
        entry['networks'].add(latest[PREFIX] or voter)
        if entry['last_at'] is None or latest[UPDATED] > entry['last_at']:
            entry['last_at'] = latest[UPDATED]
            entry['rounds'] = {row[ROUND_NO]: row[CLUES] for row in voter_rows}
    typed = [{'rounds': e['rounds'], 'keys': e['keys'], 'voters': sorted(e['voters']),
              'players': len(e['networks']), 'last_at': e['last_at']} for e in alike.values()]
    typed.sort(key=lambda t: t['last_at'], reverse=True)
    typed.sort(key=lambda t: (-t['players'], -len(t['rounds'])))
    return typed


def page_sets(sets):
    """The sets as the page gets them: {'site', 'rounds': {'1': [clue words]}}."""
    return [{'site': s['site'],
             'rounds': {str(slot): clues.split() for slot, clues in sorted(s['rounds'].items())}}
            for s in sets]


def _sets_for(day):
    """Today's sets for players whose date is `day`, as the page gets them.
    Served from the memo while fresh (ROUNDS_CACHE_SECONDS), else read again;
    never raises. When an entry goes stale, the first request pushes it out 15
    seconds before reading, and every other request keeps the stale sets
    meanwhile, so a slow database holds up one request, not all of them. A
    failed read is remembered too, for ROUNDS_RETRY_SECONDS."""
    now = time.monotonic()
    entry = _sets_cache.get(day)
    if entry is not None and now < entry[0]:
        return entry[1]
    if entry is not None:
        _sets_cache[day] = (now + 15, entry[1])
    try:
        sets = page_sets(todays_sets(rows_on(day, day), day)[0])
        _sets_cache[day] = (time.monotonic() + ROUNDS_CACHE_SECONDS, sets)
    except Exception as e:
        alerts.alert_throttled('chainle_board_read_failed', exc=type(e).__name__, msg=e)
        sets = []
        _sets_cache[day] = (time.monotonic() + ROUNDS_RETRY_SECONDS, sets)
    for old in [d for d in list(_sets_cache) if abs((d - day).days) > 2]:
        _sets_cache.pop(old, None)
    return sets


def _log_round(data, dev):
    """A round a player used, sent the first time they take a hint on it or
    leave it solved. Always the same reply, stored or not: nothing is stored
    from /chainle_dev (James's own play), for a word the solver doesn't know,
    or for a date that can't be today anywhere."""
    round_no = data.get('round_no')
    if not (isinstance(round_no, int) and not isinstance(round_no, bool) and round_no in ROUND_SLOTS):
        raise ValidationError('round_no must be 1 to 5')
    clues = _any_clues(data.get('clues'))
    source = data.get('source')
    if source not in ROUND_SOURCES:
        raise ValidationError('source must be typed or edited')
    day = parse_played_on(data.get('played_on'), _today())
    if day is not None and not dev and _known(clues):
        store_round(day, crowd.player_hash('chainle'), crowd.prefix_hash('chainle'), round_no,
                    clue_text(clues), _utc_offset(data.get('utc_offset')), source)
    return jsonify(status='success')


def handle_rounds_action(data, dev=False):
    """A round to log, or a request for today's rounds, posted to /chainle
    (or /chainle_dev with `dev`). Today's rounds are an empty list while dark,
    even to a direct call; /chainle_dev gets them all the same."""
    if data.get('action') == 'log_round':
        return _log_round(data, dev)
    day = parse_played_on(data.get('played_on'), _today())
    if not (dev or CHAINLE_ROUNDS_LIVE) or day is None:
        return jsonify(sets=[])
    return jsonify(sets=_sets_for(day))


##### admin: the round log #####

ADMIN = '/chainle_admin'
SITES = ('chainle.io', 'chainle.org', 'chaindle.org', 'other')
# The day's puzzle, an unlimited one, or a past day's from the archive.
MODES = ('daily', 'unlimited', 'archive')
# The daily game's rounds: 1-3 two-word bridges, 4 a three-word bridge, 5 the
# closest meaning. The form can override the kind for a site that differs.
ROUND_KIND = {1: 'bridge', 2: 'bridge', 3: 'bridge', 4: 'bridge', 5: 'meaning'}
ROUND_CLUES = {1: 2, 2: 2, 3: 2, 4: 3, 5: 1}
# The summaries cover the last SHOW_ROUNDS rounds; the page lists only the
# newest RECENT_ROUNDS of them.
SHOW_ROUNDS = 100
RECENT_ROUNDS = 10
MAX_NOTES = 200
FIRST_DAY = date(2026, 1, 1)

# A row is one round, keyed by the day played, site, round and clues
# (mysql_tables/chainle_rounds_unlimited.sql): saving the same round again
# replaces it, and another puzzle the same day adds a row.
ROUND_COLUMNS = ('played_on', 'site', 'mode', 'round_no', 'kind', 'clues', 'answer', 'guess',
                 'score', 'closeness', 'game_rank', 'rank_of', 'notes', 'created_at')
ROUND_KEY = ('played_on', 'site', 'round_no', 'clues')
# What the solver made of a round when it was logged
# (mysql_tables/chainle_rounds_version.sql): its version (solver_version()),
# its first pick and its rank for the game's answer. Saved and shown once
# that SQL has run; until then the admin works as before without them.
VERSION_COLUMNS = ('solver_version', 'solver_top', 'answer_rank')


def _upsert(columns):
    return (f"INSERT INTO chainle_rounds ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))}) AS new ON DUPLICATE KEY UPDATE "
            + ', '.join(f'{c} = new.{c}' for c in columns if c not in ROUND_KEY))


UPSERT_ROUND = _upsert(ROUND_COLUMNS)
UPSERT_ROUND_VERSIONED = _upsert(ROUND_COLUMNS + VERSION_COLUMNS)
# Per solver version, newest first: rounds, your average score, rounds it
# could be judged on, its first pick right, its answer in the top 3, and
# rounds you took its first pick.
VERSION_SUMMARY = """
SELECT solver_version, COUNT(*), AVG(score), SUM(solver_top IS NOT NULL AND answer IS NOT NULL),
    SUM(solver_top = answer), SUM(answer_rank <= 3), SUM(guess = solver_top), MIN(played_on), MAX(played_on)
FROM chainle_rounds WHERE solver_version IS NOT NULL
GROUP BY solver_version ORDER BY MAX(created_at) DESC LIMIT 10
"""


def _has_version_columns(cursor):
    """True once mysql_tables/chainle_rounds_version.sql has run."""
    cursor.execute("SHOW COLUMNS FROM chainle_rounds LIKE 'solver_version'")
    return bool(cursor.fetchall())


def _versions(rows):
    return [{'version': v, 'rounds': n, 'avg': round(avg) if avg is not None else None,
             'judged': int(judged or 0), 'top1': int(top1 or 0), 'top3': int(top3 or 0),
             'took': int(took or 0), 'first': str(first), 'last': str(last)}
            for v, n, avg, judged, top1, top3, took, first, last in rows]


def _back_to_log(**message):
    """Back to the admin page's Round Log tab, with a success or error
    message (and any form defaults: see _form_defaults)."""
    message = {k: v for k, v in dict(message, tab='log').items() if v}
    return redirect(ADMIN + '?' + urlencode(message))


def _back(**message):
    """Back to /chainle_admin as the admin left it after a word action, with
    a message (see crowd.admin_return_url)."""
    return redirect(crowd.admin_return_url(ADMIN, request.form, **message))


def _form_defaults(args):
    """The log form's date and round. After a save it's the same day's next
    round, so playing all five is one flow; otherwise today's round 1."""
    day = args.get('date', '')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        day = _today().isoformat()
    try:
        round_no = int(args.get('round', ''))
    except ValueError:
        round_no = 1
    return day, round_no if round_no in ROUND_KIND else 1


def _word(form, field, label):
    """An optional single word from the form, upper case, or None."""
    value = form.get(field, '').strip()
    if not value:
        return None
    if not re.fullmatch(r'[A-Za-z]{1,%d}' % MAX_WORD_LEN, value):
        raise ValidationError(f'{label} must be one word, letters only')
    return value.upper()


def _closeness(form, count):
    """The game's closeness to each clue, as it shows them ('0.54 0.08'), or
    None. One number per clue or none at all, each from 0 to 1."""
    raw = [form.get(f'link{n}', '').strip() for n in range(1, count + 1)]
    if not any(raw):
        return None
    values = []
    for r in raw:
        try:
            v = float(r)
        except ValueError:
            v = -1.0
        if not 0 <= v <= 1:
            raise ValidationError('Closeness takes a number from 0 to 1 for every clue')
        values.append(f'{v:.3f}'.rstrip('0').rstrip('.'))
    return ' '.join(values)


def _game_rank(form):
    """(the guess's rank in the game's list, the list's size), either None."""
    rank = form.get('game_rank', '').strip().lstrip('#')
    of = form.get('rank_of', '').strip().replace(',', '')
    for value, label in ((rank, "The game's rank"), (of, 'The word count')):
        if value and not (value.isdigit() and int(value) >= 1):
            raise ValidationError(f'{label} must be a whole number')
    rank, of = (int(rank) if rank else None), (int(of) if of else None)
    if rank and of and rank > of:
        raise ValidationError("The game's rank can't be past its word count")
    return rank, of


def parse_round(form):
    """A round from the admin form, as {column: value} (ROUND_COLUMNS), or
    raise ValidationError with a message for the page. The date is the day
    it was played: unlimited puzzles have no date of their own."""
    raw_day = form.get('played_on', '').strip()
    try:
        played_on = datetime.strptime(raw_day, '%Y-%m-%d').date() if raw_day else _today()
    except ValueError:
        raise ValidationError('Date must look like 2026-10-14')
    if not FIRST_DAY <= played_on <= _today() + timedelta(days=1):
        raise ValidationError('Date is out of range')
    site = form.get('site', '')
    if site not in SITES:
        raise ValidationError('Choose the site you played on')
    mode = form.get('mode') or 'daily'
    if mode not in MODES:
        raise ValidationError('Choose daily or unlimited')
    try:
        round_no = int(form.get('round_no', ''))
    except ValueError:
        round_no = 0
    if round_no not in ROUND_KIND:
        raise ValidationError('Round must be 1 to 5')
    kind = form.get('kind') or ROUND_KIND[round_no]
    if kind not in CLUES_PER_KIND:
        raise ValidationError('Unknown round type')
    clues = [form.get(f'clue{n}', '').strip() for n in (1, 2, 3)]
    try:
        clues = _clue_list([c for c in clues if c], kind)
    except ValidationError as e:
        raise ValidationError(str(e).capitalize())
    answer = _word(form, 'answer', "The game's answer")
    guess = _word(form, 'guess', 'Your guess')
    raw_score = form.get('score', '').strip()
    if raw_score and not (raw_score.isdigit() and int(raw_score) <= 1000):
        raise ValidationError('Score must be a whole number from 0 to 1000')
    notes = form.get('notes', '').strip()[:MAX_NOTES] or None
    game_rank, rank_of = _game_rank(form)
    return {
        'played_on': played_on.isoformat(), 'site': site, 'mode': mode, 'round_no': round_no,
        'kind': kind, 'clues': ' '.join(c.upper() for c in clues), 'answer': answer, 'guess': guess,
        'score': int(raw_score) if raw_score else None, 'closeness': _closeness(form, len(clues)),
        'game_rank': game_rank, 'rank_of': rank_of, 'notes': notes, 'created_at': pst_now_str(),
    }


# How the solver rates each logged round, by (kind, clues, answer, guess):
# the rows don't change, so the admin page only pays for new ones. Cleared
# when full; the data itself only changes on a deploy.
_assessed = {}
ASSESS_MEMO_MAX = 1000


def _assess(data, kind, clues, answer, guess):
    key = (kind, clues, answer, guess)
    if key not in _assessed:
        if len(_assessed) >= ASSESS_MEMO_MAX:
            _assessed.clear()
        removed, kept = get_corrections()
        _assessed[key] = ch.assess(data, clues.lower().split(), kind, [answer, guess],
                                   removed=removed, kept=kept)
    return _assessed[key]


def _round_type(kind, clues):
    if kind == 'meaning':
        return 'Closest meaning'
    return 'Three-word bridge' if len(clues) == 3 else 'Two-word bridge'


def assess_rounds(data, rows):
    """The admin page's rows and summary: for each logged round, where the
    solver ranks the game's answer and your guess, and its own first pick."""
    rounds, tally, scores = [], {}, []
    for (rid, played_on, site, mode, round_no, kind, clues, answer, guess, score, closeness, game_rank,
         rank_of, notes, version, top_then, rank_then) in rows:
        got = _assess(data, kind, clues, answer, guess) if data is not None else None
        r = {
            'id': rid, 'played_on': str(played_on), 'site': site, 'mode': mode, 'round_no': round_no,
            'type': _round_type(kind, clues.split()), 'clues': clues.split(), 'answer': answer,
            'guess': guess, 'score': score, 'closeness': closeness.split() if closeness else [],
            'game_rank': game_rank, 'rank_of': rank_of, 'notes': notes, 'known': got is not None,
            'top': got['top'] if got else None,
            'rank': got['ranks'].get(answer) if got and answer else None,
            'guess_rank': got['ranks'].get(guess) if got and guess else None,
            # The version it was logged with, and what that version said.
            'version': version, 'top_then': top_then, 'rank_then': rank_then,
        }
        rounds.append(r)
        if score is not None:
            scores.append(score)
        if got and answer:
            t = tally.setdefault(r['type'], {'rounds': 0, 'top1': 0, 'top3': 0, 'top10': 0})
            t['rounds'] += 1
            for name, cut in (('top1', 1), ('top3', 3), ('top10', 10)):
                t[name] += r['rank'] is not None and r['rank'] <= cut
    order = ('Two-word bridge', 'Three-word bridge', 'Closest meaning')
    return {
        'rounds': rounds,
        'summary': [(name, tally[name]) for name in order if name in tally],
        'avg_score': round(sum(scores) / len(scores)) if scores else None,
    }


ADMIN_TABS = ('rounds', 'reports', 'invalid', 'added', 'log')


def _round_log():
    """(context, error) for the Round Log tab: the newest logged rounds with
    how the solver rates each, and the summary by solver version."""
    try:
        with db_cursor() as (conn, cursor):
            versioned = _has_version_columns(cursor)
            cursor.execute("SELECT COUNT(*) FROM chainle_rounds")
            total = cursor.fetchone()[0]
            cursor.execute(
                "SELECT id, played_on, site, mode, round_no, kind, clues, answer, guess, score, "
                "closeness, game_rank, rank_of, notes, "
                + (', '.join(VERSION_COLUMNS) if versioned else 'NULL, NULL, NULL') +
                " FROM chainle_rounds ORDER BY played_on DESC, created_at DESC, id DESC LIMIT %s",
                (SHOW_ROUNDS,))
            rows = cursor.fetchall()
            versions = []
            if versioned:
                cursor.execute(VERSION_SUMMARY)
                versions = _versions(cursor.fetchall())
    except Exception as e:
        print(f"Error loading chainle round log: {e}")
        return ({'total': 0, 'versioned': False, 'versions': [], 'rounds': [], 'summary': [],
                 'avg_score': None}, f'{type(e).__name__}: {e}')
    return {'total': total, 'versioned': versioned, 'versions': versions,
            **assess_rounds(get_data(), rows)}, None


@bp.route(ADMIN)
@auth.login_required
def chainle_admin():
    """Today's rounds, what players reported, the removed and allowed words,
    and the round log. Each part still shows if another's tables can't be
    read."""
    # With no tab asked for, it opens on Today's Rounds, the first tab
    # (James asked, 2026-10-29). Logging a round still comes back to the
    # Round Log (_back_to_log).
    args = request.args.to_dict()
    args.setdefault('tab', 'rounds')
    try:
        view, crowd_error = CHAINLE_CROWD.admin_view(args, request.path, tabs=ADMIN_TABS), None
    except Exception as e:
        print(f"Error loading chainle admin word lists: {e}")
        view = CHAINLE_CROWD.empty_view(ADMIN, args['tab'] if args['tab'] in ADMIN_TABS else 'rounds')
        crowd_error = f'{type(e).__name__}: {e}'
    log, log_error = _round_log()
    rounds_days, rounds_error = _admin_rounds()
    form_date, form_round = _form_defaults(request.args)
    return render_template('chainle_admin.html', **view, **log, crowd_error=crowd_error,
                           log_error=log_error, form_date=form_date, form_round=form_round,
                           sites=SITES, modes=MODES, round_clues=ROUND_CLUES,
                           data_ok=get_data() is not None, recent=RECENT_ROUNDS,
                           version=solver_version(), rounds_days=rounds_days,
                           rounds_error=rounds_error, rounds_live=CHAINLE_ROUNDS_LIVE,
                           board_sites=BOARD_SITES, serve_crowd=SERVE_CROWD, link_min=LINK_MIN,
                           todays_sets_max=TODAYS_SETS_MAX, pacific_today=_today())


@bp.route(ADMIN + '/log_round', methods=['POST'])
@auth.login_required
def chainle_log_round():
    """Save a round. Logging the same date, site and round again replaces it."""
    keep = {'date': request.form.get('played_on'), 'round': request.form.get('round_no')}
    try:
        row = parse_round(request.form)
    except ValidationError as e:
        return _back_to_log(error=str(e), **keep)
    data = get_data()
    got = _assess(data, row['kind'], row['clues'], row['answer'], row['guess']) if data is not None else None
    row.update(solver_version=solver_version(), solver_top=got['top'] if got else None,
               answer_rank=got['ranks'].get(row['answer']) if got and row['answer'] else None)
    try:
        with db_cursor() as (conn, cursor):
            if _has_version_columns(cursor):
                cursor.execute(UPSERT_ROUND_VERSIONED, tuple(row[c] for c in ROUND_COLUMNS + VERSION_COLUMNS))
            else:
                cursor.execute(UPSERT_ROUND, tuple(row[c] for c in ROUND_COLUMNS))
            conn.commit()
    except Exception as e:
        print(f"Error logging chainle round: {e}")
        return _back_to_log(error='Database error', **keep)
    saved = f"Saved round {row['round_no']} ({row['mode']})"
    if row['round_no'] < max(ROUND_KIND):
        return _back_to_log(success=saved, date=row['played_on'], round=row['round_no'] + 1)
    return _back_to_log(success=f'{saved}: that was the last one')


@bp.route(ADMIN + '/delete_round', methods=['POST'])
@auth.login_required
def chainle_delete_round():
    try:
        rid = int(request.form.get('id', ''))
    except ValueError:
        return _back_to_log(error='No round chosen')
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute("DELETE FROM chainle_rounds WHERE id = %s", (rid,))
            conn.commit()
    except Exception as e:
        print(f"Error deleting chainle round: {e}")
        return _back_to_log(error='Database error')
    return _back_to_log(success='Round deleted')


NO_CHOICE = 'Choose Remove word or Allow word first'


def _check_word(word):
    """An error message for a word the lists can't take, or None."""
    if not word.isalpha():
        return 'Word must be letters only'
    data = get_data()
    if data is not None and data.id(word) < 0:
        return f"{word} isn't in the solver's word list, so it is never suggested anyway"
    return None


@bp.route(ADMIN + '/add_word', methods=['POST'])
@auth.login_required
def chainle_add_word():
    """Take a word off the list ('invalid') or keep one on it ('missing':
    the -s filter and player reports leave it alone)."""
    word, kind = crowd.admin_form()
    if kind is None:
        return _back(error=NO_CHOICE)
    error = _check_word(word)
    if error:
        return _back(error=error)
    try:
        CHAINLE_CROWD.admin_set(word, kind)
    except Exception as e:
        print(f"Error adding chainle word: {e}")
        return _back(error='Database error')
    done = 'allowed: it stays on the list' if kind == 'missing' else 'taken off the list'
    return _back(success=f'{word} {done}')


@bp.route(ADMIN + '/remove_word', methods=['POST'])
@auth.login_required
def chainle_remove_word():
    """Undo an entry in either list, and reset that word's votes."""
    word, kind = crowd.admin_form()
    if kind is None:
        return _back(error=NO_CHOICE)
    if not word.isalpha():
        return _back(error='Word must be letters only')
    try:
        CHAINLE_CROWD.admin_remove(word, kind)
    except Exception as e:
        print(f"Error removing chainle word: {e}")
        return _back(error='Database error')
    return _back(success=f'{word} undone')


##### admin: today's rounds #####

def _admin_rounds():
    """(days, error) for the Today's Rounds tab: each recent day, newest
    first, with the sets offered (or that would be, while dark), the sets
    players typed with all five rounds (typed_sets, each with 'set_no': the
    offered set holding all its rounds, or None; James doesn't want the
    rest, 2026-10-29) and every round players typed."""
    today = _today()
    first, last = today - timedelta(days=ADMIN_DAYS_BACK), today + timedelta(days=1)
    try:
        rows = rows_on(first, last)
    except Exception as e:
        print(f"Error loading chainle admin rounds: {e}")
        return [], f'{type(e).__name__}: {e}'
    days = []
    day = last
    while day >= first:
        sets, groups = todays_sets(rows, day)
        if sets or groups:
            where = set_numbers(sets)
            typed = [t for t in typed_sets(rows, day) if len(t['rounds']) == len(ROUND_SLOTS)]
            for t in typed:
                found = {where.get(key) for key in t['keys'].values()}
                t['set_no'] = found.pop() if len(found) == 1 else None
            days.append({'day': day, 'sets': sets, 'typed': typed, 'groups': groups})
        day -= timedelta(days=1)
    return days, None


def _form_set():
    """{round: clue text} from a set's form on the Today's Rounds tab (its
    hidden round<n>_<m> words, in order), or None if anything in it isn't a
    round."""
    rounds = {}
    for n in ROUND_SLOTS:
        words = [w for w in (request.form.get(f'round{n}_{m}', '').strip() for m in (1, 2, 3)) if w]
        if words:
            text = ' '.join(w.upper() for w in words)
            if not _CLUE_TEXT.fullmatch(text):
                return None
            rounds[n] = text
    return rounds or None


def _approved_round(n):
    """Round n from an approve form, as stored, or None if it was left
    blank. One clue word per box (round<n>_1 to _3), as many as the round
    takes (ROUND_CLUES: every site's day is three pairs, a triple, then one
    word), each one the solver knows; else raise ValidationError. A word it
    doesn't know is a typo here, and players couldn't use it anyway."""
    words = [w for w in (request.form.get(f'round{n}_{m}', '').strip() for m in (1, 2, 3)) if w]
    if not words:
        return None
    if len(words) != ROUND_CLUES[n]:
        raise ValidationError(f'Round {n} takes {ROUND_CLUES[n]} clue word{"" if ROUND_CLUES[n] == 1 else "s"}')
    try:
        clues = _any_clues(words)
    except ValidationError as e:
        raise ValidationError(f'Round {n}: {e}')
    if not _known(clues):
        raise ValidationError(f"Round {n}: a word isn't in the solver's list. Check the spelling")
    return clue_text(clues)


@bp.route(ADMIN + '/approve_rounds', methods=['POST'])
@auth.login_required
def chainle_approve_rounds():
    """Offer these rounds as one site's set to every player whose date is
    `played_on`, ahead of the players' sets: James's own game. The tab's form
    sends what he typed; a set's Approve (one the players settled, or one a
    player typed) sends its rounds. It replaces that site's whole set for
    the day; a round left blank is just not in it."""
    day = parse_iso_day(request.form.get('played_on', ''))
    site = request.form.get('site', '')
    if day is None or site not in BOARD_SITES:
        return _back(error='Choose a date and a site')
    try:
        rounds = {n: text for n in ROUND_SLOTS if (text := _approved_round(n))}
    except ValidationError as e:
        return _back(error=str(e))
    if not rounds:
        return _back(error="Type at least one round's clue words")
    try:
        store_approval(day, site, rounds)
    except Exception as e:
        print(f"Error approving chainle rounds: {e}")
        return _back(error='Database error')
    _sets_cache.clear()
    count = f"{len(rounds)} round{'' if len(rounds) == 1 else 's'}"
    return _back(success=f"Approved {count} of {site} for {day:%m-%d}")


@bp.route(ADMIN + '/unapprove_rounds', methods=['POST'])
@auth.login_required
def chainle_unapprove_rounds():
    """Undo one day's approval of a site: the players' sets decide again,
    and none of their rounds is touched."""
    day = parse_iso_day(request.form.get('played_on', ''))
    site = request.form.get('site', '')
    if day is None or site not in BOARD_SITES:
        return _back(error='Not an approval')
    try:
        removed = remove_approval(day, site)
    except Exception as e:
        print(f"Error undoing chainle approval: {e}")
        return _back(error='Database error')
    _sets_cache.clear()
    if not removed:
        return _back(error=f'Nothing of {site} was approved for {day:%m-%d}')
    return _back(success="Approval undone: the players' sets decide again")


def _removed(removed, day):
    return f"Removed {removed} entr{'y' if removed == 1 else 'ies'} on {day:%m-%d}"


@bp.route(ADMIN + '/remove_set', methods=['POST'])
@auth.login_required
def chainle_remove_set():
    """Reject a set the players settled: delete every player's entry of its
    rounds that day (junk, or staging tests: staging writes the same table).
    Approvals stay."""
    day = parse_iso_day(request.form.get('played_on', ''))
    rounds = _form_set()
    if day is None or rounds is None:
        return _back(error='Not a set')
    try:
        removed = remove_rounds(day, sorted(set(rounds.values())))
    except Exception as e:
        print(f"Error removing a chainle set: {e}")
        return _back(error='Database error')
    _sets_cache.clear()
    return _back(success=_removed(removed, day))


@bp.route(ADMIN + '/remove_typed_set', methods=['POST'])
@auth.login_required
def chainle_remove_typed_set():
    """Reject a set players typed: delete the entries of every player who
    typed exactly these rounds that day, as the tab showed them together.
    If one of them has changed a round since, nothing goes. Approvals stay."""
    day = parse_iso_day(request.form.get('played_on', ''))
    rounds = _form_set()
    if day is None or rounds is None:
        return _back(error='Not a set')
    try:
        keys = {n: clue_key(clues) for n, clues in rounds.items()}
        typed = next((t for t in typed_sets(rows_on(day, day), day) if t['keys'] == keys), None)
        if typed is None:
            return _back(error='Nobody has exactly that set any more. Reload the page and look again')
        removed = remove_players(day, typed['voters'])
    except Exception as e:
        print(f"Error removing a chainle typed set: {e}")
        return _back(error='Database error')
    _sets_cache.clear()
    return _back(success=_removed(removed, day))
