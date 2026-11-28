# Firsts solver data

One file backs the `/firsts` solver (`functions/firsts.py`):
`firsts_data.npz`. `scripts/build_firsts_data.py` builds it offline. The
deployed app reads it from disk and never fetches anything.

The solver needs two things: the answers it may suggest (words and phrases,
by letter count, with how common each is), and what those answers mean, so
it can match them to a clue.

| Arrays | What they hold |
|---|---|
| `lex_2` ... `lex_15` | The answers of each letter count, one per line: upper case with spaces between words, then one lower-case letter for its popularity (Zipf in steps of 0.3), most popular first |
| `lem_blob`, `lem_off` | WordNet 3.0 lemmas, letters only and sorted (EMAIL, LIONSSHARE), searched by bisection |
| `lem_ptr`, `lem_syn` | Each lemma's senses (synsets), most used first |
| `syn_ptr`, `syn_tok`, `syn_tok_hi` | Each sense's words: the stems of its definition (quoted examples left out), its lemmas and its parents' lemmas. Token ids take 17 bits: the low 16 in `syn_tok`, the top bit packed in `syn_tok_hi` |
| `tok_blob`, `tok_off`, `tok_idf` | The token stems, sorted, and each one's IDF over the senses (times 16) |
| `version` | The build's label |

The current file was built on 2026-11-27. It holds 461,598 answers (164,334
of them phrases) and 117,659 WordNet senses over 140,003 lemmas and 78,439
tokens: 11.9 MiB in memory, 6.1 MB on disk. The build stops if the arrays
pass 12 MiB.

The word vectors the solver also uses are not in this file: they are the
Chainle solver's (`chainle_data.npz`, see [`CHAINLE_DATA.md`](CHAINLE_DATA.md)),
already in memory for `/chainle` and `/4x3`.

## Sources and licences

| Source | Licence | Used for |
|---|---|---|
| [WordNet 3.0](https://wordnet.princeton.edu/), Princeton University | WordNet 3.0 licence (`licenses/WordNet-3.0-LICENSE.txt`) | Definitions, lemmas and parent terms; also lemmas as answers |
| `phrase_bank.tsv.gz` (see [`PHRASE_BANK.md`](PHRASE_BANK.md)): Wiktionary, WordNet 3.0 and Wikidata | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), WordNet licence, [CC0](https://creativecommons.org/publicdomain/zero/1.0/) | Phrases and names as answers, with their popularity |
| `phrase_words.tsv.gz` (see [`PHRASE_BANK.md`](PHRASE_BANK.md)): [wordfreq](https://github.com/rspeer/wordfreq) by Robyn Speer | Data [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | Single words and how common they are (Zipf) |
| [ENABLE](https://github.com/MagicOctopusUrn/wordListsByLength) word list (`all_words_blossom.csv`) | Public domain | Single words |
| `phrase_blocklist.txt` (see [`PHRASE_BANK.md`](PHRASE_BANK.md)), from [LDNOOBW](https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Filter: listed words are left out |

Every source allows commercial use, so the page can carry ads. No puzzle,
clue, answer or word list from Firsts itself is in this file. Answers
checked by hand on the admin page live in the database, not here.

### Licence of the file here

`firsts_data.npz` is **not** covered by the repository's MIT licence. It is
shared under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), as an
adaptation of Wiktionary and wordfreq material (ShareAlike). It also
contains WordNet 3.0 material: WordNet 3.0 Copyright 2006 by Princeton
University. All rights reserved. Used under the licence in
`licenses/WordNet-3.0-LICENSE.txt`, which applies to that material.

Changes from the sources: words are filtered (letters only, 2 to 15 letters,
popularity, the offensive-word list), definitions are cut to their meaningful
words and stemmed, and the result is packed into arrays.

### Credits the sources ask for

- **WordNet:** Princeton University "About WordNet." WordNet. Princeton
  University. 2010.
- **Wiktionary:** "Wiktionary contributors", with a link and the licence.
- **wordfreq and LDNOOBW:** as in `PHRASE_BANK.md`.

## How the answers are chosen

- **Single words:** every ENABLE word, every wordfreq word with a Zipf of 1.5
  or more, every WordNet lemma, and phrase-bank single words (names and
  places) from popularity 10.
- **Phrases:** everyday ones (the phrase bank's PHRASE, PROVERB, THING, FOOD
  and ANIMAL kinds: HOLY COW, ICE CREAM) from popularity 14; names and titles
  (people, places, films, songs) only from popularity 30; and WordNet's
  multi-word lemmas.
- The phrase bank's popularity (0 to about 53) is divided by 10 to sit on the
  Zipf scale.
- Phrases no list has (NO SUGAR, EXIT ROW) are not stored: the solver builds
  them on the board from common words when a clue's length has more than one
  word.

To rebuild: `python scripts/build_firsts_data.py` (downloads the 12 MB
WordNet 3.0 tarball into a cache folder outside the repo, the same one
`scripts/build_chainle_data.py` uses).
