CREATE OR REPLACE VIEW vw_prod_blossom_errors AS

SELECT
 all_dates.date AS Date
,COUNT(av.submit_time) AS Count
FROM (
    SELECT DISTINCT DATE(submit_time) AS date
    FROM ndsvta8po4bdiw50.app_visits
    WHERE DATE(submit_time) <= CURDATE()
	) AS all_dates
LEFT JOIN ndsvta8po4bdiw50.app_visits AS av ON DATE(av.submit_time) = all_dates.date
	AND av.page_name LIKE '%error%'
    AND av.referrer LIKE '%blossom%'
GROUP BY all_dates.date
ORDER BY all_dates.date DESC
LIMIT 10
;
