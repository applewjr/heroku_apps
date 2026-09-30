# Monitoring

Everything needed to run this app unattended: what changed, what to set up,
and how to undo it.

**Stop checking, start being told.** Alerts are log lines, not emails.
Anything wrong prints one token line to stdout, Heroku's drain carries it to
Papertrail, and Papertrail decides who gets notified. The notification channel
stays a UI setting rather than a deploy, and the alerting path has no SMTP
failure mode of its own.

```
JJ_ALERT check=youtube_trending_today sev=crit actual=0 compare=">=" threshold=45 note="Daily youtube_trending load, scheduler 12am PST"
JJ_PULSE source=checks ok=11 alert=0 skip=0 hour_pst=14 ms=940
```

`JJ_PULSE` feeds Papertrail's *inactivity* alert, which fires when logs
**stop**. It is the only check that survives the thing being monitored dying;
everything else can only report problems it is alive to see.

---

## Do this, in this order

Deploying alone changes nothing visible: the app starts emitting alert lines at
step 3, nothing reaches your inbox until step 5, and no checks run until
step 6.

| # | Step | Where | Detail |
|---|---|---|---|
| 1 | Review and merge the branch | git | - |
| 2 | Deploy to **staging**, run the smoke test | Actions -> Deploy -> `staging` | [Deploy](#1-deploy) |
| 3 | Deploy to **prod**, run the same smoke test | Actions -> Deploy -> `prod` | [Deploy](#1-deploy) |
| 4 | Scale staging back to 0 | Actions -> Stop staging | [Deploy](#1-deploy) |
| 5 | Create the **`JJ_ALERT`** alert | Papertrail | [Papertrail](#4-papertrail---two-alerts) |
| 6 | Add **`run_checks`** hourly | Heroku Scheduler | [Health check](#3-heroku-scheduler---add-the-health-check) |
| 7 | Confirm `JJ_PULSE` lines are arriving | Papertrail | [Health check](#3-heroku-scheduler---add-the-health-check) |
| 8 | Create the **`JJ_PULSE` inactivity** alert | Papertrail | [Papertrail](#4-papertrail---two-alerts) |
| 9 | Re-point the existing jobs through `run_job` | Heroku Scheduler | [Wrap the jobs](#2-heroku-scheduler---wrap-the-existing-jobs) |
| 10 | Update the dashboard views, **including the two probe-traffic exclusions** | Workbench | [Dashboard views](#7-dashboard-views) |
| 11 | Test the dead-man's switch | Heroku Scheduler | [Dead-man's switch](#8-test-the-dead-mans-switch) |
| 12 | Add the **Heroku error code** alert | Papertrail | [Papertrail](#4-papertrail---two-alerts) |
| 13 | Add **`daily_digest`** at 14:00 UTC | Heroku Scheduler | [The digest](#3b-the-daily-digest) |

### Why that order

**Steps 5 to 8 are the one sequence that matters.** Create the `JJ_ALERT`
alert early: from step 3 the app is already emitting `http_500` and
`feedback_new` alerts, so something should be listening. Create the
**`JJ_PULSE` inactivity alert last**, and only after step 7 confirms pulses are
flowing. It fires when no pulse has been seen for 90 minutes, so setting it up
before anything emits one means it fires immediately, on a healthy system.

**Step 9 can trail.** Wrapping the scheduled jobs is independent of everything
else, and the jobs keep working unwrapped - just silently, as they do today.
Wrap one, watch it overnight, then do the rest; the table in
[2. Wrap the jobs](#2-heroku-scheduler---wrap-the-existing-jobs) is a menu, not
a single action.

**Staging shares the JawsDB with prod** (step 2), so a smoke test writes real
rows to `app_visits` and `blossom_solver_clicks` - harmless log rows, but real
ones. The deploy workflow also scales the staging dyno up automatically, which
is what step 4 undoes.

**Take staging down at step 4, not at the end.** Its only job is the step-2
smoke test, and step 9 explicitly invites spreading the remaining work over
days. Left running, a second app holds a second connection pool against the
15-connection JawsDB cap, burns dyno hours, and - if staging drains to the same
Papertrail - mixes its alerts into yours during the exact window you are
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
best-effort analytics during an outage is the intended trade, but a quiet
`log_queue_full` is not evidence that nothing was lost.

`log_page_visit` had exactly one call site left (`routes/blossom.py`), so
`vw_prod_errors` and `vw_prod_blossom_errors` matched zero rows: the "Blossom
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
  serving, including pages that need no database.
- **`extensions.py`** builds the connection pool lazily.
  `MySQLConnectionPool`'s constructor eagerly opens connections, so building
  it at import meant a JawsDB outage during a dyno boot took the entire app
  down, and `restart-dyno.yml` restarts twice daily. A failed build is now
  remembered for `_POOL_RETRY_SECONDS` (20). Without that, every request
  during an outage retries the whole build, and because those retries
  serialise on one lock the eighth waiting thread sits eight
  `connection_timeout`s deep, past Heroku's 30s router limit. With it, a
  database page fails in about five seconds instead of queueing, and a blip
  still self-heals on the next window.

**`extensions.py` also moves visit and click logging off the request path.**
It used to be an INSERT, an explicit COMMIT, and a session reset - roughly
three round trips to JawsDB in front of the response, and on `/blossom`, which
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
  `GROUP BY`. This was **not** broken: `sql_mode` is `NO_ENGINE_SUBSTITUTION`
  only, with no `ONLY_FULL_GROUP_BY`, so the view works today. The change is
  correctness and future-proofing. Revert it freely.

---

## Files

| File | What it does |
|---|---|
| `alerts.py` | `alert()`, `pulse()`, `alert_throttled()`. Scrubbing and rate limiting. No import side effects. |
| `run_job.py` | Runs an existing scheduled script under `runpy`, adding a failure alert. No edits to the script. |
| `run_checks.py` | Hourly checks against JawsDB, Redis and the live site. Refuses any check SQL that is not a SELECT. |
| `daily_digest.py` | Daily email: /etl_dash, every check value, latency, storage, feedback. A second notification channel that does not depend on Papertrail. |
| `../datasets/health_checks.yaml` | The SQL checks: SQL, threshold, severity, active window. |
| `../datasets/health_web.yaml` | The web probes: path, latency ceiling, expected content. |

---

## Setup, in order

Nothing below happens automatically.

### 1. Deploy

Normal deploy. No new dependencies, no schema changes, no config vars
required - every new setting has a default. After deploying, the app is
already safer (the hardening above) and already emitting `http_500` and
`feedback_new` alerts, but nothing is listening yet.

#### The smoke test - run it on staging, then again on prod

The test suite mocks the database, so **no code in this branch has ever
written a row to real MySQL.** The smoke test is what proves the write-behind
path works, and it cannot be checked any earlier. Staging shares prod's
JawsDB, so the staging run is a genuine test and its rows are real rows.

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
2. **Timestamps are current PST wall-clock, not UTC.** Logging moved from
   `CONVERT_TZ(NOW(), ...)` to Python-side `pst_now_str()`; an
   off-by-seven-hours here silently corrupts
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

Then confirm the site itself is healthy: `/`, `/blossom`, `/wordle_revamp`
and `/etl_dash` all render. A database-backed page failing while `/` still
serves is the signature of a pool problem rather than a deploy problem.

### 2. Heroku Scheduler - wrap the existing jobs

The four YouTube scripts and `espresso_data_import.py` send their success
email as the last unguarded statement in the file, so any exception before it
kills the job with **no email at all** - a signal whose absence you have to
notice.

Two are already better and are wrapped for consistency rather than rescue:
`mtg_prices_bsky.py` sends its own `FAILED:`/`WARNING:` emails and re-raises,
and `redis_wordle.py` sends two emails with `try`/`except` between them, so a
failure in its second half still produces the first.

Re-point each command through the wrapper; the scripts themselves are
untouched. Every entry already carries a `timeout 600` prefix: keep it, insert
`-s INT -k 60` after `timeout`, and `-m monitoring.run_job` after `python`.

Commands are entered unquoted in the Scheduler UI - it takes the whole field
as the command, so there is no flag-parsing ambiguity to protect against. (The
`heroku run` CLI is different; quote the command there.)

| Job | Command |
|---|---|
| youtube_trending_v2 | `timeout -s INT -k 60 600 python -m monitoring.run_job scheduled_tasks_youtube/youtube_trending_v2.py` |
| youtube_trending_revamp_v3 | `timeout -s INT -k 60 600 python -m monitoring.run_job scheduled_tasks_youtube/youtube_trending_revamp_v3.py` |
| youtube_backup_v2 | `timeout -s INT -k 60 600 python -m monitoring.run_job scheduled_tasks_youtube/youtube_backup_v2.py` |
| youtube_backup_revamp_v3 | `timeout -s INT -k 60 600 python -m monitoring.run_job scheduled_tasks_youtube/youtube_backup_revamp_v3.py` |
| redis_wordle | `timeout -s INT -k 60 600 python -m monitoring.run_job scheduled_tasks_redis/redis_wordle.py` |
| espresso_data_import | `timeout -s INT -k 60 600 python -m monitoring.run_job scheduled_tasks_espresso/espresso_data_import.py` |
| mtg_prices_bsky | `timeout -s INT -k 60 600 python -m monitoring.run_job scheduled_tasks_mtg/mtg_prices_bsky.py` |

`mtg_prices.py` is deliberately absent - it is the Twitter-era predecessor of
`mtg_prices_bsky.py`. Wrap it too if it is still scheduled.

#### Why `-s INT -k 60`

Plain `timeout` sends SIGTERM, which Python does not raise as an exception, so
`run_job` never reaches its handler and a hung job dies silently - the failure
mode the wrapper exists to eliminate. `-s INT` sends SIGINT, which arrives as
`KeyboardInterrupt`; `run_job` catches `BaseException`, so a hang produces a
`sev=crit` alert naming the job, a traceback, and exit 1.

`-k 60` follows up with an unblockable SIGKILL sixty seconds later, because
SIGINT is not guaranteed to land: `youtube_trending_v2.py` and
`youtube_trending_revamp_v3.py` each contain **seven bare `except:`
clauses**, and a bare `except:` catches `BaseException`, `KeyboardInterrupt`
included. A SIGINT arriving inside one of those blocks is swallowed and the
script carries on, past the timeout, holding its database connection. Order:
SIGINT first so `run_job` can report, SIGKILL as the guarantee the dyno ends.

#### Why the timeout matters

Every one of these scripts opens a JawsDB connection, and a process killed
mid-query never runs its `finally`. The plan caps you at **15 concurrent
connections**, shared with the web dynos' pool of 5, so enough stacked hangs
and the *website* stops being able to reach the database. Bounding the job
bounds the blast radius.

600s is comfortable for these: they are daily, so even a full-length timeout
cannot overlap the next run.

### 3. Heroku Scheduler - add the health check and the digest

| Command | Frequency |
|---|---|
| `timeout -s INT -k 60 600 python -m monitoring.run_checks` | Hourly |
| `timeout -s INT -k 60 600 python -m monitoring.daily_digest` | Daily, 14:00 UTC |

14:00 UTC is 6am PST in winter and 7am PDT in summer. Heroku Scheduler only
speaks UTC, so the digest drifts an hour with daylight saving. 13:00 UTC is the
other reasonable choice if 6am year-round matters more than never arriving at
5am. Cost measured 2026-09-28: **6.2 s of work, 24.5 KB of email.**

The cost of `run_checks` grew with the web probes - five HTTP fetches rather
than one - from 157 ms to roughly 3.6 s. Still trivial against the 1.4 s of
dyno lifecycle it already paid, and still nowhere near the 600 s bound.

Same signal flags as the wrapped jobs, for slightly different reasons.
**Detection is identical either way.** There is no `run_job` here and none is
needed: `main()` wraps `run_sql_checks` in `except Exception`, which does not
catch `KeyboardInterrupt`, so an interrupt propagates straight out, the
closing `alerts.pulse()` is never reached, and the 90-minute inactivity alert
picks it up.

What `-s INT` buys on top of that is diagnosis and cleanup:

* **A traceback instead of silence.** SIGTERM prints nothing; a
  `KeyboardInterrupt` traceback lands in the log drain and names *which* check
  was hung.
* **`finally` blocks run.** `run_sql_checks` closes its cursor and connection
  in a `finally`; under SIGTERM that never executes and the JawsDB connection
  is left for the server to reap. Connection pressure is the reason for
  bounding these jobs at all.

`-k 60` is cheaper insurance here than it is for the YouTube scripts: nothing
in `run_checks` uses a bare `except:`, every handler is `except Exception`,
and `KeyboardInterrupt` is not an `Exception` subclass, so SIGINT always
lands. It guards only against a third-party library (mysql-connector, redis,
requests) swallowing the interrupt internally, and it keeps one convention
across all eight Scheduler entries for 40 seconds of worst-case runtime.

Cost, measured on 2026-09-26: **157 ms of work, 1.4 s of dyno lifecycle**
start to finish (an earlier ~20 s-per-run estimate here was about 15x
conservative). At 720 runs a month this is well under an hour of dyno time.

### 3b. The daily digest

`monitoring/daily_digest.py` emails the whole of `/etl_dash` once a day, plus
every health check value, current page latency, storage against the plan, and
the full text of any feedback from the last 24 hours. The summary is in the
subject line - `JJ daily 2026-09-28 - 16 ok, 0 alerts, 330 MB` - because on
most days the phone notification is the entire report.

**Its second job is the one that matters.** Every alert in this system ends the
same way: a log line, a Heroku drain, Papertrail, an email. That is a single
path, and it fails *silently* - a Papertrail outage, or simply exhausting the
free plan's log quota, takes the whole alerting system down without alerting
anyone, because the thing that would report it is the thing that is down.

The digest re-runs every check itself and sends over SMTP, touching nothing
Papertrail owns. So:

* the digest arriving proves the database, the scheduler and mail all work;
* the digest not arriving is itself a signal, and it is the one signal the
  alerting path cannot suppress;
* a `JJ_ALERT check=digest_failed` says the digest broke while Papertrail is
  fine.

Neither channel can fail quietly on its own. That is the whole design.

It deliberately does **not** alert on a breached check. `run_checks` already
owns that decision hourly; a second process emitting the same `JJ_ALERT` lines
would double every email.

Two details worth knowing:

* **A panel that fails is reported, not dropped.** `routes/dashboards.py`
  swallows a broken panel with a `print` and renders the page without it, so a
  broken panel looks exactly like a panel that was never there. The digest
  prints `panel failed: <error>` instead, which surfaces a class of failure
  that is currently invisible on the dashboard itself.
* **Rows per panel are capped** at `MAX_ROWS_PER_PANEL` (40) and the digest
  says when it truncated. Gmail clips a message past ~102 KB behind a "view
  entire message" link, which would otherwise quietly hide the bottom of the
  email as a panel grew.

Preview it without sending, or send one on demand:

```bash
python -c "import monitoring.daily_digest as d, datetime, pytz; \
  h,s = d.build(datetime.datetime.now(pytz.timezone('America/Los_Angeles'))); \
  open('digest.html','w',encoding='utf-8').write(h); print(s)"

heroku run "python -m monitoring.daily_digest" -a apple-apps
```

### Query timeouts

Both `run_checks` and `db_cursor` set `max_execution_time` on their session, so
a query that hangs cannot take anything else with it. This was unbounded until
2026-09-28.

| Where | Value | What it prevents |
|---|---|---|
| `run_checks.QUERY_TIMEOUT_MS` | 30 s | A hung check blocking the loop. Every later check silently never runs and the closing pulse never prints, so the only surviving signal is the inactivity alert - "nothing is reporting", which names nothing. With the bound, MySQL raises, the per-check `except` catches it, and the check names itself while the rest run. |
| `extensions.QUERY_TIMEOUT_MS` | 10 s | A runaway query on a page taking the site down. With `--workers 1 --threads 8`, one query that never returns parks a thread until gunicorn's `--timeout 60` kills the *worker*, which takes every in-flight request with it and returns a run of H12s. |

Verified 2026-09-28 against the live database: a genuinely expensive `SELECT`
raises `DatabaseError` errno **3024** - *"Query execution was interrupted,
maximum statement execution time exceeded"* - at the bound, rather than
returning a partial result. That distinction is load-bearing: a check that
silently got a wrong-but-plausible number would *pass* when it should alert.

`SELECT SLEEP(n)` is a documented exception - MySQL interrupts it and `SLEEP`
returns 1 rather than erroring - so do not use it to prove the mechanism works.

Both are set **per checkout**, not once on the pool, because
`pool_reset_session` is `True` and a session variable is cleared when the
connection goes back. Cost is one extra round-trip on the minority of requests
that touch MySQL at all; the solver pages do not on GET. MySQL applies
`max_execution_time` to read-only `SELECT`s only, so the write-behind drain's
`INSERT`s are unaffected.

#### Do not wait an hour to confirm it works

Heroku Scheduler picks its own offset within the hour, so a new entry can sit
for up to 60 minutes with no pulse and no way to tell "not yet" from "broken."
Force one instead:

```bash
heroku run "timeout -s INT -k 60 600 python -m monitoring.run_checks" -a apple-apps
```

Quote the command here. Unlike the Scheduler UI, the CLI has to decide for
itself whether `-m` belongs to it or to the command, and `-a` is its own
flag.

That prints the same `ok`/`skip` lines and closing `JJ_PULSE` a scheduled run
would, through the same log drain, so it satisfies step 7 in seconds. It is
read-only - the runner refuses any check SQL that is not a `SELECT`, and
touches Redis only through `ping()` and `xlen()` - so it is safe to run
against prod as often as you like.

Expect `16 ok, 0 alerts, 1 skip` on a healthy system. The skip is `db_size_mb`
outside its 09:00-10:00 PST window.

Every `ok` line now carries what was measured, not just the fact that the check
ran:

```
ok    site_idle               actual=1 compare=<= threshold=180 ms=93
ok    web:blossom             actual=234 compare=<= threshold=3000
skip  db_size_mb              (outside PST window [9, 10])
```

That is deliberate. A bare `ok` proves a check ran and nothing else, so nothing
could be seen drifting until it breached. Searching Papertrail for
`ok db_size_mb` now gives the storage trend, and `ok web:blossom` gives the
latency trend. These lines carry no `JJ_` token on purpose - they are state,
not alerts, and must not match the search that sends email. `alerts.scrub()`
guarantees it: a measured value containing `JJ_` is rewritten before printing.

A green run still does not prove `blossom_errors_today` works - it passes
trivially until error rows accumulate.

#### What gets checked

Nine checks come from `datasets/health_checks.yaml`: `youtube_trending_today`,
`youtube_grouped_today`, `blossom_idle`, `site_idle`, `blossom_errors_today`,
`db_size_mb`, `db_connections`, `wordle_drain_fresh`, `antiwordle_drain_fresh`.

Five are web probes from `datasets/health_web.yaml`, reported as `web:<name>`:
`home`, `blossom`, `smush`, `wordle`, `youtube_trending`. Each asserts three
things - 200, the body contains an expected substring, and the response
arrived inside `max_ms`:

| Failure | What it means |
|---|---|
| unreachable or non-200 | The dyno is down or erroring. |
| 200 but content missing | It served a page that did not render. A solver whose template loads while its engine returns nothing answers 200 all day. |
| 200, correct, but slow | The only performance signal in the system. Nothing else measures how long anything takes, so a page that gets ten times slower is otherwise indistinguishable from a healthy one. |

A latency breach is measured twice before it alerts. `restart-dyno.yml`
restarts the dyno at 3am and 3pm PST and the first request after a boot pays
for `data.py` loading its CSVs, so one slow sample is a cold start rather than
evidence.

Three more are Python rather than SQL, so they live in `run_checks.py` and are
easy to miss when reading the YAML:

| Check | What it proves |
|---|---|
| `redis_up` | Redis answers `ping()`. Wordle and antiwordle logging goes through it, so silence there is silent data loss. |
| `redis_backlog` | `XLEN` on `wordle_logging` and `antiwordle_logging` is under `REDIS_BACKLOG_MAX` (20000). A growing backlog means `redis_wordle.py` stopped reconciling and is no longer draining. |
| `checks_slow` | The whole run exceeded `RUN_BUDGET_MS` (30s) **while every other check passed**. Silent when anything else failed, since a probe timing out costs 15 seconds on its own and already explains the time. |

The SELECT-only guard on YAML checks is a literal prefix test, not a parser: a
check written to start with a comment or a `WITH` CTE would be rejected as
non-SELECT.

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

- Fold the platform-error alert into the first search:
  `"JJ_ALERT" OR "error code=H" OR "Error R"`, threshold 1 over 10 minutes.
- If still capped, retire the "5 x 500 in 10 minutes" rule. The new
  `check=http_500` emitter supersedes it and reports the *first* 500.

Free-tier limits: search retention is 2 days (archives keep 7), and the volume
cap is 10 MB/day. Exceeding the cap stops ingestion, which trips the
inactivity alert - loud, but recognise it for what it is.

The `ok` lines added on 2026-09-28 cost about **29 KB a day** (17 checks x 24
runs at ~70 bytes), or 0.3% of the daily cap. Not a concern.

#### Heroku's own error codes - nothing watches these

Heroku emits these into the drain already, and no alert matches them. This is
the single largest gap left, and it needs no deploy:

| Code | Means | Why you care |
|---|---|---|
| `H12` | Request timed out at the router's 30s limit | The exact failure `extensions.QUERY_TIMEOUT_MS` now bounds. A run of these means one slow path is taking the single worker down. |
| `H13` | Connection closed without a response | The worker died mid-request. |
| `H10` | App crashed | Boot failure. The site is down and no check inside the app can say so. |
| `R14` / `R15` | Memory quota exceeded / hard limit | The one that catches a genuine leak rather than a worker-count mistake. Worth watching given the `--workers 1` pin in the `Procfile`. |

One search covers all of them, because Heroku formats them consistently:

```
"error code=H" OR "Error R"
```

`"Error R"` rather than naming R14 and R15: it also catches **R10** (boot
timeout), **R12** (exit timeout), R13 and R17 at no extra cost. Every R-code is
formatted `Error R<n> (description)`, so the prefix is the whole family.

If the saved-search cap bites, fold it into the `JJ_ALERT` search rather than
dropping it - a site returning H12s is worse than any check in
`health_checks.yaml` firing.

**Confirm the search actually matches before trusting it.** Papertrail indexes
on token boundaries and treats `=` as a delimiter, so it is not obvious that
`code=H` matches `code=H12` - the indexed token may be `H12`. A saved search
that silently matches nothing is worse than no saved search, because it reads
as coverage.

Real platform errors are too rare to test against - a 1500-line pull on
2026-09-29 covering two and a half hours contained **zero** `at=error` lines.
So test the tokenizer with data that is already there instead. Every router
line carries `status=200`:

| Search | Expected |
|---|---|
| `"status=200"` | thousands of hits |
| `"status=2"` | if this also hits, prefix matching works and `"error code=H"` is fine |
| neither | use a wildcard: `code=H*` |

#### Threshold: 1 in 10 minutes is right for H, wrong for R14

The H-codes are **incident-shaped**: they fire, you fix the cause, they stop.
One event in ten minutes is the correct trigger.

`R14` is **condition-shaped**, like `db_size_mb`. Memory over quota emits R14
continuously rather than once, so a 1-per-10-minutes rule sends 144 emails a
day until it is fixed - the same alert-fatigue failure that `only_between_pst`
exists to prevent. If R14 ever fires for real, throttle that alert rather than
reading past it.

**Watch for R12 from your own restarts.** `.github/workflows/restart-dyno.yml`
cycles the web dyno twice a day. If gunicorn does not exit within 30s of
SIGTERM, Heroku emits `Error R12 (Exit timeout)`, which `"Error R"` catches -
twice a day, looking like an incident. Unverified either way: no web dyno
restart appeared in the window pulled on 2026-09-29, which covered 22:00 UTC
when the cron is set to fire. If R12 shows up at 3am and 3pm PST, that is the
restart, not a fault.

#### Three channels, and what each one survives

Worth being explicit about, because each covers the others' blind spot:

| Channel | Survives | Blind to |
|---|---|---|
| `JJ_ALERT` -> Papertrail | Anything the app is alive to see | The app being dead; Papertrail being dead or over quota |
| `JJ_PULSE` inactivity | The app, the dyno or the scheduler dying | Papertrail being dead or over quota |
| Daily digest -> SMTP | Papertrail being dead or over quota | Anything in the 24h between digests |

The first two share a single point of failure - Papertrail - which is exactly
what the digest was added to cover.

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
2026-09-24 from how far back each view actually reads:

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

`information_schema` caches table statistics for 24 hours on MySQL 8, so a
size query run after an `OPTIMIZE` reads the stale value unless the expiry is
reset first. `table_rows` is a sampled estimate and not usable here; use
`SELECT COUNT(*)`. Measured error rates and the third trap:
[Reading sizes without being misled](#reading-sizes-without-being-misled).

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
longer dominant. Purging it in two passes mapped where the weight sits:

| Age band | Size |
|---|---|
| Older than 365 days | 21.1 MB |
| 180 to 365 days | 121.1 MB |
| Under 180 days (kept) | ~132.6 MB |

The payload grew sharply somewhere around late 2025: rows older than a year
average ~1.6 KB, recent ones ~9.4 KB. Two years of history cost 21 MB; the six
months before the cutoff cost 121 MB. Age-based retention works on this table
as long as the window reaches into the period where the rows are large - do not
judge a window by a longer one's result.

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
mysqldump -h HOST -u USER -p DB \
  spotify_tracks spotify_artists \
  lol_summoner lol_champion lol_match \
  lol_participants_info lol_participants_challenges \
  > archived_tables.sql
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
the last commented line, `... AS quordle_mobile`, has none either, so enabling
a middle subset left both a missing comma and a trailing one. The file now
holds finished text rather than a menu.

Each page needs **two** entries: a `SUM(CASE WHEN ...)` column *and* a name in
the `WHERE page_name IN (...)` list. A column without the `WHERE` entry reads
zero forever.

Dropped on purpose: `blossom_bee.html`, `wordle.html`, `wordle_example.html`,
`antiwordle.html` and `quordle_mobile.html`. Nothing logs them, so they would
be five columns of zeroes. Their historical rows stay in `app_visits` either
way - this only changes what the view surfaces.

Rows only start arriving after the prod deploy, so there is no hurry.
`CREATE OR REPLACE VIEW` is instant and independent of everything else.

#### Two views must exclude probe traffic - 2026-09-28

**Apply these with the deploy that adds the web probes, not after.**
`run_checks` now fetches `/blossom`, `/smush` and `/wordle` every hour, and
those GETs call `log_page_visit` like any other request. Two views count them
and would silently overstate real usage from the first hour:

| View | Effect if not updated |
|---|---|
| `vw_prod_word_solver_page_visits` | +24 a day on the blossom, smush and wordle columns - roughly a 29% overstatement against ~250 real visits a day |
| `vw_prod_blossom_search_source` | The probe sends no `Referer`, so it appears as a `No referrer` traffic source worth 24 visits a day |

Both repo copies already carry `AND user_agent <> 'jj-healthcheck'`. Paste them
into Workbench as-is; `CREATE OR REPLACE VIEW` is instant.

`vw_prod_errors` and `vw_prod_blossom_errors` need nothing - they filter on
`page_name LIKE '%error%'` and the probe never 404s or 500s. `vw_prod_blossom`
reads `blossom_solver_clicks`, which the probe does not write to, since it only
GETs the page and never submits the solver.

This is the same class of problem as the `must_have='a'` blossom probe
exclusion and as `site_idle`'s: **anything that reasons about real usage has to
exclude the thing that generates fake usage on a timer.** A new view reading
`app_visits` should assume it needs the filter.

### 8. Test the dead-man's switch

Disable the `run_checks` Scheduler entry, wait for the email, then **re-enable
it.** Budget about two hours: the alert fires at 90 minutes of silence.
`run_checks` prints `JJ_PULSE source=checks` at the end of every hourly run and
Papertrail evaluates the no-new-events search on its own servers, so stopping
the job is what produces the email.

Re-enabling is the step to not forget: leaving it off leaves you with no
monitoring at all. It is self-correcting only in that the inactivity alert
keeps firing until pulses resume.

This is the one test to run deliberately, because it is the only alert that
proves an absence. Every other alert needs the app alive enough to report its
own problem: a dead dyno, an unreachable database, a failed boot or a broken
log drain cannot emit a `JJ_ALERT`, and only something outside Heroku notices
the quiet. It is also the alert most likely to be silently misconfigured - a
bare `JJ_PULSE` search instead of the quoted phrase, "at least 1 event"
instead of "no new events match", or a notification never attached. Every one
of those looks fine in the UI and fails only when you need it.

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

**Every threshold lives in a YAML file, so changing one is a deploy.** SQL
check thresholds are in `datasets/health_checks.yaml`; web probe paths,
latency ceilings and expected content are in `datasets/health_web.yaml`. Only
two settings are `heroku config:set`-tunable: `HEALTH_WEB_URL` and
`REDIS_BACKLOG_MAX` (default 20000). Nothing else in `config.py` feeds a
threshold.

**The latency ceilings in `health_web.yaml` are not baselined.** There was no
latency measurement anywhere before 2026-09-28, so they were set generously to
avoid crying wolf. First real samples, taken the day they shipped:

| Probe | Observed | Ceiling |
|---|---|---|
| `/` | 390-420 ms | 2500 |
| `/blossom` | 234-405 ms | 3000 |
| `/smush` | 156-485 ms | 3000 |
| `/wordle` | 280-436 ms | 3000 |
| `/youtube_trending` | 344-358 ms | 6000 |

That is 6-15x headroom, which is too loose to catch a page that merely doubled.
Every run now prints `ms=` per probe, so after a couple of weeks of history in
Papertrail these should be tightened to something measured - the same way
`blossom_idle`'s 240 and `site_idle`'s 180 were set from a replay rather than a
guess. Leave room for the cold start after the twice-daily dyno restart; the
two-measurement retry absorbs it, but only if the ceiling is not absurdly
tight.

**`smush_idle` was deleted on 2026-09-28. It could never fire.** The claim that
used to sit here - that it would switch itself on once 28 days of history
existed - was wrong, and worth understanding because the mistake is easy to
repeat. Its gate divided by a hard-coded 28, so the average did not grow as the
table filled; it *converged* to the true per-hour rate and stopped there. 28
days was when the number stopped moving, not when the check turned on. It
converged below its own gate, so it returned a passing `0` forever while still
counting as an `ok` in the tally - a green light wired to nothing.

Two things replaced it, both stronger:

- **`site_idle`**, a plain "minutes since anyone reached any logged page" rule,
  measured over 90 days rather than guessed.
- **`web:smush`** in `health_web.yaml`, which fetches the page and asserts it
  rendered. A probe catches a broken smush within the hour no matter how much
  traffic smush is getting, which no threshold on one page can do. Measured the
  same day the check was deleted: smush went from 8 visits on 09-25 to 537 on
  09-27, so any per-page threshold set then would have been calibrated against
  a moving target.

**Hour-of-day awareness turned out to be unnecessary, not just broken.** The
idea was that a fixed "no usage in N hours" rule either cries wolf at 4am or
sleeps through a lunchtime outage. Measured, this site has no 4am lull - traffic
is broadly uniform, which is what an internationally visited solver looks like.
`blossom_idle` still carries its gate, but the gate never closes, and its
threshold was set from the measured gap distribution rather than from the gate,
so the check is correctly calibrated for what it actually does.

**`site_idle` is 180 minutes, measured.** Replaying it hourly over 90 days of
`app_visits`, with the hourly probe and obvious bots excluded: 27 breaches at
60 minutes, 3 at 90, and zero at 120 and above. The longest genuine quiet
stretch in 90 days was 116 minutes against a baseline of ~205 non-bot visits a
day, so 180 clears the worst real gap by half again.

**Excluding probe traffic from `site_idle` is load-bearing.** `run_checks`
fetches five pages every hour as `jj-healthcheck`, and the solver pages call
`log_page_visit` on GET, so those probes write `app_visits` rows. A check that
counted them would be held permanently quiet by its own prober. Bots are
excluded for the same reason and it is not theoretical: dropping them moved the
longest observed gap from 73 minutes to 116, so crawlers were genuinely filling
real gaps.

`db_connections` warns above 13 of the plan's 15. Prod and staging pools of 5
each plus a Workbench session reaches 12 by design, so 13 warns without firing
at the designed maximum. Observed steady state is 7.

`db_size_mb` warns above 700 of the plan's 1024. That leaves 324 MB free -
comfortably more than the largest table (~133 MB) needs for a `DELETE` +
`OPTIMIZE` rebuild, so every remediation option stays open, and at ~260 MB/year
it is about fourteen months of notice. It was 800 until 2026-09-28.

**`only_between_pst` doubles as a repeat-rate control.** The Papertrail rule
emails on any `JJ_ALERT` inside 10 minutes, so an hourly check that breaches
emails every hour until it is fixed. That is right for something you fix today
and wrong for something that takes months, so `db_size_mb` is windowed rather
than hourly. The same applies to any slow-moving check added later.

**But a one-hour window is a single point of failure.** `[9, 9]` meant the
09:00 PST run evaluated it and no other, so scheduler drift, a dyno that failed
to start, or one run that died on an earlier check silently cost the whole day
- and invisibly, because a skipped check looks exactly like a passing one in
the tally. The window is now `[9, 10]`. That costs a second email a day while a
breach is open and buys a free retry every day one is not.

## JawsDB reference

What the plan allows, how the server is configured, and which maintenance
patterns work. All verified 2026-09-23 against the live database.

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
  long time without anyone noticing; it was never a fault.
- **No `STRICT_TRANS_TABLES`**, so an over-long value is *silently truncated*
  rather than rejected. The write-behind drain's per-row retry will therefore
  rarely fire for width violations, and an over-long referrer loses its tail
  without complaint.

### Privileges

| Available | Not available |
|---|---|
| `CREATE INDEX`, `CREATE TABLE`, `RENAME TABLE`, `DROP TABLE`, `DROP VIEW` | `information_schema.innodb_tablespaces` - needs `PROCESS` |
| `OPTIMIZE TABLE`, `ANALYZE TABLE` | Other tenants' threads in `information_schema.processlist` |
| `SET SESSION information_schema_stats_expiry` | |

Not holding `PROCESS` is the reason `processlist` shows only your own
connections - which is what makes the `db_connections` check
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
recreate + analyze instead"**. That is success, not an instruction: it has
already substituted a table rebuild, and the `status OK` row beneath it is the
outcome. The manual equivalent is `ALTER TABLE ... FORCE`.

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

## Reading an http_500 from scanner junk

**A 500 caused by junk input is a validation bug, not an attack.** Worth
internalising, because the alert looks alarming and the fix is mundane.

On 2026-09-29 a scanner walked `/feedback` and `/espresso/baseline` with sqlmap's
standard payload set - `1'`, `1"`, `1)`, `98766`, `1,")).'(abcd`. It produced
eight `http_500` alerts and eight rows in `vw_prod_errors`, all pointing at
`/espresso/baseline`, all `KeyError`.

None of it was a database problem. `roast` went straight into
`espresso_points['roast_variable'][roast]` with no validation, so any
unrecognised value was a `KeyError`, and an uncaught `KeyError` is a 500. The
payloads were incidental - `roast=banana` would have done the same thing.

The tell is in the alert itself:

| `exc=` in the alert | Usually means |
|---|---|
| `KeyError`, `ValueError`, `IndexError`, `TypeError` | A route is taking a value from the form and using it as a dict key, a column name, or a number without checking it first. Fix with `parse_choice` / `parse_int` / `parse_float` / `parse_letters` from `helpers.py`, which raise `ValidationError` and become a clean 400. |
| `DatabaseError`, `PoolError`, `OperationalError` | An actual infrastructure problem. |

The distinction matters operationally: the first kind fires on *every* scanner
sweep and will train you to ignore `http_500`, which is the one alert that
reports a real fault the moment it happens. Every route that reads a value from
a fixed set should validate against that set, so a sweep produces 400s that
nobody is paged for.

`log_page_visit(f'error.html (500: {e})')` puts the exception message in
`vw_prod_errors`, which is how the cause was identified from the dashboard
alone - `error.html (500: '98766')` is a `KeyError` naming the exact rejected
value.

## What this does not catch

Anything wrong that produces neither a log line, a measurable database
symptom, nor a difference in what a probe fetches: wrong-but-plausible data
(YouTube returning 50 rows of stale videos), visual or CSS breakage, SEO
decline. Those still need occasional eyes.

Narrowed on 2026-09-28. "A solver returning wrong answers" used to be on this
list and is now partly covered: each web probe asserts an expected substring,
so a page that loads without rendering is caught within the hour. What is still
missing is a page that renders correctly while the *engine behind it* returns
garbage - a probe would have to POST a known input and check the answer. That
is the next step for `health_web.yaml`.

`mtg_prices_bsky.py` already handles its own staleness and failure alerting
and was left alone.

---

## Future: richer page-visit logging (not implemented)

Parked deliberately - the first deploy already carries enough change. The
research is recorded here so it does not have to be redone.

### The three columns to add

`app_visits` is `id, submit_time, page_name, referrer, user_agent`, all three
strings `varchar(255)`.

| Column | Type | Why |
|---|---|---|
| `status_code` | `SMALLINT NULL` | The status is currently smuggled into `page_name` as `error.html (404: ...)`, which is why the views match on `LIKE '%error%'`. A real column turns `errors_500_today` into `WHERE status_code = 500` and gives per-page 4xx/5xx rates. |
| `is_bot` | `TINYINT(1) NULL` | Stops crawler traffic holding `site_idle` quiet. Already handled by a UA regex inline in the check, so this is a cleanup rather than a capability. See below. |
| `country` | `CHAR(2) NULL` | Cloudflare already sets `CF-IPCountry` on every proxied request and the app throws it away. No logic needed, no PII. An empty value is informative too: the request bypassed Cloudflare. |

### What the bot rate justifies

Measured 2026-09-25 against the 238k rows of `user_agent` already collected:
**12.4% bots.** Re-measured 2026-09-28 over the trailing 28 days: **17%.**

That number alone is a weak argument: inflating an hourly baseline by 12%
barely changes when a check fires. The real risk is the **recency** half of
the idle checks. They alert on minutes-since-last-hit, so one polite crawler
on a 15-minute cycle holds the check quiet through a total collapse in human
traffic. What matters is bot *regularity*, not bot *share*.

**This was measured on 2026-09-28 and the concern is real, not theoretical.**
Replaying `site_idle` over 90 days, excluding obvious bots moved the longest
observed quiet stretch from 73 minutes to 116. Crawlers were genuinely filling
real gaps, which is exactly the masking described above.

And it applies to only one check:

- **`blossom_idle` needs nothing.** It reads `blossom_solver_clicks` - solver
  POSTs from someone typing into the form. Crawlers do not generate those.
- **`site_idle` is exposed.** It reads `app_visits` page GETs, which is exactly
  what crawlers produce.

`site_idle` already filters them, with the regex inline in its SQL rather than
through a column. So the column is now a **simplification**, not a new
capability: it would replace a regex over a day of rows with
`AND COALESCE(is_bot, 0) = 0`. The `COALESCE` would be load-bearing - rows
written before the change are NULL, and a bare `= 0` would discard all of them.

Keep the regex until the column has been populated for longer than the check's
lookback window, then swap. Whichever is in force, the exclusion of
`user_agent <> 'jj-healthcheck'` must survive: the hourly probes write
`app_visits` rows, and a check counting them is held quiet by its own prober.

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
| 4 | Update views and `site_idle` | whenever |

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

- **`ALGORITHM=INSTANT`** is metadata-only, milliseconds on 238k rows, and
  stating it explicitly makes MySQL *error* rather than silently falling back
  to a full rebuild. Adding at the end of the table is what qualifies;
  positioning with `AFTER` would force the rebuild.
- **`NULL`, not `DEFAULT 0`.** NULL honestly means "not recorded yet";
  `is_bot = 0` would claim every historical row was verified human.
- **Nothing reads `app_visits` with `SELECT *`** - all four views name their
  columns - and a view's column list is frozen at `CREATE` time anyway, so no
  view changes shape.

Prod and staging share one JawsDB, so the ALTER hits both. That is fine
because it is additive: old and new code both work against the altered table,
in either direction.

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

### View updates that would come with it

Once enough rows carry `status_code`, the error views can move from the
fragile `page_name LIKE '%error%'` matching to `status_code >= 400`. The other
view work is not future - it applies to the current deploy, and lives in
[7. Dashboard views](#7-dashboard-views).

---

## Ideas, not planned

Nothing here is scheduled or required. Recorded so the reasoning does not have
to be redone.

- **Shrink what `scheduled_tasks_redis/redis_wordle.py` serialises into
  `data_dict`.** ~9.4 KB per antiwordle click is a lot, and
  `wordle_revamp_clicks` - written by the same job - is only 36.6 MB, so
  whatever grew grew only on the antiwordle side. Capping it at source would
  end the growth in `antiwordle_revamp_clicks` rather than managing it with
  retention forever.
- **Add POST probes to `health_web.yaml`.** The GET probes assert a page
  rendered; they cannot see `/smush` rendering fine while the solver returns
  garbage. A probe that POSTs a known board and asserts a known word comes back
  closes that, and needs no bot filtering or traffic volume at all. This is the
  natural next step for the probe file and would have caught the class of
  failure `smush_idle` was reaching for.
- **Correction recorded.** After the 365-day pass on
  `antiwordle_revamp_clicks` freed only 21 MB, the conclusion written here was
  that age-based retention is a weak lever for that table. The 180-day pass
  freed 121 MB and disproved it. Retention works fine; the window just has to
  reach into the period where the rows are large.
