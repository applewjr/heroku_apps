CREATE OR REPLACE VIEW vw_prod_internal_flow AS

-- Visits that came from one of this site's pages to a solver: the last 7 days
-- against the 7 before, one row per from -> to pair. from_page is the first
-- path segment of the referrer, home for the front page; to_page is the
-- solver landed on, named as in vw_prod_search_sources. A page's own reloads
-- and result pages (from_page = to_page) are left out, and so are links from
-- anywhere else (the admin and other pages).
--
-- It scores the "More daily puzzle solvers" cards at the bottom of every
-- solver page (templates/_related_solvers.html). A move made from the cards,
-- the menu or the front page looks the same here, so read it against the
-- weeks before the cards. Same pages and filters as
-- vw_prod_word_solver_page_visits.
--
-- The referrer has to start with this site's own address: a search engine's
-- redirect URL can carry it further along (google.com/url?...).

WITH visits AS (
    SELECT
     REPLACE(REPLACE(REPLACE(page_name, 'guess_the_phrase', 'gtp'), '_revamp', ''), '.html', '') AS to_page
    ,SUBSTRING_INDEX(SUBSTRING_INDEX(SUBSTRING_INDEX(SUBSTRING_INDEX(referrer, '/', 4), '/', -1), '?', 1), '#', 1) AS segment
    ,submit_time >= pst_now() - INTERVAL 7 DAY AS recent
    FROM app_visits
    WHERE submit_time >= pst_now() - INTERVAL 14 DAY
        AND page_name IN ('smush.html', 'blossom.html', 'chainle.html', 'ribbit.html', 'wordle_revamp.html',
                          'guess_the_phrase.html', 'wordiply.html', 'antiwordle_revamp.html', 'quordle.html')
        AND referrer REGEXP '^https://(www\\.)?jamesapplewhite\\.com([/?#]|$)'
        AND user_agent <> 'jj-healthcheck'
        AND referrer NOT LIKE '%127.0.0.1:5000%'
        AND referrer NOT LIKE '%apple-apps-staging%'
        AND NOT (LOWER(user_agent) REGEXP 'bot|spider|crawl|slurp|headless|python|curl|wget|httpclient|facebookexternalhit|preview|monitor')
    ),
moves AS (
    SELECT
     CASE
        -- '' is the front page with its slash; a bare host (no slash) leaves
        -- the host itself as the segment.
        WHEN segment = '' OR segment LIKE '%jamesapplewhite.com%' THEN 'home'
        WHEN segment = 'guess_the_phrase' THEN 'gtp'
        ELSE segment
        END AS from_page
    ,to_page
    ,recent
    FROM visits
    )
SELECT
 from_page
,to_page
,SUM(recent) AS moves_7d
,SUM(NOT recent) AS prior_7d
,SUM(recent) - SUM(NOT recent) AS moves_change
FROM moves
WHERE from_page IN ('home', 'smush', 'blossom', 'chainle', 'ribbit', 'wordiply', 'gtp',
                    'wordle', 'antiwordle', 'quordle')
    AND from_page <> to_page
GROUP BY from_page, to_page
ORDER BY moves_7d DESC, prior_7d DESC
;
