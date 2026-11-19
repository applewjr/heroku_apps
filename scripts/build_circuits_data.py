"""Build datasets/circuits_data.npz, the word-pair data behind /circuits.

    python scripts/build_circuits_data.py --count    # count word pairs in Wikipedia's text (cached)
    python scripts/build_circuits_data.py            # build the file from the sources and the counts
    python scripts/build_circuits_data.py --report   # the same, but only report: sizes, canaries, samples

The solver needs directed word pairs: "A B" or "AB" is a compound term, A
written first (SNOWBALL, FIRE STATION). They come from:

    closed compounds   ENABLE words that split into two known words, in both
                       orders (HOUSEBOAT and BOATHOUSE are separate pairs)
    phrase lists       the phrase bank's two-word phrases (Wiktionary,
                       WordNet, Wikidata), weighted by the kinds that list them
    Wikipedia text     how often two words sit side by side across the
                       English Wikipedia (CC BY-SA 4.0), kept when they go
                       together far more than chance (PMI). This finds the
                       everyday phrases no list carries.
    Wikipedia titles   two-word titles and redirects. They only confirm a pair
                       another source has: on their own they are mostly people,
                       films and places (about 3 million pairs).

Inputs from the repo: datasets/all_words_blossom.csv (ENABLE, public domain),
phrase_words.tsv.gz (wordfreq, CC BY-SA 4.0), phrase_bank.tsv.gz,
phrase_blocklist.txt, and chainle_data.npz (Numberbatch vectors, to check that
a closed compound has something to do with its parts). Downloads, cached in
the system temp folder (circuits_data_cache): Wikipedia's title list (110 MB)
and its article dump in 73 parts (27 GB), fetched two at a time and counted
as each part lands. See datasets/CIRCUITS_DATA.md.
"""

import argparse
import bz2
import csv
import gzip
import hashlib
import html
import math
import multiprocessing
import os
import re
import sys
import tempfile
import threading
import time
import urllib.request
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, os.pardir, 'datasets')
sys.path.insert(0, os.path.join(HERE, os.pardir))

from functions import circuits  # noqa: E402
from functions.phrase_solver import TAG_BIT, blocklist_hash, load_blocklist  # noqa: E402

UA = 'jj-apps-circuits-data-build/1.0 (offline dataset build)'
DUMPS = 'https://dumps.wikimedia.org/enwiki/latest/'
TITLES = 'enwiki-latest-all-titles-in-ns0.gz'
_PART = re.compile(r'href="(enwiki-latest-pages-articles-multistream\d+\.xml-p\d+p\d+\.bz2)">'
                   r'[^<]*</a>\s+\S+ \S+\s+(\d+)')
_SPAN = re.compile(r'multistream(\d+)\.xml-p(\d+)p(\d+)\.bz2$')
PART_MIN_BYTES = 1 << 20  # smaller listed parts are dead links
STALL_S = 1800  # the count gives up after this long with no download progress

WORD = re.compile(r'[a-z]{2,15}')
# The vocabulary: words wordfreq rates at least KNOWN_ZIPF. Answers are
# everyday words: ANSWER_ZIPF or more, and in ENABLE unless very common.
KNOWN_ZIPF = 1.5
ANSWER_ZIPF = 2.5
COMMON_ZIPF = 3.5
# Function words never make the compounds Circuits means (OF THE, IT IS).
# Particles (UP, OUT, OVER) do (SETUP, TIMEOUT), so they stay.
STOPWORDS = frozenset("""
a an the and or but nor so yet if then than that this these those there which
who whom whose what when where why how i me my mine we us our ours you your
yours he him his she her hers it its they them their theirs is are was were be
been being am have has had having do does did will would shall should can could
may might must of to for with at by from as into onto upon via not no
""".split())

# Closed compounds: a dictionary word made of two known words.
COMPOUND_MIN_LEN = 5
COMPOUND_ZIPF = 1.5
PART_ZIPF = 2.5
SHORT_PARTS = {'up', 'in', 'on', 'by', 'ox'}
# A split whose vector sits far from both parts' counts for less (CAR + PET).
NEAR_FULL = 0.4
NEAR_FLOOR = 0.55
# A real compound is seldom much more common than its rarer part (FOOTBALL,
# BUTTERFLY). A word far more common than that is something else split by
# accident: THROUGH is not THRO + UGH, nor MILLION MIL + LION.
COMPOUND_OVER = 1.0

# Phrase bank weights by the kinds of list that carry the phrase, as in
# scripts/build_chainle_data.py: an established compound is a dictionary noun
# and a dictionary phrase, often a title too; names alone barely count.
BANK_BASE = 1.2
BANK_KINDS = [
    (('THING', 'FOOD', 'ANIMAL'), 0.9),
    (('PHRASE', 'PROVERB'), 0.5),
    (('MOVIE', 'SONG', 'TV', 'BOOK', 'TITLE', 'GAME', 'ART', 'BAND'), 0.3),
    (('PLACE', 'LANDMARK', 'SPACE', 'EVENT'), 0.2),
]
BANK_NAMES_ONLY = 0.6

# Wikipedia text. Counting keeps pairs of known words where neither is in
# COUNT_SKIP and one could be an answer (Zipf COUNT_ANSWER_ZIPF or more); these
# are fixed apart from the selection settings below, so retuning those never
# needs a recount.
COUNT_SKIP = frozenset("""
a an the and or but nor if than that this these those which who whom whose what
i me my we us our you your he him his she her it its they them their is are was
were be been being am have has had do does did will would shall should can
could may might must of to for with at by from as into onto upon not
""".split())
COUNT_ANSWER_ZIPF = 2.0
COUNT_SHARDS = 64
COUNT_KEEP = 5
# Selection. Pointwise mutual information favours rare pairs: by PMI, PAPER's
# best partners are MACHE, CUTOUTS and SHREDDERS (a median of 172 sightings
# for a word's top ten, over 20 common words). The log-likelihood score G2 =
# 2 O ln(O / E), observed against expected count, ranks PAPER MILL, MONEY and
# BAG first (a median of 2,362). A pair needs CORPUS_MIN sightings, twice the
# chance count, and G2 of at least G2_MIN; each word keeps its CORPUS_CAP best
# partners in each direction; and the best new pairs are kept until the
# file's pair budget is spent.
CORPUS_MIN = 20
CORPUS_RATIO = 2.0
G2_MIN = 300.0
CORPUS_CAP = 150
# Verbs and adverbs after a noun are prose, not compounds (BAND RELEASED,
# TEAM REALLY): a corpus-only pair whose second word is an -ED or -LY form of
# a known word, or whose first is an -LY form, is left out.
_PROSE_TAIL = re.compile(r'.{3,}(?:ed|ly)')
_PROSE_HEAD = re.compile(r'.{3,}ly')

# Combining. A pair's weight is its strongest source's, plus MORE_SOURCES for
# each other kind of source that has it, plus a title bonus, up to WEIGHT_MAX.
MORE_SOURCES = 0.3
TITLE2_BONUS = 0.3
TITLE3_BONUS = 0.15
WEIGHT_MAX = 4.0
MIN_WEIGHT = 0.5

# Source bits (succ_src), for reports.
SRC_CLOSED, SRC_BANK, SRC_CORPUS, SRC_TITLE = 1, 2, 4, 8

# The file must stay small: it is held in the web dyno's memory.
MAX_PAIRS = 1_500_000
MAX_BYTES = 20 * 2 ** 20

CANARIES_PRESENT = [('snow', 'ball'), ('fire', 'station'), ('foot', 'ball'), ('tooth', 'brush'),
                    ('house', 'boat'), ('boat', 'house')]
CANARIES_ABSENT = [('thro', 'ugh')]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --- inputs -----------------------------------------------------------------------

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
            w = w.lower()
            if WORD.fullmatch(w):
                zipf[w] = max(float(z), zipf.get(w, 0.0))
    return zipf


def vocabulary():
    """(known words sorted, zipf, the answer words). Offensive words are left out."""
    zipf = load_zipf()
    enable = load_enable()
    blocked = load_blocklist(os.path.join(DATA, 'phrase_blocklist.txt'))
    known = sorted(w for w, z in zipf.items() if z >= KNOWN_ZIPF and blocklist_hash(w.upper()) not in blocked)
    answer = {w for w in known if zipf[w] >= ANSWER_ZIPF and w not in STOPWORDS
              and (w in enable or zipf[w] >= COMMON_ZIPF)}
    return known, zipf, answer, enable


def fetch(url, path, size=None):
    """Download url to path, resuming a partial download (path + '.part'),
    which becomes path only once complete. Returns path."""
    if os.path.exists(path) and (size is None or os.path.getsize(path) == size):
        return path
    part = path + '.part'
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if size is not None and have >= size:  # overrun or never renamed: start over
        have = 0
    req = urllib.request.Request(url, headers={'User-Agent': UA, **({'Range': f'bytes={have}-'} if have else {})})
    with urllib.request.urlopen(req, timeout=120) as resp:
        if resp.status != 206:  # the whole file, not the rest of it
            have = 0
        with open(part, 'ab' if have else 'wb') as out:
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                out.write(block)
    got = os.path.getsize(part)
    if size is not None and got != size:
        raise OSError(f'{os.path.basename(path)}: {got:,} of {size:,} bytes')
    os.replace(part, path)
    return path


def dump_parts(cache):
    """[(name, size)] of the article dump's parts, from the cached listing.
    The latest/ folder also keeps the last part of earlier dumps (same first
    page, fewer pages) and dead links a few bytes long. Counting both copies
    would count those articles twice, so only the longest copy is kept."""
    os.makedirs(os.path.join(cache, 'wiki'), exist_ok=True)
    listing = os.path.join(cache, 'wiki', 'files.txt')
    if not os.path.exists(listing):
        req = urllib.request.Request(DUMPS, headers={'User-Agent': UA})
        page = urllib.request.urlopen(req, timeout=60).read().decode('utf-8', 'replace')
        parts = sorted(set(_PART.findall(page)))
        with open(listing, 'w', encoding='utf-8') as fh:
            fh.writelines(f'{n} {s}\n' for n, s in parts)
    with open(listing, encoding='utf-8') as fh:
        listed = [(n, int(s)) for n, s in (line.split() for line in fh if line.strip())]
    best = {}
    for name, size in listed:
        stream, first, last = map(int, _SPAN.search(name).groups())
        if size >= PART_MIN_BYTES and last > best.get((stream, first), (None, 0, -1))[2]:
            best[(stream, first)] = (name, size, last)
    spans = sorted((first, last) for (_, first), (_, _, last) in best.items())
    for (_, last), (first, _) in zip(spans, spans[1:]):
        if first <= last:
            sys.exit(f'dump parts overlap at page {first}: check {listing}')
    return sorted((n, s) for n, s, _ in best.values())


# --- counting word pairs in Wikipedia's text ---------------------------------------

_COMMENT = re.compile(r'<!--.*?-->', re.S)
_REF = re.compile(r'<ref[^>]*/>|<ref[^>]*>.*?</ref>', re.S | re.I)
_TEMPLATE = re.compile(r'\{\{[^{}]*\}\}')
_TABLE = re.compile(r'\{\|.*?\|\}', re.S)
_FILE = re.compile(r'\[\[(?:file|image|category|media):[^\[\]]*(?:\[\[[^\[\]]*\]\][^\[\]]*)*\]\]', re.I)
_LINK = re.compile(r'\[\[(?:[^\[\]|]*\|)?([^\[\]]*)\]\]')
_EXT = re.compile(r'\[https?://\S*\s*([^\]]*)\]')
_TAG = re.compile(r'<[^>]*>')
_TOKEN = re.compile(r"[a-z]+|[^a-z\s]")


def articles(path):
    """The wikitext of each article (namespace 0, not a redirect) in a dump part."""
    with bz2.open(path, 'rt', encoding='utf-8', errors='replace') as fh:
        ns0 = redirect = False
        buf = None
        for line in fh:
            if buf is not None:
                end = line.find('</text>')
                if end < 0:
                    buf.append(line)
                    continue
                buf.append(line[:end])
                if ns0 and not redirect:
                    yield ''.join(buf)
                buf = None
                continue
            s = line.lstrip()
            if s.startswith('<page>'):
                ns0 = redirect = False
            elif s.startswith('<ns>'):
                ns0 = s.startswith('<ns>0<')
            elif s.startswith('<redirect'):
                redirect = True
            elif s.startswith('<text'):
                start = s.find('>') + 1
                if s[start - 2] == '/':
                    continue
                rest = s[start:]
                end = rest.find('</text>')
                if end >= 0:
                    if ns0 and not redirect:
                        yield rest[:end]
                else:
                    buf = [rest]


def plain_text(wikitext):
    """Wikitext with markup crudely stripped: good enough to count which
    words sit next to each other in running prose."""
    t = html.unescape(wikitext)
    t = _COMMENT.sub(' ', t)
    t = _REF.sub(' ', t)
    for _ in range(6):
        t, n = _TEMPLATE.subn(' ', t)
        if not n:
            break
    t = _TABLE.sub(' ', t)
    t = _FILE.sub(' ', t)
    t = _LINK.sub(r'\1', t)
    t = _EXT.sub(r'\1', t)
    t = _TAG.sub(' ', t)
    return t.replace("'''", '').replace("''", '')


_W = {}


def _count_init(vocab_path):
    words = np.load(vocab_path, allow_pickle=False)
    _W['index'] = {w: i for i, w in enumerate(words['words'].tolist())}
    _W['keep'] = words['keep']
    _W['answer'] = words['answer']
    _W['size'] = len(words['keep'])


def _merge(codes, counts):
    u, inv = np.unique(np.concatenate(codes), return_inverse=True)
    return u, np.bincount(inv, weights=np.concatenate(counts)).astype(np.int64)


def count_part(job):
    """Count adjacent known-word pairs (and single words) in one dump part.
    Writes {codes_s, counts_s} per hash shard s, plus the word counts."""
    src, dst = job
    index, keep, answer, size = _W['index'], _W['keep'], _W['answer'], _W['size']
    get = index.get
    uni = np.zeros(size, dtype=np.int64)
    run_codes, run_counts = [], []
    toks = []
    pending = 0

    def flush():
        nonlocal toks, pending
        if not toks:
            return
        arr = np.array(toks, dtype=np.int32)
        toks = []
        hit = arr[arr >= 0]
        uni[:] += np.bincount(hit, minlength=size)
        a, b = arr[:-1], arr[1:]
        m = (a >= 0) & (b >= 0)
        a, b = a[m], b[m]
        m = keep[a] & keep[b] & (answer[a] | answer[b]) & (a != b)
        codes = a[m].astype(np.int64) * size + b[m]
        u, c = np.unique(codes, return_counts=True)
        run_codes.append(u)
        run_counts.append(c.astype(np.int64))
        pending += len(u)
        if pending > 40_000_000:
            mu, mc = _merge(run_codes, run_counts)
            run_codes[:] = [mu]
            run_counts[:] = [mc]
            pending = len(mu)

    n_articles = 0
    for text in articles(src):
        n_articles += 1
        toks.extend(get(t, -1) for t in _TOKEN.findall(plain_text(text).lower()))
        toks.append(-1)
        if len(toks) > 5_000_000:
            flush()
    flush()
    codes, counts = _merge(run_codes, run_counts) if run_codes else (np.zeros(0, np.int64), np.zeros(0, np.int64))
    shard = codes % COUNT_SHARDS
    out = {'uni': uni, 'articles': np.array(n_articles)}
    for s in range(COUNT_SHARDS):
        m = shard == s
        out[f'codes_{s}'] = codes[m]
        out[f'counts_{s}'] = counts[m].astype(np.int32)
    tmp = dst + '.tmp.npz'
    np.savez(tmp, **out)
    os.replace(tmp, dst)
    return os.path.basename(src), n_articles, len(codes)


def count_corpus(cache, workers, download=True):
    """Count word pairs in every part of the article dump, part by part as
    downloads finish (two at a time), then merge them into wiki_pairs.npz."""
    known, zipf, answer, _ = vocabulary()
    folder = os.path.join(cache, 'wiki')
    counts_dir = os.path.join(cache, 'wiki_counts')
    os.makedirs(counts_dir, exist_ok=True)
    keep = np.array([w not in COUNT_SKIP for w in known])
    ans = np.array([zipf[w] >= COUNT_ANSWER_ZIPF and w not in COUNT_SKIP for w in known])
    sig = hashlib.sha1(('\n'.join(known) + keep.tobytes().hex() + ans.tobytes().hex()).encode()).hexdigest()[:10]
    vocab_path = os.path.join(counts_dir, f'vocab_{sig}.npz')
    if not os.path.exists(vocab_path):
        np.savez(vocab_path, words=np.array(known), keep=keep, answer=ans)
    parts = dump_parts(cache)
    out_of = lambda name: os.path.join(counts_dir, f'{name}.{sig}.npz')  # noqa: E731
    todo = [(n, s) for n, s in parts if not os.path.exists(out_of(n))]
    log(f'{len(parts)} dump parts, {len(todo)} still to count (vocabulary {sig}, {len(known)} words)')

    downloading, failed = set(), set()
    lock = threading.Lock()
    queue = iter(todo)  # shared, so the two threads never fetch the same part

    def downloader():
        while True:
            with lock:
                name, size = next(queue, (None, 0))
                if name is None:
                    return
                downloading.add(name)
            for attempt in range(5):
                try:
                    fetch(DUMPS + name, os.path.join(folder, name), size)
                    break
                except Exception as e:  # network hiccup: resume where it stopped
                    log(f'  download {name}: {type(e).__name__}, retrying')
                    time.sleep(10 * (attempt + 1))
            else:
                with lock:
                    failed.add(name)
            with lock:
                downloading.discard(name)

    if download:
        for _ in range(2):
            threading.Thread(target=downloader, daemon=True).start()

    def on_disk():
        return sum(e.stat().st_size for e in os.scandir(folder) if e.is_file())

    started = moved = time.time()
    seen = on_disk()
    with multiprocessing.Pool(workers, initializer=_count_init, initargs=(vocab_path,)) as pool:
        pending = list(todo)
        running = []
        while pending or running:
            with lock:
                ready = [(n, s) for n, s in pending
                         if os.path.exists(os.path.join(folder, n)) and os.path.getsize(os.path.join(folder, n)) == s
                         and n not in downloading]
                lost = [(n, s) for n, s in pending if n in failed]
            for n, s in lost:
                pending.remove((n, s))
                log(f'  gave up downloading {n}')
            for n, s in ready:
                pending.remove((n, s))
                running.append(pool.apply_async(count_part, ((os.path.join(folder, n), out_of(n)),)))
            for r in [r for r in running if r.ready()]:
                running.remove(r)
                name, n_art, n_pairs = r.get()
                done = len(parts) - len(pending) - len(running)
                log(f'  counted {name}: {n_art:,} articles, {n_pairs:,} pairs '
                    f'({done}/{len(parts)}, {(time.time() - started) / 60:.0f} min)')
            now = on_disk()
            if ready or running or now != seen:
                seen, moved = now, time.time()
            elif pending and time.time() - moved > STALL_S:
                log(f'  no download progress for {STALL_S // 60} minutes')
                break
            time.sleep(5)
    left = [n for n, _ in parts if not os.path.exists(out_of(n))]
    if left:
        sys.exit(f'{len(left)} parts not counted (first: {left[0]}): nothing merged, rerun to finish')
    merge_counts(cache, sig, [out_of(n) for n, _ in parts])


def merge_counts(cache, sig, files):
    """Sum the parts' counts shard by shard, keeping pairs seen COUNT_KEEP times."""
    codes_all, counts_all = [], []
    uni, n_articles = None, 0
    for f in files:
        with np.load(f) as z:
            uni = z['uni'].copy() if uni is None else uni + z['uni']
            n_articles += int(z['articles'])
    for s in range(COUNT_SHARDS):
        cs, ns = [], []
        for f in files:
            with np.load(f) as z:
                cs.append(z[f'codes_{s}'])
                ns.append(z[f'counts_{s}'].astype(np.int64))
        u, c = _merge(cs, ns)
        m = c >= COUNT_KEEP
        codes_all.append(u[m])
        counts_all.append(c[m])
    codes = np.concatenate(codes_all)
    counts = np.concatenate(counts_all)
    order = np.argsort(codes)
    path = os.path.join(cache, 'wiki_counts', f'wiki_pairs.{sig}.npz')
    np.savez(path, codes=codes[order], counts=counts[order], uni=uni, articles=np.array(n_articles))
    log(f'merged: {n_articles:,} articles, {len(codes):,} pairs seen {COUNT_KEEP}+ times -> {path}')


# --- the pair sources -------------------------------------------------------------

def closed_compounds(enable, known, zipf, answer):
    """{(a, b): weight} for ENABLE words made of two known words, weighted by
    how common the compound is and how close its meaning sits to a part."""
    vec = _vectors()
    out = {}
    for word in enable:
        if len(word) < COMPOUND_MIN_LEN or zipf.get(word, 0.0) < COMPOUND_ZIPF:
            continue
        for i in range(2, len(word) - 1):
            a, b = word[:i], word[i:]
            if a not in known or b not in known or (a not in answer and b not in answer):
                continue
            if any(len(p) < 3 and p not in SHORT_PARTS for p in (a, b)):
                continue
            rarer = min(zipf.get(a, 0.0), zipf.get(b, 0.0))
            if rarer < PART_ZIPF or zipf[word] > rarer + COMPOUND_OVER:
                continue
            near = vec(word, a, b)
            factor = min(1.0, NEAR_FLOOR + (1 - NEAR_FLOOR) * max(near, 0.0) / NEAR_FULL)
            w = (0.8 + 0.4 * (zipf[word] - COMPOUND_ZIPF)) * factor
            if w >= MIN_WEIGHT:
                out[(a, b)] = max(w, out.get((a, b), 0.0))
    return out


def _vectors():
    """near(word, a, b): the compound's best cosine with either part, from
    Chainle's Numberbatch vectors (0.2 when a word has none)."""
    with np.load(os.path.join(DATA, 'chainle_data.npz')) as z:
        vocab = z['vocab']
        vec = z['vec']
        scale = z['scale']
    index = {w.decode(): i for i, w in enumerate(vocab.tolist())}

    def unit(w):
        i = index.get(w)
        return None if i is None else vec[i].astype(np.float32) * scale[i]

    def near(word, a, b):
        uw = unit(word)
        if uw is None:
            return 0.2
        best = -1.0
        for p in (a, b):
            up = unit(p)
            if up is not None:
                best = max(best, float(uw @ up))
        return best if best > -1.0 else 0.2
    return near


def bank_phrases(known, answer):
    """({(a, b): weight}, {(unit, word) or (word, unit): weight}) from the
    phrase bank: its two-word phrases, and three-word ones split around a
    two-word phrase the bank also has (ICE CREAM + TRUCK), so a two-word tile
    still finds its box."""
    kinds = [(sum(TAG_BIT[t] for t in tags), bonus) for tags, bonus in BANK_KINDS]

    def weight(mask):
        listed = [bonus for bits, bonus in kinds if mask & bits]
        return BANK_BASE + sum(listed) if listed else BANK_NAMES_ONLY

    two, three = {}, []
    with gzip.open(os.path.join(DATA, 'phrase_bank.tsv.gz'), 'rt', encoding='utf-8') as fh:
        next(fh)
        for line in fh:
            key, mask = line.split('\t', 2)[:2]
            ws = key.lower().split(' ')
            if not all(WORD.fullmatch(w) for w in ws):
                continue
            if len(ws) == 2:
                a, b = ws
                if a != b and a in known and b in known and (a in answer or b in answer):
                    two[(a, b)] = max(weight(int(mask)), two.get((a, b), 0.0))
            elif len(ws) == 3 and all(w in known for w in ws):
                three.append((ws, weight(int(mask))))
    units = {}
    for (x, y, z), w in three:
        if len(x) + len(y) + 1 > circuits.MAX_CELL_LEN or len(y) + len(z) + 1 > circuits.MAX_CELL_LEN:
            continue
        if (x, y) in two and z in answer:
            units[(f'{x} {y}', z)] = max(w, units.get((f'{x} {y}', z), 0.0))
        if (y, z) in two and x in answer:
            units[(x, f'{y} {z}')] = max(w, units.get((x, f'{y} {z}'), 0.0))
    return two, units


def title_pairs(cache, known):
    """(two-word titles, bigrams of three-word titles) as sets of (a, b), from
    Wikipedia's title list (redirects included). A plural last word also
    counts for its singular (BUS STOPS)."""
    path = fetch(DUMPS + TITLES, os.path.join(cache, TITLES))
    paren = re.compile(r'_\(.*\)$')
    t2, t3 = set(), set()
    with gzip.open(path, 'rt', encoding='utf-8', errors='replace') as fh:
        for raw in fh:
            t = paren.sub('', raw.strip()).lower().split('_')
            if len(t) == 2:
                a, b = t
                if a in known and b in known:
                    t2.add((a, b))
                    if b.endswith('s') and b[:-1] in known:
                        t2.add((a, b[:-1]))
            elif len(t) == 3:
                for a, b in ((t[0], t[1]), (t[1], t[2])):
                    if a in known and b in known:
                        t3.add((a, b))
    return t2, t3


def corpus_weight(g2):
    """A corpus pair's weight from its G2: 0.8 at G2_MIN, up to 2.0. Below the
    dictionary nouns (2.1+), so the lists still lead when both have a word."""
    return min(2.0, max(0.6, 0.4 * math.log10(g2) - 0.19))


def corpus_pairs(cache, known, answer, listed, budget):
    """{(a, b): (weight, count)} from the Wikipedia counts. Pairs the lists
    already have are all kept (they add a source); new ones are kept best
    first, by G2, until `budget` new pairs are in."""
    folder = os.path.join(cache, 'wiki_counts')
    found = sorted(f for f in os.listdir(folder) if f.startswith('wiki_pairs.')) if os.path.isdir(folder) else []
    if not found:
        log('no Wikipedia counts yet (run --count): building without corpus pairs')
        return {}
    with np.load(os.path.join(folder, found[-1])) as z:
        codes, counts, uni = z['codes'], z['counts'].astype(np.float64), z['uni'].astype(np.float64)
    words = np.load(os.path.join(folder, 'vocab_' + found[-1].split('.')[1] + '.npz'))['words'].tolist()
    size = len(words)
    a = (codes // size).astype(np.int64)
    b = (codes % size).astype(np.int64)
    expected = uni[a] * uni[b] / uni.sum()
    ok_answer = np.array([w in answer for w in words])
    # Two-letter tokens in running text are mostly numerals and initials (II, MR).
    ok_known = np.array([w in known and w not in STOPWORDS and (len(w) >= 3 or w in SHORT_PARTS) for w in words])
    prose_tail = np.array([bool(_PROSE_TAIL.fullmatch(w)) and (w[:-2] in known or w[:-1] in known) for w in words])
    prose_head = np.array([bool(_PROSE_HEAD.fullmatch(w)) and w[:-2] in known for w in words])
    m = ((counts >= CORPUS_MIN) & (counts >= CORPUS_RATIO * expected)
         & ok_known[a] & ok_known[b] & (ok_answer[a] | ok_answer[b]))
    a, b, counts, expected = a[m], b[m], counts[m], expected[m]
    g2 = 2 * counts * np.log(counts / expected)
    m = g2 >= G2_MIN
    a, b, counts, g2 = a[m], b[m], counts[m], g2[m]
    in_list = np.array([(words[x], words[y]) in listed for x, y in zip(a.tolist(), b.tolist())], dtype=bool)
    m = in_list | ~(prose_tail[b] | prose_head[a])
    a, b, counts, g2, in_list = a[m], b[m], counts[m], g2[m], in_list[m]
    log(f'corpus: {len(a):,} pairs pass the count, chance and G2 floors ({int(in_list.sum()):,} already listed)')
    keep = np.zeros(len(a), dtype=bool)
    for side in (a, b):
        order = np.lexsort((-g2, side))
        s = side[order]
        rank = np.arange(len(s)) - np.searchsorted(s, s, side='left')
        keep[order[rank < CORPUS_CAP]] = True
    keep |= in_list
    new = np.flatnonzero(keep & ~in_list)
    if len(new) > budget:
        ranked = new[np.argsort(-g2[new])]
        keep[ranked[max(budget, 0):]] = False
        floor = g2[ranked[budget - 1]] if budget > 0 else float('inf')
        log(f'corpus: the budget keeps the best {max(budget, 0):,} new pairs (G2 >= {floor:.0f})')
    log(f'corpus: {int(keep.sum()):,} pairs kept, {int((keep & ~in_list).sum()):,} of them new')
    out = {}
    for x, y, c, g in zip(a[keep].tolist(), b[keep].tolist(), counts[keep].tolist(), g2[keep].tolist()):
        out[(words[x], words[y])] = (corpus_weight(g), int(c))
    return out


# --- combining ----------------------------------------------------------------------

def combine(closed, bank, units, corpus, t2, t3):
    """{(a, b): weight} and {(a, b): source bits}: each pair's strongest
    source, plus MORE_SOURCES per other kind of source, plus the title bonus.
    Titles never add a pair of their own."""
    weight, bits = {}, defaultdict(int)
    for pairs, bit in ((closed, SRC_CLOSED), (bank, SRC_BANK), (units, SRC_BANK)):
        for ab, w in pairs.items():
            weight[ab] = max(w, weight.get(ab, 0.0))
            bits[ab] |= bit
    for ab, (w, _) in corpus.items():
        weight[ab] = max(w, weight.get(ab, 0.0))
        bits[ab] |= SRC_CORPUS
    for ab in weight:
        kinds = bin(bits[ab] & (SRC_CLOSED | SRC_BANK | SRC_CORPUS)).count('1')
        bonus = MORE_SOURCES * (kinds - 1)
        if ab in t2:
            bonus += TITLE2_BONUS
            bits[ab] |= SRC_TITLE
        elif ab in t3:
            bonus += TITLE3_BONUS
            bits[ab] |= SRC_TITLE
        weight[ab] = min(WEIGHT_MAX, weight[ab] + bonus)
    return weight, dict(bits)


def canaries(data):
    failed = [f'{a}+{b} missing' for a, b in CANARIES_PRESENT if data.pair(data.id(a), data.id(b)) is None]
    failed += [f'{a}+{b} present' for a, b in CANARIES_ABSENT if data.pair(data.id(a), data.id(b)) is not None]
    return failed


def build(cache, report_only=False, force=False, out=None):
    known_list, zipf, answer, enable = vocabulary()
    known = set(known_list)
    log(f'vocabulary: {len(known):,} known words, {len(answer):,} may be answers')
    closed = closed_compounds(enable, known, zipf, answer)
    log(f'closed compounds: {len(closed):,} pairs')
    bank, units = bank_phrases(known, answer)
    log(f'phrase bank: {len(bank):,} two-word pairs, {len(units):,} with a two-word unit')
    listed = set(closed) | set(bank)
    budget = int(MAX_PAIRS * 0.95) - len(listed | set(units))
    corpus = corpus_pairs(cache, known, answer, listed, budget)
    log('titles...')
    t2, t3 = title_pairs(cache, known)
    log(f'titles: {len(t2):,} two-word, {len(t3):,} bigrams of three-word titles (confirming only)')
    weight, bits = combine(closed, bank, units, corpus, t2, t3)
    weight = {ab: w for ab, w in weight.items() if w >= MIN_WEIGHT}
    by_source = defaultdict(int)
    for ab in weight:
        by_source[bits.get(ab, 0)] += 1
    log(f'combined: {len(weight):,} pairs')
    names = {SRC_CLOSED: 'closed', SRC_BANK: 'bank', SRC_CORPUS: 'corpus', SRC_TITLE: 'title'}
    for b, n in sorted(by_source.items(), key=lambda kv: -kv[1])[:12]:
        log(f'  {"+".join(v for k, v in names.items() if b & k):24s} {n:>10,}')

    arrays = circuits.pack(weight, zipf=zipf, answer=answer)
    data = circuits.Data(**arrays)
    nbytes = sum(a.nbytes for a in arrays.values())
    log(f'arrays: {len(weight):,} pairs, {data.size:,} words, {nbytes / 2 ** 20:.1f} MB in memory')
    failed = canaries(data)
    over = []
    if len(weight) > MAX_PAIRS:
        over.append(f'{len(weight):,} pairs > {MAX_PAIRS:,}')
    if nbytes > MAX_BYTES:
        over.append(f'{nbytes / 2 ** 20:.1f} MB > {MAX_BYTES / 2 ** 20:.0f} MB')
    for word in ('snow', 'fire', 'house'):
        ids, w = data.follows(data.id(word))
        top = sorted(zip(w.tolist(), ids.tolist()), reverse=True)[:8]
        log(f'  {word} + ...: ' + ', '.join(f'{data.word(i)} {x:.1f}' for x, i in top))
    if report_only:
        log(f'report only: canaries {failed or "ok"}, budget {over or "ok"}')
        return data
    if (failed or over) and not force:
        sys.exit(f'nothing written: canaries {failed or "ok"}, budget {over or "ok"}')
    path = out or os.path.join(DATA, 'circuits_data.npz')
    np.savez_compressed(path, **arrays)
    log(f'wrote {path}: {os.path.getsize(path) / 1e6:.1f} MB on disk, {nbytes / 2 ** 20:.1f} MB loaded')
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--count', action='store_true', help="count word pairs in Wikipedia's text first")
    ap.add_argument('--no-download', action='store_true', help='count only dump parts already downloaded')
    ap.add_argument('--workers', type=int, default=max(1, min(10, (os.cpu_count() or 2) - 4)))
    ap.add_argument('--report', action='store_true', help='report sizes and canaries, write nothing')
    ap.add_argument('--force', action='store_true', help='write even if canaries or the budget fail')
    ap.add_argument('--cache', default=os.path.join(tempfile.gettempdir(), 'circuits_data_cache'))
    ap.add_argument('--out', help='write here instead of datasets/circuits_data.npz')
    args = ap.parse_args()
    os.makedirs(args.cache, exist_ok=True)
    if args.count:
        count_corpus(args.cache, args.workers, download=not args.no_download)
        return
    build(args.cache, report_only=args.report, force=args.force, out=args.out)


if __name__ == '__main__':
    main()
