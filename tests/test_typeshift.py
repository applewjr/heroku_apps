"""Typeshift's solver engine and its page (functions/typeshift.py,
routes/typeshift.py, templates/typeshift.html).

Every board here is made up from dictionary words, never a real puzzle's.
conftest.py keeps every database cursor of routes/typeshift.py failing and
puts the plain word list's index in place.
"""

import itertools
import json
import random
import re

import pytest

from functions import typeshift as ts

# A tiny word list, with how common each word is.
ZIPF = {'bat': 3.9, 'bet': 3.8, 'bit': 4.4, 'cat': 4.5, 'cot': 2.5, 'cut': 4.6, 'hat': 4.2,
        'hit': 4.7, 'hot': 4.6, 'hut': 3.0, 'rat': 3.8, 'rot': 3.1}
# B C H over A I O over T: bat, bit, cat, cot, hat, hit and hot fit; bet,
# cut, hut, rat and rot don't.
BOARD = ('bch', 'aio', 't')
FITS = ['bat', 'bit', 'cat', 'cot', 'hat', 'hit', 'hot']


@pytest.fixture
def tiny():
    return ts.Index.build(ZIPF)


def _solve(index, board=BOARD, **kw):
    kw.setdefault('popularity', ZIPF)
    return ts.solve(index, board, **kw)


# --- parsing ---------------------------------------------------------------------

def test_a_board_is_its_columns_in_lower_case():
    assert ts.parse_board(['BCH', ' aio ', 'T']) == BOARD


@pytest.mark.parametrize('columns', [
    None, 'bch aio t', ['bch', 'aio'],                      # not a list; too few columns
    ['ab'] * (ts.MAX_COLS + 1),                              # too many
    ['bch', '', 't'], ['bch', 'a1o', 't'], ['bch', 'aio', 'é'],   # empty, a digit, not a-z
    ['b' * (ts.MAX_LETTERS + 1), 'aio', 't'], ['bch', 7, 't'],
])
def test_bad_boards_are_refused(columns):
    with pytest.raises(ts.BoardError):
        ts.parse_board(columns)


def test_solid_tiles_are_column_and_position_pairs():
    assert ts.parse_solid([[0, 2], [1, 0]], BOARD) == {(0, 2), (1, 0)}
    assert ts.parse_solid(None, BOARD) == frozenset()
    for bad in ([[3, 0]], [[0, 3]], [[0]], [['0', 1]], [[True, 0]], 'x', [[0, 0]] * 8):
        with pytest.raises(ts.BoardError):
            ts.parse_solid(bad, BOARD)


def test_fits_board():
    assert ts.fits_board('hot', BOARD)
    assert not ts.fits_board('hut', BOARD) and not ts.fits_board('ho', BOARD) and not ts.fits_board(None, BOARD)


# --- the index -------------------------------------------------------------------

def test_the_index_keeps_only_words_a_board_can_hold():
    index = ts.Index.build(['hot', 'Hot', 'hot', 'to', 'it', 'x-ray', 'naïve', float('nan'), None,
                            'abcdefghijk', 'abcdefghij'])
    assert index.size == 2
    assert 'hot' in index and 'abcdefghij' in index
    assert 'Hot' not in index and 'to' not in index and 'naïve' not in index and 5 not in index


def test_fits_is_a_scan_of_the_list(typeshift_index):
    from data import words
    rng = random.Random(4)
    for _ in range(20):
        board = tuple(''.join(rng.sample('abcdefghijklmnopqrstuvwxyz', 5)) for _ in range(4))
        scan = sorted(w for w in words if isinstance(w, str) and ts.fits_board(w, board))
        assert typeshift_index.fits(board) == scan


def test_fits_on_the_tiny_list(tiny):
    assert tiny.fits(BOARD) == FITS
    assert tiny.fits(('zq', 'zq', 'zq')) == []
    assert tiny.fits(('ab', 'cd', 'ef', 'gh', 'ij', 'kl', 'mn', 'op', 'qr', 'st')) == []


# --- solving ---------------------------------------------------------------------

def test_the_plan_is_the_fewest_and_most_familiar_words(tiny):
    result = _solve(tiny)
    # Three words at least (three letters in the first column), and of the
    # three-word plans, BIT CAT HOT has the most familiar least familiar word.
    assert result['plan'] == ['hot', 'cat', 'bit'] and result['exact'] is True
    assert [w['word'] for w in result['words']] == ['hit', 'hot', 'cat', 'bit', 'hat', 'bat', 'cot']
    assert result['left'] == 7 and result['dead'] == []


def test_tile_counts_and_hints_hardest_first(tiny):
    result = _solve(tiny)
    counts = [[t['count'] for t in col] for col in result['tiles']]
    assert counts == [[2, 2, 3], [3, 2, 2], [7]]
    hints = [(h['col'], h['pos'], h['word']) for h in result['hints']]
    assert hints == [(0, 0, 'bit'), (0, 1, 'cat'), (1, 1, 'bit'), (1, 2, 'hot'),
                     (0, 2, 'hot'), (1, 0, 'cat'), (2, 0, 'hot')]


def test_gain_counts_the_letters_left_a_word_would_use(tiny):
    result = _solve(tiny, solid={(0, 0), (2, 0)})
    gain = {w['word']: w['gain'] for w in result['words']}
    assert gain['bat'] == 1 and gain['cat'] == 2 and gain['hot'] == 2


def test_solid_letters_leave_the_plan(tiny):
    result = _solve(tiny, solid={(0, 0), (1, 1)})            # B and I are solid
    assert result['plan'] == ['hot', 'cat'] and result['left'] == 5
    assert result['tiles'][0][0]['solid'] is True and result['tiles'][0][1]['solid'] is False
    assert all((h['col'], h['pos']) not in {(0, 0), (1, 1)} for h in result['hints'])
    done = _solve(tiny, solid={(c, i) for c, col in enumerate(BOARD) for i in range(len(col))})
    assert done['plan'] == [] and done['left'] == 0 and done['hints'] == []


def test_excluded_words_are_left_out(tiny):
    result = _solve(tiny, exclude={'bit'})
    assert 'bit' not in [w['word'] for w in result['words']]
    assert sorted(result['plan']) == ['bat', 'cot', 'hit']


def test_blocked_words_only_when_nothing_else_will_do(tiny):
    avoid = _solve(tiny, blocked=lambda w: w == 'bit')
    assert sorted(avoid['plan']) == ['bat', 'cot', 'hit']
    assert 'bit' in [w['word'] for w in avoid['words']]        # still listed
    # Every word with a B is blocked: one of them has to go in.
    forced = _solve(tiny, blocked=lambda w: w[0] == 'b')
    assert sum(w[0] == 'b' for w in forced['plan']) == 1


def test_a_letter_no_word_uses_is_dead_and_left_out_of_the_plan(tiny):
    result = _solve(tiny, board=('bcz', 'aio', 't'))
    assert result['dead'] == [[0, 2]]
    assert result['tiles'][0][2]['count'] == 0
    # A, I and O still need a word each.
    assert len(result['plan']) == 3 and result['exact'] is True
    assert (0, 2) not in {(h['col'], h['pos']) for h in result['hints']}


def test_a_letter_twice_in_a_column_needs_two_words(tiny):
    result = _solve(tiny, board=('bb', 'ai', 't'))
    assert sorted(result['plan']) == ['bat', 'bit']
    assert result['tiles'][0][0]['repeat'] and result['tiles'][0][1]['repeat']
    assert {h['word'] for h in result['hints'] if h['col'] == 0} == {'bat', 'bit'}
    # One copy solid: one word left to find.
    assert _solve(tiny, board=('bb', 'ai', 't'), solid={(0, 0), (1, 0)})['plan'] == ['bit']


def test_no_word_fits(tiny):
    result = _solve(tiny, board=('zq', 'zq', 'zq'))
    assert result['words'] == [] and result['plan'] == [] and result['exact'] is True
    assert len(result['dead']) == 6


def test_out_of_time_the_plan_is_the_greedy_one(tiny, monkeypatch):
    monkeypatch.setattr(ts, 'NODE_CHECK', 1)
    result = _solve(tiny, budget_s=0)
    # Greedy takes HIT, then CAT, HOT and BIT: a plan, but not the fewest.
    assert result['plan'] == ['hit', 'hot', 'cat', 'bit'] and result['exact'] is False


def test_popularity_may_be_missing_or_odd(tiny):
    result = ts.solve(tiny, BOARD, popularity={'hot': float('nan'), 'cat': 'x'})
    assert len(result['plan']) == 3 and all(w['zipf'] == 0.0 for w in result['words'])
    assert all(w['rare'] for w in result['words'])


def _covers(board, solid, words):
    need = {}
    for c, col in enumerate(board):
        for i, letter in enumerate(col):
            if (c, i) not in solid:
                need[(c, letter)] = need.get((c, letter), 0) + 1
    for w in words:
        for c in range(len(board)):
            if need.get((c, w[c]), 0) > 0:
                need[(c, w[c])] -= 1
    return need


def test_plans_on_made_up_boards_are_the_fewest(typeshift_index):
    """Brute force on small boards built from a few common words, as a
    puzzle is built from its core words (scripts/backtest_typeshift.py)."""
    from data import word_pop
    rng = random.Random(11)
    common = sorted(w for w in word_pop if isinstance(w, str) and len(w) == 5 and word_pop[w] >= 3.3
                    and w in typeshift_index)
    checked = 0
    for _ in range(40):
        core = rng.sample(common, 3)
        board = tuple(''.join(dict.fromkeys(w[c] for w in core)) for c in range(5))
        solid = frozenset(t for t in ((c, i) for c, col in enumerate(board) for i in range(len(col)))
                          if rng.random() < 0.3)
        result = ts.solve(typeshift_index, board, solid=solid, popularity=word_pop)
        words = [w['word'] for w in result['words']]
        if len(words) > 30:
            continue
        checked += 1
        reach = _covers(board, solid, words)
        assert all(v <= reach.get(k, 0) for k, v in _covers(board, solid, result['plan']).items())
        for size in range(len(result['plan'])):
            for combo in itertools.combinations(words, size):
                rest = _covers(board, solid, combo)
                assert not all(v <= reach.get(k, 0) for k, v in rest.items()), (board, combo)
    assert checked >= 20


# --- the page --------------------------------------------------------------------

def _faq(html):
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    return next(d for d in map(json.loads, blocks) if d.get('@type') == 'FAQPage')


def test_page_shows_the_marker_and_says_unofficial(client):
    html = client.get('/typeshift').get_data(as_text=True)
    # The hourly web probe expects this marker (datasets/health_web.yaml).
    assert 'id="ts-data-ok"' in html
    assert 'Unofficial helper. Not affiliated with or endorsed by Puzzmo.' in html


def test_faq_structured_data_parses_and_matches_the_visible_faq(client):
    html = client.get('/typeshift').get_data(as_text=True)
    faq = _faq(html)
    visible = re.sub(r'<[^>]+>', '', re.sub(r'<script.*?</script>', '', html, flags=re.S))
    visible = ' '.join(visible.split())
    assert len(faq['mainEntity']) == 8
    for q in faq['mainEntity']:
        assert q['name'] in visible
        assert q['acceptedAnswer']['text'] in visible
    assert any('midnight US Central' in q['acceptedAnswer']['text'] for q in faq['mainEntity'])


def test_faq_leaves_out_shared_boards_when_they_are_off(client, monkeypatch):
    import routes.typeshift as rt
    monkeypatch.setattr(rt, 'TYPESHIFT_BOARDS_LIVE', False)
    html = client.get('/typeshift').get_data(as_text=True)
    assert len(_faq(html)['mainEntity']) == 7 and 'id="ts-shared"' not in html


def test_players_are_never_told_how_boards_or_words_are_decided(client):
    lowered = client.get('/typeshift').get_data(as_text=True).lower()
    for hint in ('different players', 'another player', 'players agreed', 'for everyone',
                 'two players', '2 players', 'three players', '3 players', 'majority'):
        assert hint not in lowered, f'page hints at a threshold: {hint!r}'


def test_the_page_wears_a_beta_tag_until_it_comes_off(client, monkeypatch):
    import helpers
    html = client.get('/typeshift').get_data(as_text=True)
    assert re.search(r'<h1 class="ts-h1">Typeshift Answers &amp; Hints <span class="jj-beta-tag">Beta</span></h1>', html)
    assert 'Beta' not in re.search(r'<title>(.*?)</title>', html, re.S).group(1)
    home = client.get('/').get_data(as_text=True)
    assert re.search(r'Typeshift Solver<span class="beta-tag">Beta</span>', home)
    assert 'href="/typeshift">Typeshift Solver<span class="jj-beta-tag">Beta</span>' in home
    strip = client.get('/smush').get_data(as_text=True).split('More daily puzzle solvers')[1]
    assert re.search(r'href="/typeshift".*?jj-more-beta">Beta</span>', strip, re.S)
    monkeypatch.setattr(helpers, 'BETA_PAGES', frozenset())
    for path in ('/typeshift', '/', '/smush'):
        assert '>Beta</span>' not in client.get(path).get_data(as_text=True), path


def test_the_public_page_never_names_the_admin_pages(client):
    html = client.get('/typeshift').get_data(as_text=True)
    assert 'typeshift_admin' not in html and 'typeshiftApproveState' not in html


def test_a_solve_through_the_route(client):
    # STONE, CRANE and BLIMP stacked: every letter is used by at least one of them.
    body = {'columns': ['scb', 'trl', 'oai', 'nm', 'ep'], 'first_solve': True}
    reply = client.post('/typeshift', json=body).get_json()
    words = {w['word'] for w in reply['words']}
    assert {'stone', 'crane', 'blimp'} <= words
    assert len(reply['plan']) == 3 and reply['exact'] is True and reply['dead'] == []
    assert reply['tiles'][0][0] == {'letter': 's', 'count': reply['tiles'][0][0]['count'], 'solid': False,
                                    'repeat': False}
    # Solid letters and hidden words go along with a solve.
    again = client.post('/typeshift', json=dict(body, solid=[[0, 0]], exclude=['stone'])).get_json()
    assert 'stone' not in {w['word'] for w in again['words']}
    assert again['tiles'][0][0]['solid'] is True and again['left'] == reply['left'] - 1


@pytest.mark.parametrize('body', [
    {'columns': ['ab']},                                   # one column
    {'columns': ['scb', 'tr1', 'oai']},                    # not letters
    {'columns': ['scb', 'trl', 'oai'], 'solid': [[5, 0]]},
    {'columns': ['scb', 'trl', 'oai'], 'solid': 'all'},
    {'columns': ['scb', 'trl', 'oai'], 'exclude': 'stone'},
    {'columns': ['scb', 'trl', 'oai'], 'exclude': ['st']},
    {'columns': ['scb', 'trl', 'oai'], 'exclude': ['a'] * 201},
    {'action': 'nope', 'columns': ['scb', 'trl', 'oai']},
])
def test_bad_requests_are_400s(client, body):
    assert client.post('/typeshift', json=body).status_code == 400
