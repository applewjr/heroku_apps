-- Guess The Phrase: players' added phrases and the admin's changes to the
-- phrase list (routes/guess_the_phrase.py; mechanics in crowd.py). The "Solved
-- it, and the answer wasn't listed?" box casts a 'missing' vote; two different
-- players typing the same phrase within 30 days adds it. Players can't remove
-- phrases; /guess_the_phrase_admin can do both. Same four-table shape as
-- smush_crowd.sql + crowd_vote_log.sql, with wider phrase and puzzle columns.
--
-- Run this BEFORE deploying the code that uses it. Four new tables, nothing
-- existing is touched, so it is safe to run live and the current code never
-- notices. Prod and staging share the JawsDB; both are fine.
--
-- Code deployed without these tables fails safe: /guess_the_phrase serves the
-- shipped phrase list on its own (retrying the corrections every 5 minutes),
-- added phrases are lost behind a `gtp_vote_failed` alert, and
-- /guess_the_phrase_admin errors until they exist.

SET SESSION lock_wait_timeout = 5;

-- One row per (phrase, kind of vote, player). A "player" is an HMAC of the
-- visitor's IP (crowd.py CrowdList.voter_hash), never the IP itself. The
-- phrase is its match key: letters and single spaces, upper case.
CREATE TABLE gtp_word_votes (
    id          INT          NOT NULL AUTO_INCREMENT,
    word        VARCHAR(80)  NOT NULL,
    vote        VARCHAR(16)  NOT NULL,   -- 'missing' (players); 'invalid' is admin-only
    voter_hash  CHAR(32)     NOT NULL,
    puzzle      VARCHAR(64)  NULL,       -- category:word lengths, e.g. 'song:4-1-7-5'
    created_at  DATETIME     NOT NULL,   -- Pacific time, stamped in Python
    PRIMARY KEY (id),
    UNIQUE KEY uq_word_vote_voter (word, vote, voter_hash),
    KEY idx_vote_created (vote, created_at),
    KEY idx_voter_created (voter_hash, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Phrases kept out of the solver's answers, whether shipped or added.
-- source: 'admin' (/guess_the_phrase_admin, never reversed by players) or 'crowd'.
CREATE TABLE gtp_invalid_words (
    id          INT          NOT NULL AUTO_INCREMENT,
    word        VARCHAR(80)  NOT NULL,
    added_date  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    source      VARCHAR(16)  NOT NULL DEFAULT 'admin',
    PRIMARY KEY (id),
    UNIQUE KEY uq_word (word)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Phrases added to the solver's answers. Same shape and sources.
CREATE TABLE gtp_added_words (
    id          INT          NOT NULL AUTO_INCREMENT,
    word        VARCHAR(80)  NOT NULL,
    added_date  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    source      VARCHAR(16)  NOT NULL DEFAULT 'admin',
    PRIMARY KEY (id),
    UNIQUE KEY uq_word (word)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Append-only history of every vote and every list change, for calibrating
-- the threshold and window later. Same events as crowd_vote_log.sql's tables
-- (vote, repeat_vote, capped, retract, crowd_applied, crowd_reverted,
-- admin_set, admin_remove). Nothing on the live path reads it.
CREATE TABLE gtp_vote_log (
    id          INT           NOT NULL AUTO_INCREMENT,
    event       VARCHAR(16)   NOT NULL,
    word        VARCHAR(80)   NOT NULL,
    vote        VARCHAR(16)   NULL,      -- 'missing' or 'invalid'
    voter_hash  CHAR(32)      NULL,      -- NULL for admin and crowd events
    puzzle      VARCHAR(64)   NULL,      -- category:word lengths, e.g. 'song:4-1-7-5'
    players     SMALLINT      NULL,      -- counted players for (word, vote) right after the event
    needed      TINYINT       NULL,      -- players that change needed at that moment
    popularity  DECIMAL(4,2)  NULL,      -- always NULL here: phrases have no word popularity
    mode        VARCHAR(16)   NULL,      -- the category the player chose, e.g. 'song'
    detail      VARCHAR(32)   NULL,      -- votes an admin or crowd change cleared
    created_at  DATETIME      NOT NULL,  -- Pacific time, stamped in Python
    PRIMARY KEY (id),
    KEY idx_word (word),
    KEY idx_created (created_at),
    KEY idx_event_created (event, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads these):
--
-- DROP TABLE gtp_vote_log;
-- DROP TABLE gtp_added_words;
-- DROP TABLE gtp_invalid_words;
-- DROP TABLE gtp_word_votes;
