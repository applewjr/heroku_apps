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

with open(os.path.join(data_folder, 'espresso_brew_points.json'), 'r') as json_file:
    espresso_points = json.load(json_file)

with open(os.path.join(data_folder, 'etl_dash_queries.yaml'), 'r') as file:
    etl_dash_queries = yaml.safe_load(file)
