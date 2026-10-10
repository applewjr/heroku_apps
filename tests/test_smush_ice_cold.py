"""ICE COLD planner (functions/smush_ice_cold.py).

Most boards here are tiny hand-built ones over the eight letter slots a..h,
so each solve takes milliseconds and every expected chance and point total
can be worked out by hand. Words are given as solver-shaped rows: only their
cost (the uses of each letter), base score, popularity and pangram flag
matter to the planner. A word's base defaults to its length.
"""

import numpy as np
import pytest

from functions import smush_ice_cold as ice

LETTERS = tuple("abcdefgh")
A, B, C, D = 0, 1, 2, 3


def row(word, cost, pop=4.0, pangram=False, base=None):
    return {"word": word, "cost": cost, "pop": pop, "pangram": pangram,
            "base": len(word) if base is None else base}


def uses(**counts):
    return tuple(counts.get(l, 0) for l in LETTERS)


def solve(rows, L, accepted=None):
    """Table and pool for these rows, every word known to be accepted unless
    `accepted` says otherwise - so the chances are pure spice luck."""
    accepted = {r["word"] for r in rows} if accepted is None else accepted
    groups = ice.make_groups(rows, LETTERS, accepted=frozenset(accepted))
    return ice.solve_box(L, groups), ice.Pool(groups)


@pytest.fixture
def no_hoard(monkeypatch):
    """Point totals without the per-word Word Hoard share, so they add up by hand."""
    monkeypatch.setattr(ice, "WORD_HOARD_PER_WORD", 0.0)


# --- the endgame: chances ------------------------------------------------------

def test_two_solo_letters_finish_whatever_the_spice_does(no_hoard):
    # A:1 and B:1, each with its own solo word: play the other letter's word,
    # the spice has nowhere left but none, and the last solo finishes it.
    # Points: two 3-point words that each smush their letter (x2), plus the
    # clean finish.
    table, _ = solve([row("aaz", {"a": 1}), row("bbz", {"b": 1})], uses(a=1, b=1))
    for spice in (A, B):
        assert table.chance(uses(a=1, b=1), spice) == pytest.approx(1.0)
        assert table.points(uses(a=1, b=1), spice) == pytest.approx(6 + 6 + ice.CLEAN_BONUS)


def test_one_finisher_needs_the_spice_on_it():
    # Only A has a solo word (KAKA, two As). With the spice on A, the closer
    # BC clears everything else and KAKA finishes; with it on B or C, nothing
    # avoids the spice without breaking KAKA's exact count.
    rows = [row("kaka", {"a": 2}), row("bc", {"b": 1, "c": 1})]
    L = uses(a=2, b=1, c=1)
    table, _ = solve(rows, L)
    assert table.chance(L, A) == pytest.approx(1.0)
    assert table.chance(L, B) == pytest.approx(0.0)
    assert table.chance(L, C) == pytest.approx(0.0)


def test_a_waiting_move_is_worth_half():
    # A:2 B:1 C:1 D:1 with the spice on B. Spending D on its solo word leaves
    # A, B, C; the spice can't stay on B, so it lands on A or C - A lets the
    # closer BC and then KAKA finish, C doesn't. Playing KAKA first is no
    # better: it only finishes if the spice lands on D. Either way, 1 in 2.
    rows = [row("kaka", {"a": 2}), row("bc", {"b": 1, "c": 1}),
            row("dz", {"d": 1}), row("bcd", {"b": 1, "c": 1, "d": 1})]
    L = uses(a=2, b=1, c=1, d=1)
    table, _ = solve(rows, L)
    assert table.chance(L, B) == pytest.approx(0.5, abs=0.01)
    assert table.chance(L, A) == pytest.approx(1.0)   # BCD closes at once


def test_the_spice_free_turn_needs_an_exact_solo_word():
    # One tile left and no spice: only a word spending exactly its last uses
    # wins. A partial one hands the spice straight back to that same tile.
    exact, _ = solve([row("aaz", {"a": 1}), row("aa", {"a": 2})], uses(a=2))
    assert exact.chance(uses(a=2), None) == pytest.approx(1.0)
    partial, _ = solve([row("aaz", {"a": 1})], uses(a=2))
    assert partial.chance(uses(a=2), None) == pytest.approx(0.0)


def test_a_word_is_never_planned_twice():
    # A:2 B:1, spice on B. With one A-solo word, clearing A needs it twice -
    # the game won't allow that, so neither may the plan. A second A-solo
    # word makes the same line legal.
    one = [row("az", {"a": 1}), row("bz", {"b": 1})]
    table, _ = solve(one, uses(a=2, b=1))
    assert table.chance(uses(a=2, b=1), B) == pytest.approx(0.0)

    two = one + [row("aay", {"a": 1})]
    table, _ = solve(two, uses(a=2, b=1))
    assert table.chance(uses(a=2, b=1), B) == pytest.approx(1.0)


def test_two_uses_of_a_group_need_two_accepted_words():
    # A:1 G:4, spice on A. G drains through its two G-solo words (GIG, IGG:
    # two Gs each), one before and one after A's solo word - so all three
    # words must be accepted. Counting the G group's "at least one of two"
    # odds for each use would claim about 0.97 * 0.83 * 0.97 instead.
    rows = [row("gig", {"g": 2}), row("igg", {"g": 2}), row("aaz", {"a": 1})]
    L = uses(a=1, g=4)
    table, _ = solve(rows, L, accepted=set())
    solo = ice.ACCEPT_SOLO[0]
    assert table.chance(L, A) == pytest.approx(solo ** 3, abs=0.01)


def test_refusal_odds_lower_the_chance_and_the_points(no_hoard):
    # Same two-solo endgame, but nobody has played these words yet: both solo
    # words have to be accepted for the clean finish, and each one's points
    # only count if Smush takes it.
    rows = [row("aaz", {"a": 1}, pop=4.0), row("bbz", {"b": 1}, pop=4.0)]
    table, _ = solve(rows, uses(a=1, b=1), accepted=set())
    solo = ice.ACCEPT_SOLO[0]
    assert table.chance(uses(a=1, b=1), A) == pytest.approx(solo * solo, abs=0.01)
    last = solo * (6 + ice.CLEAN_BONUS)
    assert table.points(uses(a=1, b=1), A) == pytest.approx(solo * (6 + last), abs=0.5)


# --- points ----------------------------------------------------------------------

def test_one_word_smushing_three_letters_beats_three_that_smush_one(no_hoard):
    # A:1 B:1 C:1 D:1, spice on D. ABC (base 9) spends A, B and C at once for
    # 9 x 4 = 36; the solo words for them (base 3 each) make 3 x 2 = 6 apiece.
    # Either way D's solo word then finishes the board.
    rows = [row("abc", {"a": 1, "b": 1, "c": 1}, base=9), row("az", {"a": 1}, base=3),
            row("bz", {"b": 1}, base=3), row("cz", {"c": 1}, base=3), row("dz", {"d": 1}, base=3)]
    L = uses(a=1, b=1, c=1, d=1)
    table, pool = solve(rows, L)
    plan = ice.plan_payload(table, pool, L, D, LETTERS)
    assert plan["next"][0]["word"] == "abc"
    assert plan["next"][0]["pts"] == 36
    assert plan["expected_points"] == pytest.approx(36 + 6 + ice.CLEAN_BONUS)


@pytest.mark.parametrize("big_base, closer_first", [(20, True), (50, False)])
def test_a_clean_finish_is_worth_its_75(no_hoard, big_base, closer_first):
    # A:1 B:1 C:1, spice on A. The closer BC (6) then AZ (4) finishes clean:
    # 6 + 4 + 75 = 85. BQ alone scores big_base x 2, but leaves C with no solo
    # word, so the board can't be cleared: 2 x big_base + 4 after AZ. At 20
    # that's 44 and the clean line wins; at 50 it's 104, and the plan takes
    # the points - which is what playing for points means.
    rows = [row("bc", {"b": 1, "c": 1}, base=2), row("az", {"a": 1}, base=2),
            row("bq", {"b": 1}, base=big_base)]
    L = uses(a=1, b=1, c=1)
    table, pool = solve(rows, L)
    plan = ice.plan_payload(table, pool, L, A, LETTERS)
    assert plan["next"][0]["word"] == ("bc" if closer_first else "bq")
    assert plan["p_success"] == (1.0 if closer_first else 0.0)
    assert table.chance(L, A) == (1.0 if closer_first else 0.0)


def test_a_groups_points_follow_its_try_order():
    # The common word is tried first; the rarer, longer one only if Smush
    # refuses it, so the expected base leans to the common word's.
    [g] = ice.make_groups([row("lul", {"a": 1}, pop=5.0, base=4),
                           row("lulled", {"a": 1}, pop=0.0, base=10)], LETTERS)
    a1, a2 = g.accepts
    expected = (a1 * 4 + (1 - a1) * a2 * 10) / (a1 + (1 - a1) * a2)
    assert g.expected_base == pytest.approx(expected)


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
    """A table worth 0 points everywhere except `overrides` {(state, col): points}."""
    size = int(np.prod([l + 1 for l in L]))
    table = ice.Table(L, np.zeros((size, ice.N_LETTERS + 1), np.float32),
                      np.zeros((size, ice.N_LETTERS + 1), np.float32))
    for (state, col), v in overrides.items():
        table.J[table.index(state), col] = v
    return table


def test_equal_values_offer_the_most_common_word_first():
    # BEE and CEE both score 6 and leave positions worth nothing: try the
    # likelier word first.
    L = uses(a=1, b=1, c=1)
    pool = ice.Pool(ice.make_groups([row("cee", {"c": 1}, pop=2.5),
                                     row("bee", {"b": 1}, pop=4.5)], LETTERS))
    moves = ice.rank_moves(_flat_table(L, {}), pool, L, A)
    assert moves[0].group.words[0] == "bee"


@pytest.mark.parametrize("bee_after, first", [(18.0, "bee"), (8.0, "bcq")])
def test_a_long_shot_only_goes_first_when_it_buys_enough(bee_after, first):
    # BCQ (rare: 29%) is worth 9 + 20 after; BEE (common) 6 + bee_after.
    # Trying BEE first costs a_rare * a_common * (difference): with BEE 5
    # behind that's about 1.2 points - within NEAR_BEST_POINTS, so offer it
    # first. 15 behind it costs 3.6, so the long shot goes first: a refusal
    # is a free retry.
    L = uses(a=1, b=1, c=1)
    rows = [row("bcq", {"b": 1, "c": 1}, pop=0.0), row("bee", {"b": 1}, pop=4.5)]
    pool = ice.Pool(ice.make_groups(rows, LETTERS))
    after_bee = (uses(a=1, c=1), C)          # the spice must land on C
    after_bcq = (uses(a=1), ice.NULL)        # only A left: the spice-free turn
    table = _flat_table(L, {after_bee: bee_after, after_bcq: 20.0})
    assert ice.rank_moves(table, pool, L, A)[0].group.words[0] == first


def test_long_shots_on_top_dont_let_any_likely_word_go_first(no_hoard):
    # Two long shots (5% each) top the order at 106, then DZ (83%, worth
    # 4 + 95) and BCDLONG (92%, worth 80 - it scores 80 now but gives up the
    # finish). Putting DZ first costs about half a point; BCDLONG would cost
    # about 15. Judged against the top two moves alone, both looked free and
    # the likelier, higher-scoring BCDLONG went first.
    L = uses(a=1, b=1, c=1, d=1)
    rows = [row("bxx", {"b": 1}, pop=0.0), row("cxx", {"c": 1}, pop=0.0),
            row("dz", {"d": 1}, base=2), row("bcdlong", {"b": 1, "c": 1, "d": 1}, base=20)]
    pool = ice.Pool(ice.make_groups(rows, LETTERS))
    after_b, after_c, after_d = uses(a=1, c=1, d=1), uses(a=1, b=1, d=1), uses(a=1, b=1, c=1)
    table = _flat_table(L, {(after_b, C): 100.0, (after_b, D): 100.0,
                            (after_c, B): 100.0, (after_c, D): 100.0,
                            (after_d, B): 95.0, (after_d, C): 95.0})
    moves = ice.rank_moves(table, pool, L, A)
    assert moves[0].group.words[0] == "dz"
    assert moves[0].value == pytest.approx(99.0)


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
    assert plan["expected_points"] > ice.CLEAN_BONUS
    assert plan["by_spice"]["a"]["exp"] == pytest.approx(plan["expected_points"], abs=0.2)


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


def test_with_no_word_left_at_all_the_board_is_impossible():
    # Q only appears in the pangram, so it can never be spent ICE COLD - and
    # with the spice on A there's nothing to play.
    rows = [row("pangramq", dict.fromkeys(LETTERS, 1), pangram=True), row("az", {"a": 1})]
    table, pool = solve(rows, uses(a=1, h=1))
    plan = ice.plan_payload(table, pool, uses(a=1, h=1), A, LETTERS)
    assert plan["status"] == "impossible" and plan["p_success"] == 0
    assert "H" in plan["reason"]


def test_a_lost_clean_finish_still_plays_for_points():
    # H has two uses left but only one word that spends it, so no clean
    # finish - yet HZ still scores, x5, and the n-uses rule mustn't hide it.
    table, pool = solve([row("az", {"a": 1}), row("hz", {"h": 1})], uses(a=1, h=2))
    plan = ice.plan_payload(table, pool, uses(a=1, h=2), A, LETTERS)
    assert plan["status"] == "ready" and plan["p_success"] == 0
    assert plan["next"][0]["word"] == "hz"
    assert plan["reason"].startswith("H has 2 uses left")


def test_a_last_tile_without_an_exact_solo_word_says_so():
    # A:2 alone in the spice-free turn, but A's only solo word spends one: it
    # still scores, but can't finish clean.
    table, pool = solve([row("az", {"a": 1})], uses(a=2))
    plan = ice.plan_payload(table, pool, uses(a=2), None, LETTERS)
    assert plan["p_success"] == 0 and plan["next"][0]["word"] == "az"
    assert plan["reason"].startswith("Only A is left, with 2 uses")


def test_a_flat_board_is_done():
    table, pool = solve([row("az", {"a": 1})], uses(a=1))
    plan = ice.plan_payload(table, pool, uses(), None, LETTERS)
    assert plan["status"] == "done" and plan["expected_points"] == ice.CLEAN_BONUS


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

def _real_board(outer):
    """The 2026-07-08 board (center L) at the given remaining uses."""
    from data import word_pop, words
    from functions import all_words
    letters = tuple(sorted(outer))
    L = tuple(outer[l] for l in letters)
    results, _, _ = all_words.smush_solver("l", outer, "", False, words,
                                           list_len=None, popularity=word_pop)
    return letters, L, ice.Pool(ice.make_groups(results, letters))


def test_a_real_mid_game_board_plans_without_touching_spice():
    # Late in a game: every word the plan offers fits the uses left and
    # avoids whichever tile is spicy.
    outer = {"e": 2, "g": 1, "i": 1, "m": 1, "n": 1, "o": 1, "p": 1, "y": 1}
    letters, L, pool = _real_board(outer)
    table = ice.solve_box(L, pool.groups)
    plan = ice.plan_payload(table, pool, L, "unknown", letters)
    for tile, w in plan["by_spice"].items():
        assert tile not in w["cost"]
        assert all(n <= outer[l] for l, n in w["cost"].items())
        assert 0.0 <= w["p"] <= 1.0 and w["exp"] >= w["pts"]


def test_a_compact_table_decides_as_well_as_a_full_precision_one():
    # The full board's table is stored as uint8. On a real position, playing
    # the move it picks - judged by the full-precision table - must cost next
    # to nothing against the full-precision table's own plan.
    outer = {"e": 3, "g": 2, "i": 2, "m": 2, "n": 2, "o": 2, "p": 2, "y": 2}
    letters, L, pool = _real_board(outer)
    full = ice.solve_box(L, pool.groups, compact=False)
    small = ice.solve_box(L, pool.groups, compact=True)
    assert small.J.dtype == np.uint8 and small.P.dtype == np.uint8
    for spice in range(len(letters)):
        moves = ice.rank_moves(full, pool, L, spice)
        if not moves:
            continue
        picked = ice.rank_moves(small, pool, L, spice)[0].group.sig
        best, _ = ice.plan_value(moves)
        tried = sorted(moves, key=lambda m: m.group.sig != picked)   # the pick first
        assert best - ice.plan_value(tried)[0] <= 0.5


def test_a_fallback_word_promises_no_clean_finish():
    # The n-uses rule leaves no move; the fallback's own points count, but the
    # table's plan after it could replay that word, so it promises nothing.
    table, pool = solve([row("az", {"a": 1}), row("hz", {"h": 1})], uses(a=1, h=2))
    [m] = ice.fallback_moves(pool, uses(a=1, h=2), A)
    assert m.group.words == ("hz",) and m.chance == 0.0
    assert ice.rank_moves(table, pool, uses(a=1, h=2), A) == []
