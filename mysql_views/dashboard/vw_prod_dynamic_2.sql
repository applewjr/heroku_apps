CREATE OR REPLACE VIEW vw_prod_dynamic_2 AS

-- Crowd word changes, last 9 days, newest first: every word the crowd put on
-- or took off a list, and every change it undid, in any game. Nobody reviews
-- these before they go live, so this is the list to check from a phone. Undo
-- a bad one on that game's _admin page. players is how many counted players
-- had voted at that moment, needed how many the change took.

SELECT game, created_at, event, word, vote, players, needed
FROM (
    SELECT 'smush' AS game, created_at, event, word, vote, players, needed
    FROM smush_vote_log
    WHERE created_at >= DATE(pst_now()) - INTERVAL 9 DAY AND event IN ('crowd_applied', 'crowd_reverted')
    UNION ALL
    SELECT 'blossom', created_at, event, word, vote, players, needed
    FROM blossom_vote_log
    WHERE created_at >= DATE(pst_now()) - INTERVAL 9 DAY AND event IN ('crowd_applied', 'crowd_reverted')
    UNION ALL
    SELECT 'ribbit', created_at, event, word, vote, players, needed
    FROM ribbit_vote_log
    WHERE created_at >= DATE(pst_now()) - INTERVAL 9 DAY AND event IN ('crowd_applied', 'crowd_reverted')
    UNION ALL
    SELECT 'gtp', created_at, event, word, vote, players, needed
    FROM gtp_vote_log
    WHERE created_at >= DATE(pst_now()) - INTERVAL 9 DAY AND event IN ('crowd_applied', 'crowd_reverted')
    UNION ALL
    SELECT 'chainle', created_at, event, word, vote, players, needed
    FROM chainle_vote_log
    WHERE created_at >= DATE(pst_now()) - INTERVAL 9 DAY AND event IN ('crowd_applied', 'crowd_reverted')
    ) AS changes
ORDER BY created_at DESC
LIMIT 50
;
