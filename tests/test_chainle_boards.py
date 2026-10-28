"""Chainle's today's rounds (routes/chainle.py): the rounds early players
type, offered to the players after them, who pick by the clues on their
screen; James's approvals; /chainle_dev; and the Today's Rounds tab.

Nothing here reaches a real database: secret_pass points at the production
JawsDB. Most tests swap the module's table functions for an in-memory
stand-in (MemoryRounds); the SQL itself is checked against test_blossom's
scripted fake. Each simulated player is a different X-Forwarded-For, which
is what helpers.client_ip reads: an IPv4 address each, or IPv6 ones where a
test needs players on one network. Clue words come from the tiny test
vocabulary (test_chainle_solver), never a real round.
"""

import json
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta
from urllib.parse import unquote_plus

import pytest

import routes.chainle as rc
import routes.wordgames as wordgames
from test_ad_slots import LIVE, LOADER, UNIT, slots  # noqa: F401
from test_blossom import _admin_headers, _ScriptedDB
from test_chainle import tiny  # noqa: F401

DAY = date(2026, 10, 28)
# One made-up day on one site, and the first rounds of another's.
R1, R2, R3, R4, R5 = ['snow', 'foot'], ['doctor', 'nurse'], ['fire', 'police'], ['basket', 'room', 'game'], ['big']
DAY_ROUNDS = (R1, R2, R3, R4, R5)
OTHER1, OTHER2 = ['cat', 'phone'], ['station', 'board']
IPS = [f'203.0.113.{n}' for n in range(1, 60)]
# Three /64s of one /48: three players on one network.
ONE_NETWORK = ['2001:db8:1:1::1', '2001:db8:1:2::1', '2001:db8:1:3::1']
FAQ_Q = "Can it fill in today's rounds?"


def _words(clues):
    """A round as the page gets it: its words upper case, in the order typed."""
    return [c.upper() for c in clues]


def _text(clues):
    """A round as it is stored."""
    return ' '.join(_words(clues))


class MemoryRounds:
    """Stands in for chainle_boards: store_round and the admin actions
    write here, and rows_on reads back, rows in routes.chainle's
    _ROUND_COLUMNS order. One row per (day, voter, site, tab), as the
    table's unique key keeps it."""

    def __init__(self):
        self.rows = {}
        self.offsets = {}
        self.clock = datetime(2026, 10, 28, 6, 0)
        self.reads = 0

    def _tick(self):
        self.clock += timedelta(minutes=1)
        return self.clock

    def store(self, day, voter, prefix, round_no, clues, utc_offset, source):
        self.rows[(day, voter, '', round_no)] = (day, voter, prefix, '', round_no, clues, source, self._tick())
        self.offsets[(day, voter, round_no)] = utc_offset

    def _drop(self, keep):
        gone = [k for k, r in self.rows.items() if not keep(r)]
        for k in gone:
            del self.rows[k]
        return len(gone)

    def approve(self, day, site, rounds):
        # A whole set: the site's earlier approval that day goes first.
        self.unapprove(day, site)
        for round_no, clues in sorted(rounds.items()):
            self.rows[(day, rc.APPROVAL, site, round_no)] = (
                day, rc.APPROVAL, None, site, round_no, clues, rc.APPROVAL, self._tick())

    def unapprove(self, day, site):
        return self._drop(lambda r: not (r[0] == day and r[3] == site and r[6] == rc.APPROVAL))

    def remove_rounds(self, day, clue_texts):
        keys = {rc.clue_key(text) for text in clue_texts}
        return self._drop(lambda r: not (r[0] == day and rc.clue_key(r[5]) in keys and r[6] != rc.APPROVAL))

    def remove_players(self, day, voters):
        return self._drop(lambda r: not (r[0] == day and r[1] in voters and r[6] != rc.APPROVAL))

    def rows_on(self, first, last):
        self.reads += 1
        return [r for r in self.rows.values() if first <= r[0] <= last]

    def players(self):
        return [r for r in self.rows.values() if r[6] != rc.APPROVAL]

    def approvals(self):
        return {(r[3], r[4]): r[5] for r in self.rows.values() if r[6] == rc.APPROVAL}


@pytest.fixture
def rounds(monkeypatch, tiny):
    """The in-memory table, today's rounds live on /chainle, no memo between
    reads, and Pacific's date set, so these tests exercise the rules; the
    dark launch and the memo have tests of their own."""
    mem = MemoryRounds()
    monkeypatch.setattr(rc, 'store_round', mem.store)
    monkeypatch.setattr(rc, 'store_approval', mem.approve)
    monkeypatch.setattr(rc, 'remove_approval', mem.unapprove)
    monkeypatch.setattr(rc, 'remove_rounds', mem.remove_rounds)
    monkeypatch.setattr(rc, 'remove_players', mem.remove_players)
    monkeypatch.setattr(rc, 'rows_on', mem.rows_on)
    monkeypatch.setattr(rc, 'CHAINLE_ROUNDS_LIVE', True)
    monkeypatch.setattr(rc, 'ROUNDS_CACHE_SECONDS', 0)
    monkeypatch.setattr(rc, '_sets_cache', {})
    monkeypatch.setattr(rc, '_today', lambda: DAY)
    return mem


def _log(client, ip, round_no, clues, day=DAY, source='typed', offset=-420, path='/chainle', headers=None):
    resp = client.post(path, json={'action': 'log_round', 'round_no': round_no, 'clues': clues,
                                   'played_on': day.isoformat(), 'utc_offset': offset, 'source': source},
                       headers={'X-Forwarded-For': ip, **(headers or {})})
    assert resp.status_code == 200 and resp.get_json() == {'status': 'success'}


def _play(client, ips, *by_tab):
    """Each player logs these rounds, the first in tab 1 and so on (None
    skips a tab)."""
    for ip in ips:
        for tab, clues in enumerate(by_tab, 1):
            if clues:
                _log(client, ip, tab, clues)


def _sets(client, day=DAY, path='/chainle', headers=None):
    resp = client.post(path, json={'action': 'todays_rounds', 'played_on': day.isoformat()}, headers=headers)
    assert resp.status_code == 200
    return resp.get_json()['sets']


def _crowd(*by_round):
    """A players' set as the page gets it: {round: clue words}."""
    return {'site': '', 'rounds': {str(n): _words(c) for n, c in by_round}}


# --- the players' rounds ------------------------------------------------------

def test_three_players_make_a_round_count_and_two_wait(client, rounds):
    _play(client, IPS[:2], R1)
    assert _sets(client) == []
    _play(client, IPS[2:3], R1)
    assert _sets(client) == [_crowd((1, R1))]


def test_players_on_one_network_count_once(client, rounds):
    _play(client, ONE_NETWORK, R1)
    assert _sets(client) == []
    _play(client, IPS[:2], R1)
    assert _sets(client) == [_crowd((1, R1))]


def test_a_later_entry_in_a_tab_replaces_the_first(client, rounds):
    _play(client, IPS[:3], R1)
    _log(client, IPS[0], 1, OTHER1)
    assert _sets(client) == []
    assert len(rounds.players()) == 3


def test_rounds_players_typed_together_make_one_set(client, rounds):
    _play(client, IPS[:3], *DAY_ROUNDS)
    assert _sets(client) == [_crowd(*enumerate(DAY_ROUNDS, 1))]


def test_one_shared_player_keeps_two_rounds_apart(client, rounds):
    _play(client, IPS[:3], R1)
    _play(client, IPS[2:5], None, R2)
    assert _sets(client) == [_crowd((1, R1)), _crowd((2, R2))]
    _play(client, IPS[3:4], R1)
    assert _sets(client) == [_crowd((1, R1), (2, R2))]


def test_more_than_half_wins_a_round_and_a_tie_leaves_it_blank(client, rounds):
    _play(client, IPS[:3], R1, R2)
    _play(client, IPS[3:6], R1, R3)
    assert _sets(client) == [_crowd((1, R1))]
    _play(client, IPS[6:7], R1, R2)
    assert _sets(client) == [_crowd((1, R1), (2, R2))]


def test_a_round_keeps_the_order_it_was_typed_in(client, rounds, tiny, monkeypatch):
    # James typed DOLLAR then CONSUMPTION and saw them swapped (2026-10-28).
    tiny()
    _approve(client, monkeypatch, 'chainle.io', round1_1='snow', round1_2='foot')
    assert rounds.approvals() == {('chainle.io', 1): 'SNOW FOOT'}
    assert _sets(client) == [{'site': 'chainle.io', 'rounds': {'1': ['SNOW', 'FOOT']}}]
    assert 'SNOW + FOOT' in _tab(client, monkeypatch)


def test_players_who_typed_a_round_the_other_way_round_still_agree(client, rounds):
    _play(client, IPS[:2], ['snow', 'foot'])
    _play(client, IPS[2:3], ['foot', 'snow'])
    # Three players count, shown in the order most of them typed.
    assert _sets(client) == [{'site': '', 'rounds': {'1': ['SNOW', 'FOOT']}}]


def test_a_round_goes_in_the_latest_tab_two_of_its_players_used(client, rounds):
    _play(client, IPS[:4], R3)
    _play(client, IPS[4:6], None, None, R3)
    assert _sets(client) == [_crowd((3, R3))]


def test_a_triple_is_round_4_and_one_word_is_round_5(client, rounds):
    _play(client, IPS[:3], R4)
    _play(client, IPS[3:6], R5)
    assert sorted(_sets(client), key=lambda s: list(s['rounds'])) == [_crowd((4, R4)), _crowd((5, R5))]


def test_round_placement():
    assert rc._round_of('FIRE POLICE', Counter({1: 4, 3: 2})) == 3
    assert rc._round_of('FIRE POLICE', Counter({1: 4, 3: 1})) == 1
    assert rc._round_of('FIRE POLICE', Counter({2: 1, 3: 1})) == 3
    assert rc._round_of('FIRE POLICE', Counter({5: 2, 1: 1})) == 3
    assert rc._round_of('BASKET GAME ROOM', Counter({1: 3})) == 4
    assert rc._round_of('BIG', Counter({2: 3})) == 5


def test_at_most_three_sets_most_players_first(client, rounds):
    _play(client, IPS[0:6], R1)
    _play(client, IPS[6:11], OTHER1)
    _play(client, IPS[11:15], R2)
    _play(client, IPS[15:18], OTHER2)
    assert [s['rounds']['1'] for s in _sets(client)] == [_words(R1), _words(OTHER1), _words(R2)]


def test_an_edited_round_counts(client, rounds):
    _play(client, IPS[:2], R1)
    _log(client, IPS[2], 1, R1, source='edited')
    assert _sets(client) == [_crowd((1, R1))]


def test_unknown_words_and_wrong_clocks_store_nothing(client, rounds):
    _log(client, IPS[0], 1, ['snow', 'zebra'])
    _log(client, IPS[0], 1, R1, day=DAY - timedelta(days=3))
    assert rounds.rows == {}
    _log(client, IPS[0], 1, R1, day=DAY + timedelta(days=1))
    assert len(rounds.rows) == 1


def test_the_players_clock_is_kept_when_it_is_a_real_one(client, rounds):
    _log(client, IPS[0], 1, R1, offset=-420)
    _log(client, IPS[1], 1, R1, offset=99999)
    assert set(rounds.offsets.values()) == {-420, None}


@pytest.mark.parametrize('change', [
    {'round_no': 0}, {'round_no': 6}, {'round_no': '1'}, {'round_no': True},
    {'clues': 'snow foot'}, {'clues': []}, {'clues': ['snow', 'foot', 'ball', 'board']},
    {'clues': ['snow', 'snow']}, {'clues': ['snow1', 'foot']},
    {'source': 'loaded'}, {'played_on': 'today'},
])
def test_anything_but_a_round_is_a_400(client, rounds, change):
    good = {'action': 'log_round', 'round_no': 1, 'clues': R1, 'played_on': DAY.isoformat(), 'source': 'typed'}
    assert client.post('/chainle', json={**good, **change}).status_code == 400
    assert rounds.rows == {}


def test_a_bad_date_asking_for_the_rounds_is_a_400(client, rounds):
    assert client.post('/chainle', json={'action': 'todays_rounds', 'played_on': 'today'}).status_code == 400


def test_a_wrong_clock_gets_no_rounds(client, rounds):
    _play(client, IPS[:3], R1)
    assert _sets(client, day=DAY - timedelta(days=3)) == []


# --- approvals ----------------------------------------------------------------

def _form(client, monkeypatch, path, **form):
    return client.post(path, data=form, headers=_admin_headers(monkeypatch))


def _approve(client, monkeypatch, site, day=DAY, **boxes):
    """The tab's approve form: one box per clue, round<n>_<m>."""
    return _form(client, monkeypatch, '/chainle_admin/approve_rounds',
                 played_on=day.isoformat(), site=site, tab='rounds', **boxes)


def test_an_approval_comes_first_and_the_players_fill_its_blank_rounds(client, rounds, tiny, monkeypatch):
    tiny()
    _play(client, IPS[:3], *DAY_ROUNDS)
    _play(client, IPS[3:6], OTHER1, OTHER2)
    resp = _approve(client, monkeypatch, 'chainle.io', round1_1='snow', round1_2='foot',
                    round2_1='doctor', round2_2='nurse')
    assert resp.status_code == 302
    assert 'Approved 2 rounds of chainle.io for 10-28' in unquote_plus(resp.location)
    assert 'tab=rounds' in resp.location
    assert _sets(client) == [
        {'site': 'chainle.io', 'rounds': {str(n): _words(c) for n, c in enumerate(DAY_ROUNDS, 1)}},
        _crowd((1, OTHER1), (2, OTHER2)),
    ]


def test_an_approval_needs_no_players(client, rounds, tiny, monkeypatch):
    tiny()
    _approve(client, monkeypatch, 'chainle.org', round5_1='big')
    assert _sets(client) == [{'site': 'chainle.org', 'rounds': {'5': ['BIG']}}]


def test_an_approval_is_the_sites_whole_set_and_replaces_the_last(client, rounds, tiny, monkeypatch):
    tiny()
    _approve(client, monkeypatch, 'chainle.io', round1_1='Snow', round1_2=' foot ', round5_1='big')
    assert rounds.approvals() == {('chainle.io', 1): 'SNOW FOOT', ('chainle.io', 5): 'BIG'}
    _approve(client, monkeypatch, 'chaindle.org', round5_1='big')
    _approve(client, monkeypatch, 'chainle.io', round1_1='cat', round1_2='phone', round2_1='doctor', round2_2='nurse')
    assert rounds.approvals() == {('chainle.io', 1): 'CAT PHONE', ('chainle.io', 2): 'DOCTOR NURSE',
                                  ('chaindle.org', 5): 'BIG'}


def test_each_site_has_its_own_approval(client, rounds, tiny, monkeypatch):
    tiny()
    for site in rc.BOARD_SITES:
        _approve(client, monkeypatch, site, round5_1='big')
    assert [s['site'] for s in _sets(client)] == list(rc.BOARD_SITES)


@pytest.mark.parametrize('site, boxes, error', [
    ('example.com', {'round1_1': 'snow', 'round1_2': 'foot'}, 'Choose a date and a site'),
    ('chainle.io', {}, "Type at least one round's clue words"),
    ('chainle.io', {'round1_1': 'snow'}, 'Round 1 takes 2 clue words'),
    ('chainle.io', {'round4_1': 'snow', 'round4_2': 'foot'}, 'Round 4 takes 3 clue words'),
    ('chainle.io', {'round1_1': 'snow', 'round1_2': 'zebra'}, "Round 1: a word isn't in the solver's list"),
    ('chainle.io', {'round2_1': 'snow', 'round2_2': 'snow'}, 'Round 2:'),
    ('chainle.io', {'round3_1': 'snow foot', 'round3_2': 'ball'}, 'Round 3:'),
])
def test_the_approve_form_refuses_anything_but_rounds(client, rounds, tiny, monkeypatch, site, boxes, error):
    tiny()
    resp = _approve(client, monkeypatch, site, **boxes)
    assert error in unquote_plus(resp.location)
    assert rounds.rows == {}


def test_approving_a_players_set_copies_its_rounds(client, rounds, tiny, monkeypatch):
    tiny()
    _play(client, IPS[:3], R1, R2, None, R4)
    html = _tab(client, monkeypatch)
    assert all(f'name="site" value="{site}"' in html for site in rc.BOARD_SITES)
    fields = _set_form(html, 'players')
    resp = _form(client, monkeypatch, '/chainle_admin/approve_rounds', site='chaindle.org', **fields)
    assert resp.status_code == 302
    assert rounds.approvals() == {('chaindle.org', 1): _text(R1), ('chaindle.org', 2): _text(R2),
                                  ('chaindle.org', 4): _text(R4)}
    assert _sets(client) == [{'site': 'chaindle.org',
                              'rounds': {'1': _words(R1), '2': _words(R2), '4': _words(R4)}}]


def test_undo_takes_back_one_sites_day(client, rounds, tiny, monkeypatch):
    tiny()
    rounds.approve(DAY, 'chainle.io', {1: _text(R1)})
    rounds.approve(DAY, 'chaindle.org', {1: _text(OTHER1)})
    _play(client, IPS[:3], R1)
    resp = _form(client, monkeypatch, '/chainle_admin/unapprove_rounds', played_on=DAY.isoformat(), site='chainle.io')
    assert 'Approval undone' in unquote_plus(resp.location)
    assert set(rounds.approvals()) == {('chaindle.org', 1)}
    assert len(rounds.players()) == 3
    resp = _form(client, monkeypatch, '/chainle_admin/unapprove_rounds', played_on=DAY.isoformat(), site='chainle.io')
    assert 'Nothing of chainle.io was approved for 10-28' in unquote_plus(resp.location)


def _set_form(html, kind, day=DAY):
    """The hidden fields of the first set card's form of this kind for `day`
    on the tab: 'players' (a set the players settled) or 'typed'."""
    marker = 'remove_set' if kind == 'players' else 'remove_typed_set'
    form = next(f for f in re.findall(r'<form method="POST" action="/chainle_admin/approve_rounds" '
                                      r'class="set-approve">(.*?)</form>', html, re.S)
                if f'formaction="/chainle_admin/{marker}"' in f
                and f'name="played_on" value="{day.isoformat()}"' in f)
    return dict(re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', form))


def _tab(client, monkeypatch):
    return client.get('/chainle_admin?tab=rounds', headers=_admin_headers(monkeypatch)).get_data(as_text=True)


def test_a_set_one_player_typed_can_be_approved(client, rounds, tiny, monkeypatch):
    tiny()
    _play(client, IPS[:1], *DAY_ROUNDS)
    assert _sets(client) == []
    fields = _set_form(_tab(client, monkeypatch), 'typed')
    resp = _form(client, monkeypatch, '/chainle_admin/approve_rounds', site='chainle.org', **fields)
    assert 'Approved 5 rounds of chainle.org' in unquote_plus(resp.location)
    assert _sets(client) == [{'site': 'chainle.org',
                              'rounds': {str(n): _words(c) for n, c in enumerate(DAY_ROUNDS, 1)}}]
    assert 'in set 1' in _tab(client, monkeypatch)


def test_remove_on_a_typed_set_deletes_only_those_players_day(client, rounds, tiny, monkeypatch):
    tiny()
    _play(client, IPS[:2], *DAY_ROUNDS)
    _play(client, IPS[2:3], R1, R2)          # agrees on two rounds, typed no more
    _log(client, IPS[0], 1, R1, day=DAY + timedelta(days=1))
    rounds.approve(DAY, 'chainle.io', {1: _text(R1)})
    fields = _set_form(_tab(client, monkeypatch), 'typed')   # most players first: the two
    resp = _form(client, monkeypatch, '/chainle_admin/remove_typed_set', **fields)
    assert 'Removed 10 entries on 10-28' in unquote_plus(resp.location)
    left = rounds.players()
    assert sorted((r[0], r[5]) for r in left) == sorted([(DAY, _text(R1)), (DAY, _text(R2)),
                                                         (DAY + timedelta(days=1), _text(R1))])
    assert len({r[1] for r in left if r[0] == DAY}) == 1    # the third player's two rounds
    assert rounds.approvals() == {('chainle.io', 1): _text(R1)}
    # Pressed again from the same page: those players have nothing left.
    resp = _form(client, monkeypatch, '/chainle_admin/remove_typed_set', **fields)
    assert 'Nobody has exactly that set any more' in unquote_plus(resp.location)


def test_remove_on_a_players_set_deletes_every_entry_of_its_rounds(client, rounds, tiny, monkeypatch):
    tiny()
    _play(client, IPS[:3], R1, R2)
    _play(client, IPS[3:4], R1)
    _play(client, IPS[4:5], OTHER1)
    rounds.approve(DAY, 'chaindle.org', {5: _text(R5)})
    fields = _set_form(_tab(client, monkeypatch), 'players')
    resp = _form(client, monkeypatch, '/chainle_admin/remove_set', **fields)
    assert 'Removed 7 entries on 10-28' in unquote_plus(resp.location)
    assert [r[5] for r in rounds.players()] == [_text(OTHER1)]
    assert rounds.approvals() == {('chaindle.org', 5): _text(R5)}


def test_remove_refuses_anything_but_a_set(client, rounds, tiny, monkeypatch):
    tiny()
    for path in ('remove_set', 'remove_typed_set'):
        resp = _form(client, monkeypatch, f'/chainle_admin/{path}', played_on=DAY.isoformat(), round1_1='sn0w')
        assert 'Not a set' in unquote_plus(resp.location)
        resp = _form(client, monkeypatch, f'/chainle_admin/{path}', played_on=DAY.isoformat())
        assert 'Not a set' in unquote_plus(resp.location)


def test_admin_round_actions_need_the_password(client):
    for path in ('approve_rounds', 'unapprove_rounds', 'remove_set', 'remove_typed_set'):
        assert client.post(f'/chainle_admin/{path}', data={}).status_code == 401


# --- the Today's Rounds tab ---------------------------------------------------

def test_the_tab_shows_the_sets_and_every_round_typed(client, rounds, tiny, monkeypatch):
    tiny()
    _play(client, IPS[:3], R1, R2)
    _log(client, IPS[9], 1, OTHER1)
    rounds.approve(DAY, 'chainle.org', {5: 'BIG'})
    html = client.get('/chainle_admin?tab=rounds', headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert 'id="rounds-tab" class="tab-content active"' in html
    assert 'your chainle.org' in html and '3 players' in html
    assert 'SNOW + FOOT' in html and 'DOCTOR + NURSE' in html
    assert 'CAT + PHONE' in html and 'needs 2 more players' in html
    assert 'Every round players typed: 3 (2 in a set)' in html
    assert 'Sets players typed: 2' in html and 'Typed by <b>3 players</b>' in html
    # Approve and Remove work on whole sets; a round on its own has neither.
    assert 'remove_round_group' not in html
    # Live in this fixture; dark, it says what would be.
    assert '>Offered<' in html and 'Would be offered' not in html
    # One Approve button per site, and a link to each game.
    for site in rc.BOARD_SITES:
        assert f'name="site" value="{site}"' in html
        assert f'href="https://{site}/"' in html
    # One box per clue word: two for rounds 1-3, three for 4, one for 5.
    for n, count in rc.ROUND_CLUES.items():
        assert f'name="round{n}_{count}"' in html and f'name="round{n}_{count + 1}"' not in html


def test_the_admin_page_still_opens_on_player_reports(client, rounds, tiny, monkeypatch):
    tiny()
    html = client.get('/chainle_admin', headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert 'id="reports-tab" class="tab-content active"' in html
    assert "Today's Rounds" in html and 'dark; shown on /chainle_dev' not in html


def test_the_tab_works_when_the_word_lists_fail(client, rounds, monkeypatch):
    # tiny's own database fails every statement, so the word lists can't load.
    _play(client, IPS[:3], R1)
    html = client.get('/chainle_admin?tab=rounds', headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert "Couldn't read the word lists" in html and 'SNOW + FOOT' in html


def test_the_tab_says_when_the_table_is_missing(client, tiny, monkeypatch):
    tiny(fail='chainle_boards')
    html = client.get('/chainle_admin?tab=rounds', headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert 'Has mysql_tables/chainle_boards.sql been run?' in html
    assert 'id="reports-tab"' in html


# --- dark, the memo, and failures --------------------------------------------

def test_rounds_ship_dark():
    # Dark until James decides to go live (2026-10-28).
    assert rc.CHAINLE_ROUNDS_LIVE is False


def test_while_dark_rounds_are_logged_and_none_offered(client, rounds, monkeypatch):
    monkeypatch.setattr(rc, 'CHAINLE_ROUNDS_LIVE', False)
    _play(client, IPS[:3], R1)
    assert len(rounds.players()) == 3
    assert _sets(client) == []
    html = client.get('/chainle').get_data(as_text=True)
    assert 'id="chn-offer"' not in html and FAQ_Q not in html
    assert "action: 'log_round'" in html


def test_the_page_carries_the_offer_once_live(client, rounds):
    html = client.get('/chainle').get_data(as_text=True)
    assert 'id="chn-offer"' in html and FAQ_Q in html
    # Credit to the players who typed them, as on /ribbit.
    assert "Other players already entered today's round" in html
    assert 'Players entered more than one round' in html
    assert 'chainleSolverState_v1' in html and '"/chainle"' in html
    assert '<meta name="robots" content="noindex' not in html and '<p class="chn-dev-note">' not in html


def test_the_rounds_are_read_at_most_once_a_minute(client, rounds, monkeypatch):
    monkeypatch.setattr(rc, 'ROUNDS_CACHE_SECONDS', 60)
    _play(client, IPS[:3], R1)
    assert _sets(client) == _sets(client) == [_crowd((1, R1))]
    assert rounds.reads == 1


def test_a_stale_memo_is_kept_while_it_is_read_again(rounds, monkeypatch):
    rc._sets_cache[DAY] = (time.monotonic() - 1, [{'site': '', 'rounds': {'1': ['OLD', 'SET']}}])

    def slow(first, last):
        # While this read runs, other requests get the stale sets.
        assert rc._sets_cache[DAY][1] == [{'site': '', 'rounds': {'1': ['OLD', 'SET']}}]
        assert rc._sets_cache[DAY][0] > time.monotonic()
        return []

    monkeypatch.setattr(rc, 'rows_on', slow)
    assert rc._sets_for(DAY) == []


def test_a_failed_read_costs_the_rounds_never_the_page(client, rounds, monkeypatch):
    fired = []
    monkeypatch.setattr(rc.alerts, 'alert_throttled', lambda check, **fields: fired.append(check))

    def broken(first, last):
        raise RuntimeError('no table')

    monkeypatch.setattr(rc, 'rows_on', broken)
    assert _sets(client) == []
    assert client.get('/chainle').status_code == 200
    assert fired == ['chainle_board_read_failed']


def test_a_missing_table_costs_the_round_never_the_request(client, tiny, monkeypatch):
    fired = []
    monkeypatch.setattr(rc.alerts, 'alert_throttled', lambda check, **fields: fired.append(check))
    monkeypatch.setattr(rc, '_today', lambda: DAY)
    tiny(fail='chainle_boards')
    _log(client, IPS[0], 1, R1)
    assert fired == ['chainle_board_log_failed']


def test_the_sql_writes_and_reads_the_right_columns(monkeypatch):
    db = _ScriptedDB()
    monkeypatch.setattr(rc, 'db_cursor', db)
    rc.store_round(DAY, 'v' * 32, 'p' * 32, 2, 'DOCTOR NURSE', -420, 'edited')
    sql, params = db.statements('INSERT INTO chainle_boards (played_on, voter_hash, prefix_hash')[0]
    assert 'ON DUPLICATE KEY UPDATE' in sql and 'new.clues' in sql
    assert params[:7] == (DAY, 'v' * 32, 'p' * 32, 2, 'DOCTOR NURSE', -420, 'edited')
    rc.rows_on(DAY, DAY)
    assert db.statements('FROM chainle_boards WHERE played_on BETWEEN')[0][1] == (DAY, DAY)
    rc.store_approval(DAY, 'chainle.io', {5: 'BIG', 1: 'FOOT SNOW'})
    approvals = db.statements('INSERT INTO chainle_boards (played_on, voter_hash, site')
    assert [p[3] for _sql, p in approvals] == [1, 5]
    assert all(p[1] == rc.APPROVAL and p[2] == 'chainle.io' and p[5] == rc.APPROVAL for _sql, p in approvals)
    rc.remove_approval(DAY, 'chainle.io')
    # The approval's own DELETE (a whole set replaces the last), then Undo's.
    assert [p for _sql, p in db.statements('AND site = %s AND source = %s')] == [(DAY, 'chainle.io', rc.APPROVAL)] * 2
    rc.remove_rounds(DAY, ['BIG', 'SNOW FOOT'])
    # Whichever order each player typed the words in.
    assert db.statements('AND clues IN (%s, %s, %s)')[0][1] == (DAY, rc.APPROVAL, 'BIG', 'FOOT SNOW', 'SNOW FOOT')
    rc.remove_players(DAY, ['a' * 32])
    assert db.statements('AND voter_hash IN (%s)')[0][1] == (DAY, rc.APPROVAL, 'a' * 32)
    assert db.commits == 5


# --- /chainle_dev ---------------------------------------------------------------

def test_chainle_dev_is_password_only(client):
    assert client.get('/chainle_dev').status_code == 401
    assert client.post('/chainle_dev', json={'action': 'todays_rounds', 'played_on': '2026-10-28'}).status_code == 401
    assert client.post('/chainle_dev', json={'clues': R1, 'kind': 'bridge'}).status_code == 401


def test_chainle_dev_shows_the_offer_while_dark_and_logs_none_of_your_rounds(client, rounds, monkeypatch):
    monkeypatch.setattr(rc, 'CHAINLE_ROUNDS_LIVE', False)
    headers = _admin_headers(monkeypatch)
    visits = []
    monkeypatch.setattr(wordgames, 'log_page_visit', visits.append)
    html = client.get('/chainle_dev', headers=headers).get_data(as_text=True)
    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert '<p class="chn-dev-note">' in html and 'id="chn-offer"' in html
    assert '"/chainle_dev"' in html and 'chainleDevState_v1' in html and FAQ_Q in html
    assert 'WebApplication' not in html
    assert visits == []
    _play(client, IPS[:3], R1)
    assert _sets(client) == []
    assert _sets(client, path='/chainle_dev', headers=headers) == [_crowd((1, R1))]
    _log(client, IPS[5], 2, R2, path='/chainle_dev', headers=headers)
    assert len(rounds.players()) == 3


def test_chainle_dev_loads_no_ads(client, rounds, slots, monkeypatch):
    headers = _admin_headers(monkeypatch)
    page = client.get('/chainle_dev', base_url=LIVE, headers=headers).get_data(as_text=True)
    assert UNIT not in page and LOADER not in page


def test_chainle_dev_reports_are_tagged_dev(client, rounds, monkeypatch):
    headers = _admin_headers(monkeypatch)
    votes = []
    monkeypatch.setattr(rc.CHAINLE_CROWD, 'record_vote', lambda *args: votes.append(args))
    body = {'action': 'refuse', 'word': 'ball', 'clues': ['snow', 'foot'], 'kind': 'bridge'}
    assert client.post('/chainle_dev', json=body, headers=headers).get_json()['result'] == 'recorded'
    assert client.post('/chainle', json=body).get_json()['result'] == 'recorded'
    assert [vote[3] for vote in votes] == ['dev_bridge', 'bridge']


def test_the_faq_entry_shows_only_where_rounds_are_on(client, rounds, monkeypatch):
    monkeypatch.setattr(rc, 'CHAINLE_ROUNDS_LIVE', False)
    headers = _admin_headers(monkeypatch)
    for path, kw, shown in (('/chainle', {}, False), ('/chainle_dev', {'headers': headers}, True)):
        html = client.get(path, **kw).get_data(as_text=True)
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
        faq = next(d for d in map(json.loads, blocks) if d.get('@type') == 'FAQPage')
        assert (FAQ_Q in [q['name'] for q in faq['mainEntity']]) is shown
        assert (FAQ_Q in html) is shown


def test_players_are_never_told_how_rounds_are_chosen(client, rounds):
    html = client.get('/chainle').get_data(as_text=True).lower()
    for hint in ('for everyone', 'players agreed', 'three players', '3 players', 'serve_crowd',
                 'link_min', 'one per network', 'more than half'):
        assert hint not in html, f'page hints at how rounds are chosen: {hint!r}'
