"""Nightly drain of the two Redis logging streams into MySQL.

No email since 2026-11-25. A run that fails raises, which run_job turns into
an alert; a run that never happened, or a stream left uncleared, is caught by
the redis_drain check in monitoring/run_checks.py.
"""

from collections import Counter
from datetime import datetime
import mysql.connector
import os
import redis
import json
import time
import traceback

if 'IS_HEROKU' in os.environ:
    # Running on Heroku, load values from Heroku Config Vars
    db_config = {
        'user': os.environ.get('jawsdb_user'),
        'password': os.environ.get('jawsdb_pass'),
        'host': os.environ.get('jawsdb_host'),
        'database': os.environ.get('jawsdb_db'),
        'raise_on_warnings': True
        }
    REDIS_HOST = os.environ.get('REDIS_HOST')
    REDIS_PORT = os.environ.get('REDIS_PORT')
    REDIS_PASS = os.environ.get('REDIS_PASS')
else:
    # Running locally, load values from secret_pass.py
    import sys
    script_directory = os.path.dirname(os.path.abspath(__file__))
    root_directory = os.path.dirname(script_directory)
    sys.path.append(root_directory)
    import secret_pass
    db_config = {
        'user': secret_pass.mysql_user,
        'password': secret_pass.mysql_pass,
        'host': secret_pass.mysql_host,
        'database': secret_pass.mysql_db,
        'raise_on_warnings': True
        }
    REDIS_HOST = secret_pass.REDIS_HOST
    REDIS_PORT = secret_pass.REDIS_PORT
    REDIS_PASS = secret_pass.REDIS_PASS

# (Redis stream, MySQL table it drains into)
STREAMS = (
    ('wordle_logging', 'wordle_revamp_clicks'),
    ('antiwordle_logging', 'antiwordle_revamp_clicks'),
)

max_retries = 5
retry_delay = 2  # seconds

for attempt in range(max_retries):
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASS, decode_responses=True, socket_timeout=10)
        print(r.ping())  # Test the connection
        break  # Connection was successful, exit the loop
    except Exception as e:
        print(f"Attempt {attempt+1}: Error connecting to Redis: {e}")
        if attempt < max_retries - 1:
            print(f"Retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)
        else:
            print("Failed to connect after several attempts.")
            raise


def as_value(data):
    """JSON text in one form, for comparing: MySQL hands it back with its keys
    in an order of its own."""
    return json.dumps(json.loads(data), sort_keys=True) if data else data


def drain(stream_name, table):
    """Move one stream's entries into its MySQL table, then take those entries
    out of the stream. Raises if anything goes wrong, and is safe to run again:
    an entry stays in Redis until its row is committed, and a row committed by
    a run that then couldn't clear the stream is not stored a second time."""

    ##### pull the data from redis #####

    entries = r.xrevrange(stream_name)
    rows = [(datetime.strptime(entry_data['datetime'], "%Y-%m-%d %H:%M:%S"),
             json.dumps(json.loads(entry_data['json'])))
            for _entry_id, entry_data in entries]
    print(f'{stream_name}: {len(rows)} entries in redis')
    if not rows:
        return

    ##### import the data into MySQL #####

    conn = mysql.connector.connect(**db_config)
    try:
        cursor = conn.cursor()

        # The entries an earlier run already stored. Only this script adds to
        # the table, so they can only be its newest rows. Counted rather than
        # looked up: two entries can be alike (a double tap).
        cursor.execute(f"SELECT click_time, data_dict FROM {table} ORDER BY id DESC LIMIT %s", (len(rows),))
        stored = Counter((click_time, as_value(data)) for click_time, data in cursor.fetchall())
        new_rows = []
        for click_time, data in rows:
            key = (click_time, as_value(data))
            if stored[key]:
                stored[key] -= 1
            else:
                new_rows.append((click_time, data))

        if new_rows:
            cursor.executemany(f"INSERT INTO {table} (click_time, data_dict) VALUES (%s, %s)", new_rows)
            if cursor.rowcount != len(new_rows):
                # Before the commit, so none of them is kept.
                raise RuntimeError(f'{table} took {cursor.rowcount} of {len(new_rows)} rows')
        conn.commit()
    finally:
        conn.close()
    print(f'{table}: {len(new_rows)} rows stored, {len(rows) - len(new_rows)} already there')

    ##### delete the data from redis #####

    # Only the entries read above: players may have added more since.
    r.xdel(stream_name, *[entry_id for entry_id, _entry_data in entries])
    print(f"Cleared {len(entries)} entries from the Redis stream: {stream_name}")


errors = []
for stream_name, table in STREAMS:
    # One stream's trouble doesn't cost the other its drain.
    try:
        drain(stream_name, table)
    except Exception as e:
        traceback.print_exc()
        errors.append(f'{stream_name}: {type(e).__name__}: {e}')
if errors:
    raise RuntimeError('; '.join(errors))


# TODO - intermittend redis connections issues? redis.exceptions.ConnectionError: Error 11002 connecting to ***REDIS_HOST*** getaddrinfo failed.
