"""Hourly health checks against JawsDB, Redis and the live site.

Run by Heroku Scheduler:

    python -m monitoring.run_checks

Prints one JJ_ALERT per breach and exactly one JJ_PULSE at the end - always,
even when checks failed. The two signals have to stay independent: JJ_ALERT
says "something is wrong", while the JJ_PULSE inactivity alert says "nothing
is reporting at all". An outage must not be able to suppress the second.

Imports are deliberately lean, and `extensions` in particular is avoided:
importing it builds a connection pool and pings Redis through a five-attempt
retry loop. This runs about 720 times a month on a metered one-off dyno, so
pandas alone would add seconds of billed import time to every single run.
"""

import operator
import os
import sys
import time
from datetime import datetime

import mysql.connector
import pytz
import redis
import requests
import yaml

import config
from monitoring import alerts

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS_PATH = os.path.join(APP_ROOT, 'datasets', 'health_checks.yaml')

COMPARATORS = {
    '>=': operator.ge,
    '<=': operator.le,
    '>': operator.gt,
    '<': operator.lt,
    '==': operator.eq,
    '!=': operator.ne,
}

# Streams drained nightly by redis_wordle.py. A backlog well past a day's
# traffic means the drain stopped reconciling and is no longer deleting.
REDIS_STREAMS = ('wordle_logging', 'antiwordle_logging')
REDIS_BACKLOG_MAX = int(os.environ.get('REDIS_BACKLOG_MAX', '20000'))

WEB_TIMEOUT_SECONDS = 15


class Results:
    """Tallies outcomes so the closing pulse can summarise the run."""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def ok(self, name):
        self.passed += 1
        print("ok    {}".format(name), flush=True)

    def skip(self, name, why):
        self.skipped += 1
        print("skip  {} ({})".format(name, why), flush=True)

    def fail(self, name, sev='warn', **fields):
        self.failed += 1
        alerts.alert(name, sev=sev, **fields)


def load_checks(path=CHECKS_PATH):
    with open(path, 'r') as handle:
        return yaml.safe_load(handle) or {}


def db_config():
    """Connection args for a one-shot connection, without the pool-only keys."""
    if config.IS_HEROKU:
        cfg = dict(config.MYSQL_POOL_CONFIG)
        for key in ('pool_name', 'pool_size', 'pool_reset_session'):
            cfg.pop(key, None)
        return cfg
    return dict(config.MYSQL_CONFIG)


def in_active_window(spec, hour_pst):
    """Is this check in scope at this PST hour?

    Keeps the daily ETL checks quiet between midnight and the job actually
    landing. A window whose start is after its end wraps around midnight.
    """
    window = spec.get('only_between_pst')
    if not window:
        return True
    start, end = window
    if start <= end:
        return start <= hour_pst <= end
    return hour_pst >= start or hour_pst <= end


def evaluate(spec, value):
    """Compare a measured value against its spec. Returns (passed, reason)."""
    if value is None:
        # An empty result usually means the table is empty or the query no
        # longer matches the schema. Either is worth hearing about.
        return False, 'query returned no rows'
    symbol = spec.get('compare', '>=')
    compare = COMPARATORS.get(symbol)
    if compare is None:
        return False, 'unknown comparator {}'.format(symbol)
    if compare(value, spec['threshold']):
        return True, None
    return False, 'breached'


def run_sql_checks(results, checks, hour_pst):
    """Run every SQL-backed check over a single connection."""
    conn = mysql.connector.connect(**db_config())
    try:
        cursor = conn.cursor()
        try:
            # MySQL 8 serves information_schema table statistics from a cache
            # with a 24-hour default expiry, so db_size_mb would alert on a
            # day-old number - or stay quiet on one. Read through to the
            # storage engine instead. Session-scoped; costs two slower
            # information_schema queries per run and nothing else.
            try:
                cursor.execute("SET SESSION information_schema_stats_expiry = 0")
            except Exception as e:
                # Not fatal: the size check still works, just staler.
                results.fail('checks_stats_expiry', exc=type(e).__name__, msg=e,
                             note='size checks may report cached values')

            for name, spec in checks.items():
                # One malformed entry must cost one check, not the whole run.
                # Reading spec['sql'] or spec['threshold'] outside this try
                # would let a KeyError escape the loop, and every check after
                # it in the file would silently never run.
                try:
                    if not in_active_window(spec, hour_pst):
                        results.skip(name, 'outside PST window {}'.format(
                            spec['only_between_pst']))
                        continue
                    sql = spec['sql'].strip()
                    if not sql.upper().startswith('SELECT'):
                        # Structural, not a matter of care: a monitoring run
                        # must never be able to modify the database it is
                        # inspecting, whatever a future edit to the YAML says.
                        results.fail(name, sev='crit',
                                     note='check SQL must be a SELECT')
                        continue

                    cursor.execute(sql)
                    row = cursor.fetchone()
                    # Drain anything left over, or the next execute is illegal.
                    cursor.fetchall()

                    value = row[0] if row else None
                    passed, reason = evaluate(spec, value)
                    if passed:
                        results.ok(name)
                    else:
                        results.fail(
                            name,
                            sev=spec.get('sev', 'warn'),
                            actual=value,
                            compare=spec.get('compare', '>='),
                            threshold=spec['threshold'],
                            note=spec.get('description') or reason,
                        )
                except Exception as e:
                    results.fail(name, sev='crit', exc=type(e).__name__, msg=e,
                                 note='check failed')
                    continue
        finally:
            cursor.close()
    finally:
        conn.close()


def check_redis(results):
    """Redis carries wordle and antiwordle logging; silence there is silent loss."""
    try:
        client = redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT, password=config.REDIS_PASS,
            decode_responses=True, socket_timeout=5, socket_connect_timeout=5,
        )
        client.ping()
    except Exception as e:
        results.fail('redis_up', sev='crit', exc=type(e).__name__, msg=e)
        return
    results.ok('redis_up')

    for stream in REDIS_STREAMS:
        try:
            length = client.xlen(stream)
        except Exception as e:
            results.fail('redis_backlog', stream=stream, exc=type(e).__name__, msg=e)
            continue
        if length > REDIS_BACKLOG_MAX:
            results.fail('redis_backlog', stream=stream, actual=length,
                         threshold=REDIS_BACKLOG_MAX,
                         note='redis_wordle.py may have stopped draining')
        else:
            results.ok('redis_backlog:' + stream)


def check_web(results):
    """Proves the web dyno is serving, which no database query can tell us."""
    try:
        response = requests.get(
            config.HEALTH_WEB_URL,
            timeout=WEB_TIMEOUT_SECONDS,
            headers={'User-Agent': 'jj-healthcheck'},
        )
    except Exception as e:
        results.fail('web_up', sev='crit', url=config.HEALTH_WEB_URL,
                     exc=type(e).__name__, msg=e)
        return
    if response.status_code != 200:
        results.fail('web_up', sev='crit', url=config.HEALTH_WEB_URL,
                     status=response.status_code)
    else:
        results.ok('web_up')


def main():
    started = time.monotonic()
    results = Results()
    hour_pst = datetime.now(pytz.timezone('America/Los_Angeles')).hour

    try:
        checks = load_checks()
    except Exception as e:
        results.fail('checks_config', sev='crit', exc=type(e).__name__, msg=e)
        checks = {}

    if checks:
        try:
            run_sql_checks(results, checks, hour_pst)
        except Exception as e:
            # Could not connect at all: one alert rather than one per check.
            # The pulse below still fires, which keeps this distinguishable
            # from the runner never having started.
            results.fail('checks_db', sev='crit', exc=type(e).__name__, msg=e,
                         note='could not run SQL checks')

    check_redis(results)
    check_web(results)

    alerts.pulse(
        'checks',
        ok=results.passed, alert=results.failed, skip=results.skipped,
        hour_pst=hour_pst, ms=int((time.monotonic() - started) * 1000),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
