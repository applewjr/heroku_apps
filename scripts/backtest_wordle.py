"""
Wordle solver backtester.

Strategy: Alt Pick 1 when the solver shows Alt Picks, otherwise Pick 1, as the
page recommends. --strategy old replays the solver before the entropy
overhaul (a copy is kept below), so the two can be compared on the same
targets. Runs against every word in the dataset, or the most common ones,
and reports the solve rate and guess distribution.

Full runs, solved and average guesses on wins (all 5710 words / the 2300 most
popular):
  old (static word_score, Alt Picks by matched letters)  97.04% 4.230 / 98.39% 4.239
  live (entropy, soft popularity prior)                  99.96% 3.804 / 100.00% 3.658
Tried on full runs and dropped:
  - the old ranking worked out per request instead: letter frequency among
    the words still possible 97.78% 4.18 / 99.00% 4.16, how evenly each letter
    splits them 99.09% 4.14 / 99.70% 4.13
  - every answer equally likely 99.96% 3.794 / 100.00% 3.723, and Antiwordle's
    steeper popularity curve (3.2 / 2.5) 99.95% 3.830 / 100.00% 3.617
  - more words scored exactly (100 words / 200 sampled answers, 300 / 400):
    no more wins, slower
"""

import sys
import re
import time
import multiprocessing as mp
import pandas as pd
from collections import Counter

sys.path.insert(0, 'c:/Users/james/projects/heroku_apps')
from functions.wordle import (wordle_solver_split_revamp, LETTER_VALUE, _word_arrays, _read_board, _picks,
                              _options_line, _pick_word)


DATASET_PATH = 'c:/Users/james/projects/heroku_apps/datasets/wordle_words.csv'
MAX_GUESSES = 6

_worker = {}  # per-process DataFrame and settings, loaded once via pool initializer


def _init_worker(dataset_path, settings):
    _worker['df'] = pd.read_csv(dataset_path)
    _worker['settings'] = settings


def _simulate_one_worker(target):
    return target, simulate_game(_worker['df'], target, **_worker['settings'])


def load_df():
    return pd.read_csv(DATASET_PATH)


# --- the old dataset's columns, for the --strategy old copies ----------------
# The solvers read only the words and their popularity. The copies of older
# strategies in these scripts read columns the old word_data_created.csv had;
# old_columns rebuilds them from the words.

PLACES = ['one', 'two', 'three', 'four', 'five']
_old_frame = None  # (df, df with the old columns)


def old_columns(df):
    """df plus the old dataset's columns, built once per df: one-five (the
    letters), word_score, one_val-five_val (each letter's LETTER_VALUE) and
    two_match-five_match (1 where the letter is its first use in the word)."""
    global _old_frame
    if _old_frame is None or _old_frame[0] is not df:
        old = df.copy()
        for i, place in enumerate(PLACES):
            old[place] = old['word'].str[i]
        old['word_score'] = _word_arrays(df)[3]
        for place in PLACES:
            old[place + '_val'] = LETTER_VALUE[[ord(c) - 97 for c in old[place]]]
        for i, place in enumerate(PLACES[1:], 1):
            old[place + '_match'] = [int(w[i] not in w[:i]) for w in old['word']]
        _old_frame = (df, old)
    return _old_frame[1]


def _filter_candidates(df, gray, greens, yellows):
    """The words in `df` a board allows, in df's order: every yellow letter
    somewhere, no gray letter anywhere, each green in its position, and no
    yellow letter in the position it was yellow in. The solvers' version
    before they moved to arrays; df needs old_columns."""
    for letter in ''.join(yellows):
        df = df[df['word'].str.contains(letter, regex=False)]
    for place in PLACES:
        for letter in gray:
            df = df[df[place] != letter]
    for place, green in zip(PLACES, greens):
        if green != '':
            df = df[df[place] == green]
    for place, letters in zip(PLACES, yellows):
        for letter in letters:
            df = df[df[place] != letter]
    return df


# --- the solver before the overhaul (verbatim, for --strategy old) -----------

def _old_solver(import_df, wordle_data_dict):
    complete_rows, first_incomplete_row, gray, greens, yellows, guessed_word_set = \
        _read_board(wordle_data_dict)
    df = _filter_candidates(import_df, gray, greens, yellows)
    # exclude already-guessed words from suggestions
    if guessed_word_set:
        df = df[~df['word'].isin(guessed_word_set)]

    # on the final 2 guesses sort by popularity; otherwise sort by word_score for information gain
    final_guess = first_incomplete_row == 6
    df = df.sort_values(by='popularity_score' if final_guess else 'word_score', ascending=False)
    rank_label = 'popularity' if final_guess else 'word score'
    return (*_picks(df), f'{_options_line(len(df), len(import_df))} · ranked by {rank_label}',
            first_incomplete_row, complete_rows, ''.join(gray), guessed_word_set)


def _old_find_word_with_letters(import_df, must_be_present: str):
    """The words with the most of these letters (a repeated one counts
    again), best word_score first: 'Pick 1: word (n match)' to 'Pick 5'."""
    must_be_present = must_be_present.lower()

    df = import_df.copy()
    df['char_match_count'] = [sum(1 for j in must_be_present if j in word) for word in df['word']]

    df = df.sort_values(by = ['char_match_count', 'word_score'], ascending =  False)
    df = df[['word', 'char_match_count']]

    picks = []
    for k in range(5):
        try:
            picks.append(f"Pick {k + 1}: {df.iat[k, 0]} ({df['char_match_count'].iloc[k]} match)")
        except Exception:
            picks.append('No words found' if k == 0 else '')
    return tuple(picks)


def _old_compute_alt_picks(import_df, picks, gray_letters='', guessed_word_set=None, min_match=4, force=False):
    """
    Given the top picks, check if Pick 1/2/3 differ by <=2 letter positions from each other.
    If so, collect the unique letters in the varying positions across all top picks and find
    eliminator words that cover as many of those letters as possible.
    Searches the full word list minus gray (eliminated) letters so eliminators aren't
    constrained to the answer candidate pool.

    min_match: minimum number of varying letters the alt pick must cover (default 4).
               Lower this when guesses are running out.
    force: skip the "top 3 within 2 positions" gate. Use when remaining >> guesses_left.

    Returns (show_alt, alt1, alt2, alt3, alt4, alt5).
    """
    words = [_pick_word(p) for p in picks[:3]]
    if len(words) < 3 or any(w is None for w in words):
        return False, '', '', '', '', ''

    def pos_diff(a, b):
        return sum(1 for x, y in zip(a, b) if x != y)

    if not force:
        pairs = [(words[0], words[1]), (words[0], words[2]), (words[1], words[2])]
        if not all(pos_diff(a, b) <= 2 for a, b in pairs):
            return False, '', '', '', '', ''

    all_words = [_pick_word(p) for p in picks if p]
    all_words = [w for w in all_words if w is not None]

    varying_letters = set()
    for pos in range(5):
        letters_at_pos = {w[pos] for w in all_words if len(w) > pos}
        if len(letters_at_pos) > 1:
            varying_letters.update(letters_at_pos)

    if not varying_letters:
        return False, '', '', '', '', ''

    # Search full word list, filtered to only gray (eliminated) letters
    search_df = import_df.copy()
    for letter in gray_letters:
        search_df = search_df[~search_df['word'].str.contains(letter)]

    # Exclude words already shown as regular picks or already guessed on the board
    top_word_set = set(all_words)
    if guessed_word_set:
        top_word_set |= guessed_word_set
    search_df = search_df[~search_df['word'].isin(top_word_set)]

    if len(search_df) == 0:
        return False, '', '', '', '', ''

    raw = _old_find_word_with_letters(search_df, ''.join(sorted(varying_letters)))

    if raw[0]:
        m = re.search(r'\((\d+) match\)', raw[0])
        if not m or int(m.group(1)) < min_match:
            return False, '', '', '', '', ''

    relabeled = [re.sub(r'^Pick \d+:', f'Alt Pick {i}:', r) if r else '' for i, r in enumerate(raw, 1)]
    return True, relabeled[0], relabeled[1], relabeled[2], relabeled[3], relabeled[4]


def old_solve(df, wordle_data):
    """The old solver with the Alt Picks the /wordle route added (fewer
    matched letters and a forced search as guesses run out), as the same
    14-tuple wordle_solver_split_revamp returns."""
    df = old_columns(df)
    out = _old_solver(df, wordle_data)
    if out[6] == 6:
        alt = (False, '', '', '', '', '')
    else:
        m = re.search(r'(\d+)/', out[5])
        remaining = int(m.group(1)) if m else 9999
        left = 6 - (out[6] or 1)
        alt = _old_compute_alt_picks(df, list(out[:5]), out[8], out[9],
                                     min_match=2 if left <= 1 else (3 if left <= 2 else 4),
                                     force=remaining > left * 4 and left <= 3)
    return (*out[:8], *alt)


# --- playing -----------------------------------------------------------------

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


def simulate_game(df: pd.DataFrame, target: str, trace: bool = False,
                  no_alt: bool = False, strategy: str = 'live') -> int | None:
    """
    Simulate one game. Returns number of guesses to solve, or None if failed.
    With trace=True, prints each step so you can verify against the real UI.
    With no_alt=True, always uses Pick 1 and never consults Alt Pick 1.
    """
    if trace:
        print(f'\n--- Target: {target.upper()} ---')

    history = []

    for attempt in range(1, MAX_GUESSES + 1):
        wordle_data = build_wordle_data(history)

        out = (old_solve if strategy == 'old' else wordle_solver_split_revamp)(df, wordle_data)
        pick1, options_remaining, show_alt, alt1 = out[0], out[5], out[8], out[9]

        if show_alt and not no_alt and extract_word(alt1):
            guess = extract_word(alt1)
            source = f'Alt Pick 1  ({alt1})'
        else:
            guess = extract_word(pick1)
            source = f'Pick 1      ({pick1})'

        if guess is None:
            if trace:
                print(f'  Attempt {attempt}: solver has no candidates - FAIL')
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
                 workers: int = 1, no_alt: bool = False, strategy: str = 'live',
                 targets: str = 'all', common_size: int = 2300):
    df = load_df()
    if word_list is not None:
        words = word_list
    else:
        if targets == 'common':
            pool = df.nlargest(common_size, 'popularity_score')['word'].tolist()
        else:
            pool = df['word'].tolist()
        if order == 'random':
            import random
            pool = random.sample(pool, len(pool))
        else:
            pool = sorted(pool)
        words = pool[:sample] if sample is not None else pool
    settings = dict(no_alt=no_alt, strategy=strategy)

    results = []
    fails = []
    t0 = time.time()

    if trace or workers == 1:
        for i, target in enumerate(words):
            if not trace and i % 500 == 0:
                print(f'  {i}/{len(words)}...')
            tries = simulate_game(df, target, trace=trace, **settings)
            if tries is not None:
                results.append(tries)
            else:
                fails.append(target)
                if verbose and not trace:
                    print(f'  FAIL: {target}')
    else:
        n = len(words)
        chunk = max(1, n // (workers * 8))
        print(f'  Running {n} games across {workers} workers...')
        with mp.Pool(workers, initializer=_init_worker, initargs=(DATASET_PATH, settings)) as pool:
            done = 0
            for target, tries in pool.imap(_simulate_one_worker, words, chunksize=chunk):
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
    total = len(words)
    solved = len(results)
    pool_label = f'top {common_size} by popularity' if targets == 'common' and word_list is None else 'all words'
    print(f'\n=== Backtest Results: {strategy}, {pool_label} ({total} words, {elapsed:.1f}s) ===')
    print(f'Solved:  {solved}/{total}  ({solved/total*100:.2f}%)')
    print(f'Failed:  {len(fails)}/{total}  ({len(fails)/total*100:.2f}%)')
    if results:
        print(f'Avg tries (wins only): {sum(results)/len(results):.3f}')
        dist = Counter(results)
        print('\nGuess distribution:')
        for k in sorted(dist):
            print(f'  {k}: {dist[k]:4d}')
    if fails:
        print(f'\nFailed words: {fails}')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Backtest the Wordle solver')
    parser.add_argument('--words', nargs='*', help='Specific words to test (default: the --targets pool)')
    parser.add_argument('--targets', choices=['all', 'common'], default='all',
                        help='all words (default) or the most popular ones')
    parser.add_argument('--common-size', type=int, default=2300, dest='common_size',
                        help='How many words --targets common takes (default 2300)')
    parser.add_argument('--sample', type=int, default=None, help='Test N words instead of all')
    parser.add_argument('--order', choices=['alpha', 'random'], default='alpha',
                        help='Word order when sampling: alpha (default) or random')
    parser.add_argument('--strategy', choices=['live', 'old'], default='live',
                        help='live (default) or the solver before the entropy overhaul')
    _default_workers = max(1, mp.cpu_count() // 2)  # physical cores, not hyperthreads
    parser.add_argument('--workers', type=int, default=_default_workers,
                        help=f'Parallel worker processes (default: {_default_workers} = physical core count)')
    parser.add_argument('--verbose', action='store_true', help='Print each failed word as it happens')
    parser.add_argument('--trace', action='store_true',
                        help='Print full step-by-step play for every word (best with --words or small --sample)')
    parser.add_argument('--no-alt', action='store_true', dest='no_alt',
                        help='Always use Pick 1, never Alt Pick 1')
    args = parser.parse_args()

    run_backtest(word_list=args.words, sample=args.sample, order=args.order,
                 verbose=args.verbose, trace=args.trace, workers=args.workers,
                 no_alt=args.no_alt, strategy=args.strategy, targets=args.targets,
                 common_size=args.common_size)

# -- Quick reference (run from heroku_apps/) ---------------------------------
# python scripts/backtest_wordle.py                              # full run, all words
# python scripts/backtest_wordle.py --targets common             # the 2300 most popular words
# python scripts/backtest_wordle.py --strategy old               # the solver before the overhaul
# python scripts/backtest_wordle.py --sample 100                 # first 100 alphabetically
# python scripts/backtest_wordle.py --sample 100 --order random  # 100 random words
# python scripts/backtest_wordle.py --words crane abbey basis    # specific words
# python scripts/backtest_wordle.py --words aging --trace        # step-by-step debug
# python scripts/backtest_wordle.py --no-alt                     # pick 1 only, no alt picks
# python scripts/backtest_wordle.py --workers 6                  # override parallel workers
# python scripts/backtest_wordle.py --verbose                    # print failures as they happen
# ----------------------------------------------------------------------------
