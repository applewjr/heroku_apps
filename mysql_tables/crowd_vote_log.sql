-- Crowd vote log: a permanent, append-only history of every crowd vote and
-- every word-list decision for /smush and /blossom (crowd.py CrowdList._log).
-- <game>_word_votes is live state, not history: admin actions, crowd changes,
-- un-ticks and repeat votes delete or overwrite its rows, and votes refused by
-- the daily cap are never stored there. This keeps all of it, for calibrating
-- the vote thresholds. Nothing on the live path reads it.
--
-- Run this BEFORE deploying the code that uses it. Two new tables, nothing
-- existing is touched, so it is safe to run live and the current code never
-- notices. Prod and staging share the JawsDB; both are fine.
--
-- Code deployed without these tables fails safe: votes and admin actions work
-- as before, and the log rows are lost behind a `<game>_vote_log_failed` alert.

SET SESSION lock_wait_timeout = 5;

-- One row per event. event is one of:
--   vote           a player's first vote on (word, vote)
--   repeat_vote    the same player voting the same way again (refreshes the live row)
--   capped         refused by CROWD_MAX_VOTES_PER_DAY; not in smush_word_votes
--   retract        the player took their vote back
--   crowd_applied  enough players agreed: the word moved lists
--   crowd_reverted a crowd change undone (detail 'crossing_off' when a player
--                  passed CROWD_MAX_INVALID_PER_DAY)
--   admin_set      James's own call from /smush_admin; vote = which list
--   admin_remove   James took the word out of that list
--   backfill       copied from smush_word_votes by the optional block below
CREATE TABLE smush_vote_log (
    id          INT           NOT NULL AUTO_INCREMENT,
    event       VARCHAR(16)   NOT NULL,
    word        VARCHAR(50)   NOT NULL,
    vote        VARCHAR(16)   NULL,      -- 'invalid' or 'missing'
    voter_hash  CHAR(32)      NULL,      -- NULL for admin and crowd events
    puzzle      VARCHAR(10)   NULL,      -- center:sorted outer 8, e.g. 'l:acefgmou'
    players     SMALLINT      NULL,      -- counted players for (word, vote) right after the event
    needed      TINYINT       NULL,      -- players that change needed at that moment
    popularity  DECIMAL(4,2)  NULL,      -- data.word_pop (Zipf) when it happened
    mode        VARCHAR(16)   NULL,      -- results tab the player was on: 'pts', 'eff', 'all8',
                                         -- 'pan'; 'dev_<tab>' or 'dev_<tab>_ice' from /smush_dev
    detail      VARCHAR(32)   NULL,      -- votes an admin or crowd change cleared, 'crossing_off'
    created_at  DATETIME      NOT NULL,  -- Pacific time, stamped in Python
    PRIMARY KEY (id),
    KEY idx_word (word),
    KEY idx_created (created_at),
    KEY idx_event_created (event, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Blossom's. Same shape and events; puzzle is center:sorted petals, and mode
-- is 'normal' or 'helper' (the page's Helper Mode).
CREATE TABLE blossom_vote_log (
    id          INT           NOT NULL AUTO_INCREMENT,
    event       VARCHAR(16)   NOT NULL,
    word        VARCHAR(50)   NOT NULL,
    vote        VARCHAR(16)   NULL,
    voter_hash  CHAR(32)      NULL,
    puzzle      VARCHAR(10)   NULL,
    players     SMALLINT      NULL,
    needed      TINYINT       NULL,
    popularity  DECIMAL(4,2)  NULL,
    mode        VARCHAR(16)   NULL,
    detail      VARCHAR(32)   NULL,
    created_at  DATETIME      NOT NULL,
    PRIMARY KEY (id),
    KEY idx_word (word),
    KEY idx_created (created_at),
    KEY idx_event_created (event, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------------
-- Optional backfill: copy the votes that survive in the live tables, so they
-- are not lost. Run it straight after the CREATE TABLEs, BEFORE deploying:
-- run later, it would copy votes the new code has already logged a second
-- time. created_at is the vote's latest repeat, not the first one (repeats
-- overwrite it), and mode is unknown.
--
-- READ COMMITTED makes INSERT ... SELECT read a snapshot of the vote tables
-- instead of locking their rows, so a vote cast at that moment never waits.
--
-- SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED;
-- INSERT INTO smush_vote_log (event, word, vote, voter_hash, puzzle, created_at)
--     SELECT 'backfill', word, vote, voter_hash, puzzle, created_at
--     FROM smush_word_votes ORDER BY created_at, id;
-- INSERT INTO blossom_vote_log (event, word, vote, voter_hash, puzzle, created_at)
--     SELECT 'backfill', word, vote, voter_hash, puzzle, created_at
--     FROM blossom_word_votes ORDER BY created_at, id;
-- COMMIT;


-- ---------------------------------------------------------------------------
-- Rollback (deploy the previous code first; it never reads these):
--
-- DROP TABLE smush_vote_log;
-- DROP TABLE blossom_vote_log;
