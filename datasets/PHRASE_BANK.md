# Guess The Phrase data

Four files back the `/guess_the_phrase` solver (`functions/phrase_solver.py`,
`routes/guess_the_phrase.py`).
`scripts/build_phrase_bank.py` builds them offline. The deployed app reads them
from disk and never fetches anything.

| File | Rows | What it is |
|---|---|---|
| `phrase_bank.tsv.gz` | 662,111 | Known phrases: match key, category tags (bitmask over `phrase_solver.TAGS`), popularity, display form where it differs from the key |
| `phrase_words.tsv.gz` | 388,258 | Fallback vocabulary, most common first: word, Zipf frequency |
| `phrase_bigrams.tsv.gz` | 94,091 | Word pairs from the phrase list itself: pair, log10 P(second word given the first) |
| `phrase_blocklist.txt` | 399 | Offensive terms, as `sha256[:16]` hashes: players' added phrases are checked against it |

Built 2026-10-11. In memory they take about 23 MB together, loaded once per
process on first use (about 1 s).

## Sources and licences

| Source | Licence | Used for |
|---|---|---|
| [Wikidata](https://www.wikidata.org), queried through [QLever](https://qlever.dev) | [CC0 1.0](https://www.wikidata.org/wiki/Wikidata:Licensing): no conditions | Titles (movies, songs, albums, TV, books, games, paintings), bands, people, characters, places, landmarks, space, food, events, animals |
| [WordNet 3.0](https://wordnet.princeton.edu) | WordNet 3.0 licence, in full in [`licenses/WordNet-3.0-LICENSE.txt`](licenses/WordNet-3.0-LICENSE.txt). "Any purpose", so commercial use is allowed | Everyday multiword terms, tagged by WordNet's own noun groups |
| [Wiktionary](https://en.wiktionary.org) category listings, by Wiktionary contributors | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | Proverbs, idioms, similes, phrases, multiword terms |
| [wordfreq](https://github.com/rspeer/wordfreq) by Robyn Speer | Data [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | Word frequencies (Zipf), for ranking and the fallback |
| [ENABLE](https://github.com/MagicOctopusUrn/wordListsByLength) word list (`all_words_blossom.csv`) | Public domain | Fallback vocabulary |
| [LDNOOBW](https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Filter: phrases containing a listed term are left out of the bank. Shipped only as hashes (`phrase_blocklist.txt`), for checking players' added phrases |

Every source allows commercial use, so the page can carry ads. NonCommercial
data is never used, which rules out most lyric and movie-quote datasets.

**Not used any more:** Peter Norvig's `count_1w` / `count_2w` (until
2026-10-11). His MIT licence covers his code only. The data files derive from
the Google Web Trillion Word Corpus, distributed by the Linguistic Data
Consortium, and state no licence of their own, so commercial use couldn't be
confirmed. (`word_popularity.csv`, used by the other solvers, still derives
from `count_1w`.)

### Licences of the files here

These files are **not** covered by the repository's MIT licence:

| File | Licence |
|---|---|
| `phrase_bank.tsv.gz`, `phrase_words.tsv.gz`, `phrase_bigrams.tsv.gz` | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), as adaptations of Wiktionary and wordfreq material (ShareAlike). They also contain WordNet 3.0 material: WordNet 3.0 Copyright 2006 by Princeton University. All rights reserved. Used under the licence in `licenses/WordNet-3.0-LICENSE.txt`, which applies to that material |
| `phrase_blocklist.txt` | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), adapted (hashed) from LDNOOBW |

Changes from the sources: entries are filtered (length, characters, the
offensive-term list), upper-cased, folded to ASCII, de-duplicated and scored.

### Credits the sources ask for

- **Wiktionary:** "Wiktionary contributors", with a link to Wiktionary and the
  licence.
- **wordfreq:** Robyn Speer (2022), rspeer/wordfreq v3.0, Zenodo,
  https://doi.org/10.5281/zenodo.7199437. Its data includes:
  - Google Books Ngrams (http://books.google.com/ngrams), whose terms ask for
    acknowledgement and a link
  - Wikipedia, the Leeds Internet Corpus and ParaCrawl
  - OpenSubtitles 2018 via OPUS, with attribution to OpenSubtitles
    (http://www.opensubtitles.org/)
  - Twitter
  - The SUBTLEX word lists by Marc Brysbaert et al. Their condition for any
    use: credit the SUBTLEX authors, and keep it clear that SUBTLEX is freely
    available (http://crr.ugent.be/programs-data/subtitle-frequencies).
- **WordNet:** the notice above, on every copy of the data. The licence also
  says Princeton's name may not be used in advertising or publicity, so the
  page credits "WordNet 3.0" and doesn't trade on Princeton's name.
- **LDNOOBW:** credit and the licence link (here).

The page's "Data sources and licences" section gives the same credits
publicly.

## How the bank is chosen

- **Wikidata** is queried in groups (`WIKIDATA` in the build script). Each group
  keeps its N items with the most sitelinks (how many Wikipedias cover the
  item). Popularity is `20 * log10(sitelinks + 1)`, and aliases score 6 lower.
  People keep only their main name.
- **WordNet and Wiktionary** popularity is the phrase's rarest word's wordfreq
  Zipf, on the same scale as a single word. Curated lists (proverbs, idioms)
  get a bonus over the generic multiword list.
- **The fallback's word pairs** are counted across the bank's own phrases
  (each phrase once, pairs seen at least twice), so they come from the same
  licensed sources.
- A phrase must fit a board: letters, spaces and the punctuation a board
  shows, at most 10 words and 48 letters. Accents fold to ASCII, and anything
  with digits is dropped.
- **Canaries.** Each Wikidata group lists famous items it must contain
  (The Godfather, Albert Einstein, the Golden Gate Bridge). A missing canary
  stops the build. This guards against the endpoint quietly returning wrong
  results, which QLever did once (below).

## QLever quirks

Each of these cost a wrong build once:

- A `FILTER` on sitelinks returned wrong empty results (no film with more
  than 100 sitelinks, though The Godfather has 132). The script ranks with
  `ORDER BY ... LIMIT` instead.
- A query that runs too long comes back as HTTP **429**, not a timeout. Joining
  labels and aliases in one `UNION` did that, so they are separate queries.
- TSV output silently came back empty for a large join, so the script uses JSON.
- Many items have only a `mul` label (Wikidata's default for all languages),
  with no `en` label. Albert Einstein is one. Labels are taken from `en` or `mul`.
- Items are often an instance of a *subclass*. The Golden Gate Bridge is a
  "suspension bridge", so the script matches `P31/P279*`. Dishes are often
  modelled as subclasses themselves ("guacamole: subclass of food"), so FOOD also
  follows `P279`.

## Rebuilding

```
python scripts/build_phrase_bank.py --check   # class labels, group sizes, canaries
python scripts/build_phrase_bank.py           # writes the four files
```

Downloads are cached in the system temp folder (`phrase_bank_cache`), so a
re-run only hits the network for what's missing. Delete a cached `wd_*.json` to
re-query that group. Wiktionary rate-limits clients that give no contact, so
set `PHRASE_BANK_CONTACT` to an email or URL to include one in the User-Agent.
The Wiktionary multiword list (about 450 pages) takes the longest.
