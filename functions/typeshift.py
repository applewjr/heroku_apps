"""Typeshift solver engine. Pure functions over a word list, no Flask or DB.

Puzzmo's Typeshift is a row of columns of letters. Sliding the columns up
and down lines up one letter from each in the highlighted row, so a word
takes its first letter from the first column, its second from the second,
and so on: every word is as long as the board is wide. The goal is to use
every letter on the board in at least one word, and a letter already used
(solid) may be used again. A puzzle is built from a few common words (its
core words), but any word the game accepts counts.

solve() lists every word in the list that fits the board, counts the words
that use each letter, and plans the fewest words that use every letter not
yet solid. That is a set cover in which a word covers one letter in each
column, so no plan is shorter than the most letters left in any one column,
and the search deepens from that bound. Among the shortest plans it keeps
the most familiar one (the highest lowest Zipf, then the highest total),
since the core words are everyday words. On made-up boards built from
common words, that plan is the core words themselves most of the time
(scripts/backtest_typeshift.py).

Whether a column can hold the same letter twice is unknown (2026-11-20), so
the engine allows it: each copy is a letter of its own to use, a word uses
one copy of each of its letters, and a word is played once.
"""

import itertools
import math
import re
import time

# The board: MIN_COLS to MAX_COLS columns of 1 to MAX_LETTERS letters each.
# Words are as long as the board is wide, so the index keeps those lengths.
MIN_COLS = 3
MAX_COLS = 10
MAX_LETTERS = 10

# A word under this Zipf (or with no score) is tagged rare, as on /smush.
RARE_ZIPF = 2.0

# The plan's search stops at this budget and keeps the best plan found; it
# checks the clock every NODE_CHECK nodes.
BUDGET_S = 0.5
NODE_CHECK = 256

_COLUMN = re.compile(r'[a-z]{1,%d}' % MAX_LETTERS)
_WORD = re.compile(r'[a-z]+')


class BoardError(ValueError):
    """A board that can't be solved as sent: the route turns it into a 400."""


def parse_board(columns):
    """The board as sent (one string per column, its letters top to bottom)
    as a tuple of lower-case strings, or BoardError."""
    if not isinstance(columns, (list, tuple)) or not MIN_COLS <= len(columns) <= MAX_COLS:
        raise BoardError(f'a board has {MIN_COLS} to {MAX_COLS} columns')
    board = []
    for n, col in enumerate(columns, 1):
        text = col.strip().lower() if isinstance(col, str) else ''
        if not _COLUMN.fullmatch(text):
            raise BoardError(f'column {n} needs 1 to {MAX_LETTERS} letters')
        board.append(text)
    return tuple(board)


def parse_solid(raw, board):
    """Solid tiles as sent, [[column, position], ...] counted from 0, as a
    frozenset of (column, position) pairs, or BoardError."""
    if raw is None:
        return frozenset()
    if not isinstance(raw, (list, tuple)) or len(raw) > sum(len(col) for col in board):
        raise BoardError('solid must list tiles')
    out = set()
    for item in raw:
        if (not isinstance(item, (list, tuple)) or len(item) != 2
                or not all(isinstance(x, int) and not isinstance(x, bool) for x in item)):
            raise BoardError('a solid tile is [column, position]')
        c, i = item
        if not (0 <= c < len(board) and 0 <= i < len(board[c])):
            raise BoardError('a solid tile is off the board')
        out.add((c, i))
    return frozenset(out)


def fits_board(word, board):
    """Does `word` take one letter from each column, in order?"""
    return (isinstance(word, str) and len(word) == len(board)
            and all(ch in col for ch, col in zip(word, board)))


class Index:
    """The word list by length: each length one newline-joined string, with
    a newline at both ends, so a board is one regex scan and a lookup one
    substring search. Lengths 3-10 of the 173k-word list take about 1.1 MB."""

    __slots__ = ('texts', 'size')

    def __init__(self, texts):
        self.texts = texts
        self.size = sum(text.count('\n') - 1 for text in texts.values())

    @classmethod
    def build(cls, words):
        """From any iterable of words: those of MIN_COLS to MAX_COLS letters
        a-z are kept (a non-string, such as data.words' one NaN, is not)."""
        # Lists, not sets, while building: a set of 125k words would cost
        # several MB more for the moment it's held.
        by_len = {}
        for w in words:
            if isinstance(w, str) and MIN_COLS <= len(w) <= MAX_COLS and _WORD.fullmatch(w):
                by_len.setdefault(len(w), []).append(w)
        texts = {}
        for n in list(by_len):
            ws = by_len.pop(n)
            ws.sort()
            texts[n] = '\n' + '\n'.join(w for w, _ in itertools.groupby(ws)) + '\n'
        return cls(texts)

    def __contains__(self, word):
        text = self.texts.get(len(word)) if isinstance(word, str) else None
        return bool(text) and _WORD.fullmatch(word) is not None and f'\n{word}\n' in text

    def fits(self, board):
        """Every listed word that fits the board, alphabetically."""
        text = self.texts.get(len(board))
        if not text:
            return []
        pattern = ''.join('[' + ''.join(sorted(set(col))) + ']' for col in board)
        return re.findall('^' + pattern + '$', text, re.M)


def _zipf(value):
    """A popularity value as a float, 0.0 for none (or a NaN)."""
    try:
        z = float(value)
    except (TypeError, ValueError):
        return 0.0
    return z if math.isfinite(z) else 0.0


def solve(index, board, solid=frozenset(), exclude=frozenset(), popularity=None,
          blocked=None, budget_s=BUDGET_S):
    """Everything the page shows for a board.

    index      -- an Index of the word list
    board      -- from parse_board
    solid      -- (column, position) tiles already solid in the game
    exclude    -- words to leave out (ones the game refused for this player)
    popularity -- {word: Zipf}; a word missing from it counts as 0
    blocked    -- word -> True for words the plan and hints avoid unless
                  nothing else uses a letter (offensive words); None: none

    Returns a dict:
      words -- every fitting word, most familiar first: {'word', 'zipf',
               'rare', 'gain'}, gain = how many letters not yet solid it
               would turn solid
      tiles -- per column, per position: {'letter', 'count' (words using
               it), 'solid', 'repeat' (its letter is in that column twice)}
      plan  -- the fewest words that turn every letter left solid, most
               familiar first; letters no word uses are left out
      exact -- whether the plan is proven the fewest (False when the
               search ran out of time: then it is the shortest found)
      left  -- letters not yet solid
      dead  -- [column, position] of letters left that no word uses
      hints -- [{'col', 'pos', 'word'}] for the letters left that some word
               uses, hardest first (fewest words), with the word to hint:
               the plan's word for that letter
    """
    popularity = popularity or {}
    deadline = time.monotonic() + budget_s
    n = len(board)
    words = [w for w in index.fits(board) if w not in exclude]
    zipf = {w: _zipf(popularity.get(w)) for w in words}
    words.sort(key=lambda w: (-zipf[w], w))

    # The positions of each (column, letter), and those not yet solid.
    spots = {}
    for c, col in enumerate(board):
        for i, letter in enumerate(col):
            spots.setdefault((c, letter), []).append(i)
    hollow = {key: [i for i in pos if (key[0], i) not in solid] for key, pos in spots.items()}
    need = {key: len(pos) for key, pos in hollow.items() if pos}

    users = dict.fromkeys(spots, 0)
    for w in words:
        for c in range(n):
            users[(c, w[c])] += 1

    plan, exact = _plan(words, zipf, need, n, blocked, deadline)
    plan.sort(key=lambda w: (-zipf[w], w))

    # Each hollow copy of a letter gets one plan word (copies in order).
    hint_for = {}
    for key, pos in hollow.items():
        planned = [w for w in plan if w[key[0]] == key[1]]
        for i, w in zip(pos, planned):
            hint_for[(key[0], i)] = w
    left = [(c, i) for c, col in enumerate(board) for i in range(len(col)) if (c, i) not in solid]
    dead = [[c, i] for c, i in left if users[(c, board[c][i])] == 0]
    hints = sorted(((users[(c, board[c][i])], c, i) for c, i in left if (c, i) in hint_for))

    return {
        'words': [{'word': w, 'zipf': round(zipf[w], 2), 'rare': zipf[w] < RARE_ZIPF,
                   'gain': sum(1 for c in range(n) if need.get((c, w[c]), 0) > 0)}
                  for w in words],
        'tiles': [[{'letter': letter, 'count': users[(c, letter)], 'solid': (c, i) in solid,
                    'repeat': len(spots[(c, letter)]) > 1}
                   for i, letter in enumerate(col)] for c, col in enumerate(board)],
        'plan': plan,
        'exact': exact,
        'left': len(left),
        'dead': dead,
        'hints': [{'col': c, 'pos': i, 'word': hint_for[(c, i)]} for _count, c, i in hints],
    }


def _plan(words, zipf, need, n, blocked, deadline):
    """The fewest words meeting `need` ({(column, letter): copies left}) and,
    of those, the most familiar; and whether that is proven the fewest.

    `words` is most familiar first. A word meets one copy of each of its
    letters. Copies no word can meet are left out (a letter no word uses
    can't be planned for). Words `blocked` calls True are left out, unless a
    letter has too few other words: then the blocked words with that letter
    may be used, for any of their letters."""
    clean = [w for w in words if not (blocked and blocked(w))]
    allowed = set(clean)
    if blocked:
        for (c, letter), copies in need.items():
            if sum(1 for w in clean if w[c] == letter) < copies:
                allowed.update(w for w in words if w[c] == letter)
    # Each letter's candidates are every allowed word with that letter: the
    # search branches on one letter's candidates at a time, so leaving out a
    # word that has the letter (a blocked word let in for another letter)
    # could miss the shortest plan.
    avail = {}
    for (c, letter) in need:
        ws = [w for w in words if w in allowed and w[c] == letter]
        if ws:
            avail[(c, letter)] = ws
    target = {key: min(need[key], len(ws)) for key, ws in avail.items()}
    if not target:
        return [], True

    pool = sorted({w for ws in avail.values() for w in ws}, key=lambda w: (-zipf[w], w))
    greedy = _greedy(pool, target, n)

    remaining = dict(target)
    col_left = [0] * n
    for (c, _letter), copies in remaining.items():
        col_left[c] += copies
    chosen, chosen_set = [], set()
    top_z = max(zipf[w] for w in pool)
    state = {'nodes': 0, 'timeout': False, 'best': None, 'low': None, 'total': None}

    def take(w):
        hit = []
        for c in range(n):
            key = (c, w[c])
            if remaining.get(key, 0) > 0:
                remaining[key] -= 1
                col_left[c] -= 1
                hit.append(key)
        return hit

    def give_back(hit):
        for key in hit:
            remaining[key] += 1
            col_left[key[0]] += 1

    def search(slots, low, total):
        state['nodes'] += 1
        if state['nodes'] % NODE_CHECK == 0 and time.monotonic() >= deadline:
            state['timeout'] = True
        if state['timeout']:
            return
        if max(col_left) == 0:
            if state['best'] is None or (low, total) > (state['low'], state['total']):
                state.update(best=list(chosen), low=low, total=total)
            return
        if max(col_left) > slots:
            return
        # A plan this one could still become is no more familiar than the
        # best so far: its lowest Zipf can only fall, and its total is at
        # most the best word left in every open slot.
        if state['best'] is not None:
            if low < state['low'] or (low == state['low'] and total + slots * top_z <= state['total']):
                return
        # The letter left with the fewest words still free.
        unit, free = None, None
        for key, copies in remaining.items():
            if copies <= 0:
                continue
            count = len(avail[key]) - sum(1 for w in chosen if w[key[0]] == key[1])
            if free is None or count < free:
                unit, free = key, count
                if count <= copies:
                    break
        if free < remaining[unit]:
            return
        for w in avail[unit]:
            if w in chosen_set:
                continue
            hit = take(w)
            chosen.append(w)
            chosen_set.add(w)
            search(slots - 1, min(low, zipf[w]), total + zipf[w])
            chosen.pop()
            chosen_set.discard(w)
            give_back(hit)
            if state['timeout']:
                return

    # Deepen from the bound; the greedy plan's length always has a plan. A
    # plan found at a size is the fewest: every smaller size was searched
    # to the end first (a time-out stops the deepening).
    bound = max(col_left)
    for size in range(bound, len(greedy) + 1):
        search(size, math.inf, 0.0)
        if state['best'] is not None or state['timeout']:
            break
    if state['best'] is None:
        return list(greedy), len(greedy) == bound
    return list(state['best']), True


def _greedy(pool, target, n):
    """A plan built one word at a time: each time the word that meets the
    most copies left (the most familiar on a tie, as `pool` is ordered)."""
    remaining = dict(target)
    plan, used = [], set()
    while any(copies > 0 for copies in remaining.values()):
        best, best_gain = None, 0
        for w in pool:
            if w in used:
                continue
            gain = sum(1 for c in range(n) if remaining.get((c, w[c]), 0) > 0)
            if gain > best_gain:
                best, best_gain = w, gain
        if best is None:
            break
        used.add(best)
        plan.append(best)
        for c in range(n):
            key = (c, best[c])
            if remaining.get(key, 0) > 0:
                remaining[key] -= 1
    return plan
