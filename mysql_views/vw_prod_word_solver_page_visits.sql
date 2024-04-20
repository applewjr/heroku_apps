CREATE OR REPLACE VIEW vw_prod_word_solver_page_visits AS

SELECT
    DATE(submit_time) AS date,
    SUM(CASE WHEN page_name = 'blossom.html' THEN 1 ELSE 0 END) AS blossom,
    SUM(CASE WHEN page_name = 'blossom_bee.html' THEN 1 ELSE 0 END) AS blossom_bee,
    SUM(CASE WHEN page_name = 'wordle_revamp.html' THEN 1 ELSE 0 END) AS wordle_revamp,
    SUM(CASE WHEN page_name = 'wordle.html' THEN 1 ELSE 0 END) AS wordle,
    SUM(CASE WHEN page_name = 'wordle_example.html' THEN 1 ELSE 0 END) AS wordle_example,
    SUM(CASE WHEN page_name = 'antiwordle_revamp.html' THEN 1 ELSE 0 END) AS antiwordle_revamp,
    SUM(CASE WHEN page_name = 'antiwordle.html' THEN 1 ELSE 0 END) AS antiwordle,
    SUM(CASE WHEN page_name = 'quordle.html' THEN 1 ELSE 0 END) AS quordle,
    SUM(CASE WHEN page_name = 'quordle_mobile.html' THEN 1 ELSE 0 END) AS quordle_mobile
FROM app_visits
WHERE page_name IN ('antiwordle.html', 'antiwordle_revamp.html', 'quordle.html', 'quordle_mobile.html', 'wordle.html', 'wordle_example.html', 'wordle_revamp.html', 'blossom.html', 'blossom_bee.html')
    AND referrer NOT LIKE '%127.0.0.1:5000%'
GROUP BY date
ORDER BY date DESC
LIMIT 14
;
