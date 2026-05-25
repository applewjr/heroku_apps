CREATE OR REPLACE VIEW vw_prod_blossom_hourly_average AS

SELECT 
    LPAD(HOUR(click_time), 2, '0') AS hour,
    ROUND(SUM(CASE 
        WHEN click_time >= CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 28 DAY 
        AND click_time < CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 21 DAY 
        THEN 1 ELSE 0 END)/7, 2) AS avg_4_week_ago,
    ROUND(SUM(CASE 
        WHEN click_time >= CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 21 DAY 
        AND click_time < CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 14 DAY 
        THEN 1 ELSE 0 END)/7, 2) AS avg_3_week_ago,
    ROUND(SUM(CASE 
        WHEN click_time >= CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 14 DAY 
        AND click_time < CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 7 DAY 
        THEN 1 ELSE 0 END)/7, 2) AS avg_2_week_ago,
    ROUND(SUM(CASE 
        WHEN click_time >= CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 7 DAY 
        AND click_time < CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME)
        THEN 1 ELSE 0 END)/7, 2) AS avg_1_week_ago,
    SUM(CASE 
        WHEN click_time >= CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) 
        AND click_time < CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) + INTERVAL 1 DAY 
        THEN 1 ELSE 0 END) AS today
FROM blossom_solver_clicks
WHERE click_time >= CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 29 DAY
GROUP BY HOUR(click_time)
UNION ALL
SELECT 
    'All' AS hour,
    ROUND(SUM(CASE 
        WHEN click_time >= CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 28 DAY 
        AND click_time < CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 21 DAY 
        THEN 1 ELSE 0 END)/7, 2) AS total_avg_4_week_ago,
    ROUND(SUM(CASE 
        WHEN click_time >= CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 21 DAY 
        AND click_time < CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 14 DAY 
        THEN 1 ELSE 0 END)/7, 2) AS total_avg_3_week_ago,
    ROUND(SUM(CASE 
        WHEN click_time >= CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 14 DAY 
        AND click_time < CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 7 DAY 
        THEN 1 ELSE 0 END)/7, 2) AS total_avg_2_week_ago,
    ROUND(SUM(CASE 
        WHEN click_time >= CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 7 DAY 
        AND click_time < CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME)
        THEN 1 ELSE 0 END)/7, 2) AS total_avg_1_week_ago,
    SUM(CASE 
        WHEN click_time >= CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) 
        AND click_time < CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) + INTERVAL 1 DAY 
        THEN 1 ELSE 0 END) AS total_today
FROM blossom_solver_clicks
WHERE click_time >= CAST(DATE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) AS DATETIME) - INTERVAL 29 DAY
ORDER BY hour
;
