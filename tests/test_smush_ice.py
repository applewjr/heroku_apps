"""/smush's 🧊 Ice Cold tab (routes/smush_ice.py): ICE COLD plans for today's
boards only, their full tables built ahead and shared.

Nothing here reaches a real database (secret_pass points at the production
JawsDB), and no full-board table is ever really built: conftest.py's
no_ice_builds keeps today's boards' build threads in a list. The board and
its words are made up: gold E plus ADILNRST, test_smush_boards.py's board.
"""

import json
import re
import threading
from datetime import date, datetime, timedelta, timezone

import pytest

import routes.smush as smush
import routes.smush_ice as smush_ice
from functions import smush_ice_cold as ice
from test_blossom import _admin_headers, _ScriptedDB
from test_smush_boards import DAY, LETTERS, _log, _todays, boards  # noqa: F401 - boards is a fixture

TAG = "e:adilnrst"
OTHER = "radeilnst"                           # another board: gold R
OTHER_TAG = "r:adeilnst"
FULL = dict.fromkeys("adilnrst", 5)
# One use of each letter: 256 states, solved per request.
LATE = dict.fromkeys("adilnrst", 1)
WORDS = {"lead", "deal", "seal", "sled", "tile", "line", "dine", "ride", "side", "tide",
         "aide", "idea", "earl", "real", "tear", "rate", "sate", "east", "seat", "lane",
         "need", "teen", "reed", "deer", "tree", "nest", "dent", "rent", "lent", "eel",
         "dee", "sees", "tees", "else", "residential", "interlards"}


@pytest.fixture
def today(client, boards, monkeypatch):
    """LETTERS is today's board (five players entered it), on one of the two
    live days; the word list is WORDS; ✗ counts come from an empty fake."""
    import routes.wordgames as wordgames
    monkeypatch.setattr(smush_ice, "live_days", lambda now=None: {DAY, DAY + timedelta(days=1)})
    monkeypatch.setattr(smush, "get_smush_words", lambda: set(WORDS))
    monkeypatch.setattr(wordgames, "get_smush_words", lambda: set(WORDS))
    db = _ScriptedDB()
    monkeypatch.setattr(smush_ice, "db_cursor", db)
    for n in range(1, 6):
        _log(client, n)
    return db


def _ice(client, outer_uses, center="e", **extra):
    """The /smush solve with the 🧊 Ice Cold tab open -> its plan."""
    body = {"center": center, "outer_uses": outer_uses, "spicy": "", "first_word": False,
            "plan": False, "ice_cold": True}
    body.update(extra)
    resp = client.post("/smush", json=body)
    assert resp.status_code == 200
    return resp.get_json()["plan"]


# ---------------------------------------------------------------------------
# Today's boards only
# ---------------------------------------------------------------------------

def test_todays_board_gets_a_plan_while_its_table_builds(client, today, no_ice_builds):
    plan = _ice(client, FULL)
    assert plan["mode"] == "ice_cold" and plan["status"] == "building" and plan["eta"] > 0
    assert len(no_ice_builds) == 1                 # one build, started when the board was read


def test_a_small_box_on_todays_board_is_solved_exactly(client, today):
    plan = _ice(client, LATE, spicy="s")
    assert plan["source"] == "exact" and plan["status"] in ("ready", "impossible")
    assert all("s" not in w["cost"] for w in plan["next"])


def test_any_other_board_costs_nothing(client, today, no_ice_builds):
    # James's rule (2026-10-31): only today's boards are planned, to spare the
    # dyno. No build, no solve, no word list, no ✗ counts.
    for uses in (dict.fromkeys("adeilnst", 5), dict.fromkeys("adeilnst", 1)):
        plan = _ice(client, uses, center="r")
        assert plan["status"] == "not_today"
        assert plan["reason"] == "ICE COLD plans are for today's board only."
    assert OTHER_TAG not in smush_ice._board_results and not smush_ice.EXACT.holding()
    assert not today.statements("FROM smush_word_votes")
    # The one build is today's board's, started when the gate read it.
    assert len(no_ice_builds) == 1 and smush_ice.TODAY_TABLES._job[0] == TAG


def test_both_of_todays_boards_are_planned(client, today):
    # Players already past their midnight have a board of their own.
    for n in range(11, 16):
        _log(client, n, letters=OTHER, played_on=DAY + timedelta(days=1))
    assert smush_ice.todays_tags() == {TAG, OTHER_TAG}
    assert smush_ice.is_today(TAG) and smush_ice.is_today(OTHER_TAG)
    assert not smush_ice.is_today("t:adeilnrs")


def test_a_held_table_keeps_its_board_planned_past_midnight(today, monkeypatch):
    jobs = []
    monkeypatch.setattr(smush_ice, "TODAY_TABLES", smush_ice.today_tables(
        spawn=jobs.append, solve=lambda root, groups: "a table", may_start=None))
    smush_ice.prewarm(DAY, {"center": "e", "outer": "adilnrst"})
    jobs.pop()()                                   # the build runs (a stand-in table)
    monkeypatch.setattr(smush_ice, "live_days", lambda now=None: {DAY + timedelta(days=2)})
    assert smush_ice.todays_tags() == set()        # no longer today anywhere...
    assert smush_ice.is_today(TAG)                 # ...but its table is still held
    assert not smush_ice.is_today(OTHER_TAG)


def test_a_built_table_answers_the_tab(client, today, monkeypatch):
    mid = dict.fromkeys("adilnrst", 3)             # 65,536 states: past the exact path
    letters = tuple("adilnrst")
    pool = smush_ice._shared_pool(TAG, "e", letters)
    table = ice.solve_box(tuple(mid[l] for l in letters), pool.groups, compact=True)
    monkeypatch.setattr(smush_ice, "today_table", lambda tag, center, letters: (table, "ready", None))
    plan = _ice(client, mid, spicy="s")
    assert plan["source"] == "table" and plan["status"] in ("ready", "impossible")
    assert all("s" not in w["cost"] for w in plan["next"])


@pytest.mark.parametrize("utc, days", [
    (datetime(2026, 11, 1, 5, 0), {date(2026, 10, 31), date(2026, 11, 1)}),   # Hawaii still on the 31st
    (datetime(2026, 11, 1, 12, 0), {date(2026, 11, 1), date(2026, 11, 2)}),   # Kiribati already on the 2nd
])
def test_the_live_days_are_the_two_that_are_today_somewhere(utc, days):
    assert smush_ice.live_days(utc.replace(tzinfo=timezone.utc)) == days


# ---------------------------------------------------------------------------
# Built the moment a board is decided
# ---------------------------------------------------------------------------

def test_a_decided_board_starts_its_table_once(client, today, no_ice_builds):
    assert _todays(client)["center"] + _todays(client)["outer"] == LETTERS
    assert len(no_ice_builds) == 1                 # however often it's read


def test_an_approval_starts_its_table_at_once(client, today, monkeypatch, no_ice_builds):
    form = {"center": "R", "outer": "A D E I L N S T", "spicy": "", "played_on": DAY.isoformat()}
    resp = client.post("/smush_admin/approve_board", headers=_admin_headers(monkeypatch), data=form)
    assert "success=Approved" in resp.headers["Location"]
    assert len(no_ice_builds) == 1


def test_only_a_day_that_is_today_somewhere_starts_a_table(client, today, monkeypatch, no_ice_builds):
    monkeypatch.setattr(smush_ice, "live_days", lambda now=None: {DAY + timedelta(days=3)})
    assert _todays(client) is not None
    assert no_ice_builds == []


def test_the_switch_turns_the_tab_plans_and_builds_off(client, today, monkeypatch, no_ice_builds):
    monkeypatch.setattr(smush, "SMUSH_ICE_LIVE", False)
    html = client.get("/smush").get_data(as_text=True)
    assert "const ICE_LIVE = false;" in html
    assert "What is ICE COLD in Smush?" not in html and "ICE COLD plan for today's board" not in html
    assert _ice(client, FULL) is None
    assert no_ice_builds == []


# ---------------------------------------------------------------------------
# What a plan knows, and how much at once
# ---------------------------------------------------------------------------

def test_what_a_player_says_they_played_is_never_proof(client, today, monkeypatch):
    # James (2026-10-31): players are chaos. Only popularity and ✗ votes.
    seen = []
    real = ice.make_groups

    def spy(results, letters, accepted=frozenset(), invalid_counts=None):
        seen.append(accepted)
        return real(results, letters, accepted, invalid_counts)

    monkeypatch.setattr(ice, "make_groups", spy)
    assert _ice(client, LATE, played=["lead"])["mode"] == "ice_cold"
    smush_ice._shared_pool(TAG, "e", tuple("adilnrst"))
    assert seen and not any(seen)


def test_too_many_plans_at_once_answer_busy(client, today, monkeypatch):
    monkeypatch.setattr(smush_ice, "_plan_slots", threading.BoundedSemaphore(1))
    smush_ice._plan_slots.acquire()                # another player's plan is being worked out
    plan = _ice(client, LATE)
    assert plan["status"] == "busy" and plan["eta"] == smush_ice.BUSY_SECONDS


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def _badge(client):
    html = client.get("/smush").get_data(as_text=True)
    return re.search(r"const ICE_BADGE = (.*?);", html).group(1)


def test_the_tabs_new_badge_goes_by_itself(client, today, monkeypatch):
    monkeypatch.setattr(smush_ice, "pacific_today", lambda: DAY)
    monkeypatch.setattr(smush_ice, "ICE_NEW_UNTIL", DAY)
    assert "NEW" in _badge(client)
    monkeypatch.setattr(smush_ice, "ICE_NEW_UNTIL", DAY - timedelta(days=1))
    assert _badge(client) == '""'


def test_the_page_has_the_tab_and_no_stats_row(client, today):
    html = client.get("/smush").get_data(as_text=True)
    assert "const SORT_MODES = ['pts', 'eff', 'all8', 'pan', 'ice'];" in html
    assert "const ICE_LIVE = true;" in html
    for gone in ('id="smush-status"', "chip-score", "Words played:", "Pangram available"):
        assert gone not in html, gone
    # One-line hint and All 8 banners.
    assert "Tap the 🌶 spicy tile - again after every play." in html
    assert "★ Pangram first, then top to bottom: every letter ends flat (PERFECT ×2)." in html
    assert "Play the pangram FIRST" not in html
    # Nothing safe left: stop to keep the x5 - or, under 5 words, it's out.
    assert "Stop here to keep your ×5." in html and "ICE COLD is out of reach for this game" in html
    # Searchers can find it: the description, and the FAQ entry on the page
    # and in its JSON-LD.
    assert "ICE COLD plan for today's board" in html
    assert "What is ICE COLD in Smush?" in html
    blocks = [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
    faq = next(b for b in blocks if b.get("@type") == "FAQPage")
    assert any(q["name"] == "What is ICE COLD in Smush?" for q in faq["mainEntity"])
