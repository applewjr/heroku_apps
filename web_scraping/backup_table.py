import mysql.connector
import os
import logging


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
    title VARCHAR(100),
    channel VARCHAR(50),
    views VARCHAR(30),
    uploaded VARCHAR(30),
    datetime DATETIME,
    date DATE,
    ranking TINYINT,
    UNIQUE KEY unique_title_channel (date, ranking)
) AS SELECT * FROM youtube_trending;
"""
cursor.execute(query)

cursor.close()
conn.close()

logging.info("complete: backup youtube_trending")
