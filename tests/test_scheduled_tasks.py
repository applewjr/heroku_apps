"""The scheduled jobs that stopped emailing on 2026-11-25: each does its work,
sends nothing, and fails loudly, so monitoring/run_job.py can alert.

The scripts themselves run here, whole, with fakes for MySQL, Redis, YouTube and
Google Sheets. Nothing may reach a real one: ``secret_pass.py`` points at
production, so every script runs as if on Heroku, where it reads its settings
from (empty) environment variables.
"""

import json
import os
import re
import smtplib
import sys
import types
from datetime import datetime

import mysql.connector
import pandas as pd
import pytest
import redis
from googleapiclient import discovery
from mysql.connector import errorcode

from monitoring import alerts, run_job

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YOUTUBE = 'scheduled_tasks_youtube/'
NO_EMAIL_JOBS = (
    YOUTUBE + 'youtube_trending_v2.py',
    YOUTUBE + 'youtube_trending_revamp_v3.py',
    YOUTUBE + 'youtube_backup_v2.py',
    YOUTUBE + 'youtube_backup_revamp_v3.py',
    'scheduled_tasks_espresso/espresso_data_import.py',
    'scheduled_tasks_redis/redis_wordle.py',
)


class FakeMySQL:
    """A server for one script run. `respond(sql, params)` gives the rows a
    SELECT returns, or raises what the statement should fail with."""

    def __init__(self, respond=lambda sql, params: None):
        self.respond = respond
        self.executed = []
        self.commits = 0
        self.closed = 0

    def connect(self, **kwargs):
        return _Conn(self)

    def statements(self, text):
        return [(sql, params) for sql, params in self.executed if text in sql]


class _Conn:
    def __init__(self, server):
        self.server = server

    def cursor(self):
        return _Cursor(self.server)

    def commit(self):
        self.server.commits += 1

    def is_connected(self):
        return True

    def close(self):
        self.server.closed += 1


class _Cursor:
    def __init__(self, server):
        self.server, self.rows = server, []

    def execute(self, sql, params=None):
        sql = ' '.join(sql.split())
        self.server.executed.append((sql, params))
        self.rows = list(self.server.respond(sql, params) or [])

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def close(self):
        pass


@pytest.fixture
def run(monkeypatch, capsys):
    """run(script, server) -> (exit code, everything it printed), through the
    wrapper the Scheduler uses."""
    def no_email(*a, **k):
        raise AssertionError('this job must not send email')

    monkeypatch.setenv('IS_HEROKU', '1')
    monkeypatch.setattr(smtplib, 'SMTP', no_email)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(sys, 'argv', list(sys.argv))

    def go(script, server):
        monkeypatch.setattr(mysql.connector, 'connect', server.connect)
        code = run_job.run(os.path.join(ROOT, *script.split('/')))
        captured = capsys.readouterr()
        return code, captured.out + captured.err
    return go


def _ok(code, out, name):
    assert code == 0, out
    assert f'JJ_PULSE source=job:{name}' in out
    assert alerts.ALERT_TOKEN not in out


def _alerted(code, out, name):
    assert code == 1, out
    assert f'JJ_ALERT check=job:{name} sev=crit' in out
    assert alerts.PULSE_TOKEN not in out


@pytest.mark.parametrize('script', NO_EMAIL_JOBS)
def test_the_script_has_no_email_code(script):
    with open(os.path.join(ROOT, *script.split('/')), encoding='utf-8') as handle:
        source = handle.read()
    for word in ('smtplib', 'MIMEText', 'GMAIL', 'gmail', '@gmail.com'):
        assert word not in source


##### the YouTube loads #####

def _fake_youtube(monkeypatch, per_chart=3):
    """`per_chart` videos on every chart, one of them with its counts hidden."""
    def items():
        out = []
        for i in range(per_chart):
            out.append({'id': f'vid{i}', 'statistics': {} if i == 0 else {'viewCount': '10'},
                        'snippet': {'title': f'Video {i}', 'channelTitle': 'Channel', 'channelId': 'c1',
                                    'categoryId': '10', 'publishedAt': '2026-11-24T08:00:00Z'}})
        return out

    class Call:
        def __init__(self, response):
            self.response = response

        def execute(self):
            return self.response

    class Youtube:
        def videos(self):
            return types.SimpleNamespace(list=lambda **kw: Call({'items': items()}))

        def channels(self):
            return types.SimpleNamespace(list=lambda **kw: Call({'items': [{'statistics': {}}]}))

    monkeypatch.setattr(discovery, 'build', lambda *a, **k: Youtube())


LOADS = [('youtube_trending_v2', 'youtube_trending', 3), ('youtube_trending_revamp_v3', 'youtube_trending_revamp', 9)]


@pytest.mark.parametrize('name,table,rows', LOADS)
def test_a_load_inserts_every_row_and_says_so(monkeypatch, run, name, table, rows):
    _fake_youtube(monkeypatch)
    server = FakeMySQL()
    code, out = run(YOUTUBE + name + '.py', server)
    _ok(code, out, name)
    assert len(server.statements(f'INSERT INTO {table} ')) == server.commits == rows
    assert f'{table}: {rows} rows inserted, 0 already there, 0 failed' in out


@pytest.mark.parametrize('name,table,rows', LOADS)
def test_a_second_run_the_same_day_is_not_a_failure(monkeypatch, run, name, table, rows):
    _fake_youtube(monkeypatch)

    def duplicate(sql, params):
        if 'INSERT' in sql:
            raise mysql.connector.IntegrityError("Duplicate entry", errno=errorcode.ER_DUP_ENTRY)

    code, out = run(YOUTUBE + name + '.py', FakeMySQL(duplicate))
    _ok(code, out, name)
    assert f'{table}: 0 rows inserted, {rows} already there, 0 failed' in out


@pytest.mark.parametrize('name,table,rows', LOADS)
def test_one_bad_row_fails_the_job_but_costs_no_other_row(monkeypatch, run, name, table, rows):
    # It read as a "duplicate" in an email nobody has to read any more.
    _fake_youtube(monkeypatch)
    seen = []

    def second_row_is_bad(sql, params):
        if 'INSERT' in sql:
            seen.append(params)
            if len(seen) == 2:
                raise mysql.connector.DataError("Data too long for column 'chnl'", errno=1406)

    server = FakeMySQL(second_row_is_bad)
    code, out = run(YOUTUBE + name + '.py', server)
    _alerted(code, out, name)
    assert len(seen) == rows and server.commits == rows - 1
    assert 'Data too long' in out


def test_a_youtube_failure_alerts(monkeypatch, run):
    def down(*a, **k):
        raise RuntimeError('quota exceeded')

    monkeypatch.setattr(discovery, 'build', down)
    code, out = run(YOUTUBE + 'youtube_trending_v2.py', FakeMySQL())
    _alerted(code, out, 'youtube_trending_v2')


##### the in-database copies #####

COPIES = [('youtube_backup_v2', 'youtube_trending'), ('youtube_backup_revamp_v3', 'youtube_trending_revamp')]


@pytest.mark.parametrize('name,table', COPIES)
def test_a_copy_is_made(run, name, table):
    server = FakeMySQL()
    code, out = run(YOUTUBE + name + '.py', server)
    _ok(code, out, name)
    assert server.statements(f'CREATE TABLE IF NOT EXISTS {table}_backup')
    assert f'complete: backup of {table}' in out


@pytest.mark.parametrize('name,table', COPIES)
def test_a_copy_that_fails_alerts(run, name, table):
    def full(sql, params):
        if 'CREATE TABLE' in sql:
            raise mysql.connector.DatabaseError('The table is full')

    code, out = run(YOUTUBE + name + '.py', FakeMySQL(full))
    _alerted(code, out, name)


##### espresso #####

ESPRESSO = 'scheduled_tasks_espresso/espresso_data_import.py'
SHEETS = ('espresso_data', 'espresso_bean', 'espresso_profile')


@pytest.fixture
def sheets(monkeypatch):
    """functions/espresso.py's three sheets, two rows each."""
    frame = pd.DataFrame({'id': ['1', '2'], 'note': ['a', 'b']})
    fake = types.SimpleNamespace(
        google_sheets_base=lambda creds: 'credentials',
        get_google_sheets_espresso=lambda creds, url: frame,
        get_google_sheets_bean=lambda creds, url: frame,
        get_google_sheets_profile=lambda creds, url: frame)
    monkeypatch.setitem(sys.modules, 'espresso', fake)


def _tables(leftovers=(), fail=None):
    """Answers the script's lookup of its own working tables; `fail` is text
    whose statement fails."""
    def respond(sql, params):
        if fail and fail in sql:
            raise mysql.connector.DatabaseError('scripted failure')
        if 'information_schema.tables' in sql:
            return [(1 if params[0] in leftovers else 0,)]
        return None
    return respond


def test_each_sheet_is_swapped_in_with_one_statement(run, sheets):
    server = FakeMySQL(_tables())
    code, out = run(ESPRESSO, server)
    _ok(code, out, 'espresso_data_import')
    renames = [sql for sql, _p in server.statements('RENAME TABLE')]
    # One RENAME does both moves, so the live table is never missing.
    assert renames == [f'RENAME TABLE {t} TO {t}_backup, temp_{t} TO {t}' for t in SHEETS]
    assert [sql for sql, _p in server.statements('DROP TABLE')] == [f'DROP TABLE {t}_backup' for t in SHEETS]
    assert server.closed == 6


@pytest.mark.parametrize('fail', ['INSERT INTO temp_espresso_data', 'CREATE TABLE temp_espresso_data',
                                  'RENAME TABLE espresso_data'])
def test_a_failed_import_leaves_the_table_alone_and_alerts(run, sheets, fail):
    # It used to print the error and swap the empty table in anyway.
    server = FakeMySQL(_tables(fail=fail))
    code, out = run(ESPRESSO, server)
    _alerted(code, out, 'espresso_data_import')
    assert not server.statements('DROP TABLE espresso_data')
    if 'RENAME' not in fail:
        assert not server.statements('RENAME TABLE')


def test_a_table_left_by_a_failed_run_does_not_block_the_next(run, sheets):
    server = FakeMySQL(_tables(leftovers=('temp_espresso_data', 'espresso_bean_backup')))
    code, out = run(ESPRESSO, server)
    _ok(code, out, 'espresso_data_import')
    sql = [s for s, _p in server.executed]
    assert sql.index('DROP TABLE temp_espresso_data') < sql.index(
        next(s for s in sql if s.startswith('CREATE TABLE temp_espresso_data')))
    # Only its own working tables, never a live one.
    dropped = [s.split()[-1] for s in sql if s.startswith('DROP TABLE')]
    assert all(t.startswith('temp_') or t.endswith('_backup') for t in dropped)


##### the Redis drain #####

DRAIN = 'scheduled_tasks_redis/redis_wordle.py'
WORDLE, ANTI = 'wordle_logging', 'antiwordle_logging'
WORDLE_TABLE, ANTI_TABLE = 'wordle_revamp_clicks', 'antiwordle_revamp_clicks'


class FakeRedis:
    """The two streams, as Redis keeps them: entries in order, each with an id.
    `broken` names streams whose entries can't be deleted; `arrives` is an
    entry a player adds to a stream just after it is read."""

    def __init__(self):
        self.streams = {WORDLE: [], ANTI: []}
        self.broken = set()
        self.arrives = {}
        self.added = 0

    def add(self, stream, data, second=0):
        self.added += 1
        # The keys in an order MySQL won't keep.
        entry = {'datetime': f'2026-11-24 08:00:{second:02}', 'json': json.dumps(data)}
        self.streams[stream].append((f'{self.added}-0', entry))

    def ping(self):
        return True

    def xrevrange(self, stream):
        entries = list(reversed(self.streams[stream]))
        if stream in self.arrives:
            self.add(stream, self.arrives.pop(stream), second=59)
        return entries

    def xdel(self, stream, *ids):
        if stream in self.broken:
            raise redis.RedisError('connection lost')
        assert ids
        self.streams[stream] = [e for e in self.streams[stream] if e[0] not in ids]

    def delete(self, *streams):
        raise AssertionError('deleting a whole stream loses what players added meanwhile')


class FakeTables:
    """The two tables, with MySQL's transactions: an insert is kept only once
    committed. `fail` is (table, 'insert' | 'commit' | 'lost commit' | 'short')
    for the next run: 'lost commit' is stored, but the script never hears."""

    def __init__(self):
        self.rows = {WORDLE_TABLE: [(datetime(2026, 11, 23, 9, 0), '[{"l": "z"}]'), (None, None)], ANTI_TABLE: []}
        self.fail = None

    def connect(self, **kwargs):
        return _TableConn(self)

    def data(self, table):
        """What was stored after the rows every test starts with."""
        return [json.loads(d) for _t, d in self.rows[table] if d and d != '[{"l": "z"}]']


class _TableConn:
    def __init__(self, tables):
        self.tables, self.pending, self.rowcount, self.result = tables, [], -1, []

    def cursor(self):
        return self

    def failing(self, table, how):
        return self.tables.fail == (table, how)

    def execute(self, sql, params=None):
        table = re.fullmatch(r'SELECT click_time, data_dict FROM (\w+) ORDER BY id DESC LIMIT %s', sql).group(1)
        newest_first = list(reversed(self.tables.rows[table]))[:params[0]]
        # MySQL hands JSON back with its keys in its own order.
        self.result = [(t, d if d is None else json.dumps(json.loads(d), sort_keys=True)) for t, d in newest_first]

    def fetchall(self):
        return self.result

    def executemany(self, sql, rows):
        table = re.fullmatch(r'INSERT INTO (\w+) \(click_time, data_dict\) VALUES \(%s, %s\)', sql).group(1)
        if self.failing(table, 'insert'):
            raise mysql.connector.DatabaseError('lock wait timeout')
        rows = rows[1:] if self.failing(table, 'short') else rows
        self.pending += [(table, row) for row in rows]
        self.rowcount = len(rows)

    def commit(self):
        for table in {t for t, _row in self.pending}:
            if self.failing(table, 'commit'):
                raise mysql.connector.OperationalError('Lost connection to MySQL server')
        for table, row in self.pending:
            self.tables.rows[table].append(row)
        done, self.pending = self.pending, []
        for table in {t for t, _row in done}:
            if self.failing(table, 'lost commit'):
                raise mysql.connector.OperationalError('Lost connection to MySQL server')

    def close(self):
        self.pending = []


@pytest.fixture
def drain(monkeypatch, run):
    """drain() runs the script once against streams and tables that last
    between runs: (exit code, what it printed)."""
    client, tables = FakeRedis(), FakeTables()
    monkeypatch.setattr(redis, 'Redis', lambda **kw: client)

    def go():
        return run(DRAIN, tables)
    go.redis, go.tables = client, tables
    return go


def test_both_streams_move_into_mysql_and_are_cleared(drain):
    drain.redis.add(WORDLE, [{'l': 'a'}])
    drain.redis.add(WORDLE, [{'l': 'b'}])
    drain.redis.add(ANTI, [{'l': 'c'}])
    code, out = drain()
    _ok(code, out, 'redis_wordle')
    assert sorted(drain.tables.data(WORDLE_TABLE), key=str) == [[{'l': 'a'}], [{'l': 'b'}]]
    assert drain.tables.data(ANTI_TABLE) == [[{'l': 'c'}]]
    # Cleared with nothing new arriving, and the next run has nothing to do.
    assert drain.redis.streams == {WORDLE: [], ANTI: []}
    code, out = drain()
    _ok(code, out, 'redis_wordle')
    assert len(drain.tables.data(WORDLE_TABLE)) == 2


def test_an_entry_added_while_draining_waits_for_the_next_run(drain):
    drain.redis.add(WORDLE, [{'l': 'a'}])
    drain.redis.arrives = {WORDLE: [{'l': 'late'}]}
    code, out = drain()
    _ok(code, out, 'redis_wordle')
    assert drain.tables.data(WORDLE_TABLE) == [[{'l': 'a'}]]
    assert [json.loads(e['json']) for _id, e in drain.redis.streams[WORDLE]] == [[{'l': 'late'}]]
    code, out = drain()
    _ok(code, out, 'redis_wordle')
    assert drain.tables.data(WORDLE_TABLE) == [[{'l': 'a'}], [{'l': 'late'}]]
    assert drain.redis.streams[WORDLE] == []


def test_one_stream_failing_does_not_cost_the_other(drain):
    drain.redis.add(WORDLE, [{'l': 'a'}])
    drain.redis.add(ANTI, [{'l': 'c'}])
    drain.tables.fail = (WORDLE_TABLE, 'insert')
    code, out = drain()
    _alerted(code, out, 'redis_wordle')
    assert 'lock wait timeout' in out
    assert len(drain.redis.streams[WORDLE]) == 1 and drain.redis.streams[ANTI] == []
    assert drain.tables.data(ANTI_TABLE) == [[{'l': 'c'}]]


@pytest.mark.parametrize('how,stored', [('insert', 0), ('short', 0), ('commit', 0), ('lost commit', 2)])
def test_a_run_that_fails_leaves_one_row_an_entry_after_the_next(drain, how, stored):
    # Whether or not the rows made it, the entries stay, and the next run
    # stores what is missing and clears the stream.
    drain.redis.add(WORDLE, [{'l': 'a', 'p': '1', 'c': '2'}])
    drain.redis.add(WORDLE, [{'l': 'b'}])
    drain.tables.fail = (WORDLE_TABLE, how)
    code, out = drain()
    _alerted(code, out, 'redis_wordle')
    assert len(drain.tables.data(WORDLE_TABLE)) == stored
    assert len(drain.redis.streams[WORDLE]) == 2
    drain.tables.fail = None
    drain.redis.add(WORDLE, [{'l': 'new'}])
    code, out = drain()
    _ok(code, out, 'redis_wordle')
    assert sorted(drain.tables.data(WORDLE_TABLE), key=str) == sorted(
        [[{'l': 'a', 'p': '1', 'c': '2'}], [{'l': 'b'}], [{'l': 'new'}]], key=str)
    assert drain.redis.streams[WORDLE] == []


def test_a_stream_that_will_not_clear_alerts_and_is_never_stored_twice(drain):
    drain.redis.add(WORDLE, [{'l': 'a'}])
    drain.redis.broken = {WORDLE}
    # Two nights in a row, with a new entry on the second.
    code, out = drain()
    _alerted(code, out, 'redis_wordle')
    drain.redis.add(WORDLE, [{'l': 'b'}])
    code, out = drain()
    _alerted(code, out, 'redis_wordle')
    assert len(drain.redis.streams[WORDLE]) == 2
    drain.redis.broken = set()
    code, out = drain()
    _ok(code, out, 'redis_wordle')
    assert '0 rows stored, 2 already there' in out
    assert drain.tables.data(WORDLE_TABLE) == [[{'l': 'a'}], [{'l': 'b'}]]
    assert drain.redis.streams[WORDLE] == []


def test_two_entries_that_are_alike_are_two_rows(drain):
    # A double tap: the same boxes in the same second. Two entries, two rows,
    # and still two after a run that stored them and couldn't clear them.
    for _ in range(2):
        drain.redis.add(WORDLE, [{'l': 'a'}], second=7)
    drain.redis.broken = {WORDLE}
    code, out = drain()
    _alerted(code, out, 'redis_wordle')
    assert drain.tables.data(WORDLE_TABLE) == [[{'l': 'a'}]] * 2
    drain.redis.broken = set()
    code, out = drain()
    _ok(code, out, 'redis_wordle')
    assert drain.tables.data(WORDLE_TABLE) == [[{'l': 'a'}]] * 2
    assert drain.redis.streams[WORDLE] == []


def test_an_entry_like_one_already_stored_is_still_stored(drain):
    # The second tap landed just after the first was read, and the run that
    # stored the first couldn't clear it: one of the two is already a row.
    drain.redis.add(WORDLE, [{'l': 'a'}], second=59)
    drain.redis.arrives = {WORDLE: [{'l': 'a'}]}
    drain.redis.broken = {WORDLE}
    code, out = drain()
    _alerted(code, out, 'redis_wordle')
    assert drain.tables.data(WORDLE_TABLE) == [[{'l': 'a'}]]
    drain.redis.broken = set()
    code, out = drain()
    _ok(code, out, 'redis_wordle')
    assert '1 rows stored, 1 already there' in out
    assert drain.tables.data(WORDLE_TABLE) == [[{'l': 'a'}]] * 2
    assert drain.redis.streams[WORDLE] == []


def test_redis_being_down_alerts(monkeypatch, drain):
    import time

    def no_route():
        raise redis.ConnectionError('no route')

    monkeypatch.setattr(drain.redis, 'ping', no_route)
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    code, out = drain()
    _alerted(code, out, 'redis_wordle')
