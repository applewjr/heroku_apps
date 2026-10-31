"""
Quordle / Sequence solver backtester.

Quordle: four hidden words, every guess goes on all four boards, and the game
is won by solving all four within 9 guesses. Sequence: the same four boards,
10 guesses, but only one board is open at a time. The next one opens when the
open one is solved and shows the colors of every guess so far (it opens solved
if one of them was its answer).

A game is 4 distinct targets: the pool shuffled with --seed and cut into fours,
so one pass plays every word once. More games than one pass reshuffle with the
next seed. Each turn the script builds the boxes the page posts (a solved
board gets no more letters; in Sequence a closed board holds letters with no
colors) and plays one word:
  live  combined Pick 1, every turn
  old   the solver before its overhaul (a copy is kept below), played the way
        the page's FAQ suggests: combined Pick 1 until some unsolved board has
        3 or fewer options left (or there are only as many guesses left as
        boards), then that board's Pick 1, fewest options first. In Sequence,
        the open board's Pick 1.

Full runs (--games 5710 / 2300: every word a target 4 times), wins and average
guesses on wins (all 5710 words / the 2300 most popular):
  Quordle   old                       84.26% 7.98 / 84.91% 8.00
            live                      99.60% 7.52 / 100.00% 7.33
            live, --no-popularity     99.63% 7.51 / 99.78% 7.49
  Sequence  old                       81.52% 8.55 / 81.39% 8.57
            live                      99.39% 7.90 / 100.00% 7.51
            live, --no-popularity     99.84% 7.87 / 99.87% 7.82
Solver: ~9 ms per call in Quordle, ~29 in Sequence, ~100 at the heaviest.
Tried on 1000-game prototype runs and dropped:
  - in Quordle, a bonus for a guess's odds of being an answer: fewer wins
    (99.8% with none, 98.5% at 4 bits, 96% at 6); Sequence keeps 1 bit
  - an exact endgame search once 20 or fewer answers are left (0.01 to 0.03
    guesses), and an expected-guesses model in place of entropy (no gain)
  - a fixed list of the 500 to 1500 best openers as the only guesses (93 to
    97% wins): the right splitter late in a game depends on the board
  - Quordle's logic in Sequence, counting closed boards as open: 35.9% wins
  - Antiwordle's steeper popularity curve (3.2 / 2.5): Sequence 98.5% on all
    words
"""

import sys
import re
import time
import random
import multiprocessing as mp
import pandas as pd
from collections import Counter

sys.path.insert(0, 'c:/Users/james/projects/heroku_apps')
sys.path.insert(0, 'c:/Users/james/projects/heroku_apps/scripts')
from functions.wordle import quordle_solver_split_revamp, _picks, _options_line, ALPHABET
from backtest_wordle import color_guess, extract_word, COLOR_CHAR, old_columns, _filter_candidates


DATASET_PATH = 'c:/Users/james/projects/heroku_apps/datasets/wordle_words.csv'
PAGE_ROWS = 10  # rows per board on the /quordle page
GUESS_LIMITS = {'quordle': 9, 'sequence': 10}
OLD_LEAD = 3    # old policy: a board with this many options or fewer is a lead

_worker = {}  # per-process DataFrame and settings, loaded once via pool initializer


def _init_worker(dataset_path, settings):
    _worker['df'] = pd.read_csv(dataset_path)
    _worker['settings'] = settings


def _simulate_one_worker(targets):
    return simulate_game(_worker['df'], targets, **_worker['settings'])


def load_df():
    return pd.read_csv(DATASET_PATH)


# --- the solver before the overhaul (verbatim, for --strategy old) -----------

def _old_quordle_solve(import_df, puzzles, guessed_words_per_puzzle):
    df = import_df.copy()
    total_len = len(df)

    picked = []
    for k, (gray, greens, yellows) in enumerate(puzzles, 1):
        dfk = _filter_candidates(df, gray, greens, yellows)
        dfk = dfk.sort_values(by='word_score', ascending=False)
        if guessed_words_per_puzzle.get(k):
            dfk = dfk[~dfk['word'].isin(guessed_words_per_puzzle[k])]
        picked.append(dfk)

    penalty = 0.5
    known_penalty = 0.25
    gray1, greens1, yellows1 = puzzles[0]
    tried = ''.join(gray1) + ''.join(greens1) + ''.join(yellows1)

    df_all_puzzle = pd.DataFrame()
    df_all_puzzle['letter'] = df['word'].str[:1]
    df_all_puzzle['freq'] = df['one_val']
    df_all_puzzle['freq'] = (df_all_puzzle['freq'] - min(df_all_puzzle['freq']))/(max(df_all_puzzle['freq']) - min(df_all_puzzle['freq'])) + 9
    df_all_puzzle = df_all_puzzle.drop_duplicates()
    df_all_puzzle = df_all_puzzle.reset_index(drop = True)
    df_all_puzzle['tried'] = [penalty if i in tried else 1 for i in ALPHABET]
    for k, (gray, greens, _yellows) in enumerate(puzzles, 1):
        not_seen, known_pos = ''.join(gray), ''.join(greens)
        df_all_puzzle[f'not_seen{k}'] = [penalty if i in not_seen else 1 for i in ALPHABET]
        df_all_puzzle[f'known_pos{k}'] = [known_penalty if i in known_pos else 1 for i in ALPHABET]
    letter_score = df_all_puzzle['freq'] * df_all_puzzle['tried']
    for column in ['not_seen1', 'not_seen2', 'not_seen3', 'not_seen4',
                   'known_pos1', 'known_pos2', 'known_pos3', 'known_pos4']:
        letter_score = letter_score * df_all_puzzle[column]
    df_all_puzzle = pd.DataFrame({'letter': df_all_puzzle['letter'], 'letter_score': letter_score})

    df_all_word_rank = df[['word', 'two_match', 'three_match', 'four_match', 'five_match']].copy()
    for k in range(1, 6):
        df_all_word_rank[f'letter{k}'] = df_all_word_rank['word'].str[k - 1:k]
    for k in range(1, 6):
        df_all_word_rank = pd.merge(how = 'left', left = df_all_word_rank, right = df_all_puzzle, left_on = f'letter{k}', right_on = 'letter')
        df_all_word_rank = df_all_word_rank.drop(columns = ['letter'])
        df_all_word_rank = df_all_word_rank.rename(columns = {'letter_score': f'letter_score{k}'})

    df_all_word_rank['all_word_score'] = round(df_all_word_rank['letter_score1'] + (df_all_word_rank['letter_score2'] * df_all_word_rank['two_match']) + (df_all_word_rank['letter_score3'] * df_all_word_rank['three_match']) + (df_all_word_rank['letter_score4'] * df_all_word_rank['four_match']) + (df_all_word_rank['letter_score5'] * df_all_word_rank['five_match']), 4)
    df_all_word_rank = df_all_word_rank[['word', 'all_word_score']]
    df_all_word_rank = df_all_word_rank.sort_values(by = ['all_word_score'], ascending = False)

    results = _picks(df_all_word_rank) + [_options_line(len(df_all_word_rank), total_len)]
    for dfk in picked:
        results += _picks(dfk) + [_options_line(len(dfk), total_len)]
    return tuple(results)


def old_solve(import_df, quordle_data_dict):
    """The pre-overhaul quordle_solver_split_revamp: the same 30-tuple."""
    puzzles_data = {
        puzzle: {"must_not_be_present": set(), "present": {i: set() for i in range(1, 6)},
                 "not_present": {i: set() for i in range(1, 6)}}
        for puzzle in range(1, 5)
    }
    for entry in quordle_data_dict:
        if entry['letter']:
            letter = entry['letter'].lower()
            position = int(entry['position'])
            color = entry['color']
            puzzle = int(entry['puzzle'])
            if color == '1':
                puzzles_data[puzzle]["must_not_be_present"].add(letter)
            elif color == '3':
                puzzles_data[puzzle]["present"][position].add(letter)
            elif color == '2':
                puzzles_data[puzzle]["not_present"][position].add(letter)

    puzzles = []
    for puzzle_num in range(1, 5):
        data = puzzles_data[puzzle_num]
        present_strings = [''.join(sorted(data["present"][i])) for i in range(1, 6)]
        not_present_strings = [''.join(sorted(data["not_present"][i])) for i in range(1, 6)]
        all_present = ''.join(present_strings + not_present_strings)
        must_not_be_present = ''.join(sorted(data["must_not_be_present"] - set(all_present)))
        puzzles.append((list(must_not_be_present.lower()),
                        [s.lower() for s in present_strings],
                        [s.lower() for s in not_present_strings]))

    guessed_words_by_puzzle_row = {}
    for entry in quordle_data_dict:
        if entry['letter']:
            puzzle = int(entry['puzzle'])
            row = entry['row']
            pos = int(entry['position'])
            guessed_words_by_puzzle_row.setdefault(puzzle, {}).setdefault(row, {})[pos] = entry['letter'].lower()
    guessed_words_per_puzzle = {}
    for puzzle, rows in guessed_words_by_puzzle_row.items():
        guessed_words_per_puzzle[puzzle] = {
            ''.join(letters[p] for p in sorted(letters))
            for letters in rows.values()
            if len(letters) == 5
        }

    return _old_quordle_solve(import_df, puzzles, guessed_words_per_puzzle)


# --- the game ----------------------------------------------------------------

def _options(line: str) -> int:
    m = re.search(r'(\d+)/', line or '')
    return int(m.group(1)) if m else 0


def board_view(out: tuple, k: int) -> tuple[list[str], int]:
    """Board k's (0 to 3) picks and options count from the solver's 30-tuple."""
    base = 6 + 6 * k
    picks = [w for w in (extract_word(p) for p in out[base:base + 5]) if w]
    return picks, _options(out[base + 5])


def old_policy(df, boxes, view):
    out = old_solve(old_columns(df), boxes)
    played = set(view['history'])
    combined = [w for w in (extract_word(p) for p in out[:5]) if w and w not in played]
    open_boards = [k for k in range(4) if view['open'][k] and not view['solved'][k]]
    boards = {k: board_view(out, k) for k in open_boards}
    boards = {k: (picks, n) for k, (picks, n) in boards.items() if picks}
    if view['mode'] == 'sequence':
        if boards:
            k = min(boards)
            return boards[k][0][0], f'board {k + 1} Pick 1'
    else:
        lead = [k for k, (_, n) in boards.items() if n <= OLD_LEAD]
        if boards and (lead or view['guesses_left'] <= len(open_boards)):
            k = min(boards, key=lambda b: (boards[b][1], b))
            return boards[k][0][0], f'board {k + 1} Pick 1 ({boards[k][1]} options)'
    if combined:
        return combined[0], 'combined Pick 1'
    return None, 'no words'


def live_policy(df, boxes, view, popularity=True):
    out = quordle_solver_split_revamp(df, boxes, mode=view['mode'], popularity=popularity)
    return extract_word(out[0]), 'combined Pick 1'


def build_boxes(targets, history, solved_at, open_at):
    """The boxes the page posts, in its order (puzzle 1's rows first). A
    board gets letters until the page sees it solved, which is once it is
    open and solved; it shows colors only while open, up to its solving
    row."""
    boxes = []
    rows = max(PAGE_ROWS, len(history))
    for k in range(4):
        opened = open_at[k] is not None
        if opened and solved_at[k] is not None:
            written = max(solved_at[k], open_at[k])
        else:
            written = len(history)
        colored = min(written, solved_at[k] or written) if opened else 0
        for r in range(1, rows + 1):
            word = history[r - 1] if r <= written else ''
            colors = color_guess(word, targets[k]) if r <= colored else ['0'] * 5
            for pos in range(5):
                boxes.append({'letter': word[pos].upper() if word else '',
                              'position': str(pos + 1),
                              'color': colors[pos] if word else '0',
                              'row': str(r),
                              'puzzle': str(k + 1)})
    return boxes


def _consistent(df_words, rows):
    return sum(1 for w in df_words if all(color_guess(g, w) == c for g, c in rows))


def play_game(df, targets, pick, mode='quordle', limit=None, trace=False):
    """
    Play one game with pick(df, boxes, view) -> (word, where it came from).
    view is what the player sees: the words played, which boards are open
    and solved, the colors on each open board, guesses left and the mode.
    Returns a dict: targets, words played, the guess each board was solved
    on (None if not), seconds in the solver and the slowest call.
    """
    limit = limit or GUESS_LIMITS[mode]
    history, solved_at = [], [None] * 4
    open_at = [0] * 4 if mode == 'quordle' else [0, None, None, None]
    spent, slowest = 0.0, 0.0
    words = df['word'].tolist() if trace else None
    if trace:
        print(f'\n--- Targets: {" ".join(t.upper() for t in targets)} ({mode}, {limit} guesses) ---')

    while len(history) < limit and None in solved_at:
        boxes = build_boxes(targets, history, solved_at, open_at)
        opened = [o is not None for o in open_at]
        page_solved = [opened[k] and solved_at[k] is not None for k in range(4)]
        view = dict(history=list(history), mode=mode, guesses_left=limit - len(history),
                    open=opened, solved=page_solved,
                    shown={k: [(g, color_guess(g, targets[k])) for g in history[:solved_at[k] or len(history)]]
                           for k in range(4) if opened[k]})
        t0 = time.perf_counter()
        guess, source = pick(df, boxes, view)
        took = time.perf_counter() - t0
        spent += took
        slowest = max(slowest, took)
        if guess is None:
            if trace:
                print(f'  {len(history) + 1:2d}. no word to play')
            break

        history.append(guess)
        n = len(history)
        for k in range(4):
            if solved_at[k] is None and guess == targets[k]:
                solved_at[k] = n
        if mode == 'sequence':
            for k in range(1, 4):
                if open_at[k] is None and solved_at[k - 1] is not None and open_at[k - 1] is not None:
                    open_at[k] = n

        if trace:
            cells = []
            for k in range(4):
                if not opened[k]:
                    cells.append(' closed')
                elif page_solved[k]:
                    cells.append(' solved')
                else:
                    colors = color_guess(guess, targets[k])
                    left = _consistent(words, view['shown'][k] + [(guess, colors)])
                    cells.append(f'{"".join(COLOR_CHAR[c] for c in colors)} {left:4d}')
            print(f'  {n:2d}. {guess.upper()}  ' + '  '.join(cells) + f'   <- {source}')

    if trace:
        result = 'won' if None not in solved_at else f'lost ({4 - solved_at.count(None)} of 4 solved)'
        print(f'  {result} in {len(history)} guesses, solved on {solved_at}')
    return dict(targets=list(targets), words=history, solved_at=solved_at,
                spent=spent, calls=len(history), slowest=slowest, limit=limit)


def simulate_game(df, targets, strategy='live', mode='quordle', limit=None,
                  popularity=True, trace=False):
    if strategy == 'old':
        pick = old_policy
    else:
        def pick(df, boxes, view):
            return live_policy(df, boxes, view, popularity=popularity)
    return play_game(df, targets, pick, mode=mode, limit=limit, trace=trace)


# --- runs --------------------------------------------------------------------

def target_pool(df: pd.DataFrame, targets: str = 'all', common_size: int = 2300) -> list[str]:
    if targets == 'common':
        return df.nlargest(common_size, 'popularity_score')['word'].tolist()
    return df['word'].tolist()


def make_games(pool: list[str], n_games: int | None = None, seed: int = 1) -> list[list[str]]:
    """The pool shuffled and cut into fours; past one pass, reshuffled with
    the next seed. Default: one pass."""
    n_games = n_games or len(pool) // 4
    games = []
    while len(games) < n_games:
        order = random.Random(seed).sample(pool, len(pool))
        games += [order[i:i + 4] for i in range(0, len(order) - 3, 4)]
        seed += 1
    return games[:n_games]


def summarize(results: list[dict], label: str, elapsed: float = 0.0, worst: int = 10):
    wins = [r for r in results if None not in r['solved_at']]
    losses = [r for r in results if None in r['solved_at']]
    calls = sum(r['calls'] for r in results)
    print(f'\n=== Quordle Backtest: {label} ({len(results)} games, {elapsed:.1f}s) ===')
    print(f'Won: {len(wins)}/{len(results)}  ({len(wins) / len(results) * 100:.2f}%)')
    if wins:
        print(f'Average guesses on wins: {sum(len(r["words"]) for r in wins) / len(wins):.3f}   '
              f'average sum of solve rows: {sum(sum(r["solved_at"]) for r in wins) / len(wins):.2f}')
    print(f'Solver: {sum(r["spent"] for r in results) / max(1, calls) * 1000:.0f} ms per call, '
          f'slowest {max(r["slowest"] for r in results) * 1000:.0f} ms')
    repeats = sum(len(r['words']) - len(set(r['words'])) for r in results)
    if repeats:
        print(f'Words played twice: {repeats}')
    dist = Counter(len(r['words']) for r in wins)
    print('\nGuesses used (wins):')
    for k in sorted(dist):
        print(f'  {k:2d}: {dist[k]:5d}')
    print(f'  lost: {len(losses)}')
    if losses:
        solved = Counter(4 - r['solved_at'].count(None) for r in losses)
        print('Boards solved on losses: ' + '  '.join(f'{k}: {solved[k]}' for k in range(3, -1, -1)))
    print('\nWorst games:')
    ranked = sorted(results, key=lambda r: (None not in r['solved_at'],
                                            -r['solved_at'].count(None), -len(r['words'])))
    for r in ranked[:worst]:
        rows = ' '.join(str(s) if s else 'x' for s in r['solved_at'])
        print(f'  {" ".join(r["targets"])}  [{rows}]  {" ".join(r["words"])}')


def run_backtest(word_list: list[str] | None = None, n_games: int | None = None, seed: int = 1,
                 targets: str = 'all', common_size: int = 2300, strategy: str = 'live',
                 mode: str = 'quordle', limit: int | None = None, popularity: bool = True,
                 verbose: bool = False, trace: bool = False, workers: int = 1):
    df = load_df()
    if word_list:
        if len(word_list) % 4:
            raise SystemExit('--words takes four words per game')
        games = [word_list[i:i + 4] for i in range(0, len(word_list), 4)]
    else:
        games = make_games(target_pool(df, targets, common_size), n_games, seed)
    settings = dict(strategy=strategy, mode=mode, limit=limit, popularity=popularity)

    results = []
    t0 = time.time()

    def record(result):
        results.append(result)
        if verbose and not trace:
            rows = ' '.join(str(s) if s else 'x' for s in result['solved_at'])
            print(f'  {" ".join(result["targets"])}  [{rows}]  {" ".join(result["words"])}')

    if trace or workers == 1:
        for i, game in enumerate(games):
            if not trace and not verbose and i % 100 == 0:
                print(f'  {i}/{len(games)}...')
            record(simulate_game(df, game, trace=trace, **settings))
    else:
        n = len(games)
        chunk = max(1, n // (workers * 8))
        print(f'  Running {n} games across {workers} workers...')
        with mp.Pool(workers, initializer=_init_worker, initargs=(DATASET_PATH, settings)) as pool:
            for result in pool.imap(_simulate_one_worker, games, chunksize=chunk):
                record(result)
                done = len(results)
                if done % 250 == 0:
                    elapsed = time.time() - t0
                    rate = done / elapsed
                    print(f'  {done}/{n}  ({rate:.1f} games/s, ~{(n - done) / rate:.0f}s remaining)')

    limit = limit or GUESS_LIMITS[mode]
    label = strategy if strategy == 'old' else 'live' + ('' if popularity else ', no popularity')
    pool_label = 'given words' if word_list else \
        (f'top {common_size} by popularity' if targets == 'common' else 'all words')
    summarize(results, f'{label}, {mode} ({limit} guesses), {pool_label}', time.time() - t0)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Backtest the Quordle / Sequence solver')
    parser.add_argument('--words', nargs='*', help='Specific targets, four per game (default: games from the --targets pool)')
    parser.add_argument('--targets', choices=['all', 'common'], default='all',
                        help='Target pool: all dataset words (default) or the most popular ones')
    parser.add_argument('--common-size', type=int, default=2300, dest='common_size',
                        help='How many of the most popular words --targets common uses (default: 2300)')
    parser.add_argument('--games', '--sample', type=int, default=None, dest='games',
                        help='How many games (default: one pass, every pool word a target once)')
    parser.add_argument('--seed', type=int, default=1,
                        help='Shuffle seed for the games, so strategies meet the same targets (default: 1)')
    parser.add_argument('--mode', choices=['quordle', 'sequence'], default='quordle',
                        help='quordle = all four boards at once (default), sequence = one board at a time')
    parser.add_argument('--guesses', type=int, default=None,
                        help='Guess limit (default: 9 for quordle, 10 for sequence)')
    parser.add_argument('--strategy', choices=['live', 'old'], default='live',
                        help='live = the site\'s solver (default), old = the solver before the overhaul')
    parser.add_argument('--no-popularity', action='store_false', dest='popularity',
                        help='live only: treat every word as an equally likely answer')
    _default_workers = max(1, mp.cpu_count() // 2)  # physical cores, not hyperthreads
    parser.add_argument('--workers', type=int, default=_default_workers,
                        help=f'Parallel worker processes (default: {_default_workers} = physical core count)')
    parser.add_argument('--verbose', action='store_true', help='Print each game as it finishes')
    parser.add_argument('--trace', action='store_true',
                        help='Print full step-by-step play for every game (best with --words or small --games)')
    args = parser.parse_args()

    run_backtest(word_list=args.words, n_games=args.games, seed=args.seed, targets=args.targets,
                 common_size=args.common_size, strategy=args.strategy, mode=args.mode,
                 limit=args.guesses, popularity=args.popularity, verbose=args.verbose,
                 trace=args.trace, workers=args.workers)

# -- Quick reference (run from heroku_apps/) ---------------------------------
# python scripts/backtest_quordle.py                                  # one pass, all words (1427 games)
# python scripts/backtest_quordle.py --targets common                 # the 2300 most popular words (575 games)
# python scripts/backtest_quordle.py --games 5710                     # 4 passes: every word a target 4 times
# python scripts/backtest_quordle.py --mode sequence                  # Sequence, 10 guesses
# python scripts/backtest_quordle.py --strategy old                   # the solver before the overhaul
# python scripts/backtest_quordle.py --games 200 --seed 7             # 200 games, another shuffle
# python scripts/backtest_quordle.py --words crane abbey basis tiger  # one game, specific targets
# python scripts/backtest_quordle.py --words crane abbey basis tiger --trace  # step-by-step debug
# python scripts/backtest_quordle.py --guesses 8                      # Extreme's limit (12 for Chill)
# python scripts/backtest_quordle.py --no-popularity                  # every word equally likely
# python scripts/backtest_quordle.py --workers 6                      # override parallel workers
# ----------------------------------------------------------------------------
