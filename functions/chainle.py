"""Chainle solver engine. Pure functions over static data, no Flask or DB.

Chainle shows clue words and takes one guess per round. A bridge round (two
or three clues) wants one word that goes with every clue; the closest-meaning
round wants the word nearest in meaning to a single clue. A guess scores by
how close it is to each clue, and the weakest clue counts most:

    10 x (0.8 x geometric mean of the clue strengths + 0.2 x the weakest)

with each strength 0-100, from word vectors. The game's own reviewed answers
score the full 1,000.

Two signals, because each finds what the other misses:
  link     compound words and two-word phrases joining a candidate to a clue
           (SNOWBALL, FIRE STATION). Word vectors store a compound as a word
           of its own, so they rate SNOW and BALL as far apart.
  meaning  word vectors (GloVe), for answers related in meaning rather than
           through a compound.

Candidates rank by the points they are expected to score: a word linked to
every clue is likely one of the reviewed answers (the full 1,000), and
otherwise earns its closeness estimate. The constants below are starting
values; they get tuned against rounds logged on /chainle_admin.

The data (datasets/chainle_data.npz, built by scripts/build_chainle_data.py)
is a few flat numpy arrays, never a Python object per word, so it costs about
its file size in memory. pack() turns plain Python structures into those
arrays, for the build script and for tests.
"""

import numpy as np

# Rows per matmul block: one block's float32 copy is ~3 MB, where converting
# the whole int8 table at once would cost ~30 MB per request.
CHUNK = 8192
TOP = 10

# Ranking. A word linked to every clue counts as a likely reviewed answer
# with this chance: rising from LINK_P0 (its weakest link's weight) over
# LINK_P_SPAN, capped at LINK_P_MAX.
#
# The cap is low on purpose: a tie-breaker, not a deciding vote. Rounds
# logged on /chainle_admin (2026-10-14) showed the game's answers are mostly
# related in meaning, not compounds, and the words picked for their phrases
# scored worst: a phrase can join a word to a clue it's otherwise far from,
# and the game scores the weakest clue hardest.
LINK_P0 = 0.75
LINK_P_SPAN = 2.0
LINK_P_MAX = 0.25
# Each clue past the second: a word that compounds with all three is far
# less likely by chance than one that compounds with two.
LINK_EXTRA_CLUE = 0.75
# A WordNet synonym of the clue, at full closeness, is a likely reviewed
# answer in the closest-meaning round with this chance.
SYN_P = 0.7
# The game scores other closest-meaning answers against the clue and its
# reviewed alternatives. The clue's synonyms nearest to it (its main sense)
# stand in for those alternatives.
SYN_ANCHORS = 5
# The game scores broad words (thing, good, great) lower in bridge rounds.
BROAD_FACTOR = 0.6
# A word built on a clue, or inside one, is too close (see _too_close).
CONTAINS_MIN = 3

# flags bits
ANSWER = 1   # may be suggested
BROAD = 2    # an indefinite or broadly evaluative word
# pos bits (WordNet's parts of speech; 0 = unknown)
NOUN, VERB, ADJ, ADV = 1, 2, 4, 8
# link_form bits, read from the clue's side of the link
CLOSED = 1   # a closed compound (SNOWBALL), not a two-word phrase
FIRST = 2    # the clue is written first

FIELDS = ('vocab', 'zipf', 'flags', 'pos', 'vec', 'scale', 'base_ptr', 'base_ids',
          'link_ptr', 'link_ids', 'link_w', 'link_form',
          'syn_ptr', 'syn_ids', 'ant_ptr', 'ant_ids')


class Data:
    """The solver's word data: arrays named in FIELDS, indexed by word id.

    vocab is sorted, so a word's id is a binary search away, with no dict
    of 80k strings. Each *_ptr/*_ids pair is a CSR list: the entries for
    word i are ids[ptr[i]:ptr[i + 1]], sorted.
    """

    def __init__(self, **arrays):
        for name in FIELDS:
            setattr(self, name, arrays[name])
        self.size = len(self.vocab)
        # Which word owns each base_ids entry, to find every word that
        # shares a base with a clue in one pass.
        self.base_src = np.repeat(np.arange(self.size, dtype=np.int32), np.diff(self.base_ptr))

    @classmethod
    def from_file(cls, path):
        with np.load(path, allow_pickle=False) as z:
            return cls(**{name: z[name] for name in FIELDS})

    def id(self, word):
        """A word's id, or -1 if the solver doesn't know it."""
        key = word.strip().lower().encode('ascii', 'ignore')
        i = int(np.searchsorted(self.vocab, key))
        return i if i < self.size and self.vocab[i] == key else -1

    def word(self, i):
        return self.vocab[i].decode()

    def _row(self, ptr, ids, i):
        return ids[ptr[i]:ptr[i + 1]]

    def bases(self, i):
        """The word and its base forms (FEET -> FOOT, RUNNING -> RUN)."""
        return {int(i), *self._row(self.base_ptr, self.base_ids, i).tolist()}

    def synonyms(self, i):
        return self._row(self.syn_ptr, self.syn_ids, i)

    def antonyms(self, i):
        return self._row(self.ant_ptr, self.ant_ids, i)

    def link(self, clue, i):
        """(weight, form) of the link from a clue to word i, or None."""
        s, e = self.link_ptr[clue], self.link_ptr[clue + 1]
        j = s + int(np.searchsorted(self.link_ids[s:e], i))
        if j < e and self.link_ids[j] == i:
            return float(self.link_w[j]), int(self.link_form[j])
        return None


# --- building the arrays ------------------------------------------------------

def _csr(size, src, dst, *cols):
    """Sorted CSR arrays from parallel lists of (src, dst, extra columns)."""
    src = np.asarray(src, dtype=np.int32)
    dst = np.asarray(dst, dtype=np.int32)
    order = np.lexsort((dst, src))
    ptr = np.searchsorted(src[order], np.arange(size + 1)).astype(np.int32)
    return (ptr, dst[order]) + tuple(np.asarray(c)[order] for c in cols)


def _pairs(size, index, mapping):
    """CSR arrays for {word: words}, keeping known words, minus the word itself."""
    pairs = {(index[w], index[x]) for w, xs in (mapping or {}).items() if w in index
             for x in xs if x in index and x != w}
    src, dst = zip(*sorted(pairs)) if pairs else ((), ())
    return _csr(size, src, dst)


def pack(vectors, zipf=None, answer=None, broad=(), bases=None, links=(), synonyms=None,
         antonyms=None, pos=None):
    """The arrays Data needs, from plain Python structures.

    vectors   {word: sequence of floats}: every word the solver knows
    zipf      {word: Zipf frequency}
    answer    words that may be suggested (None = all)
    broad     words the game scores lower in bridge rounds
    bases     {word: its base forms}
    links     (a, b, weight, closed): "a b" or "ab" is a phrase, a written first
    synonyms, antonyms  {word: words}
    pos       {word: part-of-speech bits (NOUN | VERB | ADJ | ADV)}
    """
    vocab = sorted(vectors)
    index = {w: i for i, w in enumerate(vocab)}
    size = len(vocab)

    mat = np.array([vectors[w] for w in vocab], dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    mat /= np.where(norms > 0, norms, 1)
    # Unit vectors stored as int8 with one scale per word: a quarter of
    # float32, and cosines stay within ~0.005.
    scale = np.abs(mat).max(axis=1) / 127
    scale[scale == 0] = 1
    vec = np.round(mat / scale[:, None]).astype(np.int8)

    flags = np.zeros(size, dtype=np.uint8)
    for w, i in index.items():
        if answer is None or w in answer:
            flags[i] |= ANSWER
        if w in broad:
            flags[i] |= BROAD

    # Links are undirected: the strongest source for a pair wins, and each
    # pair is stored from both ends, its form read from that end.
    best = {}
    for a, b, weight, closed in links:
        if a in index and b in index and a != b:
            key = (min(index[a], index[b]), max(index[a], index[b]))
            if weight > best.get(key, (-1,))[0]:
                best[key] = (weight, index[a], bool(closed))
    src, dst, w, form = [], [], [], []
    for (i, j), (weight, first, closed) in best.items():
        for here, there in ((i, j), (j, i)):
            src.append(here)
            dst.append(there)
            w.append(weight)
            form.append((CLOSED if closed else 0) | (FIRST if here == first else 0))
    link_ptr, link_ids, link_w, link_form = _csr(size, src, dst, np.array(w, dtype=np.float16),
                                                 np.array(form, dtype=np.uint8))

    base_ptr, base_ids = _pairs(size, index, bases)
    syn_ptr, syn_ids = _pairs(size, index, synonyms)
    ant_ptr, ant_ids = _pairs(size, index, antonyms)
    return {
        'vocab': np.array([w.encode('ascii') for w in vocab], dtype=f'S{max(map(len, vocab), default=1)}'),
        'zipf': np.array([(zipf or {}).get(w, 0.0) for w in vocab], dtype=np.float16),
        'flags': flags, 'pos': np.array([(pos or {}).get(w, 0) for w in vocab], dtype=np.uint8),
        'vec': vec, 'scale': scale.astype(np.float32),
        'base_ptr': base_ptr, 'base_ids': base_ids,
        'link_ptr': link_ptr, 'link_ids': link_ids,
        'link_w': link_w.astype(np.float16), 'link_form': link_form.astype(np.uint8),
        'syn_ptr': syn_ptr, 'syn_ids': syn_ids, 'ant_ptr': ant_ptr, 'ant_ids': ant_ids,
    }


# --- scoring --------------------------------------------------------------------

def _unit(data, ids):
    """The listed words' unit vectors, as columns: (dims, len(ids))."""
    return data.vec[ids].astype(np.float32).T * data.scale[ids]


def _dot(data, cols):
    """Every word's dot product with each column: (cols.shape[1], size)."""
    out = np.empty((cols.shape[1], data.size), dtype=np.float32)
    for s in range(0, data.size, CHUNK):
        e = min(s + CHUNK, data.size)
        out[:, s:e] = (data.vec[s:e].astype(np.float32) @ cols).T * data.scale[s:e]
    return out


def _cosines(data, ids):
    """Each listed word's cosine to every word: (len(ids), size) float32."""
    return _dot(data, _unit(data, ids))


def _repeats(data, clue_ids):
    """Words that repeat a clue: the clue, its base forms, and every word
    sharing one of them (FEET for FOOT, LEAVES for LEAF)."""
    bases = set()
    for c in clue_ids:
        bases |= data.bases(c)
    mask = np.zeros(data.size, dtype=bool)
    mask[list(bases)] = True
    mask[data.base_src[np.isin(data.base_ids, list(bases))]] = True
    return mask


def _strengths(cos, allowed):
    """0-100 per clue: a word's cosine over the clue's best cosine to any
    allowed word. A stand-in for the game's per-clue adjustment, which it
    doesn't publish."""
    best = np.where(allowed, cos, -1.0).max(axis=1, keepdims=True)
    return 100 * np.clip(cos / np.maximum(best, 1e-3), 0, 1)


def estimate(strengths):
    """The game's points (0-1000) for clue strengths (one row per clue)."""
    gm = np.exp(np.log(np.maximum(strengths, 1.0)).mean(axis=0))
    return 10 * (0.8 * gm + 0.2 * strengths.min(axis=0))


def _bridge_scores(data, clue_ids):
    """(expected points, weakest link weight, allowed) for every word."""
    allowed = ((data.flags & ANSWER) != 0) & ~_repeats(data, clue_ids)
    sem = estimate(_strengths(_cosines(data, clue_ids), allowed))
    sem = np.where((data.flags & BROAD) != 0, sem * BROAD_FACTOR, sem)
    weights = np.zeros((len(clue_ids), data.size), dtype=np.float32)
    for j, c in enumerate(clue_ids):
        s, e = data.link_ptr[c], data.link_ptr[c + 1]
        weights[j, data.link_ids[s:e]] = data.link_w[s:e]
    link = weights.min(axis=0)
    extra = LINK_EXTRA_CLUE * (len(clue_ids) - 2)
    p = np.where(link > 0, np.clip((link - LINK_P0 + extra) / LINK_P_SPAN, 0, LINK_P_MAX), 0.0)
    return p * 1000 + (1 - p) * sem, link, allowed


def _closest_scores(data, clue_id):
    """(expected points, synonym mask, allowed) for every word."""
    allowed = ((data.flags & ANSWER) != 0) & ~_repeats(data, [clue_id])
    syn, ant = set(), set()
    for b in data.bases(clue_id):
        syn.update(data.synonyms(b).tolist())
        ant.update(data.antonyms(b).tolist())
    # The game scores an opposite at most 100.
    allowed[list(ant)] = False
    # A synonym shares a part of speech with the clue (HARD: not COME).
    if data.pos[clue_id]:
        allowed &= (data.pos == 0) | ((data.pos & data.pos[clue_id]) != 0)
    cos = _cosines(data, [clue_id])
    # Closeness to the clue alone favours words used alongside it (BIG ->
    # LITTLE, COMING); closeness to its synonyms' centre favours words that
    # mean the same.
    anchors = sorted((s for s in syn if cos[0, s] > 0), key=lambda s: -cos[0, s])[:SYN_ANCHORS]
    if anchors:
        centre = _unit(data, [clue_id] + anchors).mean(axis=1, keepdims=True)
        cos = np.vstack([cos, _dot(data, centre / (np.linalg.norm(centre) or 1))])
    strengths = _strengths(cos, allowed)
    is_syn = np.zeros(data.size, dtype=bool)
    is_syn[list(syn)] = True
    p = np.where(is_syn, SYN_P * strengths[0] / 100, 0.0)
    return p * 1000 + (1 - p) * estimate(strengths), is_syn, allowed


def _ranked(score, allowed, limit):
    """Ids of the best `limit` allowed words, best first."""
    score = np.where(allowed, score, -np.inf)
    limit = min(limit, int(allowed.sum()))
    if limit <= 0:
        return []
    idx = np.argpartition(-score, limit - 1)[:limit]
    return idx[np.argsort(-score[idx], kind='stable')].tolist()


def _same_answer(data, i, seen):
    """True if word i is an inflection of one already listed (DRINKS after
    DRINK): the game counts them as one answer. Records i's forms in `seen`."""
    forms = data.bases(i)
    if forms & seen:
        return True
    seen |= forms
    return False


def _shown(data, i, allowed):
    """The form to list word i as: its base when it's a regular inflection
    of an allowed word (WATERS -> WATER). The game counts them as one answer."""
    word, best = data.word(i), i
    for b in data.bases(i) - {i}:
        base = data.word(b)
        if (allowed[b] and len(base) >= 3 and word.startswith(base[:-1])
                and len(base) < len(data.word(best))):
            best = b
    return best


def _too_close(word, clues):
    """True if word is built on a clue or sits inside one (FOOTBALL for
    FOOT, PHOTOGRAPH for PHOTO, PHONE for TELEPHONE): the game turns those
    down as too close."""
    for c in clues:
        if word == c:
            continue
        long_, short = (word, c) if len(word) > len(c) else (c, word)
        if len(short) >= CONTAINS_MIN and (long_.startswith(short) or long_.endswith(short)):
            return True
    return False


def _phrase(clue, word, form):
    """The phrase a link makes, and the same with the answer hidden."""
    hidden = '_' * len(word)
    sep = '' if form & CLOSED else ' '
    if form & FIRST:
        return (clue + sep + word).upper(), (clue + sep + hidden).upper()
    return (word + sep + clue).upper(), (hidden + sep + clue).upper()


def bridge(data, clue_ids, top=TOP):
    """Best words joining every clue: [{word, type, score, phrases, patterns}].

    type is 'link' for a word that forms a compound or phrase with every
    clue, else 'meaning'. phrases/patterns hold, per clue, the phrase the
    word makes with it and the same phrase with the word hidden (None where
    there's no link).
    """
    score, link, allowed = _bridge_scores(data, clue_ids)
    clues = [data.word(c) for c in clue_ids]
    out, seen = [], set()
    for i in _ranked(score, allowed, top * 20):
        if _too_close(data.word(i), clues) or _same_answer(data, i, seen):
            continue
        # A linked word stays as linked (its phrases name it); a word that's
        # only close in meaning shows in its base form.
        j = i if link[i] > 0 else _shown(data, i, allowed)
        word = data.word(j)
        phrases, patterns = [], []
        for c, clue in zip(clue_ids, clues):
            found = data.link(c, j)
            shown, hidden = _phrase(clue, word, found[1]) if found else (None, None)
            phrases.append(shown)
            patterns.append(hidden)
        out.append({'word': word.upper(), 'type': 'link' if link[i] > 0 else 'meaning',
                    'score': float(score[i]), 'phrases': phrases, 'patterns': patterns})
        if len(out) >= top:
            break
    return out


def closest(data, clue_id, top=TOP):
    """Words nearest in meaning to one clue: [{word, type, score}], type
    'synonym' (WordNet) or 'meaning' (word vectors). Opposites are left out."""
    score, is_syn, allowed = _closest_scores(data, clue_id)
    clue = [data.word(clue_id)]
    out, seen = [], set()
    for i in _ranked(score, allowed, top * 3):
        if _too_close(data.word(i), clue) or _same_answer(data, i, seen):
            continue
        j = _shown(data, i, allowed)
        out.append({'word': data.word(j).upper(),
                    'type': 'synonym' if is_syn[i] or is_syn[j] else 'meaning',
                    'score': float(score[i]), 'phrases': [], 'patterns': []})
        if len(out) >= top:
            break
    return out


def solve(data, clues, kind, top=TOP):
    """What the page needs for one round.

    clues -- the clue words (two or three for 'bridge', one for 'meaning')
    Returns {'kind', 'clues', 'unknown', 'suggestions'}. Each suggestion
    carries 'strength', its score relative to the best (0-1): the scores
    aren't calibrated to the game's points yet, so the page shows them as
    bars, never as numbers.
    """
    ids = [data.id(c) for c in clues]
    result = {'kind': kind, 'clues': [c.upper() for c in clues],
              'unknown': [c.upper() for c, i in zip(clues, ids) if i < 0], 'suggestions': []}
    if result['unknown']:
        return result
    rows = bridge(data, ids, top) if kind == 'bridge' else closest(data, ids[0], top)
    best = rows[0]['score'] if rows else 0.0
    for r in rows:
        r['strength'] = round(max(r.pop('score'), 0.0) / best, 3) if best > 0 else 0.0
    result['suggestions'] = rows
    return result


def assess(data, clues, kind, words, limit=100):
    """How the solver rates a logged round, for /chainle_admin.

    Returns {'top': its first pick, 'ranks': {word: rank or None}} where a
    rank is the word's place in the solver's list (None past `limit`, or for
    a word it doesn't know). None when a clue is unknown.
    """
    ids = [data.id(c) for c in clues]
    if any(i < 0 for i in ids):
        return None
    rows = bridge(data, ids, limit) if kind == 'bridge' else closest(data, ids[0], limit)
    order = [r['word'] for r in rows]
    ranks = {}
    for w in words:
        if w:
            ranks[w.upper()] = order.index(w.upper()) + 1 if w.upper() in order else None
    return {'top': order[0] if order else None, 'ranks': ranks}
