"""Tests for the alerting and health-check machinery.

Nothing here may touch real MySQL, Redis or SMTP. The write-behind drain
thread is kept from starting by patching _ensure_drain, so queued rows can be
inspected directly instead of being written somewhere.
"""

import queue
from contextlib import contextmanager

import pytest

import extensions
from monitoring import alerts, run_checks, run_job


##### alerts #####

def test_scrub_collapses_newlines(capsys):
    # One alert is one line: a newline in an exception message would split it,
    # and Papertrail would never match the orphaned second half.
    alerts.alert('x', msg="first line\nsecond line")
    out = capsys.readouterr().out.strip()
    assert out.count("\n") == 0
    assert "first line second line" in out


def test_scrub_neutralises_embedded_token(capsys):
    # A visitor typing the token into a feedback box must not be able to
    # forge an alert that reaches the inbox.
    alerts.alert('feedback_new', header="JJ_ALERT check=fake sev=crit")
    out = capsys.readouterr().out.strip()
    assert out.count(alerts.ALERT_TOKEN) == 1
    assert "JJ.ALERT" in out


def test_scrub_truncates_long_values(capsys):
    alerts.alert('x', msg="y" * 5000)
    out = capsys.readouterr().out.strip()
    assert len(out) < alerts.MAX_VALUE_LEN + 100
    assert out.endswith("...")


def test_scrub_truncates_and_quotes_long_spaced_values(capsys):
    alerts.alert('x', msg="word " * 1000)
    out = capsys.readouterr().out.strip()
    assert len(out) < alerts.MAX_VALUE_LEN + 100
    assert out.endswith('..."')


def test_scrub_quotes_values_containing_spaces(capsys):
    alerts.alert('x', msg="two words")
    assert 'msg="two words"' in capsys.readouterr().out


def test_alert_line_shape(capsys):
    alerts.alert('youtube_trending_today', sev='crit', actual=0, threshold=45)
    out = capsys.readouterr().out.strip()
    assert out == "JJ_ALERT check=youtube_trending_today sev=crit actual=0 threshold=45"


def test_pulse_line_shape(capsys):
    alerts.pulse('checks', ok=11, alert=0)
    assert capsys.readouterr().out.strip() == "JJ_PULSE source=checks ok=11 alert=0"


def test_alert_throttled_suppresses_repeats(capsys):
    alerts.reset_throttle()
    assert alerts.alert_throttled('http_500', min_interval=600) is True
    assert alerts.alert_throttled('http_500', min_interval=600) is False
    assert alerts.alert_throttled('http_500', min_interval=600) is False
    assert capsys.readouterr().out.count(alerts.ALERT_TOKEN) == 1


def test_alert_throttled_is_per_check(capsys):
    alerts.reset_throttle()
    alerts.alert_throttled('log_queue_full', min_interval=600)
    alerts.alert_throttled('http_500', min_interval=600)
    assert capsys.readouterr().out.count(alerts.ALERT_TOKEN) == 2


##### check evaluation #####

@pytest.mark.parametrize("window,hour,expected", [
    ([2, 23], 1, False),      # before the 12am ETL job has had time to land
    ([2, 23], 2, True),
    ([2, 23], 23, True),
    ([2, 23], 0, False),
    ([22, 3], 23, True),      # window wrapping midnight
    ([22, 3], 2, True),
    ([22, 3], 12, False),
    (None, 5, True),          # no window means always in scope
])
def test_in_active_window(window, hour, expected):
    spec = {'only_between_pst': window} if window else {}
    assert run_checks.in_active_window(spec, hour) is expected


@pytest.mark.parametrize("spec,value,expected", [
    ({'compare': '>=', 'threshold': 45}, 50, True),
    ({'compare': '>=', 'threshold': 45}, 45, True),
    ({'compare': '>=', 'threshold': 45}, 44, False),
    ({'compare': '<=', 'threshold': 60}, 0, True),
    ({'compare': '<=', 'threshold': 60}, 99999, False),
    ({'threshold': 45}, 50, True),
])
def test_evaluate(spec, value, expected):
    assert run_checks.evaluate(spec, value)[0] is expected


def test_evaluate_treats_no_rows_as_a_breach():
    # An empty result means the table is empty or the query no longer matches
    # the schema. Silently passing would be the worst possible answer.
    passed, reason = run_checks.evaluate({'compare': '<=', 'threshold': 60}, None)
    assert passed is False
    assert 'no rows' in reason


def test_evaluate_rejects_unknown_comparator():
    assert run_checks.evaluate({'compare': '~=', 'threshold': 1}, 5)[0] is False


def test_evaluate_handles_decimal_from_round():
    from decimal import Decimal
    assert run_checks.evaluate({'compare': '<=', 'threshold': 800}, Decimal('412'))[0] is True


def test_shipped_checks_are_well_formed():
    checks = run_checks.load_checks()
    assert checks
    for name, spec in checks.items():
        assert 'sql' in spec, name
        assert 'threshold' in spec, name
        assert spec.get('compare', '>=') in run_checks.COMPARATORS, name
        assert spec.get('sev', 'warn') in ('crit', 'warn', 'info'), name


##### the job wrapper #####

def _script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_run_job_reports_success(tmp_path, capsys):
    path = _script(tmp_path, "ok_job.py", "print('worked')\n")
    assert run_job.run(path) == 0
    out = capsys.readouterr().out
    assert "worked" in out
    assert "JJ_PULSE source=job:ok_job" in out
    assert alerts.ALERT_TOKEN not in out


def test_run_job_reports_failure(tmp_path, capsys):
    # The whole point: today this script would die before its success email
    # and the only signal would be an email that never arrived.
    path = _script(tmp_path, "boom_job.py", "raise RuntimeError('table is gone')\n")
    assert run_job.run(path) == 1
    out = capsys.readouterr().out
    assert "JJ_ALERT check=job:boom_job sev=crit" in out
    assert "RuntimeError" in out
    assert alerts.PULSE_TOKEN not in out


def test_run_job_treats_exit_zero_as_success(tmp_path, capsys):
    path = _script(tmp_path, "exit0.py", "import sys\nsys.exit(0)\n")
    assert run_job.run(path) == 0
    assert alerts.PULSE_TOKEN in capsys.readouterr().out


def test_run_job_preserves_nonzero_exit_code(tmp_path, capsys):
    path = _script(tmp_path, "exit3.py", "import sys\nsys.exit(3)\n")
    assert run_job.run(path) == 3
    assert alerts.ALERT_TOKEN in capsys.readouterr().out


def test_run_job_alerts_on_missing_script(tmp_path, capsys):
    assert run_job.run(str(tmp_path / "nope.py")) == 2
    assert "JJ_ALERT check=job:nope" in capsys.readouterr().out


def test_run_job_runs_script_as_main(tmp_path, capsys):
    # runpy must use run_name="__main__", or a guarded script would no-op.
    path = _script(tmp_path, "guarded.py",
                   "if __name__ == '__main__':\n    print('ran as main')\n")
    assert run_job.run(path) == 0
    assert "ran as main" in capsys.readouterr().out


##### write-behind logging #####

class _RecordingCursor:
    def __init__(self, fail_executemany=False):
        self.executemany_calls = []
        self.execute_calls = []
        self.fail_executemany = fail_executemany

    def executemany(self, sql, rows):
        if self.fail_executemany:
            raise RuntimeError("Data too long for column 'referrer'")
        self.executemany_calls.append((sql, list(rows)))

    def execute(self, sql, params):
        # Stand in for a column-width rejection of one specific row.
        if "BAD" in str(params):
            raise RuntimeError("Data too long")
        self.execute_calls.append((sql, params))

    def close(self):
        pass


class _RecordingConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def is_connected(self):
        return True

    def close(self):
        pass


@pytest.fixture
def quiet_queue(monkeypatch):
    """Keep the drain thread from starting, and hand back an empty queue."""
    monkeypatch.setattr(extensions, "_ensure_drain", lambda: None)
    monkeypatch.setattr(extensions, "_log_queue", queue.Queue(maxsize=extensions._LOG_QUEUE_MAX))
    return extensions._log_queue


def _drained(cursor):
    @contextmanager
    def fake_db_cursor():
        yield _RecordingConn(cursor), cursor
    return fake_db_cursor


def test_enqueue_does_not_touch_the_database(quiet_queue, monkeypatch):
    def explode():
        raise AssertionError("the request path must never open a connection")
    monkeypatch.setattr(extensions, "db_cursor", explode)
    extensions.enqueue_write("INSERT INTO t VALUES (%s)", ("a",))
    assert quiet_queue.qsize() == 1


def test_enqueue_drops_rather_than_blocks_when_full(quiet_queue, capsys):
    for _ in range(extensions._LOG_QUEUE_MAX):
        extensions.enqueue_write("INSERT INTO t VALUES (%s)", ("a",))
    alerts.reset_throttle()
    extensions.enqueue_write("INSERT INTO t VALUES (%s)", ("overflow",))
    # Dropped, but loudly: silent data loss is worse than loud data loss.
    assert "JJ_ALERT check=log_queue_full" in capsys.readouterr().out


def test_drain_batches_one_executemany_per_statement(monkeypatch):
    cursor = _RecordingCursor()
    monkeypatch.setattr(extensions, "db_cursor", _drained(cursor))
    batch = [("INSERT A", (1,)), ("INSERT B", (2,)), ("INSERT A", (3,))]
    extensions._drain_batch(batch)
    assert len(cursor.executemany_calls) == 2
    by_sql = dict(cursor.executemany_calls)
    assert by_sql["INSERT A"] == [(1,), (3,)]
    assert by_sql["INSERT B"] == [(2,)]


def test_drain_retries_row_by_row_when_a_batch_fails(monkeypatch):
    # One unwritable row must not take its 49 healthy neighbours with it.
    cursor = _RecordingCursor(fail_executemany=True)
    monkeypatch.setattr(extensions, "db_cursor", _drained(cursor))
    extensions._drain_batch([("INSERT A", ("good1",)),
                             ("INSERT A", ("BAD",)),
                             ("INSERT A", ("good2",))])
    written = [params for _sql, params in cursor.execute_calls]
    assert written == [("good1",), ("good2",)]


def test_drain_reraises_when_no_row_can_be_written(monkeypatch):
    # Nothing writing means the connection or table is gone, not a bad row,
    # and the caller needs to hear about that.
    cursor = _RecordingCursor(fail_executemany=True)
    monkeypatch.setattr(extensions, "db_cursor", _drained(cursor))
    with pytest.raises(RuntimeError):
        extensions._drain_batch([("INSERT A", ("BAD",))])


def test_timestamp_is_stamped_at_enqueue_not_at_flush(quiet_queue):
    # If the drain backs up behind a slow database, server-side NOW() would
    # stamp every queued row with the flush time, distorting the hourly
    # averages the idle checks depend on.
    extensions.enqueue_write(extensions.APP_VISITS_SQL,
                             (extensions.pst_now_str(), "p", "r", "ua"))
    _sql, params = quiet_queue.get_nowait()
    assert "%s" in extensions.APP_VISITS_SQL
    assert "CONVERT_TZ" not in extensions.APP_VISITS_SQL
    assert params[0][:2] == "20"


##### request path stays off the database #####

def test_solver_pages_render_with_the_database_down(client, monkeypatch, quiet_queue):
    def explode(*a, **k):
        raise RuntimeError("JawsDB is unreachable")
    monkeypatch.setattr(extensions, "db_cursor", explode)
    for path in ("/blossom", "/smush", "/ribbit", "/wordiply"):
        assert client.get(path).status_code == 200, path
    # ...and every visit was still recorded, just not synchronously.
    assert quiet_queue.qsize() == 4



def test_shipped_checks_are_all_read_only():
    # A monitoring run must never be able to modify what it inspects.
    for name, spec in run_checks.load_checks().items():
        assert spec['sql'].strip().upper().startswith('SELECT'), name


def test_runner_refuses_non_select_sql(capsys):
    class Cur:
        def execute(self, sql, *a, **k):
            # The runner's own SET SESSION is fine; a check's SQL is not.
            if str(sql).strip().upper().startswith('SET '):
                return
            raise AssertionError("a non-SELECT check must never reach the database")
        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cur()
        def close(self):
            pass

    import monitoring.run_checks as rc
    original = rc.mysql.connector.connect
    rc.mysql.connector.connect = lambda **kw: Conn()
    try:
        results = rc.Results()
        rc.run_sql_checks(results, {'evil': {'sql': "DELETE FROM app_visits", 'threshold': 1}}, 12)
    finally:
        rc.mysql.connector.connect = original
    assert results.failed == 1
    assert "must be a SELECT" in capsys.readouterr().out


def test_runner_forces_fresh_information_schema_stats():
    # MySQL 8 caches information_schema statistics for 24 hours by default, so
    # without this the db_size_mb check could alert a day late - or stay quiet
    # a day too long.
    executed = []

    class Cur:
        def execute(self, sql, *a, **k):
            executed.append(" ".join(str(sql).split()))
        def fetchone(self):
            return (1,)
        def fetchall(self):
            return []
        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cur()
        def close(self):
            pass

    import monitoring.run_checks as rc
    original = rc.mysql.connector.connect
    rc.mysql.connector.connect = lambda **kw: Conn()
    try:
        rc.run_sql_checks(rc.Results(), {'x': {'sql': 'SELECT 1', 'threshold': 0}}, 12)
    finally:
        rc.mysql.connector.connect = original

    assert executed[0] == "SET SESSION information_schema_stats_expiry = 0"


##### 404 logging actually reaches the database #####

def test_referred_404_is_logged(client, monkeypatch, quiet_queue):
    # routes/misc.py's catch-all RETURNS 404 rather than raising it, so
    # app.py's errorhandler(404) never fires for it. This is the test that
    # would have caught that: the logging has to live where 404s land.
    def explode(*a, **k):
        raise AssertionError("the request path must never open a connection")
    monkeypatch.setattr(extensions, "db_cursor", explode)

    resp = client.get("/no-such-page", headers={"Referer": "https://example.com/x"})
    assert resp.status_code == 404
    assert quiet_queue.qsize() == 1
    _sql, params = quiet_queue.get_nowait()
    assert "error.html (404:" in params[1]


def test_unreferred_404_is_not_logged(client, quiet_queue):
    # Scanner traffic carries no Referer, and NOT_FOUND_LIMITS permits
    # 600/hour per key. Logging those would put most of a gigabyte a year into
    # a 1 GB database with nothing pruning it.
    assert client.get("/no-such-page").status_code == 404
    assert quiet_queue.qsize() == 0
