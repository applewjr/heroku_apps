"""Backtest the Typeshift solver on made-up boards built from our own words.

    python scripts/backtest_typeshift.py                # 30 boards per size
    python scripts/backtest_typeshift.py --boards 100 --seed 7 --check

Every board is invented here, never taken from a real puzzle: a few common
words of one length (Zipf CORE_ZIPF or more, from datasets/word_popularity.csv)
stacked into columns, as a Typeshift puzzle is built from its core words.
Each column keeps its letters once, in the order they first appear
(--repeats keeps every copy, to time boards with a letter twice in a column).
Each board is solved from scratch and again with a random share of its
letters already solid.

The report gives, per board width and core count: the words that fit, the
plan's length against the core count and the bound (the tallest column), how
often the plan is exactly the core words, solve times and time-outs. --check
also proves each plan on small boards by brute force: no fewer words cover
the letters, and no plan of that length is more familiar. The index's
memory is measured with tracemalloc.
"""

import argparse
import itertools
import math
import os
import random
import statistics
import sys
import time
import tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir))

import data  # noqa: E402
from functions import typeshift as ts  # noqa: E402

CORE_ZIPF = 3.3
WIDTHS = (3, 4, 5, 6, 7, 8, 10)
CORES = (3, 4, 5, 6, 7)
SOLID_SHARE = 0.4
# Brute force only boards this small: at most CHECK_WORDS words that fit and
# a plan of at most CHECK_PLAN words.
CHECK_WORDS = 45
CHECK_PLAN = 4


def make_board(rng, common, width, cores, repeats):
    core = rng.sample(common[width], cores)
    columns = []
    for c in range(width):
        letters = [w[c] for w in core]
        if not repeats:
            letters = list(dict.fromkeys(letters))
        columns.append(''.join(letters[:ts.MAX_LETTERS]))
    return ts.parse_board(columns), core


def covers(board, solid, plan):
    """Does `plan` turn every hollow letter some fitting word uses solid?
    A word uses one copy of each of its letters."""
    need = {}
    for c, col in enumerate(board):
        for i, letter in enumerate(col):
            if (c, i) not in solid:
                need[(c, letter)] = need.get((c, letter), 0) + 1
    for w in plan:
        for c in range(len(board)):
            key = (c, w[c])
            if need.get(key, 0) > 0:
                need[key] -= 1
    return need


def check(board, solid, result, words, zipf):
    """Brute force: the plan covers what can be covered, nothing shorter
    does, and nothing as short is more familiar. None if the board is too
    big to check, else a list of problems (empty: all good)."""
    plan = result['plan']
    if len(words) > CHECK_WORDS or len(plan) > CHECK_PLAN:
        return None
    problems = []
    # What any set of words can reach (every fitting word at once).
    reachable = covers(board, solid, words)
    left = covers(board, solid, plan)
    if any(left.get(k, 0) > reachable.get(k, 0) for k in left):
        problems.append('plan leaves a letter some word could use')
    goal = {k: v for k, v in reachable.items()}

    def done(combo):
        rest = covers(board, solid, combo)
        return all(rest.get(k, 0) <= goal.get(k, 0) for k in rest)

    if not plan:
        # Nothing left to plan for (every letter solid, or none coverable).
        return problems
    for size in range(len(plan)):
        if any(done(combo) for combo in itertools.combinations(words, size)):
            problems.append(f'a plan of {size} words exists, solver gave {len(plan)}')
            return problems
    best = max(((min(zipf[w] for w in combo), sum(zipf[w] for w in combo))
                for combo in itertools.combinations(words, len(plan)) if done(combo)),
               default=None)
    mine = (min(zipf[w] for w in plan), sum(zipf[w] for w in plan)) if plan else None
    if plan and best is not None and (round(best[0], 6), round(best[1], 6)) > (round(mine[0], 6), round(mine[1], 6)):
        problems.append(f'a more familiar plan exists: {best} vs {mine}')
    return problems


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(math.ceil(q * len(xs))) - 1)] if xs else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--boards', type=int, default=30, help='boards per width and core count')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--budget', type=float, default=ts.BUDGET_S, help='search budget, seconds')
    ap.add_argument('--repeats', action='store_true', help='keep every copy of a letter in a column')
    ap.add_argument('--check', action='store_true', help='brute-force small boards')
    args = ap.parse_args()

    tracemalloc.start()
    index = ts.Index.build(data.words)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f'index: {index.size:,} words, {current / 2**20:.2f} MB held, {peak / 2**20:.2f} MB peak while built')

    pop = data.word_pop
    common = {}
    for w in data.words:
        if isinstance(w, str) and w in index and ts._zipf(pop.get(w)) >= CORE_ZIPF:
            common.setdefault(len(w), []).append(w)
    for n in common:
        common[n].sort()

    rng = random.Random(args.seed)
    print(f'{args.boards} boards per row, seed {args.seed}, budget {args.budget}s'
          + (', repeats kept' if args.repeats else ''))
    print('width cores | fit (avg/max) | plan-core plan-bound | plan=core | p50 ms  p95 ms  max ms | '
          'not exact | solid: p95 ms max ms | checked bad')
    all_times, bad_total, checked_total = [], 0, 0
    for width in WIDTHS:
        for cores in CORES:
            if len(common.get(width, ())) < cores:
                continue
            fits, extra, over_bound, same, times, solid_times = [], [], [], 0, [], []
            inexact, checked, bad = 0, 0, 0
            for _ in range(args.boards):
                board, core = make_board(rng, common, width, cores, args.repeats)
                t0 = time.perf_counter()
                result = ts.solve(index, board, popularity=pop, budget_s=args.budget)
                times.append((time.perf_counter() - t0) * 1000)
                words = [w['word'] for w in result['words']]
                zipf = {w: ts._zipf(pop.get(w)) for w in words}
                fits.append(len(words))
                bound = max(len(col) for col in board)
                extra.append(len(result['plan']) - cores)
                over_bound.append(len(result['plan']) - bound)
                same += set(result['plan']) == set(core)
                inexact += not result['exact']
                if args.check:
                    problems = check(board, frozenset(), result, words, zipf)
                    if problems is not None:
                        checked += 1
                        if problems:
                            bad += 1
                            print('  BAD', board, problems)

                tiles = [(c, i) for c, col in enumerate(board) for i in range(len(col))]
                solid = frozenset(t for t in tiles if rng.random() < SOLID_SHARE)
                t0 = time.perf_counter()
                result = ts.solve(index, board, solid=solid, popularity=pop, budget_s=args.budget)
                solid_times.append((time.perf_counter() - t0) * 1000)
                inexact += not result['exact']
                if args.check:
                    problems = check(board, solid, result, words, zipf)
                    if problems is not None:
                        checked += 1
                        if problems:
                            bad += 1
                            print('  BAD (solid)', board, sorted(solid), problems)
            all_times += times + solid_times
            bad_total += bad
            checked_total += checked
            print(f'{width:5d} {cores:5d} | {statistics.mean(fits):5.1f} / {max(fits):4d} | '
                  f'{statistics.mean(extra):+9.2f} {statistics.mean(over_bound):+10.2f} | '
                  f'{100 * same / args.boards:8.0f}% | '
                  f'{pct(times, .5):6.1f} {pct(times, .95):7.1f} {max(times):7.1f} | '
                  f'{inexact:9d} | {pct(solid_times, .95):9.1f} {max(solid_times):6.1f} | '
                  f'{checked:7d} {bad:3d}')
    print(f'all solves: p50 {pct(all_times, .5):.1f} ms, p95 {pct(all_times, .95):.1f} ms, '
          f'max {max(all_times):.1f} ms' + (f'; brute-force checked {checked_total}, bad {bad_total}' if args.check else ''))


if __name__ == '__main__':
    main()
