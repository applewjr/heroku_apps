import re
import numpy as np

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


# --- antiwordle ---------------------------------------------------------------
# Antiwordle is won by lasting: every guess must keep the grays out, the greens
# in place and the yellows somewhere, until only the answer is left to play.
# The solver picks the guess whose likely colors leave the most words playable,
# scored on log(words left) so one big all-gray outcome can't hide a likely
# green, and a guess that could be the answer loses its odds of ending the game.
# scripts/backtest_antiwordle.py measures it.

# Answer odds by word: a logistic curve on popularity_score (a Zipf value), so
# common words count as likely answers and rare ones as nearly safe.
ANTI_POP_MID = 3.2
ANTI_POP_SLOPE = 2.5
ANTI_PRERANK = 300  # possible answers the cheap pre-rank uses, evenly spaced
ANTI_SAMPLE = 1000  # and the ones each of the top guesses is scored against
                    # (backtest: as long games as scoring against all of them)
ANTI_TOP_K = 30     # guesses scored on words kept, after the pre-rank
_HIT = 242          # all green: the guess was the answer
_POW3 = 3 ** np.arange(5)

_anti_data = None  # (df, letter codes, letter presence, answer odds, openers)


def _anti_arrays(df):
    """Letter codes (n, 5), letter presence (n, 26) and answer odds for the
    words in df, plus a dict for the empty-board picks, built once per df."""
    global _anti_data
    data = _anti_data
    if data is None or data[0] is not df:
        codes = np.array([[ord(c) - 97 for c in w] for w in df['word']], dtype=np.int8)
        has = np.zeros((len(codes), 26), bool)
        for i in range(5):
            has[np.arange(len(codes)), codes[:, i]] = True
        pop = df['popularity_score'].to_numpy()
        odds = 1 / (1 + np.exp(-ANTI_POP_SLOPE * (pop - ANTI_POP_MID)))
        data = _anti_data = (df, codes, has, odds, {})
    return data


def _patterns(guesses, targets):
    """The colors each guess gets against each target (both rows of letter
    codes), as (len(guesses), len(targets)) codes 0 to 242: position k adds
    3**k for yellow and 2 * 3**k for green. Greens first, then yellows left
    to right while the target has that letter to spare, as the game does."""
    out = np.empty((len(guesses), len(targets)), np.int16)
    t = targets[None, :, :]
    step = max(1, 200_000 // max(1, len(targets)))  # keeps each block a few MB
    for start in range(0, len(guesses), step):
        g = guesses[start:start + step, None, :]
        green = g == t
        yellow = np.zeros_like(green)
        code = np.zeros(green.shape[:2], np.int16)
        for i in range(5):
            letter = g[:, :, i:i + 1]
            spare = ((t == letter) & ~green).sum(-1)
            used = ((g[:, :, :i] == letter) & yellow[:, :, :i]).sum(-1)
            yellow[:, :, i] = ~green[:, :, i] & (spare > used)
            code += green[:, :, i] * (2 * 3 ** i) + yellow[:, :, i] * 3 ** i
        out[start:start + step] = code
    return out


def _kept(codes, has, guess, pattern, known):
    """Which of these words could still be played after `guess` gets
    `pattern`: its greens in place, its yellows somewhere, its grays gone
    (unless the letter shows yellow or green in the row or is `known`)."""
    digits = pattern // _POW3 % 3
    shown = {guess[i] for i in range(5) if digits[i]}
    keep = np.ones(len(codes), bool)
    for i in range(5):
        if digits[i] == 2:
            keep &= codes[:, i] == guess[i]
        elif digits[i] == 1:
            keep &= has[:, guess[i]]
        elif guess[i] not in shown and guess[i] not in known:
            keep &= ~has[:, guess[i]]
    return keep


def _played_rows(wordle_data_dict):
    """Each full row on the board as (letter codes (1, 5), {position: color})
    for the boxes that have one, 0 gray, 1 yellow, 2 green."""
    rows = {}
    for entry in wordle_data_dict:
        if entry['letter']:
            rows.setdefault(entry['row'], {})[int(entry['position'])] = \
                (entry['letter'].lower(), entry['color'])
    played = []
    for boxes in rows.values():
        if len(boxes) == 5:
            word = np.array([[ord(boxes[p][0]) - 97 for p in range(1, 6)]], dtype=np.int8)
            shown = {p - 1: int(color) - 1 for p, (_, color) in boxes.items() if color in ('1', '2', '3')}
            played.append((word, shown))
    return played


def _anti_rank(codes, has, weights, playable, possible, known):
    """The playable word indexes, best guess first (just the top
    ANTI_TOP_K)."""
    idx = np.flatnonzero(playable)
    cand = np.flatnonzero(possible)

    def spread(n):
        return cand[np.unique(np.linspace(0, len(cand) - 1, min(len(cand), n)).astype(int))]

    # pre-rank: guesses whose colors bunch the answers together reveal least
    sample = spread(ANTI_PRERANK)
    weight = weights[sample]
    pats = _patterns(codes[idx], codes[sample])
    bunched = np.empty(len(idx))
    for start in range(0, len(idx), 1000):
        block = pats[start:start + 1000].astype(np.int64) + 243 * np.arange(min(1000, len(idx) - start))[:, None]
        buckets = np.bincount(block.ravel(), np.tile(weight, len(block)), 243 * len(block)).reshape(-1, 243)
        buckets[:, _HIT] = 0
        bunched[start:start + 1000] = (buckets ** 2).sum(1)
    top = np.argsort(-bunched, kind='stable')[:ANTI_TOP_K]

    # score: expected log(words still playable), times the odds the guess isn't the answer
    sample = spread(ANTI_SAMPLE)
    weight = weights[sample]
    pats = _patterns(codes[idx[top]], codes[sample])
    codes_a, has_a = codes[idx], has[idx]
    total = weights[cand].sum()
    scores = []
    for pos, row in zip(top, pats):
        guess = codes[idx[pos]]
        value = mass = 0.0
        for pattern in np.unique(row):
            if pattern == _HIT:
                continue
            w = weight[row == pattern].sum()
            keep = _kept(codes_a, has_a, guess, pattern, known)
            keep[pos] = False
            value += w * np.log1p(keep.sum())
            mass += w
        risk = weights[idx[pos]] / total if possible[idx[pos]] else 0
        scores.append((1 - risk) * value / mass if mass else 0)
    return idx[top[np.argsort(-np.array(scores), kind='stable')]]


def antiwordle_solver_split_revamp(import_df, wordle_data_dict, popularity=True):
    """Antiwordle picks for a board. popularity=False treats every word as an
    equally likely answer, for scripts/backtest_antiwordle.py."""
    complete_rows, first_incomplete_row, gray, greens, yellows, guessed_word_set = \
        _read_board(wordle_data_dict)
    df, codes, has, odds, openers = _anti_arrays(import_df)
    empty = not any(entry['letter'] for entry in wordle_data_dict)
    if empty and popularity in openers:
        return (*openers[popularity], first_incomplete_row, complete_rows)

    def code(letter):
        return ord(letter) - 97

    playable = ~np.isin(df['word'].to_numpy(), list(guessed_word_set))
    for letter in gray:
        playable &= ~has[:, code(letter)]
    for letter in set(''.join(yellows)):
        playable &= has[:, code(letter)]
    for i, letters in enumerate(greens):
        for letter in letters:
            playable &= codes[:, i] == code(letter)
    if not playable.any():
        return ('No words found', '', '', '', '', f'Options remaining: 0/{len(df)} (0.0%)',
                first_incomplete_row, complete_rows)

    # the words that could still be the answer: their colors against every
    # full row match the board. Many playable words can't be: Antiwordle lets
    # a yellow stay where it was, and a word can repeat a letter the board
    # shows there is only one of. A board that rules out every word was
    # mis-tapped, so fall back to the playable ones.
    possible = playable.copy()
    for word, shown in _played_rows(wordle_data_dict):
        idx = np.flatnonzero(possible)
        pats = _patterns(word, codes[idx])[0]
        for i, digit in shown.items():
            possible[idx[pats // 3 ** i % 3 != digit]] = False
    if not possible.any():
        possible = playable

    known = {code(letter) for letter in ''.join(greens + yellows)}
    weights = odds if popularity else np.ones(len(df))
    order = _anti_rank(codes, has, weights, playable, possible, known)
    result = (*_picks(df.iloc[order[:5]]), _options_line(int(playable.sum()), len(df)))
    if empty:
        openers[popularity] = result
    return (*result, first_incomplete_row, complete_rows)


# --- quordle -----------------------------------------------------------------
# Quordle: four boards, every guess goes on all of them, and 9 guesses to
# solve all four. Sequence: the same boards one at a time and 10 guesses; the
# next board opens when the open one is solved, showing every guess so far.
# The combined Pick 1 is the word whose colors tell the most (entropy, summed)
# about the open boards' answers, from every word in the list. A board with one
# word left is played first, and once there are only as many guesses left as
# boards, only words that can still be an answer. In Sequence an answer's odds
# of being the open board's count too, and so does half of what a guess will
# show on the boards still closed. scripts/backtest_quordle.py measures it.

QUORDLE_LIMITS = {'quordle': 9, 'sequence': 10}
# Answer odds by word, a softer curve than Antiwordle's: daily answers are
# common words, but a rare one still has to be found in time.
QUORDLE_POP_MID = 2.5
QUORDLE_POP_SLOPE = 1.5
QUORDLE_SHORTLIST = 300      # words scored exactly each tap, by a cheap letter count first
QUORDLE_SAMPLE = 400         # possible answers per board each word is scored against
QUORDLE_CANDIDATES = 300     # the open boards' answers join the scored words up to this many
QUORDLE_CLOSED_SAMPLE = 600  # words a closed board's information is measured over
SEQUENCE_CASH_IN = 1.0       # bits a word's odds of being the open board's answer are worth
SEQUENCE_CLOSED_WEIGHT = 0.5  # weight of the bits a word will show on each closed board

_quordle_data = None  # (df, answer odds, empty-board results)


def _quordle_arrays(df):
    """Answer odds for the words in df, and a dict for the empty-board
    results, built once per df."""
    global _quordle_data
    data = _quordle_data
    if data is None or data[0] is not df:
        pop = df['popularity_score'].to_numpy()
        odds = 1 / (1 + np.exp(-QUORDLE_POP_SLOPE * (pop - QUORDLE_POP_MID)))
        data = _quordle_data = (df, odds, {})
    return data


def _spread(idx, n):
    """Up to n of these word indexes, evenly spaced."""
    if len(idx) <= n:
        return idx
    return idx[np.unique(np.linspace(0, len(idx) - 1, n).astype(int))]


def _entropy(pats, weight):
    """The entropy in bits of each row of colors (codes 0 to 242), the
    targets weighted by `weight`, which sums to 1."""
    rows = len(pats)
    flat = (pats.astype(np.int64) + 243 * np.arange(rows)[:, None]).ravel()
    mass = np.bincount(flat, np.tile(weight, rows), 243 * rows).reshape(rows, 243)
    return -(mass * np.log2(np.where(mass > 0, mass, 1))).sum(1)


def _letter_score(codes, has, boards, weights):
    """A cheap stand-in for information, for every word: how evenly each of
    its letters splits each board's possible answers, green in its place
    and in the word at all (binary entropy), summed over the boards."""
    def split(p):
        p = np.clip(p, 1e-12, 1 - 1e-12)
        return -(p * np.log2(p) + (1 - p) * np.log2(1 - p))
    score = np.zeros(len(codes))
    for cand in boards:
        w = weights[cand] / weights[cand].sum()
        green = np.stack([np.bincount(codes[cand, i].astype(np.intp), w, 26) for i in range(5)])
        score += split(green[np.arange(5), codes]).sum(1) + has @ split(w @ has[cand])
    return score


def _closed_info(codes, pool, played, weights):
    """The bits each word in pool will show on a board that hasn't opened:
    how far it shrinks the expected log2(words left) once the board shows
    every guess so far, over an even spread of the dictionary."""
    sample = _spread(np.arange(len(codes)), QUORDLE_CLOSED_SAMPLE)
    w = weights[sample] / weights[sample].sum()
    group = np.zeros(len(sample), np.int64)
    for word in played:
        group = np.unique(group * 243 + _patterns(word, codes[sample])[0], return_inverse=True)[1]
    before = (w * np.log2(np.bincount(group)[group])).sum()
    pats = _patterns(codes[pool], codes[sample]).astype(np.int64)
    key = (np.arange(len(pool))[:, None] * (243 * len(sample)) + group * 243 + pats).ravel()
    _, inv, counts = np.unique(key, return_inverse=True, return_counts=True)
    after = (np.log2(counts[inv]).reshape(len(pool), -1) * w).sum(1)
    return before - after


def _board_answers(df, codes, boxes, rows, unplayed):
    """The words a board's colors allow: each full row's colors against
    the word match what the board shows. A board that rules out every word
    was mis-tapped, so fall back to the gray / green / yellow rules."""
    possible = unplayed.copy()
    for word, shown in rows:
        idx = np.flatnonzero(possible)
        pats = _patterns(word, codes[idx])[0]
        for i, digit in shown.items():
            possible[idx[pats // 3 ** i % 3 != digit]] = False
    if not possible.any():
        _, _, gray, greens, yellows, _ = _read_board(boxes)
        allowed = _filter_candidates(df, gray, greens, yellows)['word']
        possible = df['word'].isin(allowed).to_numpy() & unplayed
    return np.flatnonzero(possible)


def quordle_solver_split_revamp(import_df, quordle_data_dict, mode='quordle', popularity=True):
    """The flat 30-tuple: five picks and an options line for the combined
    view, then for each puzzle. quordle_data_dict is the page's boxes:
    [{"letter": "A", "position": "1", "color": "1", "row": "1", "puzzle": "1"}, ...]
    with color '0' unset, '1' gray, '2' yellow, '3' green. mode is
    'quordle' or 'sequence'; popularity=False treats every word as an
    equally likely answer, for scripts/backtest_quordle.py."""
    df = import_df
    _, codes, has, _, _ = _anti_arrays(df)
    _, odds, cache = _quordle_arrays(df)
    n = len(df)
    empty = not any(entry['letter'] for entry in quordle_data_dict)
    if empty and (mode, popularity) in cache:
        return cache[(mode, popularity)]
    weights = odds if popularity else np.ones(n)

    boxes = {k: [e for e in quordle_data_dict if str(e['puzzle']) == str(k)] for k in range(1, 5)}
    rows = {k: _played_rows(boxes[k]) for k in boxes}
    solved = {k: any(len(shown) == 5 and all(c == 2 for c in shown.values()) for _, shown in rows[k])
              for k in boxes}
    unsolved = [k for k in boxes if not solved[k]]
    # Sequence shows one board at a time: the first unsolved one
    open_boards = unsolved[:1] if mode == 'sequence' else unsolved
    closed = [k for k in unsolved if k not in open_boards]

    # the words played so far, in order: the full rows on any board
    letters = {}
    for entry in quordle_data_dict:
        if entry['letter']:
            letters.setdefault((int(entry['row']), str(entry['puzzle'])), {})[int(entry['position'])] = \
                entry['letter'].lower()
    played = {}
    for (row, _), word in sorted(letters.items()):
        if len(word) == 5:
            played.setdefault(row, ''.join(word[p] for p in range(1, 6)))
    played = list(played.values())
    unplayed = ~np.isin(df['word'].to_numpy(), played)

    answers = {k: _board_answers(df, codes, boxes[k], rows[k], unplayed) for k in open_boards}
    boards = [cand for cand in answers.values() if len(cand)]
    score_of = {}
    if boards:
        everyone = np.unique(np.concatenate(boards))
        sure = np.array(sorted({int(cand[0]) for cand in boards if len(cand) == 1}), int)
        if QUORDLE_LIMITS[mode] - len(played) <= len(unsolved):
            pool = everyone  # each guess left has to solve a board
        else:
            cheap = _letter_score(codes, has, boards, weights)
            cheap[~unplayed] = -np.inf
            pool = np.argsort(-cheap, kind='stable')[:QUORDLE_SHORTLIST]
            pool = np.union1d(pool, everyone if len(everyone) <= QUORDLE_CANDIDATES else sure)
        cash_in = SEQUENCE_CASH_IN if mode == 'sequence' else 0.0
        score = np.zeros(len(pool))
        for cand in boards:
            sample = _spread(cand, QUORDLE_SAMPLE)
            score += _entropy(_patterns(codes[pool], codes[sample]), weights[sample] / weights[sample].sum())
            hit = np.zeros(n)
            hit[cand] = weights[cand] / weights[cand].sum()
            score += (cash_in + 1e-9) * hit[pool]  # ties go to a possible answer
        if closed:
            played_codes = [np.array([[ord(c) - 97 for c in w]], dtype=np.int8) for w in played]
            score += SEQUENCE_CLOSED_WEIGHT * len(closed) * _closed_info(codes, pool, played_codes, weights)
        score_of = dict(zip(pool.tolist(), score))
        first = np.isin(pool, sure)  # a sure answer is never worse played now
        order = pool[np.lexsort((-score, ~first))]
        result = _picks(df.iloc[order[:5]])
    else:
        result = _picks(df.iloc[:0])
    result.append(_options_line(n, n))

    word_score = df['word_score'].to_numpy()
    for k in boxes:
        if solved[k]:
            result += ['', '', '', '', '', 'Solved']
        elif k in closed:
            result += ['', '', '', '', '', 'Not open yet']
        else:
            cand = answers[k]
            scored = np.array([score_of.get(int(i), -np.inf) for i in cand])
            ranked = cand[np.lexsort((-word_score[cand], -weights[cand], -scored))]
            result += _picks(df.iloc[ranked[:5]]) + [_options_line(len(cand), n)]
    result = tuple(result)
    if empty:
        cache[(mode, popularity)] = result
    return result
