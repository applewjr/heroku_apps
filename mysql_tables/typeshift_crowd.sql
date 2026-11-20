-- Typeshift crowd word corrections: the ✗ on a result ("Typeshift refused
-- this word") and the "Typeshift accepted a word that isn't listed?" box
-- (routes/typeshift.py, "word reports"; mechanics in crowd.py). Typeshift's
-- own tables: no other game's corrections reach Typeshift, and these reach
-- no other game. Same four-table shape as ribbit_crowd.sql.
--
-- Run this BEFORE deploying the code that uses it, with typeshift_boards.sql.
-- Four new tables, nothing existing is touched, so it is safe to run live
-- and the current code never notices. Prod and staging share the JawsDB;
-- both are fine.
--
-- Code deployed without these tables fails safe: /typeshift serves the raw
-- word list (retrying every 5 minutes), votes are lost behind a
-- `typeshift_vote_failed` alert, and /typeshift_admin's word tabs show the
-- error (its Today's Boards tab still works).

SET SESSION lock_wait_timeout = 5;

-- One row per (word, kind of vote, player). A "player" is an HMAC of the
-- visitor's IP (crowd.player_hash), never the IP itself.
CREATE TABLE typeshift_word_votes (
    id          INT           NOT NULL AUTO_INCREMENT,
    word        VARCHAR(50)   NOT NULL,
    vote        VARCHAR(16)   NOT NULL,   -- 'invalid' or 'missing'
    voter_hash  CHAR(32)      NOT NULL,
    puzzle      VARCHAR(120)  NULL,       -- the board: each column's letters sorted, joined by ':', e.g. 'abs:eio:lnr'
    created_at  DATETIME      NOT NULL,   -- Pacific time, stamped in Python
    PRIMARY KEY (id),
    UNIQUE KEY uq_word_vote_voter (word, vote, voter_hash),
    KEY idx_vote_created (vote, created_at),
    KEY idx_voter_created (voter_hash, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Words taken out of Typeshift's list. source: 'admin' (/typeshift_admin,
-- never reversed by players) or 'crowd'.
CREATE TABLE typeshift_invalid_words (
    id          INT          NOT NULL AUTO_INCREMENT,
    word        VARCHAR(50)  NOT NULL,
    added_date  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    source      VARCHAR(16)  NOT NULL DEFAULT 'admin',
    PRIMARY KEY (id),
    UNIQUE KEY uq_word (word)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Words put into Typeshift's list. Same shape and sources.
CREATE TABLE typeshift_added_words (
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
CREATE TABLE typeshift_vote_log (
    id          INT           NOT NULL AUTO_INCREMENT,
    event       VARCHAR(16)   NOT NULL,
    word        VARCHAR(50)   NOT NULL,
    vote        VARCHAR(16)   NULL,      -- 'invalid' or 'missing'
    voter_hash  CHAR(32)      NULL,      -- NULL for admin and crowd events
    puzzle      VARCHAR(120)  NULL,      -- as in typeshift_word_votes
    players     SMALLINT      NULL,      -- counted players for (word, vote) right after the event
    needed      TINYINT       NULL,      -- players that change needed at that moment
    popularity  DECIMAL(4,2)  NULL,      -- data.word_pop (Zipf) when it happened
    mode        VARCHAR(16)   NULL,      -- the page's mode the player voted from: 'help' or 'solve'
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
-- DROP TABLE typeshift_vote_log;
-- DROP TABLE typeshift_added_words;
-- DROP TABLE typeshift_invalid_words;
-- DROP TABLE typeshift_word_votes;
