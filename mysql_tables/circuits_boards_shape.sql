-- Circuits today's boards: boards come in more than one shape (routes/circuits.py).
-- Each board gets its shape, rows x columns, next to its cells: '5x3' (Puzzmo's
-- so far) or '4x4' (Yahoo's). Boards logged before this column existed are all
-- 5 by 3, which is what the default fills in, and a 5 by 3 board keeps the
-- board_key it always had, so they still match the boards logged after.
--
-- For a circuits_boards table made from circuits_boards.sql before it had this
-- column. A table made from today's circuits_boards.sql has it already.
--
-- Run this BEFORE deploying the code that uses it. One column with a default,
-- added in place (JawsDB is MySQL 8.4, so ALGORITHM=INSTANT), nothing else
-- touched: safe to run live. Prod and staging share the JawsDB; both are fine.
--
-- Code deployed without it fails safe: /circuits solves as usual, but boards
-- are lost behind a `circuits_board_log_failed` alert, none is offered to
-- players (`circuits_board_read_failed`), and /circuits_admin shows the error
-- instead of the boards.

SET SESSION lock_wait_timeout = 5;

ALTER TABLE circuits_boards
    ADD COLUMN shape VARCHAR(5) NOT NULL DEFAULT '5x3',   -- rows x columns: '5x3' or '4x4'
    ALGORITHM=INSTANT;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads this column):
--
-- SET SESSION lock_wait_timeout = 5;
-- ALTER TABLE circuits_boards DROP COLUMN shape, ALGORITHM=INSTANT;
