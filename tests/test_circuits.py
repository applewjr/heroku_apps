"""The Circuits solver engine (functions/circuits.py) on made-up boards.

Boards here are invented from everyday compounds, never taken from a real
puzzle, and the word data is a tiny hand-made table packed with pack().
"""

import os

import numpy as np
import pytest

from functions import circuits as cz

B = cz.BOX

PAIRS = {
    ('snow', 'ball'): 2.5, ('snow', 'man'): 2.5, ('snow', 'flake'): 2.5, ('snow', 'storm'): 2.0,
    ('ball', 'room'): 2.5, ('ball', 'game'): 2.0, ('ball', 'park'): 2.0, ('ball', 'boy'): 1.5,
    ('foot', 'ball'): 2.5, ('basket', 'ball'): 2.5, ('man', 'hole'): 2.0, ('fire', 'man'): 2.5,
    ('game', 'plan'): 2.0, ('game', 'show'): 2.0, ('battle', 'plan'): 2.0, ('battle', 'ship'): 2.5,
    ('battle', 'field'): 2.5, ('show', 'room'): 2.0, ('store', 'room'): 2.0, ('park', 'way'): 1.5,
    ('fire', 'station'): 2.5, ('police', 'station'): 2.5, ('station', 'wagon'): 2.5,
    ('fire', 'place'): 2.5, ('fire', 'truck'): 2.5, ('ice', 'cream'): 2.6, ('ice cream', 'truck'): 2.0,
    ('dog', 'house'): 2.5, ('hot', 'dog'): 2.5, ('tree', 'house'): 2.5, ('green', 'house'): 2.5,
    ('house', 'boat'): 2.5, ('boat', 'house'): 2.5, ('light', 'house'): 2.5, ('house', 'plant'): 2.0,
    ('sun', 'light'): 2.5, ('moon', 'light'): 2.5, ('light', 'bulb'): 2.5, ('flash', 'light'): 2.5,
    ('moon', 'shine'): 2.0, ('gas', 'mask'): 2.0,
}
ZIPF = {w: 4.0 for ab in PAIRS for w in ab if ' ' not in w}


@pytest.fixture(scope='module')
def data():
    return cz.Data(**cz.pack(PAIRS, zipf=ZIPF))


def board(rows, arrows):
    """A board from 5 rows of 3 cells and arrows as (tail, head) cells."""
    cells = [c for row in rows for c in row]
    return cz.parse_board(cells, [list(a) for a in arrows])


def blank_rows(*rows):
    rows = list(rows)
    return rows + [['', '', '']] * (cz.ROWS - len(rows))


def words(result):
    return [b['word'] for b in result['boxes']]


# --- the data -------------------------------------------------------------------

def test_pack_and_lookups(data):
    snow, ball = data.id('snow'), data.id('BALL')
    assert snow >= 0 and ball >= 0 and data.id('nope') == -1
    assert data.word(snow) == 'snow'
    assert data.pair(snow, ball) == pytest.approx(2.5, abs=1 / cz.W_SCALE)
    assert data.pair(ball, snow) is None
    after = {data.word(i) for i in data.follows(snow)[0]}
    assert after == {'ball', 'man', 'flake', 'storm'}
    before = {data.word(i) for i in data.precedes(ball)[0]}
    assert before == {'snow', 'foot', 'basket'}
    # Both orders of a two-way compound are separate pairs.
    house, boat = data.id('house'), data.id('boat')
    assert data.pair(house, boat) and data.pair(boat, house)


def test_two_word_units_are_known_but_never_answers(data):
    unit = data.id('ice  cream')
    assert unit >= 0 and not data.can_answer(unit)
    assert data.can_answer(data.id('truck'))


# --- the board ------------------------------------------------------------------

@pytest.mark.parametrize('cells, arrows, message', [
    ([''] * 14, [], 'cells'),
    ([''] * 15, [], 'box'),
    (['SNOW', B] + [''] * 13, [[1, 0]], 'right or down'),
    (['SNOW', '', B] + [''] * 12, [[0, 2]], 'right or down'),
    (['SNOW', B] + [''] * 13, [[0, 4]], 'right or down'),
    (['SNOW', B, ''] + [''] * 12, [[1, 2]], 'two tiles'),
    (['SNOW', 'BALL', B] + [''] * 12, [[0, 1]], 'touches a box'),
    (['SNOW2', B] + [''] * 13, [[0, 1]], 'letters'),
    (['SNOW', B] + [''] * 13, [[0, True]], 'pair'),
    (['SNOW', B, '', '', '', ''] + [''] * 9, [[2, 3]], 'right or down'),
])
def test_bad_boards_are_refused(cells, arrows, message):
    with pytest.raises(cz.BoardError, match=message):
        cz.parse_board(cells, arrows)


def test_cells_are_cleaned_and_boxes_numbered_in_reading_order():
    b = cz.parse_board(['  snow ', B, 'room', B, B, ''] + [''] * 9, [[0, 1], [1, 2], [3, 4], [1, 4], [1, 2]])
    assert b.cells[:3] == ['SNOW', B, 'ROOM']
    assert b.boxes == [1, 3, 4]
    assert [b.number(c) for c in b.boxes] == [1, 2, 3]
    assert b.arrows == [(0, 1), (1, 2), (1, 4), (3, 4)]
    assert b.key().startswith('SNOW|?|ROOM|')


# --- solving --------------------------------------------------------------------

def test_a_box_between_two_tiles(data):
    r = cz.solve(data, board(blank_rows(['SNOW', B, 'ROOM']), [(0, 1), (1, 2)]))
    assert words(r) == ['BALL']
    box = r['boxes'][0]
    assert box['confidence'] == 'sure' and box['letters'] == 4 and box['n'] == 1
    assert [a['known'] for a in box['arcs']] == [True, True]
    assert r['start'] == 1 and not r['partial'] and r['unknown'] == []


def test_a_box_no_tile_touches_gets_candidates_through_its_neighbours(data):
    # SNOW BALL ROOM across the top, BALL GAME and GAME PLAN down the middle,
    # BATTLE PLAN into the bottom box.
    rows = blank_rows(['SNOW', B, 'ROOM'], ['', B, ''], ['BATTLE', B, ''])
    r = cz.solve(data, board(rows, [(0, 1), (1, 2), (1, 4), (4, 7), (6, 7)]))
    assert words(r) == ['BALL', 'GAME', 'PLAN']
    assert all(b['confidence'] in ('sure', 'likely') for b in r['boxes'])


def test_one_missing_compound_is_tolerated(data):
    # FIRE STATION and STATION WAGON are known. GAS is a known word, but this
    # data lacks STATION + GAS: one missing pair, and STATION still wins.
    rows = blank_rows(['FIRE', B, 'WAGON'], ['', 'GAS', ''])
    r = cz.solve(data, board(rows, [(0, 1), (1, 2), (1, 4)]))
    assert words(r) == ['STATION'] and r['unknown'] == []
    assert [a['known'] for a in r['boxes'][0]['arcs']] == [True, True, False]
    assert not any(a.get('unknown_tile') for a in r['boxes'][0]['arcs'])


def test_locked_words_hold_and_unknown_ones_do_not_break_the_solve(data):
    rows = blank_rows(['SNOW', B, 'ROOM'], ['', B, ''], ['BATTLE', B, ''])
    arrows = [(0, 1), (1, 2), (1, 4), (4, 7), (6, 7)]
    r = cz.solve(data, board(rows, arrows), locked={4: 'show'})
    assert words(r) == ['BALL', 'SHOW', 'PLAN'] or words(r)[1] == 'SHOW'
    assert r['boxes'][1]['locked'] and r['boxes'][1]['confidence'] == 'locked'
    r = cz.solve(data, board(rows, arrows), locked={4: 'zzyzx'})
    assert r['boxes'][1]['word'] == 'ZZYZX' and r['boxes'][1]['locked']
    # Arrows from a word the data doesn't know count as unknown, not as misses.
    assert words(r)[0] == 'BALL'


def test_a_word_is_not_used_twice_when_another_fits(data):
    # SUN ___ BULB takes LIGHT. MOON ___ alone would take LIGHT too (2.5 over
    # SHINE's 2.0), but a second LIGHT costs REPEAT, so it gets SHINE.
    rows = blank_rows(['SUN', '', 'MOON'], [B, '', B], ['BULB', '', ''])
    r = cz.solve(data, board(rows, [(0, 3), (3, 6), (2, 5)]))
    assert words(r) == ['LIGHT', 'SHINE']
    alone = cz.solve(data, board(blank_rows(['', '', 'MOON'], ['', '', B]), [(2, 5)]))
    assert words(alone) == ['LIGHT']


def test_multi_word_tiles(data):
    # ICE CREAM is a known unit; HOT DOG is not, so its last word joins.
    r = cz.solve(data, board(blank_rows(['ICE CREAM', B, '']), [(0, 1)]))
    assert words(r) == ['TRUCK']
    r = cz.solve(data, board(blank_rows(['HOT DOG', B, 'BOAT']), [(0, 1), (1, 2)]))
    assert words(r) == ['HOUSE']


def test_unknown_tiles_are_reported_and_ignored(data):
    r = cz.solve(data, board(blank_rows(['SNOW', B, 'QWERTY']), [(0, 1), (1, 2)]))
    assert r['unknown'] == ['QWERTY']
    assert words(r)[0] in {'BALL', 'MAN', 'FLAKE', 'STORM'}
    assert r['boxes'][0]['arcs'][1]['unknown_tile']


def test_an_ambiguous_box_is_a_guess_with_alternatives(data):
    r = cz.solve(data, board(blank_rows(['SNOW', B, '']), [(0, 1)]))
    box = r['boxes'][0]
    assert box['confidence'] in ('guess', 'likely')
    assert {a['word'] for a in box['alternatives']} <= {'BALL', 'MAN', 'FLAKE', 'STORM'}
    assert len(box['alternatives']) >= 2


def test_a_box_nothing_fits(data):
    r = cz.solve(data, board(blank_rows(['QWERTY', B, '']), [(0, 1)]))
    assert words(r) == [None] and r['boxes'][0]['confidence'] == 'none' and r['start'] is None


def test_the_time_budget_returns_what_it_has(data, monkeypatch):
    monkeypatch.setattr(cz, 'NODE_CHECK', 1)
    rows = blank_rows(['SNOW', B, 'ROOM'], ['', B, ''], ['BATTLE', B, ''])
    r = cz.solve(data, board(rows, [(0, 1), (1, 2), (1, 4), (4, 7), (6, 7)]), budget_s=0)
    assert r['partial']
    assert len(r['boxes']) == 3


def test_extra_pairs_join_the_data(data):
    snow, room = data.id('snow'), data.id('room')
    r = cz.solve(data, board(blank_rows(['SNOW', B, '']), [(0, 1)]), extra={(snow, room): 3.0})
    assert words(r) == ['ROOM']


# --- the shipped file -----------------------------------------------------------

DATA_FILE = os.path.join(os.path.dirname(__file__), os.pardir, 'datasets', 'circuits_data.npz')


def _build_script():
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, 'scripts'))
    import build_circuits_data
    return build_circuits_data


def test_dump_listing_keeps_only_the_newest_copy_of_each_part(tmp_path):
    build = _build_script()
    name = 'enwiki-latest-pages-articles-multistream{}.xml-p{}p{}.bz2'.format
    (tmp_path / 'wiki').mkdir()
    listing = tmp_path / 'wiki' / 'files.txt'
    listing.write_text('\n'.join([
        f'{name(1, 1, 100)} 5000000',
        f'{name(2, 101, 180)} 83',  # a dead link
        f'{name(2, 101, 190)} 4000000',  # an older dump's last part
        f'{name(2, 101, 200)} 4500000',
    ]) + '\n', encoding='utf-8')
    assert build.dump_parts(str(tmp_path)) == [(name(1, 1, 100), 5000000), (name(2, 101, 200), 4500000)]
    listing.write_text(f'{name(1, 1, 100)} 5000000\n{name(2, 90, 200)} 4500000\n', encoding='utf-8')
    with pytest.raises(SystemExit):
        build.dump_parts(str(tmp_path))


@pytest.mark.skipif(not os.path.exists(DATA_FILE), reason='circuits_data.npz not built yet')
def test_shipped_data_passes_its_canaries_and_budget():
    build = _build_script()
    shipped = cz.Data.from_file(DATA_FILE)
    assert build.canaries(shipped) == []
    nbytes = sum(getattr(shipped, name).nbytes for name in cz.FIELDS)
    assert nbytes <= build.MAX_BYTES
    assert len(shipped.succ_ids) <= build.MAX_PAIRS
    assert len(shipped.succ_ids) == len(shipped.pred_ids)
    assert np.all(np.diff(shipped.succ_ptr) >= 0)
