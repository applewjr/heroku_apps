-- Guess The Phrase's today's board: the word lengths players tap in on
-- /guess_the_phrase, so the first players each day do it for everyone after
-- them (routes/guess_the_phrase.py, "today's board"). Once James approves the
-- day's board in /guess_the_phrase_admin, or enough players have entered the
-- same one, later players are offered it ("Today's board", with a Load it
-- button).
--
-- A board is its word lengths and the category, nothing else: no letters are
-- ever stored, and never a phrase.
--
-- Run this BEFORE deploying the code that uses it. One new table, nothing
-- existing is touched, so it is safe to run live and the current code never
-- notices. Prod and staging share the JawsDB; both are fine.
--
-- Code deployed without it fails safe: /guess_the_phrase works as today (no
-- offer), boards are lost behind a throttled `gtp_board_log_failed` alert,
-- reads fail behind `gtp_board_read_failed`, and /guess_the_phrase_admin's
-- Today's Board tab shows the error instead of the boards.

SET SESSION lock_wait_timeout = 5;

-- One row per (board, player, day): a player who uses the same board again
-- that day updates their row. A "player" is an HMAC of the visitor's IP
-- (crowd.player_hash), never the IP itself, and prefix_hash is their network
-- (crowd.prefix_hash), so one network counts once. An approval is one row per
-- day.
CREATE TABLE gtp_boards (
    id           INT          NOT NULL AUTO_INCREMENT,
    played_on    DATE         NOT NULL,   -- the game's day, from the server's clock (_game_today)
    voter_hash   CHAR(32)     NOT NULL,   -- crowd.player_hash('gtp'), or 'admin' on an approval
    prefix_hash  CHAR(32)     NULL,       -- crowd.prefix_hash('gtp'); NULL on approvals
    shape        VARCHAR(40)  NOT NULL,   -- each word's length, joined by dashes: '3-3-4'
    category     VARCHAR(20)  NOT NULL,   -- the solver's category id; 'any' if they chose none
    source       VARCHAR(8)   NOT NULL,   -- 'typed', 'edited' (loaded from other players, then changed) or 'admin'
    created_at   DATETIME     NOT NULL,   -- Pacific time, stamped in Python
    updated_at   DATETIME     NOT NULL,   -- when the player last used this board
    PRIMARY KEY (id),
    UNIQUE KEY uq_board_player_day (shape, voter_hash, played_on),
    KEY idx_played_on (played_on),
    KEY idx_player_day (voter_hash, played_on)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads this):
--
-- DROP TABLE gtp_boards;
