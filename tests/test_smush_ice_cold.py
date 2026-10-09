"""ICE COLD planner (functions/smush_ice_cold.py).

Most boards here are tiny hand-built ones over the eight letter slots a..h,
so each solve takes milliseconds and every expected chance can be worked out
by hand. Words are given as solver-shaped rows: only their cost (the uses of
each letter), popularity and pangram flag matter to the planner.
"""

import numpy as np
import pytest

from functions import smush_ice_cold as ice

LETTERS = tuple("abcdefgh")
A, B, C, D = 0, 1, 2, 3


def row(word, cost, pop=4.0, pangram=False):
    return {"word": word, "cost": cost, "pop": pop, "base": len(word), "pangram": pangram}


def uses(**counts):
    return tuple(counts.get(l, 0) for l in LETTERS)


def solve(rows, L, accepted=None):
    """Table and pool for these rows, every word known to be accepted unless
    `accepted` says otherwise - so the chances are pure spice luck."""
    accepted = {r["word"] for r in rows} if accepted is None else accepted
    groups = ice.make_groups(rows, LETTERS, accepted=frozenset(accepted))
    return ice.solve_box(L, groups), ice.Pool(groups)


# --- the endgame -------------------------------------------------------------

def test_two_solo_letters_finish_whatever_the_spice_does():
    # A:1 and B:1, each with its own solo word: play the other letter's word,
    # the spice has nowhere left but none, and the last solo finishes it.
    table, _ = solve([row("aaz", {"a": 1}), row("bbz", {"b": 1})], uses(a=1, b=1))
    assert table.value(uses(a=1, b=1), A) == pytest.approx(1.0)
    assert table.value(uses(a=1, b=1), B) == pytest.approx(1.0)


def test_one_finisher_needs_the_spice_on_it():
    # Only A has a solo word (KAKA, two As). With the spice on A, the closer
    # BC clears everything else and KAKA finishes; with it on B or C, nothing
    # avoids the spice without breaking KAKA's exact count.
    rows = [row("kaka", {"a": 2}), row("bc", {"b": 1, "c": 1})]
    L = uses(a=2, b=1, c=1)
    table, _ = solve(rows, L)
    assert table.value(L, A) == pytest.approx(1.0)
    assert table.value(L, B) == pytest.approx(0.0)
    assert table.value(L, C) == pytest.approx(0.0)


def test_a_waiting_move_is_worth_half():
    # A:2 B:1 C:1 D:1 with the spice on B. Spending D on its solo word leaves
    # A, B, C; the spice can't stay on B, so it lands on A or C - A lets the
    # closer BC and then KAKA finish, C doesn't. Playing KAKA first is no
    # better: it only finishes if the spice lands on D. Either way, 1 in 2.
    rows = [row("kaka", {"a": 2}), row("bc", {"b": 1, "c": 1}),
            row("dz", {"d": 1}), row("bcd", {"b": 1, "c": 1, "d": 1})]
    L = uses(a=2, b=1, c=1, d=1)
    table, _ = solve(rows, L)
    assert table.value(L, B) == pytest.approx(0.5, abs=0.01)
    assert table.value(L, A) == pytest.approx(1.0)   # BCD closes at once


def test_the_spice_free_turn_needs_an_exact_solo_word():
    # One tile left and no spice: only a word spending exactly its last uses
    # wins. A partial one hands the spice straight back to that same tile.
    exact, _ = solve([row("aaz", {"a": 1}), row("aa", {"a": 2})], uses(a=2))
    assert exact.value(uses(a=2), None) == pytest.approx(1.0)
    partial, _ = solve([row("aaz", {"a": 1})], uses(a=2))
    assert partial.value(uses(a=2), None) == pytest.approx(0.0)


def test_a_word_is_never_planned_twice():
    # A:2 B:1, spice on B. With one A-solo word, clearing A needs it twice -
    # the game won't allow that, so neither may the plan. A second A-solo
    # word makes the same line legal.
    one = [row("az", {"a": 1}), row("bz", {"b": 1})]
    table, _ = solve(one, uses(a=2, b=1))
    assert table.value(uses(a=2, b=1), B) == pytest.approx(0.0)

    two = one + [row("aay", {"a": 1})]
    table, _ = solve(two, uses(a=2, b=1))
    assert table.value(uses(a=2, b=1), B) == pytest.approx(1.0)


def test_two_uses_of_a_group_need_two_accepted_words():
    # A:1 G:4, spice on A. G drains through its two G-solo words (GIG, IGG:
    # two Gs each), one before and one after A's solo word - so all three
    # words must be accepted. Counting the G group's "at least one of two"
    # odds for each use would claim about 0.97 * 0.83 * 0.97 instead.
    rows = [row("gig", {"g": 2}), row("igg", {"g": 2}), row("aaz", {"a": 1})]
    L = uses(a=1, g=4)
    table, _ = solve(rows, L, accepted=set())
    solo = ice.ACCEPT_SOLO[0]
    assert table.value(L, A) == pytest.approx(solo ** 3, abs=0.01)


def test_refusal_odds_lower_the_chance():
    # Same two-solo endgame, but nobody has played these words yet: the plan
    # needs both solo words accepted, so the chance is their priors' product.
    rows = [row("aaz", {"a": 1}, pop=4.0), row("bbz", {"b": 1}, pop=4.0)]
    table, _ = solve(rows, uses(a=1, b=1), accepted=set())
    solo = ice.ACCEPT_SOLO[0]
    assert table.value(uses(a=1, b=1), A) == pytest.approx(solo * solo, abs=0.01)


# --- words and acceptance ----------------------------------------------------

def test_pangrams_are_never_planned():
    groups = ice.make_groups([row("abcdefghz", dict.fromkeys(LETTERS, 1), pangram=True),
                              row("az", {"a": 1})], LETTERS)
    assert [g.words for g in groups] == [("az",)]


def test_a_group_tries_its_most_common_word_first():
    groups = ice.make_groups([row("obscure", {"a": 1}, pop=0.0),
                              row("common", {"a": 1}, pop=5.0)], LETTERS)
    [g] = groups
    assert g.words == ("common", "obscure")
    # at least one of the two accepted
    expected = 1 - (1 - ice.ACCEPT_SOLO[0]) * (1 - ice.ACCEPT_SOLO[4])
    assert g.accept == pytest.approx(expected)


def test_played_evidence_beats_x_votes():
    [g] = ice.make_groups([row("tit", {"a": 1})], LETTERS,
                          accepted=frozenset({"tit"}), invalid_counts={"tit": 3})
    assert g.accept == pytest.approx(1.0)


@pytest.mark.parametrize("players, factor", [(0, 1.0), (1, 0.4), (2, 0.15), (3, 0.05), (9, 0.05)])
def test_x_votes_scale_a_words_odds(players, factor):
    prior = ice.ACCEPT_MULTI[0]
    assert ice.word_accept(4.0, solo=False, invalid_players=players) == pytest.approx(prior * factor)


def test_solo_words_have_their_own_priors():
    assert ice.word_accept(4.0, solo=True) == ice.ACCEPT_SOLO[0]
    assert ice.word_accept(0.0, solo=False) == ice.ACCEPT_MULTI[4]
    assert ice.word_accept(2.8, solo=False) == ice.ACCEPT_MULTI[1]


# --- ranking ------------------------------------------------------------------

def _flat_table(L, overrides):
    """A table that says 1.0 everywhere except `overrides` {(state, col): v}."""
    table = ice.Table(L, np.ones((int(np.prod([l + 1 for l in L])), ice.N_LETTERS + 1), np.float32))
    for (state, col), v in overrides.items():
        table.V[table.index(state), col] = v
    return table


def test_equal_chances_offer_the_most_common_word_first():
    L = uses(a=1, b=1, c=1)
    pool = ice.Pool(ice.make_groups([row("bcq", {"b": 1, "c": 1}, pop=0.0),
                                     row("bee", {"b": 1}, pop=4.5),
                                     row("cee", {"c": 1}, pop=2.5)], LETTERS))
    moves = ice.rank_moves(_flat_table(L, {}), pool, L, A)
    assert [m.group.words[0] for m in moves][0] == "bee"


def test_a_long_shot_only_goes_first_when_it_buys_enough():
    # BCQ (rare) finishes for sure; BEE (common) leaves a 99% position. Trying
    # BEE first costs about 0.3% - offer it first. At 95% it costs more than
    # NEAR_BEST, so the long shot goes first: a refusal is a free retry.
    L = uses(a=1, b=1, c=1)
    rows = [row("bcq", {"b": 1, "c": 1}, pop=0.0), row("bee", {"b": 1}, pop=4.5)]
    pool = ice.Pool(ice.make_groups(rows, LETTERS))
    after_bee = (uses(a=1, c=1), C)          # the spice must land on C
    after_bcq = (uses(a=1), ice.NULL)        # only A left: the spice-free turn
    close = _flat_table(L, {after_bee: 0.99, after_bcq: 1.0})
    assert ice.rank_moves(close, pool, L, A)[0].group.words[0] == "bee"
    far = _flat_table(L, {after_bee: 0.95, after_bcq: 1.0})
    assert ice.rank_moves(far, pool, L, A)[0].group.words[0] == "bcq"


def test_moves_never_touch_the_spicy_letter():
    rows = [row("ab", {"a": 1, "b": 1}), row("az", {"a": 1}), row("bz", {"b": 1}),
            row("cz", {"c": 1}), row("bc", {"b": 1, "c": 1})]
    L = uses(a=1, b=1, c=1)
    table, pool = solve(rows, L)
    for s in (A, B, C):
        for m in ice.rank_moves(table, pool, L, s):
            assert m.group.sig[s] == 0


# --- the payload --------------------------------------------------------------

def test_unknown_spice_offers_a_word_for_every_living_tile():
    table, pool = solve([row("aaz", {"a": 1}), row("bbz", {"b": 1})], uses(a=1, b=1))
    plan = ice.plan_payload(table, pool, uses(a=1, b=1), "unknown", LETTERS)
    assert plan["status"] == "ready" and plan["spice"] == "unknown"
    assert plan["by_spice"]["a"]["word"] == "bbz"     # spice on A: play B's word
    assert plan["by_spice"]["b"]["word"] == "aaz"
    assert plan["p_success"] == pytest.approx(1.0)


def test_known_spice_gives_the_word_and_its_try_order():
    rows = [row("bbz", {"b": 1}, pop=4.0), row("bob", {"b": 1}, pop=3.0), row("aaz", {"a": 1})]
    table, pool = solve(rows, uses(a=1, b=1))
    plan = ice.plan_payload(table, pool, uses(a=1, b=1), A, LETTERS)
    assert [w["word"] for w in plan["next"]] == ["bbz", "bob"]
    first = plan["next"][0]
    # shaped like a /smush row, so the page's ✓ Played can spend it
    assert first["cost"] == {"b": 1} and first["smushes"] == 1 and first["mult"] == 2
    assert first["pts"] == first["base"] * 2 and first["spicy_uses"] == 0
    assert set(plan["finish_words"]) <= {"bbz", "bob", "aaz"}


def test_a_lost_board_says_why():
    # Q only appears in the pangram, so it can never be spent ICE COLD.
    rows = [row("pangramq", dict.fromkeys(LETTERS, 1), pangram=True), row("az", {"a": 1})]
    table, pool = solve(rows, uses(a=1, h=1))
    plan = ice.plan_payload(table, pool, uses(a=1, h=1), A, LETTERS)
    assert plan["status"] == "impossible" and plan["p_success"] == 0
    assert "H" in plan["reason"]


def test_a_letter_with_too_few_words_is_named():
    # H has two uses left but only one word that spends it.
    table, pool = solve([row("az", {"a": 1}), row("hz", {"h": 1})], uses(a=1, h=2))
    plan = ice.plan_payload(table, pool, uses(a=1, h=2), A, LETTERS)
    assert plan["status"] == "impossible"
    assert plan["reason"].startswith("H has 2 uses left")


def test_a_last_tile_without_an_exact_solo_word_says_so():
    # A:2 alone in the spice-free turn, but A's only solo word spends one.
    table, pool = solve([row("az", {"a": 1})], uses(a=2))
    plan = ice.plan_payload(table, pool, uses(a=2), None, LETTERS)
    assert plan["status"] == "impossible"
    assert plan["reason"].startswith("Only A is left, with 2 uses")


def test_a_flat_board_is_done():
    table, pool = solve([row("az", {"a": 1})], uses(a=1))
    assert ice.plan_payload(table, pool, uses(), None, LETTERS)["status"] == "done"


# --- the shared table -----------------------------------------------------------

class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _cache(**kw):
    jobs = []
    clock = _Clock()
    built = []

    def fake_solve(root, groups):
        built.append(root)
        return "table-%d" % len(built)

    cache = ice.TableCache(solve=fake_solve, spawn=jobs.append, clock=clock, **kw)
    return cache, jobs, clock, built


def test_the_first_request_starts_one_build():
    cache, jobs, clock, built = _cache()
    pool = lambda: ice.Pool([])
    assert cache.get("l:egimnopy", "f1", pool)[1] == "building"
    assert cache.get("l:egimnopy", "f1", pool)[1] == "building"
    assert len(jobs) == 1                    # one thread, not one per request
    jobs[0]()                                # the thread runs
    table, status, _ = cache.get("l:egimnopy", "f1", pool)
    assert (table, status) == ("table-1", "ready")
    assert built == [ice.FULL_ROOT]


def test_newer_evidence_rebuilds_but_not_too_often():
    cache, jobs, clock, _ = _cache(rebuild_gap=900)
    pool = lambda: ice.Pool([])
    cache.get("k", "f1", pool); jobs.pop()()
    clock.now += 60
    assert cache.get("k", "f2", pool)[:2] == ("table-1", "ready")   # old table keeps answering
    assert not jobs                                                   # too soon to rebuild
    clock.now += 900
    assert cache.get("k", "f2", pool)[:2] == ("table-1", "ready")
    assert len(jobs) == 1
    jobs.pop()()
    assert cache.get("k", "f2", pool)[0] == "table-2"


def test_a_failed_build_waits_before_retrying():
    errors = []

    def broken(root, groups):
        raise MemoryError("no room")

    jobs, clock = [], _Clock()
    cache = ice.TableCache(solve=broken, spawn=jobs.append, clock=clock,
                           on_error=errors.append, retry_after=600)
    pool = lambda: ice.Pool([])
    cache.get("k", "f", pool); jobs.pop()()
    assert isinstance(errors[0], MemoryError)
    assert cache.get("k", "f", pool)[1] == "failed"
    clock.now += 601
    assert cache.get("k", "f", pool)[1] == "building"


def test_builds_are_capped_per_day():
    cache, jobs, clock, _ = _cache(max_builds_per_day=1)
    pool = lambda: ice.Pool([])
    cache.get("one", "f", pool); jobs.pop()()
    assert cache.get("two", "f", pool)[1] == "capped"


# --- a real board ---------------------------------------------------------------

def test_a_real_mid_game_board_plans_without_touching_spice():
    # The 2026-07-08 board (center L), late in a game: every word the plan
    # offers fits the uses left and avoids whichever tile is spicy.
    from data import word_pop, words
    from functions import all_words
    outer = {"e": 2, "g": 1, "i": 1, "m": 1, "n": 1, "o": 1, "p": 1, "y": 1}
    letters = tuple(sorted(outer))
    L = tuple(outer[l] for l in letters)
    results, _, _ = all_words.smush_solver("l", outer, "", False, words,
                                           list_len=None, popularity=word_pop)
    pool = ice.Pool(ice.make_groups(results, letters))
    table = ice.solve_box(L, pool.groups)
    plan = ice.plan_payload(table, pool, L, "unknown", letters)
    for tile, w in plan["by_spice"].items():
        assert tile not in w["cost"]
        assert all(n <= outer[l] for l, n in w["cost"].items())
