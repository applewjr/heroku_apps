-- Chainle's confirmed #1 words: the best word for a round, which James enters
-- on /chainle_admin's Today's Rounds tab after he has played it
-- (routes/chainle.py, "confirmed words"). /chainle then puts that word at the
-- top of the list for anyone who enters those clues, marked Checked.
--
-- Run this BEFORE deploying the code that uses it. One new table, nothing
-- existing is touched, so it is safe to run live and the current code never
-- notices. Prod and staging share the JawsDB; both are fine.
--
-- Code deployed without it fails safe: /chainle suggests words as today,
-- reads fail behind a throttled `chainle_best_read_failed` alert, and Save on
-- /chainle_admin says "Database error".

SET SESSION lock_wait_timeout = 5;

-- One row per round: its kind and its clue words in any order. Not keyed by
-- date, since the same clues have the same best word whenever they're played.
CREATE TABLE chainle_best (
    id              INT          NOT NULL AUTO_INCREMENT,
    kind            VARCHAR(8)   NOT NULL,   -- 'bridge' or 'meaning'
    clue_key        VARCHAR(64)  NOT NULL,   -- the clue words, upper case, alphabetical, one space apart
    word            VARCHAR(40)  NOT NULL,   -- the confirmed word, upper case
    played_on       DATE         NOT NULL,   -- the day of the set it was entered on
    solver_top      VARCHAR(40)  NULL,       -- the solver's own first pick when it was entered
    solver_rank     SMALLINT     NULL,       -- where the solver had the word then; NULL: not in its first 100
    solver_version  CHAR(8)      NULL,       -- which solver that was
    created_at      DATETIME     NOT NULL,   -- Pacific time, stamped in Python
    updated_at      DATETIME     NOT NULL,   -- when James last changed it
    PRIMARY KEY (id),
    UNIQUE KEY uq_round (kind, clue_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads this):
--
-- DROP TABLE chainle_best;
