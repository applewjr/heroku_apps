"""ICE COLD plans (functions/smush_ice_cold.py) for /smush's 🧊 Ice Cold tab
and for /smush_dev.

/smush plans today's boards only: the board decided for each date that is
today somewhere, which is two at most, since Smush rolls over at each
player's own midnight. Any other board gets 'not_today' and costs nothing
(James's call, 2026-10-31, to spare the dyno's memory and CPU). Each of
those boards' full table is built once, in the background, the moment the
board is decided (prewarm, called from routes/smush.py), and every player
shares it, so almost nobody waits for a build.

A table is never rebuilt for a correction made later in the day. A word
removed mid-day is never offered, since a plan's moves come from the
player's own, current word list; the table only prices where each move
leads. Measured 2026-10-31 by building a day's table from its start-of-day
and its end-of-day word list, then playing 300 games from the live list:

    day      changes left out    first word differs    points lost a game
    10-23    32 out, 3 in        29% of decisions      ~2.2 of ~213 (1%)
    10-30    14 out, 2 in        13%                   ~0.6 (0.3%)

A rebuild would hold the old table, the new build and the other board's
table at once (about +180 MB) for that 0.3-1%. The next day's board, or a
restart, picks the corrections up.

What a plan knows about which words Smush takes: popularity priors and ✗
votes, never what a player says they played (James, 2026-10-31: "players
are chaos"). /smush_dev also counts James's own played words, for his own
requests only (routes/smush_dev.py).
"""

import threading
import time
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
# Yesterday's board goes this long after its last use, once everyone's
# midnight has passed.
TODAY_IDLE_SECONDS = 6 * 3600
# Builds a Pacific day: two boards, plus restarts and deploys. Only a loop
# of failures or evictions could reach it.
TODAY_BUILDS_PER_DAY = 8
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
    settings = dict(on_error=alert_build_failed, on_built=log_built, max_tables=ICE_MAX_TABLES,
                    rebuild_gap=None, idle_seconds=TODAY_IDLE_SECONDS,
                    max_builds_per_day=TODAY_BUILDS_PER_DAY, may_start=_room_to_build,
                    must_shed=_short_of_memory, wait_retry=ICE_BUILD_WAIT_SECONDS,
                    today=pacific_today)
    settings.update(overrides)
    return ice.TableCache(**settings)


TODAY_TABLES = today_tables()
EXACT = ice.ExactCache()
# Drops yesterday's table and idle exact tables, and starts a build that
# waited for memory; it stops once nothing is held.
SWEEPER = ice.IdleSweeper(lambda: (TODAY_TABLES, EXACT))
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
    reads a day's board, and on an approval; a board already held or on its
    way costs nothing. Never raises."""
    if not _live() or day not in live_days():
        return
    try:
        center, letters = board['center'], tuple(sorted(board['outer']))
        tag = board_tag(center, letters)
        TODAY_TABLES.prewarm(tag, None, lambda: _shared_pool(tag, center, letters))
        SWEEPER.poke()
    except Exception as e:  # noqa: BLE001 - nothing here may take a page down
        alert_build_failed(e)


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
            'sim_win_rate': None, 'reason': WAITING_REASONS.get(status),
            'spice': None if spice is None else ('unknown' if spice == 'unknown' else letters[spice])}


def exact_plan(cache, tag, letters, L, spice, results, accepted, votes):
    """The plan solved exactly from this player's own pool (their played and
    ✗'d words are already out of `results`), for a small box."""
    pool = ice.Pool(ice.make_groups(results, letters, accepted, votes))
    table = cache.get((tag, L, pool.fingerprint()), lambda: ice.solve_box(L, pool.groups))
    payload = ice.plan_payload(table, pool, L, spice, letters)
    payload['source'] = 'exact'
    return payload


def table_plan(table, status, eta, letters, L, spice, results, accepted, votes):
    """The plan from a board's full table, or why there isn't one yet."""
    if table is None:
        return waiting(status, eta, spice, letters)
    pool = ice.Pool(ice.make_groups(results, letters, accepted, votes))
    payload = ice.plan_payload(table, pool, L, spice, letters)
    payload['source'] = 'table'
    return payload


def public_plan(center, outer_uses, spicy, results, smush_words):
    """/smush's 🧊 Ice Cold tab: the plan for this position, on today's board
    only, with no played-word evidence. `results` is the player's own solve
    (smush_solver, their played and ✗'d words out)."""
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
            payload = exact_plan(EXACT, tag, letters, L, spice, results, frozenset(), votes)
        else:
            table, status, eta = today_table(tag, center, letters)
            payload = table_plan(table, status, eta, letters, L, spice, results, frozenset(), votes)
    finally:
        _plan_slots.release()
    SWEEPER.poke()
    return payload
