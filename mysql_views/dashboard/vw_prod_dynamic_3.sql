CREATE OR REPLACE VIEW vw_prod_dynamic_3 AS

-- Today's board, last 9 days: how many players logged a board (or a Chainle
-- round) each day, per game, by the player's own date. These players are what
-- feeds the "Load it" offers, so a game that drops to 0 while its traffic in
-- Traffic Today holds means logging broke. approvals counts admin boards.

SELECT
 d.played_on AS day
,COALESCE(s.players, 0) AS smush
,COALESCE(b.players, 0) AS blossom
,COALESCE(c.players, 0) AS chainle
,COALESCE(r.players, 0) AS ribbit
,COALESCE(s.approvals, 0) + COALESCE(b.approvals, 0) + COALESCE(c.approvals, 0) AS approvals
FROM (
    SELECT DISTINCT played_on FROM smush_boards WHERE played_on >= DATE(pst_now()) - INTERVAL 9 DAY
    UNION
    SELECT DISTINCT played_on FROM blossom_boards WHERE played_on >= DATE(pst_now()) - INTERVAL 9 DAY
    UNION
    SELECT DISTINCT played_on FROM chainle_boards WHERE played_on >= DATE(pst_now()) - INTERVAL 9 DAY
    UNION
    SELECT DISTINCT played_on FROM ribbit_boards WHERE played_on >= DATE(pst_now()) - INTERVAL 9 DAY
    ) AS d
LEFT JOIN (
    SELECT played_on, COUNT(DISTINCT CASE WHEN voter_hash <> 'admin' THEN voter_hash END) AS players,
           SUM(voter_hash = 'admin') AS approvals
    FROM smush_boards WHERE played_on >= DATE(pst_now()) - INTERVAL 9 DAY GROUP BY played_on
    ) AS s ON s.played_on = d.played_on
LEFT JOIN (
    SELECT played_on, COUNT(DISTINCT CASE WHEN voter_hash <> 'admin' THEN voter_hash END) AS players,
           SUM(voter_hash = 'admin') AS approvals
    FROM blossom_boards WHERE played_on >= DATE(pst_now()) - INTERVAL 9 DAY GROUP BY played_on
    ) AS b ON b.played_on = d.played_on
LEFT JOIN (
    SELECT played_on, COUNT(DISTINCT CASE WHEN voter_hash <> 'admin' THEN voter_hash END) AS players,
           COUNT(DISTINCT CASE WHEN voter_hash = 'admin' THEN CONCAT(site, round_no) END) AS approvals
    FROM chainle_boards WHERE played_on >= DATE(pst_now()) - INTERVAL 9 DAY GROUP BY played_on
    ) AS c ON c.played_on = d.played_on
LEFT JOIN (
    SELECT played_on, COUNT(DISTINCT voter_hash) AS players
    FROM ribbit_boards WHERE played_on >= DATE(pst_now()) - INTERVAL 9 DAY GROUP BY played_on
    ) AS r ON r.played_on = d.played_on
ORDER BY day DESC
;
