"""Build the Guess The Phrase solver's phrase data.

Writes the four files functions/phrase_solver.py and routes/guess_the_phrase.py load:

    datasets/phrase_bank.tsv.gz     match key, category tags, popularity, display form
    datasets/phrase_words.tsv.gz    fallback vocabulary: word, zipf
    datasets/phrase_bigrams.tsv.gz  word pair, log10 web count
    datasets/phrase_blocklist.txt   hashed offensive terms, for players' added phrases

Build-time only. It downloads public data into a cache folder outside the
repo, and the deployed app never fetches anything. Sources and licences are
recorded in datasets/PHRASE_BANK.md:

    Wikidata (CC0), queried through the QLever SPARQL endpoint
    WordNet 3.0 (Princeton WordNet licence)
    Wiktionary category listings (CC BY-SA 4.0)
    Peter Norvig's count_1w / count_2w (Google Web Trillion Word Corpus)
    LDNOOBW bad-words list (CC BY 4.0), used only as a filter

Run manually to regenerate:

    python scripts/build_phrase_bank.py            # build the three files
    python scripts/build_phrase_bank.py --check    # Wikidata class labels + counts

Set PHRASE_BANK_CONTACT (an email or URL) to add a contact to the
User-Agent, as Wikimedia asks of automated clients. Nothing personal is
hard-coded here.
"""

import argparse
import collections
import gzip
import io
import json
import math
import os
import re
import sys
import tarfile
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, os.pardir, 'datasets')
sys.path.insert(0, os.path.join(HERE, os.pardir))

from functions.phrase_solver import TAGS, blocklist_hash, phrase_key  # noqa: E402

QLEVER = 'https://qlever.dev/api/wikidata'
WIKTIONARY = 'https://en.wiktionary.org/w/api.php'
WORDNET = 'https://wordnetcode.princeton.edu/3.0/WNdb-3.0.tar.gz'
NORVIG_1W = 'https://www.norvig.com/ngrams/count_1w.txt'
NORVIG_2W = 'https://www.norvig.com/ngrams/count_2w.txt'
BADWORDS = ('https://raw.githubusercontent.com/LDNOOBW/'
            'List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/en')

# Board limits. A phrase longer than this can't be a puzzle, and the limits
# keep the bank's memory bounded.
MAX_WORDS = 10
MAX_LETTERS = 48
MAX_WORD_LEN = 16

# Wikidata groups: (tags, how many, instance-of classes, canaries). Each
# group keeps its `how many` items with the most sitelinks (how many
# Wikipedias cover the item: the fame measure), so the bank holds what a
# puzzle setter would pick. Ranking with ORDER BY/LIMIT, not FILTER: QLever
# returned wrong empty results for a FILTER on sitelinks (2026-10-11, e.g.
# no film with > 100 sitelinks, though The Godfather has 132).
#
# Canaries are famous items each group must contain, or the build stops.
# They guard against the endpoint quietly returning a wrong or partial
# answer, and are deliberately not Bored Panda answers.
Group = collections.namedtuple('Group', 'tags top classes canaries subclasses aliases',
                               defaults=(False, True))
# subclasses=True also takes things modelled as a subclass rather than an
# instance, as most dishes are (guacamole is "subclass of food").
# aliases=False keeps only the item's main name. People's aliases (134k:
# transliterations, birth names) never helped the benchmark, where answers use
# the common name; other groups' aliases did (a galaxy's longer usual name).
WIKIDATA = [
    Group(('MOVIE',), 40000, ['Q11424', 'Q24869', 'Q202866', 'Q29168811', 'Q506240'],
          ['THE GODFATHER', 'TITANIC']),
    Group(('SONG',), 25000, ['Q7366', 'Q134556', 'Q105543609'], ['BOHEMIAN RHAPSODY', 'HEY JUDE']),
    Group(('TITLE',), 12000, ['Q482994', 'Q208569', 'Q25379', 'Q2743'], ['ABBEY ROAD']),
    Group(('TV',), 20000, ['Q5398426', 'Q15416', 'Q581714', 'Q1259759'], ['BREAKING BAD', 'FRIENDS']),
    # Not "written work" or "book": they pull in laws and scripture, which
    # outrank novels and push children's books out of the quota.
    Group(('BOOK',), 25000, ['Q7725634', 'Q8261', 'Q277759', 'Q1667921'],
          ['PRIDE AND PREJUDICE', 'MOBY DICK']),
    Group(('BOOK',), 3000, ['Q699'], ['CINDERELLA']),
    Group(('GAME',), 10000, ['Q7889', 'Q131436'], ['MINECRAFT', 'MONOPOLY']),
    Group(('ART',), 5000, ['Q3305213', 'Q860861'], ['MONA LISA', 'THE STARRY NIGHT']),
    Group(('BAND',), 15000, ['Q215380', 'Q5741069', 'Q9212979', 'Q216337'], ['THE BEATLES']),
    Group(('PERSON',), 60000, ['Q5'], ['ABRAHAM LINCOLN', 'ALBERT EINSTEIN'], aliases=False),
    Group(('CHARACTER', 'FICTION'), 15000,
          ['Q95074', 'Q15632617', 'Q15773347', 'Q15711870', 'Q1114461', 'Q3658341', 'Q15773317',
           'Q4271324', 'Q188784'], ['SHERLOCK HOLMES', 'MICKEY MOUSE']),
    Group(('PLACE', 'FICTION'), 5000, ['Q3895768', 'Q1964689', 'Q15796005'], ['GOTHAM CITY']),
    # Places in three quotas: bot-written Wikipedias give villages dozens of
    # sitelinks, which would otherwise crowd out mountains and rivers.
    Group(('PLACE',), 15000,
          ['Q6256', 'Q3624078', 'Q5119', 'Q35657', 'Q107390', 'Q5107', 'Q1549591', 'Q1637706',
           'Q200250'], ['NEW YORK CITY', 'CANADA']),
    Group(('PLACE',), 20000,
          ['Q8502', 'Q4022', 'Q23397', 'Q23442', 'Q165', 'Q9430', 'Q34763', 'Q8514', 'Q46831',
           'Q8072', 'Q35666', 'Q39594'], ['MOUNT EVEREST', 'AMAZON RIVER']),
    Group(('PLACE',), 30000, ['Q515', 'Q1093829', 'Q82794'], ['LAS VEGAS']),
    Group(('LANDMARK', 'PLACE'), 20000,
          ['Q570116', 'Q4989906', 'Q12518', 'Q12280', 'Q839954', 'Q33506', 'Q23413', 'Q16560',
           'Q483110', 'Q22698', 'Q46169', 'Q11303', 'Q2319498', 'Q43501', 'Q194195', 'Q34038',
           'Q150784', 'Q12323', 'Q2977', 'Q174782'], ['EIFFEL TOWER', 'GOLDEN GATE BRIDGE']),
    Group(('SPACE',), 8000,
          ['Q523', 'Q634', 'Q2537', 'Q318', 'Q8928', 'Q2488', 'Q44559', 'Q40218', 'Q2133344',
           'Q26529', 'Q25956', 'Q83373', 'Q13632', 'Q3559'], ['ANDROMEDA GALAXY', 'JUPITER']),
    Group(('FOOD',), 15000,
          ['Q746549', 'Q2095', 'Q40050', 'Q154', 'Q134768', 'Q11004', 'Q1364', 'Q182940', 'Q10943',
           'Q28803'], ['CAESAR SALAD', 'GUACAMOLE'], subclasses=True),
    Group(('EVENT',), 8000, ['Q1445650', 'Q1197685', 'Q178561', 'Q198'], ['BATTLE OF WATERLOO']),
]
# Animals: taxa under Animalia (Q729), English common names.
ANIMAL_TOP = 20000
ANIMAL_CANARIES = ['POLAR BEAR', 'GIRAFFE']
# Below this many sitelinks an item is too obscure to be a puzzle answer.
MIN_SITELINKS = 3

# Wiktionary categories -> tags. The curated lists get a popularity prior
# over the huge generic one.
WIKTIONARY_CATS = [
    ('English_proverbs', ('PROVERB', 'PHRASE'), 14),
    ('English_idioms', ('PHRASE',), 8),
    ('English_similes', ('PHRASE',), 6),
    ('English_catchphrases', ('PHRASE',), 8),
    ('English_phrases', ('PHRASE',), 6),
    ('English_euphemisms', ('PHRASE',), 2),
    ('English_humorous_terms', ('PHRASE',), 0),
    ('English_multiword_terms', ('PHRASE',), -4),
]

# WordNet lexicographer files -> tags (multiword lemmas, plus single words
# where the tag is a "name the thing" category). Nouns not listed are THING;
# multiword verbs, adjectives and adverbs ("kick the bucket") are PHRASE.
WORDNET_LEX = {
    'noun.animal': ('ANIMAL',), 'noun.food': ('FOOD',), 'noun.location': ('PLACE',),
    'noun.person': ('PERSON',), 'noun.event': ('EVENT',), 'noun.time': ('EVENT',),
    'noun.act': ('THING', 'PHRASE'), 'noun.cognition': ('PHRASE',), 'noun.state': ('PHRASE',),
    'noun.attribute': ('PHRASE',), 'noun.feeling': ('PHRASE',), 'noun.relation': ('PHRASE',),
}
SINGLE_WORD_LEX = {'noun.animal', 'noun.food'}
# WordNet's lexicographer file numbers (lexnames(5WN)); the WNdb tarball
# doesn't ship the table. Verbs and adjectives only need "not a noun".
NOUN_LEXNAMES = dict(enumerate(
    ['Tops', 'act', 'animal', 'artifact', 'attribute', 'body', 'cognition', 'communication',
     'event', 'feeling', 'food', 'group', 'location', 'motive', 'object', 'person',
     'phenomenon', 'plant', 'possession', 'process', 'quantity', 'relation', 'shape', 'state',
     'substance', 'time'], start=3))
# WordNet's animal and plant files name taxa too ("genus Canis").
TAXON_RANKS = {'genus', 'family', 'order', 'class', 'subclass', 'phylum', 'subphylum', 'suborder',
               'superfamily', 'subfamily', 'superorder', 'infraorder', 'division', 'subdivision',
               'kingdom', 'subkingdom', 'tribe', 'subgenus'}
# Lemmas whose synsets (or ancestors) mark astronomy.
SPACE_ROOTS = {'celestial_body', 'heavenly_body', 'extraterrestrial_object', 'galaxy',
               'constellation', 'spacecraft', 'space_station', 'planet', 'star'}

CACHE = None
UA = 'jj-apps-phrase-bank-build/1.0 (offline dataset build)'


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def _download(url, label, data=None, accept=None):
    """One request, retried with backoff (and Retry-After on a 429)."""
    headers = {'User-Agent': UA}
    if accept:
        headers['Accept'] = accept
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=600) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001
            if attempt == 5:
                raise
            wait = 10 * 2 ** attempt
            if getattr(e, 'code', None) == 429:
                wait = max(wait, int(e.headers.get('Retry-After') or 0), 60)
            log(f'  retry {label} in {wait}s: {e}')
            time.sleep(wait)


def fetch(url, name, data=None, accept=None):
    """A download with a disk cache, so re-runs never hit the network."""
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        with open(path, 'rb') as fh:
            return fh.read()
    body = _download(url, name, data, accept)
    with open(path, 'wb') as fh:
        fh.write(body)
    return body


SPARQL_PREFIX = """PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX wikibase: <http://wikiba.se/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
"""


def sparql(query, name):
    # JSON, not TSV: QLever's TSV output silently comes back empty for some
    # large joins.
    cached = os.path.exists(os.path.join(CACHE, name))
    body = fetch(QLEVER, name, urllib.parse.urlencode({'query': SPARQL_PREFIX + query}).encode(),
                 accept='application/sparql-results+json')
    if not cached:
        time.sleep(2)  # QLever answers 429 to bursts
    return json.loads(body)['results']['bindings']


def _top_items(group):
    # P31/P279*: an instance of the class or of any subclass. The Golden
    # Gate Bridge is a "suspension bridge", Jupiter a "gas giant".
    values = ' '.join(f'wd:{q}' for q in group.classes)
    path = '(wdt:P31|wdt:P279)/wdt:P279*' if group.subclasses else 'wdt:P31/wdt:P279*'
    return f"""SELECT DISTINCT ?item ?sl WHERE {{ VALUES ?cls {{ {values} }}
    ?item {path} ?cls ; wikibase:sitelinks ?sl }} ORDER BY DESC(?sl) LIMIT {group.top}"""


def _animal_items():
    return f"""SELECT DISTINCT ?item ?sl WHERE {{ ?item wdt:P171+ wd:Q729 ;
    wikibase:sitelinks ?sl }} ORDER BY DESC(?sl) LIMIT {ANIMAL_TOP}"""


def names(top_items, name, aliases=True):
    """English names of the items a ranking subquery picks, as rows of
    {name, sl, kind}. Labels and aliases are separate queries: joined in one
    UNION they time out on QLever (it reports the timeout as HTTP 429).

    "mul" is Wikidata's default-for-all-languages label. Editors drop an
    English label that repeats it, so Albert Einstein has only "mul"."""
    rows = []
    for kind, pred in (('label', 'rdfs:label'), ('alias', 'skos:altLabel')):
        if kind == 'alias' and not aliases:
            continue
        if name == 'wd_animals' and kind == 'alias':
            kind, pred = 'common', 'wdt:P1843'
        q = f"""SELECT ?item ?name ?sl WHERE {{ {{ {top_items} }}
  ?item {pred} ?name . FILTER(LANG(?name) IN ("en", "mul")) }}"""
        for r in sparql(q, f'{name}_{kind}.json'):
            r['kind'] = {'value': kind}
            rows.append(r)
    return rows


def check_canaries(label, rows, canaries):
    keys = {phrase_key(r['name']['value']) for r in rows}
    missing = [c for c in canaries if c not in keys]
    sl = [int(r['sl']['value']) for r in rows]
    log(f'  wikidata {label}: {len(rows)} names, sitelinks {min(sl, default=0)}-{max(sl, default=0)}'
        + (f', MISSING {missing}' if missing else ''))
    return missing


def check_classes():
    """Print every configured class's English label, then each group's size."""
    qids = sorted({q for g in WIKIDATA for q in g.classes} | {'Q729'})
    values = ' '.join(f'wd:{q}' for q in qids)
    rows = sparql(f"""SELECT ?cls ?label WHERE {{ VALUES ?cls {{ {values} }}
        ?cls rdfs:label ?label . FILTER(LANG(?label) = "en") }}""", 'check_labels.json')
    labels = {r['cls']['value'].rsplit('/', 1)[1]: r['label']['value'] for r in rows}
    for g in WIKIDATA:
        print(f"{'/'.join(g.tags):18} " + ', '.join(f'{q} {labels.get(q, "??")}' for q in g.classes))
    for i, g in enumerate(WIKIDATA):
        check_canaries('/'.join(g.tags), names(_top_items(g), f'wd_{i}', g.aliases), g.canaries)
    check_canaries('ANIMAL', names(_animal_items(), 'wd_animals'), ANIMAL_CANARIES)


# --- text ----------------------------------------------------------------

PAREN = re.compile(r'\s*\([^)]*\)')


def clean_name(name):
    """A source string as it would sit on the board, or None if it can't.

    Accents fold to ASCII, parentheticals go, and anything left that a
    phrase board doesn't show (digits, other scripts) rejects the name.
    """
    name = PAREN.sub('', name).strip()
    name = unicodedata.normalize('NFKD', name)
    name = ''.join(c for c in name if not unicodedata.combining(c))
    name = name.replace('\u2019', "'").replace('\u2018', "'").replace('\u2013', '-')
    name = name.replace('\u2014', '-').replace('\u201c', '"').replace('\u201d', '"')
    if not re.fullmatch(r"[A-Za-z][A-Za-z '&.,!?:;\-\"]*", name):
        return None
    name = re.sub(r'\s+', ' ', name).upper().strip(' ,;:')
    key = phrase_key(name)
    if not key:
        return None
    words = key.split(' ')
    if (len(words) > MAX_WORDS or sum(map(len, words)) > MAX_LETTERS
            or max(map(len, words)) > MAX_WORD_LEN):
        return None
    return name


# --- sources -------------------------------------------------------------

def wikidata_entries():
    missing = []
    for i, g in enumerate(WIKIDATA):
        tags = g.tags
        rows = names(_top_items(g), f'wd_{i}', g.aliases)
        missing += check_canaries('/'.join(tags), rows, g.canaries)
        for r in rows:
            sl = int(r['sl']['value'])
            if sl < MIN_SITELINKS:
                continue
            name = r['name']['value']
            pop = 20 * math.log10(sl + 1)
            if r['kind']['value'] == 'alias':
                # Abbreviations ("NYC", "JFK") aren't board answers.
                if name.isupper() and len(name) <= 5:
                    continue
                pop -= 6
            yield name, tags, pop
    rows = names(_animal_items(), 'wd_animals')
    missing += check_canaries('ANIMAL', rows, ANIMAL_CANARIES)
    for r in rows:
        name = r['name']['value']
        # A taxon's English label is often its Latin binomial ("Panthera
        # leo"); keep labels only when they look like a common name.
        if r['kind']['value'] == 'label' and re.fullmatch(r'[A-Z][a-z]+ [a-z]+', name):
            continue
        yield name, ('ANIMAL',), 20 * math.log10(int(r['sl']['value']) + 1)
    if missing:
        sys.exit(f'canaries missing from Wikidata results: {missing}')


def wiktionary_titles(cat):
    path = os.path.join(CACHE, f'wikt_{cat}.json')
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    titles, cont = [], {}
    while True:
        params = {'action': 'query', 'list': 'categorymembers', 'cmtitle': f'Category:{cat}',
                  'cmlimit': '500', 'cmnamespace': '0', 'format': 'json', **cont}
        url = WIKTIONARY + '?' + urllib.parse.urlencode(params)
        d = json.loads(_download(url, f'{cat} page {len(titles) // 500 + 1}'))
        titles += [m['title'] for m in d['query']['categorymembers']]
        if 'continue' not in d:
            break
        cont = d['continue']
        time.sleep(2)  # Wikimedia rate-limits bursts
    with open(path, 'w') as fh:
        json.dump(titles, fh)
    return titles


def wiktionary_entries(pair_pop):
    for cat, tags, prior in WIKTIONARY_CATS:
        titles = wiktionary_titles(cat)
        log(f'  wiktionary {cat}: {len(titles)} titles')
        for t in titles:
            yield t, tags, pair_pop(t) + prior


def wordnet_entries(pair_pop, word_pop):
    raw = fetch(WORDNET, 'WNdb-3.0.tar.gz')
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        files = {os.path.basename(m.name): tar.extractfile(m).read().decode('latin-1')
                 for m in tar.getmembers() if m.isfile()}
    synsets = {}  # offset -> (lexname, lemmas, hypernym offsets)
    for pos in ('noun', 'verb', 'adj', 'adv'):
        for line in files[f'data.{pos}'].splitlines():
            if line.startswith('  '):
                continue
            f = line.split(' | ')[0].split()
            offset = f[0]
            lex = f'noun.{NOUN_LEXNAMES[int(f[1])]}' if pos == 'noun' else pos
            wcnt = int(f[3], 16)
            lemmas = [re.sub(r'\(.*\)$', '', f[4 + 2 * i]) for i in range(wcnt)]
            p = 4 + 2 * wcnt
            pcnt = int(f[p])
            hyper = [f[p + 1 + 4 * j + 1] for j in range(pcnt)
                     if f[p + 1 + 4 * j] in ('@', '@i') and f[p + 1 + 4 * j + 2] == 'n']
            synsets[(pos, offset)] = (lex, lemmas, hyper)

    space = {}

    def is_space(key):
        if key not in space:
            space[key] = False  # cycle guard
            lex, lemmas, hyper = synsets[key]
            space[key] = (any(l.lower() in SPACE_ROOTS for l in lemmas)
                          or any(is_space(('noun', h)) for h in hyper))
        return space[key]

    for (pos, offset), (lex, lemmas, _) in synsets.items():
        tags = WORDNET_LEX.get(lex, ('THING',) if pos == 'noun' else ('PHRASE',))
        if pos == 'noun' and is_space((pos, offset)):
            tags = tags + ('SPACE',)
        if not tags:
            continue
        for lemma in lemmas:
            text = lemma.replace('_', ' ')
            if text.split(' ', 1)[0].lower() in TAXON_RANKS:
                continue
            if ' ' in text:
                yield text, tags, pair_pop(text)
            elif lex in SINGLE_WORD_LEX or 'SPACE' in tags:
                yield text, tags, 6 * word_pop(text.lower()) - 6


# --- popularity ----------------------------------------------------------

def load_norvig():
    one = {}
    for line in fetch(NORVIG_1W, 'count_1w.txt').decode().splitlines():
        w, c = line.split('\t')
        one[w] = int(c)
    total = sum(one.values())
    zipf = {w: round(math.log10(c / total * 1e9), 2) for w, c in one.items()}
    two = {}
    for line in fetch(NORVIG_2W, 'count_2w.txt').decode('latin-1').splitlines():
        pair, c = line.split('\t')
        a, b = pair.lower().split(' ')
        if a.isalpha() and b.isalpha() and a.isascii() and b.isascii():
            k = f'{a} {b}'
            two[k] = two.get(k, 0) + int(c)
    return zipf, two


def make_pop(zipf, two, dict_pop):
    def word_pop(w):
        return zipf.get(w, dict_pop.get(w, 0.0))

    def pair_pop(text):
        """Familiarity of a multiword phrase from web counts of its word pairs.

        Every adjacent pair seen on the web -> scored by the rarest pair.
        Otherwise a floor from the words' own frequencies, well below.
        """
        key = phrase_key(clean_name(text) or '')
        words = key.lower().split(' ') if key else []
        if not words:
            return 0.0
        if len(words) == 1:
            return 6 * word_pop(words[0]) - 6
        counts = [two.get(f'{a} {b}') for a, b in zip(words, words[1:])]
        if all(counts):
            return 6 * math.log10(min(counts)) - 15
        return 2 * (sum(word_pop(w) for w in words) / len(words))
    return word_pop, pair_pop


# --- build ---------------------------------------------------------------

def load_badwords():
    terms = [t.strip().upper() for t in fetch(BADWORDS, 'badwords_en.txt').decode().splitlines()]
    return [phrase_key(t) for t in terms if t and phrase_key(t)]


def build(out_dir):
    import pandas as pd

    dict_words = set(pd.read_csv(os.path.join(DATA, 'all_words_blossom.csv'))['0'].astype(str))
    pop_df = pd.read_csv(os.path.join(DATA, 'word_popularity.csv'))
    dict_pop = dict(zip(pop_df['word'], pop_df['zipf']))
    log('norvig...')
    zipf, two = load_norvig()
    word_pop, pair_pop = make_pop(zipf, two, dict_pop)
    bad = load_badwords()
    # The app checks players' added phrases against the same list. It ships
    # as hashes, so the repo doesn't carry the words themselves.
    with open(os.path.join(out_dir, 'phrase_blocklist.txt'), 'w', encoding='utf-8') as fh:
        fh.write('# sha256[:16] of each LDNOOBW term (CC BY 4.0), as phrase_solver.blocklist_hash\n')
        fh.writelines(f'{h}\n' for h in sorted({blocklist_hash(b) for b in bad}))
    bad_re = re.compile(r'(?:^| )(?:' + '|'.join(re.escape(b) for b in sorted(bad, key=len, reverse=True))
                        + r')(?: |$)')

    bank = {}  # key -> [display, tagmask, pop, source]
    bit = {t: 1 << i for i, t in enumerate(TAGS)}
    dropped = collections.Counter()
    blocked = []

    def add(raw, tags, pop, source):
        name = clean_name(raw)
        if not name:
            dropped[source] += 1
            return
        key = phrase_key(name)
        if bad_re.search(key):
            blocked.append(key)
            return
        mask = 0
        for t in tags:
            mask |= bit[t]
        cur = bank.get(key)
        if cur is None:
            bank[key] = [name, mask, pop, source]
        else:
            cur[1] |= mask
            if pop > cur[2]:
                cur[2] = pop
            # Prefer a display form that carries punctuation.
            if len(name) > len(cur[0]):
                cur[0] = name

    log('wikidata...')
    for raw, tags, pop in wikidata_entries():
        add(raw, tags, pop, 'wikidata')
    log('wordnet...')
    for raw, tags, pop in wordnet_entries(pair_pop, word_pop):
        add(raw, tags, pop, 'wordnet')
    log('wiktionary...')
    for raw, tags, pop in wiktionary_entries(pair_pop):
        add(raw, tags, pop, 'wiktionary')

    rows = sorted(bank.items(), key=lambda kv: (-kv[1][2], kv[0]))
    by_src = collections.Counter(v[3] for _, v in rows)
    log(f'bank: {len(rows)} phrases {dict(by_src)}; dropped {dict(dropped)}; '
        f'blocked {len(blocked)} e.g. {blocked[:8]}')

    with gzip.open(os.path.join(out_dir, 'phrase_bank.tsv.gz'), 'wt', encoding='utf-8',
                   compresslevel=9) as fh:
        # The match key is precomputed so the app loads without normalising
        # 600k strings; the display form is written only where it differs.
        fh.write('key\ttags\tpop\tdisplay\n')
        for key, (name, mask, pop, _) in rows:
            fh.write(f'{key}\t{mask}\t{max(pop, 0.0):.1f}\t{name if name != key else ""}\n')

    # Fallback vocabulary: the dictionary, every word the bank uses (names,
    # places), and the one-letter words the dictionary lacks.
    vocab = set(dict_words) | {'a', 'i'}
    for key, _ in rows:
        vocab.update(w.lower() for w in key.split(' '))
    vocab = {w for w in vocab if w.isalpha() and w.isascii() and len(w) <= MAX_WORD_LEN}
    with gzip.open(os.path.join(out_dir, 'phrase_words.tsv.gz'), 'wt', encoding='utf-8',
                   compresslevel=9) as fh:
        fh.write('word\tzipf\n')
        for w in sorted(vocab, key=lambda w: (-word_pop(w), w)):
            fh.write(f'{w.upper()}\t{word_pop(w):.2f}\n')
    log(f'vocab: {len(vocab)} words')

    pairs = {k: c for k, c in two.items() if all(w in vocab for w in k.split(' '))}
    with gzip.open(os.path.join(out_dir, 'phrase_bigrams.tsv.gz'), 'wt', encoding='utf-8',
                   compresslevel=9) as fh:
        fh.write('pair\tlog10\n')
        for k in sorted(pairs, key=lambda k: -pairs[k]):
            fh.write(f'{k.upper()}\t{math.log10(pairs[k]):.2f}\n')
    log(f'bigrams: {len(pairs)} pairs')


def main():
    global CACHE, UA
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--check', action='store_true', help='print Wikidata class labels and counts')
    ap.add_argument('--cache', default=os.path.join(tempfile.gettempdir(), 'phrase_bank_cache'))
    ap.add_argument('--out', default=DATA)
    args = ap.parse_args()
    CACHE = args.cache
    os.makedirs(CACHE, exist_ok=True)
    contact = os.environ.get('PHRASE_BANK_CONTACT')
    if contact:
        UA = f'{UA[:-1]}; {contact})'
    if args.check:
        check_classes()
    else:
        build(args.out)


if __name__ == '__main__':
    main()
