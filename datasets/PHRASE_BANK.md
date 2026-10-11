# Guess The Phrase data

Four files back the `/guess_the_phrase` solver (`functions/phrase_solver.py`,
`routes/guess_the_phrase.py`).
`scripts/build_phrase_bank.py` builds them offline. The deployed app reads them
from disk and never fetches anything.

| File | Rows | What it is |
|---|---|---|
| `phrase_bank.tsv.gz` | 662,111 | Known phrases: match key, category tags (bitmask over `phrase_solver.TAGS`), popularity, display form where it differs from the key |
| `phrase_words.tsv.gz` | 388,258 | Fallback vocabulary, most common first: word, Zipf frequency |
| `phrase_bigrams.tsv.gz` | 245,649 | Word pairs seen on the web: pair, log10 count |
| `phrase_blocklist.txt` | 399 | Offensive terms, as `sha256[:16]` hashes: players' added phrases are checked against it |

Built 2026-10-11. In memory they take about 23 MB together, loaded once per
process on first use (about 1 s).

## Sources and licences

| Source | Licence | Used for |
|---|---|---|
| [Wikidata](https://www.wikidata.org), queried through [QLever](https://qlever.dev) | [CC0 1.0](https://www.wikidata.org/wiki/Wikidata:Licensing) | Titles (movies, songs, albums, TV, books, games, paintings), bands, people, characters, places, landmarks, space, food, events, animals |
| [WordNet 3.0](https://wordnet.princeton.edu) | [WordNet licence](https://wordnet.princeton.edu/license-and-commercial-use) (permissive, commercial use allowed) | Everyday multiword terms, tagged by WordNet's own noun groups |
| [Wiktionary](https://en.wiktionary.org) category listings | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | Proverbs, idioms, similes, phrases, multiword terms |
| Peter Norvig's [`count_1w` / `count_2w`](https://norvig.com/ngrams/) (Google Web Trillion Word Corpus) | Same source as `word_popularity.csv` | Word and word-pair frequencies (ranking and the fallback) |
| [LDNOOBW](https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words) | CC BY 4.0 | Filter: phrases containing a listed term are left out of the bank. It ships only as hashes (`phrase_blocklist.txt`), for checking players' added phrases |

**`phrase_bank.tsv.gz` is licensed CC BY-SA 4.0** because it contains Wiktionary
material. The page's FAQ credits Wikidata, WordNet and Wiktionary.

All of these allow commercial use, so the page can carry ads. NonCommercial
data is never used, which rules out most lyric and movie-quote datasets.

## How the bank is chosen

- **Wikidata** is queried in groups (`WIKIDATA` in the build script). Each group
  keeps its N items with the most sitelinks (how many Wikipedias cover the
  item). Popularity is `20 * log10(sitelinks + 1)`, and aliases score 6 lower.
  People keep only their main name.
- **WordNet and Wiktionary** popularity comes from web counts of the phrase's
  word pairs, scored by the rarest pair. A phrase whose pairs aren't all seen
  gets a low floor. Curated lists (proverbs, idioms) get a bonus over the
  generic multiword list.
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
