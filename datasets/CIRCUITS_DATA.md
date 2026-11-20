# Circuits solver data

One file backs the `/circuits` solver (`functions/circuits.py`):
`circuits_data.npz`. `scripts/build_circuits_data.py` builds it offline. The
deployed app reads it from disk and never fetches anything.

The solver needs directed word pairs: "A B" or "AB" is a compound term with
A written first (SNOWBALL, FIRE STATION). Each pair has a weight for how well
established it is.

| Arrays | What they hold |
|---|---|
| `vocab`, `zipf`, `flags` | The words (and two-word units such as ICE CREAM), sorted; each one's frequency, and whether it may be an answer |
| `succ_ptr`, `succ_ids`, `succ_w` | For each word, the words that can follow it, with weights (uint8, weight x 32) |
| `pred_ptr`, `pred_ids`, `pred_w` | For each word, the words that can come before it |

The current file was built on 2026-11-19 from the English Wikipedia dump of
that week (7.26 million articles). It holds 1,313,588 directed pairs over
112,531 words: 16.3 MB in memory, 8.5 MB on disk.

| Source | Pairs |
|---|---|
| Closed compounds | 28,485 |
| Phrase bank two-word phrases | 231,144, plus 34,339 with a two-word unit |
| Wikipedia text | 1,121,365, of which 1,022,334 no list has |
| Wikipedia titles (confirming only) | about 615,000 of the above confirmed |
| After combining | 1,313,588 |

## Sources and licences

| Source | Licence | Used for |
|---|---|---|
| [Wikipedia](https://en.wikipedia.org) article text and title list, by Wikipedia contributors | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | How often two words sit side by side (everyday compounds no dictionary lists), and titles that confirm a pair |
| `phrase_bank.tsv.gz` (see [`PHRASE_BANK.md`](PHRASE_BANK.md)): Wiktionary, WordNet 3.0 and Wikidata | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), WordNet licence, [CC0](https://creativecommons.org/publicdomain/zero/1.0/) | Two-word phrases |
| `phrase_words.tsv.gz` (see [`PHRASE_BANK.md`](PHRASE_BANK.md)): [wordfreq](https://github.com/rspeer/wordfreq) by Robyn Speer | Data [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | Word frequencies (Zipf) and the vocabulary |
| [ENABLE](https://github.com/MagicOctopusUrn/wordListsByLength) word list (`all_words_blossom.csv`) | Public domain | Closed compounds, and which words may be answers |
| [ConceptNet Numberbatch](https://github.com/commonsense/conceptnet-numberbatch) 19.08, by Luminoso Technologies, Inc., via `chainle_data.npz` (see [`CHAINLE_DATA.md`](CHAINLE_DATA.md)) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | At build time only: whether a closed compound has to do with its parts (CAR + PET counts for less) |
| `phrase_blocklist.txt` (see [`PHRASE_BANK.md`](PHRASE_BANK.md)), from [LDNOOBW](https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Filter: listed words are left out |

Every source allows commercial use, so the page can carry ads. No puzzle,
answer or word list from Circuits itself is in this file. Pairs added by hand
on the admin page from a day's real answers live in the database, not here.

### Licence of the file here

`circuits_data.npz` is **not** covered by the repository's MIT licence. It is
shared under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), as an
adaptation of Wikipedia, Wiktionary and wordfreq material (ShareAlike). It
also contains WordNet 3.0 material: WordNet 3.0 Copyright 2006 by Princeton
University. All rights reserved. Used under the licence in
`licenses/WordNet-3.0-LICENSE.txt`, which applies to that material.

Changes from the sources: words are filtered (letters only, frequency, the
offensive-word list), word pairs are counted and scored, and the result is
packed into arrays.

### Credits the sources ask for

- **Wikipedia and Wiktionary:** "Wikipedia contributors" and "Wiktionary
  contributors", with links and the licence.
- **ConceptNet Numberbatch, WordNet, wordfreq and LDNOOBW:** as in
  `CHAINLE_DATA.md` and `PHRASE_BANK.md`.

The page's "Data sources and licences" section gives the same credits
publicly.

## How the pairs are chosen

- **Closed compounds:** an ENABLE word made of two known words, in both orders
  where both exist (HOUSEBOAT and BOATHOUSE).
  - Both parts need a Zipf of 2.5 or more and 3+ letters (UP, IN, ON, BY and
    OX excepted).
  - A word much more common than its rarer part is an accidental split
    (THROUGH is not THRO + UGH), so it is left out.
  - A split whose meaning sits far from both parts counts for less.
- **Two-word phrases** from the phrase bank, weighted by the kinds of list
  that carry them: dictionary nouns most, other phrase lists less, names alone
  least. Three-word phrases split around a two-word phrase give two-word units
  (ICE CREAM + TRUCK), so a two-word tile still finds its box.
- **Wikipedia text:** adjacent pairs of known words are counted across the
  English Wikipedia. A pair is kept when it is seen at least 20 times, at
  least twice as often as chance, and its log-likelihood score (G2) is high.
  G2 rather than pointwise mutual information (PMI), because PMI favours rare
  pairs: ranked by PMI, PAPER's best partners are MACHE, CUTOUTS and
  SHREDDERS; ranked by G2 they are MILL, MONEY and BAG. Each word keeps its
  best partners in each direction, and new pairs are kept best first until
  the file's budget is spent. The text finds encyclopedia phrases far better
  than everyday ones: a phrase common in speech but rare in articles can
  still be missing.
- **Wikipedia titles** only confirm a pair another source has. On their own
  they are mostly people, films and places: about 3 million pairs.
- **Weight:** the strongest source's weight, plus a bonus for each other kind
  of source that lists the pair, plus a title bonus, capped.
- **Budget:** at most 1.5 million pairs and 20 MB in memory. The build fails
  rather than grow past it.
- **Canaries:** SNOW + BALL, FIRE + STATION, FOOT + BALL and TOOTH + BRUSH
  present, HOUSE + BOAT and BOAT + HOUSE both present, THRO + UGH absent, or the
  build writes nothing.

## Rebuilding

```
python scripts/build_circuits_data.py --count    # count word pairs in Wikipedia's text (downloads 27 GB once)
python scripts/build_circuits_data.py --report   # sizes, canaries and sample rows, without writing
python scripts/build_circuits_data.py            # write datasets/circuits_data.npz
python scripts/backtest_circuits.py              # the solver on made-up boards
```

The build reads Wikipedia's `latest/` folder, so a rebuild uses whatever dump
is current then and won't reproduce this file exactly.

Downloads are cached in the system temp folder (`circuits_data_cache`):
Wikipedia's title list (110 MB) and its article dump in 71 parts (27 GB). The
count downloads two parts at a time and counts each part as it lands, on all
but a few cores; it takes about as long as the download. Wikipedia's `latest/`
folder also lists the last part of older dumps, and dead links; the build
counts only the newest copy of each part, so no article is counted twice.
