CREATE OR REPLACE VIEW vw_prod_blossom_most_recent AS

SELECT
 '-' AS id
,CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles') AS click_time
,TIMESTAMPDIFF(MINUTE, MAX(click_time), CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS lag_minute
,'-' AS must_have
,'-' AS may_have
,'-' AS petal_letter
,'-' AS list_len
FROM blossom_solver_clicks
UNION ALL
SELECT
 id
,click_time
,TIMESTAMPDIFF(MINUTE, LAG(click_time) OVER (ORDER BY click_time), click_time) AS lag_minute
,must_have
,may_have
,petal_letter
,list_len
FROM blossom_solver_clicks
ORDER BY click_time DESC, id DESC
LIMIT 26
;
