"""Regression tests for the pure word / scoring logic.

Values are pinned to the current dataset so a refactor can't silently change
the answers your users see. These import ``functions`` / ``data`` directly and
touch no external services.
"""

import helpers
from data import df, words
from functions import all_words, wordle


# --- all_words -------------------------------------------------------------

def test_wordiply_orders_longest_first():
    out = all_words.wordiply_solver("zz", words, 5)
    assert out == [
        "puzzleheadednesses",
        "puzzleheadedness",
        "quizzicalities",
        "bedazzlements",
        "embezzlements",
    ]


def test_wordiply_empty_search_returns_empty():
    assert all_words.wordiply_solver("", words) == []


def test_length_score_curve():
    assert [all_words.length_score(n) for n in (4, 5, 6, 7, 8)] == [2, 4, 6, 12, 15]


def test_is_pangram_revamp_needs_all_required_letters():
    assert all_words.is_pangram_revamp("abcx", set("abc")) == 7
    assert all_words.is_pangram_revamp("abx", set("abc")) == 0


def test_unused_letters():
    assert all_words.unused_letters("abc", "") == ["defghijklmnopqrstuvwxyz"]


def test_filter_words_all_respects_constraints():
    out = all_words.filter_words_all(
        required_letters="q",
        forbidden_letters="z",
        first_letter="",
        sort_order="A-Z",
        list_len=5,
        words=words,
        min_length=4,
        max_length=5,
    )
    assert len(out) <= 5
    for word in out:
        assert "q" in word and "z" not in word
        assert 4 <= len(word) <= 5
    assert out == sorted(out)  # A-Z ordering


# --- smush -------------------------------------------------------------------

# The 2026-07-08 board from hankgreen.com/smush: center L, pangram "employing".
SMUSH_FRESH = {"e": 5, "g": 5, "i": 5, "m": 5, "n": 5, "o": 5, "p": 5, "y": 5}


def test_smush_word_score_sums_letter_values():
    assert all_words.smush_word_score("camouflage") == 18
    assert all_words.smush_word_score("eel") == 3


def test_smush_pangram_first_word_multiplier():
    results, total, status = all_words.smush_solver(
        "l", SMUSH_FRESH, "", True, words)
    assert status == "affordable"
    assert total > 0
    top = results[0]
    assert top["word"] == "employing"
    assert top["pangram"] is True
    assert top["mult"] == 5  # 1 + 4 for a first-word pangram
    assert top["pts"] == top["base"] * 5
    # server order is points-desc
    pts = [r["pts"] for r in results]
    assert pts == sorted(pts, reverse=True)


def test_smush_spicy_and_smush_bonuses_add():
    # EEL spends both remaining Es: +2 spicy (two spicy uses) +1 smush on top
    # of the base x1. The unused Z keeps EEL from counting as a pangram.
    results, _, _ = all_words.smush_solver("l", {"e": 2, "z": 5}, "e", False, words)
    eel = next(r for r in results if r["word"] == "eel")
    assert eel["spicy_uses"] == 2
    assert eel["smushes"] == 1
    assert eel["mult"] == 4
    assert eel["pts"] == 3 * 4


def test_smush_respects_remaining_uses():
    # With a single E left, EEL (two Es) must be filtered out but ELL stays.
    results, _, _ = all_words.smush_solver("l", {"e": 1}, "", False, words)
    found = {r["word"] for r in results}
    assert "eel" not in found
    assert "ell" in found


def test_smush_pangram_out_of_reach_when_letters_spent():
    depleted = dict(SMUSH_FRESH, o=0)
    _, _, status = all_words.smush_solver("l", depleted, "", False, words)
    assert status == "out_of_reach"


def test_smush_exclude_drops_rejected_words_entirely():
    # "employing" is this board's only pangram: rejecting it must remove it
    # from the results/totals AND flip the pangram status.
    results, total, status = all_words.smush_solver(
        "l", SMUSH_FRESH, "", True, words, exclude=["employing"])
    baseline_total = all_words.smush_solver("l", SMUSH_FRESH, "", True, words)[1]
    assert all(r["word"] != "employing" for r in results)
    assert total == baseline_total - 1
    assert status == "none"


def test_smush_played_words_are_dropped_and_pangram_reports_found():
    results, _, status = all_words.smush_solver(
        "l", SMUSH_FRESH, "", False, words, played=["employing", "mole"])
    found = {r["word"] for r in results}
    assert "employing" not in found and "mole" not in found
    assert status == "found"  # the played pangram beats affordable/out_of_reach

    # A played non-pangram leaves the pangram still available.
    _, _, status = all_words.smush_solver(
        "l", SMUSH_FRESH, "", False, words, played=["mole"])
    assert status == "affordable"

    # A played word must match the board letters exactly to be the pangram;
    # a superset (only possible via a hand-crafted request) doesn't count.
    _, _, status = all_words.smush_solver(
        "l", SMUSH_FRESH, "", False, words, played=["employings"])
    assert status == "affordable"


def test_smush_center_letter_is_free_and_required():
    results, _, _ = all_words.smush_solver("l", {"e": 1}, "", False, words)
    for r in results:
        assert "l" in r["word"]
        assert "l" not in r["cost"]  # the gold center never costs uses


def _smush_plan(center, outer, word_list):
    results, _, _ = all_words.smush_solver(
        center, outer, "", False, word_list, list_len=None)
    return all_words.smush_all_plan(results, outer)


def test_smush_all_plan_complete_exactly_exhausts_the_board():
    # BAB (b×2) + ACA (c×1) is the only exact fit. CAB is the pangram here,
    # but seeding it would strand a b use, so the planner must give it up
    # rather than lose the clean plate.
    plan, leftover = _smush_plan("a", {"b": 2, "c": 1}, {"bab", "aca", "cab"})
    assert leftover == {}
    assert sorted(r["word"] for r in plan) == ["aca", "bab"]


def test_smush_all_plan_puts_the_pangram_first_when_it_fits():
    # CABBA (b×2 c×1) is the pangram; ACA finishes the plate, PERFECT setup.
    plan, leftover = _smush_plan("a", {"b": 2, "c": 2}, {"cabba", "aca"})
    assert leftover == {}
    assert [r["word"] for r in plan] == ["cabba", "aca"]
    assert plan[0]["pangram"] is True


def test_smush_all_plan_reports_stranded_letters():
    # No word touches z, so it can never be flattened; b and c still plan out.
    plan, leftover = _smush_plan("a", {"b": 2, "c": 1, "z": 5}, {"bab", "aca", "cab"})
    assert leftover == {"z": 5}
    assert sorted(r["word"] for r in plan) == ["aca", "bab"]


def test_smush_all_plan_flat_board_is_an_empty_complete_plan():
    plan, leftover = _smush_plan("a", {"b": 0, "c": 0}, {"bab", "aca"})
    assert plan == [] and leftover == {}


def test_smush_all_plan_full_board_accounting_is_exact():
    results, _, _ = all_words.smush_solver(
        "l", SMUSH_FRESH, "", True, words, list_len=None)
    plan, leftover = all_words.smush_all_plan(results, SMUSH_FRESH)
    plan_words = [r["word"] for r in plan]
    assert len(set(plan_words)) == len(plan_words)  # no word played twice
    spent = {}
    for r in plan:
        for l, n in r["cost"].items():
            spent[l] = spent.get(l, 0) + n
    # spent + stranded always reconciles to the starting uses per letter
    for l, u in SMUSH_FRESH.items():
        assert spent.get(l, 0) + leftover.get(l, 0) == u
    # a fresh board over the full dictionary should be fully smushable,
    # with the pangram seeded up front for the PERFECT bonus
    assert leftover == {}
    assert plan[0]["pangram"] is True


# --- wordle ----------------------------------------------------------------

def test_wordle_opener_is_stable():
    result = wordle.wordle_solver_split_revamp(df, [])
    assert result[0] == "Pick 1: arose"
    assert result[5].startswith("Options remaining: 5710/5710")


def test_find_word_with_letters_crane():
    out = wordle.find_word_with_letters(df, "crane")
    assert len(out) == 5
    assert out[0] == "Pick 1: caner (5 match)"


# --- helpers ---------------------------------------------------------------

def test_schema_data_has_required_keys():
    schema = helpers.make_schema_data("Name", "Desc", "https://example.com")
    assert schema["@type"] == "WebApplication"
    assert schema["name"] == "Name"
    for key in ("@context", "url", "offers", "creator", "operatingSystem"):
        assert key in schema
