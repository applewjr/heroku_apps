-- Wordiply's today's starter word: the starter words players type into
-- /wordiply, so the first players each day do the typing for everyone after
-- them (routes/wordiply.py). Once James approves the day's starter in
-- /wordiply_admin, or enough players have entered the same one, later
-- players are offered it ("Today's starter word:", with a Load it button).
--
-- Run this BEFORE deploying the code that uses it. One new table, nothing
-- existing is touched, so it is safe to run live and the current code never
-- notices. Prod and staging share the JawsDB; both are fine.
--
-- Code deployed without it fails safe: /wordiply works as today (no offer),
-- starters are lost behind a throttled `wordiply_board_log_failed` alert,
-- reads fail behind `wordiply_board_read_failed`, and /wordiply_admin shows
-- the error instead of the starters.

SET SESSION lock_wait_timeout = 5;

-- One row per (starter, player, day): a player who uses the same starter
-- again that day updates their row. A "player" is an HMAC of the visitor's
-- IP (crowd.player_hash), never the IP itself, and prefix_hash is their
-- network (crowd.prefix_hash), so one network counts once. An approval is
-- one row per day. Letters are stored lowercase.
CREATE TABLE wordiply_boards (
    id           INT          NOT NULL AUTO_INCREMENT,
    played_on    DATE         NOT NULL,   -- Wordiply's day: the date in Europe/London, from the server's clock
    voter_hash   CHAR(32)     NOT NULL,   -- crowd.player_hash('wordiply'), or 'admin' on an approval
    prefix_hash  CHAR(32)     NULL,       -- crowd.prefix_hash('wordiply'); NULL on approvals
    letters      VARCHAR(15)  NOT NULL,   -- the starter word, a-z
    source       VARCHAR(8)   NOT NULL,   -- 'typed', 'edited' (loaded from other players, then changed) or 'admin'
    created_at   DATETIME     NOT NULL,   -- Pacific time, stamped in Python
    updated_at   DATETIME     NOT NULL,   -- when the player last used this starter
    PRIMARY KEY (id),
    UNIQUE KEY uq_starter_player_day (letters, voter_hash, played_on),
    KEY idx_played_on (played_on),
    KEY idx_player_day (voter_hash, played_on)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads this):
--
-- DROP TABLE wordiply_boards;
