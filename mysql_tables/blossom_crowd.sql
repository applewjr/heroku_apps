-- Blossom crowd word corrections: one-tap "Invalid" flags and the
-- "Blossom accepted a word that isn't listed?" box (routes/blossom.py,
-- "crowd corrections" section).
--
-- Run this BEFORE deploying the code that uses it. The current code keeps
-- working afterwards: every existing INSERT and SELECT on these tables names
-- its columns, so the new `source` column simply takes its 'admin' default.
-- Prod and staging share the JawsDB; the change is additive, so both are fine.
--
-- Safe to run live. JawsDB is MySQL 8.4.8, where ADD COLUMN ... ALGORITHM=INSTANT
-- only rewrites table metadata: no copy, no rebuild, milliseconds. Naming the
-- algorithm makes MySQL refuse rather than silently fall back to a rebuild.
-- An INSTANT ALTER still needs a brief metadata lock; the timeout below makes
-- it give up after 5 seconds instead of queueing other queries behind a long
-- one (the server default is a year). If a statement times out, nothing
-- changed: rerun just that statement in the same Workbench tab. Rerunning the
-- whole file would stop at CREATE TABLE, which already succeeded.

SET SESSION lock_wait_timeout = 5;

-- One row per (word, kind of vote, player). A "player" is an HMAC of the
-- visitor's IP (routes/blossom.py voter_hash), never the IP itself.
CREATE TABLE blossom_word_votes (
    id          INT          NOT NULL AUTO_INCREMENT,
    word        VARCHAR(50)  NOT NULL,
    vote        VARCHAR(16)  NOT NULL,   -- 'invalid' or 'missing'
    voter_hash  CHAR(32)     NOT NULL,
    puzzle      VARCHAR(8)   NULL,       -- center:sorted petals, e.g. 'f:egilnu'
    created_at  DATETIME     NOT NULL,   -- Pacific time, stamped in Python
    PRIMARY KEY (id),
    UNIQUE KEY uq_word_vote_voter (word, vote, voter_hash),
    KEY idx_vote_created (vote, created_at),
    KEY idx_voter_created (voter_hash, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Who decided: 'admin' (/blossom_admin) or 'crowd'. Existing rows become
-- 'admin', which is right - every one of them was added by hand - and admin
-- rows are never reversed by the crowd.
ALTER TABLE blossom_invalid_words
    ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'admin',
    ALGORITHM=INSTANT;

ALTER TABLE blossom_added_words
    ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'admin',
    ALGORITHM=INSTANT;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads these):
--
-- SET SESSION lock_wait_timeout = 5;
-- ALTER TABLE blossom_added_words   DROP COLUMN source, ALGORITHM=INSTANT;
-- ALTER TABLE blossom_invalid_words DROP COLUMN source, ALGORITHM=INSTANT;
-- DROP TABLE blossom_word_votes;
