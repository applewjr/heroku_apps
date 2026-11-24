-- Wordiply crowd word corrections: the ✗ on a result ("Wordiply refused it")
-- and /wordiply_admin's Remove and Add (routes/wordiply.py, "word
-- corrections"; mechanics in crowd.py). Wordiply's own tables: no other
-- game's corrections reach Wordiply, and these reach no other game. Same
-- four-table shape as ribbit_crowd.sql. Players can only report a word
-- Wordiply refused; there is no box for missing words.
--
-- Run this BEFORE deploying the code that uses it. Four new tables, nothing
-- existing is touched, so it is safe to run live and the current code never
-- notices. Prod and staging share the JawsDB; both are fine.
--
-- Code deployed without these tables fails safe: /wordiply serves the raw
-- word list (retrying every 5 minutes), votes are lost behind a
-- `wordiply_vote_failed` alert, and /wordiply_admin's word tabs show the
-- error (its Today's Starter tab still works).

SET SESSION lock_wait_timeout = 5;

-- One row per (word, kind of vote, player). A "player" is an HMAC of the
-- visitor's IP (crowd.player_hash), never the IP itself.
CREATE TABLE wordiply_word_votes (
    id          INT          NOT NULL AUTO_INCREMENT,
    word        VARCHAR(50)  NOT NULL,
    vote        VARCHAR(16)  NOT NULL,   -- 'invalid' (players); 'missing' is admin-only
    voter_hash  CHAR(32)     NOT NULL,
    puzzle      VARCHAR(24)  NULL,       -- the starter it was reported on, e.g. 'starter:row'
    created_at  DATETIME     NOT NULL,   -- Pacific time, stamped in Python
    PRIMARY KEY (id),
    UNIQUE KEY uq_word_vote_voter (word, vote, voter_hash),
    KEY idx_vote_created (vote, created_at),
    KEY idx_voter_created (voter_hash, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Words taken out of Wordiply's list. source: 'admin' (/wordiply_admin, never
-- reversed by players) or 'crowd'.
CREATE TABLE wordiply_invalid_words (
    id          INT          NOT NULL AUTO_INCREMENT,
    word        VARCHAR(50)  NOT NULL,
    added_date  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    source      VARCHAR(16)  NOT NULL DEFAULT 'admin',
    PRIMARY KEY (id),
    UNIQUE KEY uq_word (word)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Words put into Wordiply's list. Same shape and sources.
CREATE TABLE wordiply_added_words (
    id          INT          NOT NULL AUTO_INCREMENT,
    word        VARCHAR(50)  NOT NULL,
    added_date  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    source      VARCHAR(16)  NOT NULL DEFAULT 'admin',
    PRIMARY KEY (id),
    UNIQUE KEY uq_word (word)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Append-only history of every vote and every list change, for calibrating
-- the thresholds later. Same events as crowd_vote_log.sql's tables (vote,
-- repeat_vote, capped, retract, crowd_applied, crowd_reverted, admin_set,
-- admin_remove). Nothing on the live path reads it.
CREATE TABLE wordiply_vote_log (
    id          INT           NOT NULL AUTO_INCREMENT,
    event       VARCHAR(16)   NOT NULL,
    word        VARCHAR(50)   NOT NULL,
    vote        VARCHAR(16)   NULL,      -- 'invalid' or 'missing'
    voter_hash  CHAR(32)      NULL,      -- NULL for admin and crowd events
    puzzle      VARCHAR(24)   NULL,      -- as in wordiply_word_votes
    players     SMALLINT      NULL,      -- counted players for (word, vote) right after the event
    needed      TINYINT       NULL,      -- players that change needed at that moment
    popularity  DECIMAL(4,2)  NULL,      -- data.word_pop (Zipf) when it happened
    mode        VARCHAR(16)   NULL,      -- always NULL here: the page has one list
    detail      VARCHAR(32)   NULL,      -- votes an admin or crowd change cleared, 'crossing_off'
    created_at  DATETIME      NOT NULL,  -- Pacific time, stamped in Python
    PRIMARY KEY (id),
    KEY idx_word (word),
    KEY idx_created (created_at),
    KEY idx_event_created (event, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads these):
--
-- DROP TABLE wordiply_vote_log;
-- DROP TABLE wordiply_added_words;
-- DROP TABLE wordiply_invalid_words;
-- DROP TABLE wordiply_word_votes;
