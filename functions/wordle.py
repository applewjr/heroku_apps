import re
import numpy as np

ALPHABET = 'abcdefghijklmnopqrstuvwxyz'
# Each letter's value, a to z: how common it is in the word list, frozen when
# the list was first scored. A word's static score is the sum over its
# different letters (a repeat adds nothing), rounded to 4 places. It ranks
# /fixer's words and breaks Quordle's last ties.
LETTER_VALUE = np.array([
    0.082313053, 0.026294971, 0.035524581, 0.041062347, 0.09949143,   # a-e
    0.018722923, 0.024185346, 0.029572424, 0.058052364, 0.003428141,  # f-j
    0.021171595, 0.053870785, 0.029610096, 0.045884347, 0.063514786,  # k-o
    0.032774534, 0.002034281, 0.067281974, 0.098398945, 0.054548879,  # p-t
    0.039969862, 0.011602938, 0.018346205, 0.005236391, 0.032586174,  # u-y
    0.004520625])                                                     # z


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


_words = None  # (df, the arrays _word_arrays returns)


def _word_arrays(df):
    """For the words in df, built once per df: letter codes (n, 5), letter
    presence (n, 26), the static score (see LETTER_VALUE) and popularity."""
    global _words
    data = _words
    if data is None or data[0] is not df:
        codes = np.array([[ord(c) - 97 for c in w] for w in df['word']], dtype=np.int8)
        has = np.zeros((len(codes), 26), bool)
        for i in range(5):
            has[np.arange(len(codes)), codes[:, i]] = True
        # left to right, each letter's first use only
        score = LETTER_VALUE[codes[:, 0]]
        for i in range(1, 5):
            score = score + LETTER_VALUE[codes[:, i]] * (codes[:, i:i + 1] != codes[:, :i]).all(1)
        data = _words = (df, codes, has, np.round(score, 4), df['popularity_score'].to_numpy())
    return data


def _letter_code(letter):
    """0 to 25 for a to z, else None (a letter no word has)."""
    return ord(letter) - 97 if len(letter) == 1 and 'a' <= letter <= 'z' else None


def _allowed(codes, has, gray, greens, yellows):
    """Which words a board allows: every yellow letter somewhere, no gray
    letter anywhere, each green in its position, and no yellow letter in the
    position it was yellow in."""
    keep = np.ones(len(codes), bool)
    for letter in ''.join(yellows):
        c = _letter_code(letter)
        keep &= has[:, c] if c is not None else False
    for letter in gray:
        c = _letter_code(letter)
        if c is not None:
            keep &= ~has[:, c]
    for i, green in enumerate(greens):
        if green:
            c = _letter_code(green)
            keep &= codes[:, i] == c if c is not None else False
    for i, letters in enumerate(yellows):
        for letter in letters:
            c = _letter_code(letter)
            if c is not None:
                keep &= codes[:, i] != c
    return keep


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


# --- wordle -----------------------------------------------------------------
# Every word is scored by how much its colors would tell about the answers
# still possible (entropy, in bits), with common words counted as likelier
# answers: daily answers are common words. Picks 1-5 are the best of the
# possible answers, so they always fit the board (hard mode too). When a word
# that can't be the answer would tell more than all of them, the best such
# words are Alt Picks. On the last guess, or with two answers left, the most
# common answer goes first. Quordle's machinery, on one board.
# scripts/backtest_wordle.py measures it.

WORDLE_SHORTLIST = 60  # words, and possible answers, scored exactly each tap, by a cheap letter count first
WORDLE_SAMPLE = 150    # possible answers each of them is scored against

_wordle_opener = None  # (df, the empty board's result)


def wordle_solver_split_revamp(import_df, wordle_data_dict):
    """The 14-tuple for a board: five picks, the options line, the first
    incomplete row, the complete rows, then whether to show Alt Picks and
    the five Alt Picks."""
    global _wordle_opener
    df = import_df
    empty = not any(entry['letter'] for entry in wordle_data_dict)
    if empty and _wordle_opener is not None and _wordle_opener[0] is df:
        return _wordle_opener[1]
    complete_rows, first_incomplete_row, _, _, _, guessed_word_set = _read_board(wordle_data_dict)
    _, codes, has, _, popularity = _word_arrays(df)
    _, weights, _ = _quordle_arrays(df)
    words = df['word'].to_numpy()
    unplayed = ~np.isin(words, list(guessed_word_set))
    cand = _board_answers(df, codes, wordle_data_dict, _played_rows(wordle_data_dict), unplayed)

    alt = [False, '', '', '', '', '']
    if len(cand) <= 2 or first_incomplete_row == 6:
        ranked, rank_label = cand[np.argsort(-popularity[cand], kind='stable')], 'popularity'
    else:
        cheap = _letter_score(codes, has, [cand], weights)
        cheap[~unplayed] = -np.inf
        pool = np.union1d(np.argsort(-cheap, kind='stable')[:WORDLE_SHORTLIST],
                          cand[np.argsort(-cheap[cand], kind='stable')[:WORDLE_SHORTLIST]])
        sample = _spread(cand, WORDLE_SAMPLE)
        bits = _entropy(_patterns(codes[pool], codes[sample]), weights[sample] / weights[sample].sum())
        answer = np.isin(pool, cand)
        best = np.argsort(-(bits + 1e-9 * answer), kind='stable')  # ties go to a possible answer
        scored = np.full(len(df), -np.inf)
        scored[pool] = bits
        ranked, rank_label = cand[np.lexsort((-cheap[cand], -scored[cand]))], 'information'
        if not answer[best[0]]:
            others = pool[best[~answer[best]]][:5]
            alt = [True] + [f'Alt Pick {k + 1}: {words[i]}' for k, i in enumerate(others)]
            alt += [''] * (6 - len(alt))
    result = (*_picks(df.iloc[ranked[:5]]), f'{_options_line(len(cand), len(df))} · ranked by {rank_label}',
              first_incomplete_row, complete_rows, *alt)
    if empty:
        _wordle_opener = (df, result)
    return result


# last letter solver
def find_word_with_letters(import_df, must_be_present: str):
    """The words with the most of these letters (a repeated one counts
    again), best static score first: 'Pick 1: word (n match)' to 'Pick 5'."""
    _, _, has, score, _ = _word_arrays(import_df)
    count = np.zeros(len(has), np.int64)
    for letter in must_be_present.lower():
        c = _letter_code(letter)
        if c is not None:
            count += has[:, c]
    rank = np.lexsort((-score, -count))  # stable: ties stay in df's order
    words = import_df['word'].to_numpy()

    picks = []
    for k in range(5):
        if k < len(rank):
            picks.append(f'Pick {k + 1}: {words[rank[k]]} ({count[rank[k]]} match)')
        else:
            picks.append('No words found' if k == 0 else '')
    return tuple(picks)


def _pick_word(pick_str):
    if not pick_str:
        return None
    m = re.match(r'^Pick \d+: ([A-Za-z]+)', pick_str)
    return m.group(1).lower() if m else None


# --- antiwordle ---------------------------------------------------------------
# Antiwordle is won by lasting: every guess must keep the grays out, the greens
# in place and the yellows somewhere, until only the answer is left to play.
# The solver picks the guess whose likely colors leave the most safe words:
# playable words the colors prove can't be the answer, so playing one can't
# end the game. Scored on log(safe words left) so one big all-gray outcome
# can't hide a likely green, and a guess that could be the answer loses its
# odds of ending the game. scripts/backtest_antiwordle.py measures it.

# Answer odds by word: a logistic curve on popularity_score (a Zipf value), so
# common words count as likely answers and rare ones as nearly safe.
ANTI_POP_MID = 3.2
ANTI_POP_SLOPE = 2.5
ANTI_PRERANK = 300  # possible answers the cheap pre-rank uses, evenly spaced
ANTI_SAMPLE = 1000  # and the ones each of the top guesses is scored against
                    # (backtest: as long games as scoring against all of them)
ANTI_SHORTLIST = 600  # playable words the pre-rank looks at: those whose letters reveal least
ANTI_TOP_K = 100    # guesses scored on safe words kept, after the pre-rank
_HIT = 242          # all green: the guess was the answer
_POW3 = 3 ** np.arange(5)

_anti_data = None  # (df, letter codes, letter presence, answer odds, openers)


def _anti_arrays(df):
    """Letter codes (n, 5), letter presence (n, 26) and answer odds for the
    words in df, plus a dict for the empty-board picks, built once per df."""
    global _anti_data
    data = _anti_data
    if data is None or data[0] is not df:
        _, codes, has, _, pop = _word_arrays(df)
        odds = 1 / (1 + np.exp(-ANTI_POP_SLOPE * (pop - ANTI_POP_MID)))
        data = _anti_data = (df, codes, has, odds, {})
    return data


def _patterns(guesses, targets):
    """The colors each guess gets against each target (both rows of letter
    codes), as (len(guesses), len(targets)) codes 0 to 242: position k adds
    3**k for yellow and 2 * 3**k for green. Greens first, then yellows left
    to right while the target has that letter to spare, as the game does.
    A guess with five different letters takes a shortcut: a letter that
    isn't green is yellow when the target has it at all."""
    n = len(targets)
    # how many of each letter each target has, (26, n)
    count = np.bincount((targets.astype(np.intp) * n + np.arange(n)[:, None]).ravel(),
                        minlength=26 * n).reshape(26, n).astype(np.int8)
    has = count > 0
    ordered = np.sort(guesses, 1)
    distinct = (ordered[:, 1:] != ordered[:, :-1]).all(1)
    out = np.empty((len(guesses), n), np.int16)
    step = max(1, 400_000 // max(1, n))  # keeps each block a few MB
    for rows, repeats in ((np.flatnonzero(distinct), False), (np.flatnonzero(~distinct), True)):
        for start in range(0, len(rows), step):
            block = rows[start:start + step]
            g = guesses[block]
            green = [g[:, i, None] == targets[None, :, i] for i in range(5)]
            code = np.zeros((len(block), n), np.int16)
            yellow = []
            for i in range(5):
                letter = g[:, i]
                if not repeats:
                    y = has[letter] & ~green[i]
                else:
                    # the target's copies of the letter that greens leave,
                    # against the earlier yellows of it in this guess
                    spare = count[letter].copy()
                    for j in range(5):
                        same = g[:, j] == letter
                        if same.any():
                            spare -= green[j] & same[:, None]
                    used = np.zeros_like(spare)
                    for j in range(i):
                        same = g[:, j] == letter
                        if same.any():
                            used += yellow[j] & same[:, None]
                    y = ~green[i] & (spare > used)
                yellow.append(y)
                code += green[i] * np.int16(2 * 3 ** i)
                code += y * np.int16(3 ** i)
            out[block] = code
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
    playable_idx = np.flatnonzero(playable)
    cand = np.flatnonzero(possible)
    idx = playable_idx
    if len(idx) > ANTI_SHORTLIST:
        # the words whose letters split the answers least: where the pre-rank's
        # picks come from anyway, at a fraction of the cost
        cheap = _letter_score(codes, has, [cand], weights)
        idx = np.sort(idx[np.argsort(cheap[idx], kind='stable')[:ANTI_SHORTLIST]])

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

    # score: expected log(safe words still playable), times the odds the guess
    # isn't the answer. The safe words are the playable ones less the answers
    # the colors leave, which the sample estimates.
    sample = spread(ANTI_SAMPLE)
    weight = weights[sample]
    pats = _patterns(codes[idx[top]], codes[sample])
    codes_a, has_a = codes[playable_idx], has[playable_idx]
    total = weights[cand].sum()
    scores = []
    for pos, row in zip(top, pats):
        guess = codes[idx[pos]]
        value = mass = 0.0
        for pattern in np.unique(row):
            if pattern == _HIT:
                continue
            same = row == pattern
            w = weight[same].sum()
            keep = _kept(codes_a, has_a, guess, pattern, known)
            keep[np.searchsorted(playable_idx, idx[pos])] = False
            answers = same.sum() / len(row) * len(cand)
            value += w * np.log1p(max(keep.sum() - answers, 0))
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
        _, _, has, _, _ = _word_arrays(df)
        possible = _allowed(codes, has, gray, greens, yellows) & unplayed
    return np.flatnonzero(possible)


def quordle_solver_split_revamp(import_df, quordle_data_dict, mode='quordle', popularity=True):
    """The flat 30-tuple: five picks and an options line for the combined
    view, then for each puzzle. quordle_data_dict is the page's boxes:
    [{"letter": "A", "position": "1", "color": "1", "row": "1", "puzzle": "1"}, ...]
    with color '0' unset, '1' gray, '2' yellow, '3' green. mode is
    'quordle' or 'sequence'; popularity=False treats every word as an
    equally likely answer, for scripts/backtest_quordle.py."""
    df = import_df
    _, codes, has, word_score, _ = _word_arrays(df)
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
