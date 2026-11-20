"""Backtest the 4x3 solver (functions/four_by_three.py) on made-up boards.

    python scripts/backtest_four_by_three.py
    python scripts/backtest_four_by_three.py --scale 0.5,1,2       # how sharp the labels are
    python scripts/backtest_four_by_three.py --split --strength 0.5,2,8

The boards come from datasets/four_by_three_eval.tsv, written by hand for
this repo: never a real 4x3 puzzle. --boards reads another file of the same
shape (a scratchpad one, for a check that must never be committed). Each
board's nine words are shuffled (--seed), then solved with no marks.

The report gives, for the default weights times each --scale factor: the hub
right, groups right, whole boards right, each label's record (are "sure"
groups right?), the mean log loss of the right answer, and solve times.
--split tunes the weights on half the boards as if they were James's
approvals (four_by_three.fit) and scores the other half, against the
defaults, for each --strength. The memory one solve takes is measured with
tracemalloc.
"""

import argparse
import math
import os
import random
import statistics
import sys
import time
import tracemalloc

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, os.pardir)
sys.path.insert(0, ROOT)

from functions import chainle as ch  # noqa: E402
from functions import circuits as cz  # noqa: E402
from functions import four_by_three as fbt  # noqa: E402

DEFAULT_BOARDS = os.path.join(ROOT, 'datasets', 'four_by_three_eval.tsv')


def read_boards(path):
    """[(hub, [(a, b) x 4])], upper case, from a tab-separated file."""
    boards = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.rstrip('\n')
            if not line.strip() or line.startswith('#'):
                continue
            hub, *pairs = line.split('\t')
            pairs = [tuple(fbt.clean_tile(w) for w in p.split(',')) for p in pairs]
            assert len(pairs) == 4 and all(len(p) == 2 for p in pairs), line
            boards.append((fbt.clean_tile(hub), pairs))
    return boards


def prepared(boards, words, pairs, seed):
    """Each board shuffled, with its Signals and the right answer's index."""
    rng = random.Random(seed)
    out = []
    for hub, groups in boards:
        tiles = [hub] + [w for p in groups for w in p]
        rng.shuffle(tiles)
        tiles = fbt.parse_board(tiles)
        at = {t: i for i, t in enumerate(tiles)}
        right = fbt.answer_index(at[hub], [(at[a], at[b]) for a, b in groups])
        start = time.perf_counter()
        signals = fbt.Signals.build(tiles, words, pairs)
        build_ms = 1000 * (time.perf_counter() - start)
        truth = {frozenset((hub, a, b)) for a, b in groups}
        out.append({'tiles': tiles, 'hub': hub, 'truth': truth, 'right': right,
                    'signals': signals, 'build_ms': build_ms})
    return out


def score(board_list, weights):
    """The record of one set of weights over prepared boards."""
    stats = {'hub': 0, 'groups': 0, 'full': 0, 'loss': [], 'ms': [],
             'labels': {}, 'hub_labels': {}}
    for b in board_list:
        start = time.perf_counter()
        result = fbt.solve(b['signals'], weights)
        stats['ms'].append(b['build_ms'] + 1000 * (time.perf_counter() - start))
        hub_right = result['hub']['tile'] == b['hub']
        stats['hub'] += hub_right
        rec = stats['hub_labels'].setdefault(result['hub']['label'], [0, 0])
        rec[0] += hub_right
        rec[1] += 1
        right = 0
        for g in result['groups']:
            ok = frozenset(g['tiles']) in b['truth']
            right += ok
            rec = stats['labels'].setdefault(g['label'], [0, 0])
            rec[0] += ok
            rec[1] += 1
        stats['groups'] += right
        stats['full'] += right == 4
        s = b['signals'].answer_features() @ np.asarray(weights, dtype=float)
        s -= s.max()
        stats['loss'].append(-(s[b['right']] - math.log(np.exp(s).sum())))
    return stats


def report(name, stats, n):
    labels = '  '.join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(stats['labels'].items(),
                                                             key=lambda kv: fbt.LABELS.index(kv[0])))
    hubs = '  '.join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(stats['hub_labels'].items(),
                                                           key=lambda kv: fbt.LABELS.index(kv[0])))
    ms = sorted(stats['ms'])
    print(f"{name}: hub {stats['hub']}/{n}  groups {stats['groups']}/{4 * n}  full {stats['full']}/{n}  "
          f"log loss {statistics.mean(stats['loss']):.2f}")
    print(f"    group labels: {labels}")
    print(f"    hub labels:   {hubs}")
    print(f"    ms per board: p50 {ms[len(ms) // 2]:.1f}  p95 {ms[int(0.95 * (len(ms) - 1))]:.1f}  max {ms[-1]:.1f}")


def numbers(text):
    """An argparse type: comma-separated numbers, each finite."""
    try:
        values = [float(x) for x in text.split(',')]
    except ValueError:
        raise argparse.ArgumentTypeError(f'{text!r} is not a list of numbers')
    if not all(math.isfinite(v) for v in values):
        raise argparse.ArgumentTypeError(f'{text!r}: each must be a finite number')
    return values


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--boards', default=DEFAULT_BOARDS)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--scale', type=numbers, default='1', help='factors for the default weights, comma-separated')
    ap.add_argument('--split', action='store_true', help='tune on half, score the other half')
    ap.add_argument('--strength', type=numbers, default=str(fbt.PRIOR_STRENGTH), help='prior strengths for --split')
    args = ap.parse_args()

    boards = read_boards(args.boards)
    need = 2 if args.split else 1
    if len(boards) < need:
        ap.error(f"{args.boards} has {len(boards)} board{'' if len(boards) == 1 else 's'}: "
                 f"{'--split needs' if args.split else 'a run needs'} at least {need} "
                 "(a line each: the hub, then four pairs as a,b, tab-separated)")

    start = time.perf_counter()
    words = ch.Data.from_file(os.path.join(ROOT, 'datasets', 'chainle_data.npz'))
    pairs = cz.Data.from_file(os.path.join(ROOT, 'datasets', 'circuits_data.npz'))
    print(f'data loaded in {time.perf_counter() - start:.1f}s')

    board_list = prepared(boards, words, pairs, args.seed)
    unknown = sorted({t for b in board_list for t, k in zip(b['tiles'], b['signals'].known) if not k})
    print(f"{len(board_list)} boards from {os.path.basename(args.boards)}; words the data lacks: "
          f"{', '.join(unknown) or 'none'}")

    for factor in args.scale:
        weights = np.asarray(fbt.DEFAULT_WEIGHTS) * factor
        report(f'defaults x {factor:g}', score(board_list, weights), len(board_list))

    if args.split:
        rng = random.Random(args.seed)
        order = board_list[:]
        rng.shuffle(order)
        half = len(order) // 2
        train, test = order[:half], order[half:]
        examples = [(b['signals'].answer_features(), b['right']) for b in train]
        report(f'held-out half ({len(test)}), defaults', score(test, fbt.DEFAULT_WEIGHTS), len(test))
        for strength in args.strength:
            t0 = time.perf_counter()
            tuned = fbt.fit(examples, strength=strength)
            fit_ms = 1000 * (time.perf_counter() - t0)
            print(f"  strength {strength:g}: weights {np.round(tuned, 2).tolist()} "
                  f"(fit on {len(train)} in {fit_ms:.0f} ms)")
            report(f'held-out half ({len(test)}), tuned at {strength:g}', score(test, tuned), len(test))

    tracemalloc.start()
    b = board_list[0]
    signals = fbt.Signals.build(b['tiles'], words, pairs)
    fbt.solve(signals, fbt.DEFAULT_WEIGHTS)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f'one build + solve: peak {peak / 1e6:.2f} MB (tracemalloc)')


if __name__ == '__main__':
    main()
