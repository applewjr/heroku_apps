CREATE OR REPLACE VIEW vw_prod_youtube_top_10_title AS

SELECT
 video
,chnl
,COUNT(*) AS occurrences
,MIN(vid_rank) AS best_vid_rank
FROM youtube_trending
WHERE collected_date >= CURDATE() - INTERVAL 30 DAY
GROUP BY vid_id
ORDER BY occurrences DESC, best_vid_rank ASC
LIMIT 10
;
