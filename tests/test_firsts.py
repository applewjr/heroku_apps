"""The Firsts solver (functions/firsts.py): board, tracing, pictures, scoring,
board rules, built phrases, and the shipped data file.

Every board, clue and answer here is made up. Firsts' own puzzles stay out
of the repo and the tests (the backtest reads them from a file elsewhere).
"""

import os
import sys

import numpy as np
import pytest

from functions import firsts

HERE = os.path.dirname(__file__)
DATA_FILE = os.path.join(HERE, os.pardir, 'datasets', 'firsts_data.npz')


def _build_script():
    sys.path.insert(0, os.path.join(HERE, os.pardir, 'scripts'))
    import build_firsts_data
    return build_firsts_data


def board(letters, greens, gone=(), **kw):
    """A board from {tile index: letter}; every other tile is a Q (no word
    here uses one)."""
    tiles = ['Q'] * 19
    for i, ch in letters.items():
        tiles[i] = ch
    return firsts.Board(tiles, [i in greens for i in range(19)], gone=[i in gone for i in range(19)], **kw)


# Tile indexes (hex_cells order: columns left to right, each top to bottom).
# Column 0 is 0-2, column 1 is 3-6, the middle column 7-11, then 12-15 and
# 16-18. In the middle column each tile touches the next one down.


@pytest.fixture(scope='module')
def tiny():
    """A small Data: a few answers and a few WordNet-like senses."""
    build = _build_script()
    synsets = {
        ('noun', '1'): (['cat'], [('@', '2', 'noun')], 'feline mammal kept as a pet; "the cat purred"'),
        ('noun', '2'): (['mammal'], [], 'warm-blooded animal'),
        ('noun', '3'): (['cot'], [], 'a small bed that folds up'),
        ('noun', '4'): (['dog'], [], 'a domesticated canine'),
        ('noun', '5'): (['sun'], [], 'the star at the centre of the solar system'),
        ('noun', '6'): (['sunday'], [], 'first day of the week; day of rest'),
        ('noun', '7'): (['suntan'], [], 'a browning of the skin from the sun; bronzed surface'),
        ('noun', '8'): (['sugar'], [], 'a sweet crystal'),
        ('noun', '9'): (['act'], [], 'a deed'),
        # The build drops words found in one definition only; this one
        # repeats the others' so they stay.
        ('noun', '99'): (['filler'], [], 'feline pet small bed folds domesticated canine star solar '
                                         'week rest browning skin bronzed surface sweet crystal deed'),
    }
    senses = {w: [(0, key)] for key, (words, _p, _g) in synsets.items() for w in words}
    arrays, _keys = build.build_senses(synsets, senses)
    answers = {'CAT': 4.0, 'COT': 4.5, 'DOG': 5.0, 'CATS': 3.5, 'SUNDAY': 4.5, 'SUNTAN': 3.0,
               'MONDAY': 4.5, 'NO': 6.0, 'SUGAR': 4.5, 'NOS': 2.0, 'SUG': 1.0}
    arrays.update(build.pack_lexicon(answers))
    arrays['version'] = np.array('test')
    return firsts.Data(arrays)


# --- text ------------------------------------------------------------------------

def test_stem_is_the_same_for_a_word_and_its_inflections():
    assert firsts.stem('HORSE') == firsts.stem('horses')
    assert firsts.stem('roamed') == firsts.stem('roam')
    assert firsts.stem('running') == firsts.stem('run')
    assert firsts.stem('stories') == firsts.stem('story')


def test_parse_spec_reads_word_lengths():
    assert firsts.parse_spec('5') == (5,)
    assert firsts.parse_spec('2,4') == (2, 4)
    assert firsts.parse_spec('3-2-5') == (3, 2, 5)
    assert firsts.parse_spec('(6 3)') == (6, 3)
    for bad in ('', 'abc', '16', '0', '1', '9,9'):
        with pytest.raises(firsts.BoardError):
            firsts.parse_spec(bad)


def test_cross_refs_are_standalone_clue_numbers():
    assert firsts.cross_refs('Planted, as 9') == [9]
    assert firsts.cross_refs('2 3 4 and 5') == [2, 3, 4, 5]
    for text in ('Most people have 24 of them', 'On the 8th of May', 'like a6 taking an L',
                 "e.g. 2019's remake", '88 days, on Mercury', 'Sold 1.5 million'):
        assert firsts.cross_refs(text) == []


def test_resolve_refs_swaps_known_answers_and_drops_unknown():
    text = firsts.resolve_refs('Planted, as 9, near 4', {9: 'GRASS'})
    assert 'grass' in text and '9' not in text and '4' not in text


# --- the board ------------------------------------------------------------------

def test_hex_cells_are_columns_of_3_4_5_4_3():
    cells = firsts.hex_cells()
    assert len(cells) == 19
    columns = [sum(1 for q, _r in cells if q == c) for c in range(-2, 3)]
    assert columns == [3, 4, 5, 4, 3]
    adj = firsts.neighbours(cells)
    assert len(adj[9]) == 6                      # the middle tile
    assert sorted(len(a) for a in adj)[:6] == [3] * 6   # the six corners
    assert 8 in adj[7] and 9 in adj[8]           # down the middle column


def test_board_rejects_bad_tiles():
    with pytest.raises(firsts.BoardError):
        firsts.Board(['A'] * 18, [False] * 18)
    with pytest.raises(firsts.BoardError):
        firsts.Board(['A'] * 18 + ['AB'], [False] * 19)
    with pytest.raises(firsts.BoardError):
        firsts.Board(['A'] * 18 + ['5'], [False] * 19)


def test_trace_starts_on_green_and_never_reuses_a_tile():
    b = board({7: 'C', 8: 'A', 9: 'T'}, greens={7})
    assert [p for p, _r in firsts.trace(b, 'CAT')] == [(7, 8, 9)]
    assert firsts.trace(b, 'CATA') == []         # would need the A twice
    assert firsts.trace(b, 'AT') == []           # A isn't green
    gone = board({7: 'C', 8: 'A', 9: 'T'}, greens={7}, gone={8})
    assert firsts.trace(gone, 'CAT') == []


def test_trace_through_a_picture_records_its_reading():
    b = board({7: '1', 8: 'D', 9: 'A', 10: 'Y'}, greens={7})
    paths = firsts.trace(b, 'SUNDAY')
    assert ((7, 8, 9, 10), {'1': 'SUN'}) in paths
    # A known reading is the only one allowed.
    known = board({7: '1', 8: 'D', 9: 'A', 10: 'Y'}, greens={7}, readings={'1': 'MON'})
    assert firsts.trace(known, 'SUNDAY') == []
    assert firsts.trace(known, 'MONDAY') == [((7, 8, 9, 10), {})]
    # valid limits what an unknown picture may stand for.
    assert firsts.trace(b, 'SUNDAY', valid={'MON'}.__contains__) == []


# --- solving ------------------------------------------------------------------------

def test_definitions_pick_the_answer_the_clue_describes(tiny):
    b = board({7: 'C', 8: 'A', 9: 'T', 12: 'O', 13: 'T', 0: 'D', 1: 'O', 2: 'G'}, greens={7, 0})
    out = firsts.solve(tiny, b, [('Feline pet', '3'), ('Small bed', '3')])
    assert out['clues'][0]['items'][0]['word'] == 'CAT'
    assert out['clues'][1]['items'][0]['word'] == 'COT'
    assert out['clues'][0]['items'][0]['path'][:2] == [7, 8]     # either T
    assert out['complete'] and out['version'] == firsts.SOLVER_VERSION
    assert out['start'] in (1, 2)


def test_known_answers_fill_their_clue_and_leave_the_others(tiny):
    b = board({7: 'C', 8: 'A', 9: 'T', 12: 'O', 13: 'T'}, greens={7})
    out = firsts.solve(tiny, b, [('Small bed', '3'), ('A pet', '3')], known={0: ('CAT', 'checked')})
    assert out['clues'][0]['label'] == 'checked' and out['clues'][0]['known'] == 'CAT'
    assert 'CAT' not in [it['word'] for it in out['clues'][1]['items']]


def test_lengths_count_letters_and_split_words(tiny):
    b = board({7: 'N', 8: 'O', 9: 'S', 10: 'U', 11: 'G', 15: 'A', 14: 'R'}, greens={7})
    out = firsts.solve(tiny, b, [('Sweet thing', '2,5')])
    item = out['clues'][0]['items'][0]
    # Not in the answer list: built from NO and SUGAR, traced in a row.
    assert item['word'] == 'NO SUGAR' and item['path'] == [7, 8, 9, 10, 11, 15, 14]


def test_a_picture_reading_comes_from_its_typed_name(tiny):
    b = board({7: '1', 8: 'D', 9: 'A', 10: 'Y', 12: 'T', 13: 'A', 14: 'N'}, greens={7},
              names={'1': 'sun'})
    out = firsts.solve(tiny, b, [('Day of rest', '6'), ('Bronzed surface', '6')])
    assert out['pictures']['1']['options'][0] == 'SUN'
    first = out['clues'][0]['items'][0]
    assert first['word'] == 'SUNDAY' and first['readings'] == {'1': 'SUN'}
    assert len(first['path']) == 4               # six letters, four tiles
    assert out['clues'][1]['items'][0]['word'] == 'SUNTAN'


def test_a_known_answer_fixes_a_pictures_reading(tiny):
    b = board({7: '1', 8: 'D', 9: 'A', 10: 'Y', 12: 'T', 13: 'A', 14: 'N'}, greens={7})
    out = firsts.solve(tiny, b, [('Day of rest', '6'), ('Bronzed surface', '6')],
                       known={0: ('SUNDAY', 'checked')})
    assert out['pictures']['1']['reading'] == 'SUN'
    assert out['clues'][1]['items'][0]['word'] == 'SUNTAN'


def test_two_pictures_stay_apart():
    b = board({7: '1', 8: '2'}, greens={7})
    assert b.pictures() == ['1', '2']
    paths = firsts.trace(b, 'SUNDAY', valid={'SUN', 'DAY'}.__contains__)
    assert paths == [((7, 8), {'1': 'SUN', '2': 'DAY'})]


# --- board rules ------------------------------------------------------------------

def _item(word, *paths):
    it = firsts.Item(word, 3.0, [(tuple(p), {}) for p in paths])
    return it


def test_a_tile_only_one_clue_can_reach_forces_that_clue():
    # Tile 9 only CAT reaches, so clue 0 is CAT, not CAB. Tile 3 both CAB
    # and DIB reach, so it forces nothing. Clue 1 is pulled two ways (tiles
    # 1-2 only DOG reaches, tile 4 only DIB), so it is left alone.
    live = {0, 1, 2, 3, 4, 7, 8, 9}
    b = board({7: 'C', 8: 'A', 9: 'T', 3: 'B', 0: 'D', 1: 'O', 2: 'G', 4: 'I'}, greens={7, 0},
              gone=set(range(19)) - live)
    lists = {0: [_item('CAB', [7, 8, 3]), _item('CAT', [7, 8, 9])],
             1: [_item('DOG', [0, 1, 2]), _item('DIB', [0, 4, 3])]}
    firsts.board_rules(b, lists, 0)
    by_word = {it.key: it.fits for items in lists.values() for it in items}
    assert by_word['CAT'] is True and by_word['CAB'] is False
    assert by_word['DOG'] is None and by_word['DIB'] is None


def test_the_last_clue_is_pinned_to_the_tiles_left():
    b = board({7: 'C', 8: 'A', 9: 'T', 10: 'S'}, greens={7}, gone=set(range(19)) - {7, 8, 9, 10},
              synced=True)
    lists = {0: [_item('CAT', [7, 8, 9]), _item('CATS', [7, 8, 9, 10])]}
    firsts.board_rules(b, lists, 0)
    assert [it.pinned for it in lists[0]] == [False, True]


def test_tiles_a_solved_answer_used_dont_force_when_the_board_isnt_kept_in_step():
    # The player solved an answer over tiles 0-2 but didn't mark them gone.
    b = board({7: 'C', 8: 'A', 9: 'T', 0: 'D', 1: 'O', 2: 'G'}, greens={7},
              gone=set(range(19)) - {0, 1, 2, 7, 8, 9})
    lists = {1: [_item('CAT', [7, 8, 9])]}
    solved_cover = (1 << 0) | (1 << 1) | (1 << 2)
    firsts.board_rules(b, lists, solved_cover)
    assert lists[1][0].pinned is True            # tiles 0-2 aren't required of it


def test_solve_marks_the_last_clue_on_a_board_kept_in_step(tiny):
    # Clue 1 solved (DOG) and its tiles gone; what's left spells CATS.
    b = board({7: 'C', 8: 'A', 9: 'T', 10: 'S'}, greens={7},
              gone=set(range(19)) - {7, 8, 9, 10}, synced=True)
    out = firsts.solve(tiny, b, [('Pets', '4'), ('Pet', '3')], known={1: ('DOG', 'yours')}, solved={1})
    assert out['clues'][0]['items'][0]['word'] == 'CATS'
    assert out['clues'][0]['items'][0]['pinned'] is True
    assert out['clues'][1]['solved'] is True and out['clues'][1]['items'] == []


# --- data ------------------------------------------------------------------------

def test_popularity_letters_round_trip():
    for z in (0.0, 1.5, 3.0, 4.2, 7.5):
        assert abs(firsts.pop_zipf(firsts.pop_char(z)) - z) <= firsts.POP_STEP / 2 + 1e-9


def test_token_ids_use_17_bits(tiny):
    for s in range(len(tiny.syn_ptr) - 1):
        for t in tiny.synset_tokens(s):
            assert 0 <= t < len(tiny.tok_idf)
    assert 'felin' in {tiny.tokens[t].decode() for t in tiny.bag('CAT')}
    # The parent's lemma is in the sense's words, the quoted example isn't.
    assert 'mammal' in {tiny.tokens[t].decode() for t in tiny.bag('CAT')}
    assert tiny.token('purr') < 0


@pytest.mark.skipif(not os.path.exists(DATA_FILE), reason='firsts_data.npz not built yet')
def test_shipped_data_passes_its_canaries_and_budget():
    build = _build_script()
    shipped = firsts.Data.from_file(DATA_FILE)
    assert build.canaries(shipped) == []
    assert shipped.nbytes <= build.MAX_BYTES
    assert np.all(np.diff(shipped.lem_ptr.astype(np.int64)) >= 0)
    assert np.all(np.diff(shipped.syn_ptr.astype(np.int64)) >= 0)
