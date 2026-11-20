-- Typeshift today's boards: the boards players enter into /typeshift, so the
-- first players each day do the work for the players after them, plus the
-- boards James approves on /typeshift_admin (routes/typeshift.py, "boards").
-- Once enough players have entered the same board, later players are
-- offered it.
--
-- Run this BEFORE deploying the code that uses it, with typeshift_crowd.sql.
-- One new table, nothing existing is touched, so it is safe to run live and
-- the current code never notices. Prod and staging share the JawsDB; both
-- are fine.
--
-- Code deployed without it fails safe: /typeshift solves as usual (no boards
-- offered), boards are lost behind a `typeshift_board_log_failed` alert,
-- reads raise `typeshift_board_read_failed`, and /typeshift_admin shows the
-- error instead of the boards.

SET SESSION lock_wait_timeout = 5;

-- One row per (board, player, day). A "player" is an HMAC of the visitor's IP
-- (crowd.player_hash), never the IP itself; an approval is a row of its own
-- with 'admin:<site>' in its place.
CREATE TABLE typeshift_boards (
    id              INT           NOT NULL AUTO_INCREMENT,
    played_on       DATE          NOT NULL,   -- the game day: the date in America/Chicago (Puzzmo's calendar)
    voter_hash      VARCHAR(40)   NOT NULL,   -- crowd.player_hash, or 'admin:puzzmo' on an approval
    prefix_hash     CHAR(32)      NULL,       -- crowd.prefix_hash (the player's network); NULL on approvals
    board_key       CHAR(40)      NOT NULL,   -- sha1 of each column's letters, sorted: the board's identity
    letters         VARCHAR(120)  NOT NULL,   -- the columns as typed, top to bottom, upper case, '|'-joined
    site            VARCHAR(10)   NOT NULL DEFAULT '',   -- 'puzzmo' on an approval, '' on a player's board
    source          VARCHAR(8)    NOT NULL,   -- 'typed', 'edited' (loaded, then changed) or 'admin'
    created_at      DATETIME      NOT NULL,   -- Pacific time, stamped in Python
    updated_at      DATETIME      NOT NULL,   -- when the player (or James) last changed it
    PRIMARY KEY (id),
    UNIQUE KEY uq_board_player_day (board_key, voter_hash, played_on),
    KEY idx_played_on (played_on),
    KEY idx_player_day (voter_hash, played_on)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads this):
--
-- DROP TABLE typeshift_boards;
