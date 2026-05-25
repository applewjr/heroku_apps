CREATE OR REPLACE VIEW vw_prod_high_level_etl AS

SELECT 'YouTube' AS table_name, MIN(collected_dt) AS min, MAX(collected_dt) AS max, COUNT(*) AS rows_count FROM youtube_trending
UNION ALL SELECT 'YouTube Grouped' AS table_name, MIN(collected_dt) AS min, MAX(collected_dt) AS max, COUNT(*) AS rows_count FROM youtube_trending_revamp
;
