-- Chainle round log, fourth change (after chainle_rounds_closeness.sql): which
-- version of the solver made the picks.
--
-- Tuning changes the solver often. With each round the admin saves a short
-- code for the solver version in use (a hash of its code and word data, see
-- solver_version() in routes/chainle.py), that version's first pick, and where
-- it ranked the game's answer, all as they were when the round was logged. The
-- admin page then splits the rounds by version.
--
-- Safe to run before or after deploying the code that writes these columns:
-- older code names the columns it reads and writes, so it ignores these, and
-- the new code checks for them and leaves them out until they exist. Existing
-- rows get NULLs.

SET SESSION lock_wait_timeout = 5;

ALTER TABLE chainle_rounds
    ADD COLUMN solver_version CHAR(8) NULL,   -- e.g. '3f9a2c1b'
    ADD COLUMN solver_top VARCHAR(40) NULL,   -- that version's first pick, upper case
    ADD COLUMN answer_rank INT NULL;          -- its rank for the game's answer (NULL past 100)


-- ---------------------------------------------------------------------------
-- Rollback (any code version works without these columns):
--
-- ALTER TABLE chainle_rounds DROP COLUMN solver_version, DROP COLUMN solver_top, DROP COLUMN answer_rank;
