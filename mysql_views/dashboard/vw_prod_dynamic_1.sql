CREATE OR REPLACE VIEW vw_prod_dynamic_1 AS

SELECT
DATE(created_at) AS day,
SUM(mode = 'ice') AS ice,
SUM(mode = 'all8') AS all8,
SUM(mode = 'pts') AS pts,
SUM(mode = 'eff') AS eff,
SUM(mode = 'pan') AS pan,
COUNT(*) AS total
FROM smush_vote_log
WHERE created_at >= CURDATE() - INTERVAL 9 DAY
	AND voter_hash IS NOT NULL
GROUP BY DATE(created_at)
ORDER BY day DESC
;