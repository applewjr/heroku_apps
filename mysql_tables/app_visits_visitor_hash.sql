-- app_visits: a same-day visitor id, so the dashboard views can count people
-- as well as page loads (vw_prod_traffic_today, vw_prod_solver_use_7d).
-- visitor_hash is 16 hex characters of an HMAC of the visitor's IP and user
-- agent, salted with the Pacific date (helpers.visitor_hash): never the IP
-- itself, and a new value every day, so nobody can be followed across days.
-- Rows logged before this column existed keep a NULL here.
--
-- Run this BEFORE deploying the code that writes it. One nullable column,
-- added in place (JawsDB is MySQL 8.4, so ALGORITHM=INSTANT), nothing else
-- touched: safe to run live, and the deployed code never notices, because its
-- INSERT names its four columns. Prod and staging share the JawsDB; both are
-- fine.
--
-- Code deployed without it fails safe for pages but loses logging: every
-- app_visits row fails to write (error 1054), and so do any
-- blossom_solver_clicks rows queued behind one in the same batch, with a
-- `log_drain_failed` alert every 15 minutes. Running this stops it at once.
--
-- If ALGORITHM=INSTANT is refused, nothing has changed. Stop there rather
-- than dropping the clause, which would rebuild the table.

SET SESSION lock_wait_timeout = 5;

ALTER TABLE app_visits
    ADD COLUMN visitor_hash CHAR(16) NULL,   -- helpers.visitor_hash; NULL on rows from before
    ALGORITHM=INSTANT;

-- Then create mysql_views/dashboard/vw_prod_solver_use_7d.sql: it only needs
-- the column, and nothing reads it until the code with its panel ships.
--
-- Checks (read-only):
-- SHOW COLUMNS FROM app_visits LIKE 'visitor_hash';
-- SELECT COUNT(*), COUNT(visitor_hash) FROM app_visits
-- WHERE submit_time >= pst_now() - INTERVAL 15 MINUTE;
--   rows still arriving from the deployed code, none with an id until the
--   new code ships.


-- ---------------------------------------------------------------------------
-- Rollback. Leaving the NULL column in place is harmless. To remove it, first
-- deploy code that doesn't write it, on prod and staging, and put back the
-- views that read it (vw_prod_traffic_today from git, and DROP VIEW
-- vw_prod_solver_use_7d):
--
-- SET SESSION lock_wait_timeout = 5;
-- ALTER TABLE app_visits DROP COLUMN visitor_hash, ALGORITHM=INSTANT;
