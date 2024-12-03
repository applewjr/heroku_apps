CREATE OR REPLACE VIEW vw_prod_demo AS

SELECT *
FROM app_visits AS av
where page_name IN ('https://twitter.com/J_R_Applewhite', 'https://www.linkedin.com/in/j-applewhite/', 'https://github.com/applewjr/heroku_apps')
	AND referrer NOT LIKE '%5000%'
ORDER BY submit_time DESC
LIMIT 25
;
