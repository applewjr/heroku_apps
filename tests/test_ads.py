"""Where AdSense loads at all (extensions.show_ads and real_site): on public
pages of the live site, and nowhere else.

Pages are rendered as the live site by passing base_url. Nothing here
requests an ad or reaches a database.
"""

import sys

import pytest

from test_blossom import _ScriptedDB, _admin_headers, blossom_client  # noqa: F401

LIVE = "https://www.jamesapplewhite.com"
LOCAL = "http://localhost"
STAGING = "https://apple-apps-staging.herokuapp.com"
LOADER = "pagead/js/adsbygoogle.js?client="


def _page(client, path, base_url=LIVE, **kw):
    resp = client.get(path, base_url=base_url, **kw)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_public_pages_on_the_live_site_load_adsense(client):
    for path in ("/smush", "/wordle"):
        html = _page(client, path)
        assert LOADER in html and 'rel="dns-prefetch" href="https://pagead2' in html


def test_off_the_live_site_adsense_never_even_loads(client):
    for base_url in (LOCAL, STAGING):
        assert LOADER not in _page(client, "/smush", base_url=base_url)


@pytest.mark.parametrize("path", ["/smush", "/blossom", "/ribbit", "/wordiply", "/chainle"])
def test_the_manual_units_are_gone(client, path):
    # Switched off 2026-11-21 and deleted 2026-11-24 (James): Auto ads only.
    html = _page(client, path)
    assert 'id="jj-ad"' not in html and ".jj-ad-box" not in html and "data-ad-slot" not in html


def _password_pages(flask_app):
    """Every GET page behind the admin password, found from the app's own
    routes the way test_routes' /jj_admin check finds them, so a new admin
    page is covered without anyone listing it. Each ETL round is filled in."""
    from data import etl_dash_queries
    from test_routes import _needs_password
    rounds = sorted({d["round"] for d in etl_dash_queries.values() if "round" in d})
    pages = set()
    for rule in flask_app.url_map.iter_rules():
        if "GET" not in rule.methods or not _needs_password(flask_app.view_functions[rule.endpoint]):
            continue
        if rule.arguments == {"round"}:
            pages |= {rule.rule.replace("<int:round>", str(r)) for r in rounds}
        else:
            assert not rule.arguments, f"no way to fill in {rule.rule}"
            pages.add(rule.rule)
    return sorted(pages)


def test_password_only_pages_load_no_ads(client, flask_app, monkeypatch):
    # Every page behind the admin password, now and future, renders without
    # AdSense (extensions.show_ads), so none of them needs an AdSense
    # exclusion (James asked, 2026-11-26). Every database read is a fake.
    for name, module in list(sys.modules.items()):
        if (name == "crowd" or name.startswith("routes.")) and hasattr(module, "db_cursor"):
            monkeypatch.setattr(module, "db_cursor", _ScriptedDB())
    headers = _admin_headers(monkeypatch)
    pages = _password_pages(flask_app)
    # The walk has to keep finding them.
    assert {"/jj_admin", "/ribbit_admin", "/ribbit_admin/board", "/smush_admin", "/blossom_admin",
            "/4x6_admin", "/etl_dash/1", "/espresso/home/"} <= set(pages)
    # Pages that only redirect, and where to. Their destination is checked
    # in its own turn; any other redirect fails.
    redirects = {"/espresso": "/espresso/home/"}
    for path in pages:
        resp = client.get(path, base_url=LIVE, headers=headers)
        if path in redirects:
            assert resp.status_code in (301, 302), path
            assert resp.headers["Location"].endswith(redirects[path]), path
            continue
        assert resp.status_code == 200, path
        assert LOADER not in resp.get_data(as_text=True), path
    # The same browser, back on a public page, gets them again.
    assert LOADER in _page(client, "/ribbit", headers=headers)


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
