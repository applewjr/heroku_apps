"""
Wordle solver backtester.

Strategy: if Alt Pick 1 is available, use it; otherwise use Pick 1.
Runs against every word in the dataset and reports success rate and guess distribution.
"""

import sys
import re
import time
import multiprocessing as mp
import pandas as pd
from collections import Counter

sys.path.insert(0, 'c:/Users/james/projects/heroku_apps')
from functions.wordle import wordle_solver_split_revamp, compute_alt_picks


DATASET_PATH = 'c:/Users/james/projects/heroku_apps/datasets/word_data_created.csv'
MAX_GUESSES = 6

_worker_df = None  # per-process DataFrame, loaded once via pool initializer


def _init_worker(dataset_path):
    global _worker_df
    _worker_df = pd.read_csv(dataset_path)


def _simulate_one_worker(target):
    return target, simulate_game(_worker_df, target, trace=False)


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


COLOR_LABEL = {'1': 'gray', '2': 'yellow', '3': 'green'}
COLOR_CHAR  = {'1': '.', '2': '?', '3': '#'}  # . gray  ? yellow  # green


def simulate_game(df: pd.DataFrame, target: str, trace: bool = False) -> int | None:
    """
    Simulate one game. Returns number of guesses to solve, or None if failed.
    With trace=True, prints each step so you can verify against the real UI.
    """
    if trace:
        print(f'\n--- Target: {target.upper()} ---')

    history = []

    for attempt in range(1, MAX_GUESSES + 1):
        wordle_data = build_wordle_data(history)

        pick1, pick2, pick3, pick4, pick5, options_remaining, _, _, gray_letters, guessed_set = \
            wordle_solver_split_revamp(df, wordle_data)

        picks = [pick1, pick2, pick3, pick4, pick5]
        show_alt, alt1, *_ = compute_alt_picks(df, picks, gray_letters, guessed_set)

        if show_alt and extract_word(alt1):
            guess = extract_word(alt1)
            source = f'Alt Pick 1  ({alt1})'
        else:
            guess = extract_word(pick1)
            source = f'Pick 1      ({pick1})'

        if guess is None:
            if trace:
                print(f'  Attempt {attempt}: solver has no candidates — FAIL')
            return None

        colors = color_guess(guess, target)
        colored = ' '.join(f'{g.upper()}({COLOR_CHAR[c]})' for g, c in zip(guess, colors))

        if trace:
            legend = '  '.join(f'{g.upper()}={COLOR_LABEL[c]}' for g, c in zip(guess, colors))
            print(f'  Attempt {attempt}: {source}')
            print(f'    Guess:  {colored}')
            print(f'    Colors: {legend}')
            print(f'    {options_remaining}')

        history.append((guess, colors))

        if guess == target:
            if trace:
                print(f'  Solved in {attempt}!')
            return attempt

    if trace:
        print(f'  FAILED after {MAX_GUESSES} guesses')
    return None


def run_backtest(word_list: list[str] | None = None, sample: int | None = None,
                 order: str = 'alpha', verbose: bool = False, trace: bool = False,
                 workers: int = 1):
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
    t0 = time.time()

    if trace or workers == 1:
        for i, target in enumerate(targets):
            if not trace and i % 500 == 0:
                print(f'  {i}/{len(targets)}...')
            tries = simulate_game(df, target, trace=trace)
            if tries is not None:
                results.append(tries)
            else:
                fails.append(target)
                if verbose and not trace:
                    print(f'  FAIL: {target}')
    else:
        n = len(targets)
        chunk = max(1, n // (workers * 8))
        print(f'  Running {n} games across {workers} workers...')
        with mp.Pool(workers, initializer=_init_worker, initargs=(DATASET_PATH,)) as pool:
            done = 0
            for target, tries in pool.imap(_simulate_one_worker, targets, chunksize=chunk):
                done += 1
                if done % 500 == 0:
                    elapsed = time.time() - t0
                    rate = done / elapsed
                    remaining = (n - done) / rate
                    print(f'  {done}/{n}  ({rate:.0f} games/s, ~{remaining:.0f}s remaining)')
                if tries is not None:
                    results.append(tries)
                else:
                    fails.append(target)
                    if verbose:
                        print(f'  FAIL: {target}')

    elapsed = time.time() - t0
    total = len(targets)
    solved = len(results)
    print(f'\n=== Backtest Results ({total} words, {elapsed:.1f}s) ===')
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
    _default_workers = max(1, mp.cpu_count() // 2)  # physical cores, not hyperthreads
    parser.add_argument('--workers', type=int, default=_default_workers,
                        help=f'Parallel worker processes (default: {_default_workers} = physical core count)')
    parser.add_argument('--verbose', action='store_true', help='Print each failed word as it happens')
    parser.add_argument('--trace', action='store_true',
                        help='Print full step-by-step play for every word (best with --words or small --sample)')
    args = parser.parse_args()

    run_backtest(word_list=args.words, sample=args.sample, order=args.order,
                 verbose=args.verbose, trace=args.trace, workers=args.workers)

"""
python backtest_wordle.py --words aging --trace
python backtest_wordle.py --words crane abbey pizza about doggy sword --verbose
python backtest_wordle.py --sample 100
python backtest_wordle.py --sample 100 --order random
python backtest_wordle.py
python backtest_wordle.py --workers 4
python backtest_wordle.py --sample 100 --workers 6
"""
