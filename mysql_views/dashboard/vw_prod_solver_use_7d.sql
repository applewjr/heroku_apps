CREATE OR REPLACE VIEW vw_prod_solver_use_7d AS

-- Each solver's average day over the last 7 complete days (today left out):
-- visits, people, uses, users and use_rate_pct, as in vw_prod_traffic_today,
-- so a low use rate today can be told apart from a page that always runs
-- low. use_rate_prior_pct is the same rate over the 7 days before, and
-- use_rate_change the difference in points. The "all games" row counts each
-- person once a day, however many solvers they used.
--
-- Only days that visitor ids cover from midnight count, since people and
-- users can't be counted without them: days is how many of the 7 there are,
-- and the averages are over those. A day counts when its first row already
-- has an id, which leaves out every day before visitor_hash existed and the
-- day it shipped. So days climbs from 0 to 7 in the first week and the prior
-- columns fill in over the week after, with nothing to update by hand.
--
-- The id lasts a day (helpers.visitor_hash), so the distinct ids over several
-- days are each day's people added up: divided by days, people per day.
--
-- Same pages, rows and filters as vw_prod_traffic_today.

WITH rows_14d AS (
    SELECT
     REPLACE(page_name, ' (solve)', '') AS page_name
    ,page_name LIKE '% (solve)' AS is_use
    ,DATE(submit_time) AS day
    ,submit_time
    ,visitor_hash
    FROM app_visits
    WHERE submit_time >= DATE(pst_now()) - INTERVAL 14 DAY
        AND submit_time < DATE(pst_now())
        AND page_name IN ('smush.html', 'blossom.html', 'chainle.html', 'ribbit.html', 'wordle_revamp.html',
                          'guess_the_phrase.html', 'wordiply.html', 'antiwordle_revamp.html', 'quordle.html',
                          'smush.html (solve)', 'blossom.html (solve)', 'chainle.html (solve)',
                          'ribbit.html (solve)', 'wordle_revamp.html (solve)', 'guess_the_phrase.html (solve)',
                          'wordiply.html (solve)', 'antiwordle_revamp.html (solve)', 'quordle.html (solve)')
        AND user_agent <> 'jj-healthcheck'
        AND referrer NOT LIKE '%127.0.0.1:5000%'
        AND referrer NOT LIKE '%apple-apps-staging%'
        AND NOT (LOWER(user_agent) REGEXP 'bot|spider|crawl|slurp|headless|python|curl|wget|httpclient|facebookexternalhit|preview|monitor')
    )
,covered AS (
    SELECT day, day >= DATE(pst_now()) - INTERVAL 7 DAY AS recent
    FROM rows_14d
    GROUP BY day
    HAVING MIN(submit_time) = MIN(CASE WHEN visitor_hash IS NOT NULL THEN submit_time END)
    )
,span AS (
    SELECT COALESCE(SUM(recent), 0) AS days
    FROM covered
    )
,counts AS (
    SELECT
     r.page_name
    ,GROUPING(r.page_name) AS is_total
    ,SUM(c.recent AND NOT r.is_use) AS visits
    ,COUNT(DISTINCT CASE WHEN c.recent THEN r.visitor_hash END) AS people
    ,SUM(c.recent AND r.is_use) AS uses
    ,COUNT(DISTINCT CASE WHEN c.recent AND r.is_use THEN r.visitor_hash END) AS users
    ,COUNT(DISTINCT CASE WHEN NOT c.recent THEN r.visitor_hash END) AS people_prior
    ,COUNT(DISTINCT CASE WHEN NOT c.recent AND r.is_use THEN r.visitor_hash END) AS users_prior
    FROM rows_14d AS r
    LEFT JOIN covered AS c ON c.day = r.day
    GROUP BY r.page_name WITH ROLLUP
    )
SELECT
 CASE WHEN is_total = 1 THEN 'all games'
      ELSE REPLACE(REPLACE(REPLACE(page_name, 'guess_the_phrase', 'gtp'), '_revamp', ''), '.html', '') END AS page
,s.days
,ROUND(counts.visits / NULLIF(s.days, 0)) AS visits
,ROUND(counts.people / NULLIF(s.days, 0)) AS people
,ROUND(counts.uses / NULLIF(s.days, 0)) AS uses
,ROUND(counts.users / NULLIF(s.days, 0)) AS users
,ROUND(100 * counts.users / NULLIF(counts.people, 0)) AS use_rate_pct
,ROUND(100 * users_prior / NULLIF(people_prior, 0)) AS use_rate_prior_pct
,ROUND(100 * counts.users / NULLIF(counts.people, 0))
    - ROUND(100 * users_prior / NULLIF(people_prior, 0)) AS use_rate_change
FROM counts
CROSS JOIN span AS s
ORDER BY is_total, counts.visits DESC
;
