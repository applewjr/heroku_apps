"""Chainle solver engine. Pure functions over static data, no Flask or DB.

Chainle shows clue words and takes one guess per round. A bridge round (two
or three clues) wants one word that goes with every clue; the closest-meaning
round wants the word nearest in meaning to a single clue. A guess scores by
how close it is to each clue, and the weakest clue counts most:

    10 x (0.8 x geometric mean of the clue strengths + 0.2 x the weakest)

with each strength 0-100, from word vectors. The game's own reviewed answers
score the full 1,000.

Closeness is the cosine of two words' ConceptNet Numberbatch vectors. They
blend how words are used with a network of what words mean, so a word sits
close to its synonyms and its parts (LEG: KNEE) as well as to the words used
alongside it, and they match the closeness Chainle shows after a guess.

Candidates rank by the points they are expected to score. In the
closest-meaning round, a WordNet synonym of the clue also gets a chance of
being a reviewed answer. Compound words and two-word phrases joining a word
to a clue (SNOWBALL, FIRE STATION) never move the ranking, since the game
scores closeness alone: they only label a word, for the page's hints. The
constants below get tuned against rounds logged on /chainle_admin.

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

# Ranking. In a bridge round a word's strength with each clue rises with
# their closeness, from 0 at CLOSE_ZERO to full at CLOSE_FULL and above. The
# game scores a clue in full once a guess is about as close to it as the
# reviewed answer is, and answers mostly sit at 0.2-0.4 with each clue.
# Saturating a little above that ranked the answer first in 78 of 88 bridge
# rounds logged on /chainle_admin (2026-10-15), and anywhere from 0.30 to
# 0.45 did nearly as well: lower ties too many words at the top, and higher
# lets a word very close to one clue make up for being far from another.
CLOSE_ZERO = -0.08
CLOSE_FULL = 0.34
# A WordNet synonym of the clue's main senses, at full closeness, is a
# likely reviewed answer in the closest-meaning round with this chance; a
# word from a main sense's family (DOG: WOLF, FOX) less so.
SYN_P = 0.7
FAMILY_P = 0.4
# The game scores broad words (thing, good, great) lower in bridge rounds.
BROAD_FACTOR = 0.6
# The game's answers are common words: almost all logged on /chainle_admin
# had a wordfreq Zipf of 4.0 or more, so the solver suggests only those,
# except the clue's synonyms in the closest-meaning round, which the game
# accepts however rare. Zipf is stored as float16, hence the small allowance.
MIN_ANSWER_ZIPF = 3.995
# A word built on a clue, or inside one, is too close (see _too_close).
CONTAINS_MIN = 3
# A word close only in meaning is listed in its base form (WATERS: WATER)
# when the base scores within this share of the form the solver ranked.
# Otherwise it keeps its own form: a base often scores far lower (SAYING:
# SAY), and the inflection rules can name another word (MATTER: MATT). On the
# rounds logged on /chainle_admin (2026-10-15), a base listed in a top 10
# scored a median 35% below the form it stood for.
SHOWN_MARGIN = 0.03

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
          'syn_ptr', 'syn_ids', 'rel_ptr', 'rel_ids', 'ant_ptr', 'ant_ids')


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

    def related(self, i):
        """Its main senses' family: their parent and its other kinds."""
        return self._row(self.rel_ptr, self.rel_ids, i)

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
         antonyms=None, pos=None, related=None):
    """The arrays Data needs, from plain Python structures.

    vectors   {word: sequence of floats}: every word the solver knows
    zipf      {word: Zipf frequency}
    answer    words that may be suggested (None = all)
    broad     words the game scores lower in bridge rounds
    bases     {word: its base forms}
    links     (a, b, weight, closed): "a b" or "ab" is a phrase, a written first
    synonyms, antonyms  {word: words}
    pos       {word: part-of-speech bits (NOUN | VERB | ADJ | ADV)}
    related   {word: its family: words of the same kind (DOG: WOLF, FOX)}
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
    rel_ptr, rel_ids = _pairs(size, index, related)
    ant_ptr, ant_ids = _pairs(size, index, antonyms)
    return {
        'vocab': np.array([w.encode('ascii') for w in vocab], dtype=f'S{max(map(len, vocab), default=1)}'),
        'zipf': np.array([(zipf or {}).get(w, 0.0) for w in vocab], dtype=np.float16),
        'flags': flags, 'pos': np.array([(pos or {}).get(w, 0) for w in vocab], dtype=np.uint8),
        'vec': vec, 'scale': scale.astype(np.float32),
        'base_ptr': base_ptr, 'base_ids': base_ids,
        'link_ptr': link_ptr, 'link_ids': link_ids,
        'link_w': link_w.astype(np.float16), 'link_form': link_form.astype(np.uint8),
        'syn_ptr': syn_ptr, 'syn_ids': syn_ids, 'rel_ptr': rel_ptr, 'rel_ids': rel_ids,
        'ant_ptr': ant_ptr, 'ant_ids': ant_ids,
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
    """0-100 per clue in the closest-meaning round: a word's cosine over the
    clue's best cosine to any allowed word, so the nearest word scores in
    full."""
    best = np.where(allowed, cos, -1.0).max(axis=1, keepdims=True)
    return 100 * np.clip(cos / np.maximum(best, 1e-3), 0, 1)


def estimate(strengths):
    """The game's points (0-1000) for clue strengths (one row per clue)."""
    gm = np.exp(np.log(np.maximum(strengths, 1.0)).mean(axis=0))
    return 10 * (0.8 * gm + 0.2 * strengths.min(axis=0))


def _candidates(data, clue_ids, spare=()):
    """Words the solver may suggest for these clues: common ones, plus any
    in `spare` however rare."""
    common = data.zipf >= MIN_ANSWER_ZIPF
    common[list(spare)] = True
    return ((data.flags & ANSWER) != 0) & common & ~_repeats(data, clue_ids)


def _bridge_scores(data, clue_ids):
    """(expected points, weakest link weight, allowed) for every word. The
    link weight only labels a word (see bridge)."""
    allowed = _candidates(data, clue_ids)
    cos = _cosines(data, clue_ids)
    score = estimate(100 * np.clip((cos - CLOSE_ZERO) / (CLOSE_FULL - CLOSE_ZERO), 0, 1))
    score = np.where((data.flags & BROAD) != 0, score * BROAD_FACTOR, score)
    weights = np.zeros((len(clue_ids), data.size), dtype=np.float32)
    for j, c in enumerate(clue_ids):
        s, e = data.link_ptr[c], data.link_ptr[c + 1]
        weights[j, data.link_ids[s:e]] = data.link_w[s:e]
    return score, weights.min(axis=0), allowed


def _closest_scores(data, clue_id):
    """(expected points, synonym mask, family mask, allowed) for every word."""
    syn, fam, ant = set(), set(), set()
    for b in data.bases(clue_id):
        syn.update(data.synonyms(b).tolist())
        fam.update(data.related(b).tolist())
        ant.update(data.antonyms(b).tolist())
    fam -= syn
    allowed = _candidates(data, [clue_id], spare=syn)
    # The game scores an opposite at most 100.
    allowed[list(ant)] = False
    # A synonym shares a part of speech with the clue (HARD: not COME).
    if data.pos[clue_id]:
        allowed &= (data.pos == 0) | ((data.pos & data.pos[clue_id]) != 0)
    strengths = _strengths(_cosines(data, [clue_id]), allowed)
    is_syn = np.zeros(data.size, dtype=bool)
    is_syn[list(syn)] = True
    is_fam = np.zeros(data.size, dtype=bool)
    is_fam[list(fam)] = True
    p = np.where(is_syn, SYN_P, np.where(is_fam, FAMILY_P, 0.0)) * strengths[0] / 100
    return p * 1000 + (1 - p) * estimate(strengths), is_syn, is_fam, allowed


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


def _shown(data, i, allowed, score):
    """The form to list word i as: its base when it's a regular inflection
    of an allowed word that scores about as well (WATERS -> WATER; see
    SHOWN_MARGIN). The game counts them as one answer."""
    word, best = data.word(i), i
    for b in data.bases(i) - {i}:
        base = data.word(b)
        if (allowed[b] and len(base) >= 3 and word.startswith(base[:-1])
                and len(base) < len(data.word(best)) and score[b] >= (1 - SHOWN_MARGIN) * score[i]):
            best = b
    return best


def _forms(data, ids):
    """The words and their base forms, as strings."""
    return {data.word(b) for i in ids for b in data.bases(i)}


def _too_close(data, i, clues):
    """True if word i is built on a clue or sits inside one (FOOTBALL for
    FOOT, TELEPHONE for PHONE, PHONE for TELEPHONE): the game turns those
    down as too close. clues holds the clues' forms (_forms), and word i's
    base forms count too: COINS sits inside BITCOIN through COIN, and
    FOOTBALL is built on FEET through FOOT."""
    for word in _forms(data, [i]):
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
    forms = _forms(data, clue_ids)
    out, seen = [], set()
    for i in _ranked(score, allowed, top * 20):
        if _too_close(data, i, forms) or _same_answer(data, i, seen):
            continue
        # A linked word stays as linked (its phrases name it); a word that's
        # only close in meaning shows in its base form if that scores about
        # as well.
        j = i if link[i] > 0 else _shown(data, i, allowed, score)
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
    'synonym' (WordNet, main senses), 'related' (a main sense's family) or
    'meaning' (word vectors). Opposites are left out."""
    score, is_syn, is_fam, allowed = _closest_scores(data, clue_id)
    forms = _forms(data, [clue_id])
    out, seen = [], set()
    for i in _ranked(score, allowed, top * 3):
        if _too_close(data, i, forms) or _same_answer(data, i, seen):
            continue
        j = _shown(data, i, allowed, score)
        kind = ('synonym' if is_syn[i] or is_syn[j] else
                'related' if is_fam[i] or is_fam[j] else 'meaning')
        out.append({'word': data.word(j).upper(), 'type': kind,
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
