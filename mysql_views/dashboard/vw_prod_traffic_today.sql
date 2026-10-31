CREATE OR REPLACE VIEW vw_prod_traffic_today AS

-- Each solver today: visits (page loads), people (different visitors), uses
-- (page loads that ran the solver), users (different visitors who ran it) and
-- use_rate_pct, users as a share of people. Then visits against their usual
-- by this time of day (the average of the last 7 days up to the same clock
-- time), the difference in percent, the usual full day, and minutes since the
-- latest visit. A page with nothing logged in the last 7 days drops out. The
-- "all games" row at the bottom counts each person once, however many
-- solvers they used.
--
-- people and users count visitor_hash, an id that only lasts the day
-- (helpers.visitor_hash): a refresh, or a phone reloading a background tab,
-- is the same person. People on one network with identical phones share an
-- id, and a phone that changes networks mid-visit gets a second one. A use is
-- a '<page> (solve)' row, logged by the first solve request of a page load
-- (extensions.log_solve). people counts the ids on both kinds of row, so
-- someone solving today in a tab opened yesterday is a person as well as a
-- user, and use_rate_pct can't pass 100. Rows from before the column existed
-- have no id, so the day it shipped reads low on people.
--
-- Read use_rate_pct against the page's own week (vw_prod_solver_use_7d), not
-- against other pages: wordle, antiwordle and quordle show opening picks, and
-- blossom and smush today's board and its hints, before any solve, so some
-- visitors get what they came for without one.
--
-- Human traffic only, as in vw_prod_word_solver_page_visits: not the hourly
-- health probe, not a local or staging run, and not a bot user agent.

WITH rows_7d AS (
    SELECT
     REPLACE(page_name, ' (solve)', '') AS page_name
    ,page_name LIKE '% (solve)' AS is_use
    ,submit_time >= DATE(pst_now()) AS is_today
    ,visitor_hash
    ,submit_time
    FROM app_visits
    WHERE submit_time >= DATE(pst_now()) - INTERVAL 7 DAY
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
,counts AS (
    SELECT
     page_name
    ,GROUPING(page_name) AS is_total
    ,SUM(is_today AND NOT is_use) AS visits
    ,COUNT(DISTINCT CASE WHEN is_today THEN visitor_hash END) AS people
    ,SUM(is_today AND is_use) AS uses
    ,COUNT(DISTINCT CASE WHEN is_today AND is_use THEN visitor_hash END) AS users
    ,SUM(NOT is_today AND NOT is_use AND TIME(submit_time) <= TIME(pst_now())) / 7 AS usual_by_now
    ,SUM(NOT is_today AND NOT is_use) / 7 AS per_day_7d
    ,MAX(CASE WHEN NOT is_use THEN submit_time END) AS last_visit
    FROM rows_7d
    GROUP BY page_name WITH ROLLUP
    )
SELECT
 CASE WHEN is_total = 1 THEN 'all games'
      ELSE REPLACE(REPLACE(REPLACE(page_name, 'guess_the_phrase', 'gtp'), '_revamp', ''), '.html', '') END AS page
,visits
,people
,uses
,users
,ROUND(100 * users / NULLIF(people, 0)) AS use_rate_pct
,ROUND(usual_by_now) AS usual_by_now
,ROUND(100 * (visits / NULLIF(usual_by_now, 0) - 1)) AS vs_usual_pct
,ROUND(per_day_7d) AS per_day_7d
,TIMESTAMPDIFF(MINUTE, last_visit, pst_now()) AS min_since_last
FROM counts
ORDER BY is_total, per_day_7d DESC
;
