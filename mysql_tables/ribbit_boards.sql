-- Ribbit shared boards: the boards players enter into /ribbit, so the first
-- few players each day do the work for everyone after them (routes/ribbit.py,
-- "shared boards"). Once enough players have entered the same board, later
-- players get a "Load today's board" button, and anyone who types a known
-- board's letters gets its links filled in.
--
-- Run this BEFORE deploying the code that uses it. One new table, nothing
-- existing is touched, so it is safe to run live and the current code never
-- notices. Prod and staging share the JawsDB; both are fine.
--
-- Code deployed without it fails safe: /ribbit works as before (no button, no
-- filled-in links), boards are lost behind a `ribbit_board_log_failed` alert,
-- and /ribbit_admin shows the error instead of the boards.

SET SESSION lock_wait_timeout = 5;

-- One row per (board, player, day), in one canonical orientation: the
-- smallest of the board's 8 rotations and flips, so the same board entered
-- turned or mirrored is the same board. A "player" is an HMAC of the
-- visitor's IP (crowd.player_hash), never the IP itself.
CREATE TABLE ribbit_boards (
    id           INT           NOT NULL AUTO_INCREMENT,
    played_on    DATE          NOT NULL,   -- the player's own date: today's puzzle, or an archive one played that day
    voter_hash   CHAR(32)      NOT NULL,
    letters      CHAR(16)      NOT NULL,   -- the 16 letters, canonical orientation, row by row
    links        VARCHAR(255)  NOT NULL,   -- canonical links: sorted 'a-b' cell pairs (cell = row * 4 + col), comma-separated
    orientation  TINYINT       NOT NULL,   -- which of the 8 rotations/flips turned the player's view into the canonical one
    source       VARCHAR(8)    NOT NULL,   -- 'typed' by the player, or 'edited' (loaded from other players, then changed)
    created_at   DATETIME      NOT NULL,   -- Pacific time, stamped in Python
    updated_at   DATETIME      NOT NULL,   -- when the player last changed this board
    PRIMARY KEY (id),
    UNIQUE KEY uq_board_player_day (letters, voter_hash, played_on),
    KEY idx_played_on (played_on),
    KEY idx_player_day (voter_hash, played_on)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads this):
--
-- DROP TABLE ribbit_boards;
