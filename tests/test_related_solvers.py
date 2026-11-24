"""The cross-links between solvers (templates/_related_solvers.html), and the
www address every solver page hands to search engines and link previews.

Pages are rendered as the live site, as tests/test_ads.py does. Nothing
here reaches a database.
"""

import json
import re

import pytest

from test_ads import LIVE
from test_blossom import blossom_client  # noqa: F401

STRIP = 'class="jj-more"'
FAQ = "Frequently Asked Questions"
# Every solver page, with its own Play link: the button row that comes before
# the strip.
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
    "/4x6": "Play 4x6",
    "/wordle": "Play Wordle",
    "/antiwordle": "Play Antiwordle",
    "/quordle": "Play Quordle",
}
# Cards for the busier daily solvers, a line of plain links for the rest
# (Wordiply and Guess The Phrase moved there 2026-11-22).
CARDS = ("/smush", "/blossom", "/chainle", "/ribbit", "/circuits", "/typeshift", "/4x3", "/4x6")
PLAIN = ("/wordiply", "/wordle", "/antiwordle", "/quordle", "/guess_the_phrase")
# Sister games, shown first under a heading of their own (James, 2026-11-24).
FAMILIES = {"Hank Green": ("/smush", "/4x3", "/4x6"),
            "Puzzmo": ("/ribbit", "/circuits", "/typeshift"),
            "Merriam-Webster": ("/blossom", "/quordle")}
SISTERS = {path: [p for p in paths if p != path] for paths in FAMILIES.values() for path in paths}
MORE = "More daily puzzle solvers"


def _hrefs(html):
    return re.findall(r'href="(/[^"]*)"', html)


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
    sisters = SISTERS.get(path, [])
    top, rest = strip.split(MORE)
    grid, line = rest.split('class="jj-more-also"')
    assert _hrefs(top) == sisters
    assert _hrefs(grid) == [p for p in CARDS if p != path and p not in sisters]
    assert _hrefs(line) == [p for p in PLAIN if p != path and p not in sisters]
    assert strip.count('class="jj-more-tile"') == len(_hrefs(top)) + len(_hrefs(grid))
    # Links only: on /blossom the strip sits inside form1, where a button
    # would submit it. And no click logging: /log-click stores nothing.
    assert "<button" not in strip and "tracked-link" not in strip


@pytest.mark.parametrize("path", sorted(PAGES))
def test_sister_games_come_first_under_their_publisher(client, path):
    strip = _strip(_page(client, path))
    titles = re.findall(r"<h2[^>]*>([^<]*)</h2>", strip)
    names = [name for name, paths in FAMILIES.items() if path in paths]
    assert titles == [f"{name}'s other games" for name in names] + [MORE]


def test_sister_cards_keep_their_tags(client):
    top = _strip(_page(client, "/smush")).split(MORE)[0]
    assert top.count("jj-more-beta") == 2      # 4x3 and 4x6, while they're Beta


@pytest.mark.parametrize("path", sorted(PAGES))
def test_the_strip_comes_after_the_tool_and_play_links(client, path):
    html = _page(client, path)
    marks = [PAGES[path], STRIP, FAQ]
    spots = [html.index(mark) for mark in marks]
    assert spots == sorted(spots), marks


def test_blossom_result_pages_keep_the_strip_after_the_results(blossom_client):
    html = blossom_client.post("/blossom", base_url=LIVE, data={
        "must_have": "t", "may_have": "raine", "petal_letter": "s"}).get_data(as_text=True)
    spots = [html.index(mark) for mark in
             ('class="word-results-container"', "Play Blossom", STRIP, FAQ)]
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
