"""Firsts (firsts.online, Bleppo Games): a crossword traced on a hex board.

The game, as its tutorial explains it: 19 letter tiles in a hexagon (columns
of 3, 4, 5, 4, 3) and 10 clues, each with its length in brackets. An answer
is traced through neighbouring tiles, no tile twice in one answer, and starts
on a green tile ("First letters are highlighted in green"). Tiles are shared
between answers, and "letters go away when they are no longer needed".

That last rule is exact (checked against the game on puzzle 001): a tile
goes once every answer whose path uses it is solved, and a green tile turns
plain once every answer starting on it is solved. So at any moment the tiles
still showing are exactly the tiles the unsolved answers use, and the green
tiles are exactly their first tiles. board_rules() uses that.

Some tiles show a picture instead of a letter (a sun, a lion, the Venus
sign), and the answer spells the picture's word through it: SUNTAN through a
sun, CLEVER through the Venus sign as C, L, EVE, R. Lengths count letters,
not tiles. A picture's word is unknown until someone works it out, so trace()
lets it stand for any run of PICTURE_MIN to PICTURE_MAX letters and records
the reading each candidate needs; picture_readings() then picks the reading
most clues agree on.

solve() takes a board and its clues and returns, for each clue, the traceable
words that fit its length, best first. A word's score adds up:
  definition   how much of the clue the word's WordNet definitions explain
               ("Great misfortune": TRAGEDY is "an event resulting in great
               loss and misfortune")
  reverse      the word appearing in the clue words' own definitions
               ("Anubis": "Egyptian god of the dead" -> GOD)
  context      definition words the clue and the word share
  vector       Numberbatch cosine between the clue and the word (the
               Chainle data, already in memory for /chainle and /4x3)
  popularity   how common the word is
then board_rules() and picture_readings() adjust it for the whole board.

The words come from Data (datasets/firsts_data.npz, built by
scripts/build_firsts_data.py from WordNet 3.0 and the site's word lists).
No game data, no network, no LLM.
"""

import bisect
import math
import re

import numpy as np

SOLVER_VERSION = 1

RADIUS = 2
CLUES = 10
MAX_LETTERS = 15
# A picture stands for this many letters (EVE, SUN, COW, LION, ADAM).
PICTURE_MIN, PICTURE_MAX = 3, 8
MAX_PICTURES = 4
# Ways to trace one word kept for the board rules: two paths that use the
# same tiles count once.
MAX_PATHS = 6
# Trace work caps (DFS steps): per word, and per solve. Hitting one leaves
# the answer list incomplete, which solve() reports.
WORD_STEPS = 20_000
SOLVE_STEPS = 3_000_000
# Words shown per clue, and how many the board rules and picture readings
# look at.
SHOW = 10
CONSIDER = 40
# Phrases built from common words when the answer list lacks them
# (composed): each word at least this common, how far a built phrase sits
# below a listed one, how many first words are tried, and a work cap.
COMPOSE_ZIPF = 3.0
COMPOSE_PENALTY = 1.5
W_BUILT = -1.5
# Short words a built phrase may use besides WordNet's (which has no "the",
# "and" or "to").
SHORT_WORDS = frozenset("""
A AM AN AS AT BE BY DO GO HE HI IF IN IS IT ME MY NO OF OH OK ON OR OX SO TO
UP US WE AND ARE BUT CAN FOR HER HIM HIS ITS NOT OFF OUR OUT SHE THE TOO WAS
WHO WHY YES YET YOU
""".split())
COMPOSE_FIRST = 300
COMPOSE_STEPS = 400_000
# A clue with a single traceable word leads by this much (Start here).
LONE_LEAD = 10.0
# The board rules' exact search runs once this few clues are unsolved.
EXACT_CLUES = 3
EXACT_PER_CLUE = 25

# Score weights (see the module docstring), set by hand and checked once on
# the dev boards with scripts/backtest_firsts.py.
W_DEF = 3.0
W_REV = 3.0
W_CTX = 2.0
W_VEC = 4.0
W_POP = 0.6
# A word that repeats a clue word is rarely its answer ("Fruit rarely seen
# in a fruit salad" is not FRUIT).
W_SELF = -2.0
# Board rules: a word that fits what the board forces, and one that doesn't.
W_FITS = 1.0
W_MISFIT = -3.0
# The exact search: a word in a combination that uses every tile left.
W_PINNED = 6.0
# Pictures: a word using the leading reading, and one using another. No
# bonus for the leading one: it would lift a picture word (SUNG) over the
# plain answer in every clue of its length.
W_LEAD = 0.0
W_OTHER_READING = -1.0
# Picture readings tried: words from the picture's typed name, the title and
# the clues, plus the NEAREST words to the name and title by Numberbatch.
NEAREST = 40
NEAREST_CHUNK = 2048
# Reading evidence (picture_readings): how sharply a clue's credit falls
# off below its top word, and the extra evidence for each kind of hint.
READING_TEMP = 1.5
R_TYPED = 4.0
R_NEAR_TYPED = 2.0
R_TITLE = 3.0
# Senses after the first two count for less.
LATE_SENSE = 0.5

LABELS = ('checked', 'players', 'yours', 'guess')

# Words that say nothing about the answer, dropped from clues and
# definitions alike. Direction words (up, down, out, off) stay: they matter
# in phrases.
STOP = frozenset("""
a an the of in on at to for from with by as and or but nor that this these
those it its is are was were be been being am who whom whose what which when
where how why you your yours he she they them their theirs his her hers him
we our ours us i me my mine one ones someone something somebody any some
other others often can could may might must has have had having does do did
doing before after most many much very so such not no yes perhaps maybe eg
etc like thing things would should will shall into about if than then there
here also just only own same all each every both either neither more less
used use using especially usually typically esp e g ie s t way kind sort
""".split())

_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")
_LETTERS = re.compile(r'[^A-Z]')
# A clue's lengths: "5", "2,4", "3-2-5" (also "6 3").
_SPEC = re.compile(r'^\s*\(?\s*(\d{1,2}(?:\s*[,\- ]\s*\d{1,2})*)\s*\)?\s*$')
# A cross-reference: a number 1-10 standing alone ("Planted, as 9",
# "2 3 4 and 5"), not 24, 8th, a6 or 2019's.
_XREF = re.compile(r"(?<![\w.,/'\-])(10|[1-9])(?![\w%/'\-]|[.,]\d)")
_DOUBLE = re.compile(r'([b-df-hj-np-tv-z])\1$')


class BoardError(ValueError):
    """A board or clue list the solver can't use; the message says why."""


# --- text -----------------------------------------------------------------------

def stem(word):
    """A rough stem, the same for a word and its usual inflections:
    MISFORTUNES -> misfortun, ROAMED -> roam, RUNNING -> run, HORSE and
    HORSES -> hors. Used on both sides (clues and definitions), so it only
    has to be consistent, not correct."""
    w = word.lower()
    if w.endswith("'s"):
        w = w[:-2]
    if len(w) <= 3:
        return w
    if w.endswith('ies') and len(w) > 4:
        w = w[:-3] + 'y'
    elif w.endswith('ied') and len(w) > 4:
        w = w[:-3] + 'y'
    elif w.endswith(('sses', 'shes', 'ches', 'xes', 'zes')):
        w = w[:-2]
    elif w.endswith('s') and not w.endswith(('ss', 'us', 'is')):
        w = w[:-1]
    elif w.endswith('ing') and len(w) > 5:
        w = w[:-3]
        if _DOUBLE.search(w):
            w = w[:-1]
    elif w.endswith('ed') and len(w) > 4:
        w = w[:-2]
        if _DOUBLE.search(w):
            w = w[:-1]
    elif w.endswith('ly') and len(w) > 5:
        w = w[:-2]
    if w.endswith('e') and len(w) > 3:
        w = w[:-1]
    return w


def words_of(text):
    """The lower-case words of a text, apostrophes kept inside words."""
    return _WORD.findall(text.lower().replace('’', "'"))


def content_stems(text):
    """Stems of a text's meaningful words, in order, repeats dropped."""
    seen, out = set(), []
    for w in words_of(text):
        if w in STOP or len(w) < 2:
            continue
        s = stem(w)
        if s not in seen and s not in STOP:
            seen.add(s)
            out.append(s)
    return out


def letters_key(text):
    """Letters only, upper case: "lion's share" -> LIONSSHARE."""
    return _LETTERS.sub('', text.upper())


def parse_spec(text):
    """A clue's length as word lengths: "5" -> (5,), "2,4" -> (2, 4).

    Raises BoardError on anything else or over MAX_LETTERS letters."""
    m = _SPEC.match(str(text or ''))
    if not m:
        raise BoardError(f'a length like 5 or 2,4, not {text!r}')
    parts = tuple(int(p) for p in re.split(r'\s*[,\- ]\s*', m.group(1).strip()))
    if any(p < 1 for p in parts) or not 2 <= sum(parts) <= MAX_LETTERS:
        raise BoardError(f'lengths add up to 2-{MAX_LETTERS} letters, not {text!r}')
    return parts


def spec_text(parts, sep=','):
    return sep.join(str(p) for p in parts)


def cross_refs(text):
    """The clue numbers (1-10) a clue mentions, in order."""
    return [int(m.group(1)) for m in _XREF.finditer(text or '')]


def resolve_refs(text, answers):
    """A clue with each clue number it mentions replaced by that clue's
    answer when known (answers: {clue number: word}), and dropped when not."""
    def swap(m):
        word = answers.get(int(m.group(1)))
        return f' {word.lower()} ' if word else ' '
    return _XREF.sub(swap, text or '')


# --- the board --------------------------------------------------------------------

_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1))


def hex_cells(radius=RADIUS):
    """Axial (q, r) cells of a hexagon, column by column from the left, each
    column top to bottom: the order the page types tiles in. Columns are 3,
    4, 5, 4, 3 tiles for radius 2."""
    return [(q, r) for q in range(-radius, radius + 1)
            for r in range(max(-radius, -q - radius), min(radius, -q + radius) + 1)]


def neighbours(cells):
    index = {c: i for i, c in enumerate(cells)}
    return [[index[(q + dq, r + dr)] for dq, dr in _DIRS if (q + dq, r + dr) in index]
            for q, r in cells]


class Board:
    """One board as the player sees it.

    tiles     per cell: a letter (A-Z), or a picture as a digit '1'-'4'
              (two tiles with the same picture share a digit)
    green     per cell: True where a tile is green now
    gone      per cell: True where a tile has gone
    readings  {picture digit: letters} the player or a known answer gave
    names     {picture digit: what the player says the picture shows}
    title     the puzzle's title ("The Garden of Eden")
    synced    True when the player keeps the board in step with the game
              (marks tiles gone as they go), so the board rules can trust it
    """

    def __init__(self, tiles, green, gone=None, readings=None, names=None,
                 title='', synced=False, cells=None):
        self.cells = cells or hex_cells()
        n = len(self.cells)
        if len(tiles) != n or len(green) != n:
            raise BoardError(f'a board has {n} tiles')
        self.tiles = [str(t).upper() for t in tiles]
        for t in self.tiles:
            if not (re.fullmatch(r'[A-Z]', t) or re.fullmatch(r'[1-4]', t)):
                raise BoardError('each tile is one letter or a picture')
        self.green = [bool(g) for g in green]
        self.gone = [bool(g) for g in (gone or [False] * n)]
        if len(self.gone) != n:
            raise BoardError(f'a board has {n} tiles')
        self.readings = {str(k): letters_key(v) for k, v in (readings or {}).items()
                         if str(k) in self.pictures() and PICTURE_MIN <= len(letters_key(v)) <= PICTURE_MAX}
        self.names = {str(k): str(v).strip()[:40] for k, v in (names or {}).items()
                      if str(k) in self.pictures() and str(v).strip()}
        self.title = str(title or '').strip()[:80]
        self.synced = bool(synced)
        self.adj = neighbours(self.cells)

    def pictures(self):
        return sorted({t for t in self.tiles if t.isdigit()})

    def live(self):
        return [i for i in range(len(self.tiles)) if not self.gone[i]]

    def starts(self):
        return [i for i in self.live() if self.green[i]]

    def unknown_pictures(self):
        return [p for p in self.pictures()
                if p not in self.readings and any(t == p and not self.gone[i] for i, t in enumerate(self.tiles))]

    def letters_on(self):
        """Letters the live tiles can spell, with counts (known readings in)."""
        counts = {}
        for i in self.live():
            t = self.tiles[i]
            for ch in (self.readings.get(t, '') if t.isdigit() else t):
                counts[ch] = counts.get(ch, 0) + 1
        return counts

    def key(self):
        """The tiles and greens as text: pictures as digits."""
        return ''.join(self.tiles) + ':' + ''.join('1' if g else '0' for g in self.green)


# --- tracing ------------------------------------------------------------------

class _Stop(Exception):
    pass


def trace(board, word, budget=None, max_paths=MAX_PATHS, valid=None):
    """Ways to spell word (letters only) on the live tiles from a green one:
    [(path, readings)], path a tuple of tile indexes, readings the picture
    letters that path needs ({} without pictures). Paths over the same tiles
    with the same readings count once.

    budget is a one-item list of DFS steps left, shared across calls; when
    it runs out this stops and sets budget[0] to -1. valid, if given, says
    which letters a picture may stand for (Data.is_word).
    """
    tiles, adj, gone = board.tiles, board.adj, board.gone
    known = board.readings
    n = len(word)
    out, seen = [], set()
    steps = [WORD_STEPS]

    def pieces(i, pos, bound):
        t = tiles[i]
        if t.isdigit():
            r = known.get(t) or bound.get(t)
            if r:
                return (r,) if word.startswith(r, pos) else ()
            return tuple(word[pos:pos + k] for k in range(PICTURE_MIN, PICTURE_MAX + 1)
                         if pos + k <= n and (valid is None or valid(word[pos:pos + k])))
        return (t,) if word.startswith(t, pos) else ()

    def dfs(i, pos, used, path, bound):
        steps[0] -= 1
        if steps[0] < 0:
            raise _Stop
        if budget is not None:
            budget[0] -= 1
            if budget[0] < 0:
                raise _Stop
        t = tiles[i]
        for piece in pieces(i, pos, bound):
            b = bound if (not t.isdigit() or t in known or t in bound) else {**bound, t: piece}
            end = pos + len(piece)
            if end == n:
                mark = (frozenset(path), tuple(sorted(b.items())))
                if mark not in seen:
                    seen.add(mark)
                    out.append((tuple(path), b))
                    if len(out) >= max_paths:
                        raise _Stop
                continue
            for j in adj[i]:
                if not gone[j] and not used >> j & 1:
                    path.append(j)
                    dfs(j, end, used | 1 << j, path, b)
                    path.pop()

    try:
        for s in board.starts():
            dfs(s, 0, 1 << s, [s], {})
    except _Stop:
        if budget is not None and budget[0] < 0:
            budget[0] = -1
    return out


# --- the word data ------------------------------------------------------------------

# An answer's popularity (Zipf, 0-7.5) as one lower-case letter, in steps
# of POP_STEP: 'a' rare, 'z' as common as words get.
POP_STEP = 0.3


def pop_char(zipf):
    return chr(97 + max(0, min(25, round(zipf / POP_STEP))))


def pop_zipf(ch):
    return (ord(ch) - 97) * POP_STEP


class _Strings:
    """A sorted list of ASCII strings packed into one bytes blob with
    offsets, searched by bisect (no dict of 150k strings in memory)."""

    def __init__(self, blob, off):
        self.blob = bytes(blob)
        self.off = off

    def __len__(self):
        return len(self.off) - 1

    def __getitem__(self, i):
        return self.blob[int(self.off[i]):int(self.off[i + 1])]

    def find(self, key):
        key = key.encode('ascii', 'ignore') if isinstance(key, str) else key
        i = bisect.bisect_left(self, key)
        return i if i < len(self) and self[i] == key else -1


class Data:
    """The solver's word data (datasets/firsts_data.npz).

    lex_<n>       the answer list for n letters: lines "KEYp", KEY upper
                  case with spaces between words, p its popularity as one
                  lower-case letter (pop_char), most popular first
    lem_blob/off  WordNet lemmas, letters only and sorted (EMAIL, LIONSSHARE)
    lem_ptr/syn   each lemma's synsets, most used sense first (CSR)
    syn_ptr/tok   each synset's words: its definition's stems, its lemmas'
                  stems and its parents' lemmas' stems (CSR, token ids in
                  17 bits: syn_tok the low 16, syn_tok_hi the top bit
                  packed 8 to a byte)
    tok_blob/off  the token stems, sorted
    tok_idf       each token's IDF over the synsets, times 16
    """

    def __init__(self, arrays):
        self.lexicon = {int(k[4:]): arrays[k].tobytes().decode('ascii')
                        for k in arrays if k.startswith('lex_')}
        self.lemmas = _Strings(arrays['lem_blob'], arrays['lem_off'])
        self.lem_ptr, self.lem_syn = arrays['lem_ptr'], arrays['lem_syn']
        self.syn_ptr, self.syn_tok = arrays['syn_ptr'], arrays['syn_tok']
        self.syn_tok_hi = arrays['syn_tok_hi']
        self.tokens = _Strings(arrays['tok_blob'], arrays['tok_off'])
        self.tok_idf = arrays['tok_idf']
        self.version = str(arrays['version'])
        self.nbytes = (sum(len(b) for b in self.lexicon.values())
                       + sum(int(arrays[k].nbytes) for k in arrays if not k.startswith('lex_')))
        self._words = {}

    @classmethod
    def from_file(cls, path):
        with np.load(path, allow_pickle=False) as z:
            return cls({k: z[k] for k in z.files})

    def lemma(self, key):
        return self.lemmas.find(letters_key(key))

    def token(self, s):
        return self.tokens.find(s)

    def idf(self, t):
        return float(self.tok_idf[t]) / 16.0

    def senses(self, lemma):
        return self.lem_syn[self.lem_ptr[lemma]:self.lem_ptr[lemma + 1]].tolist()

    def synset_tokens(self, s):
        a, b = int(self.syn_ptr[s]), int(self.syn_ptr[s + 1])
        if a == b:
            return []
        low = self.syn_tok[a:b].astype(np.uint32)
        high = np.unpackbits(self.syn_tok_hi[a // 8:(b + 7) // 8])[a % 8:a % 8 + (b - a)]
        return (low | high.astype(np.uint32) << 16).tolist()

    def bag(self, key):
        """{token: weight} for a word or phrase's WordNet senses (empty if
        WordNet doesn't know it)."""
        out = {}
        lemma = self.lemma(key)
        if lemma < 0:
            return out
        for rank, s in enumerate(self.senses(lemma)):
            w = 1.0 if rank < 2 else LATE_SENSE
            for t in self.synset_tokens(s):
                if out.get(t, 0.0) < w:
                    out[t] = w
        return out

    def scan(self, n, pattern):
        """(KEY, Zipf) for the n-letter answers whose line matches pattern
        (a regex over the KEY part), most popular first."""
        blob = self.lexicon.get(n)
        if not blob:
            return
        for m in re.finditer(r'^(' + pattern + r')([a-z])$', blob, re.M):
            yield m.group(1), pop_zipf(m.group(2))

    def zipf(self, key):
        """A listed answer's Zipf, or None."""
        n = len(letters_key(key))
        blob = self.lexicon.get(n, '')
        m = re.search(r'^' + re.escape(key.upper()) + r'([a-z])$', blob, re.M)
        return pop_zipf(m.group(1)) if m else None

    def is_word(self, letters):
        """True for a WordNet lemma (a picture stands for one: SUN, LION,
        EVE). A bisect, memoized."""
        hit = self._words.get(letters)
        if hit is None:
            if len(self._words) > 5000:
                self._words.clear()
            hit = self._words[letters] = self.lemma(letters) >= 0
        return hit


class Vectors:
    """Numberbatch unit vectors from the Chainle data (functions/chainle.py
    Data), averaged over a phrase's words."""

    def __init__(self, chainle_data):
        self.data = chainle_data
        self._memo = {}

    def unit(self, words):
        key = tuple(words)
        if key in self._memo:
            return self._memo[key]
        ids = []
        for w in words:
            i = self.data.id(w)
            if i < 0:
                for base in _bases(w):
                    i = self.data.id(base)
                    if i >= 0:
                        break
            if i >= 0:
                ids.append(i)
        v = None
        if ids:
            m = (self.data.vec[ids].astype(np.float32) * self.data.scale[ids][:, None]).mean(axis=0)
            norm = float(np.linalg.norm(m))
            v = m / norm if norm else None
        if len(self._memo) > 1500:
            self._memo.clear()
        self._memo[key] = v
        return v

    def nearest(self, words, k=NEAREST):
        """The k words closest to these words' average, PICTURE_MIN to
        PICTURE_MAX letters, upper case (one pass over the vectors, in
        small chunks so the float copy stays small)."""
        v = self.unit(words)
        if v is None:
            return []
        d = self.data
        sims = np.empty(d.size, dtype=np.float32)
        for s in range(0, d.size, NEAREST_CHUNK):
            e = min(s + NEAREST_CHUNK, d.size)
            sims[s:e] = (d.vec[s:e].astype(np.float32) @ v) * d.scale[s:e]
        top = min(d.size - 1, k * 4)
        idx = np.argpartition(-sims, top)[:top]
        out = []
        for i in idx[np.argsort(-sims[idx])].tolist():
            w = d.word(i)
            if PICTURE_MIN <= len(w) <= PICTURE_MAX and w.isalpha():
                out.append(w.upper())
                if len(out) >= k:
                    break
        return out


# --- scoring ------------------------------------------------------------------

class _Clue:
    """What scoring needs from one clue's text."""

    def __init__(self, data, vectors, text):
        self.text = text
        raw = [w for w in words_of(text) if w not in STOP and len(w) > 1]
        self.stems = content_stems(text)
        self.terms = {}
        for s in self.stems:
            t = data.token(s)
            if t >= 0:
                self.terms[t] = data.idf(t)
        self.stem_set = set(self.stems)
        # The clue words' own senses, plus any run of 2-4 clue words WordNet
        # lists as a phrase ("fruit salad", "put one's foot down").
        self.exp = {}
        words = words_of(text)
        looked = set()
        for size in (4, 3, 2, 1):
            for i in range(len(words) - size + 1):
                run = words[i:i + size]
                if size == 1 and (run[0] in STOP or len(run[0]) < 2):
                    continue
                key = letters_key(''.join(run))
                if key in looked:
                    continue
                looked.add(key)
                bag = data.bag(key)
                if not bag and size == 1:
                    for base in _bases(run[0]):
                        bag = data.bag(base)
                        if bag:
                            break
                weight = 1.0 if size > 1 else 0.8
                for t, w in bag.items():
                    v = w * weight
                    if self.exp.get(t, 0.0) < v:
                        self.exp[t] = v
        self.vec = vectors.unit(raw) if vectors and raw else None


def _bases(word):
    """Likely base forms of an inflected word, for WordNet lookups."""
    out = []
    for suf, rep in (('ies', 'y'), ('es', ''), ('s', ''), ('ed', ''), ('ed', 'e'), ('ing', ''), ('ing', 'e'), ("'s", '')):
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            out.append(word[:-len(suf)] + rep)
    return out


def _cosine_bags(a, b, data):
    """IDF-weighted cosine between two token bags."""
    if not a or not b:
        return 0.0
    small, big = (a, b) if len(a) < len(b) else (b, a)
    dot = 0.0
    for t, w in small.items():
        v = big.get(t)
        if v:
            dot += w * v * data.idf(t) ** 2
    if not dot:
        return 0.0
    na = math.sqrt(sum((w * data.idf(t)) ** 2 for t, w in a.items()))
    nb = math.sqrt(sum((w * data.idf(t)) ** 2 for t, w in b.items()))
    return dot / (na * nb) if na and nb else 0.0


class _Word:
    """Scoring material for one answer word, shared across clues."""

    def __init__(self, data, vectors, key):
        self.key = key
        parts = key.lower().split(' ')
        self.bag = data.bag(key)
        if not self.bag and len(parts) > 1:
            # A phrase WordNet doesn't list: its words' senses, at half weight.
            for p in parts:
                for t, w in data.bag(p).items():
                    if self.bag.get(t, 0.0) < w * 0.5:
                        self.bag[t] = w * 0.5
        self.stems = [stem(p) for p in parts if p not in STOP] or [stem(p) for p in parts]
        self.terms = {}
        for s in self.stems:
            t = data.token(s)
            if t >= 0:
                self.terms[t] = data.idf(t)
        self.vec = vectors.unit(parts) if vectors else None


def score_word(clue, word, zipf, data):
    """A word's score for a clue, and its parts (for the backtest)."""
    parts = {}
    total = sum(clue.terms.values())
    parts['definition'] = (sum(idf for t, idf in clue.terms.items() if t in word.bag) / total) if total else 0.0
    wt = sum(word.terms.values())
    parts['reverse'] = (sum(idf * clue.exp.get(t, 0.0) for t, idf in word.terms.items()) / wt) if wt else 0.0
    parts['context'] = _cosine_bags(clue.exp, word.bag, data)
    parts['vector'] = float(np.dot(clue.vec, word.vec)) if clue.vec is not None and word.vec is not None else 0.0
    parts['popularity'] = max(0.0, min(zipf, 7.0)) / 7.0
    parts['self'] = 1.0 if clue.stem_set & set(word.stems) else 0.0
    score = (W_DEF * parts['definition'] + W_REV * parts['reverse'] + W_CTX * parts['context']
             + W_VEC * parts['vector'] + W_POP * parts['popularity'] + W_SELF * parts['self'])
    return score, parts


# --- candidates ------------------------------------------------------------------

def _class(letters):
    return '[' + ''.join(sorted(letters)) + ']' if letters else '(?!)'


def _pattern(board, parts, readings=None):
    """A regex over the answer list's keys that every traceable answer of
    these word lengths matches (a quick filter; trace() decides).

    Without unknown pictures: a green tile's letter, then board letters.
    With them, also: words holding one of the pictures' reading options,
    the rest board letters."""
    on = ''.join(sorted(board.letters_on()))
    unknown = board.unknown_pictures()
    starts = set()
    for i in board.starts():
        t = board.tiles[i]
        if t.isdigit():
            r = board.readings.get(t)
            if r:
                starts.add(r[0])
            else:
                starts.update(on)    # a green picture can start anything
        else:
            starts.add(t)
    plain = _class(starts) + (r'[%s ]*' % on if on else '')
    options = set().union(*(readings or {}).values()) if readings else set()
    if not unknown or not options:
        return plain
    body = r'[%s ]*' % on if on else ''
    run = _trie_regex(sorted(options))
    held = body + run + body + ''.join('(?:%s%s)?' % (run, body) for _ in unknown[1:])
    return '(?:%s|%s)' % (plain, held)


def _NO_PICTURE(_letters):
    return False


def reading_options(data, vectors, board, texts):
    """The words an unknown picture may stand for: the words of its typed
    name, the title and the clues (singular forms too), plus the nearest
    words to the name, the title, and the two together. WordNet words of
    PICTURE_MIN-PICTURE_MAX letters only. A sun picture on a board whose
    clue says "features a sun" gets SUN; the Venus sign on "The Garden of
    Eden" gets EVE from the title's neighbours."""
    title = [w for w in words_of(board.title) if w not in STOP]
    clue_words = [w for t in texts for w in words_of(t) if w not in STOP]
    near_title = vectors.nearest(title) if vectors and title else []
    out = {}
    for p in board.unknown_pictures():
        name = [w for w in words_of(board.names.get(p, '')) if w not in STOP]
        seeds = name + title + clue_words + near_title
        seeds += [b for w in name + clue_words for b in _bases(w)]
        if vectors and name:
            seeds += vectors.nearest(name) + vectors.nearest(name + title)
        # Words in the name's and title's WordNet senses ("Eden: the
        # garden where Adam and Eve lived").
        for w in name + title:
            for t in data.bag(letters_key(w)):
                seeds.append(data.tokens[t].decode())
        out[p] = {r for r in (letters_key(s) for s in seeds)
                  if PICTURE_MIN <= len(r) <= PICTURE_MAX and data.is_word(r)}
    return out


def _trie_regex(words):
    """One regex matching any of the words, as a letter trie (fast in re)."""
    trie = {}
    for w in words:
        node = trie
        for ch in w:
            node = node.setdefault(ch, {})
        node[''] = True

    def build(node):
        ends = '' in node
        alts = [re.escape(ch) + build(sub) for ch, sub in sorted(node.items()) if ch]
        if not alts:
            return ''
        body = alts[0] if len(alts) == 1 else '(?:' + '|'.join(alts) + ')'
        return '(?:' + body + ')?' if ends else body
    return build(trie) if trie else '(?!)'


def picture_windows(data, letters, counts, pictures, allowed):
    """The runs of a word a picture could stand for: words in allowed (see
    reading_options), never the whole word. With one unknown picture, the
    run must hold every letter the board lacks (SUNTAN on a board with no S
    or U: SUN, not TAN). None if nothing fits."""
    absent = [i for i, ch in enumerate(letters) if ch not in counts]
    n = len(letters)
    if pictures == 1 and absent and absent[-1] - absent[0] >= PICTURE_MAX:
        return None
    out = set()
    for s in range(n):
        if pictures == 1 and absent and s > absent[0]:
            break
        for m in range(PICTURE_MIN, min(PICTURE_MAX, n - 1) + 1):
            e = s + m
            if e > n:
                break
            if pictures == 1 and absent and e <= absent[-1]:
                continue
            w = letters[s:e]
            if w in allowed:
                out.add(w)
    if absent and not out:
        return None
    return out


def candidates(data, board, parts, budget, readings=None):
    """[(KEY, Zipf, paths)] for the answers of these word lengths that trace
    on the board, most popular first. readings: {picture: options} from
    reading_options(), for unknown pictures."""
    n = sum(parts)
    shape = list(parts)
    counts = board.letters_on()
    unknown = len(board.unknown_pictures())
    allowed = set().union(*readings.values()) if readings else set()
    out = []
    for key, zipf in data.scan(n, _pattern(board, parts, readings)):
        if [len(w) for w in key.split(' ')] != shape:
            continue
        letters = key.replace(' ', '')
        # Every letter on the board, as many times as the word needs?
        need, fits = {}, True
        for ch in letters:
            need[ch] = need.get(ch, 0) + 1
            if need[ch] > counts.get(ch, 0):
                fits = False
                break
        if fits:
            # Traced on the letter tiles (and known pictures) alone.
            valid = _NO_PICTURE if unknown else None
        elif unknown:
            windows = picture_windows(data, letters, counts, unknown, allowed)
            if not windows:
                continue
            valid = windows.__contains__
        else:
            continue
        paths = trace(board, letters, budget, valid=valid)
        if not paths and fits and unknown:
            # On the board letter for letter, but maybe only traceable
            # through a picture.
            windows = picture_windows(data, letters, counts, unknown, allowed)
            if windows:
                paths = trace(board, letters, budget, valid=windows.__contains__)
        if budget[0] < 0:
            break
        if not paths:
            continue
        out.append((key, zipf, paths))
    if len(parts) > 1 and budget[0] >= 0:
        have = {k for k, _z, _p in out}
        out += composed(data, board, parts, budget, have)
    return out


def composed(data, board, parts, budget, have):
    """Phrases the answer list lacks, made of common words traced one after
    another (NO SUGAR, EXIT ROW): [(KEY, Zipf, paths, True)]. A built
    phrase's popularity is its rarest word's, less COMPOSE_PENALTY, and its
    score takes W_BUILT. Letter tiles only; one-letter words aren't built."""
    if min(parts) < 2:
        return []
    on = ''.join(sorted(set(board.letters_on())))
    common = {}
    for n in set(parts):
        common[n] = {k: z for k, z in data.scan(n, '[%s]+' % on)
                     if z >= COMPOSE_ZIPF and (k in SHORT_WORDS or (n > 2 and data.is_word(k)))}
    starts = sorted(common[parts[0]].items(), key=lambda kv: -kv[1])[:COMPOSE_FIRST]
    found = {}
    steps = [COMPOSE_STEPS]

    def runs(i, length, used):
        """Every (letters, path) of `length` letter tiles after tile i."""
        acc = []

        def dfs(j, s, path, used):
            steps[0] -= 1
            if steps[0] < 0:
                raise _Stop
            if len(s) == length:
                acc.append((s, list(path)))
                return
            for k in board.adj[j]:
                t = board.tiles[k]
                if board.gone[k] or used >> k & 1 or t.isdigit():
                    continue
                path.append(k)
                dfs(k, s + t, path, used | 1 << k)
                path.pop()
        dfs(i, '', [], used)
        return acc

    def extend(path, used, words, zs, j):
        if j == len(parts):
            key = ' '.join(words)
            if key not in have:
                entry = found.setdefault(key, [min(zs) - COMPOSE_PENALTY, []])
                if len(entry[1]) < MAX_PATHS:
                    entry[1].append((tuple(path), {}))
            return
        for s, more in runs(path[-1], parts[j], used):
            z = common[parts[j]].get(s)
            if z is not None:
                m = used
                for k in more:
                    m |= 1 << k
                extend(path + more, m, words + [s], zs + [z], j + 1)

    try:
        for w, z in starts:
            for path, _r in trace(board, w, budget, valid=_NO_PICTURE):
                used = 0
                for k in path:
                    used |= 1 << k
                extend(list(path), used, [w], [z], 1)
    except _Stop:
        pass
    return [(k, z, p, True) for k, (z, p) in found.items()]


class Item:
    """One suggested answer for one clue (built: a phrase composed() made)."""
    __slots__ = ('key', 'zipf', 'paths', 'built', 'score', 'parts', 'fits', 'pinned')

    def __init__(self, key, zipf, paths, built=False):
        self.key = key
        self.zipf = zipf
        self.paths = paths
        self.built = built
        self.score = 0.0
        self.parts = {}
        self.fits = None
        self.pinned = False

    def reading(self, picture):
        """The letters this word needs from a picture, or None."""
        for _p, r in self.paths:
            if picture in r:
                return r[picture]
        return None

    def masks(self):
        return [sum(1 << t for t in p) for p, _r in self.paths]


# --- the whole board ----------------------------------------------------------------

def picture_readings(board, lists, data, vectors):
    """{picture: [(reading, evidence)]}, best first, for each unknown picture.

    Evidence adds up, per reading: how strongly each clue's best words
    using it score (a reading many clues need wins: SUNDAY, SUNTAN and
    SUNFLOWER all read SUN), whether it is what the player typed for the
    picture or close to it, and how close it is to the puzzle's title.
    """
    out = {}
    title = [w for w in words_of(board.title) if w not in STOP]
    title_vec = vectors.unit(title) if vectors and title else None
    for p in board.unknown_pictures():
        evidence = {}
        for items in lists.values():
            if not items:
                continue
            # A reading counts for a clue as far as its best word leads the
            # clue's whole list: 1 when it is the clue's top word.
            top = items[0].score
            best = {}
            for it in items[:CONSIDER]:
                r = it.reading(p)
                if r and it.score > best.get(r, -1e9):
                    best[r] = it.score
            for r, s in best.items():
                evidence[r] = evidence.get(r, 0.0) + math.exp(READING_TEMP * (s - top))
        name = board.names.get(p, '')
        name_key = letters_key(name)
        name_bag = data.bag(name_key) if name_key else {}
        for r in list(evidence):
            # A typed name helps a reading as far as clues back it up.
            support = min(1.0, evidence[r])
            if name_key and r == name_key:
                evidence[r] += R_TYPED * support
            elif name_bag:
                t = data.token(stem(r))
                if t >= 0 and t in name_bag:
                    evidence[r] += R_NEAR_TYPED * support
            if title_vec is not None:
                v = vectors.unit([r.lower()])
                if v is not None:
                    evidence[r] += R_TITLE * max(0.0, float(np.dot(title_vec, v)))
        out[p] = sorted(evidence.items(), key=lambda kv: (-kv[1], kv[0]))
    return out


def board_rules(board, lists, solved_cover):
    """Mark items that fit what the board forces (see the module docstring).

    lists: {clue: [Item]} for the clues still unsolved, each already sorted.
    solved_cover: tile mask the solved answers use (their tiles may still be
    showing if the player hasn't marked them gone).

    1. Every live tile is on some unsolved answer and every green starts
       one. A tile only one clue's words can reach forces that clue's
       answer through it (likewise a green and its start), repeated until
       nothing changes. A clue whose words can't meet what is forced is left
       alone (its answer may be missing from the word list).
    2. With EXACT_CLUES or fewer clues unsolved, combinations of their top
       words are tried: the ones whose paths cover the live tiles exactly
       (and start on every green) mark their words pinned.
    When the player hasn't kept the board in step (board.synced is False)
    and some clues are solved, tiles their answers use don't count as
    needing an unsolved answer.
    """
    live = 0
    for i in board.live():
        live |= 1 << i
    greens = [i for i in board.starts()]
    must = live if board.synced or not solved_cover else live & ~solved_cover
    clues = list(lists)
    allowed = {c: None for c in clues}     # c -> item indexes still fitting
    forced_tiles = {c: 0 for c in clues}
    forced_starts = {c: set() for c in clues}
    for _round in range(6):
        cover, start = {}, {}
        for c in clues:
            for k, it in enumerate(lists[c][:CONSIDER]):
                if allowed[c] is not None and k not in allowed[c]:
                    continue
                for (path, _r), mask in zip(it.paths, it.masks()):
                    if mask & ~live:
                        continue
                    for t in path:
                        cover.setdefault(t, set()).add(c)
                    start.setdefault(path[0], set()).add(c)
        changed = False
        for t in range(len(board.tiles)):
            if must >> t & 1 and len(cover.get(t, ())) == 1:
                c = next(iter(cover[t]))
                if not forced_tiles[c] >> t & 1:
                    forced_tiles[c] |= 1 << t
                    changed = True
        for g in greens:
            if (board.synced or not solved_cover) and len(start.get(g, ())) == 1:
                c = next(iter(start[g]))
                if g not in forced_starts[c]:
                    forced_starts[c].add(g)
                    changed = True
        for c in clues:
            ft, fs = forced_tiles[c], forced_starts[c]
            if not ft and not fs:
                continue
            ok = set()
            for k, it in enumerate(lists[c][:CONSIDER]):
                for (path, _r), mask in zip(it.paths, it.masks()):
                    if mask & ft == ft and (not fs or (len(fs) == 1 and path[0] in fs)):
                        ok.add(k)
                        break
            if ok and ok != allowed[c]:
                allowed[c] = ok
                changed = True
        if not changed:
            break
    for c in clues:
        if allowed[c] is None:
            continue
        for k, it in enumerate(lists[c][:CONSIDER]):
            it.fits = k in allowed[c]
    if len(clues) <= EXACT_CLUES:
        _exact(board, lists, clues, live, must, greens, solved_cover)


def _exact(board, lists, clues, live, must, greens, solved_cover):
    """Mark words in a combination whose paths use exactly the live tiles."""
    green_mask = 0
    for g in greens:
        green_mask |= 1 << g
    strict = board.synced or not solved_cover
    options = []
    for c in clues:
        opts = []
        for k, it in enumerate(lists[c][:EXACT_PER_CLUE]):
            for (path, _r), mask in zip(it.paths, it.masks()):
                if not mask & ~live:
                    opts.append((k, mask, 1 << path[0]))
        options.append(opts)
    hits = [set() for _ in clues]

    def walk(i, used, starts, chosen):
        if i == len(clues):
            if used & must == must and (not strict or (used == live and starts & green_mask == green_mask)):
                for j, k in enumerate(chosen):
                    hits[j].add(k)
            return
        for k, mask, s in options[i]:
            walk(i + 1, used | mask, starts | s, chosen + [k])

    if all(options):
        walk(0, 0, 0, [])
    for j, c in enumerate(clues):
        for k in hits[j]:
            lists[c][k].pinned = True


def _effective(it):
    """An item's score with the board rules' adjustments."""
    return (it.score + (W_PINNED if it.pinned else 0.0)
            + (W_FITS if it.fits else W_MISFIT if it.fits is False else 0.0))


def _rank(items):
    items.sort(key=lambda it: (-_effective(it), -it.zipf, it.key))


def solve(data, board, clues, vectors=None, known=None, solved=(), refused=None, show=SHOW,
          budget=SOLVE_STEPS):
    """Each clue's best answers on this board.

    clues   [(clue text, length text)] in clue order (text may be ''; a
            clue with no length is skipped)
    known   {clue index: (word, label)} answers known from elsewhere: label
            'checked' (James), 'players' (enough players), 'yours' (this
            player marked it worked)
    solved  clue indexes the player has solved (their tiles may be gone)
    refused {clue index: words} players say the game refused there

    Returns {'clues': [...], 'pictures': {...}, 'complete': bool}; each clue
    {'n', 'spec', 'label', 'known', 'items': [{'word', 'path', 'readings',
    'fits', 'pinned'}], 'count'}.
    """
    known = {int(k): v for k, v in (known or {}).items()}
    solved = {int(s) for s in solved}
    specs = [parse_spec(spec) if str(spec or '').strip() else None for _text, spec in clues]
    refused = {int(k): {letters_key(w) for w in v} for k, v in (refused or {}).items()}
    answers = {i + 1: w for i, (w, _l) in known.items()}
    steps = [budget]
    # Readings a known answer pins (CLEVER through the Venus sign: EVE).
    for i, (word, _label) in known.items():
        letters = letters_key(word)
        if board.unknown_pictures():
            paths = trace(board, letters, steps, valid=data.is_word)
            for _p, r in paths[:1]:
                for pic, v in r.items():
                    board.readings.setdefault(pic, v)
    options = reading_options(data, vectors, board, [t for t, _s in clues]) if board.unknown_pictures() else None
    words = {}
    lists, counts = {}, {}
    for i, ((text, _spec), parts) in enumerate(zip(clues, specs)):
        if i in solved or parts is None:
            continue
        clue = _Clue(data, vectors, resolve_refs(text, answers))
        if i in known:
            letters = letters_key(known[i][0])
            key = ' '.join(letters[sum(parts[:j]):sum(parts[:j + 1])] for j in range(len(parts)))
            paths = trace(board, letters, steps) if len(letters) == sum(parts) else []
            items = [Item(key, data.zipf(key) or 0.0, paths)]
        else:
            items = [Item(*c) for c in candidates(data, board, parts, steps, options)]
            taken = {letters_key(w) for w, _l in known.values()}
            taken |= refused.get(i, set())
            items = [it for it in items if it.key.replace(' ', '') not in taken]
        for it in items:
            w = words.get(it.key)
            if w is None:
                w = words[it.key] = _Word(data, vectors, it.key)
            it.score, it.parts = score_word(clue, w, it.zipf, data)
            if it.built:
                it.score += W_BUILT
        items.sort(key=lambda it: (-it.score, -it.zipf, it.key))
        lists[i] = items
        counts[i] = len(items)
    pictures = {}
    if board.unknown_pictures():
        readings = picture_readings(board, {i: v for i, v in lists.items() if i not in known}, data, vectors)
        for p, ranked in readings.items():
            pictures[p] = ranked
            if not ranked:
                continue
            lead = ranked[0][0]
            for i, items in lists.items():
                if i in known:
                    continue
                for it in items:
                    r = it.reading(p)
                    if r is not None:
                        it.score += W_LEAD if r == lead else W_OTHER_READING
                items.sort(key=lambda it: (-it.score, -it.zipf, it.key))
    solved_cover = 0
    for i in solved:
        if i in known:
            paths = trace(board, letters_key(known[i][0]), steps)
            for p, _r in paths[:1]:
                for t in p:
                    solved_cover |= 1 << t
    board_rules(board, lists, solved_cover)
    leads = {}
    for i, items in lists.items():
        if i not in known:
            _rank(items)
            if items:
                second = _effective(items[1]) if len(items) > 1 else _effective(items[0]) - LONE_LEAD
                leads[i] = _effective(items[0]) - second
    # "Start here": the open clue whose best word is furthest ahead of its
    # next one. Solving it first takes tiles away and narrows the rest.
    start = max(leads, key=lambda i: (leads[i], -i)) + 1 if leads else None
    out = []
    for i, ((text, spec), parts) in enumerate(zip(clues, specs)):
        entry = {'n': i + 1, 'spec': spec_text(parts, '-' if '-' in str(spec) else ',') if parts else '',
                 'label': known[i][1] if i in known else 'guess',
                 'known': known[i][0].upper() if i in known else None,
                 'solved': i in solved, 'count': counts.get(i, 0), 'items': []}
        for it in lists.get(i, [])[:show]:
            path, reads = it.paths[0] if it.paths else ((), {})
            entry['items'].append({'word': it.key, 'path': list(path), 'readings': reads,
                                   'fits': it.fits, 'pinned': it.pinned})
        out.append(entry)
    pics = {p: {'reading': board.readings.get(p), 'name': board.names.get(p, ''),
                'options': [r for r, _e in pictures.get(p, [])[:3]]}
            for p in board.pictures()}
    return {'clues': out, 'pictures': pics, 'start': start, 'complete': steps[0] >= 0,
            'version': SOLVER_VERSION}
