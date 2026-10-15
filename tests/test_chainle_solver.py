"""functions/chainle.py: the Chainle solver engine.

Most tests run on a tiny hand-made word set, small enough to check every
ranking by hand. The last few load the shipped datasets/chainle_data.npz.
"""

import os

import numpy as np
import pytest

from functions import chainle as ch

# Six-dimensional "word vectors": one axis per theme, so closeness is easy
# to reason about. 0 snow, 1 foot, 2 basket, 3 medicine, 4 size, 5 other.
_V = {
    'snow': [1, 0, 0, 0, 0, 0],
    'foot': [0, 1, 0, 0, 0, 0],
    'basket': [0, 0, 1, 0, 0, 0],
    'ball': [.3, .3, .3, 0, 0, .85],
    'board': [.1, .4, .1, 0, 0, .9],
    'man': [.65, .65, 0, 0, 0, .2],        # close in meaning to snow and foot, no link to foot
    'thing': [.7, .7, 0, 0, 0, .1],         # closer still, but a broad word
    'the': [.7, .7, .1, 0, 0, 0],           # a stopword: never an answer
    'feet': [.05, 1, 0, 0, 0, 0],           # foot's plural
    'football': [.2, .9, 0, 0, 0, .3],      # built on a clue
    'snowball': [.9, .2, 0, 0, 0, .3],
    'doctor': [0, 0, 0, 1, 0, .1],
    'nurse': [0, 0, 0, .9, 0, .3],
    'hospital': [0, 0, 0, .95, 0, .2],
    'hospitals': [0, 0, 0, .97, 0, .2],     # a shade closer than its singular
    'big': [0, 0, 0, 0, 1, .1],
    'large': [0, 0, 0, 0, .9, .2],
    'huge': [0, 0, 0, 0, .8, .3],
    'small': [0, 0, 0, 0, .95, 0],          # an antonym, though as close as a synonym
    'little': [0, 0, 0, 0, .93, .1],
    'tiny': [0, 0, 0, 0, .5, .5],
    'room': [0, 0, .2, 0, .3, .8],
    'game': [.1, .2, .2, 0, 0, .9],
    'fire': [.2, 0, 0, .2, 0, .9],
    'police': [0, .1, 0, .3, 0, .9],
    'station': [.1, .1, 0, .2, 0, .95],
    'cat': [0, 0, 0, 0, 0, 1],
    'phone': [0, 0, .3, 0, .3, .9],
    'telephone': [0, 0, .3, 0, .3, .91],    # built on the clue: too close
    'handset': [0, 0, .25, 0, .35, .9],
}
_LINKS = [
    ('snow', 'ball', 3.3, True), ('foot', 'ball', 5.0, True), ('basket', 'ball', 4.5, True),
    ('snow', 'board', 3.5, True), ('foot', 'board', 3.0, True),
    ('snow', 'man', 3.0, True),
    ('ball', 'room', 3.5, True), ('ball', 'game', 2.8, False),
    ('fire', 'station', 3.0, False), ('police', 'station', 3.2, False),
]


def _arrays():
    return ch.pack(
        _V, zipf={w: 4.0 for w in _V}, answer=set(_V) - {'the'}, broad={'thing'},
        bases={'feet': {'foot'}, 'hospitals': {'hospital'}}, links=_LINKS,
        synonyms={'big': {'large', 'huge'}, 'large': {'big', 'huge'},
                  'phone': {'telephone', 'handset'}},
        antonyms={'big': {'small', 'little'}, 'large': {'small'}},
        pos={'big': ch.ADJ, 'large': ch.ADJ, 'huge': ch.ADJ, 'small': ch.ADJ, 'little': ch.ADJ,
             'tiny': ch.ADJ, 'cat': ch.NOUN})


@pytest.fixture(scope='module')
def data():
    return ch.Data(**_arrays())


def _words(result):
    return [s['word'] for s in result['suggestions']]


# --- lookups ------------------------------------------------------------------

def test_words_are_found_by_a_binary_search(data):
    assert data.id('SNOW') == data.id('snow') >= 0
    assert data.word(data.id('snow')) == 'snow'
    assert data.id('nope') == -1
    assert data.id('') == -1


def test_unknown_clues_are_reported_not_guessed(data):
    res = ch.solve(data, ['snow', 'zzzz'], 'bridge')
    assert res['unknown'] == ['ZZZZ']
    assert res['suggestions'] == []


# --- bridge rounds --------------------------------------------------------------

def _find(res, word):
    return next(s for s in res['suggestions'] if s['word'] == word)


def test_a_compound_with_every_clue_says_so(data):
    res = ch.solve(data, ['snow', 'foot'], 'bridge')
    ball = _find(res, 'BALL')
    assert ball['type'] == 'link'
    assert ball['phrases'] == ['SNOWBALL', 'FOOTBALL']
    assert ball['patterns'] == ['SNOW____', 'FOOT____']
    assert res['suggestions'][0]['strength'] == 1.0
    assert all(0 <= s['strength'] <= 1 for s in res['suggestions'])


def test_closeness_leads_and_a_link_breaks_ties(data):
    # MAN is closer in meaning to both clues than BALL, and the game scores
    # closeness, so it leads. The compounds still lift BALL above where
    # closeness alone puts it.
    words = _words(ch.solve(data, ['snow', 'foot'], 'bridge', top=30))
    assert words[0] == 'MAN'
    unlinked = ch.Data(**ch.pack(_V, zipf={w: 4.0 for w in _V}, answer=set(_V) - {'the'},
                                 broad={'thing'}, bases={'feet': {'foot'}}))
    alone = _words(ch.solve(unlinked, ['snow', 'foot'], 'bridge', top=30))
    assert words.index('BALL') < alone.index('BALL')


def test_a_third_clue_narrows_the_links(data):
    words = _words(ch.solve(data, ['snow', 'foot', 'basket'], 'bridge'))
    assert words[0] == 'BALL'
    # BOARD compounds with snow and foot but not basket: no longer a link.
    board = next(s for s in ch.solve(data, ['snow', 'foot', 'basket'], 'bridge')['suggestions']
                 if s['word'] == 'BOARD')
    assert board['type'] == 'meaning'
    assert board['phrases'][2] is None


def test_phrases_read_in_the_right_order_open_or_closed(data):
    ball = _find(ch.solve(data, ['room', 'game'], 'bridge', top=30), 'BALL')
    assert ball['phrases'] == ['BALLROOM', 'BALL GAME']
    assert ball['patterns'] == ['____ROOM', '____ GAME']
    station = _find(ch.solve(data, ['fire', 'police'], 'bridge', top=30), 'STATION')
    assert station['phrases'] == ['FIRE STATION', 'POLICE STATION']
    assert station['patterns'] == ['FIRE _______', 'POLICE _______']


def test_never_suggests_a_clue_its_inflections_or_words_built_on_it(data):
    words = _words(ch.solve(data, ['snow', 'foot'], 'bridge', top=30))
    for banned in ('SNOW', 'FOOT', 'FEET', 'FOOTBALL', 'SNOWBALL'):
        assert banned not in words
    # The plural as the clue still rules out the singular.
    assert 'FOOT' not in _words(ch.solve(data, ['snow', 'feet'], 'bridge', top=30))


def test_stopwords_are_never_suggested(data):
    assert 'THE' not in _words(ch.solve(data, ['snow', 'foot'], 'bridge', top=30))


def test_broad_words_rank_below_specific_ones(data):
    words = _words(ch.solve(data, ['snow', 'foot'], 'bridge', top=30))
    assert words.index('MAN') < words.index('THING')


def test_a_meaning_only_answer_wins_when_nothing_links(data):
    res = ch.solve(data, ['doctor', 'nurse'], 'bridge')
    top = res['suggestions'][0]
    # HOSPITALS scores a shade higher, but it's the same answer: listed once,
    # in its base form.
    assert top['word'] == 'HOSPITAL'
    assert 'HOSPITALS' not in _words(res)
    assert top['type'] == 'meaning'
    assert top['phrases'] == [None, None]


# --- closest-meaning rounds -----------------------------------------------------------

def test_closest_meaning_prefers_synonyms_and_drops_opposites(data):
    res = ch.solve(data, ['big'], 'meaning', top=30)
    words = _words(res)
    assert words[:2] == ['LARGE', 'HUGE']
    assert [s['type'] for s in res['suggestions'][:2]] == ['synonym', 'synonym']
    assert 'SMALL' not in words and 'LITTLE' not in words
    assert 'BIG' not in words


def test_closest_meaning_skips_a_word_built_on_the_clue(data):
    # The game turns a word built on the clue down as too close.
    words = _words(ch.solve(data, ['phone'], 'meaning', top=30))
    assert words[0] == 'HANDSET'
    assert 'TELEPHONE' not in words


def test_a_word_without_synonyms_leans_on_its_family():
    # A rare sense's synonym (FRUMP) never comes into it: the build keeps
    # main senses only. With no synonyms left, the clue's family (DOG: WOLF,
    # FOX) gets a boost and anchors closeness, so WOLF beats CAT, which is
    # a shade closer on the vectors alone.
    vecs = {'dog': [1, .2, 0], 'wolf': [.9, .1, .3], 'fox': [.8, .1, .4], 'cat': [1, .5, 0],
            'cab': [0, 0, 1], 'frump': [0, 1, 0]}
    plain = ch.Data(**ch.pack(vecs, zipf={w: 4.0 for w in vecs}))
    assert _words(ch.solve(plain, ['dog'], 'meaning'))[0] == 'CAT'
    kin = ch.Data(**ch.pack(vecs, zipf={w: 4.0 for w in vecs}, related={'dog': {'wolf', 'fox'}}))
    res = ch.solve(kin, ['dog'], 'meaning')
    assert _words(res)[0] == 'WOLF'
    assert res['suggestions'][0]['type'] == 'related'


def test_closest_meaning_keeps_to_the_clues_part_of_speech(data):
    words = _words(ch.solve(data, ['big'], 'meaning', top=30))
    assert 'CAT' not in words        # a noun, for an adjective
    assert 'BALL' in words           # part of speech unknown: kept


# --- admin assessment ---------------------------------------------------------------

def test_assess_reports_the_top_pick_and_each_words_rank(data):
    got = ch.assess(data, ['snow', 'foot'], 'bridge', ['ball', 'man', 'zzzz', ''])
    assert got['top'] == 'MAN'
    assert got['ranks']['MAN'] == 1
    assert got['ranks']['BALL'] == 2
    assert got['ranks']['ZZZZ'] is None
    assert ch.assess(data, ['snow', 'zzzz'], 'bridge', ['ball']) is None


def test_the_arrays_survive_a_round_trip_through_a_file(tmp_path):
    path = tmp_path / 'tiny.npz'
    np.savez_compressed(path, **_arrays())
    loaded = ch.Data.from_file(str(path))
    for clues, kind in ((['snow', 'foot'], 'bridge'), (['big'], 'meaning')):
        assert ch.solve(loaded, clues, kind) == ch.solve(ch.Data(**_arrays()), clues, kind)


# --- the shipped data ---------------------------------------------------------------

SHIPPED = os.path.join(os.path.dirname(__file__), os.pardir, 'datasets', 'chainle_data.npz')


@pytest.fixture(scope='module')
def shipped():
    return ch.Data.from_file(SHIPPED)


@pytest.mark.parametrize('clues,answer', [
    (['bed', 'bath'], 'ROOM'),
    (['tooth', 'hair', 'paint'], 'BRUSH'),
    (['doctor', 'nurse'], 'HOSPITAL'),
], ids=['bed-bath', 'tooth-hair-paint', 'doctor-nurse'])
def test_the_shipped_data_finds_classic_bridges(shipped, clues, answer):
    assert answer in _words(ch.solve(shipped, clues, 'bridge'))[:3]


def test_the_shipped_data_finds_a_synonym_not_an_opposite(shipped):
    words = _words(ch.solve(shipped, ['big'], 'meaning'))
    assert 'LARGE' in words[:5]
    assert 'SMALL' not in words and 'LITTLE' not in words
