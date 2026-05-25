CREATE OR REPLACE VIEW vw_prod_youtube_top_10_channel AS

SELECT
 chnl
,COUNT(*) AS occurrences
,MIN(vid_rank) AS best_channel_rank
FROM youtube_trending
WHERE collected_date >= CURDATE() - INTERVAL 30 DAY
GROUP BY chnl_id
ORDER BY occurrences DESC, best_channel_rank ASC
LIMIT 10
;
