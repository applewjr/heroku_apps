"""
Firsts solver backtester.

Reads a dev file of boards with known answers (JSON, kept outside the repo:
the answers are the game's, so they never go in the repo or the tests) and
reports, per clue, where the right answer ranks in solve()'s list for the
starting board:

    top 1 / 3 / 5 / 10    share of clues whose answer ranks that high
    missing               the answer isn't in the list at all (the word list
                          lacks it, or it doesn't trace)
    list size             traceable words per clue (median)

Rows split out picture clues (the answer goes through a picture tile) and
cross-reference clues (the clue names another clue's number). Run with
--vectors-only for the baseline: Numberbatch cosine and popularity only, no
WordNet definitions.

Start here: how often the clue solve() marks to start with has the right
answer on top.

--replay also plays each board the way a player would, tiles going and
greens turning plain as the game does it (every answer's own path) and the
solved answers known, and reports how often each of the last three clues
ranks first. Two orders: clue order, and surest first (the clues whose
answer ranked highest on the starting board first, the way a player picks
off the easy ones before the hard ones).

Picture boards use the names a player would type for the pictures (the dev
file's "names"); --no-names leaves them out.

Results on the dev file of 42 boards (#-2 to #39, 420 clues, answers worked
out by hand and checked against the board rules), 2026-11-27, solver
version 1, about 0.16 s a board (slowest 0.54 s):

                     top1   top3   top5  top10  missing
    full            72.1%  87.1%  91.0%  95.5%   0.5%
    vectors only    74.5%  86.0%  90.0%  95.2%   0.5%
    pictures only (24 clues): 79.2% top1 with typed names, 66.7% without
    Start here right on top: 42 of 42 boards
    replay, last 3 / 2 / 1 clues first: clue order 89.7 / 96.4 / 100%,
        surest first 78.6 / 83.3 / 95.2%
WordNet definitions are about even with vectors alone (two points fewer
at the top, one more in the top 3); kept, since the WordNet data is also
what finds picture readings (EVE from "The Garden of Eden") and checks
built phrases. Before built phrases (composed) and picture reading options,
4.5% of answers were missing and picture clues were 50% top1. Several
answers in the dev file were found with the solver's help, so it flatters
the solver a little.

Dev file format: {"boards": [{"n", "title", "tiles" (19 characters in
functions/firsts.py hex_cells() order, pictures as digits), "green" (19 of
0/1), "clues": [[text, length], ...], "answers": ["WORD|OTHER", ...],
"readings": {"1": "SUN"}, "names": {"1": "sun"}, "grade"}]}.

    python scripts/backtest_firsts.py path/to/firsts_eval.json [--vectors-only] [--replay] [--no-names]
"""

import argparse
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, os.pardir)
sys.path.insert(0, ROOT)

from functions import chainle, firsts  # noqa: E402

NAMES = True


def load(args):
    data = firsts.Data.from_file(os.path.join(ROOT, 'datasets', 'firsts_data.npz'))
    vectors = firsts.Vectors(chainle.Data.from_file(os.path.join(ROOT, 'datasets', 'chainle_data.npz')))
    with open(args.dev, encoding='utf-8') as fh:
        boards = [b for b in json.load(fh)['boards'] if all(b['answers'])]
    return data, vectors, boards


def board_of(b, readings=None, gone=None, green=None, synced=False):
    return firsts.Board(list(b['tiles']), green or [c == '1' for c in b['green']], gone=gone,
                        readings=readings or {}, names=b.get('names', {}) if NAMES else {},
                        title=b.get('title', ''), synced=synced)


def rank_of(items, answer):
    """1-based rank of any of the answer's spellings, or None."""
    wanted = {firsts.letters_key(a) for a in answer.split('|')}
    for k, it in enumerate(items, 1):
        if firsts.letters_key(it['word']) in wanted:
            return k
    return None


def kind(b, i):
    if firsts.cross_refs(b['clues'][i][0]):
        return 'cross-ref'
    if any(t.isdigit() for t in b['tiles']):
        answer = b['answers'][i].split('|')[0]
        board = firsts.Board(list(b['tiles']), [c == '1' for c in b['green']], readings=b.get('readings'))
        for path, _r in firsts.trace(board, firsts.letters_key(answer)):
            if any(board.tiles[t].isdigit() for t in path):
                return 'picture'
    return 'plain'


def summarize(rows, label):
    n = len(rows)
    if not n:
        return
    ranks = [r for r, _size in rows]

    def share(k):
        return sum(1 for r in ranks if r is not None and r <= k) / n
    missing = sum(1 for r in ranks if r is None) / n
    size = statistics.median(s for _r, s in rows)
    print(f'{label:<12} {n:>4} clues  top1 {share(1):6.1%}  top3 {share(3):6.1%}  top5 {share(5):6.1%}  '
          f'top10 {share(10):6.1%}  missing {missing:6.1%}  list {size:.0f}')


def run(args):
    global NAMES
    NAMES = not args.no_names
    data, vectors, boards = load(args)
    if args.vectors_only:
        firsts.W_DEF = firsts.W_REV = firsts.W_CTX = 0.0
    rows = {'all': [], 'plain': [], 'picture': [], 'cross-ref': []}
    misses, starts, first_ranks = [], [], {}
    t0 = time.perf_counter()
    slowest = 0.0
    for b in boards:
        clues = [tuple(c) for c in b['clues']]
        t = time.perf_counter()
        out = firsts.solve(data, board_of(b), clues, vectors=vectors, show=1000)
        slowest = max(slowest, time.perf_counter() - t)
        if out['start']:
            s = out['start'] - 1
            starts.append(rank_of(out['clues'][s]['items'], b['answers'][s]) == 1)
        for i, c in enumerate(out['clues']):
            r = rank_of(c['items'], b['answers'][i])
            first_ranks[(b['n'], i)] = r if r is not None else 999
            rows['all'].append((r, c['count']))
            rows[kind(b, i)].append((r, c['count']))
            if r is None or r > 10:
                misses.append((b['n'], i + 1, b['answers'][i], r, [it['word'] for it in c['items'][:3]]))
    took = time.perf_counter() - t0
    print(f"{'vectors only' if args.vectors_only else 'full'}{'' if NAMES else ', no picture names'}: "
          f'{len(boards)} boards, {took / max(1, len(boards)):.2f} s a board, slowest {slowest:.2f} s')
    for label, r in rows.items():
        summarize(r, label)
    if starts:
        print(f'start here   {len(starts):>4} boards  right on top {sum(starts) / len(starts):6.1%}')
    if args.misses:
        print()
        print('ranked below 10 (board, clue, answer, rank, top 3):')
        for m in misses:
            print('  ', m)
    if args.replay:
        replay(data, vectors, boards, None)
        replay(data, vectors, boards, first_ranks)


def replay(data, vectors, boards, first_ranks):
    """Solve each board clue by clue, the board changing as the game's
    does: in clue order, or (first_ranks given) surest first."""
    last = {1: [], 2: [], 3: []}
    for b in boards:
        clues = [tuple(c) for c in b['clues']]
        full = firsts.Board(list(b['tiles']), [c == '1' for c in b['green']], readings=b.get('readings'))
        paths = []
        for a in b['answers']:
            found = firsts.trace(full, firsts.letters_key(a.split('|')[0]), max_paths=1)
            paths.append(found[0][0] if found else ())
        n = len(clues)
        order = list(range(n))
        if first_ranks:
            order.sort(key=lambda i: (first_ranks[(b['n'], i)], i))
        for solved_count in range(n - 3, n):
            solved = set(order[:solved_count])
            unsolved = [i for i in range(n) if i not in solved]
            live = {t for i in unsolved for t in paths[i]}
            gone = [t not in live for t in range(19)]
            green = [any(paths[i] and paths[i][0] == t for i in unsolved) for t in range(19)]
            board = board_of(b, gone=gone, green=green, synced=True)
            known = {i: (b['answers'][i].split('|')[0], 'yours') for i in solved}
            out = firsts.solve(data, board, clues, vectors=vectors, known=known, solved=solved, show=100)
            left = n - solved_count
            for i in unsolved:
                last[left].append(rank_of(out['clues'][i]['items'], b['answers'][i]) == 1)
    print()
    print(f"replay, {'surest first' if first_ranks else 'clue order'}, the board kept in step:")
    for left in (3, 2, 1):
        hits = last[left]
        print(f'  {left} clue{"s" if left > 1 else ""} left: first {sum(hits) / len(hits):6.1%} of {len(hits)}')


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().split('\n')[0])
    ap.add_argument('dev', help='the dev file (JSON, outside the repo)')
    ap.add_argument('--vectors-only', action='store_true', help='baseline: no WordNet definitions')
    ap.add_argument('--replay', action='store_true', help='also replay boards clue by clue')
    ap.add_argument('--misses', action='store_true', help='list the clues ranked below 10')
    ap.add_argument('--no-names', action='store_true', help="leave out the pictures' typed names")
    run(ap.parse_args())


if __name__ == '__main__':
    main()
