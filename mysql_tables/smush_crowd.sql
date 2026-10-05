-- Smush crowd word corrections: the ✗ on a result ("Smush didn't accept this
-- word") and the "Smush accepted a word that isn't listed?" box
-- (routes/smush.py, "crowd corrections"; mechanics in crowd.py). Smush's own
-- tables: Blossom's corrections no longer reach Smush.
--
-- Run this BEFORE deploying the code that uses it. Three new tables, nothing
-- existing is touched, so it is safe to run live and the current code never
-- notices. Prod and staging share the JawsDB; both are fine.
--
-- Code deployed without these tables fails safe: /smush serves the raw word
-- list (retrying every 5 minutes) and votes are lost behind a
-- `smush_vote_failed` alert. /smush_admin would error until they exist.

SET SESSION lock_wait_timeout = 5;

-- One row per (word, kind of vote, player). A "player" is an HMAC of the
-- visitor's IP (crowd.py CrowdList.voter_hash), never the IP itself.
CREATE TABLE smush_word_votes (
    id          INT          NOT NULL AUTO_INCREMENT,
    word        VARCHAR(50)  NOT NULL,
    vote        VARCHAR(16)  NOT NULL,   -- 'invalid' or 'missing'
    voter_hash  CHAR(32)     NOT NULL,
    puzzle      VARCHAR(10)  NULL,       -- center:sorted outer 8, e.g. 'l:acefgmou'
    created_at  DATETIME     NOT NULL,   -- Pacific time, stamped in Python
    PRIMARY KEY (id),
    UNIQUE KEY uq_word_vote_voter (word, vote, voter_hash),
    KEY idx_vote_created (vote, created_at),
    KEY idx_voter_created (voter_hash, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Words taken out of Smush's list. source: 'admin' (/smush_admin, never
-- reversed by players) or 'crowd'.
CREATE TABLE smush_invalid_words (
    id          INT          NOT NULL AUTO_INCREMENT,
    word        VARCHAR(50)  NOT NULL,
    added_date  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    source      VARCHAR(16)  NOT NULL DEFAULT 'admin',
    PRIMARY KEY (id),
    UNIQUE KEY uq_word (word)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Words put into Smush's list. Same shape and sources.
CREATE TABLE smush_added_words (
    id          INT          NOT NULL AUTO_INCREMENT,
    word        VARCHAR(50)  NOT NULL,
    added_date  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    source      VARCHAR(16)  NOT NULL DEFAULT 'admin',
    PRIMARY KEY (id),
    UNIQUE KEY uq_word (word)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads these):
--
-- DROP TABLE smush_added_words;
-- DROP TABLE smush_invalid_words;
-- DROP TABLE smush_word_votes;
