CREATE OR REPLACE VIEW vw_prod_youtube_top_categories AS

SELECT
 category
,SUM(CASE WHEN vid_rank <= 50 THEN 1 ELSE 0 END) AS top_50_count
,SUM(CASE WHEN vid_rank <= 10 THEN 1 ELSE 0 END) AS top_10_count
,SUM(CASE WHEN vid_rank <= 1 THEN 1 ELSE 0 END) AS top_1_count
FROM youtube_trending AS yt
LEFT JOIN youtube_cat AS cat ON yt.vid_cat_id = cat.id
WHERE collected_date >= CURDATE() - INTERVAL 30 DAY
GROUP BY category
ORDER BY top_50_count DESC, top_10_count DESC, top_1_count DESC
;
