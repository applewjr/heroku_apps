"""Build the Firsts solver's word data.

Writes datasets/firsts_data.npz, which functions/firsts.py (Data) loads:

    lex_<n>        the answers the solver can suggest, n letters each, most
                   popular first
    lem_*, syn_*   WordNet 3.0 lemmas, their senses, and each sense's words:
                   its definition, its lemmas and its parents' lemmas
    tok_*          those words' stems and IDF

Build-time only. It downloads WordNet 3.0 into a cache folder outside the
repo (the same tarball scripts/build_chainle_data.py uses), and the deployed
app never fetches anything. Sources and licences are recorded in
datasets/FIRSTS_DATA.md:

    WordNet 3.0 (WordNet 3.0 licence)
    datasets/phrase_bank.tsv.gz, phrase_words.tsv.gz and phrase_blocklist.txt,
        built by scripts/build_phrase_bank.py (see datasets/PHRASE_BANK.md)
    ENABLE word list (public domain), via datasets/all_words_blossom.csv

Run manually to regenerate:

    python scripts/build_firsts_data.py
"""

import argparse
import collections
import csv
import gzip
import io
import math
import os
import re
import sys
import tarfile
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, os.pardir, 'datasets')
sys.path.insert(0, os.path.join(HERE, os.pardir))
sys.path.insert(0, HERE)

import build_phrase_bank as bpb  # noqa: E402  (its download cache)
from functions import firsts  # noqa: E402
from functions.phrase_solver import TAG_BIT, blocklist_hash, is_blocked, load_blocklist, phrase_key  # noqa: E402

WORDNET = 'https://wordnetcode.princeton.edu/3.0/WordNet-3.0.tar.gz'
POS = ('noun', 'verb', 'adj', 'adv')
# A sense's words also take its parents' lemmas (hypernyms, instance-of)
# and, for adjectives, the ones it is similar to.
PARENT_POINTERS = {'@', '@i', '&'}

# Which answers to list. Popularity is Zipf (log10 per billion words) for
# single words; the phrase bank's own popularity (0-53) is divided by
# BANK_TO_ZIPF to sit on the same scale (ICE AGE 38 -> 3.8).
MIN_LETTERS, MAX_LETTERS = 2, firsts.MAX_LETTERS
SINGLE_ZIPF = 1.5          # phrase_words entries at least this common
BANK_SINGLE_POP = 10.0
# Phrases: everyday ones (sayings, things, food, animals: HOLY COW, ICE
# CREAM) from BANK_MULTI_POP up; names and titles (people, places, films,
# songs) only when famous, from BANK_NAME_POP up.
EVERYDAY_TAGS = ('PHRASE', 'PROVERB', 'THING', 'FOOD', 'ANIMAL')
BANK_MULTI_POP = 14.0
BANK_NAME_POP = 30.0
BANK_TO_ZIPF = 10.0
WORDNET_ONLY_ZIPF = 1.0    # a WordNet word no frequency list has
ENABLE_ONLY_ZIPF = 0.5
MAX_LEMMA_LETTERS = 20
# What the arrays may take in memory, all together.
MAX_BYTES = 12 * 1024 * 1024

# Checks the built data must pass (ordinary words, nothing from Firsts).
CANARY_SENSES = (('CAT', 'felin'), ('VOLCANO', 'erupt'), ('ICECREAM', 'frozen'), ('ANKARA', 'capital'))
CANARY_ANSWERS = ('CAT', 'VOLCANO', 'ICE CREAM', 'NEW YORK', 'HOT DOG', 'ANKARA')


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --- WordNet -----------------------------------------------------------------------

def load_wordnet():
    """(synsets, senses).

    synsets  {(pos, offset): (lemmas as written, pointers, gloss)}
    senses   {lemma, lower case with underscores: [(rank, (pos, offset))]}
    """
    raw = bpb.fetch(WORDNET, 'WordNet-3.0.tar.gz')
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        files = {os.path.basename(m.name): tar.extractfile(m).read().decode('latin-1')
                 for m in tar.getmembers() if m.isfile() and '/dict/' in m.name}
    synsets = {}
    for pos in POS:
        for line in files[f'data.{pos}'].splitlines():
            if line.startswith('  '):
                continue
            head, _, gloss = line.partition(' | ')
            f = head.split()
            wcnt = int(f[3], 16)
            words = [re.sub(r'\(.*\)$', '', f[4 + 2 * i]) for i in range(wcnt)]
            p = 4 + 2 * wcnt
            pointers = []
            for j in range(int(f[p])):
                sym, off, ppos = f[p + 1 + 4 * j:p + 4 + 4 * j]
                pointers.append((sym, off, {'n': 'noun', 'v': 'verb', 'a': 'adj', 's': 'adj', 'r': 'adv'}[ppos]))
            synsets[(pos, f[0])] = (words, pointers, gloss.strip())
    senses = collections.defaultdict(list)
    for pos in POS:
        for line in files[f'index.{pos}'].splitlines():
            if line.startswith('  '):
                continue
            f = line.split()
            for rank, off in enumerate(f[-int(f[2]):]):
                senses[f[0]].append((rank, (pos, off)))
    return synsets, senses


def definition(gloss):
    """A gloss's definitions, without its quoted examples."""
    parts = [p.strip() for p in gloss.split(';')]
    return ' '.join(p for p in parts if p and not p.startswith('"'))


def lemma_words(lemma):
    return lemma.replace('_', ' ').replace('-', ' ')


def build_senses(synsets, senses):
    """The lem_*, syn_* and tok_* arrays."""
    sids = {key: n for n, key in enumerate(sorted(synsets))}
    tokens = []
    for key in sorted(synsets):
        words, pointers, gloss = synsets[key]
        seen, toks = set(), []

        def add(text):
            for s in firsts.content_stems(text):
                if s not in seen:
                    seen.add(s)
                    toks.append(s)
        add(definition(gloss))
        for w in words:
            add(lemma_words(w))
        for sym, off, ppos in pointers:
            if sym in PARENT_POINTERS and (ppos, off) in synsets:
                for w in synsets[(ppos, off)][0]:
                    add(lemma_words(w))
        tokens.append(toks)
    df = collections.Counter(t for toks in tokens for t in toks)
    # A word in one definition only, and in no lemma, can't link a clue to
    # an answer twice: dropped, which keeps token ids to 16 bits.
    lemma_stems = {s for words, _p, _g in synsets.values() for w in words
                   for s in firsts.content_stems(lemma_words(w))}
    keep = {t for t, n in df.items() if n > 1 or t in lemma_stems}
    tokens = [[t for t in toks if t in keep] for toks in tokens]
    vocab = sorted(keep)
    tid = {t: i for i, t in enumerate(vocab)}
    n_syn = len(tokens)
    if len(vocab) >= 1 << 17:
        sys.exit(f'{len(vocab)} tokens: token ids are stored in 17 bits')
    syn_ptr = np.zeros(n_syn + 1, dtype=np.uint32)
    flat = []
    for i, toks in enumerate(tokens):
        flat.extend(tid[t] for t in toks)
        syn_ptr[i + 1] = len(flat)
    flat = np.array(flat, dtype=np.uint32)
    idf = np.array([min(255, round(16 * math.log(n_syn / df[t]))) for t in vocab], dtype=np.uint8)

    by_key = collections.defaultdict(list)
    for lemma, entries in senses.items():
        key = firsts.letters_key(lemma_words(lemma))
        if MIN_LETTERS <= len(key) <= MAX_LEMMA_LETTERS:
            by_key[key].extend((rank, sids[s]) for rank, s in entries if s in sids)
    keys = sorted(by_key)
    lem_ptr = np.zeros(len(keys) + 1, dtype=np.uint32)
    lem_syn = []
    for i, k in enumerate(keys):
        order, seen = [], set()
        for _rank, s in sorted(by_key[k]):
            if s not in seen:
                seen.add(s)
                order.append(s)
        lem_syn.extend(order)
        lem_ptr[i + 1] = len(lem_syn)
    lem_blob, lem_off = pack_strings(keys)
    tok_blob, tok_off = pack_strings(vocab)
    return {
        'lem_blob': lem_blob, 'lem_off': lem_off, 'lem_ptr': lem_ptr,
        'lem_syn': np.array(lem_syn, dtype=np.uint32),
        # Token ids in 17 bits: the low 16, and the top bit packed 8 to a byte.
        'syn_ptr': syn_ptr, 'syn_tok': (flat & 0xFFFF).astype(np.uint16),
        'syn_tok_hi': np.packbits((flat >> 16).astype(np.uint8)),
        'tok_blob': tok_blob, 'tok_off': tok_off, 'tok_idf': idf,
    }, keys


def pack_strings(strings):
    """Sorted ASCII strings as one uint8 blob plus uint32 offsets."""
    off = np.zeros(len(strings) + 1, dtype=np.uint32)
    parts, pos = [], 0
    for i, s in enumerate(strings):
        b = s.encode('ascii')
        parts.append(b)
        pos += len(b)
        off[i + 1] = pos
    return np.frombuffer(b''.join(parts), dtype=np.uint8).copy(), off


# --- the answer list ----------------------------------------------------------------

def load_answers(synsets, blocked):
    """{KEY: Zipf}, KEY upper case with spaces between words."""
    zipf = {}
    with gzip.open(os.path.join(DATA, 'phrase_words.tsv.gz'), 'rt', encoding='utf-8') as fh:
        next(fh)
        for line in fh:
            w, z = line.rstrip('\n').split('\t')
            zipf[w.upper()] = float(z)
    out = {}

    def add(key, z):
        letters = key.replace(' ', '')
        if not MIN_LETTERS <= len(letters) <= MAX_LETTERS or not letters.isalpha():
            return
        if (' ' in key and is_blocked(key, blocked)) or blocklist_hash(key) in blocked:
            return
        if out.get(key, -1.0) < z:
            out[key] = z

    for w, z in zipf.items():
        if z >= SINGLE_ZIPF and w.isalpha():
            add(w, z)
    with open(os.path.join(DATA, 'all_words_blossom.csv'), encoding='utf-8') as fh:
        rows = csv.reader(fh)
        next(rows)
        for row in rows:
            w = row[0].upper()
            add(w, zipf.get(w, ENABLE_ONLY_ZIPF))
    everyday = sum(TAG_BIT[t] for t in EVERYDAY_TAGS)
    with gzip.open(os.path.join(DATA, 'phrase_bank.tsv.gz'), 'rt', encoding='utf-8') as fh:
        next(fh)
        for line in fh:
            key, tags, pop, _display = (line.rstrip('\n').split('\t') + [''])[:4]
            pop = float(pop)
            if ' ' in key:
                floor = BANK_MULTI_POP if int(tags) & everyday else BANK_NAME_POP
            else:
                floor = BANK_SINGLE_POP
            if pop >= floor:
                add(key, max(zipf.get(key, 0.0), pop / BANK_TO_ZIPF))
    for words, _pointers, _gloss in synsets.values():
        for w in words:
            key = phrase_key(lemma_words(w))
            if key and key not in out:
                add(key, zipf.get(key, WORDNET_ONLY_ZIPF))
    return out


def pack_lexicon(answers):
    """{'lex_<n>': uint8 blob} of "KEYp" lines (p: firsts.pop_char), most popular first."""
    buckets = collections.defaultdict(list)
    for key, z in answers.items():
        buckets[len(key.replace(' ', ''))].append((key, z))
    out = {}
    for n, rows in sorted(buckets.items()):
        rows.sort(key=lambda r: (-r[1], r[0]))
        text = ''.join(f'{k}{firsts.pop_char(z)}\n' for k, z in rows)
        out[f'lex_{n}'] = np.frombuffer(text.encode('ascii'), dtype=np.uint8).copy()
    return out


# --- build --------------------------------------------------------------------

def canaries(data):
    failed = []
    for key, s in CANARY_SENSES:
        t = data.token(s)
        if t < 0 or t not in data.bag(key):
            failed.append(f'{key} senses lack {s}')
    for key in CANARY_ANSWERS:
        if data.zipf(key) is None:
            failed.append(f'{key} not listed')
    return failed


def build(out_dir):
    blocked = load_blocklist(os.path.join(DATA, 'phrase_blocklist.txt'))
    log('wordnet...')
    synsets, senses = load_wordnet()
    arrays, lemma_keys = build_senses(synsets, senses)
    log(f'  {len(synsets)} synsets, {len(lemma_keys)} lemma keys, {len(arrays["tok_idf"])} tokens, '
        f'{len(arrays["syn_tok"])} sense words')
    log('answers...')
    answers = load_answers(synsets, blocked)
    multi = sum(1 for k in answers if ' ' in k)
    log(f'  {len(answers)} answers ({multi} with more than one word)')
    arrays.update(pack_lexicon(answers))
    arrays['version'] = np.array(f'firsts-data {firsts.SOLVER_VERSION}')
    size = sum(int(a.nbytes) for a in arrays.values())
    lex = sum(int(a.nbytes) for k, a in arrays.items() if k.startswith('lex_'))
    log(f'in memory: {size / 2**20:.2f} MiB ({lex / 2**20:.2f} MiB answer list, '
        f'{(size - lex) / 2**20:.2f} MiB WordNet)')
    if size > MAX_BYTES:
        sys.exit(f'over the {MAX_BYTES / 2**20:.0f} MiB budget: raise the popularity floors')
    data = firsts.Data(arrays)
    failed = canaries(data)
    if failed:
        sys.exit(f'canaries failed, nothing written: {failed}')
    path = os.path.join(out_dir, 'firsts_data.npz')
    np.savez_compressed(path, **arrays)
    log(f'wrote {path}: {os.path.getsize(path) / 1e6:.1f} MB on disk')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cache', default=os.path.join(tempfile.gettempdir(), 'chainle_data_cache'))
    ap.add_argument('--out', default=DATA)
    args = ap.parse_args()
    os.makedirs(args.cache, exist_ok=True)
    bpb.CACHE = args.cache
    bpb.UA = 'jj-apps-firsts-data-build/1.0 (offline dataset build)'
    contact = os.environ.get('PHRASE_BANK_CONTACT')
    if contact:
        bpb.UA = f'{bpb.UA[:-1]}; {contact})'
    build(args.out)


if __name__ == '__main__':
    main()
