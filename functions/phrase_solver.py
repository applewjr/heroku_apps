"""Guess The Phrase solver engine. Pure functions over static data, no DB.

A board is the puzzle as the player sees it: letters already shown, `?` for
each blank tile, spaces between words. Every occurrence of a called letter is
shown at once, so a blank can never be a letter already on the board, nor a
letter the player tried that wasn't there. That one rule does most of the work.

Three static files back it (built by scripts/build_phrase_bank.py):
  phrase_bank     known phrases, bucketed by length, e.g. a 20-character board
                  only ever scans 20-character phrases
  phrase_words    fallback vocabulary when no known phrase fits
  phrase_bigrams  how often one word follows another across the known
                  phrases, so the fallback prefers words that go together
                  ("HOTEL CALIFORNIA") over words that merely fit

Buckets hold fixed-width records in one string, so a regex scans a whole
bucket in one call and a match's index is just its offset // record width.
The regex pins each space, so a bucket can mix word shapes. That keeps the
bank to about 20 bytes per phrase instead of a Python object each: ~600k
phrases in ~15 MB.
"""

import gzip
import hashlib
import heapq
import re
from array import array
from bisect import bisect_left

TAGS = ('MOVIE', 'SONG', 'TV', 'BOOK', 'TITLE', 'GAME', 'ART', 'BAND', 'PERSON',
        'CHARACTER', 'FICTION', 'PLACE', 'LANDMARK', 'SPACE', 'ANIMAL', 'FOOD',
        'EVENT', 'PROVERB', 'PHRASE', 'THING')
TAG_BIT = {t: 1 << i for i, t in enumerate(TAGS)}

LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
VOWELS = frozenset('AEIOU')
BLANKS = '?_'
# Shown on the board but not part of any word's letters. A phrase may also
# end in a question mark, which on a board is just another blank.
_DROP = re.compile(r"['\u2019.,!:;\"]")
_DROP_PHRASE = re.compile(r"['\u2019.,!:;\"?]")
# Break a word in two, the way a tile gap does.
_SPLIT = re.compile(r'[\s\-&/]+')

# Ranking. A phrase's popularity is ~20 * log10(Wikipedia sitelinks), so
# these are in the same units: a candidate outside the chosen category ranks
# as if it were 10^(25/20) ~ 18x less famous.
CATEGORY_PENALTY = 25.0
MASHUP_PENALTY = 12.0
# Per-request memory bounds (measured 2026-10-11 under 8 threads: holding
# every match instead of these caps cost ~20 MB more at the plateau, with no
# change in results on the benchmark).
MASHUP_SIDE_LIMIT = 2000
ADVICE_POOL = 2000
# How far letter advice trusts known phrases over the per-word fallback when
# both exist (the answer may not be in the bank at all).
BANK_TRUST = 0.8

FALLBACK_SLOT_LIMIT = 300
FALLBACK_BEAM = 200
# Words per board word that solve() hands back for the word-by-word hints.
SLOT_SHOW = 10
# Stupid-backoff bigram model, all log10: a pair the phrase list has seen
# scores its P(next | word); an unseen one, a tenth of the next word's
# overall frequency (its Zipf is log10 per billion words, hence the -9).
_BACKOFF = -1.0


def phrase_key(text):
    """Letters-only form used for matching: 'DON'T STOP' -> 'DONT STOP'."""
    text = _DROP_PHRASE.sub('', text.upper())
    words = [w for w in _SPLIT.split(text) if w]
    if not words or not all(w.isalpha() and w.isascii() for w in words):
        return ''
    return ' '.join(words)


def parse_board(board):
    """'?OTEL CALIF?RNIA' -> ('?OTEL', 'CALIF?RNIA').

    Blanks may be typed as ? or _. Punctuation the board shows (' , . !) is
    dropped and - & / split words, so either way of typing it matches.
    Raises ValueError on anything else.
    """
    text = _DROP.sub('', board.upper())
    for b in BLANKS:
        text = text.replace(b, '?')
    words = tuple(w for w in _SPLIT.split(text) if w)
    if not words:
        raise ValueError('empty board')
    for w in words:
        if not re.fullmatch(r'[A-Z?]+', w):
            raise ValueError(f'bad characters in {w!r}')
    return words


def excluded_letters(patterns, tried=()):
    """Letters no blank can be: everything shown, plus misses."""
    shown = {c for w in patterns for c in w if c != '?'}
    return shown | {c.upper() for c in tried if c.upper() in LETTERS}


def _word_regex(pattern, allowed):
    return ''.join(allowed if c == '?' else c for c in pattern)


def _allowed_class(exclude):
    keep = ''.join(c for c in LETTERS if c not in exclude)
    return f'[{keep}]' if keep else '(?!)'


# Popularity is stored in one byte, in quarter points (0-63.75).
_POP_SCALE = 4


class _Bucket:
    """Every phrase of one length, most popular first."""
    __slots__ = ('width', 'blob', 'pending', 'pop', 'tags', 'shown_at', 'shown_off', 'shown')

    def __init__(self, length):
        self.width = length + 1
        # While loading: joined chunks, plus keys not yet joined (a list of
        # 600k small strings would cost several times the finished blob).
        self.blob = []
        self.pending = []
        self.pop = array('B')
        self.tags = array('I')
        # The few records shown differently from their key (apostrophes,
        # hyphens): sorted record indexes, and offsets into one string.
        self.shown_at = array('I')
        self.shown_off = array('I')
        self.shown = []

    def add(self, key, display, mask, pop):
        if display and display != key:
            self.shown_at.append(len(self.pop))
            self.shown.append(display)
        self.pending.append(key)
        if len(self.pending) >= 4096:
            self.blob.append('\n'.join(self.pending) + '\n')
            self.pending = []
        self.pop.append(max(0, min(255, round(pop * _POP_SCALE))))
        self.tags.append(mask)

    def finish(self):
        if self.pending:
            self.blob.append('\n'.join(self.pending) + '\n')
        self.blob, self.pending = ''.join(self.blob), None
        offset = 0
        for d in self.shown:
            self.shown_off.append(offset)
            offset += len(d)
        self.shown = ''.join(self.shown)

    def display(self, i, key):
        j = bisect_left(self.shown_at, i)
        if j == len(self.shown_at) or self.shown_at[j] != i:
            return key
        end = self.shown_off[j + 1] if j + 1 < len(self.shown_off) else len(self.shown)
        return self.shown[self.shown_off[j]:end]


class PhraseBank:
    """Known phrases bucketed by length (letters plus spaces)."""

    def __init__(self, rows=()):
        """rows: iterable of (display, tag mask, popularity), any order."""
        self.buckets = {}
        self.size = 0
        seen = set()
        for display, mask, pop in sorted(rows, key=lambda r: -r[2]):
            key = phrase_key(display)
            if key and key not in seen:
                seen.add(key)
                self._add(key, display, mask, pop)
        self._finish()

    def _add(self, key, display, mask, pop):
        bucket = self.buckets.get(len(key))
        if bucket is None:
            bucket = self.buckets[len(key)] = _Bucket(len(key))
        bucket.add(key, display, mask, pop)
        self.size += 1

    def _finish(self):
        for bucket in self.buckets.values():
            bucket.finish()

    @classmethod
    def from_file(cls, path):
        """Stream the build's file: one row per key, already sorted by
        popularity, key precomputed (display only where it differs)."""
        bank = cls()
        with gzip.open(path, 'rt', encoding='utf-8') as fh:
            next(fh)
            for line in fh:
                key, mask, pop, display = line.rstrip('\n').split('\t')
                bank._add(key, display, int(mask), float(pop))
        bank._finish()
        return bank

    def search_top(self, patterns, exclude, tag_mask, k):
        """(how many phrases fit, the best k as (key, display, score)).

        In-category phrases score their popularity, the rest CATEGORY_PENALTY
        less. A bucket is sorted by popularity, so each group's first k are
        its best: every other fit is only counted, never built. A blank
        board can fit 20k+ phrases, and a list of them all is what used to
        make a request cost megabytes.
        """
        bucket = self.buckets.get(sum(map(len, patterns)) + len(patterns) - 1)
        if bucket is None:
            return 0, []
        allowed = _allowed_class(exclude)
        rx = re.compile('^' + ' '.join(_word_regex(p, allowed) for p in patterns) + '$', re.M)
        total, inside, outside, w = 0, [], [], bucket.width
        for m in rx.finditer(bucket.blob):
            total += 1
            i = m.start() // w
            if not tag_mask or bucket.tags[i] & tag_mask:
                if len(inside) < k:
                    inside.append(i)
            elif len(outside) < k:
                outside.append(i)
        rows = []
        for idx, penalty in ((inside, 0.0), (outside, CATEGORY_PENALTY)):
            for i in idx:
                key = bucket.blob[i * w:(i + 1) * w - 1]
                rows.append((key, bucket.display(i, key), bucket.pop[i] / _POP_SCALE - penalty))
        rows.sort(key=lambda r: -r[2])
        return total, rows[:k]

    def search(self, patterns, exclude, limit=None):
        """Phrases fitting the patterns, most popular first.

        Returns a list of (key, display, pop, tag mask).
        """
        bucket = self.buckets.get(sum(map(len, patterns)) + len(patterns) - 1)
        if bucket is None:
            return []
        allowed = _allowed_class(exclude)
        rx = re.compile('^' + ' '.join(_word_regex(p, allowed) for p in patterns) + '$', re.M)
        out = []
        for m in rx.finditer(bucket.blob):
            i = m.start() // bucket.width
            key = m.group()
            out.append((key, bucket.display(i, key), bucket.pop[i] / _POP_SCALE, bucket.tags[i]))
            if limit and len(out) >= limit:
                break
        return out


def mashups(bank, patterns, exclude, limit=MASHUP_SIDE_LIMIT):
    """Before-and-after phrases: FIRE TRUCK + TRUCK STOP -> FIRE TRUCK STOP.

    Two known phrases of 2+ words that share one word, the first's last and
    the second's first. Returns (key, display, score).
    """
    n = len(patterns)
    found = {}
    for k in range(1, n - 1):
        left = bank.search(patterns[:k + 1], exclude, limit)
        if not left:
            continue
        right = bank.search(patterns[k:], exclude, limit)
        by_first = {}
        for key, display, pop, _ in right:
            by_first.setdefault(key.split(' ', 1)[0], []).append((key, display, pop))
        for lkey, ldisp, lpop, _ in left:
            shared = lkey.rsplit(' ', 1)[1]
            for rkey, rdisp, rpop in by_first.get(shared, ()):
                key = lkey + rkey[len(shared):]
                display = ldisp + rdisp[len(rdisp.split(' ', 1)[0]):]
                score = (lpop + rpop) / 2
                if score > found.get(key, (None, -1e9))[1]:
                    found[key] = (display, score)
    return sorted(((k, d, s) for k, (d, s) in found.items()), key=lambda t: -t[2])


# --- fallback: per-word candidates + a bigram model -----------------------

class WordList:
    """Fallback vocabulary bucketed by length, most common first."""

    def __init__(self, rows=(), presorted=False):
        """rows: (word, zipf). presorted: already most common first."""
        parts, self.zipf = {}, {}
        for word, zipf in (rows if presorted else sorted(rows, key=lambda r: (-r[1], r[0]))):
            n = len(word)
            if n not in parts:
                parts[n] = ([], [])
                self.zipf[n] = array('f')
            done, pending = parts[n]
            pending.append(word)
            if len(pending) >= 4096:
                done.append('\n'.join(pending) + '\n')
                pending.clear()
            self.zipf[n].append(zipf)
        self.buckets = {n: ''.join(done) + ('\n'.join(pending) + '\n' if pending else '')
                        for n, (done, pending) in parts.items()}

    @classmethod
    def from_file(cls, path):
        def rows():
            with gzip.open(path, 'rt', encoding='utf-8') as fh:
                next(fh)
                for line in fh:
                    w, z = line.rstrip('\n').split('\t')
                    yield w, float(z)
        return cls(rows(), presorted=True)

    def match(self, pattern, exclude, limit=FALLBACK_SLOT_LIMIT):
        """Words fitting one word's pattern, most common first: [(word, zipf)].

        A fully shown word is its own only candidate, listed or not.
        """
        blob = self.buckets.get(len(pattern), '')
        rx = re.compile('^' + _word_regex(pattern, _allowed_class(exclude)) + '$', re.M)
        out, width = [], len(pattern) + 1
        for m in rx.finditer(blob):
            out.append((m.group(), self.zipf[len(pattern)][m.start() // width]))
            if len(out) >= limit:
                break
        if not out and '?' not in pattern:
            out = [(pattern, 0.0)]
        return out


class Bigrams:
    """log10 P(next word | word) for pairs seen in the phrase list, as one
    sorted array of 64-bit ints.

    Each int packs a 47-bit hash of the pair over -log10 P in hundredths
    (16 bits), so a lookup is one bisect. A false hit needs two pairs to
    share 47 bits of hash: about 1 in a million per lookup.
    """
    _VAL_BITS = 16
    _HASH_MASK = (1 << 47) - 1

    def __init__(self, rows):
        self.keys = array('q', sorted(
            ((hash(p) & self._HASH_MASK) << self._VAL_BITS) | min(65535, round(-c * 100))
            for p, c in rows))

    @classmethod
    def from_file(cls, path):
        def rows():
            with gzip.open(path, 'rt', encoding='utf-8') as fh:
                next(fh)
                for line in fh:
                    p, c = line.rstrip('\n').split('\t')
                    yield p, float(c)
        return cls(rows())

    def get(self, a, b):
        h = hash(f'{a} {b}') & self._HASH_MASK
        i = bisect_left(self.keys, h << self._VAL_BITS)
        if i < len(self.keys) and self.keys[i] >> self._VAL_BITS == h:
            return -(self.keys[i] & 0xFFFF) / 100
        return None


def _bigram_logp(bigrams, prev, word, zipf):
    logp = bigrams.get(prev, word) if bigrams else None
    return logp if logp is not None else _BACKOFF + zipf - 9


def fallback_phrases(slots, bigrams, beam=FALLBACK_BEAM, top=20):
    """Best word sequences from per-slot candidates, by a bigram model.

    slots: per word, a list of (word, zipf). Returns [(text, log10 score)].
    """
    if not slots or any(not s for s in slots):
        return []
    states = [((w,), z - 9) for w, z in slots[0]]
    states = heapq.nlargest(beam, states, key=lambda s: s[1])
    for cands in slots[1:]:
        # A generator: nlargest keeps `beam` states, never all beam x slot
        # extensions (60k a step) at once.
        states = heapq.nlargest(
            beam,
            ((words + (w,), score + _bigram_logp(bigrams, words[-1], w, z))
             for words, score in states for w, z in cands),
            key=lambda s: s[1])
    return [(' '.join(words), score) for words, score in states[:top]]


# --- letter advice --------------------------------------------------------

def _advice_from_weighted(items, patterns, exclude):
    """items: (key, weight). Returns {letter: (p_hit, expected tiles)}."""
    blanks = [i for i, c in enumerate(' '.join(patterns)) if c == '?']
    total = 0.0
    hit = dict.fromkeys(LETTERS, 0.0)
    count = dict.fromkeys(LETTERS, 0.0)
    for key, w in items:
        total += w
        seen = {}
        for i in blanks:
            seen[key[i]] = seen.get(key[i], 0) + 1
        for c, n in seen.items():
            hit[c] += w
            count[c] += w * n
    if not total:
        return {}
    return {c: (hit[c] / total, count[c] / total) for c in LETTERS if c not in exclude}


def _advice_per_slot(slots, patterns, exclude):
    """Independent per-word letter odds: P(L) = 1 - prod(1 - P_i(L))."""
    miss = dict.fromkeys(LETTERS, 1.0)
    expect = dict.fromkeys(LETTERS, 0.0)
    for pattern, cands in zip(patterns, slots):
        if '?' not in pattern or not cands:
            continue
        adv = _advice_from_weighted([(w, 10 ** z) for w, z in cands], (pattern,), exclude)
        for c, (p, e) in adv.items():
            miss[c] *= 1 - p
            expect[c] += e
    return {c: (1 - miss[c], expect[c]) for c in LETTERS if c not in exclude}


def _blend(a, b, wa):
    if not a:
        return b
    if not b:
        return a
    return {c: (wa * a[c][0] + (1 - wa) * b[c][0], wa * a[c][1] + (1 - wa) * b[c][1]) for c in a}


def weight(score):
    """Popularity score -> relative likelihood (back to the sitelinks scale)."""
    return 10 ** (score / 20)


# --- the whole query -----------------------------------------------------

def solve(data, board, tried=(), tags=(), mashup='also', limit=50):
    """Everything the page needs for one board state.

    data   -- object with .bank (PhraseBank), .words (WordList), .bigrams
    tags   -- category tags to favour; empty = no preference
    mashup -- 'no', 'also' (ranked below plain phrases) or 'only'

    Returns a dict:
      candidates  [(display, score, share)], best first, at most `limit`;
                  share is the candidate's estimated chance of being the
                  answer, among the known phrases that fit
      total       how many known phrases fit (before the limit)
      fallback    [(text, score, share)] best guesses when nothing known fits
      slots       per word, its most common fitting words (a fully shown
                  word is just itself)
      advice      [(letter, p_hit, expected tiles)], best first
      blanks      how many tiles are still hidden
    """
    patterns = parse_board(board)
    exclude = excluded_letters(patterns, tried)
    tag_mask = 0
    for t in tags:
        tag_mask |= TAG_BIT[t]

    total, ranked = (0, []) if mashup == 'only' else data.bank.search_top(
        patterns, exclude, tag_mask, ADVICE_POOL)
    if mashup != 'no' and len(patterns) >= 3:
        penalty = 0.0 if mashup == 'only' else MASHUP_PENALTY
        have = {k for k, _, _ in ranked}
        extra = [(k, d, s - penalty) for k, d, s in mashups(data.bank, patterns, exclude)
                 if k not in have]
        total += len(extra)
        ranked = sorted(ranked + extra, key=lambda t: -t[2])[:ADVICE_POOL]

    blanks = sum(p.count('?') for p in patterns)
    result = {'total': total, 'blanks': blanks, 'fallback': [], 'candidates': []}

    slots = [data.words.match(p, exclude) for p in patterns]
    result['slots'] = [[w for w, _ in s[:SLOT_SHOW]] for s in slots]
    slot_adv = _advice_per_slot(slots, patterns, exclude)
    if ranked:
        top = ranked[0][2]
        pool = [(k, weight(s - top)) for k, _, s in ranked[:ADVICE_POOL]]
        total_w = sum(w for _, w in pool)
        result['candidates'] = [(d, round(s, 1), round(weight(s - top) / total_w, 4))
                                for _, d, s in ranked[:limit]]
        advice = _blend(_advice_from_weighted(pool, patterns, exclude), slot_adv, BANK_TRUST)
    else:
        advice = slot_adv
        guesses = fallback_phrases(slots, data.bigrams)
        if guesses:
            best = guesses[0][1]
            total_w = sum(10 ** (s - best) for _, s in guesses)
            result['fallback'] = [(t, round(s, 2), round(10 ** (s - best) / total_w, 4))
                                  for t, s in guesses]
    # Sort and filter before rounding: a letter only a long-shot candidate
    # needs can round to 0.000, and dropping it would leave no advice while
    # tiles are still hidden.
    ranked_letters = sorted(((c, p, e) for c, (p, e) in advice.items() if p > 0),
                            key=lambda t: (-t[1], -t[2]))
    result['advice'] = [(c, round(p, 3), round(e, 2)) for c, p, e in ranked_letters]
    return result


class SolverData:
    """The three static files, loaded together."""

    def __init__(self, bank_path, words_path, bigrams_path):
        self.bank = PhraseBank.from_file(bank_path)
        self.words = WordList.from_file(words_path)
        self.bigrams = Bigrams.from_file(bigrams_path)


# --- categories -------------------------------------------------------------

# The page's category menu: (id, label, tags to favour, mashup mode). Bored
# Panda's category changes daily ("STORYTIME CLASSIC", "AROUND THE HOUSE");
# a player picks the nearest of these, or "Not sure".
CATEGORIES = (
    ('any', 'Not sure / other', (), 'also'),
    ('phrase', 'Phrase, idiom or saying', ('PHRASE', 'PROVERB'), 'no'),
    ('proverb', 'Proverb', ('PROVERB', 'PHRASE'), 'no'),
    ('quote', 'Quote', ('PHRASE',), 'no'),
    ('mashup', 'Phrase mashup (before & after)', (), 'only'),
    ('movie', 'Movie', ('MOVIE',), 'no'),
    ('tv', 'TV show or cartoon', ('TV', 'CHARACTER'), 'no'),
    ('song', 'Song', ('SONG', 'TITLE'), 'no'),
    ('band', 'Band or singer', ('BAND', 'PERSON'), 'no'),
    ('book', 'Book or story', ('BOOK',), 'no'),
    ('game', 'Video or board game', ('GAME',), 'no'),
    ('title', 'Other title', ('TITLE', 'GAME', 'MOVIE', 'TV', 'BOOK', 'SONG'), 'no'),
    ('art', 'Painting or artwork', ('ART',), 'no'),
    ('person', 'Famous person or profession', ('PERSON',), 'no'),
    ('character', 'Fictional character', ('CHARACTER',), 'no'),
    ('fictional_place', 'Fictional place', ('FICTION',), 'no'),
    ('place', 'Place (on the map)', ('PLACE', 'LANDMARK'), 'no'),
    ('landmark', 'Landmark or natural wonder', ('LANDMARK', 'PLACE'), 'no'),
    ('space', 'Space', ('SPACE',), 'no'),
    ('animal', 'Animal', ('ANIMAL',), 'no'),
    ('food', 'Food or drink', ('FOOD',), 'no'),
    ('thing', 'Thing, hobby or around the house', ('THING', 'PHRASE'), 'no'),
    ('event', 'Event, holiday or history', ('EVENT',), 'no'),
)
CATEGORY_BY_ID = {c[0]: c for c in CATEGORIES}


# --- players' corrections -------------------------------------------------

# A phrase players added scores like an item with ~100 Wikipedia sitelinks,
# in every category: it is a real answer someone just solved.
ADDED_POP = 40.0
ALL_TAGS = (1 << len(TAGS)) - 1


class OverlayBank:
    """The shipped bank with players' and the admin's changes on top:
    `added` keys join it, `removed` keys drop out. Searches like a PhraseBank."""

    def __init__(self, base, added=(), removed=()):
        self.base = base
        self.removed = frozenset(removed)
        self.extra = PhraseBank((k, ALL_TAGS, ADDED_POP) for k in added if k not in self.removed)
        self.size = base.size + self.extra.size

    def search(self, patterns, exclude, limit=None):
        out = self.base.search(patterns, exclude, limit)
        if self.removed:
            out = [r for r in out if r[0] not in self.removed]
        if self.extra.size:
            have = {r[0] for r in out}
            out += [r for r in self.extra.search(patterns, exclude, limit) if r[0] not in have]
            out.sort(key=lambda r: -r[2])
            if limit:
                out = out[:limit]
        return out

    def search_top(self, patterns, exclude, tag_mask, k):
        """As PhraseBank.search_top. Added phrases are in every category.
        A removed phrase outside the base bank's best k is still counted in
        the total: it is never shown, and finding it would mean building
        every match again."""
        total, rows = self.base.search_top(patterns, exclude, tag_mask, k)
        if self.removed:
            kept = [r for r in rows if r[0] not in self.removed]
            total -= len(rows) - len(kept)
            rows = kept
        if self.extra.size:
            have = {r[0] for r in rows}
            _, extra = self.extra.search_top(patterns, exclude, 0, k)
            extra = [r for r in extra if r[0] not in have]
            total += len(extra)
            rows = sorted(rows + extra, key=lambda r: -r[2])[:k]
        return total, rows

    def __contains__(self, key):
        patterns = tuple(key.split(' '))
        return any(k == key for k, _, _, _ in self.search(patterns, set()))


def fits_board(key, patterns, exclude):
    """Could this phrase be the answer to this board?"""
    words = key.split(' ')
    if len(words) != len(patterns):
        return False
    for word, pattern in zip(words, patterns):
        if len(word) != len(pattern):
            return False
        for c, p in zip(word, pattern):
            if (p != '?' and c != p) or (p == '?' and c in exclude):
                return False
    return True


# --- offensive-term filter ------------------------------------------------

def blocklist_hash(key):
    """How datasets/phrase_blocklist.txt stores a term: hashed, so the repo
    doesn't carry the words themselves."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def load_blocklist(path):
    with open(path, encoding='utf-8') as fh:
        return frozenset(line.strip() for line in fh if line.strip() and not line.startswith('#'))


def is_blocked(key, blocklist, longest=4):
    """True if any run of up to `longest` words in the phrase is a listed term."""
    words = key.split(' ')
    for n in range(1, min(longest, len(words)) + 1):
        for i in range(len(words) - n + 1):
            if blocklist_hash(' '.join(words[i:i + n])) in blocklist:
                return True
    return False
