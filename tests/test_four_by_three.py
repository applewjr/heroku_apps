"""The 4x3 solver (functions/four_by_three.py) and its page (/4x3).

The engine runs on a tiny made-up dataset here: nine invented-for-the-test
words with hand-set vectors, synonyms and pairs, never a real puzzle's. The
page tests use the shipped word data and a board made up from dictionary
words (one of datasets/four_by_three_eval.tsv's). Nothing reaches a database
(conftest.py keeps /4x3 off it).
"""

import itertools
import json
import random
import re
from collections import defaultdict

import numpy as np
import pytest

from functions import four_by_three as fbt

# A made-up board: the hub, and four pairs that each make a group with it.
HUB = 'HUB'
PAIRS = [('ANT', 'BEE'), ('CAT', 'DOG'), ('ELM', 'FIR'), ('GAS', 'OIL')]
BOARD = [HUB] + [w for p in PAIRS for w in p]


class FakeWords:
    """Stands in for functions/chainle.py Data: vectors, synonyms, families."""

    def __init__(self, vectors, synonyms=None, related=None):
        self.vocab = sorted(vectors)
        self.index = {w: i for i, w in enumerate(self.vocab)}
        mat = np.array([vectors[w] for w in self.vocab], dtype=np.float32)
        self.vec = mat / np.linalg.norm(mat, axis=1, keepdims=True)
        self.scale = np.ones(len(self.vocab), dtype=np.float32)
        self._syn = self._lists(synonyms)
        self._rel = self._lists(related)

    def _lists(self, mapping):
        out = defaultdict(list)
        for w, xs in (mapping or {}).items():
            for x in xs:
                out[self.index[w]].append(self.index[x])
                out[self.index[x]].append(self.index[w])
        return {k: np.array(sorted(v)) for k, v in out.items()}

    def id(self, word):
        return self.index.get(word.strip().lower(), -1)

    def synonyms(self, i):
        return self._syn.get(i, np.array([], dtype=np.int64))

    def related(self, i):
        return self._rel.get(i, np.array([], dtype=np.int64))


class FakePairs:
    """Stands in for functions/circuits.py Data: directed pairs "a b"."""

    def __init__(self, pairs):
        self.vocab = sorted({w for a, b, _w in pairs for w in (a, b)})
        self.index = {w: i for i, w in enumerate(self.vocab)}
        self.flags = np.ones(len(self.vocab), dtype=np.uint8)
        self.succ, self.pred = defaultdict(list), defaultdict(list)
        for a, b, w in pairs:
            self.succ[self.index[a]].append((self.index[b], w))
            self.pred[self.index[b]].append((self.index[a], w))

    def id(self, text):
        return self.index.get(' '.join(str(text).lower().split()), -1)

    def word(self, i):
        return self.vocab[i]

    def _side(self, rows):
        rows = sorted(rows)
        return np.array([r[0] for r in rows], dtype=np.int64), np.array([r[1] for r in rows], dtype=float)

    def follows(self, i):
        return self._side(self.succ[i])

    def precedes(self, i):
        return self._side(self.pred[i])


def _vectors():
    """The hub sits between four directions; each pair sits on one."""
    dims = np.eye(8)
    vec = {'hub': dims[0] + dims[1] + dims[2] + dims[3]}
    for k, (a, b) in enumerate(PAIRS):
        vec[a.lower()] = dims[k] + 0.25 * dims[4 + k]
        vec[b.lower()] = dims[k] - 0.25 * dims[4 + k]
    return vec


@pytest.fixture
def words():
    return FakeWords(_vectors())


def _signals(words, tiles=BOARD, pairs=None, seed=3):
    tiles = list(tiles)
    random.Random(seed).shuffle(tiles)
    return fbt.Signals.build(fbt.parse_board(tiles), words, pairs)


def _groups(result):
    return {frozenset(g['tiles']) for g in result['groups']}


TRUTH = {frozenset((HUB,) + p) for p in PAIRS}


# --- the answers --------------------------------------------------------------------

def test_there_are_945_answers_each_a_hub_and_four_pairs():
    assert fbt.ANSWERS == 945 and len(fbt.GROUP_LIST) == 252
    seen = set()
    for n in range(fbt.ANSWERS):
        hub = 1 << int(fbt.ANSWER_HUB[n])
        masks = [int(m) for m in fbt.ANSWER_MASKS[n]]
        assert all(m & hub for m in masks)
        union = 0
        for m in masks:
            union |= m
        assert union == (1 << fbt.TILES) - 1
        assert all(a & b == hub for a, b in itertools.combinations(masks, 2))
        seen.add(tuple(sorted(masks)))
    assert len(seen) == fbt.ANSWERS


def test_answer_index_finds_each_answer():
    for n in (0, 104, 105, 500, 944):
        hub = int(fbt.ANSWER_HUB[n])
        pairs = [fbt.GROUP_LIST[g][1:] for g in fbt.ANSWER_GROUPS[n]]
        assert fbt.answer_index(hub, [tuple(reversed(p)) for p in pairs]) == n
    assert fbt.answer_index(0, [(1, 2), (3, 4), (5, 6), (7, 0)]) is None
    assert fbt.answer_index(0, [(1, 2), (1, 3), (5, 6), (7, 8)]) is None


# --- the board ----------------------------------------------------------------------

def test_a_board_is_nine_different_words_in_capitals():
    assert fbt.parse_board(['  ice  cream', 'a', "rock 'n' roll", 'T-SHIRT', '4x4', 'b', 'c', 'd', 'e']) == (
        'ICE CREAM', 'A', "ROCK 'N' ROLL", 'T-SHIRT', '4X4', 'B', 'C', 'D', 'E')


@pytest.mark.parametrize('tiles', [
    None, 'nine words', ['A'] * 9, ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'],
    ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', ''], ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'X' * 41],
    ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', '<b>'], ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', '--'],
    ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 9], ['a', 'A', 'C', 'D', 'E', 'F', 'G', 'H', 'I'],
])
def test_bad_boards_are_refused(tiles):
    with pytest.raises(fbt.BoardError):
        fbt.parse_board(tiles)


def test_a_board_is_the_same_board_in_any_order():
    assert fbt.board_key(BOARD) == fbt.board_key(list(reversed(BOARD)))
    assert fbt.board_key(BOARD) != fbt.board_key(BOARD[:8] + ['NEW'])


# --- solving ------------------------------------------------------------------------

def test_a_clear_board_is_solved_and_sure(words):
    result = fbt.solve(_signals(words), fbt.DEFAULT_WEIGHTS)
    assert result['hub']['tile'] == HUB and result['hub']['label'] == 'sure'
    assert _groups(result) == TRUTH
    assert all(g['label'] == 'sure' for g in result['groups'])
    assert all(g['tiles'][0] == HUB and g['pair'] == g['tiles'][1:] for g in result['groups'])
    assert result['answers_left'] == fbt.ANSWERS and result['dropped'] == []
    assert result['known'] == [True] * 9


def _brute(kind, trio, solved):
    """Which answers a mark allows, worked out the slow way from the rules."""
    out = []
    for n in range(fbt.ANSWERS):
        groups = [frozenset((fbt.GROUP_LIST[g][0],) + fbt.GROUP_LIST[g][1:]) for g in fbt.ANSWER_GROUPS[n]]
        if kind == 'hub':
            out.append(int(fbt.ANSWER_HUB[n]) == trio)
            continue
        is_group = trio in groups
        open_groups = [g for g in groups if g not in solved]
        two = any(len(g & trio) == 2 for g in open_groups)
        out.append({'group': is_group, 'no': not is_group, 'missed': not is_group,
                    'one_away': not is_group and two,
                    'not_close': not is_group and not any(len(g & trio) >= 2 for g in open_groups)}[kind])
    return np.array(out)


def test_what_each_mark_allows_matches_the_rules():
    rng = random.Random(11)
    for _ in range(40):
        trio = frozenset(rng.sample(range(9), 3))
        solved_groups = [frozenset(rng.sample(range(9), 3)) for _ in range(rng.choice((0, 1, 2)))]
        solved = frozenset(fbt.mask_of(g) for g in solved_groups)
        for kind in ('group', 'no', 'missed', 'one_away', 'not_close'):
            assert (fbt._fits(kind, fbt.mask_of(trio), solved) == _brute(kind, trio, set(solved_groups))).all(), kind
    for hub in range(9):
        assert (fbt._fits('hub', 1 << hub) == _brute('hub', hub, set())).all()


def _at(signals, words_):
    at = {t: i for i, t in enumerate(signals.tiles)}
    return tuple(at[w] for w in words_)


def test_a_right_mark_is_kept_and_labelled_yours(words):
    signals = _signals(words)
    result = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, marks=[(_at(signals, (HUB, 'ANT', 'BEE')), 'right')])
    mine = next(g for g in result['groups'] if set(g['tiles']) == {HUB, 'ANT', 'BEE'})
    assert mine['label'] == 'yours' and result['groups'][0] is mine


def test_not_even_close_rules_out_the_hub(words):
    signals = _signals(words)
    result = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, marks=[(_at(signals, (HUB, 'ANT', 'CAT')), 'not_close')])
    assert result['hub']['tile'] not in (HUB, 'ANT', 'CAT')


def test_one_away_on_a_group_with_the_hub_keeps_the_hub(words):
    signals = _signals(words)
    result = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, marks=[(_at(signals, (HUB, 'ANT', 'CAT')), 'one_away')])
    assert result['hub']['tile'] == HUB and _groups(result) == TRUTH


def test_not_sure_which_only_says_it_missed(words):
    signals = _signals(words)
    # Not a group, and nothing more: the hub can still be among the three
    # (where Not even close would rule it out).
    result = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, marks=[(_at(signals, (HUB, 'ANT', 'CAT')), 'missed')])
    assert result['hub']['tile'] == HUB and _groups(result) == TRUTH and result['dropped'] == []
    # A true group said to have missed is left out of the answer.
    result = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, marks=[(_at(signals, (HUB, 'ANT', 'BEE')), 'missed')])
    assert frozenset((HUB, 'ANT', 'BEE')) not in _groups(result)


def test_a_mark_that_contradicts_the_ones_before_is_left_out(words):
    signals = _signals(words)
    trio = _at(signals, (HUB, 'ANT', 'BEE'))
    result = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, marks=[(trio, 'right'), (trio, 'one_away'), ((0,), 'hub')])
    assert 1 in result['dropped'] and _groups(result) & {frozenset((HUB, 'ANT', 'BEE'))}


def test_the_hub_can_be_called(words):
    signals = _signals(words)
    result = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, marks=[(_at(signals, ('ANT',)), 'hub')])
    assert result['hub']['tile'] == 'ANT' and result['hub']['label'] == 'yours'


def test_known_groups_win_and_the_last_one_is_left_over(words):
    signals = _signals(words)
    mask = lambda ws: fbt.mask_of(_at(signals, ws))
    # A made-up "real" answer that the vectors disagree with.
    answer = [(HUB, 'ANT', 'CAT'), (HUB, 'BEE', 'DOG'), (HUB, 'ELM', 'GAS')]
    result = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, fixed={mask(ws): 'players' for ws in answer})
    labels = {frozenset(g['tiles']): g['label'] for g in result['groups']}
    assert labels == {**{frozenset(ws): 'players' for ws in answer}, frozenset((HUB, 'FIR', 'OIL')): 'left'}
    checked = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, fixed={mask(ws): 'checked' for ws in answer},
                        names={mask(answer[0]): 'Small things'})
    assert checked['hub']['label'] == 'checked'
    small = next(g for g in checked['groups'] if g['label'] == 'checked' and 'ANT' in g['tiles'])
    assert small['theme'] == 'Small things'


def test_refused_groups_are_never_suggested(words):
    signals = _signals(words)
    bad = fbt.mask_of(_at(signals, (HUB, 'ANT', 'BEE')))
    result = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, refused={bad})
    assert frozenset((HUB, 'ANT', 'BEE')) not in _groups(result)


def test_unsure_groups_offer_alternatives_and_candidates_narrow_the_hub(words):
    signals = _signals(words)
    result = fbt.solve(signals, np.asarray(fbt.DEFAULT_WEIGHTS) * 0.05)
    assert all(g['label'] in ('likely', 'guess') for g in result['groups'])
    assert any(g['alternatives'] for g in result['groups'])
    for g in result['groups']:
        for alt in g['alternatives']:
            assert len(set(alt['pair']) & set(g['pair'])) == 1
    assert 1 < len(result['hub']['candidates']) <= fbt.HUB_CANDIDATES
    assert result['hub']['candidates'][0][0] == result['hub']['tile']


def test_words_the_data_lacks(words):
    signals = _signals(words, ['ZZZ' if t == 'GAS' else t for t in BOARD])
    assert signals.known[signals.tiles.index('ZZZ')] is False
    # A tile of two words it knows, and one with a digit (never a word).
    tiles = ['ANT BEE' if t == 'ANT' else '4X4' if t == 'BEE' else t for t in BOARD]
    signals = _signals(words, tiles)
    assert signals.known[signals.tiles.index('ANT BEE')] is True
    assert signals.known[signals.tiles.index('4X4')] is False
    assert fbt.solve(signals, fbt.DEFAULT_WEIGHTS)['tiles'] == list(signals.tiles)


def test_a_plural_the_data_lacks_reads_as_its_singular(words):
    signals = _signals(words, ['ANTS' if t == 'ANT' else t for t in BOARD])
    assert signals.known[signals.tiles.index('ANTS')] is True


def test_a_shared_partner_names_the_theme_but_never_on_a_guess(words):
    pairs = FakePairs([(w, 'ball', 9.0) for w in ('hub', 'elm', 'fir')] + [('ant', 'ball', 1.0)])
    result = fbt.solve(_signals(words, pairs=pairs), fbt.DEFAULT_WEIGHTS)
    themes = {frozenset(g['tiles']): g['theme'] for g in result['groups']}
    assert themes[frozenset((HUB, 'ELM', 'FIR'))] == '___ BALL'
    assert themes[frozenset((HUB, 'ANT', 'BEE'))] is None
    before = FakePairs([('ball', w, 9.0) for w in ('hub', 'elm', 'fir')])
    result = fbt.solve(_signals(words, pairs=before), fbt.DEFAULT_WEIGHTS)
    assert '___' in next(g['theme'] for g in result['groups'] if 'ELM' in g['tiles']).split()[-1]
    guesses = fbt.solve(_signals(words, pairs=pairs), np.asarray(fbt.DEFAULT_WEIGHTS) * 0.02)
    assert all(g['theme'] is None for g in guesses['groups'] if g['label'] == 'guess')


def test_the_first_pass_is_compact(words):
    top = fbt.first_pass(_signals(words), fbt.DEFAULT_WEIGHTS)
    assert top['hub'] == HUB and top['hub_label'] == 'sure'
    assert {frozenset(g[:2]) for g in top['groups']} == {frozenset(p) for p in PAIRS}
    # Its guess at the order for the most points, as indexes into groups.
    assert sorted(top['order']) == [0, 1, 2, 3]
    assert len(json.dumps(top)) < 1000


# --- the most points --------------------------------------------------------------------

def test_game_points_follow_4x3s_scoring():
    best = fbt.BEST_ORDER
    # A perfect game is 200 whatever else it earned, then 5 for the fewest taps.
    assert fbt.game_points(best) == 205
    assert fbt.game_points(('yellow', 'green', 'blue', 'purple')) == 125
    assert fbt.game_points(None) == 125
    assert fbt.game_points(('purple', 'green', 'blue', 'yellow')) == 140
    assert fbt.game_points(('blue', 'purple', 'green', 'yellow')) == 130
    # A mistake costs 15, or 30 once two groups are solved, and the order
    # bonuses still count; three lose the game.
    assert fbt.game_points(best, misses_at=[0]) == 150
    assert fbt.game_points(None, misses_at=[1]) == 105
    assert fbt.game_points(None, misses_at=[2]) == 90
    assert fbt.game_points(None, misses_at=[0, 3]) == 75
    assert fbt.game_points(best, misses_at=[0, 0, 1]) is None
    # After a right hub call, mistakes cost nothing and never lose the game.
    assert fbt.game_points(None, misses_at=[0, 2, 3], called=True) == 120
    assert fbt.game_points(best, called=True) == 205


def _ranked_words():
    """The test words with links of different strengths: none for ANT-BEE, a
    synonym pair for CAT-DOG, a synonym and family pair for ELM-FIR, and
    those plus a synonym of the hub for GAS-OIL."""
    return FakeWords(_vectors(), synonyms={'cat': ['dog'], 'elm': ['fir'], 'gas': ['oil', 'hub']},
                     related={'elm': ['fir'], 'gas': ['oil']})


def _planned(result):
    return [frozenset(result['groups'][i]['pair']) for i in result['plan']['order']]


PAIR_SETS = {p: frozenset(p) for p in PAIRS}
# Made-up colors, an order that isn't the solver's guess.
COLORS = {('GAS', 'OIL'): 'purple', ('ELM', 'FIR'): 'blue', ('ANT', 'BEE'): 'green', ('CAT', 'DOG'): 'yellow'}


def _colors(signals):
    return {fbt.mask_of(_at(signals, (HUB,) + p)): c for p, c in COLORS.items()}


def test_without_colors_the_plan_guesses_hardest_looking_first():
    signals = _signals(_ranked_words())
    result = fbt.solve(signals, fbt.DEFAULT_WEIGHTS)
    assert _groups(result) == TRUTH
    assert _planned(result) == [PAIR_SETS[p] for p in PAIRS]
    plan = result['plan']
    assert plan['colors'] == 'guess' and plan['points'] == 205 and plan['floor'] == 125 and plan['perfect']
    assert plan['solved'] == 0 and plan['started'] is False
    assert all(g['color'] is None for g in result['groups'])
    # The listing itself stays surest first, so Help's group numbers hold.
    assert [g['p'] for g in result['groups']] == sorted((g['p'] for g in result['groups']), reverse=True)


def test_jamess_colors_set_the_plan_purple_blue_green_yellow():
    signals = _signals(_ranked_words())
    checked = {fbt.mask_of(_at(signals, (HUB,) + p)): 'checked' for p in PAIRS}
    result = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, fixed=checked, colors=_colors(signals))
    assert _planned(result) == [PAIR_SETS[p] for p in (('GAS', 'OIL'), ('ELM', 'FIR'), ('ANT', 'BEE'), ('CAT', 'DOG'))]
    assert result['plan'] == dict(result['plan'], colors='checked', points=205, floor=125, perfect=True)
    assert {frozenset(g['pair']): g['color'] for g in result['groups']} == {PAIR_SETS[p]: c for p, c in COLORS.items()}


def test_the_plan_keeps_the_players_solved_groups_first():
    signals = _signals(_ranked_words())
    colors = _colors(signals)
    green_first = [(_at(signals, (HUB, 'ANT', 'BEE')), 'right')]
    result = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, marks=green_first, colors=colors)
    assert _planned(result)[0] == PAIR_SETS[('ANT', 'BEE')]
    assert result['plan'] == dict(result['plan'], points=125, perfect=False, solved=1, started=True)
    purple_first = [(_at(signals, (HUB, 'GAS', 'OIL')), 'right')]
    result = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, marks=purple_first, colors=colors)
    assert result['plan'] == dict(result['plan'], points=205, perfect=True, solved=1)
    # Without colors, a solved group's color is the player's to see: the plan
    # promises only what no order bonus can take away.
    result = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, marks=green_first)
    assert result['plan'] == dict(result['plan'], colors='guess', points=None, floor=125)


def test_the_plan_counts_the_players_misses():
    signals = _signals(_ranked_words())
    early = [(_at(signals, (HUB, 'ANT', 'CAT')), 'one_away')]
    plan = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, marks=early)['plan']
    assert plan == dict(plan, points=150, floor=105, perfect=False, started=True, solved=0)
    late = [(_at(signals, (HUB,) + PAIRS[0]), 'right'), (_at(signals, (HUB,) + PAIRS[1]), 'right'),
            (_at(signals, (HUB, 'ELM', 'GAS')), 'one_away')]
    assert fbt.solve(signals, fbt.DEFAULT_WEIGHTS, marks=late)['plan']['floor'] == 90
    misses = [(_at(signals, trio), 'one_away') for trio in ((HUB, 'ANT', 'CAT'), (HUB, 'ANT', 'DOG'), (HUB, 'BEE', 'CAT'))]
    lost = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, marks=misses)['plan']
    assert lost['points'] is None and lost['floor'] is None
    called = fbt.solve(signals, fbt.DEFAULT_WEIGHTS, marks=[(_at(signals, (HUB,)), 'hub')] + misses)['plan']
    assert called['floor'] == 120 and called['points'] == 165


# --- tuning ---------------------------------------------------------------------------

def _synthetic(true_w, n, seed):
    rng = np.random.default_rng(seed)
    examples = []
    for _ in range(n):
        feats = rng.normal(size=(fbt.ANSWERS, fbt.NF))
        s = feats @ true_w
        p = np.exp(s - s.max())
        p /= p.sum()
        examples.append((feats, int(rng.choice(fbt.ANSWERS, p=p))))
    return examples


def test_fit_finds_the_weights_the_answers_came_from():
    true_w = np.array([1.5, 0.5, 2.0, 0.0, 1.0, 0.3, 2.5])
    fitted = fbt.fit(_synthetic(true_w, 400, seed=5), prior=np.ones(fbt.NF), strength=0.01)
    assert np.abs(fitted - true_w).max() < 0.35


def test_fit_stays_near_the_prior_with_few_answers_and_never_goes_negative():
    prior = np.asarray(fbt.DEFAULT_WEIGHTS)
    assert np.array_equal(fbt.fit([], prior=prior), prior)
    examples = _synthetic(np.array([-3.0, 0, 0, 0, 0, 0, 0]), 3, seed=2)
    fitted = fbt.fit(examples, prior=prior)
    assert (fitted >= 0).all()
    assert np.abs(fitted - prior).max() < np.abs(prior).max()
    objective = lambda w: fbt._objective(w, examples, prior, fbt.PRIOR_STRENGTH)
    assert objective(fitted) <= objective(prior)


def _correlated(true_w, n, seed, corr=0.7):
    """Like _synthetic, with a part all the features share, so the weights
    pull on each other: a Newton step cut off at 0 can then miss the best
    weights."""
    rng = np.random.default_rng(seed)
    examples = []
    for _ in range(n):
        shared = rng.normal(size=(fbt.ANSWERS, 1))
        feats = corr * shared + (1 - corr) * rng.normal(size=(fbt.ANSWERS, fbt.NF))
        s = feats @ true_w
        p = np.exp(s - s.max())
        p /= p.sum()
        examples.append((feats, int(rng.choice(fbt.ANSWERS, p=p))))
    return examples


def test_fit_reaches_the_best_weights_when_some_are_held_at_zero():
    # Answers that want some weights below 0. The best weights at 0 or above
    # hold those at 0 (where the objective rises as they grow) and leave the
    # objective flat along every other one.
    prior, strength = np.asarray(fbt.DEFAULT_WEIGHTS), 0.5
    examples = _correlated(np.array([4.0, -3.0, 2.0, -2.0, 1.0, 0.5, 3.0]), 20, seed=3)
    w = fbt.fit(examples, prior=prior, strength=strength)
    grad = strength * (w - prior)
    for feats, right in examples:
        s = feats @ w
        p = np.exp(s - s.max())
        grad += (p / p.sum()) @ feats - feats[right]
    held = w == 0
    assert held.any() and not held.all()
    assert np.abs(grad[~held]).max() < 1e-4 and grad[held].min() > -1e-4


def test_weights_code_tells_weights_apart():
    assert fbt.weights_code(fbt.DEFAULT_WEIGHTS) == fbt.weights_code(list(fbt.DEFAULT_WEIGHTS))
    assert fbt.weights_code(fbt.DEFAULT_WEIGHTS) != fbt.weights_code(np.asarray(fbt.DEFAULT_WEIGHTS) * 1.01)


# --- the page ------------------------------------------------------------------------

# A made-up board from dictionary words (datasets/four_by_three_eval.tsv).
PAGE_BOARD = ['RACKET', 'BAT', 'OWL', 'WINK', 'SUPER', 'CLUB', 'MOTH', 'BLINK', 'SPIDER']


def _faq(html):
    blocks = [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
    return next(b for b in blocks if b.get('@type') == 'FAQPage')


def test_page_shows_the_marker_and_says_unofficial(client):
    html = client.get('/4x3').get_data(as_text=True)
    # The hourly web probe expects this marker (datasets/health_web.yaml).
    assert 'id="fbt-data-ok"' in html
    assert 'Not affiliated with or endorsed by Hank Green' in html
    assert 'https://www.hankgreen.com/fourbythree/' in html


def test_faq_structured_data_parses_and_matches_the_visible_faq(client):
    html = client.get('/4x3').get_data(as_text=True)
    faq = _faq(html)
    visible = re.sub(r'<[^>]+>', '', re.sub(r'<script.*?</script>', '', html, flags=re.S))
    visible = ' '.join(visible.split())
    assert len(faq['mainEntity']) == 9
    for q in faq['mainEntity']:
        assert q['name'] in visible
        assert q['acceptedAnswer']['text'] in visible
    assert any('midnight where you are' in q['acceptedAnswer']['text'] for q in faq['mainEntity'])
    assert any('perfect game' in q['acceptedAnswer']['text'] for q in faq['mainEntity'])


def test_faq_leaves_out_shared_boards_when_they_are_off(client, monkeypatch):
    import routes.four_by_three as rt
    monkeypatch.setattr(rt, 'FOUR_BY_THREE_BOARDS_LIVE', False)
    html = client.get('/4x3').get_data(as_text=True)
    assert len(_faq(html)['mainEntity']) == 8 and 'id="fbt-shared"' not in html


def test_the_page_never_tells_players_to_hurry(client):
    # A perfect game scores the same at any speed: the page leaves the
    # game's clock bonus out, in its words and its script's.
    html = client.get('/4x3').get_data(as_text=True).lower()
    for word in ('90 seconds', 'hurry', 'faster', 'quickly', 'speed', 'under 90', 'clock bonus', 'sub-90'):
        assert word not in html, f'the page mentions {word!r}'


def test_players_are_never_told_how_boards_or_answers_are_decided(client):
    lowered = client.get('/4x3').get_data(as_text=True).lower()
    for hint in ('different players', 'another player', 'players agreed', 'for everyone',
                 'two players', '2 players', 'three players', '3 players', 'majority', 'networks'):
        assert hint not in lowered, f'page hints at a threshold: {hint!r}'


def test_the_page_wears_a_beta_tag_until_it_comes_off(client, monkeypatch):
    import helpers
    html = client.get('/4x3').get_data(as_text=True)
    assert re.search(r'<h1 class="fbt-h1">4x3 Answers &amp; Hints <span class="jj-beta-tag">Beta</span></h1>', html)
    assert 'Beta' not in re.search(r'<title>(.*?)</title>', html, re.S).group(1)
    monkeypatch.setattr(helpers, 'BETA_PAGES', frozenset())
    html = client.get('/4x3').get_data(as_text=True)
    assert '<h1 class="fbt-h1">4x3 Answers &amp; Hints</h1>' in html


def test_the_public_page_never_names_the_admin_pages(client):
    html = client.get('/4x3').get_data(as_text=True)
    assert '4x3_admin' not in html and 'ApproveState' not in html


def test_a_solve_through_the_route(client):
    resp = client.post('/4x3', json={'tiles': PAGE_BOARD})
    assert resp.status_code == 200
    result = resp.get_json()
    assert result['hub']['tile'] == 'BAT' and result['checked'] is False
    assert {frozenset(g['tiles']) for g in result['groups']} == {
        frozenset(t) for t in (('BAT', 'CLUB', 'RACKET'), ('BAT', 'OWL', 'MOTH'), ('BAT', 'BLINK', 'WINK'),
                               ('BAT', 'SUPER', 'SPIDER'))}
    marked = client.post('/4x3', json={'tiles': PAGE_BOARD, 'marks': [
        {'tiles': ['BAT', 'OWL', 'MOTH'], 'verdict': 'right'}]}).get_json()
    assert next(g for g in marked['groups'] if 'OWL' in g['tiles'])['label'] == 'yours'


@pytest.mark.parametrize('body', [
    {}, {'tiles': PAGE_BOARD[:8]}, {'tiles': PAGE_BOARD[:8] + ['BAT']},
    {'tiles': PAGE_BOARD, 'marks': 'no'},
    {'tiles': PAGE_BOARD, 'marks': [{'tiles': ['BAT', 'OWL'], 'verdict': 'right'}]},
    {'tiles': PAGE_BOARD, 'marks': [{'tiles': ['BAT', 'OWL', 'NOPE'], 'verdict': 'right'}]},
    {'tiles': PAGE_BOARD, 'marks': [{'tiles': ['BAT', 'OWL', 'OWL'], 'verdict': 'right'}]},
    {'tiles': PAGE_BOARD, 'marks': [{'tiles': ['BAT', 'OWL', 'MOTH'], 'verdict': 'maybe'}]},
    {'tiles': PAGE_BOARD, 'marks': [{'tiles': ['BAT', 'OWL', 'MOTH'], 'verdict': ['right']}]},
    {'tiles': PAGE_BOARD, 'marks': [{'tiles': ['BAT', 'OWL', 'MOTH'], 'verdict': {'right': 1}}]},
    {'tiles': PAGE_BOARD, 'marks': [{'tiles': ['BAT', 'OWL'], 'verdict': 'hub'}]},
    {'tiles': PAGE_BOARD, 'marks': [{'tiles': ['BAT'], 'verdict': 'hub'}] * 13},
    {'action': 'nope'},
])
def test_bad_requests_are_400s(client, body):
    assert client.post('/4x3', json=body).status_code == 400


def test_without_its_word_data_the_page_says_so(client, monkeypatch):
    import routes.four_by_three as rt
    monkeypatch.setattr(rt, 'get_words', lambda: None)
    assert 'The solver is unavailable right now' in client.get('/4x3').get_data(as_text=True)
    resp = client.post('/4x3', json={'tiles': PAGE_BOARD})
    assert resp.status_code == 503 and 'unavailable' in resp.get_json()['error']


def test_without_circuits_pairs_it_still_solves(client, monkeypatch):
    import routes.four_by_three as rt
    monkeypatch.setattr(rt, 'get_pairs', lambda: None)
    assert client.post('/4x3', json={'tiles': PAGE_BOARD}).get_json()['hub']['tile'] == 'BAT'


# --- the hub the player's marks prove -----------------------------------------------

def _proven(result):
    return result['hub']['proven']


def _marks(signals, *said):
    at = {t: i for i, t in enumerate(signals.tiles)}
    return [(tuple(at[w] for w in group), verdict) for group, verdict in said]


def test_two_right_groups_prove_the_hub_and_one_does_not(words):
    s = _signals(words)
    one = ((HUB,) + PAIRS[0], 'right')
    two = (PAIRS[1] + (HUB,), 'right')
    assert _proven(fbt.solve(s, fbt.DEFAULT_WEIGHTS)) is None
    assert _proven(fbt.solve(s, fbt.DEFAULT_WEIGHTS, _marks(s, one))) is None
    assert _proven(fbt.solve(s, fbt.DEFAULT_WEIGHTS, _marks(s, one, two))) == HUB
    # Undo either and it isn't proved any more.
    assert _proven(fbt.solve(s, fbt.DEFAULT_WEIGHTS, _marks(s, two))) is None


def test_misses_can_prove_the_hub_with_one_right_group(words):
    # HUB ANT BEE is a group, so the hub is one of the three. "Not even
    # close" on guesses holding ANT and BEE leaves only HUB.
    s = _signals(words)
    said = [((HUB, 'ANT', 'BEE'), 'right'),
            (('ANT', 'CAT', 'ELM'), 'not_close'), (('BEE', 'CAT', 'GAS'), 'not_close')]
    assert _proven(fbt.solve(s, fbt.DEFAULT_WEIGHTS, _marks(s, *said[:2]))) is None
    assert _proven(fbt.solve(s, fbt.DEFAULT_WEIGHTS, _marks(s, *said))) == HUB


def test_proof_comes_only_from_the_players_own_marks(words):
    s = _signals(words)
    at = {t: i for i, t in enumerate(s.tiles)}
    fixed = {fbt.mask_of(at[w] for w in (HUB,) + p): label
             for p in PAIRS for label in ['checked']}
    known = fbt.solve(s, fbt.DEFAULT_WEIGHTS, fixed=fixed)
    assert known['hub']['tile'] == HUB and _proven(known) is None
    players = fbt.solve(s, fbt.DEFAULT_WEIGHTS, fixed={m: 'players' for m in fixed})
    assert players['hub']['tile'] == HUB and _proven(players) is None
    marks = _marks(s, ((HUB,) + PAIRS[0], 'right'), ((HUB,) + PAIRS[1], 'right'))
    # On a checked board too, once the player's marks settle it.
    both = fbt.solve(s, fbt.DEFAULT_WEIGHTS, marks, fixed=fixed)
    assert both['hub']['label'] == 'checked' and _proven(both) == HUB


def test_marks_that_contradict_prove_nothing_wrong(words):
    s = _signals(words)
    right = [((HUB,) + PAIRS[0], 'right'), ((HUB,) + PAIRS[1], 'right')]
    # A third group with another shared word doesn't fit the first two, so
    # it's dropped and the proof stands.
    result = fbt.solve(s, fbt.DEFAULT_WEIGHTS, _marks(s, *right, (('ANT', 'ELM', 'GAS'), 'right')))
    assert result['dropped'] == [2] and _proven(result) == HUB
    # Marks that prove another hub than James's answer: nothing is proved.
    at = {t: i for i, t in enumerate(s.tiles)}
    fixed = {fbt.mask_of(at[w] for w in (HUB,) + p): 'checked' for p in PAIRS}
    other = _marks(s, (('ANT', 'CAT', 'ELM'), 'right'), (('ANT', 'DOG', 'FIR'), 'right'))
    assert _proven(fbt.solve(s, fbt.DEFAULT_WEIGHTS, other)) == 'ANT'
    against = fbt.solve(s, fbt.DEFAULT_WEIGHTS, other, fixed=fixed)
    assert against['hub']['tile'] == HUB and _proven(against) is None


def test_a_proven_hub_leaves_scoring_alone(words):
    s = _signals(words)
    result = fbt.solve(s, fbt.DEFAULT_WEIGHTS, _marks(s, ((HUB,) + PAIRS[0], 'right'), ((HUB,) + PAIRS[1], 'right')))
    assert result['hub']['label'] == 'yours' and _groups(result) == TRUTH


def test_the_page_moves_a_proven_hub_to_the_middle(client):
    html = client.get('/4x3').get_data(as_text=True)
    assert "setProvenHub(data.hub && isTile(data.hub.proven) ? data.hub.proven : '')" in html
    assert "boardEl.replaceChildren(...shownWords().map(" in html
    assert "is the hub: it's in all four groups." in html
    assert 'it moves to the middle, as it does in the game.' in html
    assert "prefers-reduced-motion" in html
