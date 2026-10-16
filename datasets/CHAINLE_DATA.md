# Chainle solver data

One file backs the `/chainle` solver (`functions/chainle.py`, `routes/chainle.py`):
`chainle_data.npz`. `scripts/build_chainle_data.py` builds it offline. The
deployed app reads it from disk and never fetches anything.

| Arrays | What they hold |
|---|---|
| `vocab`, `zipf`, `flags`, `pos` | 72,106 words, sorted; each word's frequency, whether it may be suggested (51,216 may), and its parts of speech |
| `vec`, `scale` | A word vector per word: unit length, int8 with one scale per word |
| `base_ptr`, `base_ids` | Each word's base forms (FEET to FOOT), so a clue's other forms aren't suggested |
| `link_*` | 188,103 links: compound words (SNOWBALL) and two-word phrases (FIRE STATION) joining two words, with a weight and which word comes first. They label a word for the page's hints and don't change its rank |
| `syn_*`, `rel_*`, `ant_*` | Synonyms, each word's family (a main sense's parent and its other kinds), and antonyms |

Built 2026-10-15: 22.4 MB on disk, about 29 MB in memory, loaded once per
process on first use (about 0.1 s). `datasets/chainle_eval.tsv` holds
hand-written test rounds for `scripts/eval_chainle.py`.

## Sources and licences

| Source | Licence | Used for |
|---|---|---|
| [ConceptNet Numberbatch](https://github.com/commonsense/conceptnet-numberbatch) 19.08 English vectors (300 dimensions), Luminoso Technologies, Inc. | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | Word vectors: how close two words are in meaning |
| [WordNet 3.0](https://wordnet.princeton.edu) | WordNet 3.0 licence, in full in [`licenses/WordNet-3.0-LICENSE.txt`](licenses/WordNet-3.0-LICENSE.txt). "Any purpose", so commercial use is allowed | Synonyms, antonyms, families, base forms (its morphy rules and exception lists), parts of speech, and part of the vocabulary |
| `phrase_bank.tsv.gz` (see [`PHRASE_BANK.md`](PHRASE_BANK.md)): Wiktionary, WordNet 3.0 and Wikidata | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), WordNet licence, [CC0](https://creativecommons.org/publicdomain/zero/1.0/) | Two-word phrases, as links |
| `phrase_words.tsv.gz` (see [`PHRASE_BANK.md`](PHRASE_BANK.md)): [wordfreq](https://github.com/rspeer/wordfreq) by Robyn Speer | Data [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | Word frequencies (Zipf) |
| [ENABLE](https://github.com/MagicOctopusUrn/wordListsByLength) word list (`all_words_blossom.csv`) | Public domain | The vocabulary and its compound words |
| `phrase_blocklist.txt` (see [`PHRASE_BANK.md`](PHRASE_BANK.md)), from [LDNOOBW](https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Filter: listed words are left out of the vocabulary |

Every source allows commercial use, so the page can carry ads. Peter Norvig's
n-gram counts are not used: their data states no licence of its own (see
`PHRASE_BANK.md`).

### Licence of the file here

`chainle_data.npz` is **not** covered by the repository's MIT licence. It is
shared under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), as an
adaptation of ConceptNet Numberbatch, Wiktionary and wordfreq material
(ShareAlike). This data contains semantic vectors from ConceptNet
Numberbatch, by Luminoso Technologies, Inc. You may redistribute or modify
the data under the terms of the CC BY-SA 4.0 license. It also contains
WordNet 3.0 material: WordNet 3.0 Copyright 2006 by Princeton University. All
rights reserved. Used under the licence in `licenses/WordNet-3.0-LICENSE.txt`,
which applies to that material.

Changes from the sources: words are filtered (length, letters only,
frequency, the offensive-word list), the vectors are normalised and
quantised, and links are scored.

### Credits the sources ask for

- **ConceptNet Numberbatch:** due credit to Luminoso Technologies, Inc.
  (the attribution above, its suggested text), and ShareAlike for anything
  built from it. Made by Robyn Speer, Joshua Chin, Catherine Havasi and
  Joanna Lowry-Duda; cited as Robyn Speer, Joshua Chin and Catherine Havasi,
  "ConceptNet 5.5: An Open Multilingual Graph of General Knowledge", AAAI
  2017. It is built from ConceptNet 5.7 (data from Wiktionary, WordNet and
  contributors to Open Mind Common Sense projects); GloVe, by Jeffrey
  Pennington, Richard Socher and Christopher Manning; word2vec, by Tomas
  Mikolov and Google Research; and parallel text from OpenSubtitles 2016, by
  Pierre Lison and Jörg Tiedemann, analyzed with fastText, by Piotr
  Bojanowski, Edouard Grave, Armand Joulin and Tomas Mikolov.
- **WordNet:** the notice above, on every copy of the data. Princeton's name
  may not be used in advertising or publicity.
- **Wiktionary, wordfreq and LDNOOBW:** as in `PHRASE_BANK.md`.

The page's "Data sources and licences" section gives the same credits
publicly.

## How the data is chosen

- **Vectors:** Numberbatch's 300-dimension English vectors, used as they
  are and scaled to unit length. Numberbatch blends word use (GloVe,
  word2vec, fastText) with ConceptNet's network of what words mean, so
  synonyms and parts of things sit close together. Their cosines match the
  closeness Chainle shows after a guess far better than any GloVe build
  tried (see `scripts/build_chainle_data.py`).

- **Vocabulary:** ENABLE words and WordNet's single-word lemmas, 2 to 15
  letters, with a Zipf frequency of at least 1.5 and a Numberbatch vector.
  - A word may be suggested if its Zipf is at least 2.0, it has 3 or more
    letters, it isn't a function word ("the", "of", "like") and it is in the
    dictionary (not only a name).
  - A short list of broad words (thing, good, great) is flagged: the solver
    counts them for less in bridge rounds.
- **Compound-word links:** a dictionary word made of two known words, SNOW +
  BALL, weighted by how common the compound is (its own Zipf).
  - A split whose vector sits far from both parts' counts for less (CAR +
    PET = CARPET).
- **Two-word phrase links,** from the phrase bank. The bank rates a phrase
  by how common its words are, not the phrase itself, so the weight comes
  from the kinds of source that list it:
  - an established compound is both a dictionary noun (WordNet's things,
    foods, animals) and a dictionary phrase, and often a title too;
  - a phrase that only one phrase list carries counts for less;
  - names alone barely count.
- **Damping:** links through a function word, or through a particle (UP,
  OUT, OVER), count for less. Those are mostly phrasal verbs, not the
  compounds a bridge means.
- **Synonyms:**
  - words sharing a WordNet synset, in any sense;
  - for adjectives, also the synset they're "similar to" (BIG, LARGE);
  - senses where WordNet capitalises the word are names (a country, a
    person), so they are skipped.
- **Family:** for each word's first two noun or verb senses (WordNet lists
  the most used first), the sense's parent and the parent's other kinds,
  the 12 most common (DOG: CANINE, WOLF, FOX). A parent with more than 80
  kinds is too broad to say much, so it is skipped.
- **Antonyms:**
  - WordNet's direct antonyms;
  - a satellite adjective also takes its head's (HUGE, SMALL).
- **Canaries:** a few plain facts the build must get right, or it writes
  nothing:
  - KING near QUEEN, HUGE near BIG, and KNEE near LEG;
  - SNOW + BALL and FIRE + STATION linked;
  - LARGE a synonym of BIG, and SMALL an antonym of LARGE;
  - FEET to FOOT, and RUNNING to RUN.

## Rebuilding

```
python scripts/build_chainle_data.py                            # the shipped file
python scripts/build_chainle_data.py --vectors glove            # GloVe 2024, to compare
python scripts/build_chainle_data.py --vectors glove --dims 100 # another GloVe size (50, 100, 200, 300)
python scripts/eval_chainle.py --misses                         # the hand-written test rounds
```

Downloads are cached in the system temp folder (`chainle_data_cache`), so a
re-run only hits the network for what's missing:
- the Numberbatch English file: 325 MB (the GloVe file instead, with
  `--vectors glove`: 1.6 GB for 300 dimensions);
- the full WordNet 3.0 release: 12 MB. It is needed for its exception lists,
  which the database-only tarball the phrase bank uses doesn't include.

Set `PHRASE_BANK_CONTACT` to include a contact in the User-Agent. The phrase
bank files must be built first (`scripts/build_phrase_bank.py`).
