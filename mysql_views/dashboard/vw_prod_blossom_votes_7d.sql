CREATE OR REPLACE VIEW vw_prod_blossom_votes_7d AS

WITH per_day AS (
    SELECT
     voter_hash
    ,DATE(created_at) AS day
    ,COUNT(*) AS ticks
    FROM blossom_word_votes
    WHERE vote = 'invalid'
    GROUP BY voter_hash, DATE(created_at)
    )
SELECT
 v.word
,v.vote
,COUNT(*) AS players_7d
,SUM(CASE WHEN v.vote = 'invalid' AND p.ticks > 10 THEN 0 ELSE 1 END) AS counted
,MAX(v.created_at) AS last_vote
FROM blossom_word_votes AS v
LEFT JOIN per_day AS p ON p.voter_hash = v.voter_hash AND p.day = DATE(v.created_at)
WHERE v.created_at >= pst_now() - INTERVAL 7 DAY
GROUP BY v.word, v.vote
ORDER BY last_vote DESC;
