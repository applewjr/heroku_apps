"""The cross-links between solvers (templates/_related_solvers.html), and the
www address every solver page hands to search engines and link previews.

Pages are rendered as the live site, as tests/test_ad_slots.py does. Nothing
here reaches a database.
"""

import json
import re

import pytest

from test_ad_slots import FAQ, LIVE, UNIT, slots  # noqa: F401
from test_blossom import blossom_client  # noqa: F401

STRIP = 'class="jj-more"'
# Every solver page, with its own Play link: the button row that comes after
# the page's ad unit and before the strip.
PAGES = {
    "/smush": "Play Smush",
    "/blossom": "Play Blossom",
    "/chainle": "Play Chainle",
    "/ribbit": "Play Ribbit",
    "/wordiply": "Play Wordiply",
    "/guess_the_phrase": "Play Guess The Phrase",
    "/circuits": "Play Circuits",
    "/typeshift": "Play Typeshift",
    "/4x3": "Play 4x3",
    "/wordle": "Play Wordle",
    "/antiwordle": "Play Antiwordle",
    "/quordle": "Play Quordle",
}
# Cards for the busier daily solvers, a line of plain links for the rest
# (Wordiply and Guess The Phrase moved there 2026-11-22).
CARDS = ("/smush", "/blossom", "/chainle", "/ribbit", "/circuits", "/typeshift", "/4x3")
PLAIN = ("/wordiply", "/wordle", "/antiwordle", "/quordle", "/guess_the_phrase")


def _page(client, path):
    resp = client.get(path, base_url=LIVE)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _strip(html):
    start = html.index(STRIP)
    return html[start:html.index("</section>", start)]


@pytest.mark.parametrize("path", sorted(PAGES))
def test_every_solver_links_every_other_solver_once(client, path):
    html = _page(client, path)
    assert html.count(STRIP) == 1
    strip = _strip(html)
    assert f'href="{path}"' not in strip
    for other in CARDS + PLAIN:
        if other != path:
            assert strip.count(f'href="{other}"') == 1, other
    # The busier daily solvers are cards; the rest a line of links, in order.
    assert strip.count('class="jj-more-tile"') == len(CARDS) - (path in CARDS)
    line = strip[strip.index('class="jj-more-also"'):]
    shown = [p for p in PLAIN if p != path]
    assert sorted(shown, key=lambda p: line.index(f'href="{p}"')) == shown
    # Links only: on /blossom the strip sits inside form1, where a button
    # would submit it. And no click logging: /log-click stores nothing.
    assert "<button" not in strip and "tracked-link" not in strip


@pytest.mark.parametrize("path", sorted(PAGES))
def test_the_strip_comes_after_the_tool_its_ad_and_play_links(client, slots, path):
    html = _page(client, path)
    marks = [PAGES[path], STRIP, FAQ]
    if UNIT in html:
        marks.insert(0, UNIT)
    spots = [html.index(mark) for mark in marks]
    assert spots == sorted(spots), marks


def test_blossom_result_pages_keep_the_strip_after_the_ad(blossom_client, slots):
    html = blossom_client.post("/blossom", base_url=LIVE, data={
        "must_have": "t", "may_have": "raine", "petal_letter": "s"}).get_data(as_text=True)
    spots = [html.index(mark) for mark in
             ('class="word-results-container"', UNIT, "Play Blossom", STRIP, FAQ)]
    assert spots == sorted(spots)
    assert html.count(STRIP) == 1


@pytest.mark.parametrize("path", sorted(PAGES) + ["/any_word"])
def test_search_engines_and_previews_get_the_www_address(client, path):
    # The bare domain only 301s to www (app.py redirect_non_www), so every
    # address a page gives out matches its canonical tag.
    html = _page(client, path)
    canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html).group(1)
    assert canonical.startswith("https://www.jamesapplewhite.com/")
    assert re.search(r'<meta property="og:url" content="([^"]+)"', html).group(1) == canonical
    blocks = [json.loads(block) for block in
              re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
    apps = [block for block in blocks if block.get("@type") == "WebApplication"]
    assert apps and all(block["url"] == canonical for block in apps)
