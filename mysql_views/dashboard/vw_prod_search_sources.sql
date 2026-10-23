CREATE OR REPLACE VIEW vw_prod_search_sources AS

-- Where each page's human landings come from: the last 7 days against the 7
-- before. google is any google.* referrer; other_search is Bing, DuckDuckGo,
-- Yahoo, Ecosia, Brave Search or Yandex; direct is no referrer at all
-- (bookmarks, typed URLs, apps, and browsers that hide it). The rest are
-- links from this site and other sites. google_change is the change in Google
-- landings, the number to watch after a page's title or description changes.
-- Same pages and filters as vw_prod_word_solver_page_visits.

WITH visits AS (
    SELECT
     page_name
    ,submit_time >= pst_now() - INTERVAL 7 DAY AS recent
    ,CASE
        WHEN referrer LIKE '%google.%' THEN 'google'
        WHEN referrer REGEXP 'bing\\.|duckduckgo|yahoo\\.|ecosia|search\\.brave|yandex' THEN 'other_search'
        WHEN referrer = 'No referrer' OR referrer = '' THEN 'direct'
        ELSE 'other'
        END AS source
    FROM app_visits
    WHERE submit_time >= pst_now() - INTERVAL 14 DAY
        AND page_name IN ('smush.html', 'blossom.html', 'chainle.html', 'ribbit.html', 'wordle_revamp.html',
                          'guess_the_phrase.html', 'wordiply.html', 'antiwordle_revamp.html', 'quordle.html')
        AND user_agent <> 'jj-healthcheck'
        AND referrer NOT LIKE '%127.0.0.1:5000%'
        AND referrer NOT LIKE '%apple-apps-staging%'
        AND NOT (LOWER(user_agent) REGEXP 'bot|spider|crawl|slurp|headless|python|curl|wget|httpclient|facebookexternalhit|preview|monitor')
    )
SELECT
 REPLACE(REPLACE(REPLACE(page_name, 'guess_the_phrase', 'gtp'), '_revamp', ''), '.html', '') AS page
,SUM(recent) AS landings_7d
,SUM(NOT recent) AS prior_7d
,SUM(recent AND source = 'google') - SUM(NOT recent AND source = 'google') AS google_change
,ROUND(100 * SUM(recent AND source = 'google') / NULLIF(SUM(recent), 0)) AS google_pct
,ROUND(100 * SUM(NOT recent AND source = 'google') / NULLIF(SUM(NOT recent), 0)) AS google_pct_prior
,ROUND(100 * SUM(recent AND source = 'other_search') / NULLIF(SUM(recent), 0)) AS other_search_pct
,ROUND(100 * SUM(recent AND source = 'direct') / NULLIF(SUM(recent), 0)) AS direct_pct
FROM visits
GROUP BY page_name
ORDER BY landings_7d DESC
;
