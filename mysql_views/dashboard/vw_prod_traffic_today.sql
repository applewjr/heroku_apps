CREATE OR REPLACE VIEW vw_prod_traffic_today AS

-- Each logged page today against its usual: landings so far, how many it
-- usually has by this time of day (the average of the last 7 days up to the
-- same clock time), the difference in percent, its usual full day, and
-- minutes since its latest landing. A page with no landing in the last 7
-- days drops out. An "all games" row at the bottom sums every page (its
-- min_since_last is the latest landing on any page).
--
-- A landing is one page load, so a refresh or a phone reloading a background
-- tab counts again. The visits columns estimate people instead: a landing
-- starts a new visit only if the same page had no landing from the same user
-- agent in the previous 30 minutes. app_visits stores no visitor id, and
-- people with identical phones share a user agent, so this slightly
-- undercounts. In the "all games" row someone who uses two solvers counts
-- twice.
--
-- Human landings only, as in vw_prod_word_solver_page_visits: not the hourly
-- health probe, not a local or staging run, and not a bot user agent.

WITH visits AS (
    SELECT page_name, user_agent, submit_time
    FROM app_visits
    -- 30 extra minutes so the first landing in the window can see an earlier one
    WHERE submit_time >= DATE(pst_now()) - INTERVAL 7 DAY - INTERVAL 30 MINUTE
        AND page_name IN ('smush.html', 'blossom.html', 'chainle.html', 'ribbit.html', 'wordle_revamp.html',
                          'guess_the_phrase.html', 'wordiply.html', 'antiwordle_revamp.html', 'quordle.html')
        AND user_agent <> 'jj-healthcheck'
        AND referrer NOT LIKE '%127.0.0.1:5000%'
        AND referrer NOT LIKE '%apple-apps-staging%'
        AND NOT (LOWER(user_agent) REGEXP 'bot|spider|crawl|slurp|headless|python|curl|wget|httpclient|facebookexternalhit|preview|monitor')
    )
,flagged AS (
    SELECT
     page_name
    ,submit_time
    ,COALESCE(submit_time >= LAG(submit_time) OVER (PARTITION BY page_name, user_agent ORDER BY submit_time)
              + INTERVAL 30 MINUTE, 1) AS new_visit
    FROM visits
    )
,counts AS (
    SELECT
     page_name
    ,SUM(submit_time >= DATE(pst_now())) AS today
    ,SUM(new_visit * (submit_time >= DATE(pst_now()))) AS visits_today
    ,SUM(submit_time < DATE(pst_now()) AND TIME(submit_time) <= TIME(pst_now())) / 7 AS usual_by_now
    ,SUM(submit_time < DATE(pst_now())) / 7 AS per_day_7d
    ,SUM(new_visit * (submit_time < DATE(pst_now()))) / 7 AS visits_per_day_7d
    ,MAX(submit_time) AS last_visit
    FROM flagged
    WHERE submit_time >= DATE(pst_now()) - INTERVAL 7 DAY
    GROUP BY page_name
    )
,with_total AS (
    SELECT 0 AS is_total, page_name, today, visits_today, usual_by_now, per_day_7d, visits_per_day_7d, last_visit
    FROM counts
    UNION ALL
    SELECT 1, 'all games', SUM(today), SUM(visits_today), SUM(usual_by_now), SUM(per_day_7d),
           SUM(visits_per_day_7d), MAX(last_visit)
    FROM counts
    )
SELECT
 REPLACE(REPLACE(REPLACE(page_name, 'guess_the_phrase', 'gtp'), '_revamp', ''), '.html', '') AS page
,visits_today
,today
,ROUND(usual_by_now) AS usual_by_now
,ROUND(100 * (today / NULLIF(usual_by_now, 0) - 1)) AS vs_usual_pct
,ROUND(per_day_7d) AS per_day_7d
,ROUND(visits_per_day_7d) AS visits_per_day_7d
,TIMESTAMPDIFF(MINUTE, last_visit, pst_now()) AS min_since_last
FROM with_total
ORDER BY is_total, per_day_7d DESC
;
