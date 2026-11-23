-- 4x6 today's boards: the boards players enter into /4x6, so the first
-- players each day do the work for the players after them, and the boards
-- James approves on /4x6_admin (routes/four_by_six.py). Once enough players
-- have entered the same board, later players are offered it. Only the board
-- is kept: the solver works out every move itself.
--
-- Run this BEFORE deploying the code that uses it. One new table, nothing
-- existing is touched, so it is safe to run live and the current code never
-- notices. Prod and staging share the JawsDB; both are fine.
--
-- Code deployed without it fails safe: /4x6 solves as usual (no boards
-- offered), boards are lost behind a `four_by_six_board_log_failed` alert,
-- reads raise `four_by_six_board_read_failed`, and /4x6_admin shows the
-- error instead of the boards.

SET SESSION lock_wait_timeout = 5;

-- One row per (board, player, date). A "player" is an HMAC of the visitor's
-- IP (crowd.player_hash), never the IP itself; an approval is a row of its
-- own with 'admin' in its place, one a date.
CREATE TABLE four_by_six_boards (
    id              INT            NOT NULL AUTO_INCREMENT,
    played_on       DATE           NOT NULL,   -- the player's own date (the game's day turns at their midnight)
    voter_hash      VARCHAR(40)    NOT NULL,   -- crowd.player_hash, or 'admin' on an approval
    prefix_hash     CHAR(32)       NULL,       -- crowd.prefix_hash (the player's network); NULL on approvals
    board           CHAR(24)       NOT NULL,   -- the starting tiles, shelf by shelf, a color code each (functions/four_by_six.py): the board's identity
    source          VARCHAR(8)     NOT NULL,   -- 'typed', 'edited' (loaded, then changed) or 'admin'
    created_at      DATETIME       NOT NULL,   -- Pacific time, stamped in Python
    updated_at      DATETIME       NOT NULL,   -- when the player (or James) last sent it
    PRIMARY KEY (id),
    UNIQUE KEY uq_board_player_day (board, voter_hash, played_on),
    KEY idx_played_on (played_on),
    KEY idx_player_day (voter_hash, played_on)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads this):
--
-- DROP TABLE four_by_six_boards;
