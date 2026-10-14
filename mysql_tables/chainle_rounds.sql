-- Chainle: rounds the admin logs on /chainle_admin after playing, to tune the
-- solver's ranking against (routes/chainle.py). One row per round played: its
-- clues, the game's answer, the admin's guess and the score it got. The solver
-- itself never reads this table.
--
-- Run this BEFORE deploying the code that uses it. One new table, nothing
-- existing is touched, so it is safe to run live and the current code never
-- notices. Prod and staging share the JawsDB; both are fine.
--
-- Code deployed without the table fails safe: /chainle never touches the
-- database, and only /chainle_admin errors until the table exists.

SET SESSION lock_wait_timeout = 5;

-- Logging the same date, site and round again replaces the row
-- (INSERT ... ON DUPLICATE KEY UPDATE on uq_round).
CREATE TABLE chainle_rounds (
    id          INT           NOT NULL AUTO_INCREMENT,
    played_on   DATE          NOT NULL,  -- the puzzle's day
    site        VARCHAR(32)   NOT NULL,  -- where it was played: routes/chainle.py SITES
    round_no    TINYINT       NOT NULL,  -- 1 to 5
    kind        VARCHAR(8)    NOT NULL,  -- 'bridge' or 'meaning'
    clues       VARCHAR(80)   NOT NULL,  -- upper case, space-separated
    answer      VARCHAR(40)   NULL,      -- the game's answer, upper case
    guess       VARCHAR(40)   NULL,      -- the admin's guess, upper case
    score       SMALLINT      NULL,      -- points the guess earned, 0 to 1000
    notes       VARCHAR(200)  NULL,
    created_at  DATETIME      NOT NULL,  -- Pacific time, stamped in Python
    PRIMARY KEY (id),
    UNIQUE KEY uq_round (played_on, site, round_no),
    KEY idx_played (played_on)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads this table):
--
-- DROP TABLE chainle_rounds;
