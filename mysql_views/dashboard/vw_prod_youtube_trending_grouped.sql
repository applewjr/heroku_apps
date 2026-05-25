CREATE OR REPLACE VIEW vw_prod_youtube_trending_grouped AS

SELECT
 MIN(collected_dt) AS min_collected_dt
,collected_date
,count(1) cnt
FROM youtube_trending_revamp
GROUP BY collected_date
ORDER BY collected_dt DESC
LIMIT 10
;
