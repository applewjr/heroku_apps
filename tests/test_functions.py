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
