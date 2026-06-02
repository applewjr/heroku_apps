"""
Wordle solver backtester.

Strategy: if Alt Pick 1 is available, use it; otherwise use Pick 1.
Runs against every word in the dataset and reports success rate and guess distribution.
"""

import sys
import re
import pandas as pd
from collections import Counter

sys.path.insert(0, 'c:/Users/james/projects/heroku_apps')
from functions.wordle import wordle_solver_split_revamp, compute_alt_picks


DATASET_PATH = 'c:/Users/james/projects/heroku_apps/datasets/word_data_created.csv'
MAX_GUESSES = 6


def load_df():
    return pd.read_csv(DATASET_PATH)


def color_guess(guess: str, target: str) -> list[str]:
    """Return list of 5 color codes ('1'=gray, '2'=yellow, '3'=green)."""
    colors = ['1'] * 5
    target_remaining = list(target)

    # First pass: greens
    for i in range(5):
        if guess[i] == target[i]:
            colors[i] = '3'
            target_remaining[i] = None

    # Second pass: yellows
    for i in range(5):
        if colors[i] == '3':
            continue
        if guess[i] in target_remaining:
            colors[i] = '2'
            target_remaining[target_remaining.index(guess[i])] = None

    return colors


def build_wordle_data(history: list[tuple[str, list[str]]]) -> list[dict]:
    """Convert guess history into the wordle_data_dict format the solver expects."""
    data = []
    for row_idx, (word, colors) in enumerate(history, start=1):
        for pos_idx, (letter, color) in enumerate(zip(word, colors), start=1):
            data.append({
                'letter': letter,
                'position': str(pos_idx),
                'color': color,
                'row': str(row_idx),
            })
    return data


def extract_word(pick_str: str) -> str | None:
    """Extract word from 'Pick 1: xxxxx' or 'Alt Pick 1: xxxxx (N match)'."""
    if not pick_str:
        return None
    m = re.match(r'^(?:Alt )?Pick \d+: ([A-Za-z]+)', pick_str)
    return m.group(1).lower() if m else None


def simulate_game(df: pd.DataFrame, target: str) -> int | None:
    """
    Simulate one game. Returns number of guesses to solve, or None if failed.
    """
    history = []

    for attempt in range(1, MAX_GUESSES + 1):
        wordle_data = build_wordle_data(history)

        pick1, pick2, pick3, pick4, pick5, _, _, _, gray_letters, guessed_set = \
            wordle_solver_split_revamp(df, wordle_data)

        picks = [pick1, pick2, pick3, pick4, pick5]
        show_alt, alt1, *_ = compute_alt_picks(df, picks, gray_letters, guessed_set)

        if show_alt and extract_word(alt1):
            guess = extract_word(alt1)
        else:
            guess = extract_word(pick1)

        if guess is None:
            return None  # Solver has no candidates

        colors = color_guess(guess, target)
        history.append((guess, colors))

        if guess == target:
            return attempt

    return None  # Failed within MAX_GUESSES


def run_backtest(word_list: list[str] | None = None, sample: int | None = None,
                 order: str = 'alpha', verbose: bool = False):
    df = load_df()
    if word_list is not None:
        targets = word_list
    else:
        all_words = df['word'].tolist()
        if order == 'random':
            import random
            all_words = random.sample(all_words, len(all_words))
        else:
            all_words = sorted(all_words)
        targets = all_words[:sample] if sample is not None else all_words

    results = []
    fails = []

    for i, target in enumerate(targets):
        if i % 500 == 0:
            print(f'  {i}/{len(targets)}...')
        tries = simulate_game(df, target)
        if tries is not None:
            results.append(tries)
        else:
            fails.append(target)
            if verbose:
                print(f'  FAIL: {target}')

    total = len(targets)
    solved = len(results)
    print(f'\n=== Backtest Results ({total} words) ===')
    print(f'Solved:  {solved}/{total}  ({solved/total*100:.1f}%)')
    print(f'Failed:  {len(fails)}/{total}  ({len(fails)/total*100:.1f}%)')
    if results:
        print(f'Avg tries (wins only): {sum(results)/len(results):.2f}')
        dist = Counter(results)
        print('\nGuess distribution:')
        for k in sorted(dist):
            bar = '#' * dist[k]
            print(f'  {k}: {dist[k]:4d}  {bar}')
    if fails:
        print(f'\nFailed words: {fails}')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Backtest the Wordle solver')
    parser.add_argument('--words', nargs='*', help='Specific words to test (default: all)')
    parser.add_argument('--sample', type=int, default=None, help='Test N words instead of all')
    parser.add_argument('--order', choices=['alpha', 'random'], default='alpha',
                        help='Word order when sampling: alpha (default) or random')
    parser.add_argument('--verbose', action='store_true', help='Print each failed word as it happens')
    args = parser.parse_args()

    run_backtest(word_list=args.words, sample=args.sample, order=args.order, verbose=args.verbose)

# python backtest_wordle.py --words crane abbey pizza about doggy sword --verbose
# python backtest_wordle.py --sample 100
# python backtest_wordle.py --sample 100 --order random
# python backtest_wordle.py

