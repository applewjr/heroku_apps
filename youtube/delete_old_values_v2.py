import mysql.connector
import os
import smtplib
from email.mime.text import MIMEText
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
    GMAIL_PASS = os.environ.get('GMAIL_PASS')
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
        'database': secret_pass.mysql_db,
        'raise_on_warnings': True
        }
    GMAIL_PASS = secret_pass.GMAIL_PASS

gmail_sender_email = 'james.r.applewhite@gmail.com'
gmail_receiver_email = 'james.r.applewhite@gmail.com'
gmail_subject = 'delete_old_values_v2.py'
gmail_list = []

def print_and_append(statement):
    print(statement)
    gmail_list.append(statement)

conn = mysql.connector.connect(**config)
cursor = conn.cursor()

cursor.execute("""SET SQL_SAFE_UPDATES = 0;""")
query = """
DELETE FROM youtube_trending
WHERE collected_date < DATE_SUB(CURDATE(), INTERVAL 90 DAY);
"""
cursor.execute(query)
cursor.execute("""SET SQL_SAFE_UPDATES = 1;""")

cursor.close()
conn.close()

print_and_append("complete: delete 90+ day old rows from youtube_trending")

gmail_message = '\n'.join(gmail_list)
msg = MIMEText(gmail_message)
msg['Subject'] = gmail_subject
msg['From'] = gmail_sender_email
msg['To'] = gmail_receiver_email
with smtplib.SMTP('smtp.gmail.com', 587) as server:
    server.starttls()
    server.login(gmail_sender_email, GMAIL_PASS)
    server.sendmail(gmail_sender_email, gmail_receiver_email, msg.as_string())
    print('email sent')
