-- Circuits added pairs: compounds James teaches the /circuits solver from
-- /circuits_admin, when his answers need a pair the shipped word data lacks
-- (routes/circuits.py, "James's added pairs"). The solver counts each as a
-- sure compound.
--
-- Run this BEFORE deploying the code that uses it, with circuits_boards.sql.
-- One new table, nothing existing is touched, so it is safe to run live.
--
-- Code deployed without it fails safe: the solver runs on the shipped data
-- alone, reads raise `circuits_pairs_read_failed`, and the admin's Added
-- Pairs tab shows the error.

SET SESSION lock_wait_timeout = 5;

CREATE TABLE circuits_pairs (
    id           INT           NOT NULL AUTO_INCREMENT,
    first_word   VARCHAR(24)   NOT NULL,   -- written first: a word as the data spells it, or a tile's whole text ('show and'), lower case
    second_word  VARCHAR(24)   NOT NULL,   -- written second, the same way
    added_at     DATETIME      NOT NULL,   -- Pacific time, stamped in Python
    PRIMARY KEY (id),
    UNIQUE KEY uq_pair (first_word, second_word)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads this):
--
-- DROP TABLE circuits_pairs;
