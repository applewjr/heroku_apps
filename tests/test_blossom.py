"""Blossom tests - the highest-traffic page.

Covers the real POST path, the session checkbox logic, and pins the solver's
output against the committed word list. No database required: the solver and
get_filtered_blossom_words fall back to the in-memory word list, and the route
tests stub the DB cursor so the path is hermetic.
"""

import re
from contextlib import contextmanager
from pathlib import Path

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


def test_blossom_post_sqli_current_count_returns_400(blossom_client):
    # Scanner payloads in the numeric field must 400 cleanly, not 500 with a
    # logged traceback (this is the exact probe seen in production logs).
    resp = blossom_client.post(
        "/blossom",
        data={
            "must_have": "t", "may_have": "raine", "petal_letter": "s",
            "current_count": "25AND EXTRACTVALUE(1337,CONCAT(0x5c,0x716b767071))",
        },
    )
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_blossom_post_non_alpha_letters_returns_400(blossom_client):
    resp = blossom_client.post(
        "/blossom",
        data={"must_have": "t'--", "may_have": "raine", "petal_letter": "s"},
    )
    assert resp.status_code == 400


def test_blossom_post_blank_current_count_still_works(blossom_client):
    # Blank current_count + load_more used to hit int('') -> 500; now it
    # falls back to the default and renders normally.
    resp = blossom_client.post(
        "/blossom",
        data={
            "must_have": "t", "may_have": "raine", "petal_letter": "s",
            "current_count": "", "load_more": "1",
        },
    )
    assert resp.status_code == 200


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


# ---------------------------------------------------------------------------
# Template JS wiring. There's no JS runtime here, so these are source-order
# assertions on templates/blossom.html. They exist because the page has a single
# point of failure: every control is an inline on* handler resolved off window,
# and clicking a petal is the only way to submit the form. If init throws before
# the exports run, letters still type in (native input behaviour) but nothing
# submits, which looks exactly like "no suggestions show up".
# ---------------------------------------------------------------------------

BLOSSOM_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "blossom.html"

INLINE_HANDLERS = [
    "handleCenterInput", "handlePetalInput", "handleKeyDown", "handleBackspace",
    "selectPetal", "showResetConfirmation", "cancelReset", "confirmReset",
    "showLoadingOverlay",
]


@pytest.fixture(scope="module")
def blossom_template():
    return BLOSSOM_TEMPLATE.read_text(encoding="utf-8")


def test_inline_handlers_exported_before_init_can_throw(blossom_template):
    # initializeHelperMode() reads sessionStorage, which throws outright on
    # browsers that block site data. Keep the window exports above it.
    risky = blossom_template.index("initializeHelperMode();")
    for name in INLINE_HANDLERS:
        exported = blossom_template.index(f"window.{name} = {name};")
        assert exported < risky, f"window.{name} is assigned after init can throw"


# Keywords that look like calls inside an on* attribute, e.g. `if(...)`.
_JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "typeof",
                "function", "new", "delete", "void", "in", "of"}


def _enclosing_block_is_try(lines, idx, col):
    """Walk back from lines[idx][col] to the brace that opens its enclosing
    block, and report whether that brace belongs to a `try`.

    Proximity is not sufficient: an already-closed try block a few lines above
    would read as a guard while the access sits outside it.
    """
    depth = 0
    for i in range(idx, -1, -1):
        text = lines[idx][:col] if i == idx else lines[i]
        for ch in reversed(text):
            if ch == "}":
                depth += 1
            elif ch == "{":
                if depth == 0:
                    return re.search(r"\btry\b", lines[i]) is not None
                depth -= 1
    return False


def test_every_inline_handler_in_markup_is_exported(blossom_template):
    # Every call in the attribute, not just the first: the onkeydown attributes
    # chain handleKeyDown(...) and then handleBackspace(...).
    called = set()
    for attr in re.findall(r'\son\w+\s*=\s*"([^"]*)"', blossom_template):
        called.update(re.findall(r"\b([A-Za-z_$][\w$]*)\s*\(", attr))
    called -= _JS_KEYWORDS
    exported = set(re.findall(r"window\.(\w+)\s*=", blossom_template))
    assert called, "expected inline handlers in the markup"
    assert not called - exported, (
        f"inline handlers missing from window: {sorted(called - exported)}"
    )


def test_web_storage_access_is_guarded(blossom_template):
    # sessionStorage and localStorage throw rather than returning null when a
    # browser blocks site data, and a truncated write leaves unparseable JSON.
    # Helper mode and the Invalid-box explainer are optional, so neither may be
    # allowed to abort the rest of init.
    lines = blossom_template.splitlines()
    hits = [(i, m.start(), m.group(1))
            for i, line in enumerate(lines)
            for m in re.finditer(r"\b(sessionStorage|localStorage)\.", line)]
    assert {kind for _i, _col, kind in hits} == {"sessionStorage", "localStorage"}, (
        "expected both the helper-mode and Invalid-explainer storage code"
    )
    for i, col, kind in hits:
        assert _enclosing_block_is_try(lines, i, col), (
            f"unguarded {kind} at line {i + 1}: {lines[i].strip()}"
        )


def test_blossom_show_more_button_renders_below_word_list(blossom_client):
    # A user reported being unable to find the "show more" control. It now sits
    # directly under the results table next to the "Showing N of M" count,
    # not above the flower.
    resp = blossom_client.post(
        "/blossom",
        data={"must_have": "t", "may_have": "raine", "petal_letter": "s"},
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Show 25 more words" in html
    table_pos = html.index('class="word-results-container"')
    count_pos = html.index('class="word-count-text"')
    button_pos = html.index("Show 25 more words")
    assert table_pos < count_pos < button_pos

    # The button must submit the enclosing form1, never its own nested <form>,
    # which the HTML parser would drop while closing form1 early.
    assert html.count("<form") == 1
    assert 'formaction="/blossom#words"' in html


def test_blossom_show_more_button_hidden_when_all_words_shown(blossom_client):
    resp = blossom_client.post(
        "/blossom",
        data={
            "must_have": "t", "may_have": "raine", "petal_letter": "s",
            "current_count": "10000",
        },
    )
    assert resp.status_code == 200
    assert "Show 25 more words" not in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Crowd corrections: the Invalid box, the missing-word box, and the vote
# counting behind them. Every database call goes to a scripted fake. These
# writes are inline, not on the write-behind queue, so conftest's drain stub
# does not cover them - and secret_pass points at the production JawsDB.
# ---------------------------------------------------------------------------

# A full board for the pinned puzzle: center t and all six petals.
CENTER, PETALS = "t", "raines"
# Fits that board but is not a word, so it can stand in for a missing one.
NOT_LISTED = "tseniar"


class _ScriptedDB:
    """Stands in for extensions.db_cursor.

    fetchone()/fetchall() answer by matching a fragment of the statement just
    run; every statement is recorded with whitespace collapsed.
    """

    def __init__(self, answers=None, rows=None, rowcount=1):
        self.answers = answers or {}
        self.rows = rows or {}
        self.rowcount = rowcount
        self.executed = []

    def statements(self, fragment):
        return [(sql, params) for sql, params in self.executed if fragment in sql]

    @contextmanager
    def __call__(self):
        db = self

        class Cursor:
            rowcount = 0
            last = ""

            def execute(self, sql, params=None):
                self.last = " ".join(sql.split())
                self.rowcount = db.rowcount
                db.executed.append((self.last, params))

            def fetchone(self):
                return next((v for f, v in db.answers.items() if f in self.last), None)

            def fetchall(self):
                return next((v for f, v in db.rows.items() if f in self.last), [])

        class Conn:
            def commit(self):
                pass

        yield Conn(), Cursor()


def _votes(players, today=0):
    """Answers for the two counts: this player's votes today, and the word's
    distinct players in the window."""
    return {
        "WHERE voter_hash = %s": (today,),
        "COUNT(*) FROM blossom_word_votes WHERE word": (players,),
    }


@pytest.fixture
def crowd_db(monkeypatch):
    import routes.blossom as blossom

    def install(answers=None, rows=None, rowcount=1):
        db = _ScriptedDB(answers, rows, rowcount)
        monkeypatch.setattr(blossom, "db_cursor", db)
        return db

    return install


@pytest.fixture
def fresh_word_cache():
    # The vote paths read the word list through the 12-hour cache. Start each
    # test empty so the scripted DB builds it, and never leave a fake behind.
    from extensions import cache
    cache.delete("blossom_filtered_words")
    yield cache
    cache.delete("blossom_filtered_words")


def _flag(client, word):
    return client.post("/blossom", json={
        "action": "toggle_invalid", "word": word, "center": CENTER, "petals": PETALS})


def _suggest(client, word):
    return client.post("/blossom", json={
        "action": "suggest_missing", "word": word, "center": CENTER, "petals": PETALS})


def test_results_table_has_an_invalid_box_on_every_row(blossom_client):
    resp = blossom_client.post(
        "/blossom",
        data={"must_have": "t", "may_have": "raine", "petal_letter": "s"},
    )
    html = resp.get_data(as_text=True)
    assert "<th>Invalid</th>" in html
    assert html.count('class="word-checkbox"') == 25
    assert html.count('class="invalid-checkbox"') == 25
    # Votes carry the letters this table was built from, not the live petals.
    assert 'data-center="t" data-petals="raine"' in html


def test_words_this_player_flagged_render_ticked(blossom_client):
    with blossom_client.session_transaction() as sess:
        sess["invalid_words"] = ["nastier"]
    resp = blossom_client.post(
        "/blossom",
        data={"must_have": "t", "may_have": "raine", "petal_letter": "s",
              "current_count": "10000"},
    )
    html = resp.get_data(as_text=True)
    assert 'data-word="nastier" aria-label="Blossom rejected nastier" checked>' in html
    assert 'data-word="retains" aria-label="Blossom rejected retains" >' in html


def test_toggle_invalid_round_trips_in_session(client, crowd_db, fresh_word_cache):
    db = crowd_db(_votes(players=1))
    first = _flag(client, "nastier")
    assert first.status_code == 200
    assert first.get_json() == {
        "status": "success", "invalid_words": ["nastier"], "removed": False}
    assert db.statements("INSERT INTO blossom_word_votes")

    second = _flag(client, "nastier")
    assert second.get_json()["invalid_words"] == []
    assert db.statements("DELETE FROM blossom_word_votes WHERE word = %s AND vote = %s AND voter_hash")


def test_two_flags_change_nothing(client, crowd_db, fresh_word_cache):
    db = crowd_db(_votes(players=2))
    assert _flag(client, "nastier").get_json()["removed"] is False
    assert not db.statements("INSERT INTO blossom_invalid_words")


def test_third_player_removes_the_word_for_everyone(client, crowd_db, fresh_word_cache):
    db = crowd_db(_votes(players=3))
    assert _flag(client, "nastier").get_json()["removed"] is True

    [(sql, params)] = db.statements("INSERT INTO blossom_invalid_words")
    assert "'crowd'" in sql and params[0] == "nastier"
    # Votes the other way are cleared, so one more can't flip it straight back.
    assert ("DELETE FROM blossom_word_votes WHERE word = %s AND vote = %s",
            ("nastier", "missing")) in db.executed
    # The next solve rebuilds the list rather than serving the cached one.
    assert fresh_word_cache.get("blossom_filtered_words") is None


def test_the_crowd_never_reverses_your_own_entry(client, crowd_db, fresh_word_cache):
    answers = _votes(players=9)
    answers["SELECT source FROM blossom_added_words"] = ("admin",)
    db = crowd_db(answers)
    assert _flag(client, "nastier").get_json()["removed"] is False
    assert not db.statements("INSERT INTO blossom_invalid_words")


def test_the_crowd_can_reverse_its_own_change(client, crowd_db, fresh_word_cache):
    answers = _votes(players=3)
    answers["SELECT source FROM blossom_added_words"] = ("crowd",)
    db = crowd_db(answers)
    assert _flag(client, "nastier").get_json()["removed"] is True
    assert db.statements("DELETE FROM blossom_added_words WHERE word = %s AND source = 'crowd'")


def test_votes_past_the_daily_cap_are_ignored(client, crowd_db, fresh_word_cache):
    from routes.blossom import CROWD_MAX_VOTES_PER_DAY
    db = crowd_db(_votes(players=99, today=CROWD_MAX_VOTES_PER_DAY))
    resp = _flag(client, "nastier")
    # The player's own row still strikes out; the vote just isn't counted.
    assert resp.get_json()["invalid_words"] == ["nastier"]
    assert not db.statements("INSERT INTO blossom_word_votes")
    assert not db.statements("INSERT INTO blossom_invalid_words")


def test_only_a_result_on_this_board_gets_a_vote(client, crowd_db, fresh_word_cache):
    db = crowd_db(_votes(players=3))
    # zebra can't be made from these letters, so no one could have been shown it.
    assert _flag(client, "zebra").get_json()["invalid_words"] == ["zebra"]
    assert not db.statements("INSERT INTO blossom_word_votes")


def test_unticking_the_deciding_vote_puts_the_word_back(client, crowd_db, fresh_word_cache):
    with client.session_transaction() as sess:
        sess["invalid_words"] = ["nastier"]
    fresh_word_cache.set("blossom_filtered_words", {"stale"})
    db = crowd_db(_votes(players=2))     # what's left once this vote is gone
    _flag(client, "nastier")
    assert db.statements("DELETE FROM blossom_invalid_words WHERE word = %s AND source = 'crowd'")
    assert fresh_word_cache.get("blossom_filtered_words") is None


def test_unticking_a_vote_that_never_counted_changes_nothing(client, crowd_db, fresh_word_cache):
    with client.session_transaction() as sess:
        sess["invalid_words"] = ["nastier"]
    db = crowd_db(_votes(players=0), rowcount=0)     # no vote of theirs to delete
    _flag(client, "nastier")
    assert not db.statements("DELETE FROM blossom_invalid_words")


@pytest.mark.parametrize("word, players, expected", [
    ("tazzt", 0, "not_in_puzzle"),      # z isn't on this board
    ("rain", 0, "not_in_puzzle"),       # no center letter
    ("nastier", 0, "already_listed"),
    (NOT_LISTED, 1, "recorded"),
    (NOT_LISTED, 2, "added"),
])
def test_suggest_missing_outcomes(client, crowd_db, fresh_word_cache, word, players, expected):
    assert NOT_LISTED not in words_blossom
    db = crowd_db(_votes(players=players))
    resp = _suggest(client, word)
    assert resp.status_code == 200
    assert resp.get_json()["result"] == expected
    assert bool(db.statements("INSERT INTO blossom_added_words")) == (expected == "added")


def test_second_player_adds_a_missing_word(client, crowd_db, fresh_word_cache):
    db = crowd_db(_votes(players=2))
    assert _suggest(client, NOT_LISTED).get_json()["result"] == "added"
    [(sql, params)] = db.statements("INSERT INTO blossom_added_words")
    assert "'crowd'" in sql and params[0] == NOT_LISTED
    assert ("DELETE FROM blossom_word_votes WHERE word = %s AND vote = %s",
            (NOT_LISTED, "invalid")) in db.executed


def test_a_word_you_removed_is_never_added_back_by_players(client, crowd_db, fresh_word_cache):
    answers = _votes(players=9)
    answers["SELECT source FROM blossom_invalid_words"] = ("admin",)
    db = crowd_db(answers)
    assert _suggest(client, NOT_LISTED).get_json()["result"] == "recorded"
    assert not db.statements("INSERT INTO blossom_added_words")


def test_json_that_is_not_an_object_is_a_400_not_a_500(client):
    # get_json() used to raise BadRequest inside the route's broad except,
    # which logged a traceback and answered 500.
    assert client.post("/blossom", data="{not json",
                       content_type="application/json").status_code == 400
    assert client.post("/blossom", json=["toggle_word"]).status_code == 400


@pytest.mark.parametrize("payload", [
    {"action": "toggle_invalid", "word": "nastier", "center": "t", "petals": "rai"},
    {"action": "toggle_invalid", "word": "nas", "center": "t", "petals": "raines"},
    {"action": "toggle_invalid", "word": "nastier", "center": 7, "petals": "raines"},
    {"action": "suggest_missing", "word": "nast1er", "center": "t", "petals": "raines"},
    {"action": "suggest_missing", "word": ["nastier"], "center": "t", "petals": "raines"},
])
def test_malformed_votes_are_a_400_and_touch_nothing(client, crowd_db, payload):
    db = crowd_db()
    assert client.post("/blossom", json=payload).status_code == 400
    assert not db.executed


def _admin_headers(monkeypatch):
    import base64
    import config
    monkeypatch.setattr(config, "GOOGLE_FORM_PASS", "test-pass")
    token = base64.b64encode(b"james:test-pass").decode()
    return {"Authorization": f"Basic {token}"}


def test_your_add_is_final(client, crowd_db, monkeypatch):
    db = crowd_db()
    resp = client.post("/add_word", data={"word": "Nastier", "word_type": "invalid"},
                       headers=_admin_headers(monkeypatch))
    assert resp.status_code == 302
    [(sql, _params)] = db.statements("INSERT INTO blossom_invalid_words")
    assert "ON DUPLICATE KEY UPDATE source = 'admin'" in sql
    assert ("DELETE FROM blossom_added_words WHERE word = %s", ("nastier",)) in db.executed
    assert ("DELETE FROM blossom_word_votes WHERE word = %s", ("nastier",)) in db.executed


def test_remove_is_a_clean_undo(client, crowd_db, monkeypatch):
    db = crowd_db()
    resp = client.post("/remove_word", data={"word": "figuline", "word_type": "invalid"},
                       headers=_admin_headers(monkeypatch))
    assert resp.status_code == 302
    assert ("DELETE FROM blossom_invalid_words WHERE word = %s", ("figuline",)) in db.executed
    assert ("DELETE FROM blossom_word_votes WHERE word = %s", ("figuline",)) in db.executed


def test_admin_page_shows_who_made_each_change(client, crowd_db, monkeypatch):
    from datetime import datetime
    when = datetime(2026, 9, 30, 8, 0)
    crowd_db(rows={
        "FROM blossom_invalid_words": [("figuline", when, "crowd"), ("yatagan", when, "admin")],
    })
    resp = client.get("/blossom_admin", headers=_admin_headers(monkeypatch))
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert '<td class="by-crowd">crowd</td>' in html
    assert '<td class="by-admin">you</td>' in html


def test_faq_names_the_real_threshold_and_still_parses(client):
    import json
    from routes.blossom import CROWD_REMOVE_VOTES
    html = client.get("/blossom").get_data(as_text=True)
    phrase = f"once {CROWD_REMOVE_VOTES} different players flag the same word"
    assert html.count(phrase) == 2          # the visible FAQ and its JSON-LD
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    parsed = [json.loads(block) for block in blocks]
    assert any(block.get("@type") == "FAQPage" for block in parsed)


# client_ip: whose address a vote belongs to.
CF_EDGE_V4 = "162.158.10.20"         # inside Cloudflare's 162.158.0.0/15
CF_EDGE_V6 = "2400:cb00:2048::1"     # inside 2400:cb00::/32


@pytest.mark.parametrize("headers, remote, expected", [
    # Through Cloudflare: Heroku appends the edge; the visitor is in CF-Connecting-IP.
    ({"X-Forwarded-For": f"198.51.100.7, {CF_EDGE_V4}", "CF-Connecting-IP": "198.51.100.7"},
     "10.0.0.1", "198.51.100.7"),
    ({"X-Forwarded-For": CF_EDGE_V6, "CF-Connecting-IP": "2001:db8:1:2::5"},
     "10.0.0.1", "2001:db8:1:2::5"),
    # Straight to the herokuapp.com origin: CF-Connecting-IP is whatever the
    # caller typed, so the hop Heroku saw is the answer.
    ({"X-Forwarded-For": "1.2.3.4, 203.0.113.9", "CF-Connecting-IP": "198.51.100.7"},
     "10.0.0.1", "203.0.113.9"),
    # Local runs and tests: no proxy headers at all.
    ({}, "127.0.0.1", "127.0.0.1"),
    # A Cloudflare hop with no usable visitor header falls back to the hop.
    ({"X-Forwarded-For": CF_EDGE_V4, "CF-Connecting-IP": "garbage"}, "10.0.0.1", CF_EDGE_V4),
    ({"X-Forwarded-For": "not-an-ip"}, "10.0.0.1", ""),
])
def test_client_ip(flask_app, headers, remote, expected):
    from helpers import client_ip
    with flask_app.test_request_context("/", headers=headers,
                                        environ_base={"REMOTE_ADDR": remote}):
        assert client_ip() == expected


def _voter(flask_app, ip):
    from routes.blossom import voter_hash
    with flask_app.test_request_context("/", environ_base={"REMOTE_ADDR": ip}):
        return voter_hash()


def test_voter_hash_counts_one_ipv6_64_as_one_player(flask_app):
    # Privacy extensions rotate the low 64 bits, so a phone on IPv6 would
    # otherwise count as a new player every day.
    one = _voter(flask_app, "2001:db8:1:2::5")
    assert _voter(flask_app, "2001:db8:1:2:aaaa:bbbb:cccc:dddd") == one
    assert _voter(flask_app, "2001:db8:1:3::5") != one
    assert _voter(flask_app, "::ffff:198.51.100.7") == _voter(flask_app, "198.51.100.7")
    assert _voter(flask_app, "198.51.100.7") != _voter(flask_app, "198.51.100.8")
    assert "198.51.100.7" not in _voter(flask_app, "198.51.100.7")
