CREATE OR REPLACE VIEW vw_prod_lol AS

SELECT 'lol_summoner' AS table_name,  COUNT(1) AS row_count, MIN(pulled_dt) AS min_pulled_dt, MAX(pulled_dt) AS max_pulled_dt, NULL AS min_matchId, NULL AS max_matchId FROM lol_summoner
    UNION ALL SELECT 'lol_champion' AS table_name, COUNT(1), MIN(pulled_dt), MAX(pulled_dt), NULL, NULL FROM lol_champion
    UNION ALL SELECT 'lol_all_match' AS table_name, COUNT(1), NULL, NULL, MIN(matchId), MAX(matchId) FROM lol_all_match
    UNION ALL SELECT 'lol_match' AS table_name, COUNT(1), MIN(gameCreation), MAX(gameCreation), MIN(matchId), MAX(matchId) FROM lol_match
    UNION ALL SELECT 'lol_participants_info' AS table_name, COUNT(1), NULL, NULL, MIN(matchId), MAX(matchId) FROM lol_participants_info
    UNION ALL SELECT 'lol_participants_challenges' AS table_name, COUNT(1), NULL, NULL, MIN(matchId), MAX(matchId) FROM lol_participants_challenges
;
