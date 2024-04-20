CREATE OR REPLACE VIEW vw_prod_blossom_search_source AS

WITH counts AS (
    SELECT
    referrer
    ,COUNT(*) AS cnt_56
    ,COUNT(CASE WHEN DATE(submit_time) BETWEEN DATE_SUB(CURRENT_DATE, INTERVAL 28 DAY) AND CURRENT_DATE THEN 1 END) AS cnt_28
    ,COUNT(CASE WHEN DATE(submit_time) BETWEEN DATE_SUB(CURRENT_DATE, INTERVAL 14 DAY) AND CURRENT_DATE THEN 1 END) AS cnt_14
    ,COUNT(CASE WHEN DATE(submit_time) BETWEEN DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY) AND CURRENT_DATE THEN 1 END) AS cnt_7
    ,COUNT(CASE WHEN DATE(submit_time) BETWEEN DATE_SUB(CURRENT_DATE, INTERVAL 1 DAY) AND CURRENT_DATE THEN 1 END) AS cnt_1
    FROM app_visits
    WHERE page_name = 'blossom.html'
        AND DATE(submit_time) BETWEEN DATE_SUB(CURRENT_DATE, INTERVAL 56 DAY) AND CURRENT_DATE
        AND referrer not like '%blossom%'
        AND referrer not like '%127.0.0.1:5000%'
        AND referrer not like '%jamesapplewhite%'
    GROUP BY referrer
    HAVING cnt_56 >= 10
    )
,minmax AS (
    SELECT
    MIN(cnt_56) AS min_56, MAX(cnt_56) AS max_56
    ,MIN(cnt_28) AS min_28, MAX(cnt_28) AS max_28
    ,MIN(cnt_14) AS min_14, MAX(cnt_14) AS max_14
    ,MIN(cnt_7) AS min_7, MAX(cnt_7) AS max_7
    ,MIN(cnt_1) AS min_1, MAX(cnt_1) AS max_1
    FROM counts
    )
SELECT
counts.referrer
,counts.cnt_56
,ROUND((counts.cnt_56 - m.min_56) / NULLIF(m.max_56 - m.min_56, 0),2) AS minmax_56
,counts.cnt_28
,ROUND((counts.cnt_28 - m.min_28) / NULLIF(m.max_28 - m.min_28, 0),2) AS minmax_28
,counts.cnt_14
,ROUND((counts.cnt_14 - m.min_14) / NULLIF(m.max_14 - m.min_14, 0),2) AS minmax_14
,counts.cnt_7
,ROUND((counts.cnt_7 - m.min_7) / NULLIF(m.max_7 - m.min_7, 0),2) AS minmax_7
,counts.cnt_1
,ROUND((counts.cnt_1 - m.min_1) / NULLIF(m.max_1 - m.min_1, 0),2) AS minmax_1
FROM counts AS counts
,minmax AS m
ORDER BY counts.cnt_56 DESC
;
