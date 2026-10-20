-- Ribbit shared boards: players on one network count once (routes/ribbit.py,
-- consensus). Each board gets the network it came from next to its player
-- hash: an HMAC of the visitor's IPv6 /48, or of their whole IPv4 address
-- (crowd.prefix_hash), never the address itself. Boards logged before this
-- column existed keep a NULL here and count by their player hash, as before.
--
-- Run this BEFORE deploying the code that uses it. One nullable column, added
-- in place (JawsDB is MySQL 8.4, so ALGORITHM=INSTANT), nothing else touched:
-- safe to run live, and the current code never notices. Prod and staging share
-- the JawsDB; both are fine.
--
-- Code deployed without it fails safe: /ribbit works as before, but boards are
-- lost behind a `ribbit_board_log_failed` alert, none is offered to players,
-- and /ribbit_admin shows the error instead of the boards.

SET SESSION lock_wait_timeout = 5;

ALTER TABLE ribbit_boards
    ADD COLUMN prefix_hash CHAR(32) NULL,   -- crowd.prefix_hash; NULL on approvals and older boards
    ALGORITHM=INSTANT;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads this column):
--
-- SET SESSION lock_wait_timeout = 5;
-- ALTER TABLE ribbit_boards DROP COLUMN prefix_hash, ALGORITHM=INSTANT;
