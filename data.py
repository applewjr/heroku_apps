"""Static datasets loaded once at startup."""

import json
import os

import pandas as pd
import yaml

from functions import all_words

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
data_folder = os.path.join(APP_ROOT, 'datasets')

df = pd.read_csv(os.path.join(data_folder, 'word_data_created.csv'))

word_df = pd.read_csv(os.path.join(data_folder, 'all_words_blossom.csv')) # changed to main "word_df" on 8/24/2024
words = set(word_df['0'].to_list())
words_blossom = all_words.filter_words_for_blossom(words)

# word -> Zipf popularity (log10 frequency per billion words, ~1 rare .. ~7.6
# "the"). Base values come from Peter Norvig's count_1w.txt (Google Web
# Trillion Word Corpus); scripts/build_word_popularity.py then adds wordfreq
# gap-fills for ~14.5k dictionary words Norvig lacks, lifting coverage to ~54%.
# The gap-fills only ADD low-frequency words and never change a Norvig value,
# so the Smush solver's popularity tier floors stay calibrated. Dictionary
# words still absent (genuinely obscure) aren't listed: treat as 0.
_pop_df = pd.read_csv(os.path.join(data_folder, 'word_popularity.csv'))
word_pop = dict(zip(_pop_df['word'], _pop_df['zipf']))

with open(os.path.join(data_folder, 'espresso_brew_points.json'), 'r') as json_file:
    espresso_points = json.load(json_file)

with open(os.path.join(data_folder, 'etl_dash_queries.yaml'), 'r') as file:
    etl_dash_queries = yaml.safe_load(file)
