-- Chainle's today's rounds: the rounds players type into /chainle, so the
-- first players each day do the typing for everyone after them
-- (routes/chainle.py, "today's rounds"). Once enough players have typed the
-- same clues for a round, or James approves a day's rounds in /chainle_admin,
-- later players are offered them under an empty round, and tap the one that
-- matches their game.
--
-- Run this BEFORE deploying the code that uses it. One new table, nothing
-- existing is touched, so it is safe to run live and the current code never
-- notices. Prod and staging share the JawsDB; both are fine.
--
-- Code deployed without it fails safe: /chainle works as today (no offer),
-- rounds are lost behind a throttled `chainle_board_log_failed` alert, reads
-- fail behind `chainle_board_read_failed`, and /chainle_admin's Today's
-- Rounds tab shows the error instead of the rounds.

SET SESSION lock_wait_timeout = 5;

-- One row per (player, day, tab): a player's later entry in the same tab that
-- day replaces their earlier one. A "player" is an HMAC of the visitor's IP
-- (crowd.player_hash), never the IP itself, and prefix_hash is their network
-- (crowd.prefix_hash), so one network counts once. An approval is one row per
-- (day, site, round), with voter_hash 'admin'. Players never say which site
-- they play on, so their rows carry site ''.
CREATE TABLE chainle_boards (
    id           INT          NOT NULL AUTO_INCREMENT,
    played_on    DATE         NOT NULL,   -- the player's own date, or the approved day
    voter_hash   CHAR(32)     NOT NULL,   -- crowd.player_hash('chainle'), or 'admin' on an approval
    prefix_hash  CHAR(32)     NULL,       -- crowd.prefix_hash('chainle'); NULL on approvals
    site         VARCHAR(16)  NOT NULL DEFAULT '',  -- '' for players; the site an approval is for
    round_no     TINYINT      NOT NULL,   -- 1 to 5: the tab it was typed in, or the approved round
    clues        VARCHAR(64)  NOT NULL,   -- upper case, space-separated, in the order typed (matching ignores it)
    utc_offset   SMALLINT     NULL,       -- the player's clock, minutes ahead of UTC; NULL on approvals
    source       VARCHAR(8)   NOT NULL,   -- 'typed', 'edited' (loaded from other players, then changed) or 'admin'
    created_at   DATETIME     NOT NULL,   -- Pacific time, stamped in Python
    updated_at   DATETIME     NOT NULL,   -- when the player last changed this round
    PRIMARY KEY (id),
    -- Starts with played_on, so it also serves every read (by date).
    UNIQUE KEY uq_round_player_day (played_on, voter_hash, site, round_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads this):
--
-- DROP TABLE chainle_boards;
