import re
import pandas as pd

# The dataset's letter-by-position columns, first to fifth.
PLACES = ['one', 'two', 'three', 'four', 'five']
ALPHABET = 'abcdefghijklmnopqrstuvwxyz'


def _read_board(wordle_data_dict):
    """What a Wordle or Antiwordle board says, from the boxes the page posts:
    (complete_rows, first_incomplete_row, gray, greens, yellows,
    guessed_word_set). gray is a list of letters, greens and yellows a string
    of letters for each position, one to five."""
    row_letters = {}
    for entry in wordle_data_dict:
        if entry['letter']:  # Check if 'letter' is not empty
            row_letters[entry['row']] = row_letters.get(entry['row'], 0) + 1
    # possibly use to make a visual indicator of rows that are considered to be complete
    complete_rows = [int(row) for row, count in row_letters.items() if count == 5]

    # a marker of where to populate the chosen word
    first_incomplete_row = None
    if complete_rows:
        for i in range(1, max(complete_rows) + 2):  # Check up to one row beyond the last complete row
            if i not in complete_rows:
                first_incomplete_row = i
                break  # Exit the loop once the first incomplete row is found
    else:
        first_incomplete_row = 1
    if first_incomplete_row >= 7:
        first_incomplete_row = None

    gray = set()
    greens = {i: set() for i in range(1, 6)}
    yellows = {i: set() for i in range(1, 6)}
    for entry in wordle_data_dict:
        letter = entry['letter'].lower()  # Ensure consistent casing
        position = int(entry['position'])
        color = entry['color']
        if color == '1' and letter:    # grey: not in the word
            gray.add(letter)
        elif color == '3' and letter:  # green: in this position
            greens[position].add(letter)
        elif color == '2' and letter:  # yellow: in the word, not in this position
            yellows[position].add(letter)

    # Convert the sets back to strings without duplicates
    gray = ''.join(sorted(gray))
    greens = [''.join(sorted(greens[i])) for i in range(1, 6)]
    yellows = [''.join(sorted(yellows[i])) for i in range(1, 6)]
    # account for a letter used twice. one is grey, one is yellow or green
    for letter in ''.join(greens + yellows):
        gray = gray.replace(letter, "")
    gray = list(gray.lower())
    greens = [s.lower() for s in greens]
    yellows = [s.lower() for s in yellows]

    guessed_words = {}
    for entry in wordle_data_dict:
        if entry['letter']:
            row = entry['row']
            pos = int(entry['position'])
            guessed_words.setdefault(row, {})[pos] = entry['letter'].lower()
    guessed_word_set = {
        ''.join(letters[p] for p in sorted(letters))
        for letters in guessed_words.values()
        if len(letters) == 5
    }
    return complete_rows, first_incomplete_row, gray, greens, yellows, guessed_word_set


def _filter_candidates(df, gray, greens, yellows):
    """The words in `df` a board allows, in df's order: every yellow letter
    somewhere, no gray letter anywhere, each green in its position, and no
    yellow letter in the position it was yellow in."""
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


def _picks(df):
    """The top five words, 'Pick 1: word' to 'Pick 5: word'; past the end of
    df '' ('No words found' for the first). Five, in case you get trapped in
    a narrow path of replacing just 1 letter at a time."""
    picks = []
    for k in range(5):
        try:
            picks.append(f'Pick {k + 1}: ' + df.iat[k, 0])
        except Exception:
            picks.append('No words found' if k == 0 else '')
    return picks


def _options_line(remaining, total):
    return f'Options remaining: {remaining}/{total} ({round(remaining/total*100,2)}%)'


def wordle_solver_split_revamp(import_df, wordle_data_dict):
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


def _quordle_solve(import_df, puzzles, guessed_words_per_puzzle):
    """The flat 30-tuple: five picks and an options line for the combined
    view, then for each puzzle. puzzles is (gray, greens, yellows) for
    puzzles 1 to 4."""
    df = import_df.copy()
    total_len = len(df)

    picked = []
    for k, (gray, greens, yellows) in enumerate(puzzles, 1):
        dfk = _filter_candidates(df, gray, greens, yellows)
        # pick the best (aka reasonably good) choice by sorting on the highest 'word_score'
        dfk = dfk.sort_values(by='word_score', ascending=False)
        if guessed_words_per_puzzle.get(k):
            dfk = dfk[~dfk['word'].isin(guessed_words_per_puzzle[k])]
        picked.append(dfk)

    # all puzzle calcs
    # all_word_score
    #     sum: Letter_score1-5 (not counted if dup)
    #         each letter_score: naive letter freq * tried penalty (0.5) * not seen in
    #         puzzle penalty for puzzles 1-4 (0.5) * known position penalty (0.25)
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


def quordle_solver_split_revamp(import_df, quordle_data_dict):
    """
    Quordle solver that follows the wordle revamp pattern
    Takes quordle_data_dict in format:
    [
        {"letter": "A", "position": "1", "color": "1", "row": "1", "puzzle": "1"},
        {"letter": "R", "position": "2", "color": "2", "row": "1", "puzzle": "1"},
        ...
    ]
    """

    # Initialize data structures for each puzzle
    puzzles_data = {
        puzzle: {"must_not_be_present": set(), "present": {i: set() for i in range(1, 6)},
                 "not_present": {i: set() for i in range(1, 6)}}
        for puzzle in range(1, 5)
    }

    # Process the input data
    for entry in quordle_data_dict:
        if entry['letter']:  # Check if 'letter' is not empty
            letter = entry['letter'].lower()
            position = int(entry['position'])
            color = entry['color']
            puzzle = int(entry['puzzle'])

            # Letters that must not be present anywhere (gray)
            if color == '1':
                puzzles_data[puzzle]["must_not_be_present"].add(letter)
            # Letters present in specific positions (green)
            elif color == '3':
                puzzles_data[puzzle]["present"][position].add(letter)
            # Letters that are not present in specific positions but are in the word (yellow)
            elif color == '2':
                puzzles_data[puzzle]["not_present"][position].add(letter)

    # (gray, greens, yellows) for each puzzle, a letter that is green or
    # yellow taken out of the gray ones
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

    # extract already-guessed words per puzzle
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

    return _quordle_solve(import_df, puzzles, guessed_words_per_puzzle)


# last letter solver
def find_word_with_letters(import_df, must_be_present: str):
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


def _pick_word(pick_str):
    if not pick_str:
        return None
    m = re.match(r'^Pick \d+: ([A-Za-z]+)', pick_str)
    return m.group(1).lower() if m else None


def compute_alt_picks(import_df, picks, gray_letters='', guessed_word_set=None, min_match=4, force=False):
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

    raw = find_word_with_letters(search_df, ''.join(sorted(varying_letters)))

    if raw[0]:
        m = re.search(r'\((\d+) match\)', raw[0])
        if not m or int(m.group(1)) < min_match:
            return False, '', '', '', '', ''

    relabeled = [re.sub(r'^Pick \d+:', f'Alt Pick {i}:', r) if r else '' for i, r in enumerate(raw, 1)]
    return True, relabeled[0], relabeled[1], relabeled[2], relabeled[3], relabeled[4]


def antiwordle_solver_split_revamp(import_df, wordle_data_dict):
    complete_rows, first_incomplete_row, gray, greens, yellows, guessed_word_set = \
        _read_board(wordle_data_dict)
    df = _filter_candidates(import_df.copy(), gray, greens, [''] * 5)

    # yellow round 1 - toss any words that do not have the letter. Antiwordle
    # locks it in: it must be played again, and may stay where it was yellow.
    not_present_joined = ''.join(yellows)
    if not_present_joined:  # Only filter if there are letters to check
        df = df[df['word'].apply(lambda x: all(letter in x for letter in not_present_joined))]

    # yellow round 2 - give a bonus if the letter is in the same position
    bonus = 0.75
    for i in range(5):
        if yellows[i] and len(df) > 0:  # Check if df is not empty
            df.loc[df['word'].str[i].isin(list(yellows[i])), 'word_score'] *= bonus

    # exclude already-guessed words from suggestions
    if guessed_word_set:
        df = df[~df['word'].isin(guessed_word_set)]

    if len(df) == 0:
        return ('No words found', '', '', '', '', f'Options remaining: 0/{len(import_df)} (0.0%)',
                first_incomplete_row, complete_rows)
    df = df.sort_values(by = 'word_score', ascending = True)
    return (*_picks(df), _options_line(len(df), len(import_df)), first_incomplete_row, complete_rows)
