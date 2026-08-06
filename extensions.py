"""Shared app services: cache, rate limiter, basic auth, MySQL pool, Redis.

cache and limiter use the init_app pattern; app.py binds them to the Flask app.
"""

import json
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

if config.IS_HEROKU:
    # Creating a connection pool
    cnxpool = mysql.connector.pooling.MySQLConnectionPool(**config.MYSQL_POOL_CONFIG)

    def get_db_connection():
        return cnxpool.get_connection()
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

def log_page_visit(page_name):
    referrer = request.headers.get('Referer', 'No referrer')
    user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'

    try:
        with db_cursor() as (conn, cursor):
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
    except mysql.connector.PoolError:
        # Pool exhausted - just skip logging, don't break the app
        print("Skipping page visit log - connection pool busy")
    except mysql.connector.Error as err:
        print("MySQL Error:", err)
    except Exception as e:
        print("Unexpected Error:", e)

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
