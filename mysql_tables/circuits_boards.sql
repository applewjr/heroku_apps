-- Circuits today's boards: the boards players enter into /circuits, so the
-- first players each day do the work for the players after them, plus the
-- boards and answers James approves on /circuits_admin (routes/circuits.py,
-- "boards"). Once enough players have entered the same board, later players
-- are offered it; a board with answers shows them as checked.
--
-- Run this BEFORE deploying the code that uses it, with circuits_pairs.sql.
-- A table made from an earlier copy of this file, without the shape column,
-- gets it from circuits_boards_shape.sql instead, and its longer columns (for
-- boards up to 8 by 8) from circuits_boards_big.sql.
-- One new table, nothing existing is touched, so it is safe to run live and
-- the current code never notices. Prod and staging share the JawsDB; both
-- are fine.
--
-- Code deployed without it fails safe: /circuits solves as usual (no boards
-- offered, no checked answers), boards are lost behind a
-- `circuits_board_log_failed` alert, reads raise `circuits_board_read_failed`,
-- and /circuits_admin shows the error instead of the boards.

SET SESSION lock_wait_timeout = 5;

-- One row per (board, player, day). A "player" is an HMAC of the visitor's IP
-- (crowd.player_hash), never the IP itself; an approval is a row of its own
-- with 'admin:<site>' in its place.
CREATE TABLE circuits_boards (
    id              INT           NOT NULL AUTO_INCREMENT,
    played_on       DATE          NOT NULL,   -- the game day: the date in America/Chicago (Puzzmo's calendar)
    voter_hash      VARCHAR(40)   NOT NULL,   -- crowd.player_hash, or 'admin:puzzmo' / 'admin:yahoo' on an approval
    prefix_hash     CHAR(32)      NULL,       -- crowd.prefix_hash (the player's network); NULL on approvals
    board_key       CHAR(40)      NOT NULL,   -- sha1 of the cells (and the size, if not 5x3): the board's identity
    cells           VARCHAR(2000) NOT NULL,   -- the squares it uses in reading order, '|'-joined: '' blank, '?' a box, else the tile
    arrows          VARCHAR(1000) NOT NULL,   -- 'tail-head' cell pairs, comma-separated (cell = row * columns + column)
    site            VARCHAR(10)   NOT NULL DEFAULT '',   -- 'puzzmo' or 'yahoo' on an approval, '' on a player's board
    answers         VARCHAR(2000) NULL,       -- approvals: James's answers, a word per box in reading order, '|'-joined
    entered         VARCHAR(2000) NULL,       -- players: the words they kept, a word per box, '|'-joined (shown to James only)
    solver_top      VARCHAR(2000) NULL,       -- players: the solver's picks when the board was logged, a word per box
    solver_version  CHAR(8)       NULL,       -- which solver made those picks (routes/circuits.py solver_version)
    source          VARCHAR(8)    NOT NULL,   -- 'typed', 'edited' (loaded, then changed), 'loaded' (words only) or 'admin'
    created_at      DATETIME      NOT NULL,   -- Pacific time, stamped in Python
    updated_at      DATETIME      NOT NULL,   -- when the player (or James) last changed it
    shape           VARCHAR(5)    NOT NULL DEFAULT '5x3',   -- rows x columns, 1 to 8 each: '5x3' for any board that fits in 5 by 3
    PRIMARY KEY (id),
    UNIQUE KEY uq_board_player_day (board_key, voter_hash, played_on),
    KEY idx_played_on (played_on),
    KEY idx_player_day (voter_hash, played_on)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads this):
--
-- DROP TABLE circuits_boards;
