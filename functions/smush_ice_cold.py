"""ICE COLD planner for Smush (hankgreen.com/smush), served on /smush for
today's boards and on /smush_dev (routes/smush_ice.py).

Smush's secret ICE COLD bonus x5s the whole final score (after Clean Plate's
+50 and the rest) for a game of at least five words in which no word ever
touched the spicy letter. Playing it well is a game against a random spice,
not a word-set puzzle, and this module plays it as one - for the most
expected points, with a clean plate counted at what it's worth.

Rules it relies on, read from the game's inline JS (2026-10-08):
  - pickSpice(): after every ACCEPTED word the spicy tile moves to a
    uniformly random living tile other than the current one, or to none when
    there is no such tile. A refused word spends nothing and moves nothing.
  - A word can't be played twice; only the gold center is free.
  - Word points are base x (1 + letters smushed flat); under ICE COLD there's
    no spicy bonus, and the pangram always touches spice.

So under ICE COLD the spicy tile is never spent and is always alive, and the
spice only goes null when that tile is the last one standing. A clean plate
can only end one way: a "closer" word kills every tile but the spicy one,
then, in the spice-free turn, a "solo" word (that letter plus the center
only) spends exactly its remaining uses. Solo words are scarce - usually two
to four letters a board, one or two copies each - and some boards can't be
cleaned ICE COLD at all (a Q that no word but the pangram can spend).

The model. A state is the remaining uses L (a count 0..5 per outer letter, in
sorted letter order) and the spicy tile s (a letter index, or None in the
null turn). From each state, under the best play:
  - J(L, s) is what the plan steers by: the points still to come before the
    x5 (every word's points, plus WORD_HOARD_PER_WORD a word), plus
    CLEAN_PLAN_BONUS on a clean finish;
  - P(L, s) is that same plan's chance of a clean finish, as the table sees it.
Neither is shown: the page's chance and points come from simulate(), the plan
played out against the player's own word list.
Where:
  - a move is a cost signature (uses of each letter) that avoids s and fits
    L; words sharing a signature are interchangeable, so they form a Group;
  - Smush may refuse a word, and a refusal is a free retry, so a state's
    value is a try list: its moves tried best-valued first, each only if
    Smush refused the ones before (acceptance priors below). The table keeps
    the three moves that would deliver most if tried first;
  - a group with n words is only planned at L when some letter i it uses has
    L[i] < (n + 1) * c[i]. Every use lowers that letter, so no path can use
    the group more than n times and no plan counts on replaying a word.
    Without it the table planned BIB, NTH and VIBE twice and the B board
    went from a 93% forecast to 4 wins in 10. It also delays some legal
    first uses (a board can be left with no such opening at all; then the
    best-scoring safe word is offered instead);
  - each use of a group needs a word of its own (Group.use_accept): draining
    G:4 with GIG and IGG needs both accepted, not the group's odds twice.
    Without it every learned game on the I board died on a refused IGG.

Offline, against the 11 boards embedded in the game page (spice moved as the
game moves it, the game's own lists refusing words, 8 first-player games a
board), the All 8 + Ice Cold mode tried on /smush on 2026-10-07 (one fixed
word set, spice-filtered; undone before it reached main) finished clean and
ICE COLD once in 110 games. Playing for points (James's call, 2026-10-09)
averages 206 points before the x5 (1,030 after) and finishes clean in 35 of
88 games; playing for the clean finish alone averages 190 (951) and
finishes clean in 43. Told exactly which words Smush takes, this planner
would average 218.

On 2026-11-01 (James's call) the plan began counting a clean finish at
CLEAN_PLAN_BONUS instead of its real 75. Played out on the live word lists
of the ten boards from 2026-10-23 to 11-01 (words taken at the odds below,
a random first spice, 1,000 games a board), it finishes clean in 63% of
games against 48%, for 2% fewer final points (1,079 against 1,105). The
2026-11-01 board went from no clean finishes at all to 14%.

J and P are solved exactly, bottom-up with numpy, over the box of every state
at or below a root L (mixed-radix indexing, one layer per total remaining
uses). The full board's box - 1.68M states x 9 spice columns - takes about a
minute (38-78 s locally, more on the dyno), so it's built once per board in a
background thread (TableCache) and kept compact (CompactTable); smaller boxes
are solved per request from the player's exact word pool. Tables nobody uses
are dropped (IdleSweeper), so their memory goes back.
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

# Imported under this name so callers and tests reach it as ice.release_memory.
from helpers import release_memory  # noqa: F401

N_LETTERS = 8
FULL_ROOT = (5,) * N_LETTERS
NULL = N_LETTERS                 # the tables' column for the spice-free turn

# A table nobody has used for this long is dropped. The full-board table only
# answers a game's first few words - the per-request solve takes over after
# that - so it goes about this long after the opening (James's call,
# 2026-10-10: one player, no need to keep a board's strategy after playing).
IDLE_RELEASE_SECONDS = 900

# A request solves the box below the player's own uses when it holds at most
# this many states. Measured 2026-10-09 (points and chance): 0.4 s at 9k
# states, 0.8 s at 19k, 1.2 s at 41k. Above it the shared full-board table
# answers. Confirm against the dyno.
ICE_EXACT_STATES = 25_000
# Boxes bigger than this are stored compactly (uint8, J with a per-table
# scale): the full board's two tables are 30 MB that way instead of 120.
COMPACT_STATES = 200_000

# Points before the x5 that a clean finish adds: Clean Plate +50, and the
# Unassisted +25 a stuck game loses by ending through "stuck?" (a hint).
CLEAN_BONUS = 75.0
# What the table and the move order count a clean finish as (James's call,
# 2026-11-01). The table underrates how often a clean line comes off - a
# turn ends after three refusals, a group's later uses get its worse words,
# and a state the n-uses rule blocks counts as a dead end though the plan
# plays on - so planning at the real 75 gave up clean finishes it could
# have had. On the 2026-11-01 board that plan finished clean in 0.3% of
# simulated games for 921 final; planning at 1,000, 17% for 1,020. Only the
# planning uses it: everything shown counts CLEAN_BONUS (simulate).
CLEAN_PLAN_BONUS = 1000.0
# Word Hoard is +25 for 13+ words. The table can't count words (that would
# multiply it by 14), so each word carries a share of it instead. Simulated:
# 2 a word scored 206 a game with 24 Word Hoards in 88 games; 0 scored 200
# with 15.
WORD_HOARD_PER_WORD = 2.0
# The bonus itself, as simulate() counts it: +25 at the end of a game of
# HOARD_WORDS words or more.
HOARD_WORDS = 13
HOARD_BONUS = 25.0

# Games simulate() plays for the chance and points the page shows: per
# request (about 250 ms at the opening, measured 2026-11-01; about +/-5
# points of chance), and for a board's opening, worked out once after its
# table is built (about 2 s per spicy tile).
SIM_ROLLOUTS = 64
SIM_OPENING_ROLLOUTS = 500

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
# this many expected points before the x5 (see _ranked). Simulated with it
# off: 204 a game against 206, so it costs nothing measurable.
NEAR_BEST_POINTS = 2.0
# Moves each state keeps for its try list while the table is built (see
# solve_box), and the compare-swaps that sort three of them by value.
KEEP_MOVES = 3
SORT_PAIRS = ((0, 1), (1, 2), (0, 1))


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
    bases: tuple                 # base score of each word, same order
    accept: float                # chance Smush takes at least one top word

    @property
    def n(self):
        return len(self.words)

    @property
    def solo(self):
        return sum(1 for c in self.sig if c) == 1

    @property
    def expected_base(self):
        """Base score of the word the player ends up playing, trying the top
        words in order until Smush takes one."""
        miss, num, took = 1.0, 0.0, 0.0
        for a, b in zip(self.accepts[:GROUP_ACCEPT_WORDS], self.bases):
            num += miss * a * b
            took += miss * a
            miss *= 1.0 - a
        return num / took if took else float(self.bases[0])

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
                            tuple(t[3] for t in rows), tuple(t[1] for t in rows), 1.0 - miss))
    return groups


class Pool:
    """A list of groups plus the arrays the vectorized move search needs."""

    def __init__(self, groups):
        self.groups = list(groups)
        self.SIG = np.array([g.sig for g in self.groups], dtype=np.int64).reshape(-1, N_LETTERS)
        self.NW = np.array([g.n for g in self.groups], dtype=np.int64)
        self.EB = np.array([g.expected_base for g in self.groups], dtype=np.float64)

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
    """For every state in the box [0, root] - one row each, one column per
    spicy letter plus NULL for the spice-free turn - the points still to come
    under the plan (J) and the plan's chance of a clean finish (P)."""

    def __init__(self, root, J, P, j_scale=1.0, p_scale=1.0):
        self.root = tuple(int(r) for r in root)
        self.R = np.array([r + 1 for r in self.root], dtype=np.int64)
        self.PW = _place_values(self.R)
        self.J, self.P = J, P
        self.j_scale, self.p_scale = j_scale, p_scale

    @property
    def nbytes(self):
        return self.J.nbytes + self.P.nbytes

    def covers(self, L):
        return all(0 <= l <= r for l, r in zip(L, self.root))

    def index(self, L):
        return int(np.dot(np.asarray(L, dtype=np.int64), self.PW))

    def points(self, L, s):
        return float(self.J[self.index(L), NULL if s is None else s]) * self.j_scale

    def chance(self, L, s):
        return float(self.P[self.index(L), NULL if s is None else s]) * self.p_scale

    def child_rows(self, child):
        """(J, P) of the states in `child` (one row of letter counts each), as
        float32 rows: a column per spicy tile, then the spice-free one."""
        idx = child @ self.PW
        return (self.J[idx].astype(np.float32) * self.j_scale,
                self.P[idx].astype(np.float32) * self.p_scale)


class CompactTable:
    """A compact Table holding only the entries the planner ever reads, for
    the full board, which stays in memory all day on /smush.

    A spent tile can't be spicy, so column s is kept only for the states
    where tile s is alive: it's stored over the box with that letter's axis
    one shorter (counts 1..root kept as 0..root-1). The spice-free column
    only means anything with one tile left, so it's kept just for those
    states, by [tile, uses]. Every other entry of the dense table is zero
    and never read, so every value, and so every plan, is the dense table's
    own: the full board takes 22 MB instead of 30 (measured 2026-10-31)."""

    def __init__(self, dense, chunk=1 << 14):
        self.root, self.R, self.PW = dense.root, dense.R, dense.PW
        self.j_scale, self.p_scale = dense.j_scale, dense.p_scale
        n = len(self.root)
        self.ONE = np.eye(n, dtype=np.int64)
        self.PWC, self.JC, self.PC = [], [], []
        for i in range(n):
            Ri = self.R.copy()
            Ri[i] -= 1
            self.PWC.append(_place_values(Ri))
            self.JC.append(np.zeros(int(np.prod(Ri)), dtype=dense.J.dtype))
            self.PC.append(np.zeros(int(np.prod(Ri)), dtype=dense.P.dtype))
        self.JN = np.zeros((n, int(self.R.max())), dtype=dense.J.dtype)
        self.PN = np.zeros((n, int(self.R.max())), dtype=dense.P.dtype)
        N = int(np.prod(self.R))
        for start in range(0, N, chunk):
            stop = min(N, start + chunk)
            D = (np.arange(start, stop, dtype=np.int64)[:, None] // self.PW) % self.R
            J, P = dense.J[start:stop], dense.P[start:stop]
            for i in range(n):
                live = D[:, i] > 0
                at = (D[live] - self.ONE[i]) @ self.PWC[i]
                self.JC[i][at] = J[live, i]
                self.PC[i][at] = P[live, i]
        for i in range(n):
            for v in range(1, self.root[i] + 1):
                self.JN[i, v] = dense.J[v * self.PW[i], NULL]
                self.PN[i, v] = dense.P[v * self.PW[i], NULL]

    @property
    def nbytes(self):
        return sum(a.nbytes for a in self.JC + self.PC) + self.JN.nbytes + self.PN.nbytes

    def covers(self, L):
        return all(0 <= l <= r for l, r in zip(L, self.root))

    def _value(self, cols, null, L, s):
        L = np.asarray(L, dtype=np.int64)
        if s is None:
            alive = np.nonzero(L > 0)[0]
            return float(null[alive[0], L[alive[0]]]) if len(alive) == 1 else 0.0
        if L[s] <= 0:
            return 0.0
        return float(cols[s][(L - self.ONE[s]) @ self.PWC[s]])

    def points(self, L, s):
        return self._value(self.JC, self.JN, L, s) * self.j_scale

    def chance(self, L, s):
        return self._value(self.PC, self.PN, L, s) * self.p_scale

    def child_rows(self, child):
        """As Table.child_rows, with zeros wherever nothing is kept - just as
        the dense table has there."""
        n = len(child)
        cj = np.zeros((n, N_LETTERS + 1), dtype=np.float32)
        cp = np.zeros((n, N_LETTERS + 1), dtype=np.float32)
        alive = child > 0
        for i in range(len(self.root)):
            live = alive[:, i]
            if live.any():
                at = (child[live] - self.ONE[i]) @ self.PWC[i]
                cj[live, i] = self.JC[i][at]
                cp[live, i] = self.PC[i][at]
        lone = np.nonzero(alive.sum(1) == 1)[0]
        if len(lone):
            tile = alive[lone].argmax(1)
            left = child[lone, tile]
            cj[lone, NULL] = self.JN[tile, left]
            cp[lone, NULL] = self.PN[tile, left]
        return cj * self.j_scale, cp * self.p_scale


def solve_box(root, groups, compact=None, chunk=1 << 14, dense=False, progress=None):
    """Solve J and P exactly for every state in the box [0, root].

    States are solved in order of total remaining uses, so every move's
    child is already known when its parent is reached. For each state and
    each spicy column, the moves are the groups that fit, avoid that letter
    and pass the n-uses rule. A move is worth its expected word points, plus
    the Word Hoard share, plus the child's J averaged over where the spice
    lands (CLEAN_PLAN_BONUS if it clears the board). The state keeps the
    KEEP_MOVES moves that would deliver most if tried first (acceptance x
    value) - keeping the highest values instead let rare long shots crowd out
    the likely word and halve a state's worth - and its J is their try list,
    best-valued first; its P follows the same moves.

    A compact box (the full board) builds J in float16 and P straight in
    uint8 - P is only shown, never steers - and ends with J in uint8 too,
    kept as a CompactTable (or the dense uint8 Table, with `dense`, to check
    the compact one against).

    `progress`, if given, hears the share of the work done after each layer,
    for the build's ETA. A layer's work is its states times its total uses
    left (deeper states have more moves that fit): on the full board that
    share tracks the build's time to within about 10% from the first few
    seconds (measured 2026-10-31; states times the moves that fit ran 30-40%
    short).
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
    # Letter counts are 0..5, so the per-state comparisons run on int8; a
    # window bound past 127 is the same as no bound at all.
    SIG8 = SIG.astype(np.int8)
    LIMIT = np.minimum((np.array([g.n for g in fit], dtype=np.int64)[:, None] + 1) * SIG,
                       127).astype(np.int8)
    EB = np.array([g.expected_base for g in fit], dtype=np.float32)
    # Acceptance of one use of each group, by how many uses it has left (j):
    # ACCJ[g][j], for j up to the most any path can make (J).
    J_MAX = [min(g.n, uses_left(g.sig, root)) for g in fit]
    ACCJ = [np.array([0.0] + [g.use_accept(j, Jg) for j in range(1, Jg + 1)], np.float32)
            for g, Jg in zip(fit, J_MAX)]
    hoard = float(WORD_HOARD_PER_WORD)

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
    tot = None
    work = np.cumsum([(bounds[t + 1] - bounds[t]) * t for t in range(total + 1)], dtype=np.float64)

    VJ = np.zeros((N, N_LETTERS + 1), dtype=np.float16 if compact else np.float32)
    VP = np.zeros((N, N_LETTERS + 1), dtype=np.uint8 if compact else np.float32)
    p_read = 1.0 / 255 if compact else 1.0
    sumj = np.zeros(N, dtype=np.float32)   # J and P summed over the living tiles' columns
    sump = np.zeros(N, dtype=np.float32)
    for t in range(1, total + 1):
        layer = order[bounds[t]:bounds[t + 1]]
        moves = np.nonzero(CTOT <= t)[0]
        for a in range(0, len(layer), chunk):
            S = layer[a:a + chunk].astype(np.int64)
            D = ((S[:, None] // PW) % R).astype(np.int8)
            most = D.max(0)
            alive = D > 0
            lone = alive_count[S] == 1
            n = len(S)
            # The moves kept for each state and spicy column: KEEP_MOVES
            # slots of [value, acceptance, chance].
            kept = [[np.zeros((n, N_LETTERS + 1), np.float32) for _ in range(3)]
                    for _ in range(KEEP_MOVES)]
            for g in moves:
                if (SIG8[g] > most).any():
                    continue                   # fits no state in this chunk
                ok = (D >= SIG8[g]).all(1) & ((D < LIMIT[g]) & POS[g]).any(1)
                rows = np.nonzero(ok)[0]
                if not len(rows):
                    continue
                Dr = D[rows]
                child = S[rows] - CIDX[g]
                cn = alive_count[child].astype(np.float32)
                cj = VJ[child].astype(np.float32)
                cp = VP[child].astype(np.float32) * p_read
                csj, csp = sumj[child], sump[child]
                # Spicy column s: the spice lands on one of the child's other
                # living tiles; if s is the only one left, the turn is spice-free.
                with np.errstate(divide='ignore', invalid='ignore'):
                    qj = np.where(cn[:, None] >= 2,
                                  (csj[:, None] - cj[:, :N_LETTERS]) / (cn[:, None] - 1),
                                  cj[:, NULL:NULL + 1])
                    qp = np.where(cn[:, None] >= 2,
                                  (csp[:, None] - cp[:, :N_LETTERS]) / (cn[:, None] - 1),
                                  cp[:, NULL:NULL + 1])
                # ... only where s is alive and the move doesn't touch it.
                valid = alive[rows] & ~POS[g][None, :]
                # The spice-free turn (one tile left): from no spice, it lands
                # on any living tile - or the board is clear.
                qjn = np.where(child == 0, CLEAN_PLAN_BONUS, csj / np.maximum(cn, 1.0))
                qpn = np.where(child == 0, 1.0, csp / np.maximum(cn, 1.0))
                smushed = ((Dr == SIG8[g]) & POS[g]).sum(1)
                word = EB[g] * (1 + smushed) + hoard
                vj = np.concatenate([np.where(valid, word[:, None] + qj, -1.0),
                                     np.where(lone[rows], word + qjn, -1.0)[:, None]],
                                    axis=1).astype(np.float32)
                vp = np.concatenate([np.where(valid, qp, 0.0),
                                     np.where(lone[rows], qpn, 0.0)[:, None]],
                                    axis=1).astype(np.float32)
                pos = POS[g]
                left = (Dr[:, pos] // SIG8[g][pos]).min(1)
                acc = ACCJ[g][np.clip(left, 1, J_MAX[g])][:, None]

                # Keep the moves that would deliver most if tried first
                # (acceptance x value), not the highest values: rare long
                # shots on top would otherwise crowd out the word Smush almost
                # surely takes, and halve the state's worth.
                worth = acc * vj
                # Most moves can't beat a state's weakest kept move once its
                # slots fill up: only touch the rows where this one gets in.
                enters = (worth > kept[-1][0][rows] * kept[-1][1][rows]).any(1)
                if not enters.any():
                    continue
                rows, worth, vj, vp, acc = (x[enters] for x in (rows, worth, vj, vp, acc))
                new = (vj, np.broadcast_to(acc, vj.shape), vp)
                old = [[x[rows] for x in slot] for slot in kept]
                placed = np.zeros(vj.shape, dtype=bool)
                for k in range(KEEP_MOVES):
                    here = ~placed & (worth > old[k][0] * old[k][1])
                    for f in range(3):
                        # the new move lands here; anything it passed shifts down one
                        below = old[k - 1][f] if k else new[f]
                        kept[k][f][rows] = np.where(here, new[f], np.where(placed, below, old[k][f]))
                    placed |= here
            # Try the kept moves in order of value: sort the slots, then the
            # try list - each only if Smush refused the ones before.
            for i, j in SORT_PAIRS:
                swap = kept[j][0] > kept[i][0]
                for f in range(3):
                    hi = np.where(swap, kept[j][f], kept[i][f])
                    kept[j][f] = np.where(swap, kept[i][f], kept[j][f])
                    kept[i][f] = hi
            js = np.zeros((n, N_LETTERS + 1), np.float32)
            ps = np.zeros((n, N_LETTERS + 1), np.float32)
            miss = np.ones((n, N_LETTERS + 1), np.float32)
            for v, a_, p in kept:
                js += miss * a_ * v
                ps += miss * a_ * p
                miss *= 1.0 - a_
            VJ[S] = js
            VP[S] = np.rint(np.clip(ps, 0.0, 1.0) * 255) if compact else ps
            sumj[S] = (js[:, :N_LETTERS] * alive).sum(1)
            sump[S] = (ps[:, :N_LETTERS] * alive).sum(1)
        if progress:
            progress(float(work[t] / work[-1]) if work[-1] else 1.0)
    sumj = sump = order = None

    if not compact:
        return Table(root, VJ, VP)
    j_max = float(VJ.max(initial=0))
    j_scale = j_max / 255 if j_max > 0 else 1.0
    Jq = np.empty(VJ.shape, dtype=np.uint8)
    for start in range(0, N, chunk):
        Jq[start:start + chunk] = np.rint(
            np.clip(VJ[start:start + chunk].astype(np.float32), 0.0, j_max) / j_scale)
    VJ = None                  # 30 MB for the full board, gone before the compact copy
    table = Table(root, Jq, VP, j_scale, p_read)
    return table if dense else CompactTable(table, chunk)


@dataclass
class Move:
    group: Group
    value: float                 # expected points (before x5) from here, playing this
    chance: float                # the plan's chance of a clean finish after it
    smushes: int                 # letters it spends to zero
    accept: float                # chance Smush takes this use of the group

    def entry(self, letters, k=0):
        """The k-th word of this move, shaped like a /smush result row so the
        page's ✓ Played can spend it."""
        word = self.group.words[k]
        base = self.group.bases[k]
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
            'q': round(self.chance, 3),
            'value': round(self.value, 1),
        }


def _child_values(table, L, s, SIG):
    """(J, P) of the state each move (a row of SIG) leaves from (L, s),
    averaged over where the spice lands next."""
    child = np.asarray(L, dtype=np.int64)[None, :] - SIG
    cj, cp = table.child_rows(child)
    alive = child > 0
    landing = alive.copy()
    if s is not None:
        landing[:, s] = False
    n = landing.sum(1)
    qj = np.where(n > 0, (cj[:, :N_LETTERS] * landing).sum(1) / np.maximum(n, 1), cj[:, NULL])
    qp = np.where(n > 0, (cp[:, :N_LETTERS] * landing).sum(1) / np.maximum(n, 1), cp[:, NULL])
    clear = ~alive.any(1)
    return np.where(clear, CLEAN_PLAN_BONUS, qj), np.where(clear, 1.0, qp)


def legal_moves(pool, L, s, used=None, window=True):
    """Indexes of the pool's groups that fit L and avoid s, counting `used`
    {sig: uses so far} against each group's words - and, unless `window` is
    off, pass the n-uses rule."""
    if not pool.groups:
        return np.zeros(0, dtype=np.int64)
    Lv = np.asarray(L, dtype=np.int64)
    nw = pool.NW
    if used:
        nw = nw - np.array([used.get(g.sig, 0) for g in pool.groups], dtype=np.int64)
    ok = (pool.SIG <= Lv).all(1) & (nw > 0)
    if window:
        ok &= ((Lv < (nw[:, None] + 1) * pool.SIG) & (pool.SIG > 0)).any(1)
    if s is not None:
        ok &= pool.SIG[:, s] == 0
    return np.nonzero(ok)[0]


def move_accept(group, L, root):
    """Group.use_accept for a use of `group` at L, in a table rooted at `root`
    (the most uses any path from there can make)."""
    J = min(group.n, uses_left(group.sig, root))
    return group.use_accept(max(1, min(uses_left(group.sig, L), J)), J)


def _ranked(table, pool, L, s, used=None):
    """Every legal move from (L, s), best first, as parallel arrays (pool
    indexes, expected points, chance after, smushes, acceptance).

    A refusal is a free retry, so trying moves in order of expected points is
    optimal - but trying a long shot first rarely buys much. Moving move i to
    the front of that order costs a_i * sum over the moves k before it of
    (chance Smush takes k) * (v_k - v_i). So the move offered first is the
    most likely to be accepted among those that cost at most NEAR_BEST_POINTS
    to put first (then the most valuable) - James asked for common words, and
    every refusal costs the player a try. The rest follow by expected points,
    acceptance, word points; ties keep the pool's own (signature) order, so
    it's stable.

    The cost has to count every move ahead of i, not just the top two: with
    two long shots on top, comparing against those alone let any word pass,
    and the "most likely" one played EYETEETH (96 points to come) over HEAVY
    (265) and threw away a 97% clean finish."""
    idx = legal_moves(pool, L, s, used)
    if not len(idx):
        empty = np.zeros(0)
        return idx, empty, empty, np.zeros(0, dtype=np.int64), empty
    SIG = pool.SIG[idx]
    qj, qp = _child_values(table, L, s, SIG)
    smushes = ((SIG == np.asarray(L, dtype=np.int64)[None, :]) & (SIG > 0)).sum(1)
    pts = pool.EB[idx] * (1 + smushes)
    value = pts + WORD_HOARD_PER_WORD + qj
    acc = np.array([move_accept(pool.groups[gi], L, table.root) for gi in idx])
    order = np.lexsort((-pts, -acc, -value))
    idx, value, qp, smushes, acc, pts = (x[order] for x in (idx, value, qp, smushes, acc, pts))
    if len(idx) > 1:
        taken = acc * np.concatenate([[1.0], np.cumprod(1.0 - acc)[:-1]])
        ahead = np.cumsum(taken) - taken                      # sum of taken_k, k < i
        ahead_value = np.cumsum(taken * value) - taken * value
        cost = acc * (ahead_value - value * ahead)
        fine = np.nonzero(cost <= NEAR_BEST_POINTS)[0]
        pick = fine[np.lexsort((-value[fine], -acc[fine]))[0]]
        if pick:
            order = np.concatenate([[pick], np.delete(np.arange(len(idx)), pick)])
            idx, value, qp, smushes, acc = (x[order] for x in (idx, value, qp, smushes, acc))
    return idx, value, qp, smushes, acc


def rank_moves(table, pool, L, s, used=None):
    """Every legal ICE COLD move from (L, s), best first (see _ranked)."""
    idx, value, qp, smushes, acc = _ranked(table, pool, L, s, used)
    return [Move(pool.groups[gi], float(value[k]), float(qp[k]), int(smushes[k]), float(acc[k]))
            for k, gi in enumerate(idx)]


def best_move(table, pool, L, s, used=None):
    """Just the top of rank_moves, or None."""
    idx, value, qp, smushes, acc = _ranked(table, pool, L, s, used)
    if not len(idx):
        return None
    return Move(pool.groups[idx[0]], float(value[0]), float(qp[0]), int(smushes[0]), float(acc[0]))


def plan_value(moves):
    """(expected points, chance of a clean finish) when the moves are tried
    in this order, each only if Smush refused every word of the ones before."""
    points = chance = 0.0
    miss = 1.0
    for m in moves:
        points += miss * m.accept * m.value
        chance += miss * m.accept * m.chance
        miss *= 1.0 - m.accept
        if miss < 1e-4:
            break
    return points, chance


def fallback_moves(pool, L, s, used=None):
    """Every safe move from (L, s), ignoring the n-uses rule, by the points
    it scores now. Only for when the plan has no move at all (a board where
    every opening word would be a group's first of several uses, or a lost
    endgame): the table can't value what comes after - it could count on
    playing the same word again - so these promise no clean finish."""
    idx = legal_moves(pool, L, s, used, window=False)
    if not len(idx):
        return []
    SIG = pool.SIG[idx]
    smushes = ((SIG == np.asarray(L, dtype=np.int64)[None, :]) & (SIG > 0)).sum(1)
    pts = pool.EB[idx] * (1 + smushes)
    acc = np.array([pool.groups[gi].accept for gi in idx])
    order = np.lexsort((-acc, -pts))
    return [Move(pool.groups[idx[k]], float(pts[k] + WORD_HOARD_PER_WORD), 0.0,
                 int(smushes[k]), float(acc[k])) for k in order]


def moves_from(table, pool, L, s):
    """The plan's moves from (L, s), or the fallback when it has none."""
    return rank_moves(table, pool, L, s) or fallback_moves(pool, L, s)


@dataclass(frozen=True)
class Simulated:
    """What simulate() saw: the share of games that finished clean, the
    points still to come before the x5, and the words the clean games ended
    on, most often first."""
    chance: float
    points: float
    finish_words: tuple
    games: int


def simulate(table, pool, L, s, words_played=0, rollouts=SIM_ROLLOUTS, seed=0, top=3):
    """Play the plan out `rollouts` times from (L, s), the way a player
    following the page would, and average what they get.

    Each turn plays the plan's word (best_move, or the best-scoring safe word
    when the plan has none), and Smush takes it at its odds. A refused word
    is spent - the player ✗s it - and the plan goes again from the same
    position; a taken one scores base x (1 + letters smushed flat) and moves
    the spice to a random other living tile, as the game does. A game ends
    clean (+CLEAN_BONUS), or when no safe word is left. Word Hoard's +25
    counts once the game's words, `words_played` included, reach HOARD_WORDS.

    The table only chooses the moves. Its own J and P misjudge some boards
    in both directions (a turn ends after three refusals, the n-uses dead
    ends, and words removed since it was built), so what the page shows comes
    from here, against the player's current word list."""
    rng = random.Random(seed)
    tally = Counter()
    wins = 0
    total = 0.0
    for _ in range(rollouts):
        state = list(L)
        spice = s
        if spice == 'unknown':
            alive = [i for i, l in enumerate(state) if l > 0]
            spice = rng.choice(alive) if alive else None
        used = {}
        path = []
        points = 0.0
        for _step in range(4 * N_LETTERS * 5):
            if not any(state):
                break
            move = best_move(table, pool, state, spice, used)
            if move is None:
                fallback = fallback_moves(pool, state, spice, used)
                if not fallback:
                    break
                move = fallback[0]
            g = move.group
            k = used.get(g.sig, 0)
            used[g.sig] = k + 1
            if rng.random() >= g.accepts[k]:
                continue
            points += g.bases[k] * (1 + sum(1 for c, l in zip(g.sig, state) if c and c == l))
            path.append(g.words[k])
            state = [l - c for l, c in zip(state, g.sig)]
            landing = [i for i, l in enumerate(state) if l > 0 and i != spice]
            spice = rng.choice(landing) if landing else None
        if not any(state):
            wins += 1
            points += CLEAN_BONUS
            tally.update(path[-2:])
        if words_played + len(path) >= HOARD_WORDS:
            points += HOARD_BONUS
        total += points
    n = max(rollouts, 1)
    return Simulated(wins / n, total / n, tuple(w for w, _ in tally.most_common(top)), rollouts)


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


def plan_payload(table, pool, L, s, letters, words_played=0, sim=None):
    """The plan for a solved table: the best word (with its try order) for
    the known spicy tile, or the best word for each tile it could be on; the
    chance of a clean finish and the points still to come (before the x5);
    and the words worth saving for the end.

    The words come from the table; the chance, the points and the words to
    save come from simulate() (or `sim`, worked out ahead for a board's
    opening), so they hold for the player's own, current word list. A reason
    for no clean finish is only given when the table finds none either and
    no simulated game got one.

    status is 'ready' whenever there's a word to play - even with no clean
    finish left, the points still count x5 - 'impossible' when no word
    avoids the spice at all, and 'done' when the board is clear."""
    alive = [i for i, l in enumerate(L) if l > 0]
    payload = {'mode': 'ice_cold', 'status': 'ready', 'next': [], 'by_spice': {},
               'finish_words': [], 'reason': None, 'expected_points': None,
               'p_success': None, 'sim_games': 0,
               'spice': None if s is None else ('unknown' if s == 'unknown' else letters[s])}
    if not alive:
        payload.update(status='done', p_success=1.0, expected_points=CLEAN_BONUS + (
            HOARD_BONUS if words_played >= HOARD_WORDS else 0.0))
        return payload

    if s == 'unknown':
        chance = 0.0
        for i in alive:
            moves = moves_from(table, pool, L, i)
            chance = max(chance, plan_value(moves)[1])
            if moves:
                payload['by_spice'][letters[i]] = moves[0].entry(letters)
        playable = bool(payload['by_spice'])
    else:
        moves = moves_from(table, pool, L, s)
        chance = plan_value(moves)[1]
        for m in moves:
            for k in range(m.group.n):
                if len(payload['next']) >= 3:
                    break
                payload['next'].append(m.entry(letters, k))
            if len(payload['next']) >= 3:
                break
        playable = bool(moves)

    if sim is None:
        sim = simulate(table, pool, L, s, words_played, SIM_ROLLOUTS, _seed(L, s))
    payload.update(p_success=round(sim.chance, 3), expected_points=round(sim.points, 1),
                   finish_words=list(sim.finish_words), sim_games=sim.games)
    if not playable:
        payload['status'] = 'impossible'
    if chance <= 0 and sim.chance <= 0:
        payload['reason'] = impossible_reason(pool, L, letters, s)
    return payload


def states_below(L):
    return math.prod(l + 1 for l in L)


class TableCache:
    """Full-board tables, built in a background thread, one build at a time.

    /smush_dev's cache (the defaults) holds one board: whatever board was
    last asked for, built the first time a request needs it. A newer
    fingerprint for that board (a crowd correction, a newly played or
    flagged word) rebuilds it, at most once per `rebuild_gap`, and the old
    table keeps answering meanwhile. A table no request has used for
    `idle_seconds` goes (release_idle, IdleSweeper).

    /smush's (routes/smush_ice.py) holds today's boards, up to `max_tables`
    of them: each built the moment its board is decided (prewarm), never
    rebuilt (`rebuild_gap` None), and dropped when a newer board needs the
    room - before that board's build starts, so a build never runs on top of
    a full cache - or after a long idle. Every read of a held table's board
    (prewarm) counts as a use, so only a board nobody reads any more idles
    out. A board waiting for the build in
    progress, or for memory (`may_start`, asked again every `wait_retry`
    seconds after a no), starts as soon as it can. When memory runs short
    (`must_shed`), the least recently used table goes, one each sweep, so
    the other goes only if the shortage lasts.

    Nothing is persisted: a dyno restart builds again.
    """

    def __init__(self, solve=None, spawn=None, clock=time.monotonic, on_error=None,
                 max_builds_per_day=12, retry_after=600.0, rebuild_gap=900.0,
                 expected_seconds=60.0, idle_seconds=IDLE_RELEASE_SECONDS,
                 max_tables=1, may_start=None, wait_retry=300.0, on_built=None,
                 must_shed=None, today=date.today, after_built=None):
        self.solve = solve or solve_box
        self.spawn = spawn or self._spawn_thread
        self.clock = clock
        self.on_error = on_error
        self.max_builds_per_day = max_builds_per_day
        self.retry_after = retry_after
        self.rebuild_gap = rebuild_gap
        self.expected_seconds = expected_seconds
        self.idle_seconds = idle_seconds
        self.max_tables = max_tables
        self.may_start = may_start
        self.wait_retry = wait_retry
        self.on_built = on_built
        # (key, table, pool) once a table is in, still in the build thread:
        # work that needs the table but mustn't hold up the requests.
        self.after_built = after_built
        self.must_shed = must_shed
        self.today = today
        self._lock = threading.Lock()
        # key -> [fingerprint, table, built_at, used_at], least recently used first
        self._tables = OrderedDict()
        self._job = None             # (key, fingerprint, started_at)
        self._progress = 0.0         # the share of its work the job has done
        self._queue = OrderedDict()  # key -> (fingerprint, make_pool): builds waiting their turn
        self._held_back = None       # when may_start last said no
        self._failed = {}            # key -> when it failed
        self._day = None
        self._builds = 0

    @staticmethod
    def _spawn_thread(target):
        threading.Thread(target=target, name='smush-ice-build', daemon=True).start()

    def _eta(self, now):
        """Seconds the build in progress has left: from its own progress once
        it's a few percent in, which holds on any machine; before that, from
        the last build's time. None once that has run out with nothing else
        to go on."""
        elapsed = now - self._job[2]
        if self._progress >= 0.03:
            return max(1, int(round(elapsed * (1 - self._progress) / self._progress)))
        left = self.expected_seconds - elapsed
        return max(1, int(round(left))) if left >= 1 else None

    def _may_build(self):
        today = self.today()
        if self._day != today:
            self._day, self._builds = today, 0
        return self._builds < self.max_builds_per_day

    def _building(self, key):
        return self._job is not None and self._job[0] == key

    def get(self, key, fingerprint, make_pool):
        """-> (table or None, status, eta_seconds). Status is 'ready',
        'building' (eta None while it waits its turn), 'failed' (retrying
        soon) or 'capped' (no builds left today). make_pool() runs in the
        build thread."""
        with self._lock:
            now = self.clock()
            held = self._tables.get(key)
            if held is None:
                return (None,) + self._ask(key, fingerprint, make_pool, now)
            held[3] = now
            self._tables.move_to_end(key)
            if (self.rebuild_gap is not None and held[0] != fingerprint
                    and now - held[2] >= self.rebuild_gap and not self._building(key)
                    and key not in self._queue and self._may_build()):
                self._queue[key] = (fingerprint, make_pool)
                self._next(now)
            return held[1], 'ready', None

    def prewarm(self, key, fingerprint, make_pool):
        """Queue a board's build before anyone asks for it. -> its status
        ('ready' when it's already held). A held table counts as used: its
        board is still being read, so the idle rule leaves it be."""
        with self._lock:
            now = self.clock()
            held = self._tables.get(key)
            if held is not None:
                held[3] = now
                self._tables.move_to_end(key)
                return 'ready'
            return self._ask(key, fingerprint, make_pool, now)[0]

    def _ask(self, key, fingerprint, make_pool, now):
        """(status, eta) for a board with no table yet, queueing its build
        unless it failed lately or today's builds are used up."""
        if not self._building(key) and key not in self._queue:
            failed_at = self._failed.get(key)
            if failed_at is not None and now - failed_at < self.retry_after:
                return 'failed', None
            if not self._may_build():
                return 'capped', None
            self._queue[key] = (fingerprint, make_pool)
            self._next(now)
        return 'building', self._eta(now) if self._building(key) else None

    def _next(self, now):
        """Start the first queued build, if none is running and may_start
        allows it. Called with the lock held."""
        if self._job is not None or not self._queue:
            return
        if not self._may_build():
            self._queue.clear()
            return
        if self._held_back is not None and now - self._held_back < self.wait_retry:
            return
        key, (fingerprint, make_pool) = next(iter(self._queue.items()))
        if key not in self._tables and self._tables and len(self._tables) >= self.max_tables:
            # Room first, then the memory check: the least recently used
            # table (yesterday's board, by then) mustn't keep a new board
            # waiting for memory it holds itself.
            while self._tables and len(self._tables) >= self.max_tables:
                self._tables.popitem(last=False)
            release_memory()
        if self.may_start is not None and not self.may_start():
            self._held_back = now
            return
        self._held_back = None
        del self._queue[key]
        self._start(key, fingerprint, make_pool, now)

    def _start(self, key, fingerprint, make_pool, now):
        self._job = (key, fingerprint, now)
        self._progress = 0.0
        self._builds += 1

        def heard(share):
            self._progress = share       # one float: _eta reads it under the lock

        def run():
            started = self.clock()
            try:
                pool = make_pool()
                table = self.solve(FULL_ROOT, pool.groups, progress=heard)
            except Exception as e:   # noqa: BLE001 - a failed build must never kill the thread silently
                with self._lock:
                    self._failed[key] = self.clock()
                    self._job = None
                    self._next(self.clock())
                if self.on_error:
                    self.on_error(e)
                return
            with self._lock:
                done = self.clock()
                self._tables[key] = [fingerprint, table, done, done]
                self._tables.move_to_end(key)
                self._failed.pop(key, None)
                self._job = None
                self.expected_seconds = max(5.0, done - started)
            # The build's scratch arrays (~70 MB), and any table this one replaced.
            release_memory()
            if self.on_built:
                self.on_built(key, done - started)
            if self.after_built:
                try:
                    self.after_built(key, table, pool)
                except Exception as e:  # noqa: BLE001 - the table is in; this is extra
                    if self.on_error:
                        self.on_error(e)
            with self._lock:
                self._next(self.clock())

        self.spawn(run)

    def has(self, key):
        """Whether this board's table is held right now."""
        with self._lock:
            return key in self._tables

    def holding(self):
        """Whether there's a table, or one on the way."""
        with self._lock:
            return bool(self._tables) or self._job is not None or bool(self._queue)

    def release_idle(self):
        """Drop the tables no request has used for idle_seconds (a build in
        progress carries on); while memory is short (must_shed), the least
        recently used table too - one a sweep; then start a build that was
        waiting for memory. The sweeper calls this every minute. -> whether
        any table went."""
        with self._lock:
            now = self.clock()
            gone = [] if self.idle_seconds is None else [
                key for key, held in self._tables.items() if now - held[3] >= self.idle_seconds]
            for key in gone:
                del self._tables[key]
            if self._tables and self.must_shed is not None and self.must_shed():
                gone.append(self._tables.popitem(last=False)[0])
            self._next(now)
        return bool(gone)


class ExactCache:
    """The last few per-request tables, so tapping a different spicy tile or
    re-polling doesn't re-solve the same box. All go once none has been used
    for `idle_seconds`."""

    def __init__(self, size=4, clock=time.monotonic, idle_seconds=IDLE_RELEASE_SECONDS):
        self.size = size
        self.clock = clock
        self.idle_seconds = idle_seconds
        self._lock = threading.Lock()
        self._tables = OrderedDict()
        self._used_at = None

    def get(self, key, build):
        with self._lock:
            self._used_at = self.clock()
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

    def holding(self):
        with self._lock:
            return bool(self._tables)

    def release_idle(self):
        """Drop every table if none has been used for idle_seconds.
        -> whether they went."""
        with self._lock:
            if not self._tables or self.clock() - self._used_at < self.idle_seconds:
                return False
            self._tables.clear()
        return True


class IdleSweeper:
    """Wakes every `every` seconds while any of its caches holds a table (or
    is building one), drops the tables nobody has used lately, and stops once
    they're all empty - so memory goes back without anyone pressing a button.

    Call poke() whenever a request has touched the caches; it starts the loop
    if it isn't running. `caches` is a function returning them, so it always
    sweeps the current ones (tests swap them out)."""

    def __init__(self, caches, every=60.0, start=None, sleep=time.sleep):
        self.caches = caches
        self.every = every
        self.start = start or self._start_thread
        self.sleep = sleep
        self._lock = threading.Lock()
        self._running = False

    @staticmethod
    def _start_thread(target):
        threading.Thread(target=target, name='smush-ice-sweep', daemon=True).start()

    def poke(self):
        with self._lock:
            if self._running:
                return
            self._running = True
        self.start(self._loop)

    def sweep(self):
        """One pass. -> whether anything was dropped."""
        dropped = [cache.release_idle() for cache in self.caches()]
        if any(dropped):
            release_memory()
        return any(dropped)

    def _loop(self):
        while True:
            self.sleep(self.every)
            self.sweep()
            with self._lock:
                # Checked under the lock poke() takes, so a table stored
                # just now either keeps this loop going or starts a new one.
                if not any(cache.holding() for cache in self.caches()):
                    self._running = False
                    return
