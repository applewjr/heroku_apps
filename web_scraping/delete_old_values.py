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

cursor.execute("""SET SQL_SAFE_UPDATES = 0;""")
query = """
DELETE FROM youtube_trending
WHERE date < DATE_SUB(CURDATE(), INTERVAL 90 DAY);
"""
cursor.execute(query)
cursor.execute("""SET SQL_SAFE_UPDATES = 1;""")

cursor.close()
conn.close()

logging.info("complete: delete 90+ day old rows from youtube_trending")
