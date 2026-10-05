CREATE OR REPLACE VIEW vw_prod_smush_crowd_changes AS

SELECT id, word, added_date, source, 'invalid' AS performed
FROM smush_invalid_words
WHERE source != 'admin'
UNION ALL
SELECT id, word, added_date, source, 'added' AS performed
FROM smush_added_words
WHERE source != 'admin'
ORDER BY added_date DESC;
