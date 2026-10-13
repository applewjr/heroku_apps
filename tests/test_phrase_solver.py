"""Tests for the Guess The Phrase engine (functions/phrase_solver.py).

Built on a tiny in-memory phrase list, so they pin the matching rules rather
than whatever the shipped bank happens to contain, and need no data files.
"""

import pytest

from functions import phrase_solver as ps


def bank(*rows):
    """rows: (display, [tags], popularity)."""
    return ps.PhraseBank((d, sum(ps.TAG_BIT[t] for t in tags), pop) for d, tags, pop in rows)


class Data:
    def __init__(self, phrase_bank, words=(), pairs=()):
        self.bank = phrase_bank
        self.words = ps.WordList(words)
        self.bigrams = ps.Bigrams(pairs)


BANK = bank(
    ('HOTEL CALIFORNIA', ['SONG'], 40),
    ('HOTEL TRANSYLVANIA', ['MOVIE'], 38),
    ('MOTEL CALIFORNIA', ['THING'], 5),
    ('FIRE TRUCK', ['THING'], 30),
    ('TRUCK STOP', ['THING'], 28),
    ('STAR WARS', ['MOVIE'], 45),
    ("DON'T STOP BELIEVIN'", ['SONG'], 35),
)


# --- normalising ------------------------------------------------------------

def test_phrase_key_drops_punctuation_and_splits_hyphens():
    assert ps.phrase_key("Don't Stop Believin'") == 'DONT STOP BELIEVIN'
    assert ps.phrase_key('Spider-Man & Friends!') == 'SPIDER MAN FRIENDS'
    assert ps.phrase_key("Where's Waldo?") == 'WHERES WALDO'


def test_phrase_key_rejects_digits_and_empty():
    assert ps.phrase_key('Apollo 13') == ''
    assert ps.phrase_key('  ') == ''


def test_parse_board_accepts_both_blank_marks_and_board_punctuation():
    assert ps.parse_board("?otel  calif_rnia") == ('?OTEL', 'CALIF?RNIA')
    assert ps.parse_board("D?N'T STOP") == ('D?NT', 'STOP')


@pytest.mark.parametrize('board', ['', '   ', 'AB1 CD', 'HELLO <script>'])
def test_parse_board_rejects_junk(board):
    with pytest.raises(ValueError):
        ps.parse_board(board)


# --- matching ---------------------------------------------------------------

def test_blank_cannot_be_a_letter_already_shown():
    # T is shown, so every T is: the blank in "?OOT" can't be T.
    b = bank(('TOOT', [], 40), ('SOOT', [], 10))
    pats = ps.parse_board('?OOT')
    assert [k for k, _, _, _ in b.search(pats, ps.excluded_letters(pats))] == ['SOOT']


def test_tried_letters_exclude_candidates():
    b = bank(('WAVE', [], 40), ('CAVE', [], 5))
    pats = ps.parse_board('?AVE')
    assert [k for k, _, _, _ in b.search(pats, ps.excluded_letters(pats))] == ['WAVE', 'CAVE']
    assert [k for k, _, _, _ in b.search(pats, ps.excluded_letters(pats, tried='w'))] == ['CAVE']


def test_search_only_scans_the_boards_word_lengths():
    assert BANK.search(ps.parse_board('????'), set()) == []


def test_display_form_keeps_punctuation():
    pats = ps.parse_board("?ON'T ?TOP ?ELIEVIN'")
    out = BANK.search(pats, ps.excluded_letters(pats))
    assert [d for _, d, _, _ in out] == ["DON'T STOP BELIEVIN'"]


def test_mashup_joins_on_the_shared_word():
    pats = ps.parse_board('FIRE TRUCK STOP')
    assert [k for k, _, _ in ps.mashups(BANK, pats, set())] == ['FIRE TRUCK STOP']


def test_category_ranks_in_category_phrases_first():
    # Out of category costs CATEGORY_PENALTY (25): 30 - 25 < 10.
    data = Data(bank(('JAZZ BAND', ['THING'], 30), ('JAZZ SONG', ['SONG'], 10)))
    res = ps.solve(data, '???? ????', tags=('SONG',), mashup='no')
    assert [d for d, _, _ in res['candidates']] == ['JAZZ SONG', 'JAZZ BAND']


def test_search_top_counts_every_fit_but_builds_only_k():
    b = bank(*[(f'{c}AT', [], 10 + i) for i, c in enumerate('BCFHMPRSV')])
    total, rows = b.search_top(ps.parse_board('?AT'), {'A', 'T'}, 0, 3)
    assert total == 9
    assert [k for k, _, _ in rows] == ['VAT', 'SAT', 'RAT']


def test_search_top_keeps_the_best_of_each_group():
    # BAT is last in popularity order but in the category: 30 beats HAT's
    # 50 - 25 = 25. Keeping just the first k matches would have lost it.
    b = bank(('BAT', ['ANIMAL'], 30), ('CAT', [], 60), ('HAT', [], 50), ('MAT', [], 45))
    total, rows = b.search_top(ps.parse_board('?AT'), {'A', 'T'}, ps.TAG_BIT['ANIMAL'], 2)
    assert total == 4
    assert [k for k, _, _ in rows] == ['CAT', 'BAT']


def test_overlay_search_top_applies_added_and_removed():
    base = bank(('CAT', [], 40), ('HAT', [], 30))
    ob = ps.OverlayBank(base, added=['MAT'], removed=['CAT'])
    total, rows = ob.search_top(ps.parse_board('?AT'), {'A', 'T'}, 0, 10)
    assert total == 2
    assert [k for k, _, _ in rows] == ['MAT', 'HAT']


def test_total_is_every_fit_even_past_the_advice_pool(monkeypatch):
    monkeypatch.setattr(ps, 'ADVICE_POOL', 2)
    data = Data(bank(*[(f'{c}AT', [], 10 + i) for i, c in enumerate('BCFHMPRSV')]))
    res = ps.solve(data, '?AT', mashup='no')
    assert res['total'] == 9
    assert len(res['candidates']) == 2


# --- solve() ----------------------------------------------------------------

def test_solve_counts_fits_and_blanks():
    res = ps.solve(Data(BANK), '?OTEL ?ALIFORNIA', mashup='no')
    assert res['total'] == 2
    assert res['blanks'] == 2
    assert res['candidates'][0][0] == 'HOTEL CALIFORNIA'


def test_advice_prefers_the_letter_most_candidates_share():
    data = Data(bank(('CAT', [], 30), ('HAT', [], 30), ('BAT', [], 30), ('CAB', [], 30)))
    res = ps.solve(data, '?A?', mashup='no')
    best, p, _ = res['advice'][0]
    assert best in 'CT' and p >= 0.5
    # Shown letters are never advised.
    assert 'A' not in {c for c, _, _ in res['advice']}


def test_advice_keeps_letters_only_a_long_shot_needs():
    # CAVE is ~10^(-100/20) as likely as WAVE: its C rounds to 0.000 but must
    # still be advised, or a player whose answer is CAVE runs out of advice.
    data = Data(bank(('WAVE', [], 110), ('CAVE', [], 10)))
    letters = [c for c, _, _ in ps.solve(data, '?AVE', tried='W', mashup='no')['advice']]
    assert letters[0] == 'C'


def test_candidate_shares_add_up_and_follow_rank():
    res = ps.solve(Data(BANK), '????? ??????????', mashup='no')
    shares = [share for _, _, share in res['candidates']]
    assert shares == sorted(shares, reverse=True)
    assert abs(sum(shares) - 1) < 0.01


def test_advice_probabilities_are_bounded():
    res = ps.solve(Data(BANK), '????? ??????????', mashup='no')
    assert all(0 < p <= 1 and e >= 0 for _, p, e in res['advice'])


def test_fallback_when_no_known_phrase_fits():
    # MOTEL is the more common word alone, but HOTEL CALIFORNIA is a pair
    # seen on the web, and the bigram model prefers words that go together.
    words = [('MOTEL', 4.6), ('HOTEL', 4.5), ('CALIFORNIA', 5.0)]
    data = Data(bank(('STAR WARS', [], 40)), words, [('HOTEL CALIFORNIA', -0.3)])
    res = ps.solve(data, '?OTEL CALIFORNIA', mashup='no')
    assert res['total'] == 0
    assert res['fallback'][0][0] == 'HOTEL CALIFORNIA'
    assert res['slots'][0] == ['MOTEL', 'HOTEL']


def test_fully_shown_word_is_its_own_candidate():
    data = Data(bank(), [('CAVE', 4.5)])
    assert data.words.match('ZYXW', set()) == [('ZYXW', 0.0)]
    assert data.words.match('CAVE', set()) == [('CAVE', 4.5)]


def test_bigrams_lookup():
    b = ps.Bigrams([('HOTEL CALIFORNIA', -0.3), ('FIRE TRUCK', -1.25), ('OF THE', 0.0)])
    assert b.get('HOTEL', 'CALIFORNIA') == -0.3
    assert b.get('FIRE', 'TRUCK') == -1.25
    assert b.get('OF', 'THE') == 0.0
    assert b.get('CALIFORNIA', 'HOTEL') is None
