# Monitoring

Everything needed to run this app unattended: what changed, what to set up,
and how to undo it.

The point: **stop checking, start being told.** Alerts are log lines, not
emails. Anything wrong prints one token line to stdout, Heroku's drain carries
it to Papertrail, and Papertrail decides who gets notified. That keeps the
notification channel a UI setting rather than a deploy, and it means the
alerting path has no SMTP failure mode of its own.

```
JJ_ALERT check=youtube_trending_today sev=crit actual=0 compare=">=" threshold=45 note="Daily youtube_trending load, scheduler 12am PST"
JJ_PULSE source=checks ok=11 alert=0 skip=0 hour_pst=14 ms=940
```

`JJ_PULSE` is the important half. It feeds Papertrail's *inactivity* alert,
which fires when logs **stop** - the only check that survives the thing being
monitored dying. Everything else can only report problems it is alive to see.

---

## Do this, in this order

Tick list. Row numbers are the order to work in; the linked section numbers
are just labels and do not match. Deploying alone changes nothing you can see:
the app starts emitting alert lines at step 3, but nothing reaches your inbox
until step 5, and no checks run until step 6.

| # | Step | Where | Detail |
|---|---|---|---|
| 1 | Review and merge the branch | git | - |
| 2 | Deploy to **staging**, run the smoke test | Actions -> Deploy -> `staging` | [1. Deploy](#1-deploy) |
| 3 | Deploy to **prod**, run the same smoke test | Actions -> Deploy -> `prod` | [1. Deploy](#1-deploy) |
| 4 | Scale staging back to 0 | Actions -> Stop staging | [1. Deploy](#1-deploy) |
| 5 | Create the **`JJ_ALERT`** alert | Papertrail | [4. Papertrail](#4-papertrail---two-alerts) |
| 6 | Add **`run_checks`** hourly | Heroku Scheduler | [3. Health check](#3-heroku-scheduler---add-the-health-check) |
| 7 | Confirm `JJ_PULSE` lines are arriving | Papertrail | [3. Health check](#3-heroku-scheduler---add-the-health-check) |
| 8 | Create the **`JJ_PULSE` inactivity** alert | Papertrail | [4. Papertrail](#4-papertrail---two-alerts) |
| 9 | Re-point the existing jobs through `run_job` | Heroku Scheduler | [2. Wrap the jobs](#2-heroku-scheduler---wrap-the-existing-jobs) |
| 10 | Update the dashboard views | Workbench | [7. Dashboard views](#7-dashboard-views) |
| 11 | Test the dead-man's switch | Heroku Scheduler | [8. Test the dead-man's switch](#8-test-the-dead-mans-switch) |

### Why that order

**Steps 5 to 8 are the one sequence that matters.** Create the `JJ_ALERT`
alert early - from step 3 the app is already emitting `http_500` and
`feedback_new` alerts, so you want something listening. But
create the **`JJ_PULSE` inactivity alert last**, and only after step 7
confirms pulses are actually flowing. It fires when no pulse has been seen for
90 minutes, so setting it up before anything emits one means it fires
immediately, on a healthy system.

**Step 9 can trail.** Wrapping the scheduled jobs is independent of everything
else, and the jobs keep working unwrapped - just silently, as they do today.
Wrap one, watch it overnight, then do the rest - the table in
[2. Wrap the jobs](#2-heroku-scheduler---wrap-the-existing-jobs) is a menu,
not a single action.

**Two things about staging** (step 2): it shares the JawsDB with prod, so a
smoke test writes real rows to `app_visits` and `blossom_solver_clicks` -
harmless log rows, but they are real. And the deploy workflow scales the
staging dyno up automatically, which is why step 4 exists to put it back.

**Take staging down at step 4, not at the end.** Its only job is the step-2
smoke test, and step 9 explicitly invites you to spread the remaining work
over days. Left running, a second app holds a second connection pool against
the 15-connection JawsDB cap, burns dyno hours, and - if staging drains to the
same Papertrail - mixes its alerts into yours during the exact window you are
learning what normal traffic looks like. `stop-staging.yml` is a standalone
manual dispatch, so it can run the moment prod is verified.

### Not on the list, deliberately

- **Indexes** - already applied by hand on 2026-09-23.
  See [6. Indexes](#6-indexes---done-2026-09-23).
- **Richer `log_page_visit` columns** - parked for a later pass.
  See [Future: richer page-visit logging](#future-richer-page-visit-logging-not-implemented).
- **Remaining dead tables** (37.6 MB) - optional cleanup, no deadline.
  See [5. Storage](#5-storage---the-numbers-as-of-2026-09-23).

### Where everything else lives

[What changed in the app](#what-changed-in-the-app-and-why) -
[Files](#files) -
[Rollback](#rollback) -
[Tuning](#tuning) -
[JawsDB reference](#jawsdb-reference) -
[What this does not catch](#what-this-does-not-catch)

---

## What changed in the app, and why

### Alerting

| Where | Change |
|---|---|
| `monitoring/` | New package. Alert emission, the job wrapper, and the hourly checks. |
| `datasets/health_checks.yaml` | The checks themselves. SQL inline rather than in views, so a check deploys atomically with the code and needs no manual DDL step. |
| `app.py` | The 500 handler calls `log_page_visit` and emits a throttled `http_500` alert. The 404 handler deliberately does not log - see `routes/misc.py`. |
| `routes/misc.py` | New feedback emits `feedback_new` on write - instant, and with no "which rows have I seen" bookkeeping. If the insert fails, the alert carries the feedback text so it is not lost. |
| `routes/wordgames.py` | `log_page_visit` on the GET branch of wordle, antiwordle, quordle, smush, ribbit, wordiply. |

Three alerts come out of the write-behind path itself, none of them in the
YAML: `log_drain_failed` (a batch could not be written), `log_queue_full`
(rows arriving faster than the drain can write them, so rows are being
dropped) and `log_enqueue_failed` (the queue could not even be reached - in
practice, the drain thread could not be started). All three are throttled to
one every 15 minutes.

**A database outage shows up as `log_drain_failed`, not `log_queue_full`.**
The 2000-row queue is a burst buffer, not an outage buffer: while the database
is down the drain keeps pulling batches and discarding them on failure - the
pool raises `PoolError` instantly inside the 20s `_POOL_RETRY_SECONDS` window -
so rows are dropped as they arrive rather than accumulating. Losing
best-effort analytics during an outage is the intended trade; just do not read
a quiet `log_queue_full` as evidence that nothing was lost.

`log_page_visit` had exactly one call site left (`routes/blossom.py`), so
`vw_prod_errors` and `vw_prod_blossom_errors` matched zero rows - the "Blossom
Errors" panel on `/etl_dash` was reading clean because nothing wrote to it.
Those are real again. Smush had no usage logging of any kind, which is why
`smush_idle` needed instrumentation before it could exist.

### Hardening - prerequisite for the above

Adding `log_page_visit` to more routes is only safe if a database outage
cannot slow them down. Two problems had to be fixed first, both of which
existed already on `/blossom`:

- **`config.py`** now sets `connection_timeout: 5`. Without it, a JawsDB
  outage that drops packets rather than refusing connections blocks on TCP
  connect for the OS default of ~2 minutes. With 8 gunicorn threads stuck
  there and `--timeout 60`, the worker is killed and the whole site stops
  serving - including pages that need no database.
- **`extensions.py`** builds the connection pool lazily.
  `MySQLConnectionPool`'s constructor eagerly opens connections, so building
  it at import meant a JawsDB outage during a dyno boot took the entire app
  down. `restart-dyno.yml` restarts twice daily, so that window was real.
  A failed build is remembered for `_POOL_RETRY_SECONDS` (20). Without that,
  every request during an outage retries the whole build, and because those
  retries serialise on one lock the eighth waiting thread sits eight
  `connection_timeout`s deep - past Heroku's 30s router limit. With it, a
  database page fails in about five seconds instead of queueing, and a blip
  still self-heals on the next window.

**`extensions.py` also moves visit and click logging off the request path.**
It used to be an INSERT, an explicit COMMIT, and a session reset - roughly
three round trips to JawsDB in front of the response. On `/blossom`, which
POSTs per keystroke, that sat between the user typing and the solver
answering. Rows now go onto a bounded queue that one drain thread batches, so
`/blossom` is **faster than before**, not slower. Timestamps are stamped in
Python at enqueue rather than by `NOW()`, so a backed-up drain cannot stamp a
burst of queued rows with the flush time and distort the hourly averages the
idle checks depend on.

### Bug fixes

- **`scheduled_tasks_youtube/youtube_trending_v2.py`** assigned
  `channel_videos = -1` in the `except` for `channel_views`. A missing
  `viewCount` left `channel_views` undefined, so the row build raised
  `NameError` and killed the run *before* any email. The revamp script always
  had this right.
- **`vw_prod_blossom_errors.sql`** hardcoded the schema name
  `ndsvta8po4bdiw50`, the only view that did; it would break on any rename.
- **`vw_prod_youtube_trending_grouped.sql`** ordered by a column not in its
  `GROUP BY`. **This was not actually broken** - your `sql_mode` is
  `NO_ENGINE_SUBSTITUTION` only, with no `ONLY_FULL_GROUP_BY`, so the view
  works today. The change is correctness and future-proofing, not a fix.
  Revert it freely.

---

## Files

| File | What it does |
|---|---|
| `alerts.py` | `alert()`, `pulse()`, `alert_throttled()`. Scrubbing and rate limiting. No import side effects. |
| `run_job.py` | Runs an existing scheduled script under `runpy`, adding a failure alert. No edits to the script. |
| `run_checks.py` | Hourly checks against JawsDB, Redis and the live site. Refuses any check SQL that is not a SELECT. |
| `../datasets/health_checks.yaml` | The checks: SQL, threshold, severity, active window. |

---

## Setup, in order

Nothing below happens automatically.

### 1. Deploy

Normal deploy. No new dependencies, no schema changes, no config vars
required - every new setting has a default. After deploying, the app is
already safer (the hardening above) and already emitting `http_500` and
`feedback_new` alerts, but nothing is listening yet.

#### The smoke test - run it on staging, then again on prod

Worth doing properly rather than glancing at the homepage. The test suite
mocks the database, so **no code in this branch has ever written a row to real
MySQL.** This is what proves the write-behind path works, and it is the one
thing that cannot be checked any earlier.

Staging shares prod's JawsDB, so the staging run is a genuine test - and its
rows are real rows.

Load `/blossom` (GET), type into the solver once (POST), then load `/smush`.
Wait about five seconds for the drain, then:

```sql
SELECT submit_time, page_name, LEFT(referrer, 40)
FROM app_visits ORDER BY id DESC LIMIT 10;

SELECT click_time, must_have, may_have, list_len
FROM blossom_solver_clicks ORDER BY id DESC LIMIT 5;
```

Four things to confirm, each a distinct failure mode:

1. **Rows arrive at all.** Proves the drain thread starts and `executemany`
   works against the real table. Nothing in CI covers this.
2. **Timestamps are current PST wall-clock, not UTC.** This is the one that
   would hurt. Logging moved from `CONVERT_TZ(NOW(), ...)` to Python-side
   `pst_now_str()`; an off-by-seven-hours here silently corrupts
   `vw_prod_blossom_hourly_average` and both idle checks, and nothing else
   would tell you.
3. **`page_name` shows `smush.html`.** Proves the new GET-branch logging in
   `routes/wordgames.py` fires.
4. **Referred 404s log and unreferred ones do not:**

   ```bash
   curl -sS -o /dev/null -H 'Referer: https://example.com/x' <host>/nope-not-a-page
   curl -sS -o /dev/null                                     <host>/nope-not-a-page
   ```

   Exactly one new `error.html (404: nope-not-a-page)` row should appear.

Then confirm the site itself is healthy - `/`, `/blossom`, `/wordle_revamp`
and `/etl_dash` all render. A database-backed page failing while `/` still
serves is the signature of a pool problem rather than a deploy problem.

### 2. Heroku Scheduler - wrap the existing jobs

The four YouTube scripts and `espresso_data_import.py` send their success
email as the last unguarded statement in the file, so any exception before it
kills the job with **no email at all** - the signal is an email that never
arrives, which you have to notice.

Two are already better and are wrapped for consistency rather than rescue:
`mtg_prices_bsky.py` sends its own `FAILED:`/`WARNING:` emails and re-raises,
and `redis_wordle.py` sends two emails with `try`/`except` between them, so a
failure in its second half still produces the first.

Re-point each command through the wrapper; the scripts themselves are
untouched.

| Before | After |
|---|---|
| `python scheduled_tasks_youtube/youtube_trending_v2.py` | `python -m monitoring.run_job scheduled_tasks_youtube/youtube_trending_v2.py` |
| `python scheduled_tasks_youtube/youtube_trending_revamp_v3.py` | `python -m monitoring.run_job scheduled_tasks_youtube/youtube_trending_revamp_v3.py` |
| `python scheduled_tasks_youtube/youtube_backup_v2.py` | `python -m monitoring.run_job scheduled_tasks_youtube/youtube_backup_v2.py` |
| `python scheduled_tasks_youtube/youtube_backup_revamp_v3.py` | `python -m monitoring.run_job scheduled_tasks_youtube/youtube_backup_revamp_v3.py` |
| `python scheduled_tasks_redis/redis_wordle.py` | `python -m monitoring.run_job scheduled_tasks_redis/redis_wordle.py` |
| `python scheduled_tasks_espresso/espresso_data_import.py` | `python -m monitoring.run_job scheduled_tasks_espresso/espresso_data_import.py` |
| `python scheduled_tasks_mtg/mtg_prices_bsky.py` | `python -m monitoring.run_job scheduled_tasks_mtg/mtg_prices_bsky.py` |

`mtg_prices.py` is deliberately absent - it is the Twitter-era predecessor of
`mtg_prices_bsky.py`. Wrap it too if it is still scheduled.

### 3. Heroku Scheduler - add the health check

| Command | Frequency |
|---|---|
| `python -m monitoring.run_checks` | Hourly |

Cost: a lean run is ~10s of work plus ~10s of dyno provisioning, so about
4 dyno-hours a month (720 runs x 20s). Pennies on Basic; free out of the Eco
pool.

This is the only job to schedule.

#### Do not wait an hour to confirm it works

Heroku Scheduler picks its own offset within the hour, so after adding the
entry you may sit for up to 60 minutes with no pulse and no way to tell "not
yet" from "broken." Force one instead:

```bash
heroku run python -m monitoring.run_checks -a apple-apps
```

That prints the same `ok`/`skip` lines and closing `JJ_PULSE` a scheduled run
would, through the same log drain, so it satisfies step 7 in seconds. It is
read-only - the runner refuses any check SQL that is not a `SELECT`, and
touches Redis only through `ping()` and `xlen()` - so it is safe to run
against prod as often as you like.

Expect `12 ok, 0 alerts, 1 skip` on a healthy system. The skip is `db_size_mb`
outside its 09:00 PST window. `blossom_errors_today` and `smush_idle` will
pass trivially at first - nothing has written those rows yet - and only become
meaningful once traffic accumulates.

#### What actually gets checked

Nine checks come from `datasets/health_checks.yaml`: `youtube_trending_today`,
`youtube_grouped_today`, `blossom_idle`, `smush_idle`, `blossom_errors_today`,
`db_size_mb`, `db_connections`, `wordle_drain_fresh`, `antiwordle_drain_fresh`.

Three more are Python rather than SQL, so they live in `run_checks.py` and are
easy to miss when reading the YAML:

| Check | What it proves |
|---|---|
| `redis_up` | Redis answers `ping()`. Wordle and antiwordle logging goes through it, so silence there is silent data loss. |
| `redis_backlog` | `XLEN` on `wordle_logging` and `antiwordle_logging` is under `REDIS_BACKLOG_MAX` (20000). A growing backlog means `redis_wordle.py` stopped reconciling and is no longer draining. |
| `web_up` | `HEALTH_WEB_URL` returns 200. The only check that proves the web dyno is serving, which no database query can tell you. |

Note the SELECT-only guard on YAML checks is a literal prefix test, not a
parser: a check written to start with a comment or a `WITH` CTE would be
rejected as non-SELECT.

### 4. Papertrail - two alerts

1. Search `JJ_ALERT` -> alert, **at least 1** event in 10 minutes -> email.
2. Search `"JJ_PULSE source=checks"` -> alert,
   **"Trigger when ... no new events match"**, 90 minutes -> email.

The second search must be the **quoted phrase**, not bare `JJ_PULSE`.
`run_job` also emits pulses - `JJ_PULSE source=job:youtube_trending_v2` - so a
bare match would let any wrapped nightly job reset the inactivity clock and
mask a dead `run_checks` for hours. Only `source=checks` is the hourly
heartbeat.

90 minutes rather than 60 because the job runs hourly: that allows one late or
slow run without crying wolf, while still catching a single missed run.

**Check the alert quota first.** The Heroku Choklad (free) plan documents no
limit, but Papertrail's own free plan is reported to cap saved searches at 2,
and two already exist. If you hit a cap:

- Fold the existing platform-error alert into the first search:
  `"JJ_ALERT" OR "error code=H" OR "Error R"`, threshold 1 over 10 minutes.
- If still capped, retire the "5 x 500 in 10 minutes" rule. The new
  `check=http_500` emitter supersedes it and reports the *first* 500.

Free-tier facts worth knowing: search retention is 2 days (archives keep 7),
and the volume cap is 10 MB/day. Exceeding the cap stops ingestion, which
trips the inactivity alert - loud, but worth recognising for what it is.

### 5. Storage - the numbers as of 2026-09-23

**330.1 MB of the plan's 1024 MB**, so 693.9 MB of headroom - down from 56% of
quota to 32%. Three cleanups got it there:

| Action | Freed | Running total |
|---|---|---|
| `DROP TABLE spotify_playlists` | 102.2 MB | 574.4 -> 472.2 |
| `antiwordle_revamp_clicks` older than 365 days, then `OPTIMIZE TABLE` | 21.0 MB | 472.2 -> 451.2 |
| `antiwordle_revamp_clicks` older than 180 days, by copy-and-swap | 121.1 MB | 451.2 -> 330.1 |

#### Safe retention windows, if you purge again

Nothing prunes automatically - there is no retention job, by choice. These are
the windows a purge can use without breaking a dashboard, worked out on
2026-09-24 by checking how far back each view actually reads:

| Table | Keep | Why that is safe |
|---|---|---|
| `app_visits` | 90 days | Deepest view lookback is 28 days (`vw_prod_blossom_search_source`) |
| `blossom_solver_clicks` | 180 days | Views look back 28-29 days; the margin preserves trend comparisons |
| `wordle_revamp_clicks` | 180 days | No view reads this today |
| `antiwordle_revamp_clicks` | 180 days | No view reads this today |

`youtube_trending` and `youtube_trending_revamp` are deliberately absent: they
are the actual analytics dataset, `functions/youtube_stats.py` does 30-day
lookbacks over them, and at ~150 rows a day they are not what fills a gigabyte.

Count before you delete, and prefer copy-and-swap over `DELETE` - see
[Maintenance patterns](#maintenance-patterns-best-first) below.

#### Reading sizes correctly

Two traps, both hit during the cleanup above:

- **`information_schema` caches statistics for 24 hours** on MySQL 8
  (`information_schema_stats_expiry`). After the OPTIMIZE the size looked
  completely unchanged, because the cached value was being served. Always run
  `SET SESSION information_schema_stats_expiry = 0;` first. A `DROP TABLE`
  appears to update instantly only because the row leaves the result set
  entirely - there is no cached statistic left to be stale.
- **`table_rows` is an estimate**, sampled from index pages, and a bad one
  here: it read 24,616 against a true 42,127 before the delete, and 14,146
  against a true 28,292 after. Use `SELECT COUNT(*)`. The size columns
  (`data_length`, `index_length`, `data_free`) are trustworthy.

`information_schema.innodb_tablespaces` would give the real file size, but it
needs the `PROCESS` privilege, which a JawsDB shared plan does not grant.

```sql
SET SESSION information_schema_stats_expiry = 0;

SELECT table_name,
       ROUND(data_length/1024/1024, 1)  AS data_mb,
       ROUND(index_length/1024/1024, 1) AS index_mb,
       ROUND(data_free/1024/1024, 1)    AS free_mb
FROM information_schema.tables
WHERE table_schema = DATABASE()
ORDER BY (data_length + index_length) DESC;
```

#### antiwordle_revamp_clicks: the bloat is recent, not old

Still the largest table at roughly **132.6 MB, 40% of the database**, but no
longer dominant. Purging it in two passes mapped where the weight actually sits:

| Age band | Size |
|---|---|
| Older than 365 days | 21.1 MB |
| 180 to 365 days | 121.1 MB |
| Under 180 days (kept) | ~132.6 MB |

The payload grew sharply somewhere around late 2025 - rows older than a year
average ~1.6 KB, recent ones ~9.4 KB. Two years of history cost 21 MB; the six
months before the cutoff cost 121 MB.

**Correction worth recording:** after the 365-day pass freed only 21 MB, the
conclusion here was that age-based retention is a weak lever for this table.
The 180-day pass freed 121 MB and disproved that. Retention works fine; the
window just has to reach into the period where the rows are large. Do not
judge a retention window by a longer one's result.

At current traffic this table adds roughly 130 MB per six months. Nothing
prunes it automatically, so that growth is unbounded until the `data_dict`
payload shrinks or you repeat the manual purge. With a 180-day window it would
instead settle around 130-140 MB.

Measure in **bytes, not rows**, before any future purge. Row counts mislead
badly here because row size varies ~6x across the table:

```sql
SET SESSION information_schema_stats_expiry = 0;
SELECT DATE_FORMAT(click_time, '%Y-%m') AS month,
       COUNT(*) AS rows_,
       ROUND(AVG(LENGTH(data_dict))) AS avg_bytes,
       ROUND(SUM(LENGTH(data_dict))/1024/1024, 1) AS mb
FROM antiwordle_revamp_clicks
GROUP BY DATE_FORMAT(click_time, '%Y-%m')
ORDER BY month DESC;
```

Still open: ~9.4 KB per antiwordle click is a lot, and `wordle_revamp_clicks`
- written by the same job - is only 36.6 MB, so whatever grew grew only on the
antiwordle side. Shrinking what
`scheduled_tasks_redis/redis_wordle.py` serialises into `data_dict` would cap
the problem at source rather than managing it with retention forever.

`blossom_solver_clicks` is 26.5 MB (~650k rows by `table_rows`, so treat the
count as approximate); pruning it saves nothing.

#### Copy-and-swap beats DELETE + OPTIMIZE here

The 180-day pass used it, and it is the method to reach for next time:

```sql
CREATE TABLE antiwordle_new LIKE antiwordle_revamp_clicks;
INSERT INTO antiwordle_new SELECT * FROM antiwordle_revamp_clicks
  WHERE click_time >= CONVERT_TZ(NOW(),'UTC','America/Los_Angeles') - INTERVAL 180 DAY;
RENAME TABLE antiwordle_revamp_clicks TO antiwordle_old,
             antiwordle_new TO antiwordle_revamp_clicks;
-- verify the live table here; RENAME back if anything is wrong
DROP TABLE antiwordle_old;
```

Three advantages over `DELETE` then `OPTIMIZE`: the old data survives until
the explicit `DROP`, so there is a rollback point; the `DROP` frees space
instantly with no rebuild and no stats-cache ambiguity; and it only writes the
rows being kept. `CREATE TABLE ... LIKE` copies indexes but not foreign keys
or triggers - neither exists on this table. Run it outside
`redis_wordle.py`'s nightly window so an insert cannot land in the old table.

#### Dead archived tables: 37.6 MB left

`spotify_tracks` (4.0), `spotify_artists` (1.7),
`lol_participants_challenges` (17.1), `lol_participants_info` (14.1),
`lol_match` (0.4), `lol_summoner` (0.2), `lol_champion` (0.1). All belong to
`_archived_scheduled_tasks_*`, retired 2026-06 and unrunnable. A DROP is
**not reversible**, so dump first:

```
mysqldump -h HOST -u USER -p DB \n  spotify_tracks spotify_artists \n  lol_summoner lol_champion lol_match \n  lol_participants_info lol_participants_challenges \n  > archived_tables.sql
```

**`vw_prod_spotify` is an invalid view** - it selects from the dropped
`spotify_playlists`. Nothing queries it (it is not in
`datasets/etl_dash_queries.yaml`), so it is inert rather than visibly broken.
`mysql_views/dashboard/vw_prod_spotify.sql` has been commented out and marked
ARCHIVED to match; run `DROP VIEW IF EXISTS vw_prod_spotify;` to clear it from
the database too, whenever convenient. `vw_prod_lol` is the same kind of
orphan and would join it if the `lol_*` tables go.

The archived scripts under `_archived_scheduled_tasks_spotify/` are left alone
deliberately: unrunnable since 2026-06, and they reference the table only from
code that cannot execute.

### 6. Indexes - done 2026-09-23

Before this, neither `app_visits.submit_time` nor
`blossom_solver_clicks.click_time` was indexed; both tables carried only a
PRIMARY key on `id`. Three indexes were applied by hand:

```sql
CREATE INDEX idx_app_visits_submit_time
    ON app_visits (submit_time);
CREATE INDEX idx_app_visits_page_name_submit_time
    ON app_visits (page_name, submit_time);
CREATE INDEX idx_blossom_solver_clicks_click_time
    ON blossom_solver_clicks (click_time);
```

They serve the 28-day baseline queries in `health_checks.yaml` and the
dashboard views. They also matter for any future manual purge: a chunked
`DELETE ... LIMIT 10000` re-scans from the start on every chunk without one,
so 50 chunks would mean 50 full table scans.

This is the only **schema change** in the whole piece. To reverse it:

```sql
DROP INDEX idx_app_visits_submit_time ON app_visits;
DROP INDEX idx_app_visits_page_name_submit_time ON app_visits;
DROP INDEX idx_blossom_solver_clicks_click_time ON blossom_solver_clicks;
```

### 7. Dashboard views

`vw_prod_word_solver_page_visits` reports only `blossom.html`, so six of the
seven newly-logged pages stay invisible on `/etl_dash` until this is done.

The repo copy at `mysql_views/dashboard/vw_prod_word_solver_page_visits.sql`
has been rewritten to cover all seven. Paste it into Workbench as-is.

**Do not try to fix the old version by uncommenting.** It carried the extra
page names as commented-out lines, and uncommenting them produced a syntax
error twice over: the live `... AS blossom` line has no trailing comma, and
the last commented line, `... AS quordle_mobile`, has none either. Enabling a
middle subset left both a missing comma and a trailing one. That is why the
file now holds finished text rather than a menu.

Each page needs **two** entries, which is the other half of what made the old
instruction wrong: a `SUM(CASE WHEN ...)` column *and* a name in the
`WHERE page_name IN (...)` list. A column without the `WHERE` entry reads zero
forever.

Dropped on purpose: `blossom_bee.html`, `wordle.html`, `wordle_example.html`,
`antiwordle.html` and `quordle_mobile.html`. Nothing logs them, so they would
be five columns of zeroes. Their historical rows stay in `app_visits` either
way - this only changes what the view surfaces.

Rows only start arriving after the prod deploy, so there is no hurry.
`CREATE OR REPLACE VIEW` is instant and independent of everything else.

### 8. Test the dead-man's switch

Disable the `run_checks` Scheduler entry, wait for the email, then **re-enable
it.** Budget about two hours: the alert fires at 90 minutes of silence.

Re-enabling is the step to not forget - leaving it off leaves you with no
monitoring at all. It is self-correcting if you do forget, because the
inactivity alert keeps firing until pulses resume, but that is a worse way to
find out.

**Why that works:** `run_checks` prints `JJ_PULSE source=checks` at the end of
every hourly run. Papertrail's alert fires when that search matches *nothing*
for 90 minutes, and Papertrail evaluates it on its own servers. Stop the job
and the pulses stop; 90 minutes later Papertrail sees silence and emails.

**Why it is the one test worth doing deliberately:** it is the only alert that
proves an absence. Every other alert needs the app alive enough to report its
own problem. A dead dyno, an unreachable database, a failed boot, a broken log
drain - none of those can emit a `JJ_ALERT`. Only something outside Heroku
notices the quiet.

It is also the alert most likely to be silently misconfigured: a bare
`JJ_PULSE` search instead of the quoted phrase, "at least 1 event" instead of
"no new events match", or a notification never attached. Every one of those
looks fine in the UI and fails only when you need it. An untested dead-man's
switch is worse than none, because you believe you are covered.

---

## Rollback

Everything is reversible except two things, both of which you opt into
separately and neither of which is the code.

| To undo | How |
|---|---|
| All code | `git checkout` the branch. No schema changes, no new dependencies, no required config vars. |
| The view edits | Re-run the previous `CREATE OR REPLACE VIEW` text. Views hold no data. |
| The indexes (applied 2026-09-23) | The three `DROP INDEX` statements in [6. Indexes](#6-indexes---done-2026-09-23). |
| Extra `app_visits` rows | `DELETE FROM app_visits WHERE page_name IN ('smush.html', 'quordle.html', ...)`. Additive data only. |
| Scheduler changes | Point the commands back at the bare scripts. |
| Papertrail alerts | Delete the saved searches. |
| **Dropped archived tables** | **Not reversible.** Restore from the mysqldump. `spotify_playlists` was dropped 2026-09-23. |

Nothing in `monitoring/` writes. `run_checks.py` refuses any check SQL that is
not a `SELECT`, and it touches Redis only through `ping()` and `xlen()`. There
is no delete path anywhere in the package.

## Tuning

**Every check threshold lives in `datasets/health_checks.yaml`, so changing
one is a deploy.** Only two settings are `heroku config:set`-tunable:
`HEALTH_WEB_URL` and `REDIS_BACKLOG_MAX` (default 20000). Nothing else in
`config.py` feeds a threshold.

**`smush_idle` stays inert until 28 days of history exist.** It compares the
current hour against that hour's 28-day average and passes whenever the hour
is not normally busy - and `smush.html` rows only start arriving with this
deploy, so it switches itself on about a month later.

`blossom_idle` uses the same rule but is **live from the first run**:
`blossom_solver_clicks` already holds years of history, so its baseline is
populated immediately.

The hour-awareness is deliberate. A fixed "no usage in N hours" rule either
cries wolf at 4am or sleeps through a lunchtime outage, and an alert that
fires overnight for normal reasons is one you learn to ignore.

`db_connections` warns above 13 of the plan's 15. Prod and staging pools of 5
each plus a Workbench session reaches 12 by design, so 13 warns without firing
at the designed maximum. Observed steady state is 7.

`db_size_mb` warns above 800 of the plan's 1024. That leaves 224 MB free -
more than the largest table (~133 MB) needs for a `DELETE` + `OPTIMIZE`
rebuild, so every remediation option stays open, and at ~260 MB/year it is
about ten months of notice.

**`only_between_pst` doubles as a repeat-rate control.** The Papertrail rule
emails on any `JJ_ALERT` inside 10 minutes, so an hourly check that breaches
emails every hour until it is fixed. That is right for something you fix today
and wrong for something that takes months, so `db_size_mb` carries
`only_between_pst: [9, 9]` - a one-hour window, meaning the 09:00 PST run
evaluates it and no other. One email a day instead of twenty-four.

Worth knowing this applies to any slow-moving check you add later.

## JawsDB reference

What the plan allows, how the server is configured, and which maintenance
patterns actually work. All verified 2026-09-23 against the live database.

### Plan limits

JawsDB **Leopard Shared**, with exactly two metered resources:

| Resource | Limit | Observed |
|---|---|---|
| Storage | 1 GB | 330.1 MB |
| Concurrent connections | 15 | 7 at steady state |

Queries, reads, writes, IOPS and data transfer are **not metered**, so the
hourly health check's ~240 queries a day cost nothing. Storage and connections
are the only things that can run out.

7 is what was observed, not a figure derived from the code - and the pool is
now lazy, so prod holds zero connections until something first touches the
database. What sets the threshold is the designed ceiling: prod and staging
pools of 5 each plus a Workbench session reaches 12, so `db_connections` warns
at 13 to sit just above it rather than on it.

### Server settings

| Setting | Value | Why it matters |
|---|---|---|
| Version | MySQL 8.4.8 | `information_schema` statistics caching applies |
| `innodb_file_per_table` | `1` | Every table has its own `.ibd`, so `DROP` returns space to the OS at once and `OPTIMIZE` genuinely shrinks a file |
| `sql_mode` | `NO_ENGINE_SUBSTITUTION` only | No `ONLY_FULL_GROUP_BY`, no `STRICT_TRANS_TABLES` |
| `@@session.time_zone`, `@@global.time_zone` | `UTC`, `UTC` | Exactly what the app's `CONVERT_TZ(NOW(),'UTC','America/Los_Angeles')` idiom assumes |
| `CONVERT_TZ` with named zones | Works | The timezone tables are loaded. A NULL return here would silently break several health checks |

Two consequences of the loose `sql_mode`:

- **No `ONLY_FULL_GROUP_BY`**, so a view can `ORDER BY` a column outside its
  `GROUP BY` and still run. `vw_prod_youtube_trending_grouped` did that for a
  long time without anyone noticing, because it was never actually a fault.
- **No `STRICT_TRANS_TABLES`**, so an over-long value is *silently truncated*
  rather than rejected. The write-behind drain's per-row retry will therefore
  rarely fire for width violations - but an over-long referrer loses its tail
  without complaint.

### Privileges

| Available | Not available |
|---|---|
| `CREATE INDEX`, `CREATE TABLE`, `RENAME TABLE`, `DROP TABLE`, `DROP VIEW` | `information_schema.innodb_tablespaces` - needs `PROCESS` |
| `OPTIMIZE TABLE`, `ANALYZE TABLE` | Other tenants' threads in `information_schema.processlist` |
| `SET SESSION information_schema_stats_expiry` | |

Not holding `PROCESS` is the reason `processlist` shows only your own
connections - which is precisely what makes the `db_connections` check
meaningful instead of noise from other tenants.

### Reading sizes without being misled

Three traps, all of which cost time during the 2026-09-23 cleanup:

1. **Statistics are cached for 24 hours.** After an `OPTIMIZE` the reported
   size was completely unchanged. Run
   `SET SESSION information_schema_stats_expiry = 0;` before any size query.
2. **`DROP TABLE` appears to update instantly** - but only because the row
   leaves the result set entirely, so no cached statistic is involved.
   Shrinking an existing table is exactly the case the cache hides.
3. **`table_rows` is a sampled estimate, and often badly wrong.** It reported
   24,616 against a true 42,127, then 14,146 against a true 28,292 - errors of
   40%+ in both directions. Use `SELECT COUNT(*)`. The `data_length`,
   `index_length` and `data_free` columns are trustworthy.

`run_checks.py` issues `SET SESSION information_schema_stats_expiry = 0` once
per run, before any check, so `db_size_mb` always measures the live figure
rather than a cached one. Without it that alert could fire a day late, or stay
quiet a day too long.

### Maintenance patterns, best first

| Pattern | Reclaims space | Use when |
|---|---|---|
| `DROP TABLE` | Instantly, in full | The whole table is dead |
| Copy-and-swap: `CREATE TABLE LIKE` + `INSERT SELECT` + `RENAME` + `DROP` | Instantly, on the final `DROP` | Removing a large share of rows. Keeps a rollback point and writes only the rows kept |
| `DELETE` then `OPTIMIZE TABLE` | After the rebuild | Removing a small share |
| `DELETE` alone | Nothing at all | Never, if space is the goal |

`DELETE` only marks pages reusable inside the `.ibd`. `OPTIMIZE` is what hands
the space back, and it does so here only because `innodb_file_per_table = 1`.

`OPTIMIZE TABLE` on InnoDB prints **"Table does not support optimize, doing
recreate + analyze instead"**. That is success, not an instruction - *doing*
means it has already substituted a table rebuild. The `status OK` row beneath
it is the outcome. The manual equivalent is `ALTER TABLE ... FORCE`.

**Measure in bytes, not rows.** `SUM(LENGTH(col))` grouped by month tells you
what a purge will really recover; row counts do not, wherever row size varies.
On `antiwordle_revamp_clicks` rows ranged from ~1.6 KB to ~9.4 KB, so a
365-day purge that removed 33% of the rows recovered 8% of the space - while a
180-day purge recovered 121 MB. Never judge one retention window by a longer
one's result.

### MySQL Workbench

Safe update mode (`Error 1175`) refuses a `DELETE` whose `WHERE` does not use
a key column. Either add a `LIMIT`, which satisfies the check and usefully
chunks the transaction, or `SET SQL_SAFE_UPDATES = 0;` for the session. The
error text tells you to change Preferences and reconnect; the session variable
works immediately and needs neither.

This is a client-side guard only. mysql-connector does not enable safe update
mode, so it never applies to anything the app itself runs - only to statements
you type into Workbench.

## Future: richer page-visit logging (not implemented)

Parked deliberately - the first deploy already carries enough change. This is
the research, so it does not have to be redone.

### The three columns worth adding

`app_visits` is `id, submit_time, page_name, referrer, user_agent`, all three
strings `varchar(255)`.

| Column | Type | Why |
|---|---|---|
| `status_code` | `SMALLINT NULL` | The status is currently smuggled into `page_name` as `error.html (404: ...)`, which is why the views match on `LIKE '%error%'`. A real column turns `errors_500_today` into `WHERE status_code = 500` and gives per-page 4xx/5xx rates. |
| `is_bot` | `TINYINT(1) NULL` | Stops crawler traffic holding `smush_idle` quiet. See below - the reason is narrower than it first appears. |
| `country` | `CHAR(2) NULL` | Cloudflare already sets `CF-IPCountry` on every proxied request and the app throws it away. No logic needed, no PII. An empty value is informative too: the request bypassed Cloudflare. |

### What the bot rate actually justifies

Measured 2026-09-25 against the 238k rows of `user_agent` already collected:
**12.4% bots.**

That number alone is a weak argument. Inflating an hourly baseline by 12%
barely changes when a check fires. The real risk is the **recency** half of
the idle checks: they alert on minutes-since-last-hit, so one polite crawler
on a 15-minute cycle holds the check quiet through a total collapse in human
traffic. What matters is bot *regularity*, not bot *share*.

And it applies to only one check:

- **`blossom_idle` needs nothing.** It reads `blossom_solver_clicks` - solver
  POSTs from someone typing into the form. Crawlers do not generate those.
- **`smush_idle` is exposed.** It reads `app_visits` page GETs, which is
  exactly what crawlers produce.

So the filter belongs in `smush_idle`'s SQL only, as
`AND COALESCE(is_bot, 0) = 0`. The `COALESCE` is load-bearing: rows written
before the change are NULL, and a bare `= 0` would discard all of them and
leave the check inert for its whole 28-day baseline window.

Re-measure before committing to a token list:

```sql
SELECT LEFT(user_agent, 90) AS agent, COUNT(*) AS hits
FROM app_visits GROUP BY LEFT(user_agent, 90) ORDER BY hits DESC LIMIT 30;

SELECT CASE WHEN LOWER(user_agent) REGEXP
         'bot|crawl|spider|slurp|facebookexternalhit|bingpreview|python-requests|curl|wget|headless|scrapy|semrush|ahrefs|petal|bytespider'
       THEN 'bot' ELSE 'human?' END AS guess,
       COUNT(*) AS hits,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM app_visits GROUP BY guess;
```

Detection is a substring list, not a library - Werkzeug 3 dropped its UA
parser and nothing else is installed. It catches crawlers that announce
themselves and misses scrapers posing as Chrome, which are the ones you would
most want. Treat `is_bot` as "excludes the obvious".

### Order of operations

**ALTER first, always.** Deploying first breaks logging: the new INSERT names
columns that do not exist, every batch fails with error 1054, and the drain
thread fires `log_drain_failed` until you catch up. The other order is
invisible, because the existing INSERT names its columns explicitly and
unlisted columns simply take their default.

| Step | What | Gap allowed |
|---|---|---|
| 1 | `ALTER TABLE` | - |
| 2 | Confirm old code still writing | minutes to weeks |
| 3 | Deploy code that populates the columns | - |
| 4 | Update views and `smush_idle` | whenever |

```sql
ALTER TABLE app_visits
    ADD COLUMN status_code SMALLINT   NULL,
    ADD COLUMN is_bot      TINYINT(1) NULL,
    ADD COLUMN country     CHAR(2)    NULL,
    ALGORITHM=INSTANT;

-- rollback, also instant on 8.4
ALTER TABLE app_visits
    DROP COLUMN status_code, DROP COLUMN is_bot, DROP COLUMN country,
    ALGORITHM=INSTANT;
```

Three details that make it safe:

- **`ALGORITHM=INSTANT`** is metadata-only, milliseconds on 238k rows. Stating
  it explicitly makes MySQL *error* rather than silently falling back to a
  full rebuild. Adding at the end of the table is what qualifies; positioning
  with `AFTER` would force the rebuild.
- **`NULL`, not `DEFAULT 0`.** NULL honestly means "not recorded yet";
  `is_bot = 0` would claim every historical row was verified human.
- **Nothing reads `app_visits` with `SELECT *`** - all four views name their
  columns - and a view's column list is frozen at `CREATE` time anyway, so no
  view changes shape.

Prod and staging share one JawsDB, so the ALTER hits both. That is fine
precisely because it is additive: old and new code both work against the
altered table, in either direction.

### Known limitation of status_code

`log_page_visit` runs mid-route, before the response exists, so it cannot
observe the real status - it has to be told:
`log_page_visit(page_name, status_code=200)`, with `app.py`'s handlers passing
404 and 500. The 200 is an assumption, correct in practice but wrong for a
page that fails *after* logging. Capturing the true status means moving
logging into `after_request`, which would also change how `page_name` is
derived and therefore touch all four views. Bigger job, deliberately separate.

### Considered and rejected

- **IP address.** Needs `CF-Connecting-IP`, since `ProxyFix(x_proto=1,
  x_host=1)` has no `x_for` and `request.remote_addr` is the Cloudflare edge.
  PII with retention consequences; `country` gives most of the value at none
  of the cost.
- **`duration_ms`.** Real value - there is no latency visibility today - but
  uncapturable where `log_page_visit` is called. Needs the `after_request`
  move above.
- **Session ID.** Would turn page views into sessions, but carries privacy
  complexity and answers no question currently being asked.

### A better fix for smush than is_bot

`blossom_idle` is the stronger check because it watches a *user action*
rather than a page load. Logging the POST branch of `/smush` under its own
`page_name` and keying `smush_idle` on that needs no bot filtering at all -
crawlers do not submit boards - and would catch soft failures too. A GET-based
idle check cannot see `/smush` rendering fine while the solver returns
garbage.

Worth doing if `smush_idle` proves noisy or too quiet in practice.

### View updates that would come with it

Once enough rows carry `status_code`, the error views can move from the
fragile `page_name LIKE '%error%'` matching to `status_code >= 400`. The other
view work is not future - it applies to the current deploy, and lives in
[7. Dashboard views](#7-dashboard-views).

## What this does not catch

Anything wrong that produces neither a log line nor a measurable database
symptom: wrong-but-plausible data (YouTube returning 50 rows of stale
videos), visual or CSS breakage, a solver returning wrong answers, SEO
decline. Those still need occasional eyes.

`mtg_prices_bsky.py` already handles its own staleness and failure alerting
and was left alone.
