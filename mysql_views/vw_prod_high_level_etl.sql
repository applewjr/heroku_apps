CREATE OR REPLACE VIEW vw_prod_high_level_etl AS

SELECT 'YouTube' AS table_name, MIN(collected_dt) AS min, MAX(collected_dt) AS max, COUNT(*) AS rows_count FROM youtube_trending
UNION ALL SELECT 'Spotify Playlists', MIN(collected_dt), MAX(collected_dt), COUNT(*) FROM spotify_playlists
UNION ALL SELECT 'Spotify Artists', MIN(collected_dt), MAX(collected_dt), COUNT(*) FROM spotify_artists
UNION ALL SELECT 'Spotify Tracks', MIN(collected_dt), MAX(collected_dt), COUNT(*) FROM spotify_tracks
UNION ALL SELECT 'LoL Summoner', MIN(pulled_dt), MAX(pulled_dt), COUNT(*) FROM lol_summoner
UNION ALL SELECT 'LoL Champion', MIN(pulled_dt), MAX(pulled_dt), COUNT(*) FROM lol_champion
UNION ALL SELECT 'LoL Match',
    CONVERT_TZ(MIN(gameStartTimestamp), 'UTC', 'America/Los_Angeles') AS min_pst,
    CONVERT_TZ(MAX(gameStartTimestamp), 'UTC', 'America/Los_Angeles') AS max_pst,
    COUNT(*)
FROM lol_match
;
