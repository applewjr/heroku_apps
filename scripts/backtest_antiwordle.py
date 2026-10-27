"""
Antiwordle solver backtester.

Antiwordle is won by lasting: every guess must keep the grays out, the greens
in place and the yellows somewhere, and the game ends on the guess that is the
answer. A game's score is the guesses made, that last one included.

Strategy: play Pick 1 every turn. --strategy old replays the ranking the solver
used before its overhaul (lowest word_score, with a 0.75 bonus for a yellow
kept in its yellow spot), so the two can be compared on the same targets.
Runs against every word in the dataset, or the most common ones, and reports
the average game length and its distribution.

Full runs, average guesses (all 5710 words / the 2300 most popular):
  old ranking                    7.80 / 8.00
  live                           9.21 / 10.06
  live, --no-popularity          9.41 / 9.73
Tried on 600 random targets and dropped: playing the rarest word once 10 or
25 or fewer are playable (8.38 / 9.31 and 7.87 / 8.78), and an exact search
of every order once 20 or fewer are playable (+0.04).
"""

import sys
import time
import random
import multiprocessing as mp
import pandas as pd
from collections import Counter
from statistics import median

sys.path.insert(0, 'c:/Users/james/projects/heroku_apps')
sys.path.insert(0, 'c:/Users/james/projects/heroku_apps/scripts')
from functions.wordle import antiwordle_solver_split_revamp, _read_board, _filter_candidates
from backtest_wordle import color_guess, build_wordle_data, extract_word, COLOR_CHAR


DATASET_PATH = 'c:/Users/james/projects/heroku_apps/datasets/word_data_created.csv'
MAX_GUESSES = 100  # safety cap; a game ends on the answer long before this
PAGE_ROWS = 30     # rows on the /antiwordle page

_worker = {}  # per-process DataFrame and settings, loaded once via pool initializer


def _init_worker(dataset_path, settings):
    _worker['df'] = pd.read_csv(dataset_path)
    _worker['settings'] = settings


def _simulate_one_worker(target):
    return target, simulate_game(_worker['df'], target, **_worker['settings'])


def load_df():
    return pd.read_csv(DATASET_PATH)


def old_pick(df: pd.DataFrame, wordle_data: list[dict]) -> str | None:
    """Pick 1 from the ranking before the overhaul: the playable word with
    the lowest word_score, a yellow kept in its yellow spot scoring 0.75."""
    _, _, gray, greens, yellows, guessed = _read_board(wordle_data)
    cand = _filter_candidates(df.copy(), gray, greens, [''] * 5)
    required = ''.join(yellows)
    if required:
        cand = cand[cand['word'].apply(lambda x: all(letter in x for letter in required))]
    for i in range(5):
        if yellows[i] and len(cand) > 0:
            cand.loc[cand['word'].str[i].isin(list(yellows[i])), 'word_score'] *= 0.75
    if guessed:
        cand = cand[~cand['word'].isin(guessed)]
    return cand.sort_values(by='word_score').iat[0, 0] if len(cand) else None


def simulate_game(df: pd.DataFrame, target: str, strategy: str = 'live', trace: bool = False,
                  popularity: bool = True) -> tuple[list[str], float]:
    """
    Play one game, Pick 1 every turn, until the guess is the target.
    Returns (the words played, seconds spent in the solver).
    With trace=True, prints each step so you can verify against the real UI.
    """
    if trace:
        print(f'\n--- Target: {target.upper()} ---')

    history = []
    spent = 0.0

    for attempt in range(1, MAX_GUESSES + 1):
        wordle_data = build_wordle_data(history)

        t0 = time.perf_counter()
        if strategy == 'old':
            guess, options = old_pick(df, wordle_data), ''
        else:
            out = antiwordle_solver_split_revamp(df, wordle_data, popularity=popularity)
            guess, options = extract_word(out[0]), out[5]
        spent += time.perf_counter() - t0

        if guess is None:  # only when the target isn't in the word list
            if trace:
                print(f'  Attempt {attempt}: no playable words left - the target is not in the list')
            break

        colors = color_guess(guess, target)

        if trace:
            played = {g for g, _ in history}
            possible = sum(1 for w in df['word'] if w not in played
                           and all(color_guess(g, w) == c for g, c in history))
            colored = ' '.join(f'{g.upper()}({COLOR_CHAR[c]})' for g, c in zip(guess, colors))
            print(f'  {attempt:2d}. {colored}   {options} | {possible} could be the answer')

        history.append((guess, colors))

        if guess == target:
            if trace:
                print(f'  Hit the answer on guess {attempt}')
            break
    else:
        if trace:
            print(f'  Still going after {MAX_GUESSES} guesses (cap)')

    return [g for g, _ in history], spent


def pick_targets(df: pd.DataFrame, targets: str, common_size: int, sample: int | None,
                 order: str, seed: int) -> list[str]:
    if targets == 'common':
        pool = df.nlargest(common_size, 'popularity_score')['word'].tolist()
    else:
        pool = df['word'].tolist()
    if order == 'random':
        pool = random.Random(seed).sample(pool, len(pool))
    else:
        pool = sorted(pool)
    return pool[:sample] if sample is not None else pool


def run_backtest(word_list: list[str] | None = None, sample: int | None = None,
                 order: str = 'alpha', seed: int = 1, targets: str = 'all',
                 common_size: int = 2300, strategy: str = 'live', popularity: bool = True,
                 verbose: bool = False, trace: bool = False, workers: int = 1):
    df = load_df()
    games = word_list if word_list is not None else \
        pick_targets(df, targets, common_size, sample, order, seed)
    settings = dict(strategy=strategy, popularity=popularity)

    played = {}
    spent = 0.0
    t0 = time.time()

    def record(target, result):
        nonlocal spent
        played[target] = result[0]
        spent += result[1]
        if verbose and not trace:
            print(f'  {target}: {len(result[0])}  {" ".join(result[0])}')

    if trace or workers == 1:
        for i, target in enumerate(games):
            if not trace and not verbose and i % 100 == 0:
                print(f'  {i}/{len(games)}...')
            record(target, simulate_game(df, target, trace=trace, **settings))
    else:
        n = len(games)
        chunk = max(1, n // (workers * 8))
        print(f'  Running {n} games across {workers} workers...')
        with mp.Pool(workers, initializer=_init_worker, initargs=(DATASET_PATH, settings)) as pool:
            for target, result in pool.imap(_simulate_one_worker, games, chunksize=chunk):
                record(target, result)
                done = len(played)
                if done % 250 == 0:
                    elapsed = time.time() - t0
                    rate = done / elapsed
                    print(f'  {done}/{n}  ({rate:.1f} games/s, ~{(n - done) / rate:.0f}s remaining)')

    elapsed = time.time() - t0
    lengths = [len(words) for words in played.values()]
    unfinished = [t for t, words in played.items() if not words or words[-1] != t]
    label = strategy if strategy == 'old' else \
        'live' + ('' if popularity else ', no popularity')
    pool_label = f'{len(games)} words' if word_list is not None else \
        (f'top {common_size} by popularity' if targets == 'common' else 'all words')
    print(f'\n=== Antiwordle Backtest: {label}, {pool_label} ({len(games)} games, {elapsed:.1f}s) ===')
    print(f'Average guesses: {sum(lengths) / len(lengths):.2f}   median {median(lengths):g}   '
          f'min {min(lengths)}   max {max(lengths)}')
    print(f'Solver: {spent / sum(lengths) * 1000:.0f} ms per call')
    print(f'Past the page\'s {PAGE_ROWS} rows: {sum(1 for n in lengths if n > PAGE_ROWS)}')
    if unfinished:
        print(f'Not ended by the answer (cap hit or target not in the list): {unfinished}')
    dist = Counter(lengths)
    print('\nGuess distribution:')
    for k in sorted(dist):
        print(f'  {k:2d}: {dist[k]:4d}')
    print('\nShortest games:')
    for target in sorted(played, key=lambda t: (len(played[t]), t))[:10]:
        print(f'  {target}  {len(played[target]):2d}  {" ".join(played[target])}')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Backtest the Antiwordle solver')
    parser.add_argument('--words', nargs='*', help='Specific target words (default: the --targets pool)')
    parser.add_argument('--targets', choices=['all', 'common'], default='all',
                        help='Target pool: all dataset words (default) or the most popular ones')
    parser.add_argument('--common-size', type=int, default=2300, dest='common_size',
                        help='How many of the most popular words --targets common uses (default: 2300)')
    parser.add_argument('--sample', type=int, default=None, help='Test N targets instead of all')
    parser.add_argument('--order', choices=['alpha', 'random'], default='alpha',
                        help='Target order when sampling: alpha (default) or random')
    parser.add_argument('--seed', type=int, default=1,
                        help='Shuffle seed for --order random, so strategies meet the same targets (default: 1)')
    parser.add_argument('--strategy', choices=['live', 'old'], default='live',
                        help='live = the site\'s solver (default), old = the ranking before the overhaul')
    parser.add_argument('--no-popularity', action='store_false', dest='popularity',
                        help='live only: treat every word as an equally likely answer')
    _default_workers = max(1, mp.cpu_count() // 2)  # physical cores, not hyperthreads
    parser.add_argument('--workers', type=int, default=_default_workers,
                        help=f'Parallel worker processes (default: {_default_workers} = physical core count)')
    parser.add_argument('--verbose', action='store_true', help='Print each game as it finishes')
    parser.add_argument('--trace', action='store_true',
                        help='Print full step-by-step play for every word (best with --words or small --sample)')
    args = parser.parse_args()

    run_backtest(word_list=args.words, sample=args.sample, order=args.order, seed=args.seed,
                 targets=args.targets, common_size=args.common_size, strategy=args.strategy,
                 popularity=args.popularity, verbose=args.verbose, trace=args.trace,
                 workers=args.workers)

# -- Quick reference (run from heroku_apps/) ---------------------------------
# python scripts/backtest_antiwordle.py                                  # full run, all words
# python scripts/backtest_antiwordle.py --targets common                 # the 2300 most popular words
# python scripts/backtest_antiwordle.py --strategy old                   # the ranking before the overhaul
# python scripts/backtest_antiwordle.py --sample 200 --order random      # 200 random targets (seed 1)
# python scripts/backtest_antiwordle.py --words royal zippy              # specific targets
# python scripts/backtest_antiwordle.py --words royal --trace            # step-by-step debug
# python scripts/backtest_antiwordle.py --no-popularity                  # every word equally likely
# python scripts/backtest_antiwordle.py --workers 6                      # override parallel workers
# python scripts/backtest_antiwordle.py --verbose                        # print games as they finish
# ----------------------------------------------------------------------------
