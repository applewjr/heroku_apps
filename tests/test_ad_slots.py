"""The deliberate ad units (templates/_ad_slot.html), and where AdSense
loads at all (extensions.show_ads and ad_slot).

Pages are rendered as the live site by passing base_url; the slot IDs are
fakes. Nothing here requests an ad or reaches a database.
"""

import pytest

from test_blossom import _ScriptedDB, _admin_headers, blossom_client  # noqa: F401

LIVE = "https://www.jamesapplewhite.com"
LOCAL = "http://localhost"
STAGING = "https://apple-apps-staging.herokuapp.com"
IDS = {"smush": "1111111111", "blossom": "2222222222", "ribbit": "3333333333",
       "wordiply": "4444444444", "chainle": "5555555555"}
LOADER = "pagead/js/adsbygoogle.js?client="
UNIT = 'id="jj-ad"'
FAQ = "Frequently Asked Questions"

# The solvers that draw their results in the browser, with what must come
# before the unit, in order: the tool, its results, the end of the results
# block. The unit comes next, then the FAQ.
PAGES = {
    "smush": ('id="smush-board"', 'id="results-container"', 'id="suggest-box"'),
    "ribbit": ('id="rb-board-wrap"', 'id="results-container"', 'id="suggest-box"'),
    "wordiply": ('id="search-input"', 'id="results-container"', 'id="copy-hint"'),
    "chainle": ('id="chn-clues"', 'id="chn-results"', 'id="chn-reveal-all"'),
}
GATES = {
    "smush": "#results-container .word-result",
    "ribbit": "#results-container .word-result",
    "wordiply": "#results-container .word-result",
    "chainle": "#chn-results:not([hidden])",
}


@pytest.fixture
def slots(monkeypatch):
    """A slot ID for every page, none switched off."""
    import config
    monkeypatch.setattr(config, "AD_SLOT_IDS", dict(IDS))
    monkeypatch.setattr(config, "AD_SLOTS_DISABLED", frozenset())
    return config


def _page(client, path, base_url=LIVE, **kw):
    resp = client.get(path, base_url=base_url, **kw)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _in_order(html, *marks):
    spots = [html.index(mark) for mark in marks]
    assert spots == sorted(spots), f"out of order: {marks}"


@pytest.mark.parametrize("page", sorted(PAGES))
def test_each_solver_puts_its_unit_after_the_whole_results_block(client, slots, page):
    html = _page(client, f"/{page}")
    _in_order(html, *PAGES[page], UNIT, FAQ)
    # The live site asks for the real unit, hidden until the first results.
    assert f'data-ad-gate="{GATES[page]}" hidden data-ad-slot="{IDS[page]}"' in html
    assert ">Advertisements<" in html and "Ad 300x250" not in html
    # Its height is held, and it folds away when AdSense has nothing.
    assert '.jj-ad-box { width: 300px; height: 250px;' in html
    assert '.jj-ad:has(ins[data-ad-status="unfilled"])' in html


def test_blossom_result_pages_carry_the_unit(blossom_client, slots):
    html = blossom_client.post("/blossom", base_url=LIVE, data={
        "must_have": "t", "may_have": "raine", "petal_letter": "s"}).get_data(as_text=True)
    _in_order(html, 'class="word-results-container"', 'class="missing-word-row"', UNIT, FAQ)
    assert f'data-ad-slot="{IDS["blossom"]}"' in html
    assert "data-ad-gate" not in html          # its results come with the page
    # The landing page has no results, so no unit.
    assert UNIT not in _page(blossom_client, "/blossom")


def test_off_the_live_site_a_placeholder_stands_in(client, slots):
    for base_url in (LOCAL, STAGING):
        html = _page(client, "/smush", base_url=base_url)
        assert UNIT in html and "Ad 300x250" in html
        assert "data-ad-slot" not in html
        assert LOADER not in html              # AdSense never even loads


def test_public_pages_on_the_live_site_load_adsense(client, slots):
    for path in ("/smush", "/wordle"):
        html = _page(client, path)
        assert LOADER in html and 'rel="dns-prefetch" href="https://pagead2' in html


def test_a_unit_switched_off_or_without_an_id_renders_nothing(client, slots, monkeypatch):
    monkeypatch.setattr(slots, "AD_SLOTS_DISABLED", frozenset({"smush"}))
    assert UNIT not in _page(client, "/smush")
    assert UNIT in _page(client, "/ribbit")
    monkeypatch.setattr(slots, "AD_SLOT_IDS", {**IDS, "ribbit": ""})
    assert UNIT not in _page(client, "/ribbit")


def test_password_only_pages_load_no_ads(client, slots, monkeypatch):
    import crowd
    import routes.ribbit as ribbit
    monkeypatch.setattr(crowd, "db_cursor", _ScriptedDB())
    monkeypatch.setattr(ribbit, "db_cursor", _ScriptedDB())
    headers = _admin_headers(monkeypatch)
    board = _page(client, "/ribbit_admin/board", headers=headers)
    assert UNIT not in board and LOADER not in board
    assert LOADER not in _page(client, "/ribbit_admin", headers=headers)
    # The same browser, back on a public page, gets them again.
    assert LOADER in _page(client, "/ribbit", headers=headers)


def test_the_unit_script_ships_once_per_page(client, slots):
    for page in PAGES:
        assert _page(client, f"/{page}").count("window.adsbygoogle || []") == 1


@pytest.mark.parametrize("host, live", [
    ("www.jamesapplewhite.com", True),
    ("jamesapplewhite.com", True),
    ("www.jamesapplewhite.com:443", True),
    ("evil-jamesapplewhite.com", False),
    ("jamesapplewhite.com.example.net", False),
    ("apple-apps-staging.herokuapp.com", False),
    ("localhost:5000", False),
])
def test_only_the_live_site_counts(flask_app, host, live):
    from extensions import real_site
    with flask_app.test_request_context("/", base_url=f"https://{host}"):
        assert real_site() is live


def test_the_privacy_policy_says_how_to_opt_out(client):
    html = _page(client, "/privacy-policy", base_url=LOCAL)
    assert 'href="https://myadcenter.google.com"' in html
    assert 'href="https://www.aboutads.info"' in html
