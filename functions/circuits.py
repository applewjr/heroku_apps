"""Circuits solver engine. Pure functions over static data, no Flask or DB.

Puzzmo's Circuits is a small grid of word tiles and empty, numbered boxes.
Arrows join neighbouring cells, pointing right or down, and each box takes
one word that reads as a compound term with every cell its arrows touch, in
the arrow's direction: the arrow's tail first, then its head (SNOW + BALL is
SNOWBALL, FIRE + STATION is FIRE STATION). Each box has one intended answer.

The solver fills every box at once. A filling scores the sum, over arrows, of
how well the two words are known to join, plus a small bonus for common
words. A pair the data lacks costs MISS rather than ruling the filling out,
since no word list knows every compound. An arrow from a tile next to two or
more boxes may not be in the game at all (the page draws one from each box
to every square next to it), so a pair it lacks costs nothing (DOUBT_MISS).
A word the player keeps counts as a tile, and first letters from a Bolt
narrow a box to words that start that way. The best fillings come from a
depth-first search that starts where the givens pin a box down hardest and
prunes on an optimistic bound. A box's confidence is how far the best filling
beats the best one with a different word in that box.

The data (datasets/circuits_data.npz, built by scripts/build_circuits_data.py)
is flat numpy arrays: each word's pairs as the first word (succ_*) and as the
second (pred_*), CSR lists sorted by word id. pack() builds them from plain
Python structures, for the build script and for tests.
"""

import heapq
import re
import time
from collections import defaultdict

import numpy as np

# The board: a grid of rows by columns with some cells blank. Puzzmo's boards
# seen so far are 5 by 3 and Yahoo's 4 by 4 (2026-11-19). The page offers the
# SHAPES (ROWS by COLS first); the solver itself takes any shape.
ROWS = 5
COLS = 3
SHAPES = ((5, 3), (4, 4))
BOX = '?'
MAX_CELL_LEN = 24
_GIVEN = re.compile(r"[A-Z](?:[A-Z '\-]{0,%d}[A-Z])?" % (MAX_CELL_LEN - 2))
_WORD = re.compile(r"[A-Za-z]{1,%d}" % MAX_CELL_LEN)

# Data layout. flags bit: the word may be an answer (a common single word).
ANSWER = 1
# Pair weights are stored as uint8: weight = stored / W_SCALE (0 to ~8).
W_SCALE = 32.0
FIELDS = ('vocab', 'zipf', 'flags', 'succ_ptr', 'succ_ids', 'succ_w',
          'pred_ptr', 'pred_ids', 'pred_w')

# Scoring. A known pair scores its weight up to CAP; a pair the data lacks
# costs MISS. Common words get up to PRIOR x (PRIOR_TO - PRIOR_FROM) on top,
# since the game's answers are everyday words. A word used in two boxes costs
# REPEAT. A multi-word tile the data doesn't know as a whole is matched by
# the word that touches the box, at PART_FACTOR of the weight.
CAP = 3.0
MISS = -1.0
# A tile with arrows to two or more boxes: the page draws an arrow from each
# box to every square next to it, and in the game such a tile often links to
# just one of them. Its arrows are doubtful: a known pair counts as usual, a
# missing one costs DOUBT_MISS, and a word needn't join any of them to be a
# candidate.
DOUBT_MISS = 0.0
PRIOR = 0.25
PRIOR_FROM = 3.0
PRIOR_TO = 5.5
REPEAT = -2.0
PART_FACTOR = 0.7

# Search. Each box keeps its DOMAIN best candidates. A box no tile touches
# takes candidates from its neighbour boxes' TWO_HOP_FROM best. The search
# keeps the TOP_SOLUTIONS best fillings and stops at the time budget.
DOMAIN = 300
TWO_HOP_FROM = 40
TOP_SOLUTIONS = 12
ALTERNATIVES = 4
SWAP_CHECK = 60
BUDGET_S = 1.5
NODE_CHECK = 2000

# Confidence: the score gap from the best filling to the best one with a
# different word in the box. On the synthetic benchmark
# (scripts/backtest_circuits.py, 2026-11-19), boxes with a gap of 3 or more
# were right 97-98% of the time, 2 to 3 about 78%, 1 to 2 about 58%, and
# under 1 well under half.
SURE_GAP = 3.0
LIKELY_GAP = 1.0
NO_RIVAL_GAP = 9.9


class BoardError(ValueError):
    """A board that can't be solved as sent: the route turns it into a 400."""


class Data:
    """The solver's word data: arrays named in FIELDS, indexed by word id.

    vocab is sorted, so a word's id is a binary search away, with no dict of
    100k strings. succ_ptr/succ_ids/succ_w list the words that can follow
    word i (i written first); pred_* the words that can come before it.
    Each row is sorted by id.
    """

    def __init__(self, **arrays):
        for name in FIELDS:
            setattr(self, name, arrays[name])
        self.size = len(self.vocab)

    @classmethod
    def from_file(cls, path):
        with np.load(path, allow_pickle=False) as z:
            return cls(**{name: z[name] for name in FIELDS})

    def id(self, word):
        """A word's (or two-word unit's) id, or -1 if the data doesn't know it."""
        key = ' '.join(str(word).lower().split()).encode('ascii', 'ignore')
        if not key:
            return -1
        i = int(np.searchsorted(self.vocab, key))
        return i if i < self.size and self.vocab[i] == key else -1

    def word(self, i):
        return self.vocab[i].decode()

    def can_answer(self, i):
        return bool(self.flags[i] & ANSWER)

    def follows(self, i):
        """(ids, weights) of the words that can come after word i."""
        s, e = self.succ_ptr[i], self.succ_ptr[i + 1]
        return self.succ_ids[s:e], self.succ_w[s:e].astype(np.float32) / W_SCALE

    def precedes(self, i):
        """(ids, weights) of the words that can come before word i."""
        s, e = self.pred_ptr[i], self.pred_ptr[i + 1]
        return self.pred_ids[s:e], self.pred_w[s:e].astype(np.float32) / W_SCALE

    def follows_among(self, i, heads):
        """(ids, weights) of the words in `heads` (a sorted int array) that
        can come after word i."""
        s, e = self.succ_ptr[i], self.succ_ptr[i + 1]
        ids = self.succ_ids[s:e]
        hit = np.isin(ids, heads, assume_unique=True)
        return ids[hit], self.succ_w[s:e][hit].astype(np.float32) / W_SCALE

    def pair(self, a, b):
        """The weight of "a b" (a first), or None if the data lacks it."""
        if a < 0 or b < 0:
            return None
        s, e = self.succ_ptr[a], self.succ_ptr[a + 1]
        j = s + int(np.searchsorted(self.succ_ids[s:e], b))
        if j < e and self.succ_ids[j] == b:
            return float(self.succ_w[j]) / W_SCALE
        return None


# --- building the arrays ------------------------------------------------------

def _csr(size, src, dst, *cols):
    """Sorted CSR arrays from parallel arrays of (src, dst, extra columns)."""
    order = np.lexsort((dst, src))
    ptr = np.searchsorted(src[order], np.arange(size + 1)).astype(np.int32)
    return (ptr, dst[order].astype(np.int32)) + tuple(c[order] for c in cols)


def pack(pairs, zipf=None, answer=None, words=()):
    """The arrays Data needs, from plain Python structures.

    pairs   {(a, b): weight}: "a b" or "ab" is a compound, a written first
    zipf    {word: Zipf frequency}
    answer  the words that may be answers (None: every single word)
    words   more vocabulary with no pairs
    """
    zipf = zipf or {}
    vocab = sorted({w for ab in pairs for w in ab} | set(words))
    index = {w: i for i, w in enumerate(vocab)}
    size = len(vocab)
    a = np.array([index[x] for x, _ in pairs], dtype=np.int64)
    b = np.array([index[y] for _, y in pairs], dtype=np.int64)
    w = np.array(list(pairs.values()), dtype=np.float64)
    q = np.clip(np.round(w * W_SCALE), 1, 255).astype(np.uint8)
    succ_ptr, succ_ids, succ_w = _csr(size, a, b, q)
    pred_ptr, pred_ids, pred_w = _csr(size, b, a, q)
    flags = np.array([ANSWER if ' ' not in x and (answer is None or x in answer) else 0
                      for x in vocab], dtype=np.uint8)
    width = max(map(len, vocab), default=1)
    return {
        'vocab': np.array([x.encode('ascii') for x in vocab], dtype=f'S{width}'),
        'zipf': np.array([zipf.get(x, 0.0) for x in vocab], dtype=np.float16),
        'flags': flags,
        'succ_ptr': succ_ptr, 'succ_ids': succ_ids, 'succ_w': succ_w,
        'pred_ptr': pred_ptr, 'pred_ids': pred_ids, 'pred_w': pred_w,
    }


# --- the board ------------------------------------------------------------------

class Board:
    """A parsed board: cells in reading order ('' blank, BOX, or a given in
    upper case), arrows as (tail, head) cell pairs, and the boxes in reading
    order, which is how the game numbers them."""

    __slots__ = ('cells', 'arrows', 'rows', 'cols', 'boxes')

    def __init__(self, cells, arrows, rows=ROWS, cols=COLS):
        self.cells = cells
        self.arrows = arrows
        self.rows = rows
        self.cols = cols
        self.boxes = [i for i, c in enumerate(cells) if c == BOX]

    def number(self, cell):
        return self.boxes.index(cell) + 1

    def key(self):
        """The board's identity for the crowd: its cells. Arrows are voted on."""
        return '|'.join(self.cells)


def shape_name(rows, cols):
    """A shape as stored with a board: '5x3'."""
    return f'{rows}x{cols}'


def parse_shape(text):
    """(rows, cols) from shape_name's text, if it's one of SHAPES, else None."""
    try:
        rows, cols = (int(x) for x in str(text).split('x'))
    except ValueError:
        return None
    return (rows, cols) if (rows, cols) in SHAPES else None


def clean_cell(value):
    """A cell as typed: '' (blank), BOX, or a given in upper case with single
    spaces. Raises BoardError for anything else."""
    if not isinstance(value, str):
        raise BoardError('each cell must be text')
    text = ' '.join(value.split()).upper()
    if text in ('', BOX):
        return text
    if len(text) > MAX_CELL_LEN or not _GIVEN.fullmatch(text):
        raise BoardError('a tile holds letters, spaces, hyphens or apostrophes')
    return text


def is_arrow(i, j, rows=ROWS, cols=COLS):
    """True if (i, j) points right or down between neighbouring cells."""
    if not (0 <= i < rows * cols and 0 <= j < rows * cols):
        return False
    return (j == i + 1 and i // cols == j // cols) or j == i + cols


def parse_board(cells, arrows, rows=ROWS, cols=COLS):
    """A Board from a page's JSON: `cells` in reading order, `arrows` as
    [tail, head] pairs. Raises BoardError if it can't be a Circuits board."""
    if not isinstance(cells, list) or len(cells) != rows * cols:
        raise BoardError(f'a board has {rows * cols} cells')
    cells = [clean_cell(c) for c in cells]
    if BOX not in cells:
        raise BoardError('a board needs at least one box')
    if not isinstance(arrows, list) or len(arrows) > 2 * rows * cols:
        raise BoardError('arrows must be a list')
    seen = set()
    for pair in arrows:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not all(isinstance(x, int) and not isinstance(x, bool) for x in pair)):
            raise BoardError('each arrow is a [tail, head] pair of cells')
        i, j = pair
        if not is_arrow(i, j, rows, cols):
            raise BoardError('arrows point right or down, between neighbours')
        if not cells[i] or not cells[j]:
            raise BoardError('an arrow joins two tiles')
        if BOX not in (cells[i], cells[j]):
            raise BoardError('an arrow touches a box')
        seen.add((i, j))
    return Board(cells, sorted(seen), rows, cols)


# --- solving --------------------------------------------------------------------

def _prior(data, i):
    if i < 0:
        return 0.0
    z = float(data.zipf[i])
    return PRIOR * (min(max(z, PRIOR_FROM), PRIOR_TO) - PRIOR_FROM)


def _norm(text):
    return ' '.join(text.lower().split())


class _Pairs:
    """Pair lookups for one solve, with James's added pairs merged in. Only
    the few rows a solve walks whole (the tiles', and the two-hop ones) become
    dicts; single lookups and the box-to-box tables stay in numpy, so a solve
    makes few Python objects (an allocator keeps the memory they took).

    An added pair can name a tile the data doesn't know as a whole (SHOW AND,
    the start of SHOW AND TELL). Such a tile gets an id of its own past the
    vocab: its pairs are the ones James added, on top of what its touching
    word gives (_given_id), and it is never an answer."""

    def __init__(self, data, extra):
        self.data = data
        self.n = len(data.vocab)
        self.units = {}          # a tile's text -> its id past the vocab
        self.unit_text = {}      # and back
        self.extra_succ = defaultdict(dict)
        self.extra_pred = defaultdict(dict)
        self.extra_words = set()
        for (a, b), w in (extra or {}).items():
            a, b = self._side(a), self._side(b)
            if a is None or b is None:
                continue
            self.extra_succ[a][b] = max(w, self.extra_succ[a].get(b, 0.0))
            self.extra_pred[b][a] = max(w, self.extra_pred[b].get(a, 0.0))
            # A box takes one word: a two-word unit in a pair (ICE CREAM, a
            # tile's) never becomes an answer.
            self.extra_words.update(x for x in (a, b) if x < self.n and ' ' not in data.word(x))
        self._succ = {}
        self._pred = {}

    def _side(self, side):
        """One side of an added pair as an id: a word id as given, or text,
        which is the data's word if it knows it, else a tile of its own."""
        if isinstance(side, str):
            text = _norm(side)
            if not text:
                return None
            i = self.data.id(text)
            if i >= 0:
                return i
            if text not in self.units:
                self.units[text] = self.n + len(self.units)
                self.unit_text[self.units[text]] = text
            return self.units[text]
        return int(side) if side >= 0 else None

    def given_id(self, text, given_first):
        """(word id, factor) for a given tile as one side of an arrow: the
        tile as an added pair names it, else as _given_id finds it."""
        u = self.units.get(_norm(text))
        if u is not None:
            return u, 1.0
        return _given_id(self.data, text, given_first)

    def _touching(self, u, first):
        """For a tile of its own: the id of its word that touches the box
        (the last word when the tile is written first), as _given_id picks."""
        parts = self.unit_text[u].split()
        return self.data.id(parts[-1] if first else parts[0]) if len(parts) > 1 else -1

    def may_answer(self, x):
        """Can word x fill a box? A common single word, or a single word in a
        pair James added (his answers can be rarer words). Never a tile."""
        return 0 <= x < self.n and (self.data.can_answer(x) or x in self.extra_words)

    def succ(self, i):
        """{word id: weight} of the words that can follow word i."""
        if i < 0:
            return {}
        d = self._succ.get(i)
        if d is None:
            if i >= self.n:
                t = self._touching(i, True)
                d = {x: min(w, CAP) * PART_FACTOR for x, w in self.succ(t).items()} if t >= 0 else {}
            else:
                ids, w = self.data.follows(i)
                d = dict(zip(ids.tolist(), w.tolist()))
            for b, x in self.extra_succ.get(i, {}).items():
                d[b] = max(x, d.get(b, 0.0))
            self._succ[i] = d
        return d

    def pred(self, i):
        """{word id: weight} of the words that can come before word i."""
        if i < 0:
            return {}
        d = self._pred.get(i)
        if d is None:
            if i >= self.n:
                t = self._touching(i, False)
                d = {x: min(w, CAP) * PART_FACTOR for x, w in self.pred(t).items()} if t >= 0 else {}
            else:
                ids, w = self.data.precedes(i)
                d = dict(zip(ids.tolist(), w.tolist()))
            for a, x in self.extra_pred.get(i, {}).items():
                d[a] = max(x, d.get(a, 0.0))
            self._pred[i] = d
        return d

    def weight(self, a, b):
        """The weight of "a b", or None."""
        if a < 0 or b < 0:
            return None
        if a >= self.n:
            return self.succ(a).get(b)
        if b >= self.n:
            return self.pred(b).get(a)
        d = self._succ.get(a)
        if d is not None:
            return d.get(b)
        w = self.data.pair(a, b)
        x = self.extra_succ.get(a, {}).get(b)
        if x is not None:
            w = x if w is None else max(w, x)
        return w

    def table(self, tails, heads):
        """{tail: {head: score}} for the known pairs from the tail words to
        the head words (a sorted int array), scores capped at CAP."""
        out = {}
        for x in tails:
            if x < 0 or x >= self.n:
                continue
            ids, w = self.data.follows_among(x, heads)
            row = dict(zip(ids.tolist(), np.minimum(w, CAP).tolist())) if len(ids) else {}
            for y, v in self.extra_succ.get(x, {}).items():
                k = int(np.searchsorted(heads, y))
                if k < len(heads) and heads[k] == y:
                    row[y] = max(min(v, CAP), row.get(y, 0.0))
            if row:
                out[x] = row
        return out


def _given_id(data, text, given_first):
    """(word id, factor) for a given tile as one side of an arrow: the whole
    tile if the data knows it, else the word of a multi-word tile that touches
    the box (its last word when the tile comes first), at PART_FACTOR."""
    i = data.id(text)
    if i >= 0:
        return i, 1.0
    parts = text.lower().split()
    if len(parts) > 1:
        i = data.id(parts[-1] if given_first else parts[0])
        if i >= 0:
            return i, PART_FACTOR
    return -1, 0.0


def tile_word(data, text):
    """The name a pair James adds gives a tile: the data's own word for the
    whole tile if it has one, else the tile's text in lower case (the solver
    then knows the tile by it: see _Pairs). Never just one word of a
    multi-word tile, which would join every tile that ends that way."""
    i = data.id(text)
    return data.word(i) if i >= 0 else _norm(text)


def lookup(data, extra=None):
    """The solver's pair lookups, with James's added pairs (as solve takes
    them): given_id(text, given_first) for a tile and weight(a, b) for a
    pair, exactly as a solve uses them. For the admin page."""
    return _Pairs(data, extra)


def _locked_ids(data, board, locked):
    """{box cell: word id} for the words the player already has. A word the
    data doesn't know gets its own negative id: it has no pairs, and its
    arrows then count as unknown rather than missing."""
    out, words = {}, {}
    for cell, word in (locked or {}).items():
        if cell not in board.boxes or not isinstance(word, str) or not _WORD.fullmatch(word.strip()):
            continue
        word = word.strip().lower()
        i = data.id(word)
        out[cell] = i if i >= 0 else -(1000 + cell)
        words[cell] = word.upper()
    return out, words


def _starts(board, starts, lock_ids):
    """{box cell: lower-case letters} a box's word is known to start with (a
    Bolt in the game shows one letter at a time). Kept boxes don't need it."""
    out = {}
    for cell, letters in (starts or {}).items():
        if (cell in board.boxes and cell not in lock_ids and isinstance(letters, str)
                and _WORD.fullmatch(letters.strip())):
            out[cell] = letters.strip().lower()
    return out


def doubtful_tiles(board):
    """The given tiles with arrows to two or more boxes (see DOUBT_MISS)."""
    count = defaultdict(int)
    for i, j in board.arrows:
        for t in (i, j):
            if board.cells[t] != BOX:
                count[t] += 1
    return {t for t, n in count.items() if n >= 2}


def solve(data, board, locked=None, extra=None, budget_s=BUDGET_S, top=TOP_SOLUTIONS, starts=None):
    """Fill every box. Returns a JSON-ready dict:

    boxes        per box, in reading order: its cell and number, the best
                 word (None if nothing fits), confidence ('sure', 'likely',
                 'guess' or 'none'), the gap behind it, letter count, the
                 alternatives, and its arrows with whether each is known
                 (and, for a tile's, whether it's doubtful: see DOUBT_MISS)
    unknown      given tiles the data doesn't know (their arrows don't count)
    start        the number of the box to start with, for hints
    partial      True if the time budget ran out before the search finished
    solutions    the best fillings' words, best first (for the admin and tests)

    locked  {box cell: word} the player already has; each is a tile to the
            boxes next to it
    extra   {(first id, second id): weight}: James's added pairs
    starts  {box cell: letters} a box's word is known to begin with
    """
    deadline = time.monotonic() + budget_s
    pairs = _Pairs(data, extra)
    lock_ids, lock_words = _locked_ids(data, board, locked)
    prefix = _starts(board, starts, lock_ids)
    cells = board.cells
    boxes = board.boxes
    unknown = set()
    doubtful = doubtful_tiles(board)

    # Each box's arrows to given tiles, as (word id, factor, given written
    # first, doubtful). A box the player has filled counts as a tile.
    given_arcs = {c: [] for c in boxes}
    box_arcs = []
    for i, j in board.arrows:
        if cells[i] == BOX and cells[j] == BOX:
            if (i in lock_ids) != (j in lock_ids):
                kept, box = (i, j) if i in lock_ids else (j, i)
                if lock_ids[kept] >= 0:
                    given_arcs[box].append((lock_ids[kept], 1.0, kept == i, False))
            else:
                box_arcs.append((i, j))
            continue
        box, given, given_first = (j, i, True) if cells[j] == BOX else (i, j, False)
        gid, factor = pairs.given_id(cells[given], given_first)
        if gid < 0:
            unknown.add(cells[given])
            continue
        given_arcs[box].append((gid, factor, given_first, given in doubtful))

    def starts_ok(c, x):
        return c not in prefix or (x >= 0 and data.word(x).startswith(prefix[c]))

    # Box domains: candidates from the givens, scored on their own. A word
    # must join all but one of the box's sure arrows. With none sure, or a
    # first letter known, joining any one arrow will do, and the box takes
    # candidates through its neighbour boxes as well (widen).
    local = {c: {} for c in boxes}
    domain = {}
    widen = set()
    for c in boxes:
        if c in lock_ids:
            x = lock_ids[c]
            local[c][x] = _given_score(pairs, x, given_arcs[c]) + _prior(data, x)
            domain[c] = [x]
            continue
        arcs = given_arcs[c]
        sure = sum(1 for a in arcs if not a[3])
        if c in prefix or (arcs and not sure):
            widen.add(c)
        if not arcs:
            continue
        total, hits, sure_hits = defaultdict(float), defaultdict(int), defaultdict(int)
        for gid, factor, given_first, doubt in arcs:
            row = pairs.succ(gid) if given_first else pairs.pred(gid)
            for x, w in row.items():
                total[x] += min(w, CAP) * factor
                hits[x] += 1
                if not doubt:
                    sure_hits[x] += 1
        need = 0 if c in widen else max(1, sure - 1)
        scored = {x: (total[x] + MISS * (sure - sure_hits[x])
                      + DOUBT_MISS * (len(arcs) - sure - (n - sure_hits[x])) + _prior(data, x))
                  for x, n in hits.items() if sure_hits[x] >= need and pairs.may_answer(x) and starts_ok(c, x)}
        if scored:
            best = heapq.nlargest(DOMAIN, scored.items(), key=lambda kv: kv[1])
            local[c] = dict(best)
            domain[c] = [x for x, _ in best]

    # Boxes no known tile pins down: candidates through neighbouring boxes.
    _two_hop(data, pairs, boxes, box_arcs, given_arcs, local, domain, widen, starts_ok)

    # Box-to-box tables, restricted to the domains: {x: {y: score}}.
    tables = {}
    for u, v in box_arcs:
        if u not in domain or v not in domain:
            continue
        heads = np.array(sorted(y for y in domain[v] if y >= 0), dtype=np.int32)
        tables[(u, v)] = pairs.table(domain[u], heads)

    solvable = [c for c in boxes if c in domain]
    sols, partial = _search(data, solvable, domain, local, box_arcs, tables, deadline, top)
    return _report(data, board, pairs, given_arcs, box_arcs, domain, local, tables,
                   sols, partial, unknown, lock_ids, lock_words, doubtful)


def _given_score(pairs, x, arcs):
    """A word's score from a box's given arrows alone."""
    score = 0.0
    if x < 0:
        return 0.0
    for gid, factor, given_first, doubt in arcs:
        w = pairs.weight(gid, x) if given_first else pairs.weight(x, gid)
        score += min(w, CAP) * factor if w is not None else (DOUBT_MISS if doubt else MISS)
    return score


def _two_hop(data, pairs, boxes, box_arcs, given_arcs, local, domain, widen=(), starts_ok=None):
    """Candidates for boxes the tiles don't pin down (no known tile, or no
    word joining all but one of them), from the words that join their
    neighbour boxes' best candidates. A box's own tile arrows still count in
    its score. Runs outwards until nothing changes, so a chain of such boxes
    still gets candidates. A box in `widen` keeps the best half of its own
    candidates and takes these for the rest."""
    def support_for(c):
        support = defaultdict(float)
        reach = defaultdict(set)
        for u, v in box_arcs:
            if c not in (u, v):
                continue
            other = v if u == c else u
            if other not in domain:
                continue
            top = heapq.nlargest(TWO_HOP_FROM, domain[other], key=lambda x: local[other].get(x, 0.0))
            for y in top:
                if y < 0:
                    continue
                row = pairs.pred(y) if u == c else pairs.succ(y)
                for x, w in row.items():
                    if pairs.may_answer(x) and (starts_ok is None or starts_ok(c, x)):
                        support[x] += min(w, CAP)
                        reach[x].add(other)
        if not support:
            return []
        return heapq.nlargest(DOMAIN, support,
                              key=lambda x: (len(reach[x]), support[x] / TWO_HOP_FROM + _prior(data, x)))

    hopped = set()
    while True:
        grew = False
        for c in boxes:
            if c in domain:
                continue
            best = support_for(c)
            if best:
                local[c] = {x: _given_score(pairs, x, given_arcs[c]) + _prior(data, x) for x in best}
                domain[c] = best
                hopped.add(c)
                grew = True
        if not grew:
            break
    for c in boxes:
        if c not in widen or c in hopped or c not in domain:
            continue
        own = domain[c][:DOMAIN // 2]
        mine = set(own)
        more = [x for x in support_for(c) if x not in mine][:DOMAIN - len(own)]
        for x in more:
            local[c][x] = _given_score(pairs, x, given_arcs[c]) + _prior(data, x)
        domain[c] = own + more


def _search(data, boxes, domain, local, box_arcs, tables, deadline, top):
    """The `top` best fillings of `boxes`, as (score, {cell: id}) best first,
    and whether the time budget cut the search short.

    Depth-first, most constrained box first, then always the box with the
    most filled neighbours, so arrow scores come in early. At each box only
    the candidates that join an already filled word are scored one by one;
    the rest are walked lazily in their own order. A branch stops once the
    most the boxes left could add can't reach the worst kept filling, which
    makes the search exact within the domains."""
    if not boxes:
        return [], False
    order = [min(boxes, key=lambda c: (len(domain[c]), -sum(c in a for a in box_arcs)))]
    while len(order) < len(boxes):
        rest = [c for c in boxes if c not in order]
        order.append(max(rest, key=lambda c: (sum(1 for u, v in box_arcs if (u == c and v in order) or (v == c and u in order)),
                                              -len(domain[c]))))
    pos = {c: k for k, c in enumerate(order)}
    # Each box's arrows to boxes filled before it, as (earlier depth, rows):
    # rows maps the earlier box's word to {this box's word: pair score} for
    # the candidate pairs the data knows. Each arrow is scored once, when its
    # later box is filled.
    back = [[] for _ in order]
    for u, v in box_arcs:
        if u not in pos or v not in pos:
            continue
        table = tables.get((u, v), {})
        if pos[u] < pos[v]:
            back[pos[v]].append((pos[u], table))
        else:
            inverse = defaultdict(dict)
            for x, row in table.items():
                for y, s in row.items():
                    inverse[y][x] = s
            back[pos[u]].append((pos[v], dict(inverse)))
    # Each box's candidates best first by their own score, for the lazy walk
    # through the ones that join nothing already filled.
    ranked = {c: sorted(domain[c], key=lambda x: -local[c].get(x, 0.0)) for c in order}
    # The bound: the most each box can still add, its own score plus, per
    # arrow it closes, the best pair its word has with any candidate on the
    # other side (MISS when it has none). Most candidates join nothing, so
    # this is far tighter than full marks on every open arrow.
    upper = []
    for k, c in enumerate(order):
        sides = []
        for d, rows in back[k]:
            best = {}
            for row in rows.values():
                for x, s in row.items():
                    if s > best.get(x, MISS):
                        best[x] = s
            floor_score = 0.0 if any(y < 0 for y in domain[order[d]]) else MISS
            sides.append((best, floor_score))
        top_score = float('-inf')
        for x in domain[c]:
            s = local[c].get(x, 0.0)
            if x >= 0:
                for best, floor_score in sides:
                    s += max(best.get(x, MISS), floor_score)
            top_score = max(top_score, s)
        upper.append(top_score)
    rest_bound = [0.0] * (len(order) + 1)
    for k in range(len(order) - 1, -1, -1):
        rest_bound[k] = rest_bound[k + 1] + upper[k]

    heap = []
    picked = [0] * len(order)
    used = defaultdict(int)
    state = {'nodes': 0, 'partial': False}
    none = float('-inf')

    def go(k, score):
        if state['partial']:
            return
        state['nodes'] += 1
        # >=, not >: Windows' clock ticks every ~15 ms, so "now" can equal a
        # deadline that is already due.
        if state['nodes'] % NODE_CHECK == 0 and time.monotonic() >= deadline:
            state['partial'] = True
            return
        if k == len(order):
            item = (score, tuple(picked))
            if len(heap) < top:
                heapq.heappush(heap, item)
            elif item > heap[0]:
                heapq.heapreplace(heap, item)
            return
        c = order[k]
        loc = local[c]
        floor = heap[0][0] if len(heap) >= top else none
        ahead = rest_bound[k + 1]
        # Every closed arrow starts as a miss; candidates that join the
        # filled word get the difference back.
        base = 0.0
        joined = {}
        for d, rows in back[k]:
            y = picked[d]
            if y < 0:
                continue
            base += MISS
            row = rows.get(y)
            if row:
                for x, s in row.items():
                    joined[x] = joined.get(x, 0.0) + (s - MISS)
        options = []
        for x, extra in joined.items():
            inc = loc.get(x, 0.0) + base + extra
            if used[x]:
                inc += REPEAT * used[x]
            options.append((inc, x))
        options.sort(reverse=True)
        rest = ranked[c]
        i = j = 0
        while True:
            known_top = options[i][0] if i < len(options) else none
            while j < len(rest) and rest[j] in joined:
                j += 1
            if j < len(rest):
                x = rest[j]
                rest_top = loc.get(x, 0.0) + (base if x >= 0 else 0.0)
            else:
                rest_top = none
            best = max(known_top, rest_top)
            if best == none or score + best + ahead <= floor:
                break
            if known_top >= rest_top:
                inc, x = options[i]
                i += 1
            else:
                x = rest[j]
                j += 1
                inc = rest_top + (REPEAT * used[x] if x >= 0 and used[x] else 0.0)
                if score + inc + ahead <= floor:
                    continue
            picked[k] = x
            used[x] += 1
            go(k + 1, score + inc)
            used[x] -= 1
            if state['partial']:
                return
            floor = heap[0][0] if len(heap) >= top else none

    go(0, 0.0)
    best_first = sorted(heap, reverse=True)
    return [(s, {order[k]: x for k, x in enumerate(p)}) for s, p in best_first], state['partial']


def _report(data, board, pairs, given_arcs, box_arcs, domain, local, tables,
            sols, partial, unknown, lock_ids, lock_words, doubtful=frozenset()):
    cells = board.cells
    best_score, best = sols[0] if sols else (0.0, {})

    def name(x, cell):
        if cell in lock_words:
            return lock_words[cell]
        return data.word(x).upper() if x is not None and x >= 0 else None

    def known(a, b):
        return a is not None and b is not None and a >= 0 and b >= 0 and pairs.weight(a, b) is not None

    def fits(c, x, filling):
        """Per arrow touching box c: True/False if word x joins its other end,
        None if that end is unknown or empty."""
        out = []
        for i, j in board.arrows:
            if c not in (i, j):
                continue
            other = j if i == c else i
            if cells[other] == BOX:
                y = filling.get(other)
                out.append(None if y is None or y < 0 or x < 0 else
                           (known(x, y) if i == c else known(y, x)))
            else:
                gid, _ = pairs.given_id(cells[other], other == i)
                out.append(None if gid < 0 or x < 0 else (known(gid, x) if other == i else known(x, gid)))
        return out

    def score_with(c, x, filling):
        """The filling's score with box c's word swapped for x."""
        trial = dict(filling)
        trial[c] = x
        return _score(data, pairs, given_arcs, box_arcs, trial, local)

    out_boxes = []
    for c in board.boxes:
        n = board.number(c)
        x = best.get(c)
        arcs = []
        for i, j in board.arrows:
            if c not in (i, j):
                continue
            other = j if i == c else i
            entry = {'other': other, 'first': i == c}
            if cells[other] == BOX:
                entry['box'] = board.number(other)
                y = best.get(other)
                entry['known'] = known(x, y) if i == c else known(y, x)
            else:
                entry['given'] = cells[other]
                gid, _ = pairs.given_id(cells[other], other == i)
                entry['known'] = gid >= 0 and x is not None and (known(gid, x) if other == i else known(x, gid))
                entry['unknown_tile'] = gid < 0
                entry['doubtful'] = other in doubtful
            arcs.append(entry)
        if x is None:
            out_boxes.append({'cell': c, 'n': n, 'word': None, 'confidence': 'none', 'gap': 0.0,
                              'letters': None, 'locked': False, 'alternatives': [], 'arcs': arcs})
            continue
        rivals = {}
        for s, filling in sols[1:]:
            y = filling.get(c)
            if y is not None and y != x and (y not in rivals or s > rivals[y]):
                rivals[y] = s
        if c not in lock_ids:
            for y in heapq.nlargest(SWAP_CHECK, domain.get(c, []), key=lambda w: local[c].get(w, 0.0)):
                if y != x:
                    s = score_with(c, y, best)
                    if y not in rivals or s > rivals[y]:
                        rivals[y] = s
        alts = sorted(rivals.items(), key=lambda kv: -kv[1])
        gap = best_score - alts[0][1] if alts else NO_RIVAL_GAP
        known_arcs = sum(1 for a in arcs if a['known'])
        if c in lock_ids:
            conf = 'locked'
        # A search the time limit cut short may not have met the best rival,
        # so its gap can't make a box sure.
        elif gap >= SURE_GAP and known_arcs >= 2 and not partial:
            conf = 'sure'
        elif gap >= LIKELY_GAP and known_arcs >= 1:
            conf = 'likely'
        else:
            conf = 'guess'
        word = name(x, c)
        out_boxes.append({
            'cell': c, 'n': n, 'word': word, 'confidence': conf,
            'gap': round(min(gap, NO_RIVAL_GAP), 2), 'letters': len(word.replace(' ', '')),
            'locked': c in lock_ids,
            'alternatives': [{'word': data.word(y).upper(), 'behind': round(best_score - s, 2),
                              'fits': fits(c, y, best)}
                             for y, s in alts[:ALTERNATIVES] if y >= 0],
            'arcs': arcs,
        })
    ranked = [b for b in out_boxes if b['word'] and not b['locked']]
    order = {'sure': 0, 'likely': 1, 'guess': 2}
    start = min(ranked, key=lambda b: (order[b['confidence']], -sum(a['known'] for a in b['arcs']), -b['gap']),
                default=None)
    return {
        'boxes': out_boxes,
        'unknown': sorted(unknown),
        'start': start['n'] if start else None,
        'partial': partial,
        'solutions': [[name(f.get(c), c) for c in board.boxes] for _, f in sols[:3]],
    }


def _score(data, pairs, given_arcs, box_arcs, filling, local):
    """A full filling's score, the same sum the search adds up."""
    score = 0.0
    seen = defaultdict(int)
    for c, x in filling.items():
        if x is None:
            continue
        score += _given_score(pairs, x, given_arcs.get(c, [])) + _prior(data, x)
        if x >= 0:
            score += REPEAT * seen[x]
            seen[x] += 1
    for u, v in box_arcs:
        x, y = filling.get(u), filling.get(v)
        if x is None or y is None or x < 0 or y < 0:
            continue
        w = pairs.weight(x, y)
        score += min(w, CAP) if w is not None else MISS
    return score
