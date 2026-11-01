"""app_visits' visitor id and the solvers' use rows (helpers.visitor_hash,
extensions.log_page_visit, extensions.log_solve).

Everything is asserted on the write-behind queue. conftest.py's autouse
no_write_behind swaps in an empty one and keeps the drain thread from
starting: secret_pass.py points at the production JawsDB, so a drain started
here would write real app_visits rows. The solves read each game's word
corrections, so every database cursor they can reach is a fake.
"""

import hashlib
import hmac
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml
from flask import jsonify

import extensions
from helpers import visitor_hash
from monitoring import alerts
from test_blossom import _admin_headers, blossom_client  # noqa: F401
from test_chainle import tiny  # noqa: F401
from test_guess_the_phrase import BOARD, gtp_db  # noqa: F401
from test_ribbit_crowd import LINKS, _nodes

ROOT = Path(__file__).resolve().parent.parent
LIVE = "https://www.jamesapplewhite.com"
IPV4 = "198.51.100.7"
IPV6 = "2001:db8:a:1::5"
UA = "Mozilla/5.0 (test)"
DAY = date(2026, 10, 27)

# A minimal solve for each JSON solver, and the page it logs a use of.
SOLVES = {
    "/smush": ("smush.html", {"center": "a", "outer_uses": {c: 5 for c in "bcdefghi"},
                              "spicy": "", "first_word": True, "rejected": [], "played": [],
                              "plan": False}),
    "/ribbit": ("ribbit.html", {"nodes": _nodes(), "edges": LINKS, "played": [], "rejected": [],
                                "min_len": 4}),
    "/chainle": ("chainle.html", {"clues": ["snow", "basket"], "kind": "bridge"}),
    "/guess_the_phrase": ("guess_the_phrase.html", {"board": BOARD, "tried": "", "category": "song"}),
    "/wordiply": ("wordiply.html", {"search_string": "ab"}),
    "/wordle": ("wordle_revamp.html", {"wordle_data": []}),
    "/antiwordle": ("antiwordle_revamp.html", {"wordle_data": []}),
    "/quordle": ("quordle.html", {"quordle_data": [], "mode": "quordle"}),
}


def _queued():
    """Everything queued so far, as (sql, params), without taking it off."""
    return list(extensions._log_queue.queue)


def _visits():
    """The app_visits rows queued so far, in order."""
    return [params for sql, params in _queued() if sql == extensions.APP_VISITS_SQL]


def _hash(flask_app, ip, ua=UA, day=DAY, headers=None):
    with flask_app.test_request_context("/", environ_base={"REMOTE_ADDR": ip}, headers=headers or {}):
        return visitor_hash(day, ua)


@pytest.fixture
def no_db(monkeypatch):
    """Every database cursor a page can reach fails, as in an outage. The
    word lists fall back to the shipped ones (crowd.CrowdList.corrected)."""
    import crowd
    import routes.blossom
    import routes.chainle
    import routes.dashboards
    import routes.misc
    import routes.ribbit
    import routes.smush
    import routes.smush_ice

    def down():
        raise RuntimeError("no database in tests")

    for module in (crowd, extensions, routes.blossom, routes.chainle, routes.dashboards,
                   routes.misc, routes.ribbit, routes.smush, routes.smush_ice):
        monkeypatch.setattr(module, "db_cursor", down)


# --- the id ------------------------------------------------------------------

def test_visitor_hash_is_16_hex_the_same_all_day_and_new_each_day(flask_app):
    first = _hash(flask_app, IPV4)
    assert re.fullmatch(r"[0-9a-f]{16}", first)
    assert _hash(flask_app, IPV4) == first
    assert _hash(flask_app, IPV4, day=DAY + timedelta(days=1)) != first
    assert _hash(flask_app, IPV4, ua="another browser") != first
    assert _hash(flask_app, "198.51.100.8") != first


def test_visitor_hash_cuts_ipv6_to_its_64_and_reads_mapped_ipv4(flask_app):
    # Privacy extensions rotate the low 64 bits, so one phone stays one id.
    assert _hash(flask_app, IPV6) == _hash(flask_app, "2001:db8:a:1:ffff:1:2:3")
    assert _hash(flask_app, IPV6) != _hash(flask_app, "2001:db8:a:2::5")
    assert _hash(flask_app, "::ffff:198.51.100.7") == _hash(flask_app, IPV4)


def test_visitor_hash_keys_on_the_visitor_behind_cloudflare(flask_app):
    # The last hop is a Cloudflare edge: the visitor is in CF-Connecting-IP.
    via_edge = {"X-Forwarded-For": "203.0.113.9, 172.70.1.1", "CF-Connecting-IP": IPV4}
    assert _hash(flask_app, "10.0.0.1", headers=via_edge) == _hash(flask_app, IPV4)
    # Anyone else's CF-Connecting-IP is their own choice, so the hop counts.
    forged = {"X-Forwarded-For": "203.0.113.9", "CF-Connecting-IP": IPV4}
    assert _hash(flask_app, "10.0.0.1", headers=forged) == _hash(flask_app, "203.0.113.9")


def test_visitor_hash_salt_is_pinned(flask_app):
    # A change here would give every visitor a second id on the day it ships.
    key = str(flask_app.secret_key).encode()
    expected = hmac.new(key, f"visit-2026-10-27|{IPV4}|{UA}".encode(), hashlib.sha256).hexdigest()[:16]
    assert _hash(flask_app, IPV4) == expected


# --- the rows ----------------------------------------------------------------

def test_every_row_has_five_params_and_a_visitor_id(client):
    assert "visitor_hash" in extensions.APP_VISITS_SQL
    assert extensions.APP_VISITS_SQL.count("%s") == 5
    client.get("/wordiply", headers={"User-Agent": UA}, environ_base={"REMOTE_ADDR": IPV4})
    (row,) = _visits()
    assert len(row) == 5
    assert re.fullmatch(r"[0-9a-f]{16}", row[4])


def test_the_id_is_salted_with_the_rows_own_day(client, flask_app):
    # One clock reading for both, so DATE(submit_time) is always the id's day.
    client.get("/wordiply", headers={"User-Agent": UA}, environ_base={"REMOTE_ADDR": IPV4})
    stamp, _page, _referrer, user_agent, visitor = _visits()[0]
    assert visitor == _hash(flask_app, IPV4, ua=user_agent, day=date.fromisoformat(stamp[:10]))


def test_a_visit_and_its_use_share_the_id(client):
    seen = dict(headers={"User-Agent": UA}, environ_base={"REMOTE_ADDR": IPV4})
    client.get("/wordiply", **seen)
    client.post("/wordiply", json={"search_string": "ab", "first_solve": True}, **seen)
    client.get("/wordiply", headers={"User-Agent": UA}, environ_base={"REMOTE_ADDR": "198.51.100.8"})
    visit, use, other = _visits()
    assert (visit[1], use[1]) == ("wordiply.html", "wordiply.html (solve)")
    assert visit[4] == use[4] != other[4]


@pytest.mark.parametrize("ip, fragment", [(IPV4, IPV4), (IPV6, "2001:db8:a:1")])
def test_no_ip_in_any_row_or_log(client, flask_app, no_db, capsys, ip, fragment):
    import app as app_module
    seen = dict(headers={"User-Agent": UA}, environ_base={"REMOTE_ADDR": ip})
    client.get("/wordiply", **seen)
    client.post("/wordiply", json={"search_string": "ab", "first_solve": True}, **seen)
    client.post("/smush", json=dict(SOLVES["/smush"][1], first_solve=True), **seen)
    client.get("/no-such-page", headers={"User-Agent": UA, "Referer": "https://example.com/"},
               environ_base={"REMOTE_ADDR": ip})
    with flask_app.test_request_context("/boom", environ_base={"REMOTE_ADDR": ip}):
        app_module.handle_exception(RuntimeError("original failure"))
    pages = [row[1] for row in _visits()]
    assert pages == ["wordiply.html", "wordiply.html (solve)", "smush.html (solve)",
                     "error.html (404: no-such-page)", "error.html (500: original failure)"]
    assert fragment not in repr(_queued())
    out, err = capsys.readouterr()
    assert fragment not in out + err


def test_a_failed_hash_still_logs_the_row_and_keeps_the_address_out(client, monkeypatch, capsys):
    def broken(day, user_agent):
        raise ValueError(f"could not read {IPV4}")
    monkeypatch.setattr(extensions, "visitor_hash", broken)
    alerts.reset_throttle()
    assert client.get("/wordiply", environ_base={"REMOTE_ADDR": IPV4}).status_code == 200
    (row,) = _visits()
    assert row[1] == "wordiply.html" and row[4] is None
    out = capsys.readouterr().out
    assert "check=visitor_hash_failed" in out and "exc=ValueError" in out
    assert IPV4 not in out


def test_test_traffic_off_the_live_site_names_its_host(client):
    # A typed URL on a local run or staging has no Referer: the host goes in
    # its place, so the views' 127.0.0.1:5000 and apple-apps-staging filters
    # catch it. The live site's rows keep 'No referrer'.
    client.get("/wordiply", base_url="http://127.0.0.1:5000")
    client.get("/wordiply", base_url=LIVE)
    client.get("/wordiply", base_url="http://127.0.0.1:5000", headers={"Referer": "https://example.com/"})
    local, live, referred = (row[2] for row in _visits())
    assert "127.0.0.1:5000" in local
    assert live == "No referrer"
    assert referred == "https://example.com/"


def test_the_front_page_logs_its_gets_only(client):
    client.get("/")
    client.post("/")
    client.head("/")
    assert [row[1] for row in _visits()] == ["index.html"]


# --- uses --------------------------------------------------------------------

@pytest.mark.parametrize("path", sorted(SOLVES))
def test_a_page_load_logs_one_use_however_many_solves(client, tiny, gtp_db, no_db, path):
    # The database is down throughout (no_db): a solve and its use row
    # never need it.
    page, body = SOLVES[path]
    for first in (True, False, False):
        resp = client.post(path, json=dict(body, first_solve=first))
        assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    assert [row[1] for row in _visits()] == [f"{page} (solve)"]


@pytest.mark.parametrize("flag, logged", [
    (True, True), (1, True), ("1", True),
    (False, False), (0, False), ("true", False), (None, False), ([True], False),
])
def test_only_a_set_flag_logs_a_use(client, flag, logged):
    client.post("/wordiply", json={"search_string": "ab", "first_solve": flag})
    client.post("/wordiply", json={"search_string": "ab"})
    assert len(_visits()) == (1 if logged else 0)


def test_blossom_flags_only_the_page_you_arrive_on(blossom_client):
    field = '<input type="hidden" name="first_solve" value="1">'
    page = blossom_client.get("/blossom").get_data(as_text=True)
    assert page.count(field) == 1
    form = {"must_have": "t", "may_have": "raine", "petal_letter": "s"}
    results = blossom_client.post("/blossom", data=dict(form, first_solve="1")).get_data(as_text=True)
    assert field not in results                      # so the next tap can't log again
    blossom_client.post("/blossom", data=dict(form, petal_letter="r"))
    blossom_client.post("/blossom", data=dict(form, load_more="true", current_count="15"))
    assert [row[1] for row in _visits()] == ["blossom.html", "blossom.html (solve)"]


def test_side_requests_never_log_a_use(client, monkeypatch):
    import routes.blossom as blossom
    import routes.chainle as chainle
    import routes.guess_the_phrase as gtp
    import routes.ribbit as ribbit
    import routes.smush as smush

    def reply(*args, **kwargs):
        return jsonify(status="success")

    for module, names in ((smush, ("handle_board_action", "handle_vote")),
                          (ribbit, ("handle_action",)),
                          (chainle, ("handle_rounds_action", "handle_vote")),
                          (gtp, ("handle_vote",)),
                          (blossom, ("_toggle_invalid", "_suggest_missing", "_log_board",
                                     "_todays_board_reply"))):
        for name in names:
            monkeypatch.setattr(module, name, reply)
    side = {
        "/smush": ("log_board", "todays_board", "reject", "restore", "suggest_missing"),
        "/ribbit": ("shared_board", "board_links", "log_board", "reject", "suggest_missing"),
        "/chainle": ("todays_rounds", "log_round", "refuse"),
        "/guess_the_phrase": ("suggest_missing",),
        "/blossom": ("toggle_invalid", "suggest_missing", "log_board", "todays_board", "toggle_word"),
    }
    for path, actions in side.items():
        for action in actions:
            resp = client.post(path, json={"action": action, "word": "test", "first_solve": True})
            assert resp.status_code == 200, (path, action)
    assert _visits() == []


def test_admin_copies_never_log(client, monkeypatch, tiny, no_db):
    import routes.ribbit as ribbit
    monkeypatch.setattr(ribbit, "handle_action", lambda data, serve_dark=False: jsonify(boards=[]))
    auth = _admin_headers(monkeypatch)
    for path in ("/smush_dev", "/chainle_dev", "/ribbit_admin/board"):
        assert client.get(path, headers=auth).status_code == 200, path
    solve = dict(SOLVES["/smush"][1], first_solve=True)
    assert client.post("/smush_dev", json=solve, headers=auth).status_code == 200
    chainle = dict(SOLVES["/chainle"][1], first_solve=True)
    assert client.post("/chainle_dev", json=chainle, headers=auth).status_code == 200
    board = {"action": "shared_board", "played_on": "2026-10-27", "first_solve": True}
    assert client.post("/ribbit_admin/board", json=board, headers=auth).status_code == 200
    assert _visits() == []


# --- the readers -------------------------------------------------------------

def _page_list(view):
    sql = (ROOT / "mysql_views" / "dashboard" / view).read_text()
    # Up to the paren that ends the line, since '(solve)' has its own.
    names = re.search(r"page_name IN \((.*?)\)\s*\n\s*AND", sql, re.S).group(1)
    return set(re.findall(r"'([^']+)'", names))


def test_the_use_views_count_every_solver_and_its_uses():
    # A misspelled '(solve)' name would read zero forever without an error.
    pages = _page_list("vw_prod_word_solver_page_visits.sql")
    assert len(pages) == 9
    expected = pages | {f"{page} (solve)" for page in pages}
    assert _page_list("vw_prod_traffic_today.sql") == expected
    assert _page_list("vw_prod_solver_use_7d.sql") == expected
    assert {page for page, _body in SOLVES.values()} | {"blossom.html"} == pages


def test_site_idle_leaves_out_the_rows_it_was_not_measured_on():
    checks = yaml.safe_load((ROOT / "datasets" / "health_checks.yaml").read_text())
    sql = checks["site_idle"]["sql"]
    assert "page_name NOT LIKE '% (solve)'" in sql
    assert "page_name <> 'index.html'" in sql
