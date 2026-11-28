-- Firsts today's boards and answers: the boards players enter into /firsts,
-- so the first players each day do the work for the players after them; the
-- marks players make on a board (which words the game took or refused); and
-- the boards James approves with the answers he checked, on
-- /firsts_admin/board (routes/firsts.py). Once enough players have entered
-- the same board, later players are offered it, and James's answers (or
-- words enough players say worked) are served with it.
--
-- Run this BEFORE deploying the code that uses it. One new table, nothing
-- existing is touched, so it is safe to run live and the current code never
-- notices. Prod and staging share the JawsDB; both are fine.
--
-- Code deployed without it fails safe: /firsts solves as usual (no boards
-- offered, no answers served), boards and marks are lost behind a
-- `firsts_board_log_failed` alert, reads raise `firsts_board_read_failed`,
-- and /firsts_admin shows the error instead of the boards.

SET SESSION lock_wait_timeout = 5;

-- One row per (board, player, date). A "player" is an HMAC of the visitor's
-- IP (crowd.player_hash), never the IP itself; an approval is a row of its
-- own with 'admin' in its place, one a date.
CREATE TABLE firsts_boards (
    id              INT            NOT NULL AUTO_INCREMENT,
    played_on       DATE           NOT NULL,   -- the puzzle's date: No. N is 2026-11-25 + N days
    voter_hash      VARCHAR(40)    NOT NULL,   -- crowd.player_hash, or 'admin' on an approval
    prefix_hash     CHAR(32)       NULL,       -- crowd.prefix_hash (the player's network), NULL on approvals
    board_key       VARCHAR(120)   CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
                                               -- 19 tiles (a picture as a digit), 19 greens (0/1), 10 lengths, colon-separated
    details         TEXT           NULL,       -- what rides along: JSON {"title", "clues": [[text, length], ...], "names": {"1": "sun"}}, or NULL on a marks-only row
    source          VARCHAR(8)     NOT NULL,   -- 'typed', 'edited' (loaded, then changed), 'loaded' (marks only) or 'admin'
    marks           VARCHAR(2000)  NULL,       -- the player's marks: JSON {"clue index": {"WORD": "right"|"miss"}}
    answers         VARCHAR(1000)  NULL,       -- an approval's checked answers: JSON {"words": {"clue index": "WORD"}, "readings": {"1": "SUN"}}
    solver_top      VARCHAR(500)   NULL,       -- an approval's: the solver's own first pick per clue before any answer (JSON list)
    solver_version  VARCHAR(16)    NULL,       -- which solver made that first pass
    created_at      DATETIME       NOT NULL,   -- Pacific time, stamped in Python
    updated_at      DATETIME       NOT NULL,   -- when the player (or James) last changed it
    PRIMARY KEY (id),
    UNIQUE KEY uq_board_player_day (board_key, voter_hash, played_on),
    KEY idx_played_on (played_on),
    KEY idx_player_day (voter_hash, played_on),
    KEY idx_source_played (source, played_on)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads this):
--
-- DROP TABLE firsts_boards;
