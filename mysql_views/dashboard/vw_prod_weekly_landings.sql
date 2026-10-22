CREATE OR REPLACE VIEW vw_prod_weekly_landings AS

-- Human landings per page per week (Monday to Sunday): this week, which is
-- partial, and the 7 before it, newest first. Same pages and filters as
-- vw_prod_word_solver_page_visits.

SELECT
 DATE_FORMAT(MIN(DATE(submit_time)) - INTERVAL WEEKDAY(MIN(submit_time)) DAY, '%Y-%m-%d') AS week_of
,SUM(page_name = 'smush.html') AS smush
,SUM(page_name = 'blossom.html') AS blossom
,SUM(page_name = 'chainle.html') AS chainle
,SUM(page_name = 'ribbit.html') AS ribbit
,SUM(page_name = 'wordle_revamp.html') AS wordle
,SUM(page_name = 'guess_the_phrase.html') AS gtp
,SUM(page_name = 'wordiply.html') AS wordiply
,SUM(page_name = 'antiwordle_revamp.html') AS antiwordle
,SUM(page_name = 'quordle.html') AS quordle
,COUNT(*) AS total
FROM app_visits
WHERE submit_time >= DATE(pst_now()) - INTERVAL WEEKDAY(pst_now()) DAY - INTERVAL 7 WEEK
    AND page_name IN ('smush.html', 'blossom.html', 'chainle.html', 'ribbit.html', 'wordle_revamp.html',
                      'guess_the_phrase.html', 'wordiply.html', 'antiwordle_revamp.html', 'quordle.html')
    AND user_agent <> 'jj-healthcheck'
    AND referrer NOT LIKE '%127.0.0.1:5000%'
    AND referrer NOT LIKE '%apple-apps-staging%'
    AND NOT (LOWER(user_agent) REGEXP 'bot|spider|crawl|slurp|headless|python|curl|wget|httpclient|facebookexternalhit|preview|monitor')
GROUP BY YEARWEEK(submit_time, 3)
ORDER BY week_of DESC
;
