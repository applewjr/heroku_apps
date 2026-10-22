"""/etl_dash: named rounds, readable tables, and every panel shown - even a
broken one, which used to vanish from the page without a word."""

import contextlib
from decimal import Decimal

import pytest

from test_blossom import _admin_headers


class _Cursor:
    """Answers each panel's `SELECT * FROM <view>` with canned (columns,
    rows). A view named in `broken` raises, the way a view that hasn't been
    created yet does."""

    def __init__(self, tables, broken=()):
        self.tables, self.broken = tables, broken
        self.description, self._rows = None, []

    def execute(self, sql, params=None):
        view = sql.split('FROM')[-1].strip(' ;')
        if view in self.broken:
            raise RuntimeError(f"Table '{view}' doesn't exist")
        columns, self._rows = self.tables.get(view, (['n'], [(1,)]))
        self.description = [(c,) for c in columns]

    def fetchall(self):
        return self._rows


@pytest.fixture
def dash_db(monkeypatch):
    import routes.dashboards as dashboards

    def install(tables=None, broken=()):
        cursor = _Cursor(tables or {}, broken)

        @contextlib.contextmanager
        def fake_cursor():
            yield None, cursor

        monkeypatch.setattr(dashboards, 'db_cursor', fake_cursor)
        return cursor
    return install


def _get(client, monkeypatch, path):
    resp = client.get(path, headers=_admin_headers(monkeypatch))
    return resp.status_code, resp.get_data(as_text=True)


def test_every_round_renders_under_its_name(client, monkeypatch, dash_db):
    from data import ETL_ROUND_NAMES
    from routes.dashboards import get_valid_rounds
    dash_db()
    rounds = sorted(get_valid_rounds())
    assert rounds == sorted(ETL_ROUND_NAMES), 'every round in the YAML should have a name'
    for r in rounds:
        status, html = _get(client, monkeypatch, f'/etl_dash/{r}')
        assert status == 200
        # Every round is one tap away, and the one in view is marked.
        for other, name in ETL_ROUND_NAMES.items():
            assert f'href="/etl_dash/{other}"' in html
            assert name in html
        assert f'class="round-pill current" aria-current="page">{ETL_ROUND_NAMES[r]}</a>' in html


def test_a_broken_panel_says_so_instead_of_vanishing(client, monkeypatch, dash_db):
    dash_db(broken=('vw_prod_traffic_today',))
    status, html = _get(client, monkeypatch, '/etl_dash/1')
    assert status == 200
    assert 'Traffic Today' in html
    assert "This panel didn't load: RuntimeError: Table &#39;vw_prod_traffic_today&#39;" in html
    # The rest of the round still renders.
    assert 'Landings (14 Days)' in html
    assert html.count('class="table-wrap"') == 4


def test_numbers_align_right_and_changes_carry_their_sign(client, monkeypatch, dash_db):
    dash_db({'vw_prod_traffic_today': (
        ['page', 'today', 'vs_usual_pct', 'min_since_last'],
        [('smush', 362, Decimal('19'), 6), ('ribbit', 50, Decimal('-18'), None), ('wordle', 12, Decimal('0'), 3)],
    )})
    _, html = _get(client, monkeypatch, '/etl_dash/1')
    assert '<th class="num">today</th>' in html
    assert '<td>smush</td>' in html
    assert '<td class="num pos">+19</td>' in html
    assert '<td class="num neg">-18</td>' in html
    assert '<td class="num">0</td>' in html
    # A missing value is an empty cell, not "None".
    assert '<td class="num"></td>' in html
    assert 'None' not in html


def test_long_text_wraps_and_empty_panels_say_so(client, monkeypatch, dash_db):
    body = 'a long feedback message that would stretch the table far past the screen'
    dash_db({
        'vw_prod_dynamic_1': (['id', 'feedback_body'], [(7, body)]),
        'vw_prod_errors': (['submit_time', 'page_name', 'referrer'], []),
    })
    _, html = _get(client, monkeypatch, '/etl_dash/1')
    assert f'<td class="wide">{body}</td>' in html
    assert '0 rows' in html
    assert 'No rows.' in html


def test_unknown_round_is_refused(client, monkeypatch, dash_db):
    dash_db()
    status, _ = _get(client, monkeypatch, '/etl_dash/99')
    assert status == 400


def test_run_panel_column_flags():
    from routes.dashboards import run_panel
    cursor = _Cursor({'v': (
        ['label', 'count', 'google_change', 'flag', 'note'],
        [('a', 1, Decimal('-3'), True, None), ('b', None, Decimal('2.5'), False, 'x' * 41)],
    )})
    panel = run_panel(cursor, 'SELECT * FROM v')
    assert panel['error'] is None
    assert panel['numeric'] == [False, True, True, False, False]
    assert panel['signed'] == [False, False, True, False, False]
    assert panel['wide'] == [False, False, False, False, True]


def test_jj_admin_cards_carry_the_round_names(client, monkeypatch):
    _, html = _get(client, monkeypatch, '/jj_admin')
    assert 'href="/etl_dash/1"' in html
    assert '<div class="app-card-title">Overview</div>' in html
