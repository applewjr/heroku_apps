CREATE OR REPLACE VIEW vw_prod_youtube_top_10_today AS

WITH vid_rank AS (
    SELECT video, MIN(vid_rank) AS best_vid_rank
    FROM youtube_trending
    WHERE vid_rank <= 10
    GROUP BY video
    )
,rank_yesterday AS (
    SELECT video, chnl, vid_rank AS rank_yesterday
    FROM youtube_trending
    WHERE collected_date = CURDATE()-1
        AND vid_rank <= 10
    )
SELECT
 yt.vid_rank
,yt.video
,yt.chnl
,vid_rank.best_vid_rank
,CASE WHEN rank_yesterday.rank_yesterday IS NULL THEN "New"
    ELSE rank_yesterday.rank_yesterday
    END AS vid_rank_yesterday
,yt.vid_id
,yt.chnl_id
FROM youtube_trending AS yt
LEFT JOIN vid_rank ON yt.video = vid_rank.video
LEFT JOIN rank_yesterday ON yt.video = rank_yesterday.video AND yt.chnl = rank_yesterday.chnl
WHERE yt.collected_date = curdate()
    AND vid_rank <= 10
ORDER BY yt.vid_rank ASC
;
