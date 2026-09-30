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
WEB_CHECKS_PATH = os.path.join(APP_ROOT, 'datasets', 'health_web.yaml')

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

# Distinctive so probe traffic stays filterable in app_visits. The solver pages
# call log_page_visit on GET, so probing them hourly writes rows; anything that
# reasons about real usage has to exclude this, or a robot masks an outage.
PROBE_USER_AGENT = 'jj-healthcheck'

# Ceiling on the whole run, checked only when nothing else failed - see main().
RUN_BUDGET_MS = 30000

# Ceiling on any single check query. The whole run is ~157ms of work, so 30s is
# roughly 190x headroom and can only ever fire on a genuine hang - but it is
# well inside the scheduler's `timeout 600`, which is the point: the run has to
# survive to print its pulse.
QUERY_TIMEOUT_MS = 30000


# Width of the name column in ok and skip lines, so the k=v detail lines up
# down the run and a column of numbers can be read at a glance.
NAME_WIDTH = 24


def _pad(name):
    """Name padded to the column, always with at least one trailing space.

    ljust on its own runs the name straight into the detail when the name is
    longer than the column, which `redis_backlog:antiwordle_logging` is.
    """
    return name.ljust(NAME_WIDTH - 1) + " "


def _state_line(name, fields):
    """A check name padded to a column, followed by scrubbed k=v detail."""
    if not fields:
        return name
    detail = " ".join("{}={}".format(k, alerts.scrub(v)) for k, v in fields.items())
    return _pad(name) + detail


class Results:
    """Tallies outcomes so the closing pulse can summarise the run."""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def ok(self, name, **fields):
        """Record a pass, carrying what was actually measured.

        A bare "ok" proves a check ran and nothing else. Printing the value
        turns Papertrail into a history: searching `ok db_size_mb` gives the
        daily trend, so drift is visible months before it breaches - which is
        the whole point of a threshold that moves at 20 MB a month.

        These lines deliberately carry no JJ_ token. They are state, not
        alerts, and must not match the Papertrail search that sends email.
        alerts.scrub() guarantees they cannot: a value containing JJ_ is
        rewritten to JJ. before it is printed.
        """
        self.passed += 1
        print("ok    {}".format(_state_line(name, fields)), flush=True)

    def skip(self, name, why):
        self.skipped += 1
        print("skip  {}({})".format(_pad(name), why), flush=True)

    def fail(self, name, sev='warn', **fields):
        self.failed += 1
        alerts.alert(name, sev=sev, **fields)


def load_checks(path=CHECKS_PATH):
    with open(path, 'r') as handle:
        return yaml.safe_load(handle) or {}


def load_web_checks(path=WEB_CHECKS_PATH):
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

            # Bound every check query so a hang costs one check, not the run.
            # Without this, a query that never returns blocks the loop: the
            # checks after it never run and the closing pulse never prints, so
            # the scheduler's `timeout 600` kills the dyno and the only signal
            # left is the 90-minute inactivity alert - "nothing is reporting",
            # which is true, useless, and does not name the query that hung.
            # With it, MySQL raises after QUERY_TIMEOUT_MS, the per-check except
            # below catches it, and the check names itself while the rest run.
            try:
                cursor.execute(
                    "SET SESSION max_execution_time = {}".format(QUERY_TIMEOUT_MS))
            except Exception as e:
                # Not fatal: checks still run, just unbounded as they were before.
                results.fail('checks_query_timeout', exc=type(e).__name__, msg=e,
                             note='check queries are not time-bounded')

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

                    # Timed so every check reports how long it took, passing or
                    # failing. This is the only database latency signal there
                    # is: a check that starts taking seconds says the database
                    # is unwell well before any threshold is breached.
                    started_check = time.monotonic()
                    cursor.execute(sql)
                    row = cursor.fetchone()
                    # Drain anything left over, or the next execute is illegal.
                    cursor.fetchall()
                    ms = int((time.monotonic() - started_check) * 1000)

                    value = row[0] if row else None
                    passed, reason = evaluate(spec, value)
                    if passed:
                        results.ok(
                            name,
                            actual=value,
                            compare=spec.get('compare', '>='),
                            threshold=spec['threshold'],
                            ms=ms,
                        )
                    else:
                        results.fail(
                            name,
                            sev=spec.get('sev', 'warn'),
                            actual=value,
                            compare=spec.get('compare', '>='),
                            threshold=spec['threshold'],
                            ms=ms,
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
    started = time.monotonic()
    try:
        client = redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT, password=config.REDIS_PASS,
            decode_responses=True, socket_timeout=5, socket_connect_timeout=5,
        )
        client.ping()
    except Exception as e:
        results.fail('redis_up', sev='crit', exc=type(e).__name__, msg=e)
        return
    results.ok('redis_up', ms=int((time.monotonic() - started) * 1000))

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
            # The backlog length matters even when it passes: it is the only
            # view of whether the nightly drain is keeping up or slowly losing.
            results.ok('redis_backlog:' + stream, actual=length,
                       compare='<=', threshold=REDIS_BACKLOG_MAX)


def _fetch(url):
    """One probe request. Returns (response, elapsed_ms)."""
    started = time.monotonic()
    response = requests.get(
        url,
        timeout=WEB_TIMEOUT_SECONDS,
        headers={'User-Agent': PROBE_USER_AGENT},
    )
    return response, int((time.monotonic() - started) * 1000)


def check_web(results, probes):
    """Proves the site is serving, rendering, and doing it quickly.

    Three failures, reported separately because they mean different things:

    * unreachable or non-200 - the dyno is down or erroring.
    * 200 but `expect` missing - it served a page that did not render. A solver
      whose template loads while its engine returns nothing answers 200 all day.
    * 200, correct, but slow - the only performance signal there is. Nothing
      else in this application measures how long anything takes, so a page that
      gets ten times slower is otherwise indistinguishable from a healthy one.
    """
    base = config.HEALTH_WEB_URL.rstrip('/')

    for name, spec in probes.items():
        check = 'web:' + name
        # One malformed probe costs one probe, not the rest of the file -
        # the same reasoning as the SQL loop above.
        try:
            sev = spec.get('sev', 'warn')
            url = base + spec['path']

            try:
                response, ms = _fetch(url)
            except Exception as e:
                results.fail(check, sev=sev, url=url, exc=type(e).__name__, msg=e)
                continue

            if response.status_code != 200:
                results.fail(check, sev=sev, url=url,
                             status=response.status_code, ms=ms)
                continue

            expect = spec.get('expect')
            if expect and expect not in response.text:
                results.fail(check, sev=sev, url=url, ms=ms, expect=expect,
                             note='page served but expected content missing')
                continue

            if ms > spec['max_ms']:
                # Measure twice before calling it slow. The dyno is restarted
                # twice a day by .github/workflows/restart-dyno.yml, and the
                # first request after a boot pays for data.py loading its CSVs,
                # so a single slow sample is a cold start rather than evidence.
                try:
                    response, ms = _fetch(url)
                except Exception as e:
                    results.fail(check, sev=sev, url=url,
                                 exc=type(e).__name__, msg=e)
                    continue
                if ms > spec['max_ms']:
                    results.fail(check, sev=sev, url=url, actual=ms,
                                 compare='<=', threshold=spec['max_ms'],
                                 note='page is slow')
                    continue

            results.ok(check, actual=ms, compare='<=', threshold=spec['max_ms'])
        except Exception as e:
            results.fail(check, sev='crit', exc=type(e).__name__, msg=e,
                         note='probe failed')
            continue


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

    try:
        probes = load_web_checks()
    except Exception as e:
        results.fail('web_config', sev='crit', exc=type(e).__name__, msg=e)
        probes = {}
    if probes:
        check_web(results, probes)

    # A slow run while every check passes is the one case the per-check timings
    # do not shout about: nothing breached, but the whole thing is drifting.
    # When checks did fail, their own alerts already account for the time - a
    # probe timing out costs 15 seconds by itself - so this stays quiet rather
    # than piling a second alert on top of an explanation you already have.
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if results.failed == 0 and elapsed_ms > RUN_BUDGET_MS:
        results.fail('checks_slow', actual=elapsed_ms, compare='<=',
                     threshold=RUN_BUDGET_MS,
                     note='every check passed but the run itself is slow')

    alerts.pulse(
        'checks',
        ok=results.passed, alert=results.failed, skip=results.skipped,
        hour_pst=hour_pst, ms=elapsed_ms,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
