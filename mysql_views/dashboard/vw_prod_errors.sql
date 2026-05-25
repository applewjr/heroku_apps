CREATE OR REPLACE VIEW vw_prod_errors AS

SELECT
 av.submit_time
,av.page_name
,av.referrer
FROM app_visits AS av
where page_name LIKE 'error.html%'
    AND submit_time >= CONVERT_TZ(NOW() - INTERVAL 48 HOUR, 'UTC', 'America/Los_Angeles')
    AND referrer NOT LIKE '%127.0.0.1:5000%'
ORDER BY submit_time DESC
LIMIT 25
;
