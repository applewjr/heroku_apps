"""Shared app services: cache, rate limiter, basic auth, MySQL pool, Redis.

cache and limiter use the init_app pattern; app.py binds them to the Flask app.
"""

import atexit
import json
import queue
import threading
import time
from contextlib import contextmanager
from datetime import datetime

import mysql.connector
import mysql.connector.pooling
from mysql.connector import Error
import pytz
import redis
from flask import Response, request
from flask_caching import Cache
from flask_httpauth import HTTPBasicAuth
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import config
from monitoring import alerts

##### cache / rate limiter #####

cache = Cache()  # SimpleCache is fine for single-process environments

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["2000 per day", "500 per hour"],
    storage_uri="memory://"  # Use in-memory storage
)

# Limits are keyed on the Cloudflare edge IP, not the visitor: gunicorn takes
# the last X-Forwarded-For hop and ProxyFix doesn't rewrite it. So every budget
# below is shared by all visitors behind the same Cloudflare POP, and the
# solver pages POST once per keystroke (~45/min for one active player), which
# the 500/hour default can't cover. Note flask-limiter's override_defaults is
# True by default: a decorated limit replaces the defaults rather than adding
# to them, so these strings carry their own daily backstop.
INTERACTIVE_LIMITS = "120 per minute; 1500 per hour; 6000 per day"

# 404s are mostly scanner traffic that shares a key with real visitors, and
# Googlebot arrives through the same POP - answering a crawl with 429 instead
# of 404 keeps dead URLs alive in the index, so keep this loose.
NOT_FOUND_LIMITS = "60 per minute; 600 per hour"

##### logins #####

auth = HTTPBasicAuth()

@auth.verify_password
def verify_password(username, password):
    return password == config.GOOGLE_FORM_PASS

@auth.error_handler
def custom_error():
    return Response(
    'Could not verify your access level for that URL.\n'
    'You have to login with proper credentials', 401,
    {'WWW-Authenticate': 'Basic realm="Login Required"'})

##### MySQL #####

# The pool is built on first use, not at import. MySQLConnectionPool's
# constructor eagerly opens pool_size connections, so building it at import
# meant a JawsDB outage during a dyno boot took the entire app down rather
# than just the database-backed pages - and restart-dyno.yml restarts the web
# dyno twice a day, so that window is real. Lazily, a failed build leaves
# _cnxpool None and the next request retries, so a blip self-heals.
_cnxpool = None
_cnxpool_lock = threading.Lock()


def _get_pool():
    global _cnxpool
    pool = _cnxpool
    if pool is not None:
        return pool
    with _cnxpool_lock:
        if _cnxpool is None:
            try:
                _cnxpool = mysql.connector.pooling.MySQLConnectionPool(**config.MYSQL_POOL_CONFIG)
            except Exception as e:
                alerts.alert_throttled('db_pool_init', sev='crit', exc=type(e).__name__, msg=e)
                raise
        return _cnxpool


if config.IS_HEROKU:
    def get_db_connection():
        return _get_pool().get_connection()
else:
    def get_db_connection():
        try:
            connection = mysql.connector.connect(**config.MYSQL_CONFIG)
            if connection.is_connected():
                return connection
        except Error as e:
            print(f"Error connecting to MySQL: {e}")
            return None

@contextmanager
def db_cursor():
    """Yield (conn, cursor), guaranteeing both are closed afterwards.

    Exceptions (including PoolError on an exhausted pool) propagate to the
    caller, which decides whether the operation is best-effort or fatal.
    """
    conn = get_db_connection()
    cursor = None
    try:
        cursor = conn.cursor()
        yield conn, cursor
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()

##### write-behind logging #####

# Visit and click logging used to run inline: an INSERT, an explicit COMMIT
# (mysql-connector sends one even under autocommit), and a session reset when
# the pooled connection is returned - roughly three round trips to JawsDB in
# front of the response. On /blossom, which POSTs per keystroke, that sat
# between the user typing and the solver answering.
#
# Rows now go onto a bounded queue that one drain thread batches, so the
# request path never waits on the database to log anything, and a database
# outage can no longer slow down pages that merely log a visit.

_LOG_QUEUE_MAX = 2000
_LOG_BATCH_MAX = 50
_LOG_BATCH_SECONDS = 2.0

_log_queue = queue.Queue(maxsize=_LOG_QUEUE_MAX)
_drain_thread = None
_drain_lock = threading.Lock()

APP_VISITS_SQL = """
INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
VALUES (%s, %s, %s, %s);
"""


def pst_now_str():
    """PST timestamp in the format MySQL DATETIME expects.

    Callers stamp rows at enqueue time rather than letting the INSERT use
    CONVERT_TZ(NOW(), ...). If the drain backs up behind a slow database,
    server-side NOW() would stamp a burst of queued rows with the flush time -
    distorting vw_prod_blossom_hourly_average and potentially masking the very
    outage that caused the backup.
    """
    return datetime.now(pytz.timezone('America/Los_Angeles')).strftime('%Y-%m-%d %H:%M:%S')


def _drain_batch(batch):
    """Write one batch, grouped by statement so each becomes one executemany."""
    by_sql = {}
    for sql, params in batch:
        by_sql.setdefault(sql, []).append(params)
    with db_cursor() as (conn, cursor):
        for sql, rows in by_sql.items():
            try:
                cursor.executemany(sql, rows)
            except Exception:
                # Batching means one unwritable row - an over-long referrer,
                # say - would otherwise take the other 49 down with it. Retry
                # individually and drop only what genuinely cannot be written.
                written = 0
                for row in rows:
                    try:
                        cursor.execute(sql, row)
                        written += 1
                    except Exception:
                        pass
                if written == 0:
                    raise  # not one bad row: the connection or table is gone
        conn.commit()


def _drain_loop():
    while True:
        batch = [_log_queue.get()]
        deadline = time.monotonic() + _LOG_BATCH_SECONDS
        while len(batch) < _LOG_BATCH_MAX:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                batch.append(_log_queue.get(timeout=remaining))
            except queue.Empty:
                break
        try:
            _drain_batch(batch)
        except Exception as e:
            # Losing best-effort analytics must never kill this thread. If it
            # died the queue would fill in silence and every later row would
            # be dropped, so swallow, report, and keep draining.
            alerts.alert_throttled('log_drain_failed', exc=type(e).__name__, msg=e, rows=len(batch))


def _ensure_drain():
    """Start the drain thread on first use, and restart it if it ever died."""
    global _drain_thread
    thread = _drain_thread
    if thread is not None and thread.is_alive():
        return
    with _drain_lock:
        if _drain_thread is None or not _drain_thread.is_alive():
            _drain_thread = threading.Thread(target=_drain_loop, name='db-log-drain', daemon=True)
            _drain_thread.start()


def enqueue_write(sql, params):
    """Queue one best-effort row. Never blocks, never raises."""
    _ensure_drain()
    try:
        _log_queue.put_nowait((sql, params))
    except queue.Full:
        # The database has been unreachable long enough to back up 2000 rows.
        # Dropping is the right call, but say so - silent data loss is worse
        # than loud data loss.
        alerts.alert_throttled('log_queue_full', queued=_LOG_QUEUE_MAX)


def flush_log_queue(timeout=3.0):
    """Drain whatever is queued now. For atexit and for tests.

    Bounded, because a dyno shutdown must not wait on a slow database. Returns
    True if the queue was emptied.
    """
    deadline = time.monotonic() + timeout
    while True:
        batch = []
        while len(batch) < _LOG_BATCH_MAX:
            try:
                batch.append(_log_queue.get_nowait())
            except queue.Empty:
                break
        if not batch:
            return True
        try:
            _drain_batch(batch)
        except Exception:
            return False
        if time.monotonic() >= deadline:
            return _log_queue.empty()


# A dyno restart (twice daily via restart-dyno.yml, plus Heroku's own cycling)
# would otherwise discard whatever the daemon thread still held.
atexit.register(flush_log_queue)


def log_page_visit(page_name):
    """Record a page view. Best-effort and off the request path.

    The request context is read here, on the request thread: the drain thread
    runs outside the request and has no context to read it from.
    """
    referrer = request.headers.get('Referer', 'No referrer')
    user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
    # Truncate before queueing. Callers now pass formatted exception text, and
    # referrers and user agents are attacker-controlled; an over-long value
    # would be rejected by the column and cost a retry pass in the drain.
    enqueue_write(APP_VISITS_SQL, (
        pst_now_str(), str(page_name)[:200], referrer[:250], user_agent[:250],
    ))

##### redis #####

max_retries = 5
retry_delay = 2 # seconds

for attempt in range(max_retries):
    try:
        r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, password=config.REDIS_PASS, decode_responses=True, socket_timeout=10)
        print(r.ping())  # Test the connection
        break  # Connection was successful, exit the loop
    except Exception as e:
        print(f"Attempt {attempt+1}: Error connecting to Redis: {e}")
        if attempt < max_retries - 1:
            print(f"Retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)
        else:
            print("Failed to connect after several attempts.")

def add_data_to_stream(stream_name, data):
    # Current datetime as a string
    pst_timezone = pytz.timezone('America/Los_Angeles')
    current_datetime_pst = datetime.now(pst_timezone).strftime('%Y-%m-%d %H:%M:%S')

    # Serialize data to JSON format
    json_text = json.dumps(data)

    # Add data to Redis stream with auto-generated ID
    r.xadd(stream_name, {'datetime': current_datetime_pst, 'json': json_text})
