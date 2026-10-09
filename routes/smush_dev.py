"""/smush_dev: James's private page for the ICE COLD planner
(functions/smush_ice_cold.py), behind the admin password.

A copy of /smush whose All 8 tab, with Ice Cold on, plays the game the
planner solves: the best word for wherever the 🌶 is, the chance of finishing
clean and ICE COLD, and the words worth saving for the finish. It lives apart
so /smush stays exactly as it is while the planner is tried out (James's
call, 2026-10-09). /smush is untouched: this module only imports from
routes.wordgames, routes.smush, crowd and functions.all_words.
"""

import threading
import time
from datetime import datetime

import pytz
from flask import Blueprint, jsonify, render_template, request

from crowd import CROWD_MAX_INVALID_PER_DAY
from data import word_pop
from extensions import INTERACTIVE_LIMITS, auth, db_cursor, limiter
from functions import all_words
from functions import smush_ice_cold as ice
from helpers import ValidationError
from monitoring import alerts
from routes import smush as smush_crowd
from routes.smush import get_smush_words
from routes.wordgames import parse_smush_word_list

bp = Blueprint('smush_dev', __name__)

# A word one player marked ✓ Played here counts as accepted in everyone's plan
# for that board (James's call, 2026-10-09: Smush has one master word list).
# In memory only - reset each Pacific day and by a dyno restart.
ICE_PLAYED_MIN_PLAYERS = 1
# A board's full word list and its ✗ counts are reused this long before
# they're read again, so crowd corrections reach the plan within minutes.
BOARD_CACHE_SECONDS = 300
VOTE_RETRY_SECONDS = 60
# The shared full-board table is rebuilt for newer evidence at most this
# often. Every newly played word is newer evidence, and the per-request solve
# answers most of a game anyway, so a stale opening table costs little.
TABLE_REBUILD_SECONDS = 1800


##### acceptance evidence #####

_played_lock = threading.Lock()
_played = {'day': None, 'boards': {}}        # tag -> {word: {voter hashes}}


def _pacific_day():
    return datetime.now(pytz.timezone('America/Los_Angeles')).strftime('%Y-%m-%d')


def record_played(tag, words, voter):
    """Remember that this player played `words` on this board today."""
    day = _pacific_day()
    with _played_lock:
        if _played['day'] != day:
            _played['day'], _played['boards'] = day, {}
        board = _played['boards'].setdefault(tag, {})
        for w in words:
            board.setdefault(str(w).lower(), set()).add(voter)


def played_words(tag):
    """Words enough players have played on this board today."""
    with _played_lock:
        if _played['day'] != _pacific_day():
            return frozenset()
        board = _played['boards'].get(tag, {})
        return frozenset(w for w, voters in board.items()
                         if len(voters) >= ICE_PLAYED_MIN_PLAYERS)


# Distinct players who flagged each word Invalid - not just this board's
# votes, since Smush's word list is the same on every board. A player's flags
# don't count on a day they crossed off more than CROWD_MAX_INVALID_PER_DAY,
# the same rule crowd.CrowdList._count_players applies. No 7-day window: the
# word list doesn't change, so an old refusal is still a refusal. Read-only.
INVALID_COUNTS_SQL = """
    SELECT v.word, COUNT(*) FROM smush_word_votes v
    WHERE v.vote = 'invalid' AND v.word IN ({marks})
      AND (SELECT COUNT(*) FROM smush_word_votes d
           WHERE d.voter_hash = v.voter_hash AND d.vote = 'invalid'
             AND d.created_at >= DATE(v.created_at)
             AND d.created_at < DATE(v.created_at) + INTERVAL 1 DAY) <= %s
    GROUP BY v.word"""

_cache_lock = threading.Lock()
_vote_counts = {}                            # tag -> (expires, {word: players})
_board_results = {}                          # tag -> (expires, smush_solver rows)


def invalid_vote_counts(tag, words):
    """{word: players who flagged it ✗} for this board's words, cached.
    A database problem just means no ✗ evidence until the retry."""
    now = time.monotonic()
    with _cache_lock:
        hit = _vote_counts.get(tag)
        if hit and hit[0] > now:
            return hit[1]
    counts, ttl = {}, BOARD_CACHE_SECONDS
    if words:
        try:
            with db_cursor() as (conn, cursor):
                cursor.execute(INVALID_COUNTS_SQL.format(marks=', '.join(['%s'] * len(words))),
                               (*words, CROWD_MAX_INVALID_PER_DAY))
                counts = {str(w): int(n) for w, n in cursor.fetchall()}
        except Exception as e:
            print(f"smush_dev: no ✗ counts this time: {e}")
            ttl = VOTE_RETRY_SECONDS
    with _cache_lock:
        _vote_counts[tag] = (now + ttl, counts)
    return counts


def full_board_results(tag, center, letters, smush_words):
    """Every word on this board at full uses - the shared table's pool."""
    now = time.monotonic()
    with _cache_lock:
        hit = _board_results.get(tag)
        if hit and hit[0] > now:
            return hit[1]
    results, _, _ = all_words.smush_solver(
        center, {l: 5 for l in letters}, '', False, smush_words,
        list_len=None, popularity=word_pop)
    with _cache_lock:
        _board_results[tag] = (now + BOARD_CACHE_SECONDS, results)
    return results


##### the plan #####

def _build_failed(exc):
    alerts.alert_throttled('smush_ice_build_failed', exc=type(exc).__name__, msg=exc)


TABLES = ice.TableCache(on_error=_build_failed, rebuild_gap=TABLE_REBUILD_SECONDS)
EXACT = ice.ExactCache()


def planning_spice(spicy, letters, L):
    """The spicy tile as the planner sees it: its index; None for the
    spice-free turn (one tile left and none tapped - under ICE COLD that last
    tile was the spicy one, so the game's spice went null); or 'unknown'
    when it hasn't been tapped since the last play."""
    if spicy and spicy in letters and L[letters.index(spicy)] > 0:
        return letters.index(spicy)
    if sum(1 for l in L if l > 0) == 1:
        return None
    return 'unknown'


def _waiting(status, eta, spice, letters):
    reasons = {
        'failed': "Building this board's plan failed - it retries in a few minutes.",
        'capped': "Today's plan builds are used up - try again tomorrow.",
    }
    return {'mode': 'ice_cold', 'status': status, 'eta': eta, 'p_success': None,
            'next': [], 'by_spice': {}, 'finish_words': [], 'sim_win_rate': None,
            'reason': reasons.get(status),
            'spice': None if spice is None else ('unknown' if spice == 'unknown' else letters[spice])}


def ice_cold_plan(center, outer_uses, spicy, results, played, smush_words, want_plan):
    """The ICE COLD plan for this request, or None when the page didn't ask
    for one. The shared table still gets started either way, so it's warm by
    the time the All 8 tab opens."""
    letters = tuple(sorted(outer_uses))
    tag = f"{center}:{''.join(letters)}"
    L = tuple(outer_uses[l] for l in letters)
    if played:
        record_played(tag, played, smush_crowd.SMUSH_CROWD.voter_hash())
    accepted = played_words(tag)
    full = full_board_results(tag, center, letters, smush_words)
    votes = invalid_vote_counts(tag, sorted({r['word'] for r in full}))
    spice = planning_spice(spicy, letters, L)

    if ice.states_below(L) <= ice.ICE_EXACT_STATES:
        if not want_plan:
            return None
        # Small enough to solve from this player's exact pool: their played
        # and ✗'d words are already out of `results`.
        pool = ice.Pool(ice.make_groups(results, letters, accepted, votes))
        table = EXACT.get((tag, L, pool.fingerprint()), lambda: ice.solve_box(L, pool.groups))
        payload = ice.plan_payload(table, pool, L, spice, letters)
        payload['source'] = 'exact'
        return payload

    shared = ice.Pool(ice.make_groups(full, letters, accepted, votes))
    table, status, eta = TABLES.get(tag, shared.fingerprint(), lambda: shared)
    if not want_plan:
        return None
    if table is None:
        return _waiting(status, eta, spice, letters)
    pool = ice.Pool(ice.make_groups(results, letters, accepted, votes))
    payload = ice.plan_payload(table, pool, L, spice, letters)
    payload['source'] = 'table'
    return payload


##### the page #####

def _with_efficiency(rows):
    """Points per outer-letter use on each row, for this page's per-use figure
    and sort. /smush's solver rows don't carry one."""
    for r in rows:
        r.setdefault('efficiency', round(r['pts'] / max(sum(r['cost'].values()), 1), 1))
    return rows


def _parse_solve(data):
    """The same checks /smush makes (routes/wordgames.py run_smush), copied
    so that route stays untouched."""
    center = data.get('center', '')
    if not (isinstance(center, str) and len(center) == 1 and 'a' <= center.lower() <= 'z'):
        raise ValidationError('center must be a single letter a-z')

    outer_uses = data.get('outer_uses')
    if not (isinstance(outer_uses, dict) and len(outer_uses) == 8):
        raise ValidationError('outer_uses must contain exactly 8 letters')
    seen_letters = set()
    for letter, uses in outer_uses.items():
        if not (isinstance(letter, str) and len(letter) == 1 and 'a' <= letter.lower() <= 'z'):
            raise ValidationError('outer_uses keys must be single letters a-z')
        if not (isinstance(uses, int) and not isinstance(uses, bool) and 0 <= uses <= 5):
            raise ValidationError('outer_uses values must be whole numbers 0-5')
        seen_letters.add(letter.lower())
    if len(seen_letters) != 8 or center.lower() in seen_letters:
        raise ValidationError('outer_uses must be 8 distinct letters, none matching the center')

    spicy = data.get('spicy', '')
    if not (isinstance(spicy, str) and (spicy == '' or (len(spicy) == 1 and spicy.isalpha()))):
        raise ValidationError('spicy must be a single letter or empty')

    first_word = data.get('first_word', False)
    if not isinstance(first_word, bool):
        raise ValidationError('first_word must be a boolean')

    rejected = parse_smush_word_list(data, 'rejected')
    played = parse_smush_word_list(data, 'played')

    want_plan = data.get('plan', False)
    if not isinstance(want_plan, bool):
        raise ValidationError('plan must be a boolean')

    ice_cold = data.get('ice_cold', False)
    if not isinstance(ice_cold, bool):
        raise ValidationError('ice_cold must be a boolean')

    outer_uses = {letter.lower(): uses for letter, uses in outer_uses.items()}
    # A non-empty pile contradicts first_word; trust the pile.
    first_word = first_word and not played
    return (center.lower(), outer_uses, spicy.lower(), first_word,
            [w.lower() for w in rejected], [w.lower() for w in played], want_plan, ice_cold)


@bp.route('/smush_dev', methods=['GET', 'POST'])
@limiter.limit(INTERACTIVE_LIMITS)
@auth.login_required
def smush_dev():
    if request.method == 'GET':
        return render_template('smush_dev.html')

    data = request.get_json()
    if not isinstance(data, dict):
        raise ValidationError('expected a JSON object')

    # ✗, restoring a hidden word and the "Smush accepted a word" box: the
    # same crowd votes /smush records - a refusal here is a real refusal.
    if 'action' in data:
        return smush_crowd.handle_vote(data)

    center, outer_uses, spicy, first_word, rejected, played, want_plan, ice_cold = _parse_solve(data)
    smush_words = get_smush_words()
    results, total_playable, pangram_status = all_words.smush_solver(
        center, outer_uses, spicy, first_word, smush_words,
        list_len=None, exclude=rejected, played=played, popularity=word_pop)

    if ice_cold:
        # Every word, spicy or not, feeds the planner (later turns need the
        # ones that touch today's spice); the list shows only the safe ones.
        plan = ice_cold_plan(center, outer_uses, spicy, results, played, smush_words, want_plan)
        safe = [r for r in results
                if not r['pangram'] and not (spicy and r['cost'].get(spicy, 0))]
        return jsonify(results=_with_efficiency(safe[:400]), total_playable=len(safe),
                       pangram_status=pangram_status, plan=plan)

    # Without Ice Cold, the same All 8 plan /smush gives.
    plan = None
    if want_plan:
        plan_words, leftover = all_words.smush_all_plan(results, outer_uses)
        plan = {'complete': not leftover, 'words': _with_efficiency(plan_words),
                'leftover': leftover}
    return jsonify(results=_with_efficiency(results[:400]), total_playable=total_playable,
                   pangram_status=pangram_status, plan=plan)
