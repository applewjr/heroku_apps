"""Augment datasets/word_popularity.csv with wordfreq gap-fills.

The file's original values come from Norvig's count_1w (Google Web Trillion
Word Corpus) and cover ~46% of the dictionary; the Smush solver's popularity
tier floors are calibrated against them. This script ADDS one row per
dictionary word the file lacks but the `wordfreq` library (merged corpora,
'large' English list) knows, lifting coverage to ~54%. Existing rows are
preserved verbatim (byte-identical), so Smush's calibration is untouched -
the added words are all low-frequency and only ever raise a plan's popularity
floor, never lower it. Idempotent: re-running finds nothing new to add.

Build-time only - `wordfreq` is never imported at runtime and stays out of
requirements.txt. Run manually to regenerate:

    python scripts/build_word_popularity.py
"""

import os

import pandas as pd
from wordfreq import zipf_frequency

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, os.pardir, 'datasets')
POP_PATH = os.path.join(DATA, 'word_popularity.csv')

words = set(pd.read_csv(os.path.join(DATA, 'all_words_blossom.csv'))['0'].astype(str))

# Keep existing rows as raw text so their values stay byte-identical.
with open(POP_PATH, encoding='utf-8') as fh:
    header = fh.readline().rstrip('\n')
    existing = {}  # word -> raw "word,zipf" line
    for line in fh:
        line = line.rstrip('\n')
        if line:
            existing[line.split(',', 1)[0]] = line

added = 0
for w in words:
    if w in existing:
        continue  # never touch a calibrated value
    z = zipf_frequency(w, 'en', wordlist='large')
    if z > 0:
        existing[w] = f'{w},{round(z, 2)}'
        added += 1

rows = [existing[w] for w in sorted(existing)]
with open(POP_PATH, 'w', encoding='utf-8', newline='\n') as fh:
    fh.write(header + '\n')
    fh.write('\n'.join(rows) + '\n')

covered = len(rows)
print(f'dictionary words : {len(words)}')
print(f'gap-fills added   : {added}')
print(f'total rows        : {covered}  ({100 * covered / len(words):.1f}% of dictionary)')
