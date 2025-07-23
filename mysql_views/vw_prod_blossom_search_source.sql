CREATE OR REPLACE VIEW vw_prod_blossom_search_source AS

WITH counts AS (
    SELECT
    referrer
    ,ROUND(COUNT(CASE WHEN submit_time BETWEEN DATE_SUB(pst_now(), INTERVAL 28*24 HOUR) AND DATE_SUB(pst_now(), INTERVAL 22*24 HOUR) THEN 1 END) / 7.0, 0) AS cnt_28
    ,ROUND(COUNT(CASE WHEN submit_time BETWEEN DATE_SUB(pst_now(), INTERVAL 21*24 HOUR) AND DATE_SUB(pst_now(), INTERVAL 15*24 HOUR) THEN 1 END) / 7.0, 0) AS cnt_21
    ,ROUND(COUNT(CASE WHEN submit_time BETWEEN DATE_SUB(pst_now(), INTERVAL 14*24 HOUR) AND DATE_SUB(pst_now(), INTERVAL 8*24 HOUR) THEN 1 END) / 7.0, 0) AS cnt_14
    ,ROUND(COUNT(CASE WHEN submit_time BETWEEN DATE_SUB(pst_now(), INTERVAL 7*24 HOUR) AND pst_now() THEN 1 END) / 7.0, 0) AS cnt_7
    ,COUNT(CASE WHEN submit_time >= DATE_SUB(pst_now(), INTERVAL 24 HOUR) THEN 1 END) AS cnt_1
    FROM app_visits
    WHERE page_name = 'blossom.html'
        AND submit_time >= DATE_SUB(pst_now(), INTERVAL 28*24 HOUR)
        AND referrer not like '%blossom%'
        AND referrer not like '%127.0.0.1:5000%'
        AND referrer not like '%jamesapplewhite%'
        AND referrer not like '%apple-apps-staging%'
    GROUP BY referrer
    HAVING cnt_28 >= 1 OR cnt_21 >= 1 OR cnt_14 >= 1 OR cnt_7 >= 1 OR cnt_1 >= 10
    )
,minmax AS (
    SELECT
    MIN(cnt_28) AS min_28, MAX(cnt_28) AS max_28
    ,MIN(cnt_21) AS min_21, MAX(cnt_21) AS max_21
    ,MIN(cnt_14) AS min_14, MAX(cnt_14) AS max_14
    ,MIN(cnt_7) AS min_7, MAX(cnt_7) AS max_7
    ,MIN(cnt_1) AS min_1, MAX(cnt_1) AS max_1
    FROM counts
    )
SELECT
counts.referrer
,counts.cnt_28
,counts.cnt_21
,counts.cnt_14
,counts.cnt_7
,counts.cnt_1
,ROUND((counts.cnt_28 - m.min_28) / NULLIF(m.max_28 - m.min_28, 0),2) AS minmax_28
,ROUND((counts.cnt_21 - m.min_21) / NULLIF(m.max_21 - m.min_21, 0),2) AS minmax_21
,ROUND((counts.cnt_14 - m.min_14) / NULLIF(m.max_14 - m.min_14, 0),2) AS minmax_14
,ROUND((counts.cnt_7 - m.min_7) / NULLIF(m.max_7 - m.min_7, 0),2) AS minmax_7
,ROUND((counts.cnt_1 - m.min_1) / NULLIF(m.max_1 - m.min_1, 0),2) AS minmax_1
FROM counts AS counts
,minmax AS m
ORDER BY counts.cnt_28 DESC;
