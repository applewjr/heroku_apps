"""
Smush ICE COLD backtester.

Plays /smush's 🧊 Ice Cold plan (functions/smush_ice_cold.py) the way a player
following the page would, on recent boards' live word lists, once for each
planning value of a clean finish (CLEAN_PLAN_BONUS), so they meet the same
boards and the same luck.

A game: the first spicy tile at random (each board's own is fixed, but not
stored). Every turn the plan's first word is tried; Smush takes it at the
planner's own odds (popularity priors and ✗ votes). A refused word is spent and
the player re-plans from the same position, from their own word list. A taken
word scores base x (1 + letters smushed flat) and moves the spice to a random
other living tile, as the game does. The game ends clean (+75: Clean Plate and
Unassisted) or when no safe word is left. Word Hoard's +25 counts at 13+ words.
The final score is all of that x5.

Data, read-only (SELECTs only, nothing written anywhere):
  - each day's board: its most reported letters in smush_boards;
  - the word list: datasets/all_words_blossom.csv with Smush's crowd
    corrections as they stand now (smush_invalid_words, smush_added_words);
  - ✗ votes: smush_word_votes, counted as routes/smush_ice.py counts them.
Nothing here uses the game's own word lists or authored data, and no board or
word list goes into the repo.

Limits: the plan plays from the full table all game, while the site switches
to an exact solve of the player's list once 25k states or fewer remain. Words
are taken at our odds, not by the real game. So it compares plans; it doesn't
predict what players will score.

Results, the ten boards 2026-10-23..11-01, 1,000 games each, random first spice:
  planning value    clean   final
  75 (to 10-31)     48.4%   1,105
  1,000 (live)      63.3%   1,079    since 2026-11-01, James's call
  per day, 75 -> 1,000:
    10-23  49.3%   937 -> 71.0%   911     10-28  56.5%   984 -> 73.2% 1,011
    10-24  67.4% 1,222 -> 73.4% 1,220     10-29  79.7% 1,234 -> 88.7% 1,190
    10-25  38.8% 1,264 -> 66.0% 1,252     10-30  30.6%   824 -> 34.3%   825
    10-26  73.9% 1,165 -> 85.0% 1,138     10-31  55.0% 1,411 -> 80.8% 1,285
    10-27  33.1% 1,065 -> 47.3%   963     11-01   0.0%   940 -> 13.6%   998
Ranking moves purely by clean chance (points only breaking ties) got 64% clean
for 1,043 on the same boards (500 games each): more points lost for the same
clean finishes.

Each board's table takes 40-90 s and ~110 MB to build, so the work runs one
(board, planning value) per process.
"""

import multiprocessing as mp
import random
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, 'c:/Users/james/projects/heroku_apps')
from functions import all_words  # noqa: E402
from functions import smush_ice_cold as ice  # noqa: E402

LIVE_BONUS = ice.CLEAN_PLAN_BONUS


def read_boards(days, until, picked):
    """[{day, center, outer, removed, added, votes}] from the database, with
    SELECTs only: the boards (each day's most reported letters, or `picked`
    as center:outer), the crowd corrections that touch them, and their ✗
    vote counts."""
    import mysql.connector

    import config
    from crowd import CROWD_MAX_INVALID_PER_DAY
    from routes.smush_ice import INVALID_COUNTS_SQL

    conn = mysql.connector.connect(**config.MYSQL_CONFIG)
    try:
        cur = conn.cursor()
        if picked:
            boards = [('-', b.split(':')[0], ''.join(sorted(b.split(':')[1]))) for b in picked]
        else:
            cur.execute("SELECT played_on, letters, COUNT(*) FROM smush_boards "
                        "WHERE played_on BETWEEN %s AND %s GROUP BY played_on, letters",
                        (until - timedelta(days=days - 1), until))
            best = {}
            for day, letters, n in cur.fetchall():
                if n > best.get(day, ('', 0))[1]:
                    best[day] = (letters, n)
            boards = [(str(d), l[0], ''.join(sorted(l[1:]))) for d, (l, _) in sorted(best.items())]
        cur.execute("SELECT word FROM smush_invalid_words")
        removed = {str(w).lower() for (w,) in cur.fetchall()}
        cur.execute("SELECT word FROM smush_added_words")
        added = {str(w).lower() for (w,) in cur.fetchall()}
        out = []
        for day, center, outer in boards:
            fits = set(outer) | {center}
            on_board = lambda w: 3 <= len(w) <= 15 and center in w and set(w) <= fits
            board_words = sorted(w for w in (_words() - removed) | added
                                 if isinstance(w, str) and on_board(w))
            cur.execute(INVALID_COUNTS_SQL.format(marks=', '.join(['%s'] * len(board_words))),
                        (*board_words, CROWD_MAX_INVALID_PER_DAY))
            out.append({'day': day, 'center': center, 'outer': outer,
                        'removed': sorted(w for w in removed if on_board(w)),
                        'added': sorted(w for w in added if on_board(w)),
                        'votes': {str(w): int(c) for w, c in cur.fetchall()}})
        return out
    finally:
        conn.close()


def _words():
    from data import words
    return words


class Board:
    """A board's live word list, ready to play: every word's cost, base and
    odds of being taken."""

    def __init__(self, full, letters, votes):
        self.full, self.letters, self.votes = full, letters, votes
        self.cost = {r['word']: tuple(r['cost'].get(l, 0) for l in letters) for r in full}
        self.base = {r['word']: r['base'] for r in full}
        self.odds = {w: a for g in ice.make_groups(full, letters, frozenset(), votes)
                     for w, a in zip(g.words, g.accepts)}
        self.pangrams = {r['word'] for r in full if r['pangram']}


def play(table, board, rng, spice=None):
    """One game, re-planning from the player's own list after every word or
    refusal, as the page does; -> (score before the x5, clean)."""
    taken = {}
    L = list(ice.FULL_ROOT)
    s = rng.randrange(len(board.letters)) if spice is None else spice
    gone = set(board.pangrams)
    score, played = 0, 0
    while any(L):
        res = [r for r in board.full if r['word'] not in gone
               and all(c <= n for c, n in zip(board.cost[r['word']], L))]
        pool = ice.Pool(ice.make_groups(res, board.letters, frozenset(), board.votes))
        moves = ice.moves_from(table, pool, L, s)
        if not moves:
            break
        w = moves[0].group.words[0]
        gone.add(w)
        if w not in taken:
            taken[w] = rng.random() < board.odds.get(w, 0.0)
        if not taken[w]:
            continue
        cost = board.cost[w]
        score += board.base[w] * (1 + sum(1 for c, n in zip(cost, L) if c and c == n))
        played += 1
        L = [n - c for n, c in zip(L, cost)]
        landing = [i for i, n in enumerate(L) if n > 0 and i != s]
        s = rng.choice(landing) if landing else None
    clean = not any(L)
    return (score + ice.CLEAN_BONUS * clean
            + (ice.HOARD_BONUS if played >= ice.HOARD_WORDS else 0)), clean


def run_one(task):
    """One board at one planning value: build its table, play the games."""
    board, bonus, games, seed, spice = task
    from data import words, word_pop
    letters = tuple(board['outer'])
    live = (words - set(board['removed'])) | set(board['added'])
    full, n_words, _ = all_words.smush_solver(board['center'], dict.fromkeys(letters, 5), '', False,
                                              live, list_len=None, popularity=word_pop)
    ice.CLEAN_PLAN_BONUS = bonus
    started = time.time()
    table = ice.solve_box(ice.FULL_ROOT, ice.make_groups(full, letters, frozenset(), board['votes']))
    playing = Board(full, letters, board['votes'])
    rng = random.Random(seed)
    first = letters.index(spice) if spice in letters else None
    results = [play(table, playing, rng, first) for _ in range(games)]
    return {'day': board['day'], 'board': f"{board['center']}:{board['outer']}", 'words': n_words,
            'bonus': bonus, 'clean': sum(c for _, c in results) / games,
            'final': 5 * sum(p for p, _ in results) / games, 'seconds': time.time() - started}


def run_backtest(days, until, picked, bonuses, games, seed, spice, workers):
    boards = read_boards(days, until, picked)
    if not boards:
        print('No boards found.')
        return
    tasks = [(b, bonus, games, seed, spice) for b in boards for bonus in bonuses]
    workers = max(1, min(workers, len(tasks)))
    print(f'  {len(boards)} boards x {len(bonuses)} planning values, {games} games each, '
          f'{workers} workers...')
    started = time.time()
    with mp.Pool(workers) as pool:
        done = pool.map(run_one, tasks, chunksize=1)
    by_board = {}
    for r in done:
        by_board.setdefault((r['day'], r['board'], r['words']), {})[r['bonus']] = r
    head = ''.join(f'   value {b:>5g}      ' for b in bonuses)
    print(f'\n=== Smush ICE COLD backtest ({games} games a board, '
          f'{"first spice " + spice if spice else "random first spice"}, {time.time() - started:.0f} s) ===')
    print(f'{"day":<11}{"board":<12}{"words":>6}{head}')
    for (day, board, n), runs in sorted(by_board.items()):
        cells = ''.join(f'   {100 * runs[b]["clean"]:5.1f}% {runs[b]["final"]:7,.0f}  ' for b in bonuses)
        print(f'{day:<11}{board:<12}{n:>6}{cells}')
    avg = ''.join('   {:5.1f}% {:7,.0f}  '.format(
        100 * sum(r[b]['clean'] for r in by_board.values()) / len(by_board),
        sum(r[b]['final'] for r in by_board.values()) / len(by_board)) for b in bonuses)
    print(f'{"average":<29}{avg}')
    print('(clean finishes, and the average final score x5)')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Backtest the Smush ICE COLD plan')
    parser.add_argument('--days', type=int, default=10,
                        help='How many days of boards, ending --until (default: 10)')
    parser.add_argument('--until', type=date.fromisoformat, default=date.today(),
                        help='Last board date, YYYY-MM-DD (default: today)')
    parser.add_argument('--boards', nargs='*', default=None,
                        help='Specific boards as center:outer, e.g. s:aegiknqu (instead of --days)')
    parser.add_argument('--values', nargs='+', type=float, default=[75.0, LIVE_BONUS],
                        help=f'Planning values of a clean finish to compare (default: 75 {LIVE_BONUS:g})')
    parser.add_argument('--games', type=int, default=1000, help='Games per board and value (default: 1000)')
    parser.add_argument('--seed', type=int, default=20261101,
                        help='Luck seed, shared by every value so they meet the same luck (default: 20261101)')
    parser.add_argument('--spice', default=None,
                        help="First spicy letter for every game, e.g. g (default: random each game)")
    _default_workers = max(1, mp.cpu_count() // 2)  # physical cores, not hyperthreads
    parser.add_argument('--workers', type=int, default=_default_workers,
                        help=f'Parallel worker processes (default: {_default_workers} = physical core count)')
    args = parser.parse_args()

    run_backtest(days=args.days, until=args.until, picked=args.boards, bonuses=args.values,
                 games=args.games, seed=args.seed, spice=args.spice, workers=args.workers)

# -- Quick reference (run from heroku_apps/) ---------------------------------
# python scripts/backtest_smush_ice.py                              # last 10 days, 75 vs the live value
# python scripts/backtest_smush_ice.py --days 3 --games 200         # a quick look
# python scripts/backtest_smush_ice.py --until 2026-11-01           # the ten boards in the docstring
# python scripts/backtest_smush_ice.py --values 1000 300            # compare other planning values
# python scripts/backtest_smush_ice.py --boards s:aegiknqu --spice g  # one board, its real first spice
# python scripts/backtest_smush_ice.py --workers 4                  # override parallel workers
# ----------------------------------------------------------------------------
