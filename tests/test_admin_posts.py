"""Password-only routes: a form another site posts is refused, password and
all, and admin pages can't be framed by another site (extensions.py
verify_password and keep_admin_out_of_frames). /4x6's own tests check what
its forms do when they get through. Nothing here reaches a database.
"""

import pytest
from test_blossom import _admin_headers
from test_routes import _needs_password

FROM_ELSEWHERE = [{'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'},
                  {'Origin': 'https://evil.example'}, {'Origin': 'null'},
                  {'Sec-Fetch-Site': 'cross-site', 'Origin': 'http://localhost'}]


def _admin_post_urls(flask_app):
    """A URL for every password-only route that takes a POST."""
    adapter = flask_app.url_map.bind('localhost')
    urls = []
    for rule in flask_app.url_map.iter_rules():
        if 'POST' not in rule.methods or not _needs_password(flask_app.view_functions[rule.endpoint]):
            continue
        values = {name: 1 if type(conv).__name__ == 'IntegerConverter' else 'x'
                  for name, conv in rule._converters.items()}
        urls.append(adapter.build(rule.endpoint, values, method='POST'))
    return sorted(set(urls))


def test_every_admin_post_from_another_site_is_refused(client, flask_app, monkeypatch):
    headers = _admin_headers(monkeypatch)
    urls = _admin_post_urls(flask_app)
    assert len(urls) > 40 and '/smush_admin/add_word' in urls and '/4x6_admin/remove_board' in urls
    for url in urls:
        for sent in FROM_ELSEWHERE:
            resp = client.post(url, headers=dict(headers, **sent))
            assert resp.status_code == 403, (url, sent, resp.status_code)


def test_the_check_comes_before_the_password(client, monkeypatch):
    _admin_headers(monkeypatch)
    assert client.post('/smush_admin/add_word', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    # From this site with no password: the usual login prompt.
    assert client.post('/smush_admin/add_word', headers={'Sec-Fetch-Site': 'same-origin'}).status_code == 401


def test_a_refused_post_is_not_a_wrong_password(client, flask_app, monkeypatch):
    import extensions
    headers = _admin_headers(monkeypatch)
    hits = []
    monkeypatch.setattr(extensions.limiter, 'enabled', True)
    monkeypatch.setattr(extensions, '_login_locked', lambda ip: False)
    monkeypatch.setattr(extensions, 'password_matches', lambda value: hits.append(value))
    with flask_app.test_request_context('/smush_admin/add_word', method='POST',
                                        headers=dict(headers, **{'Sec-Fetch-Site': 'cross-site'})):
        with pytest.raises(Exception) as refused:
            extensions.verify_password('james', 'test-pass')
    assert getattr(refused.value, 'code', None) == 403 and hits == []


@pytest.mark.parametrize('sent', [{}, {'Sec-Fetch-Site': 'same-origin'}, {'Origin': 'http://localhost'},
                                  {'Sec-Fetch-Site': 'same-origin', 'Origin': 'null'}])
def test_a_form_on_this_site_gets_through(client, monkeypatch, sent):
    resp = client.post('/smush_admin/add_word', headers=dict(_admin_headers(monkeypatch), **sent),
                       data={'word': 'x'})
    assert resp.status_code == 302


def test_reading_an_admin_page_and_public_posts_are_left_alone(client, monkeypatch):
    other = {'Sec-Fetch-Site': 'cross-site', 'Origin': 'https://evil.example'}
    assert client.get('/jj_admin', headers=dict(_admin_headers(monkeypatch), **other)).status_code == 200
    assert client.post('/wordiply', headers=other, json={'search_string': 'row'}).status_code == 200


def test_only_admin_pages_refuse_other_sites_frames(client, monkeypatch):
    headers = _admin_headers(monkeypatch)
    assert client.get('/jj_admin', headers=headers).headers['X-Frame-Options'] == 'SAMEORIGIN'
    assert client.get('/4x6_admin/board', headers=headers).headers['X-Frame-Options'] == 'SAMEORIGIN'
    # The same browser on a public page, and a visitor's.
    assert 'X-Frame-Options' not in client.get('/smush', headers=headers).headers
    assert 'X-Frame-Options' not in client.get('/smush').headers
