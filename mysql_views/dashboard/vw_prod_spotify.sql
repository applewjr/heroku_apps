CREATE OR REPLACE VIEW vw_prod_spotify AS

WITH all_dates AS (
    SELECT collected_date FROM spotify_playlists
    UNION SELECT collected_date FROM spotify_artists
    UNION SELECT collected_date FROM spotify_tracks
    )
SELECT
 all_dates.collected_date
,COALESCE(p.playlist_count, 0) AS playlist_count
,COALESCE(a.artist_count, 0) AS artist_count
,COALESCE(t.track_count, 0) AS track_count
FROM all_dates
LEFT JOIN (
    SELECT collected_date, COUNT(*) AS playlist_count
    FROM spotify_playlists
    GROUP BY collected_date
    ) p ON all_dates.collected_date = p.collected_date
LEFT JOIN (
    SELECT collected_date, COUNT(*) AS artist_count
    FROM spotify_artists
    GROUP BY collected_date
    ) a ON all_dates.collected_date = a.collected_date
LEFT JOIN (
    SELECT collected_date, COUNT(*) AS track_count
    FROM spotify_tracks
    GROUP BY collected_date
    ) t ON all_dates.collected_date = t.collected_date
ORDER BY all_dates.collected_date DESC
LIMIT 10
;
