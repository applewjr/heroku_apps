-- Blossom's today's board: the boards players enter into /blossom, so the
-- first players each day do the typing for everyone after them
-- (routes/blossom.py, "today's board"). Once James approves the day's board
-- in /blossom_admin, or enough players have entered the same one, later
-- players are offered it ("Today's board:", with a Load it button) and
-- a box of hints for it.
--
-- Run this BEFORE deploying the code that uses it. One new table, nothing
-- existing is touched, so it is safe to run live and the current code never
-- notices. Prod and staging share the JawsDB; both are fine.
--
-- Code deployed without it fails safe: /blossom works as today (no offer, no
-- hints), boards are lost behind a throttled `blossom_board_log_failed`
-- alert, reads fail behind `blossom_board_read_failed`, and /blossom_admin's
-- Today's Board tab shows the error instead of the boards.

SET SESSION lock_wait_timeout = 5;

-- One row per (puzzle, player, day): a player's later board of the same
-- puzzle that day replaces their earlier one. A "player" is an HMAC of the
-- visitor's IP (crowd.player_hash), never the IP itself, and prefix_hash is
-- their network (crowd.prefix_hash), so one network counts once. An approval
-- is one row per (puzzle, day). All letters are stored lowercase.
CREATE TABLE blossom_boards (
    id           INT          NOT NULL AUTO_INCREMENT,
    played_on    DATE         NOT NULL,   -- the player's own date (Blossom rolls over at their midnight)
    voter_hash   CHAR(32)     NOT NULL,   -- crowd.player_hash('blossom'), or 'admin' on an approval
    prefix_hash  CHAR(32)     NULL,       -- crowd.prefix_hash('blossom'); NULL on approvals
    letters      CHAR(7)      NOT NULL,   -- the puzzle: the center, then the six petals sorted
    board        CHAR(7)      NOT NULL,   -- the board state: the center, then the petals in spots petal-0..5
    source       VARCHAR(8)   NOT NULL,   -- 'typed', 'edited' (loaded from other players, then changed) or 'admin'
    created_at   DATETIME     NOT NULL,   -- Pacific time, stamped in Python
    updated_at   DATETIME     NOT NULL,   -- when the player last changed this board
    PRIMARY KEY (id),
    UNIQUE KEY uq_board_player_day (letters, voter_hash, played_on),
    KEY idx_played_on (played_on),
    KEY idx_player_day (voter_hash, played_on)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads this):
--
-- DROP TABLE blossom_boards;
