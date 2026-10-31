"""/smush_dev: James's private page for the ICE COLD planner
(functions/smush_ice_cold.py), behind the admin password.

A copy of /smush whose All 8 tab, with Ice Cold on, plays the game the
planner solves: the best word for wherever the 🌶 is, the chance of finishing
clean and ICE COLD, and the words worth saving for the finish. Unlike /smush's
🧊 Ice Cold tab, which plans today's boards only, it plans any board James
asks for, with its own one-board table; on today's boards it uses the table
/smush's players share (routes/smush_ice.py). It also counts the words James
plays here as accepted, for his own plans only.
"""

import threading

from flask import Blueprint, jsonify, render_template, request

from data import word_pop
from extensions import INTERACTIVE_LIMITS, auth, limiter
from functions import all_words
from functions import smush_ice_cold as ice
from helpers import ValidationError, json_object, pacific_today
from routes import smush as smush_crowd
from routes import smush_ice
from routes.smush import get_smush_words
from routes.wordgames import parse_solve

bp = Blueprint('smush_dev', __name__)

# A word James marked ✓ Played here counts as accepted in his plans for that
# board (James's call, 2026-10-09: Smush has one master word list). Never in
# /smush's: there, players are chaos (James, 2026-10-31). In memory only -
# reset each Pacific day and by a dyno restart.
ICE_PLAYED_MIN_PLAYERS = 1
# This page's own table is rebuilt for newer evidence at most this often.
# Every newly played word is newer evidence, and the per-request solve
# answers most of a game anyway, so a stale opening table costs little.
TABLE_REBUILD_SECONDS = 1800


##### acceptance evidence #####

_played_lock = threading.Lock()
_played = {'day': None, 'boards': {}}        # tag -> {word: {voter hashes}}


def record_played(tag, words, voter):
    """Remember that this player played `words` on this board today."""
    day = pacific_today().isoformat()
    with _played_lock:
        if _played['day'] != day:
            _played['day'], _played['boards'] = day, {}
        board = _played['boards'].setdefault(tag, {})
        for w in words:
            board.setdefault(str(w).lower(), set()).add(voter)


def played_words(tag):
    """Words enough players have played on this board today."""
    with _played_lock:
        if _played['day'] != pacific_today().isoformat():
            return frozenset()
        board = _played['boards'].get(tag, {})
        return frozenset(w for w, voters in board.items()
                         if len(voters) >= ICE_PLAYED_MIN_PLAYERS)


##### the plan #####

TABLES = ice.TableCache(on_error=smush_ice.alert_build_failed, on_built=smush_ice.log_built,
                        rebuild_gap=TABLE_REBUILD_SECONDS)
EXACT = ice.ExactCache()
# Drops both caches' tables once they've sat unused for
# ice.IDLE_RELEASE_SECONDS, then stops until the next request.
SWEEPER = ice.IdleSweeper(lambda: (TABLES, EXACT))


def ice_cold_plan(center, outer_uses, spicy, results, played, smush_words, want_plan):
    """The ICE COLD plan for this request, or None when the page didn't ask
    for one. The board's table still gets started either way, so it's warm by
    the time the All 8 tab opens."""
    letters = tuple(sorted(outer_uses))
    tag = smush_ice.board_tag(center, letters)
    L = tuple(outer_uses[l] for l in letters)
    if played:
        record_played(tag, played, smush_crowd.SMUSH_CROWD.voter_hash())
    accepted = played_words(tag)
    full = smush_ice.full_board_results(tag, center, letters, smush_words)
    votes = smush_ice.invalid_vote_counts(tag, sorted({r['word'] for r in full}))
    spice = smush_ice.planning_spice(spicy, letters, L)

    if ice.states_below(L) <= ice.ICE_EXACT_STATES:
        if not want_plan:
            return None
        # Small enough to solve from this player's exact pool: their played
        # and ✗'d words are already out of `results`.
        payload = smush_ice.exact_plan(EXACT, tag, letters, L, spice, results, accepted, votes)
        SWEEPER.poke()
        return payload

    if smush_ice.is_today(tag):
        # Today's board: the table /smush's players share, built without
        # anyone's played words.
        table, status, eta = smush_ice.today_table(tag, center, letters)
        smush_ice.SWEEPER.poke()
    else:
        shared = ice.Pool(ice.make_groups(full, letters, accepted, votes))
        table, status, eta = TABLES.get(tag, shared.fingerprint(), lambda: shared)
        SWEEPER.poke()
    if not want_plan:
        return None
    return smush_ice.table_plan(table, status, eta, letters, L, spice, results, accepted, votes)


##### the page #####

def _with_efficiency(rows):
    """Points per outer-letter use on each row, for this page's per-use figure
    and sort. /smush's solver rows don't carry one."""
    for r in rows:
        r.setdefault('efficiency', round(r['pts'] / max(sum(r['cost'].values()), 1), 1))
    return rows


@bp.route('/smush_dev', methods=['GET', 'POST'])
@limiter.limit(INTERACTIVE_LIMITS)
@auth.login_required
def smush_dev():
    if request.method == 'GET':
        return render_template('smush_dev.html')

    data = json_object()

    # ✗, restoring a hidden word and the "Smush accepted a word" box: the
    # same crowd votes /smush records - a refusal here is a real refusal.
    if 'action' in data:
        return smush_crowd.handle_vote(data, dev=True)

    # The same checks /smush makes, plus Ice Cold.
    center, outer_uses, spicy, first_word, rejected, played, want_plan = parse_solve(data)
    ice_cold = data.get('ice_cold', False)
    if not isinstance(ice_cold, bool):
        raise ValidationError('ice_cold must be a boolean')
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
