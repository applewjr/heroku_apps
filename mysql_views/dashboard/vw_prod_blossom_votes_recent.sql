CREATE OR REPLACE VIEW vw_prod_blossom_votes_recent AS

SELECT
 word
,vote
,LEFT(voter_hash, 8) AS player
,puzzle
,created_at
FROM blossom_word_votes
ORDER BY created_at DESC
LIMIT 100;
