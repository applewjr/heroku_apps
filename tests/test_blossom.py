"""Blossom tests - the highest-traffic page.

Covers the real POST path, the session checkbox logic, and pins the solver's
output against the committed word list. No database required: the solver and
get_filtered_blossom_words fall back to the in-memory word list, and the route
tests stub the DB cursor so the path is hermetic.
"""

import re
from contextlib import contextmanager

import pytest

from data import words_blossom
from functions import all_words

# ---------------------------------------------------------------------------
# Pure solver / scoring, run against the committed dataset.
# Pinned values are for this fixed puzzle; they change only if the word list or
# the scoring logic changes (which is exactly what we want to catch).
# ---------------------------------------------------------------------------

# Puzzle: center 't', petals 'r a i n e', bonus petal 's'  ->  {t,r,a,i,n,e,s}
MUST, MAY, PETAL = "t", "raine", "s"
REQUIRED = set("traines")
FORBIDDEN = set("abcdefghijklmnopqrstuvwxyz") - REQUIRED

EXPECTED_TOTAL = 1114
EXPECTED_PANGRAMS = sorted([
    "anestri", "antisera", "antistress", "antsier", "arenites", "arsenite",
    "arsenites", "artiness", "artinesses", "attainers", "entertainers",
    "entertains", "entrainers", "entrains", "entreaties", "errantries",
    "inertias", "instanter", "intenerates", "interstate", "interstates",
    "interstrain", "interstrains", "intrastate", "intreats", "irateness",
    "iratenesses", "itinerants", "itineraries", "itinerates", "nastier",
    "nitrates", "rainiest", "ratanies", "ratines", "reattains", "reinitiates",
    "reinstate", "reinstates", "resinate", "resinates", "resistant",
    "resistants", "restrain", "restrainer", "restrainers", "restrains",
    "restraint", "restraints", "retainers", "retains", "retinas", "retirants",
    "retrains", "retsina", "retsinas", "sanitaries", "seatrain", "seatrains",
    "stainer", "stainers", "stannaries", "stearin", "stearine", "stearines",
    "stearins", "strainer", "strainers", "straiten", "straitens", "straitness",
    "straitnesses", "tanistries", "tanneries", "tearstain", "tearstains",
    "tenantries", "ternaries", "terrains", "tertians", "trainees", "trainers",
    "transient", "transients", "tristearin", "tristearins",
])


def _solve(list_len=25):
    return all_words.filter_words_blossom_revamp(
        MUST, MAY, PETAL, list_len, words_blossom
    )


def test_blossom_solver_pinned_output():
    _table, total, show_more, pangrams = _solve()
    assert total == EXPECTED_TOTAL
    assert show_more is True
    assert sorted(pangrams) == EXPECTED_PANGRAMS


def test_blossom_solver_filtering_invariants():
    table, _total, _show_more, pangrams = _solve()

    # Words shown in the rendered table must obey the puzzle rules.
    displayed = re.findall(r"/dictionary/([a-z]+)\"", table)
    assert displayed, "expected some words in the results table"
    for word in displayed:
        assert "t" in word, f"{word} missing the center letter"
        assert not (set(word) & FORBIDDEN), f"{word} contains a forbidden letter"
        assert len(word) >= 4

    # Every pangram must actually use all seven letters.
    for word in pangrams:
        assert REQUIRED.issubset(set(word)), f"{word} is not a real pangram"


def test_unused_letters_revamp():
    (result,) = all_words.unused_letters_revamp(MUST, MAY, PETAL)
    # Order is set-dependent (varies by run), so compare as a set.
    assert set(result) == FORBIDDEN
    assert len(result) == len(FORBIDDEN)


def test_filter_words_for_blossom_keeps_only_valid():
    sample = {"tree", "abcdefgh", "cat", "Hello!", "mississippi", "AArdvark", "12ab"}
    # kept: tree (3 unique), mississippi (4 unique), aardvark (5 unique).
    # dropped: abcdefgh (8 unique > 7), cat (<4), Hello!/12ab (not alpha).
    assert sorted(all_words.filter_words_for_blossom(sample)) == [
        "aardvark", "mississippi", "tree",
    ]


# ---------------------------------------------------------------------------
# Route / session behaviour (the real user path).
# ---------------------------------------------------------------------------

class _FakeCursor:
    description = []
    lastrowid = 0

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def close(self):
        pass


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    def commit(self):
        pass

    def is_connected(self):
        return True

    def close(self):
        pass


@contextmanager
def _fake_db_cursor():
    conn = _FakeConn()
    yield conn, conn.cursor()


@pytest.fixture
def blossom_client(client, monkeypatch):
    # Stub the DB so the POST path is hermetic (no real MySQL): invalid/added
    # word lists come back empty and the solver runs on the in-memory list.
    import routes.blossom as blossom
    monkeypatch.setattr(blossom, "db_cursor", _fake_db_cursor)
    return client


def test_blossom_post_returns_results(blossom_client):
    resp = blossom_client.post(
        "/blossom",
        data={"must_have": "t", "may_have": "raine", "petal_letter": "s"},
    )
    assert resp.status_code == 200
    assert b"Showing" in resp.data and b"words" in resp.data


def test_blossom_post_missing_fields_returns_400(client):
    # The route explicitly guards against missing form fields -> 400, not 500.
    assert client.post("/blossom", data={}).status_code == 400


def test_blossom_toggle_word_round_trips_in_session(client):
    r1 = client.post("/blossom", json={"action": "toggle_word", "word": "tree"})
    assert r1.status_code == 200
    assert r1.get_json()["used_words"] == ["tree"]

    # Toggling the same word again removes it.
    r2 = client.post("/blossom", json={"action": "toggle_word", "word": "tree"})
    assert r2.get_json()["used_words"] == []


def test_blossom_reset_clears_session(client):
    client.post("/blossom", json={"action": "toggle_word", "word": "tree"})
    resp = client.get("/blossom/reset")
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get("used_words", []) == []
