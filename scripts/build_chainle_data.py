"""Build the Chainle solver's word data.

Writes datasets/chainle_data.npz, which functions/chainle.py and
routes/chainle.py load (arrays described in functions/chainle.py):

    vocab, zipf, flags   the words the solver knows, how common each is, and
                         which may be suggested
    vec, scale           GloVe word vectors, unit length, stored as int8
    base_*               each word's base forms, to spot a repeated clue
    link_*               compound words and two-word phrases joining two words
    syn_*, ant_*         WordNet synonyms and antonyms

Build-time only. It downloads public data into a cache folder outside the
repo, and the deployed app never fetches anything. Sources and licences are
recorded in datasets/CHAINLE_DATA.md:

    GloVe 2024 Wiki-Gigaword vectors (PDDL 1.0), Stanford NLP
    WordNet 3.0 (WordNet 3.0 licence)
    datasets/phrase_bank.tsv.gz, phrase_words.tsv.gz and phrase_blocklist.txt,
        built by scripts/build_phrase_bank.py (see datasets/PHRASE_BANK.md)
    ENABLE word list (public domain), via datasets/all_words_blossom.csv

Run manually to regenerate:

    python scripts/build_chainle_data.py              # 100-dimension vectors
    python scripts/build_chainle_data.py --dims 300   # another GloVe size

Set PHRASE_BANK_CONTACT (an email or URL) to add a contact to the
User-Agent, as Wikimedia and Princeton ask of automated clients.
"""

import argparse
import collections
import csv
import gzip
import io
import os
import re
import sys
import tarfile
import tempfile
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, os.pardir, 'datasets')
sys.path.insert(0, os.path.join(HERE, os.pardir))
sys.path.insert(0, HERE)

import build_phrase_bank as bpb  # noqa: E402  (its download cache and WordNet source)
from functions import chainle  # noqa: E402
from functions.phrase_solver import TAG_BIT, blocklist_hash, load_blocklist  # noqa: E402

GLOVE = 'https://nlp.stanford.edu/data/wordvecs/glove.2024.wikigiga.{dims}d.zip'
# The full WordNet 3.0 release: unlike the WNdb database-only tarball the
# phrase bank uses, it carries the irregular-form lists (FEET -> FOOT).
WORDNET = 'https://wordnetcode.princeton.edu/3.0/WordNet-3.0.tar.gz'

# The words the solver knows: clues can be rarer than the answers it gives.
MIN_LEN, MAX_LEN = 2, 15
VOCAB_ZIPF = 1.5
ANSWER_ZIPF = 2.0
ANSWER_MIN_LEN = 3

# Closed compounds: a common dictionary word made of two known words.
COMPOUND_ZIPF = 1.5
PART_ZIPF = 2.5
# Two-letter words that do make compounds (INSIDE, UPSET, ONLINE, BYPASS).
SHORT_PARTS = {'up', 'in', 'on', 'by'}
MIN_LINK = 0.5
# A link through a function word (BUT + TON) is rarely the intended one.
STOP_DAMP = 0.3
# Particles join almost anything (KEY OUT, PASS OFF): mostly phrasal verbs,
# not the compounds a bridge means.
PARTICLES = set('up down out off over under back away in on by around through about along '
                'across apart aside ahead'.split())
PARTICLE_DAMP = 0.6

# Two-word phrases from the phrase bank. The bank rates a phrase by how
# common its words are, not the phrase itself (TAKE FIRE rates like FIRE
# STATION), so a link's weight comes from the kinds of source that list it.
# An established compound is a dictionary noun (WordNet's things, foods and
# animals) and a dictionary phrase, and often a film or song title too; a
# phrase only one phrase list carries often isn't. Names alone barely count.
BANK_BASE = 1.2
BANK_KINDS = [
    (('THING', 'FOOD', 'ANIMAL'), 0.9),
    (('PHRASE', 'PROVERB'), 0.5),
    (('MOVIE', 'SONG', 'TV', 'BOOK', 'TITLE', 'GAME', 'ART', 'BAND'), 0.3),
    (('PLACE', 'LANDMARK', 'SPACE', 'EVENT'), 0.2),
]
BANK_NAMES_ONLY = 0.6

# Never suggested. Kept in the vocabulary, since a clue may still be one.
STOPWORDS = set("""
a an the and or but nor so yet if then than that this these those there here
which who whom whose what when where why how i me my mine myself we us our ours
ourselves you your yours yourself yourselves he him his himself she her hers
herself it its itself they them their theirs themselves am is are was were be
been being have has had having do does did doing done will would shall should
can could may might must ought of to for with at by from into onto upon about
above below between through during before after against among around without
within along across beyond toward towards via per not no yes also too very just
only even ever never always often still already again all any both each either
neither every few many more most much other others some such same own one ones
another anyone anything anybody someone something somebody nobody nothing none
everyone everything everybody as because since unless until while whereas
although though whether etc like unlike enough else rather quite almost perhaps
maybe instead however therefore thus hence indeed
""".split())


def damp(a, b, weight):
    """A link's weight after the function-word and particle discounts."""
    if a in STOPWORDS or b in STOPWORDS:
        weight *= STOP_DAMP
    if a in PARTICLES or b in PARTICLES:
        weight *= PARTICLE_DAMP
    return weight

# The game's published scoring rules count indefinite and broadly evaluative
# words for less in bridge rounds, so the solver does too.
BROAD = set("""
thing things stuff kind sort type way lot lots bit good great nice fine bad
best better worse worst awesome amazing cool okay wonderful terrible perfect
excellent fantastic horrible lovely
""".split())

# WordNet's morphy rules (morphy(7WN)): inflected ending -> base ending.
MORPHY = {
    'noun': [('s', ''), ('ses', 's'), ('xes', 'x'), ('zes', 'z'), ('ches', 'ch'), ('shes', 'sh'),
             ('men', 'man'), ('ies', 'y')],
    'verb': [('s', ''), ('ies', 'y'), ('es', 'e'), ('es', ''), ('ed', 'e'), ('ed', ''),
             ('ing', 'e'), ('ing', '')],
    'adj': [('er', ''), ('est', ''), ('er', 'e'), ('est', 'e')],
    'adv': [],
}
# For words WordNet doesn't list (EMAILS): plural and verb endings only, so
# FLOWER never becomes an inflection of FLOW.
FALLBACK = MORPHY['noun'] + MORPHY['verb']
POINTER_POS = {'n': 'noun', 'v': 'verb', 'a': 'adj', 's': 'adj', 'r': 'adv'}

WORD = re.compile(r'[a-z]+')


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --- inputs -------------------------------------------------------------------

def load_enable():
    with open(os.path.join(DATA, 'all_words_blossom.csv'), encoding='utf-8-sig') as fh:
        rows = csv.reader(fh)
        next(rows)
        return {row[1] for row in rows if WORD.fullmatch(row[1])}


def load_zipf():
    """word -> Zipf frequency, from wordfreq via the phrase bank's word list."""
    zipf = {}
    with gzip.open(os.path.join(DATA, 'phrase_words.tsv.gz'), 'rt', encoding='utf-8') as fh:
        next(fh)
        for line in fh:
            w, z = line.rstrip('\n').split('\t')
            zipf[w.lower()] = float(z)
    return zipf


def load_wordnet():
    """(synsets, lemmas, common, exceptions).

    synsets     {(pos, offset): (words, pointers)}, words as written
    lemmas      {pos: lowercase single-word lemmas}
    common      single-word lemmas WordNet writes in lower case (not names)
    exceptions  {pos: {inflected: [bases]}} from the *.exc lists
    """
    raw = bpb.fetch(WORDNET, 'WordNet-3.0.tar.gz')
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        files = {os.path.basename(m.name): tar.extractfile(m).read().decode('latin-1')
                 for m in tar.getmembers() if m.isfile() and '/dict/' in m.name}
    synsets, lemmas, common = {}, {pos: set() for pos in MORPHY}, set()
    for pos in MORPHY:
        for line in files[f'data.{pos}'].splitlines():
            if line.startswith('  '):
                continue
            f = line.split(' | ')[0].split()
            wcnt = int(f[3], 16)
            words = [re.sub(r'\(.*\)$', '', f[4 + 2 * i]) for i in range(wcnt)]
            p = 4 + 2 * wcnt
            pointers = [tuple(f[p + 1 + 4 * j:p + 5 + 4 * j]) for j in range(int(f[p]))]
            synsets[(pos, f[0])] = (words, pointers)
            for w in words:
                if WORD.fullmatch(w.lower()):
                    lemmas[pos].add(w.lower())
                    if w.islower():
                        common.add(w)
    exceptions = {pos: {} for pos in MORPHY}
    for pos in MORPHY:
        for line in files.get(f'{pos}.exc', '').splitlines():
            parts = line.lower().split()
            if len(parts) >= 2:
                exceptions[pos].setdefault(parts[0], []).extend(parts[1:])
    return synsets, lemmas, common, exceptions


def load_glove(dims, wanted):
    """{word: vector} for the wanted words, from the GloVe 2024 Wiki-Gigaword file."""
    raw = bpb.fetch(GLOVE.format(dims=dims), f'glove.2024.wikigiga.{dims}d.zip')
    vectors = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = next(n for n in zf.namelist() if n.endswith('.txt'))
        with zf.open(name) as fh:
            for line in io.TextIOWrapper(fh, encoding='utf-8', errors='replace'):
                word, _, rest = line.partition(' ')
                if word in wanted and word not in vectors:
                    values = rest.split()
                    if len(values) == dims:
                        vectors[word] = np.array(values, dtype=np.float32)
    return vectors


# --- word relations -------------------------------------------------------------

POS_BITS = {'noun': chainle.NOUN, 'verb': chainle.VERB, 'adj': chainle.ADJ, 'adv': chainle.ADV}


def forms_of(w, lemmas, exceptions, every, known):
    """(the base forms w inflects, its part-of-speech bits).

    Base forms come from WordNet's morphy, plus plural and verb endings
    (onto a word in `known`) for words WordNet doesn't list. Its parts of
    speech are those it is a WordNet lemma of, or an inflection of one
    (RUNNING: a verb)."""
    found, tags = set(), 0
    for p, rules in MORPHY.items():
        here = set(exceptions[p].get(w, ()))
        for end, base_end in rules:
            if w.endswith(end) and len(w) > len(end):
                base = w[:-len(end)] + base_end
                if base in lemmas[p]:
                    here.add(base)
        if here or w in lemmas[p]:
            tags |= POS_BITS[p]
        found |= here
    if w not in every:
        for end, base_end in FALLBACK:
            base = w[:-len(end)] + base_end
            if w.endswith(end) and len(base) >= 3 and base in known:
                found.add(base)
    found.discard(w)
    return found, tags


def word_forms(vocab, lemmas, exceptions):
    """({word: its base forms in the vocabulary}, {word: part-of-speech bits})."""
    every = set().union(*lemmas.values())
    bases, pos = {}, {}
    for w in vocab:
        found, tags = forms_of(w, lemmas, exceptions, every, vocab)
        found &= vocab
        if found:
            bases[w] = found
        if tags:
            pos[w] = tags
    return bases, pos


def synonyms_antonyms(synsets):
    """({word: synonyms}, {word: antonyms}) over single words.

    Synonyms share a synset, or an adjective is "similar to" the other's
    synset (BIG -> LARGE). Antonyms are WordNet's direct ones, and a
    satellite adjective takes its head's (HUGE -> SMALL, through LARGE).
    """
    def single(words):
        return [w.lower() for w in words if WORD.fullmatch(w.lower())]

    syn, direct = collections.defaultdict(set), collections.defaultdict(set)
    for (pos, _), (words, pointers) in synsets.items():
        here = single(words)
        for w in here:
            syn[w].update(x for x in here if x != w)
        for symbol, offset, ppos, src_tgt in pointers:
            other = synsets.get((POINTER_POS[ppos], offset))
            if other is None:
                continue
            if symbol == '&' and pos == 'adj':
                for w in here:
                    syn[w].update(single(other[0]))
            elif symbol == '!':
                s, t = int(src_tgt[:2], 16), int(src_tgt[2:], 16)
                if s and t:
                    a, b = words[s - 1].lower(), other[0][t - 1].lower()
                    if WORD.fullmatch(a) and WORD.fullmatch(b):
                        direct[a].add(b)
                        direct[b].add(a)
    ant = collections.defaultdict(set, {w: set(v) for w, v in direct.items()})
    for (pos, _), (words, pointers) in synsets.items():
        if pos != 'adj':
            continue
        for symbol, offset, ppos, _ in pointers:
            head = synsets.get((POINTER_POS[ppos], offset)) if symbol == '&' else None
            if head:
                opposite = set().union(*(direct.get(h, set()) for h in single(head[0])))
                for w in single(words):
                    ant[w] |= opposite
    return syn, ant


def compound_links(enable, vocab, zipf, vectors, lemmas, exceptions):
    """(a, b, weight, closed=True) for dictionary words made of two known
    words (SNOWBALL = SNOW + BALL), weighted by how common the compound is.

    Only base forms are split: BATHERS is BATHER plus an ending, not BATH +
    ERS (SNOWBALLS adds nothing SNOWBALL doesn't). A split that has nothing
    to do with its parts (CARPET = CAR + PET) counts for less: the
    compound's vector sits far from both parts'."""
    every = set().union(*lemmas.values())
    known = vocab | enable
    def unit(w):
        v = vectors.get(w)
        return v / (np.linalg.norm(v) or 1) if v is not None else None

    for word in enable:
        if not (5 <= len(word) <= MAX_LEN) or zipf.get(word, 0.0) < COMPOUND_ZIPF:
            continue
        if forms_of(word, lemmas, exceptions, every, known)[0]:
            continue
        for i in range(2, len(word) - 1):
            a, b = word[:i], word[i:]
            if a not in vocab or b not in vocab:
                continue
            if any(len(p) < 3 and p not in SHORT_PARTS for p in (a, b)):
                continue
            if min(zipf.get(a, 0.0), zipf.get(b, 0.0)) < PART_ZIPF:
                continue
            uw, ua, ub = unit(word), unit(a), unit(b)
            near = max(float(uw @ ua), float(uw @ ub)) if uw is not None else 0.2
            weight = damp(a, b, zipf[word] * min(1.0, 0.6 + max(near, 0.0)))
            if weight >= MIN_LINK:
                yield a, b, weight, True


def bank_links(vocab):
    """(a, b, weight, closed=False) for the phrase bank's two-word entries."""
    kinds = [(sum(TAG_BIT[t] for t in tags), bonus) for tags, bonus in BANK_KINDS]
    with gzip.open(os.path.join(DATA, 'phrase_bank.tsv.gz'), 'rt', encoding='utf-8') as fh:
        next(fh)
        for line in fh:
            key, mask, _, _ = line.rstrip('\n').split('\t')
            words = key.lower().split(' ')
            if len(words) != 2 or words[0] == words[1] or not all(w in vocab for w in words):
                continue
            listed = [bonus for bits, bonus in kinds if int(mask) & bits]
            weight = damp(words[0], words[1], BANK_BASE + sum(listed) if listed else BANK_NAMES_ONLY)
            if weight >= MIN_LINK:
                yield words[0], words[1], weight, False


# --- build ----------------------------------------------------------------------

def canaries(data):
    """Plain facts any sane build gets right. Returns the failures."""
    def i(word):
        n = data.id(word)
        if n < 0:
            raise KeyError(f'{word} is not in the vocabulary')
        return n

    def near(word, other, k):
        cos = chainle._cosines(data, [i(word)])[0]
        cos[i(word)] = -1
        return i(other) in np.argsort(-cos)[:k].tolist()

    checks = {
        'queen near king': lambda: near('king', 'queen', 10),
        'huge near big': lambda: near('big', 'huge', 10),
        'snow + ball link': lambda: data.link(i('snow'), i('ball')) is not None,
        'foot + ball link': lambda: data.link(i('foot'), i('ball')) is not None,
        'fire + station link': lambda: data.link(i('fire'), i('station')) is not None,
        'large a synonym of big': lambda: i('large') in data.synonyms(i('big')).tolist(),
        'small an antonym of large': lambda: i('small') in data.antonyms(i('large')).tolist(),
        'feet -> foot': lambda: i('foot') in data.bases(i('feet')),
        'running -> run': lambda: i('run') in data.bases(i('running')),
        'no ending split off a plural (BATH + ERS)':
            lambda: data.id('ers') < 0 or data.link(i('bath'), data.id('ers')) is None,
    }
    failed = []
    for name, check in checks.items():
        try:
            ok = check()
        except Exception as e:  # noqa: BLE001
            ok = False
            log(f'  canary {name}: {e}')
        if not ok:
            failed.append(name)
    return failed


def build(dims, out_dir):
    enable = load_enable()
    zipf = load_zipf()
    blocked = load_blocklist(os.path.join(DATA, 'phrase_blocklist.txt'))
    log('wordnet...')
    synsets, lemmas, common, exceptions = load_wordnet()

    words = {w for w in enable | set().union(*lemmas.values())
             if MIN_LEN <= len(w) <= MAX_LEN and zipf.get(w, 0.0) >= VOCAB_ZIPF
             and blocklist_hash(w.upper()) not in blocked}
    log(f'glove {dims}d...')
    vectors = load_glove(dims, words)
    vocab = set(vectors)
    log(f'vocabulary: {len(vocab)} of {len(words)} candidate words have vectors')

    bases, pos = word_forms(vocab, lemmas, exceptions)
    # Not an inflection of a two-letter word either (ERS, ESS): no game
    # takes those as words.
    answer = {w for w in vocab if zipf.get(w, 0.0) >= ANSWER_ZIPF and len(w) >= ANSWER_MIN_LEN
              and w not in STOPWORDS and (w in enable or w in common)
              and all(len(b) >= ANSWER_MIN_LEN for b in bases.get(w, ()))}
    syn, ant = synonyms_antonyms(synsets)

    links = list(compound_links(enable, vocab, zipf, vectors, lemmas, exceptions))
    n_closed = len(links)
    links += bank_links(vocab)
    log(f'links: {n_closed} from compound words, {len(links) - n_closed} from two-word phrases')

    arrays = chainle.pack(vectors, zipf, answer, BROAD & vocab, bases, links, syn, ant, pos)
    data = chainle.Data(**arrays)
    failed = canaries(data)
    if failed:
        sys.exit(f'canaries failed, nothing written: {failed}')

    path = os.path.join(out_dir, 'chainle_data.npz')
    np.savez_compressed(path, **arrays)
    in_memory = sum(a.nbytes for a in arrays.values())
    log(f'wrote {path}: {os.path.getsize(path) / 1e6:.1f} MB on disk, {in_memory / 1e6:.1f} MB loaded')
    log(f'  {data.size} words ({int((arrays["flags"] & chainle.ANSWER != 0).sum())} answers), '
        f'{len(arrays["link_ids"]) // 2} links, {len(arrays["syn_ids"])} synonym and '
        f'{len(arrays["ant_ids"])} antonym entries, {len(arrays["base_ids"])} base forms')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dims', type=int, default=100, choices=(50, 100, 200, 300),
                    help='GloVe vector size')
    ap.add_argument('--cache', default=os.path.join(tempfile.gettempdir(), 'chainle_data_cache'))
    ap.add_argument('--out', default=DATA)
    args = ap.parse_args()
    os.makedirs(args.cache, exist_ok=True)
    bpb.CACHE = args.cache
    bpb.UA = 'jj-apps-chainle-data-build/1.0 (offline dataset build)'
    contact = os.environ.get('PHRASE_BANK_CONTACT')
    if contact:
        bpb.UA = f'{bpb.UA[:-1]}; {contact})'
    build(args.dims, args.out)


if __name__ == '__main__':
    main()
