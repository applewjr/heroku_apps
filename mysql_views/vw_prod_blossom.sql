CREATE OR REPLACE VIEW vw_prod_blossom AS

WITH all_clicks AS (
    SELECT 
        DATE(click_time) AS calendar_day,
        COUNT(*) AS clicks
    FROM blossom_solver_clicks
    WHERE DATE(click_time) BETWEEN DATE_SUB(CURRENT_DATE, INTERVAL 28 DAY) AND CURRENT_DATE
    GROUP BY DATE(click_time)
),
timed_clicks AS (
    SELECT 
        DATE(click_time) AS calendar_day,
        COUNT(*) AS clicks
    FROM blossom_solver_clicks
    WHERE TIME(click_time) <= TIME(CONVERT_TZ(CURTIME(), 'UTC', 'America/Los_Angeles'))
        AND DATE(click_time) BETWEEN DATE_SUB(CURRENT_DATE, INTERVAL 28 DAY) AND CURRENT_DATE
    GROUP BY DATE(click_time)
),
clicks_combined AS (
    SELECT 
        all_clicks.calendar_day,
        timed_clicks.clicks AS clicks_by_time,
        all_clicks.clicks AS total_clicks
    FROM all_clicks
    LEFT JOIN timed_clicks ON all_clicks.calendar_day = timed_clicks.calendar_day
),
minmax_values AS (
    SELECT
        MIN(clicks_by_time) AS min_clicks_by_time,
        MAX(clicks_by_time) AS max_clicks_by_time,
        -- Adjust the min and max calculation to exclude today's date
        MIN(CASE WHEN calendar_day <> DATE_FORMAT(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), '%Y-%m-%d') THEN total_clicks END) AS min_total_clicks,
        MAX(CASE WHEN calendar_day <> DATE_FORMAT(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), '%Y-%m-%d') THEN total_clicks END) AS max_total_clicks
    FROM clicks_combined
)
SELECT 
    c.calendar_day,
    SUBSTRING(DAYNAME(c.calendar_day), 1, 3) AS day_of_week,
    c.clicks_by_time,
    ROUND(c.clicks_by_time / (HOUR(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')) + MINUTE(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'))/60) /60,4) AS cpm_by_time,
    CASE 
        WHEN m.max_clicks_by_time = m.min_clicks_by_time THEN 1
        ELSE ROUND((c.clicks_by_time - m.min_clicks_by_time) / (m.max_clicks_by_time - m.min_clicks_by_time), 2)
        END AS clicks_by_time_minmax,
    CASE
        WHEN c.calendar_day = DATE_FORMAT(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), '%Y-%m-%d') THEN "-"				
        ELSE c.total_clicks
        END AS total_clicks,
    CASE
        WHEN c.calendar_day = DATE_FORMAT(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), '%Y-%m-%d') THEN "-"             
        ELSE ROUND(c.total_clicks/24/60,4)
        END AS total_cpm,
    CASE 
        WHEN c.calendar_day = DATE_FORMAT(CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), '%Y-%m-%d') THEN "-"
        WHEN m.max_total_clicks = m.min_total_clicks THEN 1
        ELSE ROUND((c.total_clicks - m.min_total_clicks) / (m.max_total_clicks - m.min_total_clicks), 2)
        END AS total_clicks_minmax
FROM clicks_combined c, minmax_values m
ORDER BY c.calendar_day DESC
;
