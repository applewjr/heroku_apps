"""ICE COLD plans (functions/smush_ice_cold.py) for /smush's 🧊 Ice Cold tab
and for /smush_dev.

/smush plans today's boards only: the board decided for each date that is
today somewhere, which is two at most, since Smush rolls over at each
player's own midnight. Any other board gets 'not_today' and costs nothing
(James's call, 2026-10-31, to spare the dyno's memory and CPU). Each of
those boards' full table is built once, in the background, the moment the
board is decided (prewarm, called from routes/smush.py), and every player
shares it, so almost nobody waits for a build.

Corrections made after a build: a removed word is never offered, since a
plan's moves come from the player's own, current word list, and the card's
chance and points are played out on that list too (simulated). Only the
table's own steering keeps the old list, and since 2026-11-01 that matters:
a plan that plays for the clean finish steers toward the finishers it was
built with. The 2026-11-01 board, built before that night's removals, played
out on the new list cleaned 0.4% of games, against 17% from a fresh table.
So James rebuilds a board when his removals are done (Recalc on
/smush_admin, his call), and a board whose list changed with no Recalc for
ICE_REFRESH_BACKSTOP_SECONDS rebuilds by itself (player votes land after
his pass). The old table answers until the new one is in, when there's
memory for both.

What a plan knows about which words Smush takes: popularity priors and ✗
votes, never what a player says they played (James, 2026-10-31: "players
are chaos"). /smush_dev also counts James's own played words, for his own
requests only (routes/smush_dev.py).
"""

import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone

from crowd import CROWD_MAX_INVALID_PER_DAY
from data import word_pop
from extensions import db_cursor
from functions import all_words
from functions import smush_ice_cold as ice
from helpers import pacific_today, rss_mb
from monitoring import alerts
from routes import smush as smush_crowd

# A board's full word list and its ✗ counts are reused this long before
# they're read again, so crowd corrections reach the plans within minutes.
BOARD_CACHE_SECONDS = 300
VOTE_RETRY_SECONDS = 60

# Today's boards' tables (TODAY_TABLES). Two dates are today somewhere at
# any moment, so two boards; the next date's board drops the least recently
# used one before its build starts. A table holds ~22 MB (CompactTable), and
# a build adds ~110 MB for a minute or more (measured in WSL, 2026-10-10),
# so a build only starts while the process is at or under
# ICE_BUILD_MAX_RSS_MB: 360 + 110, plus ~25 MB for gunicorn's master, stays
# under the dyno's 512 MB. Heroku Metrics, 10-30/31: 296 MB average, 374 MB
# peak, before any of this.
ICE_MAX_TABLES = 2
ICE_BUILD_MAX_RSS_MB = 360
# Past this, the dyno is close to swapping (512 MB, less the master), so
# today's tables give way to the rest of the site: the least recently used
# one each sweep (a minute), the other only if the shortage lasts. A board
# whose table went waits to be built again until memory is back under
# ICE_BUILD_MAX_RSS_MB, so a table can't go and come straight back.
ICE_SHED_RSS_MB = 460
# A build held back for memory asks again this often.
ICE_BUILD_WAIT_SECONDS = 300
# A table goes this long after its last use: a plan from it, or any read
# of its board (prewarm; every /smush page view reads today's, at most once
# a minute). So a live board's table stays while the site has visitors, and
# yesterday's goes once nobody reads it.
TODAY_IDLE_SECONDS = 6 * 3600
# Builds a Pacific day: two boards, restarts and deploys, and Recalcs (an
# admin's, or the backstop's). Only a loop of failures or evictions could
# reach it.
TODAY_BUILDS_PER_DAY = 12
# A Recalc keeps the old table answering while the new one builds only at
# or under this: the build's ~110 MB plus the new table's ~22 MB on top
# stays under the dyno's 512 MB, with the master. Above it, the old table
# goes first and the board shows "building" for the minute or two.
ICE_REFRESH_MAX_RSS_MB = 330
# A live board whose word list changed after its table was built, with no
# Recalc this long after the change was first seen, rebuilds by itself:
# the player votes that land after James's pass.
ICE_REFRESH_BACKSTOP_SECONDS = 2 * 3600
# Plans worked out at once. One holds the GIL for up to about a second, so
# any more answer 'busy' and the page asks again BUSY_SECONDS later, which
# keeps gunicorn's 8 threads free for every other page.
PLANS_AT_ONCE = 2
BUSY_SECONDS = 2
# From UTC-10 (Hawaii) to UTC+14 (Kiribati), where everyone is, it's always
# one of two dates.
LIVE_UTC_OFFSETS = (-10, 14)
# The tab's NEW badge goes by itself after this Pacific day, with no deploy
# (James is slowing his updates, so nothing here needs a hand to take it off).
ICE_NEW_UNTIL = date(2026, 12, 15)


def is_new():
    """Whether the 🧊 Ice Cold tab still wears its NEW badge."""
    return pacific_today() <= ICE_NEW_UNTIL


##### a board's words and ✗ evidence #####

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
            print(f"smush_ice: no ✗ counts this time: {e}")
            ttl = VOTE_RETRY_SECONDS
    with _cache_lock:
        _drop_expired(_vote_counts, now)
        _vote_counts[tag] = (now + ttl, counts)
    return counts


def _drop_expired(cache, now):
    """Forget boards whose cached entry has run out, so boards played once
    don't pile up until the next restart."""
    for tag in [t for t, (expires, _) in cache.items() if expires <= now]:
        del cache[tag]


def forget_board_results():
    """Read every board's words and ✗ counts afresh next time: Smush's word
    list just changed (routes/smush.py), so a Recalc or the admin's panel
    must not see the list from up to BOARD_CACHE_SECONDS ago."""
    with _cache_lock:
        _board_results.clear()
        _vote_counts.clear()


def full_board_results(tag, center, letters, smush_words):
    """Every word on this board at full uses - a full table's pool."""
    now = time.monotonic()
    with _cache_lock:
        hit = _board_results.get(tag)
        if hit and hit[0] > now:
            return hit[1]
    results, _, _ = all_words.smush_solver(
        center, {l: 5 for l in letters}, '', False, smush_words,
        list_len=None, popularity=word_pop)
    with _cache_lock:
        _drop_expired(_board_results, now)
        _board_results[tag] = (now + BOARD_CACHE_SECONDS, results)
    return results


##### the simulated chance and points #####

# What simulate() found for the positions players asked about lately, so a
# re-poll or a tap on another spicy tile costs nothing.
SIMS = ice.ExactCache(size=256)
# Today's boards' openings, worked out in the build thread right after each
# table is built (OPENING_TILES runs of ice.SIM_OPENING_ROLLOUTS games), so
# the first card every player sees is the precise one:
# (tag, pool fingerprint) -> {spice: ice.Simulated}.
_openings = OrderedDict()
OPENINGS_KEPT = 4
OPENING_TILES = ['unknown'] + list(range(ice.N_LETTERS))


# Today's boards' builds, for Recalc and its backstop: tag -> the words its
# table was built from (pangrams aside, as the planner leaves them), when,
# and when a change to them was first seen (time.monotonic), if one has been.
_built = {}


def table_built(tag, table, pool):
    """TableCache after_built: note what the table was built from, then work
    out its opening."""
    with _cache_lock:
        _built[tag] = {'words': frozenset(w for g in pool.groups for w in g.words),
                       'at': datetime.now(timezone.utc), 'noticed': None}
    simulate_opening(tag, table, pool)


def simulate_opening(tag, table, pool):
    """A just-built table's opening, for every spicy tile and an untapped
    one (about 2 s each)."""
    started = time.monotonic()
    runs = {s: ice.simulate(table, pool, ice.FULL_ROOT, s, 0, ice.SIM_OPENING_ROLLOUTS,
                            ice._seed(ice.FULL_ROOT, s))
            for s in OPENING_TILES}
    print(f"smush_ice: simulated {tag}'s opening in {time.monotonic() - started:.0f} s, "
          f"{runs['unknown'].chance:.0%} clean")
    key = (tag, pool.fingerprint())
    with _cache_lock:
        _openings[key] = runs
        _openings.move_to_end(key)
        while len(_openings) > OPENINGS_KEPT:
            _openings.popitem(last=False)


def simulated(tag, table, pool, L, spice, words_played):
    """simulate()'s result for this position: the opening's, worked out
    ahead, when the player's word list is still the board's; else played
    out now and kept a while."""
    fingerprint = pool.fingerprint()
    if L == ice.FULL_ROOT and words_played == 0:
        with _cache_lock:
            hit = _openings.get((tag, fingerprint), {}).get(spice)
        if hit is not None:
            return hit
    return SIMS.get((tag, id(table), fingerprint, L, spice, words_played),
                    lambda: ice.simulate(table, pool, L, spice, words_played,
                                         ice.SIM_ROLLOUTS, ice._seed(L, spice)))


##### the tables #####

def alert_build_failed(exc):
    alerts.alert_throttled('smush_ice_build_failed', exc=type(exc).__name__, msg=exc)


def log_built(key, seconds):
    """One line a build, so the dyno's build time and memory show in the logs."""
    rss = rss_mb()
    print(f"smush_ice: built {key} in {seconds:.0f} s" + ('' if rss is None else f", rss {rss:.0f} MB"))


def _room_to_build():
    """The memory guard (TableCache may_start): True where RSS can't be read."""
    rss = rss_mb()
    if rss is None or rss <= ICE_BUILD_MAX_RSS_MB:
        return True
    print(f"smush_ice: a table build waits for memory (rss {rss:.0f} MB)")
    return False


def _room_to_refresh():
    """TableCache may_refresh: True where RSS can't be read."""
    rss = rss_mb()
    return rss is None or rss <= ICE_REFRESH_MAX_RSS_MB


def _short_of_memory():
    """TableCache must_shed: False where RSS can't be read."""
    rss = rss_mb()
    if rss is None or rss <= ICE_SHED_RSS_MB:
        return False
    print(f"smush_ice: dropping a table for memory (rss {rss:.0f} MB)")
    return True


def today_tables(**overrides):
    """Today's boards' cache, as TODAY_TABLES has it (tests swap in their own,
    with a spawn that never runs)."""
    settings = dict(on_error=alert_build_failed, on_built=log_built, after_built=table_built,
                    may_refresh=_room_to_refresh, max_tables=ICE_MAX_TABLES,
                    rebuild_gap=None, idle_seconds=TODAY_IDLE_SECONDS,
                    max_builds_per_day=TODAY_BUILDS_PER_DAY, may_start=_room_to_build,
                    must_shed=_short_of_memory, wait_retry=ICE_BUILD_WAIT_SECONDS,
                    today=pacific_today)
    settings.update(overrides)
    return ice.TableCache(**settings)


TODAY_TABLES = today_tables()
EXACT = ice.ExactCache()
# Drops yesterday's table and idle exact tables, starts a build that waited
# for memory, and runs the Recalc backstop; it stops once nothing is held.
SWEEPER = ice.IdleSweeper(lambda: (TODAY_TABLES, EXACT, SIMS), on_sweep=lambda: backstop())
_plan_slots = threading.BoundedSemaphore(PLANS_AT_ONCE)


##### today's boards #####

def live_days(now=None):
    """The two dates that are today somewhere right now (UTC `now`)."""
    now = now or datetime.now(timezone.utc)
    return {(now + timedelta(hours=h)).date() for h in LIVE_UTC_OFFSETS}


def board_tag(center, outer):
    return center + ':' + ''.join(sorted(outer))


def _live():
    return smush_crowd.SMUSH_BOARD_LIVE and smush_crowd.SMUSH_ICE_LIVE


def todays_tags():
    """The boards decided for the live days (routes/smush.py, memoized)."""
    if not _live():
        return set()
    tags = set()
    for day in live_days():
        board = smush_crowd._board_for(day)
        if board:
            tags.add(board_tag(board['center'], board['outer']))
    return tags


def is_today(tag):
    """Whether ICE COLD plans this board: one of today's, or one whose table
    is still held, so a game on yesterday's board keeps its plan past the
    player's midnight."""
    return bool(_live() and (TODAY_TABLES.has(tag) or tag in todays_tags()))


def _shared_pool(tag, center, letters):
    """Today's board's pool for its shared table, read in the build thread:
    every word on the full board, with ✗ evidence and no played words."""
    full = full_board_results(tag, center, letters, smush_crowd.get_smush_words())
    votes = invalid_vote_counts(tag, sorted({r['word'] for r in full}))
    return ice.Pool(ice.make_groups(full, letters, frozenset(), votes))


def today_table(tag, center, letters):
    """(table, status, eta) of today's board's shared table."""
    return TODAY_TABLES.get(tag, None, lambda: _shared_pool(tag, center, letters))


def prewarm(day, board):
    """Start building `board`'s table when `day` is today somewhere, so it's
    ready before anyone opens the tab. routes/smush.py calls this whenever it
    reads a day's board, and on an approval; a board already on its way
    costs nothing, and one already held counts as used, which keeps it from
    idling out while its board is read. Never raises."""
    if not _live() or day not in live_days():
        return
    try:
        center, letters = board['center'], tuple(sorted(board['outer']))
        tag = board_tag(center, letters)
        TODAY_TABLES.prewarm(tag, None, lambda: _shared_pool(tag, center, letters))
        SWEEPER.poke()
    except Exception as e:  # noqa: BLE001 - nothing here may take a page down
        alert_build_failed(e)


##### Recalc: a table built again from its board's list as it is now #####

def _current_words(tag):
    """The words a table built now would plan with (pangrams and
    center-only words aside, as make_groups leaves them)."""
    center, outer = tag.split(':')
    full = full_board_results(tag, center, tuple(outer), smush_crowd.get_smush_words())
    return frozenset(r['word'] for r in full if not r['pangram'] and r['cost'])


def changed_words(tag):
    """(removed, added) since this board's table was built, sorted; None if
    no build of it is on record."""
    with _cache_lock:
        built = _built.get(tag)
    if built is None:
        return None
    now = _current_words(tag)
    return sorted(built['words'] - now), sorted(now - built['words'])


def recalc(tag):
    """Build this board's table again from its word list as it is now: the
    admin's Recalc, or the backstop. -> TableCache.refresh's status."""
    center, outer = tag.split(':')
    letters = tuple(outer)
    forget_board_results()
    status = TODAY_TABLES.refresh(tag, None, lambda: _shared_pool(tag, center, letters))
    SWEEPER.poke()
    return status


def backstop(now=None):
    """The sweeper's minute: a held board whose list changed after its build
    is noted when the change is first seen, and recalculated once
    ICE_REFRESH_BACKSTOP_SECONDS pass with no Recalc - the player votes
    that land after James's pass. A Recalc's build starts the record over."""
    now = time.monotonic() if now is None else now
    with _cache_lock:
        tags = list(_built)
    for tag in tags:
        if not TODAY_TABLES.has(tag):
            with _cache_lock:
                _built.pop(tag, None)
            continue
        changed = changed_words(tag)
        with _cache_lock:
            built = _built.get(tag)
            if built is None:
                continue
            if not changed or not any(changed):
                built['noticed'] = None
                continue
            if built['noticed'] is None:
                built['noticed'] = now
            due = now - built['noticed'] >= ICE_REFRESH_BACKSTOP_SECONDS
        state = TODAY_TABLES.describe(tag)
        if due and not (state['building'] or state['queued']):
            print(f"smush_ice: {tag}'s word list changed ({len(changed[0])} out, "
                  f"{len(changed[1])} in) with no Recalc; rebuilding")
            recalc(tag)


def _ago(seconds):
    minutes = int(seconds // 60)
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"


def ice_plans():
    """Today's boards' tables, for /smush_admin: one row a board, with what
    its table is doing and which of its words changed since the build."""
    if not _live():
        return []
    rows = {}
    for day in sorted(live_days()):
        board = smush_crowd._board_for(day)
        if not board:
            continue
        tag = board_tag(board['center'], board['outer'])
        row = rows.setdefault(tag, {
            'tag': tag, 'days': [],
            'text': smush_crowd.board_text(board['center'] + ''.join(sorted(board['outer'])))})
        row['days'].append(day.strftime('%m-%d'))
    for tag, row in rows.items():
        state = TODAY_TABLES.describe(tag)
        if state['building']:
            row['table'] = 'building' + (f", about {_ago(state['eta'] + 59)} left" if state['eta'] else '')
        elif state['held']:
            row['table'] = f"built {_ago(state['age'])} ago" + (', rebuild waiting' if state['queued'] else '')
        else:
            row['table'] = 'waiting to build' if state['queued'] else 'not built'
        changed = changed_words(tag) if state['held'] else None
        row['removed'], row['added'] = changed or ([], [])
        row['known'] = changed is not None
        with _cache_lock:
            noticed = (_built.get(tag) or {}).get('noticed')
        row['backstop'] = None
        if noticed is not None and (row['removed'] or row['added']):
            left = ICE_REFRESH_BACKSTOP_SECONDS - (time.monotonic() - noticed)
            row['backstop'] = f"rebuilds by itself in {_ago(max(0, left))}"
    return list(rows.values())


##### the plan #####

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


WAITING_REASONS = {
    'failed': "Building this board's plan failed - it retries in a few minutes.",
    'capped': "Today's plan builds are used up - try again tomorrow.",
    'not_today': "ICE COLD plans are for today's board only.",
}


def waiting(status, eta, spice, letters):
    """A plan payload with no plan in it yet, or none to come."""
    return {'mode': 'ice_cold', 'status': status, 'eta': eta, 'p_success': None,
            'expected_points': None, 'next': [], 'by_spice': {}, 'finish_words': [],
            'sim_games': 0, 'reason': WAITING_REASONS.get(status),
            'spice': None if spice is None else ('unknown' if spice == 'unknown' else letters[spice])}


def exact_plan(cache, tag, letters, L, spice, results, accepted, votes, words_played=0):
    """The plan solved exactly from this player's own pool (their played and
    ✗'d words are already out of `results`), for a small box."""
    pool = ice.Pool(ice.make_groups(results, letters, accepted, votes))
    table = cache.get((tag, L, pool.fingerprint()), lambda: ice.solve_box(L, pool.groups))
    sim = simulated(tag, table, pool, L, spice, words_played)
    payload = ice.plan_payload(table, pool, L, spice, letters, words_played, sim)
    payload['source'] = 'exact'
    return payload


def table_plan(table, status, eta, tag, letters, L, spice, results, accepted, votes,
               words_played=0):
    """The plan from a board's full table, or why there isn't one yet."""
    if table is None:
        return waiting(status, eta, spice, letters)
    pool = ice.Pool(ice.make_groups(results, letters, accepted, votes))
    sim = simulated(tag, table, pool, L, spice, words_played)
    payload = ice.plan_payload(table, pool, L, spice, letters, words_played, sim)
    payload['source'] = 'table'
    return payload


def public_plan(center, outer_uses, spicy, results, smush_words, words_played=0):
    """/smush's 🧊 Ice Cold tab: the plan for this position, on today's board
    only, with no played-word evidence. `results` is the player's own solve
    (smush_solver, their played and ✗'d words out); `words_played` counts
    them, for Word Hoard."""
    letters = tuple(sorted(outer_uses))
    tag = board_tag(center, letters)
    L = tuple(outer_uses[l] for l in letters)
    spice = planning_spice(spicy, letters, L)
    if not is_today(tag):
        return waiting('not_today', None, spice, letters)
    if not _plan_slots.acquire(blocking=False):
        return waiting('busy', BUSY_SECONDS, spice, letters)
    try:
        full = full_board_results(tag, center, letters, smush_words)
        votes = invalid_vote_counts(tag, sorted({r['word'] for r in full}))
        if ice.states_below(L) <= ice.ICE_EXACT_STATES:
            payload = exact_plan(EXACT, tag, letters, L, spice, results, frozenset(), votes,
                                 words_played)
        else:
            table, status, eta = today_table(tag, center, letters)
            payload = table_plan(table, status, eta, tag, letters, L, spice, results,
                                 frozenset(), votes, words_played)
    finally:
        _plan_slots.release()
    SWEEPER.poke()
    return payload
