CREATE OR REPLACE VIEW vw_prod_errors_by_day AS

-- Error pages per day, last 14 days, newest first; today is partial.
-- server_errors are 500s and should read 0. not_found are 404s, nearly all of
-- them scanners probing for files the site doesn't have, so only a sudden
-- jump matters. Local runs are left out.

WITH days AS (
    SELECT DISTINCT DATE(submit_time) AS date
    FROM app_visits
    WHERE submit_time >= DATE(pst_now()) - INTERVAL 13 DAY
    )
,errors AS (
    SELECT DATE(submit_time) AS date, page_name
    FROM app_visits
    WHERE page_name LIKE 'error.html%'
        AND submit_time >= DATE(pst_now()) - INTERVAL 13 DAY
        AND referrer NOT LIKE '%127.0.0.1:5000%'
    )
SELECT
 d.date
,COALESCE(SUM(e.page_name NOT LIKE 'error.html (404:%'), 0) AS server_errors
,COALESCE(SUM(e.page_name LIKE 'error.html (404:%'), 0) AS not_found
FROM days AS d
LEFT JOIN errors AS e ON e.date = d.date
GROUP BY d.date
ORDER BY d.date DESC
;
