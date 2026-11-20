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
    # A search cut short may not have met the best rival: nothing is sure.
    assert 'sure' not in {b['confidence'] for b in r['boxes']}


def test_extra_pairs_join_the_data(data):
    snow, room = data.id('snow'), data.id('room')
    r = cz.solve(data, board(blank_rows(['SNOW', B, '']), [(0, 1)]), extra={(snow, room): 3.0})
    assert words(r) == ['ROOM']


def test_an_arrow_from_a_tile_next_to_two_boxes_may_be_one_the_game_lacks(data):
    # HOT [DOG] above HOUSE, GAS [?] below it. DOG HOUSE is real; HOUSE down
    # into the second box is an arrow the page drew by itself. HOUSE has
    # arrows to two boxes, so HOUSE + MASK missing costs nothing and GAS MASK
    # wins. Were it a sure arrow, HOUSE BOAT (missing only GAS) would.
    rows = blank_rows(['HOT', B, ''], ['', 'HOUSE', ''], ['GAS', B, ''])
    r = cz.solve(data, board(rows, [(0, 1), (1, 4), (4, 7), (6, 7)]))
    assert words(r) == ['DOG', 'MASK']
    arcs = r['boxes'][1]['arcs']
    assert [(a['given'], a['known'], a['doubtful']) for a in arcs] == [('HOUSE', False, True), ('GAS', True, False)]
    assert [a['doubtful'] for a in r['boxes'][0]['arcs']] == [False, True]
    assert cz.doubtful_tiles(board(rows, [(0, 1), (1, 4), (4, 7), (6, 7)])) == {4}


def test_a_kept_word_is_a_tile_to_the_boxes_next_to_it(data):
    # The second box's only tile is HOT, whose other arrow goes to the third
    # box (HOT DOG, DOG HOUSE). With FIRE kept above it, it takes a word that
    # joins FIRE, which HOT alone would never have offered.
    rows = blank_rows(['', B, ''], ['HOT', B, ''], [B, '', ''], ['HOUSE', '', ''])
    b = board(rows, [(1, 4), (3, 4), (3, 6), (6, 9)])
    r = cz.solve(data, b, locked={1: 'fire'})
    assert words(r)[0] == 'FIRE' and words(r)[2] == 'DOG'
    assert words(r)[1] in {'MAN', 'STATION', 'PLACE', 'TRUCK'}
    assert r['boxes'][1]['arcs'][0] == {'other': 1, 'first': False, 'box': 1, 'known': True}


def test_first_letters_narrow_a_box(data):
    # SNOW ___ alone fits BALL, MAN, FLAKE and STORM; a Bolt's S leaves STORM.
    b = board(blank_rows(['SNOW', B, '']), [(0, 1)])
    assert words(cz.solve(data, b, starts={1: 's'})) == ['STORM']
    assert words(cz.solve(data, b, starts={1: 'FL'})) == ['FLAKE']
    # Letters nothing fits leave the box empty rather than break them.
    assert words(cz.solve(data, b, starts={1: 'Q'})) == [None]
    # A kept word wins over letters; letters that aren't letters, or for a
    # cell that isn't a box, are ignored.
    assert words(cz.solve(data, b, locked={1: 'ball'}, starts={1: 'S'})) == ['BALL']
    assert words(cz.solve(data, b, starts={1: 'S1', 0: 'S'}))[0] in {'BALL', 'MAN', 'FLAKE', 'STORM'}


def test_shapes():
    assert cz.shape_name(4, 4) == '4x4' and cz.parse_shape('4x4') == (4, 4) and cz.parse_shape('5x3') == (5, 3)
    assert cz.parse_shape('6x6') is None and cz.parse_shape('5') is None and cz.parse_shape(None) is None
    # A 4 by 4 board: arrows across a row end at its fourth column.
    cells = ['', '', 'SNOW', ''] + ['', '', B, ''] + [''] * 8
    b = cz.parse_board(cells, [[2, 6]], 4, 4)
    assert b.boxes == [6] and (b.rows, b.cols) == (4, 4)
    with pytest.raises(cz.BoardError):
        cz.parse_board(cells, [[3, 4]], 4, 4)
    with pytest.raises(cz.BoardError):
        cz.parse_board(cells[:15], [[2, 6]], 4, 4)


def test_an_added_pair_can_name_a_whole_tile_the_data_lacks(data):
    # SHOW AND (the start of SHOW AND TELL) isn't in the tiny data, and nor is
    # AND, so its box has nothing to go on until SHOW AND + ROOM is added.
    b = board(blank_rows(['SHOW AND', B, '']), [(0, 1)])
    r = cz.solve(data, b)
    assert words(r) == [None] and r['unknown'] == ['SHOW AND']
    extra = {('show and', data.id('room')): 3.0}
    r = cz.solve(data, b, extra=extra)
    assert words(r) == ['ROOM'] and r['unknown'] == [] and r['boxes'][0]['arcs'][0]['known']
    # The tile is known by its whole text, and is never an answer.
    pairs = cz.lookup(data, extra)
    tile, factor = pairs.given_id('Show  And', True)
    assert tile >= len(data.vocab) and factor == 1.0 and not pairs.may_answer(tile)
    assert pairs.weight(tile, data.id('room')) == 3.0 and pairs.weight(tile, data.id('ball')) is None
    # A multi-word tile keeps what its touching word gives: HOT DOG still
    # finds DOG HOUSE with HOT DOG + PLANT added.
    hot = board(blank_rows(['HOT DOG', B, 'BOAT']), [(0, 1), (1, 2)])
    assert words(cz.solve(data, hot, extra={('hot dog', data.id('plant')): 3.0})) == ['HOUSE']
    # What an added pair calls a tile: the whole tile, never one of its words.
    assert cz.tile_word(data, 'SHOW  AND') == 'show and' and cz.tile_word(data, 'ICE CREAM') == 'ice cream'
    assert cz.tile_word(data, 'HOT DOG') == 'hot dog' and cz.tile_word(data, 'SNOW') == 'snow'


def test_a_two_word_unit_in_an_added_pair_never_becomes_an_answer(data):
    # [?] TRUCK: FIRE TRUCK is known, and so is the unit ICE CREAM (+ TRUCK).
    # An added ICE CREAM + TRUCK (say from an ICE CREAM tile) outweighs FIRE,
    # but a box takes one word, so the box stays FIRE.
    b = board(blank_rows([B, 'TRUCK', '']), [(0, 1)])
    extra = {(data.id('ice cream'), data.id('truck')): 3.0}
    assert words(cz.solve(data, b, extra=extra)) == ['FIRE']
    assert not cz.lookup(data, extra).may_answer(data.id('ice cream'))


def test_a_word_in_an_added_pair_may_be_an_answer():
    # GNU is in the data but too rare to be an answer, until James adds a
    # pair with it: his answers can be rarer words than the solver guesses.
    small = cz.Data(**cz.pack({('snow', 'gnu'): 2.5, ('gnu', 'herd'): 2.5},
                              zipf={'snow': 4.0, 'gnu': 2.0, 'herd': 3.5}, answer={'snow', 'herd'}))
    b = board(blank_rows(['SNOW', B, 'HERD']), [(0, 1), (1, 2)])
    assert words(cz.solve(small, b)) == [None]
    extra = {(small.id('snow'), small.id('gnu')): 3.0}
    assert words(cz.solve(small, b, extra=extra)) == ['GNU']


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


# --- the page (routes/circuits.py, templates/circuits.html) ---------------------

@pytest.fixture
def tiny(monkeypatch, data):
    """The tiny word data in place of the shipped file. conftest.py keeps
    every database cursor of routes/circuits.py failing."""
    import routes.circuits as rc
    monkeypatch.setattr(rc, '_static', {'data': data, 'failed_at': 0.0, 'version': None})
    return data


def _faq(html):
    import json
    import re
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    return next(d for d in map(json.loads, blocks) if d.get('@type') == 'FAQPage')


def test_page_shows_the_loaded_marker_and_says_unofficial(client, tiny):
    html = client.get('/circuits').get_data(as_text=True)
    # The hourly web probe expects this marker (datasets/health_web.yaml).
    assert 'id="cc-data-ok"' in html
    assert 'Unofficial helper. Not affiliated with or endorsed by Puzzmo.' in html


def test_page_says_when_the_data_is_unavailable(client, monkeypatch):
    import time
    import routes.circuits as rc
    monkeypatch.setattr(rc, '_static', {'data': None, 'failed_at': time.time(), 'version': None})
    html = client.get('/circuits').get_data(as_text=True)
    assert 'id="cc-data-ok"' not in html and 'The solver is unavailable right now' in html
    resp = client.post('/circuits', json={'cells': ['SNOW', B] + [''] * 13, 'arrows': [[0, 1]]})
    assert resp.status_code == 503


def test_faq_structured_data_parses_and_matches_the_visible_faq(client, tiny):
    import re
    html = client.get('/circuits').get_data(as_text=True)
    faq = _faq(html)
    visible = re.sub(r'<[^>]+>', '', re.sub(r'<script.*?</script>', '', html, flags=re.S))
    visible = ' '.join(visible.split())
    for q in faq['mainEntity']:
        assert q['name'] in visible
        assert q['acceptedAnswer']['text'] in visible


def test_page_credits_every_source(client, tiny):
    # The credits the sources' licences ask for (datasets/CIRCUITS_DATA.md).
    html = client.get('/circuits').get_data(as_text=True)
    for required in (
        'Wikipedia contributors', 'Wiktionary contributors', 'creativecommons.org/licenses/by-sa/4.0',
        'WordNet 3.0 Copyright 2006 by Princeton University', 'Wikidata',
        'creativecommons.org/publicdomain/zero/1.0', 'ENABLE', 'wordfreq', 'Robyn Speer', 'SUBTLEX',
        'Brysbaert', 'freely available', 'Google Books Ngrams', 'ConceptNet Numberbatch',
        'Luminoso Technologies', 'List of Dirty, Naughty, Obscene, and Otherwise Bad Words',
        'creativecommons.org/licenses/by/4.0',
    ):
        assert required in html, required


def test_the_page_wears_a_beta_tag_until_it_comes_off(client, tiny, monkeypatch):
    import re
    import helpers
    html = client.get('/circuits').get_data(as_text=True)
    assert re.search(r'<h1 class="cc-h1">Circuits Answers &amp; Hints <span class="jj-beta-tag">Beta</span></h1>', html)
    # Visual only: never in the title or the description.
    assert 'Beta' not in re.search(r'<title>(.*?)</title>', html, re.S).group(1)
    home = client.get('/').get_data(as_text=True)
    assert re.search(r'Circuits Solver<span class="beta-tag">Beta</span>', home)
    strip = client.get('/smush').get_data(as_text=True).split('More daily puzzle solvers')[1]
    assert re.search(r'href="/circuits".*?jj-more-beta">Beta</span>', strip, re.S)
    assert 'href="/circuits">Circuits Solver<span class="jj-beta-tag">Beta</span>' in home
    # One switch takes it off everywhere.
    monkeypatch.setattr(helpers, 'BETA_PAGES', frozenset())
    for path in ('/circuits', '/', '/smush'):
        assert '>Beta</span>' not in client.get(path).get_data(as_text=True), path


def test_the_public_page_never_names_the_admin_pages(client, tiny):
    html = client.get('/circuits').get_data(as_text=True)
    assert 'circuits_admin' not in html and 'circuitsApproveState' not in html


def test_players_are_never_told_how_boards_are_chosen(client, tiny):
    lowered = client.get('/circuits').get_data(as_text=True).lower()
    for hint in ('different players', 'another player', 'players agreed', 'for everyone',
                 'two players', '2 players', 'three players', '3 players', 'majority'):
        assert hint not in lowered, f'page hints at the threshold: {hint!r}'


def test_a_solve_through_the_route(client, tiny):
    cells = ['SNOW', B, 'ROOM', '', B, '', 'BATTLE', B, ''] + [''] * 6
    arrows = [[0, 1], [1, 2], [1, 4], [4, 7], [6, 7]]
    reply = client.post('/circuits', json={'cells': cells, 'arrows': arrows}).get_json()
    assert [b['word'] for b in reply['boxes']] == ['BALL', 'GAME', 'PLAN']
    assert reply['checked'] is False and reply['known_arrows'] is None
    assert 'solutions' not in reply
    # A kept word holds, and the rest is solved around it.
    reply = client.post('/circuits', json={'cells': cells, 'arrows': arrows, 'locked': {'4': 'show'}}).get_json()
    assert reply['boxes'][1]['word'] == 'SHOW' and reply['boxes'][1]['confidence'] == 'locked'
    # First letters from a Bolt narrow a box.
    reply = client.post('/circuits', json={'cells': ['SNOW', B] + [''] * 13, 'arrows': [[0, 1]],
                                           'starts': {'1': 's'}}).get_json()
    assert [b['word'] for b in reply['boxes']] == ['STORM']


def test_a_four_by_four_board_through_the_route(client, tiny):
    # SNOW [BALL] ROOM along the second row of a 4 by 4 grid, GAME under BALL.
    cells = [''] * 4 + ['SNOW', B, 'ROOM', ''] + ['', B, '', ''] + ['', 'PLAN', '', '']
    arrows = [[4, 5], [5, 6], [5, 9], [9, 13]]
    reply = client.post('/circuits', json={'rows': 4, 'cols': 4, 'cells': cells, 'arrows': arrows}).get_json()
    assert [b['word'] for b in reply['boxes']] == ['BALL', 'GAME']
    # The same cells sent without a shape are a 5 by 3 board: 15 cells, not 16.
    assert client.post('/circuits', json={'cells': cells, 'arrows': arrows}).status_code == 400


@pytest.mark.parametrize('body', [
    {'cells': ['SNOW'] * 15, 'arrows': []},                       # no box
    {'cells': ['SNOW', B] + [''] * 13, 'arrows': [[1, 0]]},       # an arrow pointing left
    {'cells': ['SN0W', B] + [''] * 13, 'arrows': [[0, 1]]},       # not letters
    {'cells': ['SNOW', B] + [''] * 13, 'arrows': [[0, 1]], 'locked': {'0': 'ball'}},   # not a box
    {'cells': ['SNOW', B] + [''] * 13, 'arrows': [[0, 1]], 'locked': {'1': 'two words'}},
    {'cells': ['SNOW', B] + [''] * 13, 'arrows': [[0, 1]], 'locked': {'¹': 'ball'}},   # a digit int() refuses
    {'cells': ['SNOW', B] + [''] * 13, 'arrows': [[0, 1]], 'locked': {'١': 'ball'}},   # not an ASCII digit
    {'cells': ['SNOW', B] + [''] * 13, 'arrows': [[0, 1]], 'starts': {'1': 's1'}},     # letters only
    {'cells': ['SNOW', B] + [''] * 13, 'arrows': [[0, 1]], 'starts': {'0': 's'}},      # a box's
    {'cells': ['SNOW', B] + [''] * 34, 'arrows': [[0, 1]], 'rows': 6, 'cols': 6},      # not a shape the page offers
    {'cells': ['SNOW', B] + [''] * 13, 'arrows': [[0, 1]], 'rows': '5', 'cols': 3},    # not a number
    {'cells': ['SNOW', B] + [''] * 13, 'arrows': [[0, 1]], 'rows': 5.0, 'cols': 3.0},
    {'cells': ['SNOW', B] + [''] * 14, 'arrows': [[0, 1]], 'rows': 4, 'cols': True},
])
def test_bad_solves_are_400s(client, tiny, body):
    assert client.post('/circuits', json=body).status_code == 400
