import mysql.connector
import os
# import logging


if 'IS_HEROKU' in os.environ:
    # Running on Heroku, load values from Heroku Config Vars
    config = {
        'user': os.environ.get('jawsdb_user'),
        'password': os.environ.get('jawsdb_pass'),
        'host': os.environ.get('jawsdb_host'),
        'database': os.environ.get('jawsdb_db'),
        'raise_on_warnings': True
        }
else:
    # Running locally, load values from secret_pass.py
    import sys
    script_directory = os.path.dirname(os.path.abspath(__file__))
    root_directory = os.path.dirname(script_directory)
    sys.path.append(root_directory)
    import secret_pass
    config = {
        'user': secret_pass.mysql_user,
        'password': secret_pass.mysql_pass,
        'host': secret_pass.mysql_host,
        'database': secret_pass.mysql_bd,
        'raise_on_warnings': True
        }


conn = mysql.connector.connect(**config)
cursor = conn.cursor()

cursor.execute("""DROP TABLE IF EXISTS youtube_trending_backup;""")
query = """
CREATE TABLE IF NOT EXISTS youtube_trending_backup (
    id INT AUTO_INCREMENT PRIMARY KEY,
    video VARCHAR(100),
    chnl VARCHAR(64),
    vid_rank TINYINT,
    vid_views BIGINT,
    vid_likes INT,
    vid_comments INT,
    vid_cat_id TINYINT,
    vid_uploaded_dt DATETIME,
    chnl_subs INT,
    chnl_views BIGINT,
    chnl_video_count INT,
    collected_dt DATETIME,
    collected_date DATE,
	vid_id VARCHAR(15),
	chnl_id VARCHAR(30),
    UNIQUE KEY unique_video_channel (collected_date, vid_rank)
) AS SELECT * FROM youtube_trending;
"""
cursor.execute(query)

cursor.close()
conn.close()

print("complete: backup of youtube_trending")
