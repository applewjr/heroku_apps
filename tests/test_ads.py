"""Where AdSense loads at all (extensions.show_ads and real_site): on public
pages of the live site, and nowhere else.

Pages are rendered as the live site by passing base_url. Nothing here
requests an ad or reaches a database.
"""

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


def test_password_only_pages_load_no_ads(client, monkeypatch):
    import crowd
    import routes.ribbit as ribbit
    monkeypatch.setattr(crowd, "db_cursor", _ScriptedDB())
    monkeypatch.setattr(ribbit, "db_cursor", _ScriptedDB())
    headers = _admin_headers(monkeypatch)
    for path in ("/ribbit_admin/board", "/ribbit_admin", "/circuits_admin/board",
                 "/typeshift_admin/board", "/typeshift_admin", "/4x3_admin/board", "/4x3_admin",
                 "/4x6_admin/board", "/4x6_admin"):
        assert LOADER not in _page(client, path, headers=headers), path
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
