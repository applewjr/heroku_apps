CREATE OR REPLACE VIEW vw_prod_dynamic_4 AS

-- Crowd votes, last 9 days, all games together: votes cast, voters (counted
-- per game, so one person voting in two games counts twice), capped (votes
-- refused because a voter hit their daily cap) and top_voter (the most votes
-- one voter cast in one game that day). A jump in capped or top_voter is one
-- person or bot hammering the vote buttons; check that game's crowd changes
-- in Dynamic View 2.

SELECT
 day
,SUM(votes) AS votes
,SUM(voters) AS voters
,SUM(capped) AS capped
,MAX(top_voter) AS top_voter
FROM (
    SELECT game, day, SUM(votes) AS votes, COUNT(*) AS voters, SUM(capped) AS capped, MAX(votes) AS top_voter
    FROM (
        SELECT 'smush' AS game, DATE(created_at) AS day, voter_hash,
               SUM(event = 'vote') AS votes, SUM(event = 'capped') AS capped
        FROM smush_vote_log
        WHERE created_at >= DATE(pst_now()) - INTERVAL 9 DAY AND voter_hash IS NOT NULL
        GROUP BY DATE(created_at), voter_hash
        UNION ALL
        SELECT 'blossom', DATE(created_at), voter_hash, SUM(event = 'vote'), SUM(event = 'capped')
        FROM blossom_vote_log
        WHERE created_at >= DATE(pst_now()) - INTERVAL 9 DAY AND voter_hash IS NOT NULL
        GROUP BY DATE(created_at), voter_hash
        UNION ALL
        SELECT 'ribbit', DATE(created_at), voter_hash, SUM(event = 'vote'), SUM(event = 'capped')
        FROM ribbit_vote_log
        WHERE created_at >= DATE(pst_now()) - INTERVAL 9 DAY AND voter_hash IS NOT NULL
        GROUP BY DATE(created_at), voter_hash
        UNION ALL
        SELECT 'gtp', DATE(created_at), voter_hash, SUM(event = 'vote'), SUM(event = 'capped')
        FROM gtp_vote_log
        WHERE created_at >= DATE(pst_now()) - INTERVAL 9 DAY AND voter_hash IS NOT NULL
        GROUP BY DATE(created_at), voter_hash
        UNION ALL
        SELECT 'chainle', DATE(created_at), voter_hash, SUM(event = 'vote'), SUM(event = 'capped')
        FROM chainle_vote_log
        WHERE created_at >= DATE(pst_now()) - INTERVAL 9 DAY AND voter_hash IS NOT NULL
        GROUP BY DATE(created_at), voter_hash
        ) AS per_voter
    GROUP BY game, day
    ) AS per_game
GROUP BY day
ORDER BY day DESC
;
