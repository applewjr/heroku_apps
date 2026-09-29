CREATE OR REPLACE VIEW vw_prod_word_solver_page_visits AS

-- One column per page the app actually logs. A page needs both a SUM here and
-- an entry in the WHERE list below; a column without the WHERE entry reads
-- zero forever.
--
-- blossom_bee.html, wordle.html, wordle_example.html, antiwordle.html and
-- quordle_mobile.html are deliberately absent - nothing has logged them since
-- the revamp, so they would be columns of zeroes. Their historical rows are
-- still in app_visits.

SELECT
    DATE(submit_time) AS date,
    SUM(CASE WHEN page_name = 'blossom.html' THEN 1 ELSE 0 END) AS blossom,
    SUM(CASE WHEN page_name = 'wordle_revamp.html' THEN 1 ELSE 0 END) AS wordle_revamp,
    SUM(CASE WHEN page_name = 'antiwordle_revamp.html' THEN 1 ELSE 0 END) AS antiwordle_revamp,
    SUM(CASE WHEN page_name = 'quordle.html' THEN 1 ELSE 0 END) AS quordle,
    SUM(CASE WHEN page_name = 'smush.html' THEN 1 ELSE 0 END) AS smush,
    SUM(CASE WHEN page_name = 'ribbit.html' THEN 1 ELSE 0 END) AS ribbit,
    SUM(CASE WHEN page_name = 'wordiply.html' THEN 1 ELSE 0 END) AS wordiply
FROM app_visits
WHERE page_name IN ('blossom.html', 'wordle_revamp.html', 'antiwordle_revamp.html', 'quordle.html', 'smush.html', 'ribbit.html', 'wordiply.html')
    AND referrer NOT LIKE '%127.0.0.1:5000%'
    -- run_checks fetches /blossom, /smush and /wordle every hour, and those
    -- GETs write app_visits rows like any other. Counting them would add 24 a
    -- day to three of these columns and quietly overstate real usage.
    AND user_agent <> 'jj-healthcheck'
GROUP BY date
ORDER BY date DESC
LIMIT 14
;
