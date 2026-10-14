-- Chainle round log, second change (after chainle_rounds.sql): unlimited games.
--
-- The admin plays unlimited puzzles as well as the daily one, so a row's date
-- is now the day it was played, and several puzzles can share a date:
--   1. a `mode` column: 'daily' or 'unlimited';
--   2. the unique key gains the clues, so a different puzzle's round adds a
--      row, and saving the same round again still replaces it (a correction).
--
-- Run this BEFORE deploying the code that writes `mode`. The table is tiny, so
-- both statements finish at once. Existing rows become 'daily'.

SET SESSION lock_wait_timeout = 5;

ALTER TABLE chainle_rounds
    ADD COLUMN mode VARCHAR(16) NOT NULL DEFAULT 'daily';

ALTER TABLE chainle_rounds
    DROP INDEX uq_round,
    ADD UNIQUE KEY uq_round (played_on, site, round_no, clues);


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first). The old key fails to come back
-- once two puzzles share a date and round number; delete or merge those first.
--
-- ALTER TABLE chainle_rounds DROP INDEX uq_round, ADD UNIQUE KEY uq_round (played_on, site, round_no);
-- ALTER TABLE chainle_rounds DROP COLUMN mode;
