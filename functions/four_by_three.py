"""4x3, Hank Green's daily word puzzle: which word is the hub and which pairs
go with it, ranked, with how sure each one is.

A board is nine words. One of them, the hub, is in all four categories; each
of the other eight is in exactly one ("Four categories. Three words each.
One word is in all four."). So an answer is the hub and four pairs, and a
board has only 945 of them: 9 hubs times 105 ways to pair the other 8.
Every one is scored, so there is no search to cut short.

A group (the hub h with the words a and b) is described by FEATURES:
  hub_cos, pair_cos          Numberbatch cosines: h with a plus h with b, and
                             a with b
  hub_syn, pair_syn          WordNet synonyms (any sense): how many of h-a and
                             h-b are, and whether a-b is
  hub_family, pair_family    the same for WordNet families (a main sense's
                             parent and its other kinds)
  partner                    a hidden partner: a word X with "h X", "a X" and
                             "b X" all known pairs (___ BALL), or "X h", "X a"
                             and "X b" (BALL ___), scored by how high X sits in
                             each word's own partner list, so a specific X
                             beats one that follows everything
An answer's features are its four groups' added up, its score is weights .
features, and P(answer) is proportional to exp(score). The weights' size is
the confidence: nothing here divides by a temperature.

The data is the app's own, already in memory for /chainle and /circuits:
functions/chainle.py Data (Numberbatch vectors, WordNet synonyms and
families) and functions/circuits.py Data (directed compound pairs). No game
data, no network, no LLM.

Marks are what the game said about a player's guess, in the order they made
them (MARK_VERDICTS). Groups known from elsewhere (James's answer, or groups
enough players confirmed) and groups players say the game refused come from
the route. solve() keeps only the answers that fit all of them.

fit() tunes the weights from James's approved answers: each approved board
is one example, his answer the right one of its 945 (a conditional logit).
"""

import hashlib
import itertools
import re

import numpy as np

TILES = 9
GROUPS = 4
# The game allows a word up to 40 characters (its own puzzle schema).
MAX_TILE_LEN = 40
# What a tile may hold once upper-cased: letters, digits, spaces and a few
# marks. A tile also needs a letter or digit.
_TILE_CHARS = re.compile(r"[A-Z0-9 '&.\-]+")
# The words in a tile, for the word data.
_PARTS = re.compile(r"[a-z]+")

FEATURES = ('hub_cos', 'pair_cos', 'hub_syn', 'pair_syn', 'hub_family', 'pair_family', 'partner')
NF = len(FEATURES)
# The weights before enough answers are in to tune them (see fit): set with
# scripts/backtest_four_by_three.py on made-up boards, and cautious on
# purpose: James's real 11-20 board scored two wrong groups high with
# sharper ones.
DEFAULT_WEIGHTS = (10.0, 10.0, 3.0, 3.0, 2.0, 2.0, 5.0)

# A group's label from its share of the answers that fit (see solve).
SURE_P = 0.9
LIKELY_P = 0.6
# Help's "Narrow it down": the likeliest hubs, always this many (a hint, so
# even when the solver is sure of one; the page lists them alphabetically).
HUB_CANDIDATES = 3
# An unsure group's alternatives: other pairs for one of its words, at most
# this many, each with at least this share.
ALTERNATIVES = 3
ALTERNATIVE_MIN = 0.02

# Hidden partners: each tile keeps its PARTNERS_KEPT strongest partners on
# each side; X's score in a word's list is 1 / (1 + rank / PARTNER_RANK_SCALE).
PARTNERS_KEPT = 300
PARTNER_RANK_SCALE = 5.0
# A group's best partner shows as its theme ("___ BALL") from this score up:
# X is within about the top 7 partners of all three words.
THEME_MIN = 0.4

# What a player can say the game did with a guess, in the page's words:
#   right      4x3 took the three words: a group
#   one_away   "One away!": some group not yet solved holds two of them
#   not_close  "Not even close": no group not yet solved holds two of them
#   hub        one word: the player called it the hub, and it was
# 'no' (a group players say the game refused, with no more said) is the
# route's, never a player's.
MARK_VERDICTS = ('right', 'one_away', 'not_close', 'hub')
# Labels, in the order the page lists groups.
LABELS = ('checked', 'players', 'yours', 'left', 'sure', 'likely', 'guess')


class BoardError(ValueError):
    pass


# --- the 945 answers -------------------------------------------------------------

def _matchings(items):
    """Every way to split `items` (an even-length tuple) into pairs."""
    if not items:
        yield ()
        return
    first = items[0]
    for k in range(1, len(items)):
        rest = items[1:k] + items[k + 1:]
        for m in _matchings(rest):
            yield ((first, items[k]),) + m


# The 252 groups: (hub, a, b) by tile position, a < b, neither the hub.
GROUP_LIST = tuple((h, a, b) for h in range(TILES)
                   for a, b in itertools.combinations([i for i in range(TILES) if i != h], 2))
GROUP_INDEX = {g: n for n, g in enumerate(GROUP_LIST)}
# Each group's tiles as a 9-bit mask (the hub's bit too).
GROUP_MASKS = np.array([(1 << h) | (1 << a) | (1 << b) for h, a, b in GROUP_LIST], dtype=np.int64)
_ANSWERS = [(h, tuple(GROUP_INDEX[(h,) + p] for p in m))
            for h in range(TILES) for m in _matchings(tuple(i for i in range(TILES) if i != h))]
ANSWER_HUB = np.array([h for h, _g in _ANSWERS], dtype=np.int64)
ANSWER_GROUPS = np.array([g for _h, g in _ANSWERS], dtype=np.int64)     # (945, 4)
ANSWER_MASKS = GROUP_MASKS[ANSWER_GROUPS]                                  # (945, 4)
_ANSWER_BY_GROUPS = {tuple(sorted(g)): n for n, (_h, g) in enumerate(_ANSWERS)}
# Set bits in each 9-bit number: how many of a guess's tiles a group holds.
_POP = np.array([bin(n).count('1') for n in range(1 << TILES)], dtype=np.int64)
ANSWERS = len(_ANSWERS)


def mask_of(positions):
    out = 0
    for p in positions:
        out |= 1 << p
    return out


def answer_index(hub, pairs):
    """The answer with this hub (a position) and these four pairs (positions),
    or None if they aren't one."""
    try:
        groups = tuple(sorted(GROUP_INDEX[(hub,) + tuple(sorted(p))] for p in pairs))
    except (KeyError, TypeError):
        return None
    return _ANSWER_BY_GROUPS.get(groups)


# --- the board ---------------------------------------------------------------------

def clean_tile(text):
    """A tile as the game shows it: upper case, spaces collapsed. '' if it
    can't be one."""
    if not isinstance(text, str):
        return ''
    tile = ' '.join(text.upper().split())
    if (not tile or len(tile) > MAX_TILE_LEN or not _TILE_CHARS.fullmatch(tile)
            or not any(c.isalnum() for c in tile)):
        return ''
    return tile


def parse_board(tiles):
    """The nine tiles (clean_tile), in the order given, or BoardError."""
    if not isinstance(tiles, (list, tuple)) or len(tiles) != TILES:
        raise BoardError('a board is 9 words')
    out = []
    for n, text in enumerate(tiles):
        tile = clean_tile(text)
        if not tile:
            raise BoardError(f'word {n + 1} must be 1 to {MAX_TILE_LEN} letters, digits or spaces')
        out.append(tile)
    if len(set(out)) != TILES:
        raise BoardError('each word can only be on the board once')
    return tuple(out)


def board_key(tiles):
    """What makes two boards the same: their nine words, in any order (the
    game shuffles every player's board)."""
    return hashlib.sha1('|'.join(sorted(tiles)).encode()).hexdigest()


# --- what the data says about a board ---------------------------------------------

def _tile_parts(tile):
    """The words in a tile, lower case. None for a tile with a digit in it:
    the word data has no numbers, and its letters alone (the X of 4X4)
    would stand for some other word."""
    if any(c.isdigit() for c in tile):
        return []
    return _PARTS.findall(tile.lower())


def _word_ids(words, tile):
    """The word data's ids for a tile: the tile itself, or for a word the
    data lacks, its singular (PEARS -> PEAR); for a tile of several words,
    each word it knows."""
    parts = _tile_parts(tile)
    if len(parts) == 1:
        word = parts[0]
        i = words.id(word)
        if i < 0:
            for end, base in (('ies', 'y'), ('es', ''), ('s', '')):
                if word.endswith(end) and len(word) > len(end) + 2:
                    i = words.id(word[:-len(end)] + base)
                    if i >= 0:
                        break
        return [i] if i >= 0 else []
    return [i for i in (words.id(p) for p in parts) if i >= 0]


def _unit_vector(words, ids):
    """The mean of the words' unit vectors, at unit length, or None."""
    if not ids:
        return None
    vec = words.vec[ids].astype(np.float64) * np.asarray(words.scale[ids], dtype=np.float64)[:, None]
    mean = vec.mean(axis=0)
    norm = np.linalg.norm(mean)
    return mean / norm if norm > 0 else None


def _ranks(pairs, ids, weights):
    """{partner id: rank} for one side of a tile: its PARTNERS_KEPT strongest
    partners that could be a category's shared word (the data's answer
    flag drops fragments like ING)."""
    ids = np.asarray(ids)
    weights = np.asarray(weights, dtype=np.float64)
    flags = getattr(pairs, 'flags', None)
    if flags is not None and len(ids):
        keep = (np.asarray(flags)[ids] & 1) > 0
        ids, weights = ids[keep], weights[keep]
    order = np.argsort(-weights, kind='stable')[:PARTNERS_KEPT]
    return {int(ids[j]): r for r, j in enumerate(order)}


def _partner_sides(pairs, tile):
    """(after, before): the {partner id: rank} of words that can follow the
    tile and of words that can come before it. The tile as a whole if the
    data knows it (ICE CREAM), else its last word for what follows and its
    first for what comes before."""
    parts = _tile_parts(tile)
    if pairs is None or not parts:
        return {}, {}
    unit = pairs.id(tile)
    last = unit if unit >= 0 else (pairs.id(parts[-1]) if parts else -1)
    first = unit if unit >= 0 else (pairs.id(parts[0]) if parts else -1)
    after = _ranks(pairs, *pairs.follows(last)) if last >= 0 else {}
    before = _ranks(pairs, *pairs.precedes(first)) if first >= 0 else {}
    return after, before


def _best_partner(sides):
    """(score, partner id, after) for three tiles' sides (see _partner_sides):
    the shared partner whose worst rank among them is best."""
    best = (0.0, -1, True)
    for k, after in ((0, True), (1, False)):
        lists = [s[k] for s in sides]
        common = set(lists[0]).intersection(lists[1], lists[2])
        for x in common:
            score = min(1.0 / (1.0 + lst[x] / PARTNER_RANK_SCALE) for lst in lists)
            if score > best[0]:
                best = (score, x, after)
    return best


class Signals:
    """One board's 252 groups: FEATURES (feats, a (252, NF) array), the best
    hidden partner of each group's three tiles (partner_of), and which tiles
    the word data knows (known)."""

    def __init__(self, tiles, feats, partner_of, known):
        self.tiles = tuple(tiles)
        self.feats = feats
        self.partner_of = partner_of      # group index -> (score, word, after)
        self.known = known

    @classmethod
    def build(cls, tiles, words, pairs=None):
        """tiles: the nine (parse_board). words: functions/chainle.py Data.
        pairs: functions/circuits.py Data, or None (no partners then)."""
        ids = [_word_ids(words, t) for t in tiles]
        vecs = [_unit_vector(words, i) for i in ids]
        known = [v is not None for v in vecs]
        cos = np.zeros((TILES, TILES))
        for x, y in itertools.combinations(range(TILES), 2):
            if vecs[x] is not None and vecs[y] is not None:
                cos[x, y] = cos[y, x] = float(vecs[x] @ vecs[y])
        # Synonyms and families only for a tile that is one word the data knows.
        single = [i[0] if len(i) == 1 and len(_tile_parts(t)) == 1 else -1
                  for i, t in zip(ids, tiles)]
        syn = [set(words.synonyms(i).tolist()) if i >= 0 else set() for i in single]
        fam = [set(words.related(i).tolist()) if i >= 0 else set() for i in single]
        same_syn = np.zeros((TILES, TILES))
        same_fam = np.zeros((TILES, TILES))
        for x, y in itertools.combinations(range(TILES), 2):
            if single[x] >= 0 and single[y] >= 0:
                same_syn[x, y] = same_syn[y, x] = float(single[y] in syn[x] or single[x] in syn[y])
                same_fam[x, y] = same_fam[y, x] = float(single[y] in fam[x] or single[x] in fam[y])

        sides = [_partner_sides(pairs, t) for t in tiles]
        by_set = {}
        for trio in itertools.combinations(range(TILES), 3):
            score, x, after = _best_partner([sides[i] for i in trio])
            by_set[trio] = (score, pairs.word(x) if x >= 0 else '', after)

        feats = np.zeros((len(GROUP_LIST), NF))
        partner_of = {}
        for n, (h, a, b) in enumerate(GROUP_LIST):
            partner = by_set[tuple(sorted((h, a, b)))]
            partner_of[n] = partner
            feats[n] = (cos[h, a] + cos[h, b], cos[a, b],
                        same_syn[h, a] + same_syn[h, b], same_syn[a, b],
                        same_fam[h, a] + same_fam[h, b], same_fam[a, b],
                        partner[0])
        return cls(tiles, feats, partner_of, known)

    def answer_features(self):
        """(945, NF): each answer's four groups' features, added up."""
        return self.feats[ANSWER_GROUPS].sum(axis=1)


# --- what fits the marks ------------------------------------------------------------

def _is_group(mask):
    return (ANSWER_MASKS == mask).any(axis=1)


def _unsolved_holding(mask, solved, at_least):
    """Each answer: does a group of it, not among `solved` (masks), hold at
    least `at_least` of the mask's tiles?"""
    holds = _POP[ANSWER_MASKS & mask] >= at_least
    if solved:
        holds &= ~np.isin(ANSWER_MASKS, list(solved))
    return holds.any(axis=1)


def _fits(kind, mask, solved=()):
    """Which of the 945 answers a constraint allows."""
    if kind == 'group':
        return _is_group(mask)
    if kind == 'no':
        return ~_is_group(mask)
    if kind == 'hub':
        return ANSWER_HUB == mask.bit_length() - 1
    if kind == 'one_away':
        # Not a group, so no group holds all three: "two" is exactly two.
        return ~_is_group(mask) & _unsolved_holding(mask, solved, 2)
    if kind == 'not_close':
        return ~_is_group(mask) & ~_unsolved_holding(mask, solved, 2)
    raise ValueError(kind)


def _label(p):
    return 'sure' if p >= SURE_P else 'likely' if p >= LIKELY_P else 'guess'


def solve(signals, weights, marks=(), fixed=None, refused=(), names=None):
    """The likeliest answer for a board, with how sure each part is.

    signals  Signals for the board.
    weights  NF numbers (the tuned ones, or DEFAULT_WEIGHTS).
    marks    the player's, in the order made: (positions, verdict), with
             three positions for a group verdict and one for 'hub'.
    fixed    {mask of a group's three positions: label}: groups known from
             elsewhere, 'checked' (James's answer) or 'players' (confirmed by
             enough players).
    refused  masks of groups players say the game refused.
    names    {mask: category name}: James's names, shown as themes.

    Constraints apply in order: James's groups, the player's marks, then the
    players' groups and refusals. One that leaves no answer is dropped, so a
    mark that contradicts the ones before it is ignored and reported in
    'dropped' (its index in marks). Returns a dict for the page (see the
    end of this function); positions are the board's, words as tiles.
    """
    fixed = dict(fixed or {})
    names = dict(names or {})
    tiles = signals.tiles
    alive = np.ones(ANSWERS, dtype=bool)
    dropped = []

    def apply(kind, mask, solved=()):
        nonlocal alive
        allowed = alive & _fits(kind, mask, solved)
        if allowed.any():
            alive = allowed
            return True
        return False

    checked = {m for m, label in fixed.items() if label == 'checked'}
    for mask in checked:
        apply('group', mask)
    yours, solved = set(), set()
    for n, (positions, verdict) in enumerate(marks):
        mask = mask_of(positions)
        if verdict == 'right':
            ok = apply('group', mask)
            if ok:
                yours.add(mask)
                solved.add(mask)
        elif verdict == 'hub':
            ok = apply('hub', mask)
        else:
            ok = apply(verdict, mask, frozenset(solved))
        if not ok:
            dropped.append(n)
    players = set()
    for mask, label in fixed.items():
        if label != 'checked' and apply('group', mask):
            players.add(mask)
    for mask in refused:
        apply('no', mask)

    w = np.asarray(weights, dtype=np.float64)
    scores = signals.answer_features() @ w
    s = np.where(alive, scores, -np.inf)
    p = np.exp(s - s.max())
    p /= p.sum()
    hub_p = np.bincount(ANSWER_HUB, weights=p, minlength=TILES)
    group_p = np.bincount(ANSWER_GROUPS.ravel(), weights=np.repeat(p, GROUPS), minlength=len(GROUP_LIST))

    top = int(np.argmax(p))
    hub = int(ANSWER_HUB[top])
    groups = [int(g) for g in ANSWER_GROUPS[top]]
    known = checked | players | yours
    hub_forced = bool(np.all(ANSWER_HUB[alive] == hub))

    out_groups = []
    for g in groups:
        h, a, b = GROUP_LIST[g]
        mask = int(GROUP_MASKS[g])
        if mask in checked:
            label = 'checked'
        elif mask in yours:
            label = 'yours'
        elif mask in players:
            label = 'players'
        elif all(int(GROUP_MASKS[o]) in known for o in groups if o != g):
            label = 'left'
        else:
            label = _label(group_p[g])
        theme = names.get(mask) or None
        # A partner names the theme only for a group the solver stands behind:
        # on a guess it is as likely to be a coincidence (BLACK sand...).
        if theme is None and label != 'guess':
            score, word, after = signals.partner_of[g]
            if score >= THEME_MIN and word:
                theme = ('___ ' + word.upper()) if after else (word.upper() + ' ___')
        alternatives = []
        if label in ('sure', 'likely', 'guess'):
            rivals = [o for o, (oh, oa, ob) in enumerate(GROUP_LIST)
                      if oh == h and o != g and len({oa, ob} & {a, b}) == 1 and group_p[o] >= ALTERNATIVE_MIN]
            rivals.sort(key=lambda o: -group_p[o])
            alternatives = [{'pair': [tiles[GROUP_LIST[o][1]], tiles[GROUP_LIST[o][2]]],
                             'p': round(float(group_p[o]), 3)} for o in rivals[:ALTERNATIVES]]
        out_groups.append({'tiles': [tiles[h], tiles[a], tiles[b]], 'pair': [tiles[a], tiles[b]],
                           'p': round(float(group_p[g]), 3), 'label': label, 'theme': theme,
                           'alternatives': alternatives})
    out_groups.sort(key=lambda x: (LABELS.index(x['label']), -x['p']))

    if hub_forced and checked:
        hub_label = 'checked'
    elif hub_forced and (yours or any(verdict == 'hub' for n, (_pos, verdict) in enumerate(marks)
                                      if n not in dropped)):
        hub_label = 'yours'
    elif hub_forced and players:
        hub_label = 'players'
    else:
        hub_label = _label(hub_p[hub])
    order = sorted(range(TILES), key=lambda i: -hub_p[i])
    candidates = [[tiles[i], round(float(hub_p[i]), 3)] for i in order[:HUB_CANDIDATES]]
    return {
        'tiles': list(tiles),
        'known': list(signals.known),
        'hub': {'tile': tiles[hub], 'p': round(float(hub_p[hub]), 3), 'label': hub_label,
                'candidates': candidates},
        'groups': out_groups,
        'answer_p': round(float(p[top]), 3),
        'answers_left': int(alive.sum()),
        'dropped': dropped,
    }


def first_pass(signals, weights):
    """The solver's own answer with no marks and nothing known: hub and
    groups with their labels, compact, as the admin's Accuracy tab keeps it."""
    result = solve(signals, weights)
    return {'hub': result['hub']['tile'], 'hub_label': result['hub']['label'],
            'groups': [[g['pair'][0], g['pair'][1], g['label']] for g in result['groups']]}


# --- tuning from James's answers ------------------------------------------------------

# How hard fit() pulls toward the prior weights: about this many boards' worth
# of evidence (see scripts/backtest_four_by_three.py).
PRIOR_STRENGTH = 2.0
FIT_STEPS = 40


def _objective(w, examples, prior, strength):
    total = 0.5 * strength * float(np.sum((w - prior) ** 2))
    for feats, right in examples:
        s = feats @ w
        m = s.max()
        total += m + float(np.log(np.exp(s - m).sum())) - s[right]
    return total


def fit(examples, prior=DEFAULT_WEIGHTS, strength=PRIOR_STRENGTH, steps=FIT_STEPS):
    """The weights that make the right answers most likely, pulled toward
    `prior` (a conditional logit with an L2 pull, solved by Newton steps;
    weights stay at 0 or above). examples: [(answer features (945, NF),
    index of the right answer)]. Never worse than `prior` on its own
    objective."""
    prior = np.asarray(prior, dtype=np.float64)
    w = prior.copy()
    if not examples:
        return w
    f = _objective(w, examples, prior, strength)
    for _ in range(steps):
        grad = strength * (w - prior)
        hess = strength * np.eye(NF)
        for feats, right in examples:
            s = feats @ w
            p = np.exp(s - s.max())
            p /= p.sum()
            mean = p @ feats
            grad += mean - feats[right]
            centered = feats - mean
            hess += (centered * p[:, None]).T @ centered
        step = np.linalg.solve(hess, grad)
        size = 1.0
        while size > 1e-4:
            candidate = np.maximum(w - size * step, 0.0)
            fc = _objective(candidate, examples, prior, strength)
            if fc < f - 1e-12:
                break
            size *= 0.5
        else:
            break
        done = f - fc < 1e-9
        w, f = candidate, fc
        if done:
            break
    return w


def weights_code(weights):
    """A short code for a set of weights, part of the route's solver_version."""
    return hashlib.sha1(np.asarray(weights, dtype=np.float64).round(6).tobytes()).hexdigest()[:8]
