CREATE OR REPLACE VIEW vw_prod_word_solver_page_visits AS

-- Human landings per page per day, last 14 days, newest first; today is
-- partial. One column per page the app actually logs, plus a total. A page
-- needs both a SUM here and an entry in the WHERE list below; a column
-- without the WHERE entry reads zero forever.
--
-- blossom_bee.html, wordle.html, wordle_example.html, antiwordle.html and
-- quordle_mobile.html are deliberately absent - nothing has logged them since
-- the revamp, so they would be columns of zeroes. Their historical rows are
-- still in app_visits.
--
-- vw_prod_weekly_landings, vw_prod_search_sources and vw_prod_internal_flow
-- use the same page list and the same filters, so their numbers agree, as
-- does vw_prod_traffic_today's visits column. A solver's '<page> (solve)'
-- rows (extensions.log_solve) are uses, not landings: only
-- vw_prod_traffic_today and vw_prod_solver_use_7d count them.

SELECT
    DATE(submit_time) AS date,
    SUM(page_name = 'smush.html') AS smush,
    SUM(page_name = 'blossom.html') AS blossom,
    SUM(page_name = 'chainle.html') AS chainle,
    SUM(page_name = 'ribbit.html') AS ribbit,
    SUM(page_name = 'wordle_revamp.html') AS wordle,
    SUM(page_name = 'guess_the_phrase.html') AS gtp,
    SUM(page_name = 'wordiply.html') AS wordiply,
    SUM(page_name = 'antiwordle_revamp.html') AS antiwordle,
    SUM(page_name = 'quordle.html') AS quordle,
    COUNT(*) AS total
FROM app_visits
WHERE submit_time >= DATE(pst_now()) - INTERVAL 13 DAY
    AND page_name IN ('smush.html', 'blossom.html', 'chainle.html', 'ribbit.html', 'wordle_revamp.html',
                      'guess_the_phrase.html', 'wordiply.html', 'antiwordle_revamp.html', 'quordle.html')
    AND referrer NOT LIKE '%127.0.0.1:5000%'
    AND referrer NOT LIKE '%apple-apps-staging%'
    -- run_checks fetches /blossom, /smush, /wordle, /guess_the_phrase and
    -- /chainle every hour, and those GETs write app_visits rows like any other.
    -- Counting them would add 24 a day to five of these columns and quietly
    -- overstate real usage.
    AND user_agent <> 'jj-healthcheck'
    -- Crawlers, link previews and scripts aren't people either.
    AND NOT (LOWER(user_agent) REGEXP 'bot|spider|crawl|slurp|headless|python|curl|wget|httpclient|facebookexternalhit|preview|monitor')
GROUP BY date
ORDER BY date DESC
;
