CREATE OR REPLACE VIEW vw_prod_smush_votes_recent AS

SELECT
 word
,vote
,LEFT(voter_hash, 8) AS player
,puzzle
,created_at
FROM smush_word_votes
ORDER BY created_at DESC
LIMIT 100;
