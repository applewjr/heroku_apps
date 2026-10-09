"""ICE COLD planner for Smush (hankgreen.com/smush), served on /smush_dev.

Smush's secret ICE COLD bonus x5s the whole final score (after Clean Plate's
+50 and the rest) for a game of at least five words in which no word ever
touched the spicy letter. Chasing it together with a clean plate is a game
against a random spice, not a word-set puzzle, and this module plays it as
one.

Rules it relies on, read from the game's inline JS (2026-10-08):
  - pickSpice(): after every ACCEPTED word the spicy tile moves to a
    uniformly random living tile other than the current one, or to none when
    there is no such tile. A refused word spends nothing and moves nothing.
  - A word can't be played twice; only the gold center is free.

So under ICE COLD the spicy tile is never spent and is always alive, and the
spice only goes null when that tile is the last one standing. A clean plate
can only end one way: a "closer" word kills every tile but the spicy one,
then, in the spice-free turn, a "solo" word (that letter plus the center
only) spends exactly its remaining uses. Solo words are scarce - usually two
to four letters a board, one or two copies each - and some boards can't be
cleaned ICE COLD at all (a Q that no word but the pangram can spend).

The model. A state is the remaining uses L (a count 0..5 per outer letter, in
sorted letter order) and the spicy tile s (a letter index, or None in the
null turn). V(L, s) is the chance of finishing clean and ICE COLD from there
under the best play, where:
  - a move is a cost signature (uses of each letter) that avoids s and fits
    L; words sharing a signature are interchangeable, so they form a Group;
  - Smush may refuse a word, and a refusal is a free retry, so a state's
    value is a try list: the best group's value if Smush takes one of its
    words, else the second best's (acceptance priors below);
  - a group with n words is only planned at L when some letter i it uses has
    L[i] < (n + 1) * c[i]. Every use lowers that letter, so no path can use
    the group more than n times and no plan counts on replaying a word.
    Without it the table planned BIB, NTH and VIBE twice and the B board
    went from a 93% forecast to 4 wins in 10. It also delays some legal
    first uses, so the shown chance can undersell a board;
  - each use of a group needs a word of its own (Group.use_accept): draining
    G:4 with GIG and IGG needs both accepted, not the group's odds twice.
    Without it every learned game on the I board died on a refused IGG.

Offline, against the 11 boards embedded in the game page (spice moved as the
game moves it, the game's own lists refusing words, 10 games a board), the
All 8 + Ice Cold mode tried on /smush on 2026-10-07 (one fixed word set,
spice-filtered; undone before it reached main) finished clean and ICE COLD once
in 110 games. This one
won 45 of the 70 games on the 7 boards where it's possible, and 58 of 70
once the words refused and played in those games were known.

V is solved exactly, bottom-up with numpy, over the box of every state at or
below a root L (mixed-radix indexing, one layer per total remaining uses).
The full board's box - 1.68M states x 9 spice columns - takes 15-45 s, so
/smush_dev builds it once per board in a background thread (TableCache);
smaller boxes are solved per request from the player's exact word pool.
"""

import hashlib
import math
import random
import threading
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import date

import numpy as np

from functions.all_words import smush_word_score

N_LETTERS = 8
FULL_ROOT = (5,) * N_LETTERS
NULL = N_LETTERS                 # V's column for the spice-free turn

# A request solves the box below the player's own uses when it holds at most
# this many states. Measured 2026-10-08: 0.3 s median, 0.8 s worst at
# 20-50k states, under 0.1 s below 5k. Above it the shared full-board table
# answers. Confirm against the dyno.
ICE_EXACT_STATES = 50_000
# Boxes bigger than this store V compactly: float16 while building, then
# uint8 - the full board is 15 MB that way instead of 60.
COMPACT_STATES = 200_000

# Acceptance priors: the share of our dictionary's words Smush accepted, by
# Zipf popularity bucket (>= 3.3, >= 2.7, >= 2.0, > 0, unknown), measured
# 2026-10-08 against the game's own lists on the 11 boards embedded in its
# page (about 2,500 board-word checks). Solo words are refused more often
# than other words of the same popularity: interjections and slang (YAY,
# HEH, POOP, TIT).
ZIPF_FLOORS = (3.3, 2.7, 2.0)
ACCEPT_MULTI = (0.92, 0.84, 0.74, 0.59, 0.29)
ACCEPT_SOLO = (0.83, 0.71, 0.67, 0.15, 0.05)
# Players who flagged a word with ✗ (and it isn't removed yet) scale its
# prior: 0, 1, 2, 3+ players. Soft, because some players ✗ words the game
# took. A word anyone has played is accepted outright.
INVALID_VOTE_FACTOR = (1.0, 0.4, 0.15, 0.05)
# Only a group's top few words count toward its acceptance: the rest are the
# long tail of obscure near-duplicates that tend to be refused together.
GROUP_ACCEPT_WORDS = 3
# Backups a use of a group may fall back on beyond its own word (Group.use_accept).
GROUP_BACKUP_WORDS = 2
# The most common word is offered first when trying it first costs at most
# this much of the chance of finishing (see _ranked).
NEAR_BEST = 0.005


def word_accept(pop, solo, invalid_players=0, played=False):
    """Chance Smush accepts one word: the popularity prior for its kind,
    scaled down by ✗ votes; 1.0 once a player has played it."""
    if played:
        return 1.0
    if pop >= ZIPF_FLOORS[0]:
        bucket = 0
    elif pop >= ZIPF_FLOORS[1]:
        bucket = 1
    elif pop >= ZIPF_FLOORS[2]:
        bucket = 2
    else:
        bucket = 3 if pop > 0 else 4
    prior = (ACCEPT_SOLO if solo else ACCEPT_MULTI)[bucket]
    return prior * INVALID_VOTE_FACTOR[min(int(invalid_players), len(INVALID_VOTE_FACTOR) - 1)]


@dataclass(frozen=True)
class Group:
    """Words that cost the same uses of each letter, in try order: most
    likely accepted first, then highest base score."""
    sig: tuple                   # uses of each letter, in the planner's letter order
    words: tuple
    accepts: tuple               # word_accept() of each word, same order
    pops: tuple                  # Zipf popularity of each word, same order
    accept: float                # chance Smush takes at least one top word

    @property
    def n(self):
        return len(self.words)

    @property
    def solo(self):
        return sum(1 for c in self.sig if c) == 1

    def use_accept(self, j, J):
        """Chance Smush takes one use of this group when it can be used j
        more times (this one included) and at most J times on any path.

        Each use needs a word of its own - a group that drains G:4 with two
        G-solo words needs both of them accepted, not the best one twice - so
        this use gets the j-th most likely word, backed up by the words no
        path can ever need. A group used once is backed by its top words, as
        `accept` is."""
        a = self.accepts
        miss = 1.0 - a[min(j, len(a)) - 1]
        for k in range(J, min(len(a), J + GROUP_BACKUP_WORDS)):
            miss *= 1.0 - a[k]
        return 1.0 - miss


def uses_left(sig, L):
    """How many more times the signature fits in L."""
    return min(l // c for c, l in zip(sig, L) if c)


def make_groups(results, letters, accepted=frozenset(), invalid_counts=None):
    """Group words for planning.

    results        -- smush_solver output (list_len=None, ice_cold off): every
                      word playable at the state being planned. A word that
                      doesn't fit now never will, since uses only go down.
    letters        -- the outer letters, in the planner's (sorted) order
    accepted       -- words a player has played, so Smush takes them
    invalid_counts -- {word: players who flagged it with ✗}

    Pangrams are dropped: they use every outer letter, so they always touch
    the spicy one.
    """
    invalid_counts = invalid_counts or {}
    by_sig = {}
    for r in results:
        if r['pangram']:
            continue
        sig = tuple(r['cost'].get(l, 0) for l in letters)
        if any(sig):
            by_sig.setdefault(sig, []).append(r)

    groups = []
    for sig in sorted(by_sig):
        solo = sum(1 for c in sig if c) == 1
        rows = sorted(
            ((word_accept(r.get('pop', 0.0), solo, invalid_counts.get(r['word'], 0),
                          r['word'] in accepted),
              r['base'], r['word'], r.get('pop', 0.0)) for r in by_sig[sig]),
            key=lambda t: (-t[0], -t[1], t[2]))
        miss = 1.0
        for a, _, _, _ in rows[:GROUP_ACCEPT_WORDS]:
            miss *= 1.0 - a
        groups.append(Group(sig, tuple(t[2] for t in rows), tuple(t[0] for t in rows),
                            tuple(t[3] for t in rows), 1.0 - miss))
    return groups


class Pool:
    """A list of groups plus the arrays the vectorized move search needs."""

    def __init__(self, groups):
        self.groups = list(groups)
        self.SIG = np.array([g.sig for g in self.groups], dtype=np.int64).reshape(-1, N_LETTERS)
        self.NW = np.array([g.n for g in self.groups], dtype=np.int64)
        # Base score of each group's first word, the one tried first.
        self.BASE = np.array([smush_word_score(g.words[0]) for g in self.groups], dtype=np.int64)

    def fingerprint(self):
        """Changes whenever a word, its group or its acceptance does."""
        canon = repr([(g.sig, g.words, tuple(round(a, 4) for a in g.accepts))
                      for g in self.groups])
        return hashlib.sha1(canon.encode()).hexdigest()


def _place_values(R):
    pw = np.ones(len(R), dtype=np.int64)
    pw[1:] = np.cumprod(R[:-1])
    return pw


class Table:
    """V(L, s) for every state in the box [0, root]: one row per state, one
    column per spicy letter plus NULL for the spice-free turn."""

    def __init__(self, root, V, scale=1.0):
        self.root = tuple(int(r) for r in root)
        self.R = np.array([r + 1 for r in self.root], dtype=np.int64)
        self.PW = _place_values(self.R)
        self.V = V
        self.scale = scale

    @property
    def nbytes(self):
        return self.V.nbytes

    def covers(self, L):
        return all(0 <= l <= r for l, r in zip(L, self.root))

    def index(self, L):
        return int(np.dot(np.asarray(L, dtype=np.int64), self.PW))

    def value(self, L, s):
        return float(self.V[self.index(L), NULL if s is None else s]) * self.scale


def solve_box(root, groups, compact=None, chunk=1 << 15):
    """Solve V exactly for every state in the box [0, root].

    States are solved in order of total remaining uses, so every move's
    child is already known when its parent is reached. For each state and
    each spicy column, the moves are the groups that fit, avoid that letter
    and pass the n-uses rule; a move's value is the mean of the child's
    values over where the spice can land (or the child's spice-free value
    when only the spicy tile is left); the state's value is the top-2 try
    list over its moves.
    """
    root = tuple(int(r) for r in root)
    R = np.array([r + 1 for r in root], dtype=np.int64)
    PW = _place_values(R)
    N = int(np.prod(R))
    total = sum(root)
    if compact is None:
        compact = N > COMPACT_STATES

    fit = [g for g in groups if all(c <= r for c, r in zip(g.sig, root))]
    SIG = np.array([g.sig for g in fit], dtype=np.int64).reshape(-1, N_LETTERS)
    POS = SIG > 0
    CIDX = SIG @ PW
    CTOT = SIG.sum(1)
    LIMIT = (np.array([g.n for g in fit], dtype=np.int64)[:, None] + 1) * SIG
    # Acceptance of one use of each group, by how many uses it has left (j):
    # ACCJ[g][j], for j up to the most any path can make (J).
    J = [min(g.n, uses_left(g.sig, root)) for g in fit]
    ACCJ = [np.array([0.0] + [g.use_accept(j, Jg) for j in range(1, Jg + 1)], np.float32)
            for g, Jg in zip(fit, J)]

    # Each state's living-tile count, and the states in order of total uses.
    tot = np.zeros(N, dtype=np.int8)
    alive_count = np.zeros(N, dtype=np.int8)
    for start in range(0, N, chunk):
        stop = min(N, start + chunk)
        d = (np.arange(start, stop, dtype=np.int64)[:, None] // PW) % R
        tot[start:stop] = d.sum(1)
        alive_count[start:stop] = (d > 0).sum(1)
    order = np.argsort(tot, kind='stable').astype(np.int32)
    bounds = np.searchsorted(tot[order], np.arange(total + 2))
    del tot

    V = np.zeros((N, N_LETTERS + 1), dtype=np.float16 if compact else np.float32)
    sumv = np.zeros(N, dtype=np.float32)   # sum of V over the spice columns of living tiles
    for t in range(1, total + 1):
        layer = order[bounds[t]:bounds[t + 1]]
        moves = np.nonzero(CTOT <= t)[0]
        for a in range(0, len(layer), chunk):
            S = layer[a:a + chunk].astype(np.int64)
            D = (S[:, None] // PW) % R
            alive = D > 0
            lone = alive_count[S] == 1
            n = len(S)
            q1 = np.zeros((n, N_LETTERS + 1), np.float32)
            a1 = np.zeros((n, N_LETTERS + 1), np.float32)
            q2 = np.zeros((n, N_LETTERS + 1), np.float32)
            a2 = np.zeros((n, N_LETTERS + 1), np.float32)
            for g in moves:
                ok = (D >= SIG[g]).all(1) & ((D < LIMIT[g]) & POS[g]).any(1)
                rows = np.nonzero(ok)[0]
                if not len(rows):
                    continue
                child = S[rows] - CIDX[g]
                cv = V[child].astype(np.float32)
                csum = sumv[child]
                cn = alive_count[child].astype(np.float32)
                # Spicy column s: the spice lands on one of the child's other
                # living tiles; if s is the only one left, the turn is spice-free.
                with np.errstate(divide='ignore', invalid='ignore'):
                    q = np.where(cn[:, None] >= 2,
                                 (csum[:, None] - cv[:, :N_LETTERS]) / (cn[:, None] - 1),
                                 cv[:, NULL:NULL + 1])
                # ... and only where s is alive and the move doesn't touch it.
                q = np.where(alive[rows] & ~POS[g][None, :], q, -1.0)
                # The spice-free turn (one tile left): from no spice, it lands
                # on any living tile - or the board is clear.
                qn = np.where(child == 0, 1.0, csum / np.maximum(cn, 1.0))
                qn = np.where(lone[rows], qn, -1.0)
                qa = np.concatenate([q, qn[:, None]], axis=1).astype(np.float32)

                r1, r2 = q1[rows], q2[rows]
                s1, s2 = a1[rows], a2[rows]
                b1 = qa > r1
                b2 = ~b1 & (qa > r2)
                pos = POS[g]
                left = (D[rows][:, pos] // SIG[g][pos]).min(1)
                acc = ACCJ[g][np.clip(left, 1, J[g])][:, None]
                q2[rows] = np.where(b1, r1, np.where(b2, qa, r2))
                a2[rows] = np.where(b1, s1, np.where(b2, acc, s2))
                q1[rows] = np.where(b1, qa, r1)
                a1[rows] = np.where(b1, acc, s1)
            vs = a1 * q1 + (1.0 - a1) * a2 * q2
            V[S] = vs
            sumv[S] = (vs[:, :N_LETTERS] * alive).sum(1)
    del sumv, order

    if not compact:
        return Table(root, V)
    Vq = np.empty(V.shape, dtype=np.uint8)
    for start in range(0, N, chunk):
        Vq[start:start + chunk] = np.rint(
            np.clip(V[start:start + chunk].astype(np.float32), 0.0, 1.0) * 255)
    return Table(root, Vq, 1.0 / 255)


@dataclass
class Move:
    group: Group
    q: float                     # chance of finishing after this move
    smushes: int                 # letters it spends to zero
    accept: float                # chance Smush takes this use of the group

    def entry(self, letters, k=0):
        """The k-th word of this move, shaped like a /smush result row so the
        page's ✓ Played can spend it."""
        word = self.group.words[k]
        base = smush_word_score(word)
        mult = 1 + self.smushes
        uses = sum(self.group.sig)
        return {
            'word': word,
            'base': base,
            'mult': mult,
            'pts': base * mult,
            'spicy_uses': 0,
            'smushes': self.smushes,
            'pangram': False,
            'cost': {letters[i]: c for i, c in enumerate(self.group.sig) if c},
            'pop': round(self.group.pops[k], 1),
            'accept': round(self.group.accepts[k], 2),
            'efficiency': round(base * mult / max(uses, 1), 1),
            'q': round(self.q, 3),
        }


def _child_values(table, L, s, SIG):
    """q for each move (a row of SIG) from (L, s): the chance of finishing
    from the state it leaves, averaged over where the spice lands next."""
    child = np.asarray(L, dtype=np.int64)[None, :] - SIG
    rows = table.V[child @ table.PW].astype(np.float32) * table.scale
    alive = child > 0
    landing = alive.copy()
    if s is not None:
        landing[:, s] = False
    n = landing.sum(1)
    spread = (rows[:, :N_LETTERS] * landing).sum(1) / np.maximum(n, 1)
    q = np.where(n > 0, spread, rows[:, NULL])
    return np.where(alive.any(1), q, 1.0)


def legal_moves(pool, L, s, used=None):
    """Indexes of the pool's groups that fit L, avoid s and pass the n-uses
    rule (counting `used` {sig: uses so far} against each group's words)."""
    if not pool.groups:
        return np.zeros(0, dtype=np.int64)
    Lv = np.asarray(L, dtype=np.int64)
    nw = pool.NW
    if used:
        nw = nw - np.array([used.get(g.sig, 0) for g in pool.groups], dtype=np.int64)
    ok = (pool.SIG <= Lv).all(1) & (nw > 0)
    ok &= ((Lv < (nw[:, None] + 1) * pool.SIG) & (pool.SIG > 0)).any(1)
    if s is not None:
        ok &= pool.SIG[:, s] == 0
    return np.nonzero(ok)[0]


def _ranked(table, pool, L, s, used=None):
    """(indexes into the pool, q, smushes) for every legal move from (L, s),
    best first.

    A refusal is a free retry, so trying moves in order of chance (q) is
    optimal - but trying a long shot first rarely buys much: swapping it
    behind a likelier word costs a_i * a_j * (q_i - q_j). So the move offered
    first is the most likely to be accepted among those whose try-first
    value (try_value with the best other move as the fallback) is within
    NEAR_BEST of the best order's - James asked for common words, and every
    refusal costs the player a try. The rest follow by chance, acceptance,
    points; ties keep the pool's own (signature) order, so it's stable."""
    idx = legal_moves(pool, L, s, used)
    if not len(idx):
        return idx, np.zeros(0), np.zeros(0, dtype=np.int64), np.zeros(0)
    SIG = pool.SIG[idx]
    q = _child_values(table, L, s, SIG)
    smushes = ((SIG == np.asarray(L, dtype=np.int64)[None, :]) & (SIG > 0)).sum(1)
    acc = np.array([move_accept(pool.groups[gi], L, table.root) for gi in idx])
    pts = pool.BASE[idx] * (1 + smushes)
    order = np.lexsort((-pts, -acc, -q))
    idx, q, smushes, acc, pts = idx[order], q[order], smushes[order], acc[order], pts[order]
    if len(idx) > 1:
        best = acc[0] * q[0] + (1.0 - acc[0]) * acc[1] * q[1]
        first = acc * q + (1.0 - acc) * acc[0] * q[0]   # move i first, then move 0
        first[0] = best
        fine = np.nonzero(first >= best - NEAR_BEST)[0]
        pick = fine[np.lexsort((-q[fine], -pts[fine], -acc[fine]))[0]]
        if pick:
            order = np.concatenate([[pick], np.delete(np.arange(len(idx)), pick)])
            idx, q, smushes, acc = idx[order], q[order], smushes[order], acc[order]
    return idx, q, smushes, acc


def move_accept(group, L, root):
    """Group.use_accept for a use of `group` at L, in a table rooted at `root`
    (the most uses any path from there can make)."""
    J = min(group.n, uses_left(group.sig, root))
    return group.use_accept(max(1, min(uses_left(group.sig, L), J)), J)


def rank_moves(table, pool, L, s, used=None):
    """Every legal ICE COLD move from (L, s), best first (see _ranked)."""
    idx, q, smushes, acc = _ranked(table, pool, L, s, used)
    return [Move(pool.groups[gi], float(q[k]), int(smushes[k]), float(acc[k]))
            for k, gi in enumerate(idx)]


def best_move(table, pool, L, s, used=None):
    """Just the top of rank_moves, or None."""
    idx, q, smushes, acc = _ranked(table, pool, L, s, used)
    if not len(idx):
        return None
    return Move(pool.groups[idx[0]], float(q[0]), int(smushes[0]), float(acc[0]))


def try_value(moves):
    """The chance of finishing when the best move is tried first and the
    second best only if Smush refuses every word of the first."""
    if not moves:
        return 0.0
    first = moves[0]
    value = first.accept * first.q
    if len(moves) > 1:
        value += (1.0 - first.accept) * moves[1].accept * moves[1].q
    return value


def finish_words(table, pool, L, s, seed, rollouts=24, top=3):
    """Play the plan out `rollouts` times against a random spice (words all
    taken) and report the words its winning games end on - the closer and
    the solo word worth saving - with the share of games it won."""
    rng = random.Random(seed)
    tally = Counter()
    wins = 0
    for _ in range(rollouts):
        state = list(L)
        spice = s
        if spice == 'unknown':
            spice = rng.choice([i for i, l in enumerate(state) if l > 0])
        used = {}
        path = []
        for _step in range(4 * N_LETTERS * 5):
            if not any(state):
                wins += 1
                tally.update(path[-2:])
                break
            move = best_move(table, pool, state, spice, used)
            if move is None or move.q <= 0:
                break
            g = move.group
            k = used.get(g.sig, 0)
            used[g.sig] = k + 1
            path.append(g.words[k])
            state = [l - c for l, c in zip(state, g.sig)]
            landing = [i for i, l in enumerate(state) if l > 0 and i != spice]
            spice = rng.choice(landing) if landing else None
    return [w for w, _ in tally.most_common(top)], wins / rollouts if rollouts else 0.0


def impossible_reason(pool, L, letters, s='unknown'):
    """Why no ICE COLD clean plate is left, in the player's terms."""
    alive = [i for i, l in enumerate(L) if l > 0]
    if s is None and len(alive) == 1:
        last = letters[alive[0]].upper()
        return (f"Only {last} is left, with {L[alive[0]]} uses, and no word of just {last} "
                f"and the center spends exactly that many.")
    for i in alive:
        # Every word once: the most uses of this letter the words left can spend.
        capacity = sum(g.sig[i] * g.n for g in pool.groups)
        if not capacity:
            return (f"No word but the pangram can spend {letters[i].upper()}, so it can't be "
                    f"cleared without touching the spicy letter.")
        if capacity < L[i]:
            return (f"{letters[i].upper()} has {L[i]} uses left, but the words left can only "
                    f"spend {capacity} of them without the pangram.")
    finishers = {next(i for i, c in enumerate(g.sig) if c) for g in pool.groups if g.solo}
    if not finishers & set(alive):
        return ("No letter left has a solo word (just that letter and the center), and the "
                "last word of a clean ICE COLD game has to be one.")
    if s != 'unknown':
        return ("With the 🌶 where it is, no word keeps a clean ICE COLD finish possible.")
    return ("No order of words clears the board without touching the spicy letter, "
            "wherever the 🌶 lands.")


def _seed(L, s):
    return int(hashlib.sha1(repr((tuple(L), s)).encode()).hexdigest()[:8], 16)


def plan_payload(table, pool, L, s, letters, rollouts=24):
    """The /smush_dev plan for a solved table: the best word (with its try
    order) for the known spicy tile, or the best word for each tile it
    could be on; the chance of finishing; and the words worth saving."""
    alive = [i for i, l in enumerate(L) if l > 0]
    payload = {'mode': 'ice_cold', 'status': 'ready', 'next': [], 'by_spice': {},
               'finish_words': [], 'sim_win_rate': None, 'reason': None,
               'spice': None if s is None else ('unknown' if s == 'unknown' else letters[s])}
    if not alive:
        payload.update(status='done', p_success=1.0)
        return payload

    if s == 'unknown':
        chances = []
        for i in alive:
            moves = rank_moves(table, pool, L, i)
            chance = try_value(moves)
            chances.append(chance)
            if moves and moves[0].q > 0:
                payload['by_spice'][letters[i]] = dict(moves[0].entry(letters), p=round(chance, 3))
        p = sum(chances) / len(chances)
    else:
        moves = rank_moves(table, pool, L, s)
        p = try_value(moves)
        for m in moves:
            if m.q <= 0:
                break
            for k in range(m.group.n):
                if len(payload['next']) >= 3:
                    break
                payload['next'].append(m.entry(letters, k))
            if len(payload['next']) >= 3:
                break

    payload['p_success'] = round(p, 3)
    if p <= 0:
        payload['status'] = 'impossible'
        payload['reason'] = impossible_reason(pool, L, letters, s)
        return payload
    words, rate = finish_words(table, pool, L, s, _seed(L, s), rollouts)
    payload['finish_words'] = words
    payload['sim_win_rate'] = round(rate, 2)
    return payload


def states_below(L):
    return math.prod(l + 1 for l in L)


class TableCache:
    """The shared full-board table: one board at a time, built lazily in a
    background thread the first time a request needs it.

    One Smush board a day means one heavy build a day (James's call,
    2026-10-09). Nothing is persisted: a dyno restart just rebuilds on the
    next request. A newer fingerprint for the same board (a crowd correction,
    a newly played or flagged word) rebuilds it, but at most once per
    `rebuild_gap`, and the old table keeps answering meanwhile.
    """

    def __init__(self, solve=None, spawn=None, clock=time.monotonic, on_error=None,
                 max_builds_per_day=12, retry_after=600.0, rebuild_gap=900.0,
                 expected_seconds=45.0):
        self.solve = solve or solve_box
        self.spawn = spawn or self._spawn_thread
        self.clock = clock
        self.on_error = on_error
        self.max_builds_per_day = max_builds_per_day
        self.retry_after = retry_after
        self.rebuild_gap = rebuild_gap
        self.expected_seconds = expected_seconds
        self._lock = threading.Lock()
        self._entry = None           # (key, fingerprint, table, built_at)
        self._job = None             # (key, fingerprint, started_at)
        self._failed = {}            # key -> when it failed
        self._day = None
        self._builds = 0

    @staticmethod
    def _spawn_thread(target):
        threading.Thread(target=target, name='smush-ice-build', daemon=True).start()

    def _eta(self, now):
        return max(1, int(round(self.expected_seconds - (now - self._job[2]))))

    def _may_build(self):
        today = date.today()
        if self._day != today:
            self._day, self._builds = today, 0
        return self._builds < self.max_builds_per_day

    def get(self, key, fingerprint, make_pool):
        """-> (table or None, status, eta_seconds). Status is 'ready',
        'building', 'failed' (retrying soon) or 'capped' (no builds left
        today). make_pool() runs in the build thread."""
        with self._lock:
            now = self.clock()
            entry = self._entry
            if entry is not None and entry[0] == key:
                if (entry[1] != fingerprint and self._job is None
                        and now - entry[3] >= self.rebuild_gap and self._may_build()):
                    self._start(key, fingerprint, make_pool, now)
                return entry[2], 'ready', None
            if self._job is not None:
                # One build at a time; another board's has to finish first.
                return None, 'building', self._eta(now)
            failed_at = self._failed.get(key)
            if failed_at is not None and now - failed_at < self.retry_after:
                return None, 'failed', None
            if not self._may_build():
                return None, 'capped', None
            self._start(key, fingerprint, make_pool, now)
            return None, 'building', self._eta(now)

    def _start(self, key, fingerprint, make_pool, now):
        self._job = (key, fingerprint, now)
        self._builds += 1

        def run():
            started = self.clock()
            try:
                table = self.solve(FULL_ROOT, make_pool().groups)
            except Exception as e:   # noqa: BLE001 - a failed build must never kill the thread silently
                with self._lock:
                    self._failed[key] = self.clock()
                    self._job = None
                if self.on_error:
                    self.on_error(e)
                return
            with self._lock:
                self._entry = (key, fingerprint, table, self.clock())
                self._failed.pop(key, None)
                self._job = None
                self.expected_seconds = max(5.0, self.clock() - started)

        self.spawn(run)


class ExactCache:
    """The last few per-request tables, so tapping a different spicy tile or
    re-polling doesn't re-solve the same box."""

    def __init__(self, size=4):
        self.size = size
        self._lock = threading.Lock()
        self._tables = OrderedDict()

    def get(self, key, build):
        with self._lock:
            if key in self._tables:
                self._tables.move_to_end(key)
                return self._tables[key]
        table = build()
        with self._lock:
            self._tables[key] = table
            self._tables.move_to_end(key)
            while len(self._tables) > self.size:
                self._tables.popitem(last=False)
        return table
