-- 4x3 today's boards and answers: the boards players enter into /4x3, so the
-- first players each day do the work for the players after them; the marks
-- players make on a board (what the game said about their guesses); and the
-- boards James approves with their answers on /4x3_admin/board
-- (routes/four_by_three.py). Once enough players have entered the same
-- board, later players are offered it, and James's answer (or groups enough
-- players confirmed) is served with it. James's answers also tune the solver.
--
-- Run this BEFORE deploying the code that uses it. One new table, nothing
-- existing is touched, so it is safe to run live and the current code never
-- notices. Prod and staging share the JawsDB; both are fine.
--
-- Code deployed without it fails safe: /4x3 solves as usual (no boards
-- offered, no answers served, default weights), boards and marks are lost
-- behind a `four_by_three_board_log_failed` alert, reads raise
-- `four_by_three_board_read_failed` and `four_by_three_tuning_failed`, and
-- /4x3_admin shows the error instead of the boards.

SET SESSION lock_wait_timeout = 5;

-- One row per (board, player, date). A "player" is an HMAC of the visitor's
-- IP (crowd.player_hash), never the IP itself; an approval is a row of its
-- own with 'admin' in its place, one a date.
CREATE TABLE four_by_three_boards (
    id              INT            NOT NULL AUTO_INCREMENT,
    played_on       DATE           NOT NULL,   -- the player's own date (the game's day turns at their midnight)
    voter_hash      VARCHAR(40)    NOT NULL,   -- crowd.player_hash, or 'admin' on an approval
    prefix_hash     CHAR(32)       NULL,       -- crowd.prefix_hash (the player's network); NULL on approvals
    board_key       CHAR(40)       NOT NULL,   -- sha1 of the nine words, sorted: the board's identity
    words           VARCHAR(400)   NOT NULL,   -- the nine words as typed, upper case, '|'-joined
    source          VARCHAR(8)     NOT NULL,   -- 'typed', 'edited' (loaded, then changed), 'loaded' (marks only) or 'admin'
    marks           VARCHAR(2000)  NULL,       -- the player's marks, in order: JSON [["W1|W2|W3", "r"|"a"|"n"|"h"], ...]
    answers         VARCHAR(1000)  NULL,       -- an approval's answer: JSON {"hub", "pairs", "names"}; NULL for the board alone
    solver_top      VARCHAR(1000)  NULL,       -- an approval's: the solver's own first pass before it (JSON), for the Accuracy tab
    solver_version  CHAR(8)        NULL,       -- which solver made that first pass
    created_at      DATETIME       NOT NULL,   -- Pacific time, stamped in Python
    updated_at      DATETIME       NOT NULL,   -- when the player (or James) last changed it
    PRIMARY KEY (id),
    UNIQUE KEY uq_board_player_day (board_key, voter_hash, played_on),
    KEY idx_played_on (played_on),
    KEY idx_player_day (voter_hash, played_on),
    KEY idx_source_updated (source, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads this):
--
-- DROP TABLE four_by_three_boards;
