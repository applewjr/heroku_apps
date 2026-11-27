-- Retire the old solver request logs (2026-11-26). The Wordle and Antiwordle
-- solvers used to put every request on two Redis streams, and the nightly
-- scheduled_tasks_redis/redis_wordle.py job moved them into
-- wordle_revamp_clicks and antiwordle_revamp_clicks. wordle_clicks,
-- quordle_clicks and blossom_clicks are older logs, which the solver pages
-- stopped writing in July 2025 (last rows 2025-07-06, 2025-07-17 and
-- 2025-07-07). Nothing reads any of them now: no code, and no view, routine,
-- trigger, event or foreign key (checked 2026-11-26). Together they were
-- about half of the database. The solvers' usage is still counted in
-- app_visits (the '(solve)' rows). blossom_solver_clicks is a different,
-- live table and stays.
--
-- Run this AFTER the deploy that stops the logging, and after the
-- redis_wordle Heroku Scheduler job is gone, so nothing writes to them.
-- Prod and staging share the JawsDB, so this runs once.
--
-- NOT REVERSIBLE. A DROP frees the space at once and leaves no copy. If you
-- want one, take it first, from your own machine (it asks for the password):
--
--   mysqldump --single-transaction --no-tablespaces -h <host> -u <user> -p <database> \
--       wordle_revamp_clicks antiwordle_revamp_clicks wordle_clicks quordle_clicks blossom_clicks \
--       | gzip > old_click_logs_2026-11-26.sql.gz

SET SESSION lock_wait_timeout = 5;

-- 1. Check first: all three lists should come back empty. Anything listed
-- still uses a table: read its whole definition before dropping. Triggers
-- are matched on their body too, since a trigger on another table that
-- writes to these would break after the drop. The pattern matches all five
-- table names, and not blossom_solver_clicks.
SELECT TABLE_NAME FROM information_schema.VIEWS
WHERE TABLE_SCHEMA = DATABASE()
  AND VIEW_DEFINITION REGEXP '(wordle_(revamp_)?|quordle_|blossom_)clicks';

SELECT TRIGGER_NAME, EVENT_OBJECT_TABLE FROM information_schema.TRIGGERS
WHERE TRIGGER_SCHEMA = DATABASE()
  AND (EVENT_OBJECT_TABLE IN ('wordle_revamp_clicks', 'antiwordle_revamp_clicks', 'wordle_clicks',
                              'quordle_clicks', 'blossom_clicks')
       OR ACTION_STATEMENT REGEXP '(wordle_(revamp_)?|quordle_|blossom_)clicks');

SELECT ROUTINE_NAME FROM information_schema.ROUTINES
WHERE ROUTINE_SCHEMA = DATABASE()
  AND ROUTINE_DEFINITION REGEXP '(wordle_(revamp_)?|quordle_|blossom_)clicks';

-- 2. Drop them.
DROP TABLE wordle_revamp_clicks;
DROP TABLE antiwordle_revamp_clicks;
DROP TABLE wordle_clicks;
DROP TABLE quordle_clicks;
DROP TABLE blossom_clicks;
