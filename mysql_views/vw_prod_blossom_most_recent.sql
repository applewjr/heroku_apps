CREATE OR REPLACE VIEW vw_prod_blossom_most_recent AS

WITH recent_clicks AS (
    SELECT *
    FROM blossom_solver_clicks
    WHERE click_time <= CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')
    ORDER BY click_time DESC
    LIMIT 26
    )
,lag_computation AS (
    SELECT
     id
    ,click_time
    ,ROUND(TIMESTAMPDIFF(SECOND, LAG(click_time) OVER (ORDER BY click_time), click_time)/60,2) AS lag_minute
    ,must_have
    ,may_have
    ,petal_letter
    ,list_len
    FROM recent_clicks
    )
SELECT
 '-' AS id
,CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles') AS click_time
,ROUND(TIMESTAMPDIFF(SECOND, MAX(click_time), CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'))/60,2) AS lag_minute
,'-' AS must_have
,'-' AS may_have
,'-' AS petal_letter
,'-' AS list_len
FROM recent_clicks
UNION ALL
SELECT * FROM lag_computation
ORDER BY click_time DESC, id DESC
LIMIT 26;
