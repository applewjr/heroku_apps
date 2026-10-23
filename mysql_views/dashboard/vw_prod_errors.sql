CREATE OR REPLACE VIEW vw_prod_errors AS

-- The latest server errors (500s) from the last 7 days, newest first. Empty
-- is good. 404s are left out: nearly all of them are scanners probing for
-- files the site doesn't have, and they buried any real error in this list.
-- vw_prod_errors_by_day still counts them.

SELECT
 av.submit_time
,av.page_name
,av.referrer
FROM app_visits AS av
WHERE av.page_name LIKE 'error.html%'
    AND av.page_name NOT LIKE 'error.html (404:%'
    AND av.submit_time >= pst_now() - INTERVAL 7 DAY
    AND av.referrer NOT LIKE '%127.0.0.1:5000%'
ORDER BY av.submit_time DESC
LIMIT 25
;
