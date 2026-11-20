"""Backtest the Circuits solver on made-up boards built from its own data.

    python scripts/backtest_circuits.py                  # 300 boards
    python scripts/backtest_circuits.py --boards 1000 --workers 12 --seed 7

Every board is invented here, never taken from a real puzzle:
- a random layout on the grid (4-6 connected boxes, given tiles around them),
  or the ladder layout (a column of boxes between two columns of tiles);
- filled with strong pairs from datasets/circuits_data.npz (weight STRONG_W
  or more: dictionary nouns, or a phrase several sources list);
- kept only if exactly one filling makes every arrow a strong pair, checked
  with a plain constraint search that shares nothing with the solver's
  scoring (the game promises one answer per box).

No word list knows every compound the game uses, so each board is solved
again with a share of its own pairs hidden from the solver. Each is also
solved with the arrows the page draws by itself (every box to each filled
square next to it), as a player who never taps an arrow sends them. The
report gives, for both, box and whole-board accuracy at each share, accuracy
per confidence label, accuracy by score gap (to set SURE_GAP and LIKELY_GAP
in functions/circuits.py), and solve times.
"""

import argparse
import multiprocessing
import os
import sys
import time
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir))

from functions import circuits as cz  # noqa: E402

DATA_FILE = os.path.join(HERE, os.pardir, 'datasets', 'circuits_data.npz')
STRONG_W = 2.4
ANSWER_MIN_ZIPF = 3.3
GIVEN_MIN_ZIPF = 3.0
HIDE = (0.0, 0.1, 0.2, 0.3)
LADDER_SHARE = 0.25
MIN_ARROWS = 3
GAP_BINS = (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 99.0)


class Hidden(cz.Data):
    """The solver's data with some pairs taken out."""

    def __init__(self, base, hidden):
        self.__dict__.update(base.__dict__)
        self.hidden = hidden

    def follows(self, i):
        ids, w = super().follows(i)
        if not self.hidden:
            return ids, w
        keep = np.array([(i, j) not in self.hidden for j in ids.tolist()], dtype=bool)
        return ids[keep], w[keep]

    def precedes(self, i):
        ids, w = super().precedes(i)
        if not self.hidden:
            return ids, w
        keep = np.array([(j, i) not in self.hidden for j in ids.tolist()], dtype=bool)
        return ids[keep], w[keep]

    def follows_among(self, i, heads):
        ids, w = super().follows_among(i, heads)
        if not self.hidden:
            return ids, w
        keep = np.array([(i, j) not in self.hidden for j in ids.tolist()], dtype=bool)
        return ids[keep], w[keep]

    def pair(self, a, b):
        return None if (a, b) in self.hidden else super().pair(a, b)


_G = {}


def _init(path):
    data = cz.Data.from_file(path)
    _G['data'] = data
    single = np.array([b' ' not in w for w in data.vocab.tolist()])
    z = data.zipf.astype(np.float32)
    _G['answers'] = np.flatnonzero((data.flags & cz.ANSWER).astype(bool) & single & (z >= ANSWER_MIN_ZIPF))
    _G['givens'] = set(np.flatnonzero(single & (z >= GIVEN_MIN_ZIPF)).tolist())
    _G['succ'] = {}
    _G['pred'] = {}


def strong_succ(i):
    s = _G['succ'].get(i)
    if s is None:
        ids, w = _G['data'].follows(i)
        s = _G['succ'][i] = set(ids[w >= STRONG_W].tolist())
    return s


def strong_pred(i):
    s = _G['pred'].get(i)
    if s is None:
        ids, w = _G['data'].precedes(i)
        s = _G['pred'][i] = set(ids[w >= STRONG_W].tolist())
    return s


def neighbours(c):
    r, k = divmod(c, cz.COLS)
    out = []
    if k > 0:
        out.append(c - 1)
    if k < cz.COLS - 1:
        out.append(c + 1)
    if r > 0:
        out.append(c - cz.COLS)
    if r < cz.ROWS - 1:
        out.append(c + cz.COLS)
    return out


def arrow(a, b):
    """The arrow between neighbouring cells a and b, pointing right or down."""
    return (a, b) if b > a else (b, a)


def page_arrows(cells):
    """The arrows the page draws by itself: one between each box and every
    filled square next to it. A tile next to two boxes often links to just
    one in the game, so a player who leaves these alone sends extras."""
    out = []
    for i in range(len(cells)):
        r, k = divmod(i, cz.COLS)
        for j in ([i + 1] if k < cz.COLS - 1 else []) + ([i + cz.COLS] if r < cz.ROWS - 1 else []):
            if cells[i] and cells[j] and cz.BOX in (cells[i], cells[j]):
                out.append([i, j])
    return out


def layout(rng, ladder):
    """(box cells, given cells, arrows) for a board shape: the ladder, or a
    random one."""
    n = cz.ROWS * cz.COLS
    if ladder:
        boxes = [r * cz.COLS + 1 for r in range(cz.ROWS)]
        givens = [r * cz.COLS + k for r in range(cz.ROWS) for k in (0, 2)]
        arrows = [(r * cz.COLS, r * cz.COLS + 1) for r in range(cz.ROWS)]
        arrows += [(r * cz.COLS + 1, r * cz.COLS + 2) for r in range(cz.ROWS)]
        arrows += [(r * cz.COLS + 1, (r + 1) * cz.COLS + 1) for r in range(cz.ROWS - 1)]
        return boxes, givens, arrows
    while True:
        k = int(rng.integers(4, 7))
        boxes = {int(rng.integers(0, n))}
        while len(boxes) < k:
            c = int(rng.choice(sorted(boxes)))
            boxes.add(int(rng.choice(neighbours(c))))
        arrows = {arrow(a, b) for a in boxes for b in neighbours(a) if b in boxes}
        givens = set()
        for c in sorted(boxes):
            free = [x for x in neighbours(c) if x not in boxes]
            rng.shuffle(free)
            want = int(rng.integers(1, 4))
            for x in free[:want]:
                givens.add(x)
                arrows.add(arrow(c, x))
        # A tile next to a second box sometimes serves it too.
        for g in sorted(givens):
            for c in neighbours(g):
                if c in boxes and arrow(c, g) not in arrows and rng.random() < 0.35:
                    arrows.add(arrow(c, g))
        degree = defaultdict(int)
        for a, b in arrows:
            degree[a] += 1
            degree[b] += 1
        # Every box on the real boards seen so far has 3 or 4 arrows.
        if all(degree[c] >= MIN_ARROWS for c in boxes):
            return sorted(boxes), sorted(givens), sorted(arrows)


def fill(rng, boxes, givens, arrows, tries=60):
    """{cell: word id} making every arrow a strong pair, or None."""
    box_set = set(boxes)
    order = [boxes[0]]
    while len(order) < len(boxes):
        nxt = [c for c in boxes if c not in order and any(arrow(c, o) in arrows for o in order)]
        order.append(nxt[0] if nxt else [c for c in boxes if c not in order][0])
    answers = _G['answers']
    for _ in range(tries):
        word = {order[0]: int(rng.choice(answers))}
        ok = True
        for c in order[1:]:
            cands = None
            for o in order:
                if o not in word:
                    continue
                if (c, o) in arrows:
                    s = strong_pred(word[o])
                elif (o, c) in arrows:
                    s = strong_succ(word[o])
                else:
                    continue
                cands = set(s) if cands is None else cands & s
            if cands is None:
                cands = set(answers[rng.integers(0, len(answers), 200)].tolist())
            cands = [x for x in cands if _G['data'].can_answer(x) and x not in word.values()]
            if not cands:
                ok = False
                break
            word[c] = int(rng.choice(cands))
        if not ok:
            continue
        for g in givens:
            cands = None
            for c in boxes:
                if (g, c) in arrows:
                    s = strong_pred(word[c])
                elif (c, g) in arrows:
                    s = strong_succ(word[c])
                else:
                    continue
                cands = set(s) if cands is None else cands & s
            cands = [x for x in (cands or ()) if x in _G['givens'] and x not in word.values()]
            if not cands:
                ok = False
                break
            word[g] = int(rng.choice(cands))
        if ok and all(c in word for c in box_set):
            return word
    return None


def count_fillings(boxes, givens, arrows, word, limit=2):
    """How many fillings of the boxes make every arrow a strong pair, given
    the tiles (stops at `limit`)."""
    dom = {}
    for c in boxes:
        cands = None
        for g in givens:
            if (g, c) in arrows:
                s = strong_succ(word[g])
            elif (c, g) in arrows:
                s = strong_pred(word[g])
            else:
                continue
            cands = set(s) if cands is None else cands & s
        dom[c] = cands
    order = sorted(boxes, key=lambda c: len(dom[c]) if dom[c] is not None else 10 ** 9)
    found = [0]
    pick = {}

    def go(k):
        if found[0] >= limit:
            return
        if k == len(order):
            found[0] += 1
            return
        c = order[k]
        cands = dom[c]
        for o in order[:k]:
            if (c, o) in arrows:
                s = strong_pred(pick[o])
            elif (o, c) in arrows:
                s = strong_succ(pick[o])
            else:
                continue
            cands = set(s) if cands is None else cands & s
        if cands is None:
            return
        for x in cands:
            if x in pick.values() or not _G['data'].can_answer(x):
                continue
            pick[c] = x
            go(k + 1)
            del pick[c]

    go(0)
    return found[0]


def one_board(seed):
    rng = np.random.default_rng(seed)
    data = _G['data']
    # The shape is drawn once per board, so shapes that are harder to fill
    # don't drop out of the mix.
    ladder = bool(rng.random() < LADDER_SHARE and cz.COLS >= 3)
    for _ in range(40 if ladder else 150):
        boxes, givens, arrows = layout(rng, ladder)
        word = fill(rng, boxes, givens, set(arrows))
        if word is None or count_fillings(boxes, givens, set(arrows), word) != 1:
            continue
        cells = [''] * (cz.ROWS * cz.COLS)
        for c in boxes:
            cells[c] = cz.BOX
        for g in givens:
            cells[g] = data.word(word[g]).upper()
        board = cz.parse_board(cells, [list(a) for a in arrows])
        drawn = page_arrows(cells)
        extras = sum(1 for a in drawn if tuple(a) not in set(arrows))
        boards = {'exact': board, 'page': cz.parse_board(cells, drawn) if extras else board}
        truth = [data.word(word[c]).upper() for c in board.boxes]
        pairs = [(word[a], word[b]) for a, b in arrows]
        records = []
        for share in HIDE:
            k = int(round(share * len(pairs)))
            hidden = {pairs[j] for j in rng.choice(len(pairs), k, replace=False)} if k else set()
            done = {}
            for kind in ('exact', 'page'):
                if kind == 'page' and not extras:
                    records.append((kind, share) + done['exact'])
                    continue
                t = time.perf_counter()
                r = cz.solve(Hidden(data, hidden), boards[kind])
                ms = (time.perf_counter() - t) * 1000
                boxes_out = [(b['word'] == want, b['confidence'], b['gap']) for b, want in zip(r['boxes'], truth)]
                done[kind] = (boxes_out, ms, r['partial'])
                records.append((kind, share) + done[kind])
        return {'seed': seed, 'ladder': ladder, 'extras': extras, 'records': records}
    return None


KINDS = (('exact', "the board's own arrows"),
         ('page', 'the arrows the page draws, extras left in'))


def report(results, asked):
    results = [r for r in results if r]
    if not results:
        print(f'0 of {asked} boards filled: nothing to report (ask for more with --boards)')
        return
    print(f'{len(results)} of {asked} boards filled ({sum(r["ladder"] for r in results)} ladders, '
          f'{sum(1 for r in results if r["extras"])} with arrows the page would add, '
          f'{sum(r["extras"] for r in results)} extra arrows in all)')
    for kind, title in KINDS:
        print(f'\nSolved with {title}:')
        print(' hidden   boxes right   boards right    sure (right)    likely (right)   guess (right)   p50 ms  p95 ms  partial')
        for share in HIDE:
            recs = [rec[2:] for r in results for rec in r['records'] if rec[:2] == (kind, share)]
            boxes = [b for rec in recs for b in rec[0]]
            right = sum(b[0] for b in boxes)
            boards = sum(all(b[0] for b in rec[0]) for rec in recs)
            by = {}
            for label in ('sure', 'likely', 'guess'):
                got = [b for b in boxes if b[1] == label]
                by[label] = f'{len(got):5d} ({(sum(b[0] for b in got) / len(got) * 100 if got else 0):5.1f}%)'
            ms = sorted(rec[1] for rec in recs)
            print(f'  {share:4.0%}   {right / len(boxes) * 100:6.1f}%      {boards / len(recs) * 100:6.1f}%     '
                  f'{by["sure"]}   {by["likely"]}   {by["guess"]}   {ms[len(ms) // 2]:6.0f}  {ms[int(len(ms) * .95)]:6.0f}'
                  f'  {sum(rec[2] for rec in recs)}')
        print('boxes right by shape:')
        for name, keep in (('ladder', lambda r: r['ladder']), ('random', lambda r: not r['ladder']),
                           ('extras', lambda r: r['extras'] > 0)):
            line = []
            for share in HIDE:
                boxes = [b for r in results if keep(r)
                         for rec in r['records'] if rec[:2] == (kind, share) for b in rec[2]]
                line.append(f'{share:.0%}: {sum(b[0] for b in boxes) / max(1, len(boxes)) * 100:5.1f}%')
            print(f'  {name:7s}', '   '.join(line))
        print('  (extras: the boards where the page would draw arrows the board lacks)')
        print('accuracy by score gap (all hidden shares together):')
        boxes = [b for r in results for rec in r['records'] if rec[0] == kind for b in rec[2] if b[1] != 'none']
        for lo, hi in zip(GAP_BINS, GAP_BINS[1:]):
            got = [b for b in boxes if lo <= b[2] < hi]
            if got:
                print(f'  gap {lo:4.1f} - {hi:4.1f}: {len(got):5d} boxes, {sum(b[0] for b in got) / len(got) * 100:5.1f}% right')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--boards', type=int, default=300)
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 4))
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--data', default=DATA_FILE)
    args = ap.parse_args()
    # A bad path would otherwise kill every worker as it starts.
    try:
        cz.Data.from_file(args.data)
    except (OSError, KeyError, ValueError) as e:
        sys.exit(f'cannot read the solver data {args.data}: {e}')
    started = time.time()
    seeds = [args.seed * 1_000_003 + i for i in range(args.boards)]
    with multiprocessing.Pool(args.workers, initializer=_init, initargs=(args.data,)) as pool:
        results = pool.map(one_board, seeds, chunksize=4)
    report(results, args.boards)
    print(f'\n({time.time() - started:.0f} s, {args.workers} workers)')


if __name__ == '__main__':
    main()
