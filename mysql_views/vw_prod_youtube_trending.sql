CREATE OR REPLACE VIEW vw_prod_youtube_trending AS

SELECT
 collected_dt
,collected_date
,count(1) cnt
FROM youtube_trending
GROUP BY collected_dt, collected_date
ORDER BY collected_dt DESC
LIMIT 10
;
