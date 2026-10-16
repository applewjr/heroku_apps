-- Chainle round log, third change (after chainle_rounds_unlimited.sql): what
-- the game shows after a guess.
--
-- After each guess the game shows how close the guess came to each clue (a
-- number from 0 to 1 under each clue) and where the guess ranks among all the
-- words it scores ("Ranked #150 of 30,000 words"). The admin logs both when
-- shown: they are the game's own closeness, to fit the solver's model to.
--
-- Run this BEFORE deploying the code that writes these columns. Existing rows
-- get NULLs.

SET SESSION lock_wait_timeout = 5;

ALTER TABLE chainle_rounds
    ADD COLUMN closeness VARCHAR(40) NULL,   -- per clue, in clue order, e.g. '0.61 0.12'
    ADD COLUMN game_rank INT NULL,           -- the guess's place in the game's list
    ADD COLUMN rank_of INT NULL;             -- how many words that list holds


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first):
--
-- ALTER TABLE chainle_rounds DROP COLUMN closeness, DROP COLUMN game_rank, DROP COLUMN rank_of;
