"""Daily retention pass over the append-only logging tables.

JawsDB Leopard Shared meters two things: 1 GB of storage and 15 concurrent
connections. Queries and I/O are unmetered, so storage is the only resource
this app can actually exhaust - and nothing here has ever pruned anything.

Run by Heroku Scheduler:

    python -m monitoring.prune              # delete
    python -m monitoring.prune --dry-run    # count what would be deleted

Deletes in chunks so no single statement holds a long lock, and stops after
MAX_ROWS_PER_RUN so the first pass over a large table cannot run for an hour
on a metered one-off dyno. Whatever is left is picked up tomorrow.

Note that DELETE does not return space to disk: InnoDB frees the pages for
reuse inside the tablespace, but the .ibd file only shrinks after a one-time
OPTIMIZE TABLE, which locks and so wants a quiet moment.
"""

import sys
import time

import mysql.connector

from monitoring import alerts
from monitoring.run_checks import db_config

# (table, timestamp column, days to keep, why that is safe)
#
# youtube_trending and youtube_trending_revamp are deliberately absent: they
# are the actual analytics dataset, functions/youtube_stats.py does 30-day
# lookbacks over them, and at ~150 rows a day they are not what fills a gigabyte.
RETENTION = (
    ('app_visits', 'submit_time', 90,
     'deepest view lookback is 28 days (vw_prod_blossom_search_source)'),
    ('blossom_solver_clicks', 'click_time', 180,
     'views look back 28-29 days; the margin preserves trend comparisons'),
    # 180 by decision on 2026-09-24, after a hand purge to 365 showed that
    # age-based retention recovers little here: the bulk of
    # antiwordle_revamp_clicks is in recent rows, not old ones (README step 5).
    ('wordle_revamp_clicks', 'click_time', 180,
     'no view reads this today'),
    ('antiwordle_revamp_clicks', 'click_time', 180,
     'no view reads this today'),
)

CHUNK_ROWS = 10000
MAX_ROWS_PER_RUN = 500000

# Table and column names are interpolated rather than bound, because MySQL
# will not parameterise identifiers. They come from the RETENTION literal
# above and never from a request, which is the same reasoning that makes the
# {table} interpolation in routes/blossom.py safe.
CUTOFF = "CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles') - INTERVAL %s DAY"


def count_prunable(cursor, table, column, days):
    cursor.execute(
        "SELECT COUNT(*) FROM {} WHERE {} < {}".format(table, column, CUTOFF),
        (days,),
    )
    return cursor.fetchone()[0]


def prune_table(conn, cursor, table, column, days):
    """Delete in chunks. Returns (rows_deleted, hit_cap)."""
    total = 0
    while total < MAX_ROWS_PER_RUN:
        cursor.execute(
            "DELETE FROM {} WHERE {} < {} LIMIT {}".format(
                table, column, CUTOFF, CHUNK_ROWS),
            (days,),
        )
        conn.commit()
        deleted = cursor.rowcount
        total += deleted
        if deleted < CHUNK_ROWS:
            return total, False
    return total, True


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    dry_run = '--dry-run' in argv

    started = time.monotonic()
    pruned = 0
    failures = 0

    try:
        conn = mysql.connector.connect(**db_config())
    except Exception as e:
        alerts.alert('prune', sev='crit', exc=type(e).__name__, msg=e,
                     note='could not connect')
        return 1

    try:
        cursor = conn.cursor()
        try:
            for table, column, days, _why in RETENTION:
                try:
                    if dry_run:
                        count = count_prunable(cursor, table, column, days)
                        print("would delete {:>8} rows from {} older than {}d".format(
                            count, table, days), flush=True)
                        continue

                    deleted, hit_cap = prune_table(conn, cursor, table, column, days)
                    pruned += deleted
                    print("deleted {:>8} rows from {} older than {}d{}".format(
                        deleted, table, days,
                        ' (hit per-run cap, resuming tomorrow)' if hit_cap else ''),
                        flush=True)
                except Exception as e:
                    # One missing or renamed table must not stop the others.
                    failures += 1
                    alerts.alert('prune', sev='warn', table=table,
                                 exc=type(e).__name__, msg=e)
        finally:
            cursor.close()
    finally:
        conn.close()

    alerts.pulse('prune', rows=pruned, failed=failures,
                 dry_run=int(dry_run),
                 ms=int((time.monotonic() - started) * 1000))
    return 0


if __name__ == "__main__":
    sys.exit(main())
