from datetime import datetime
import mysql.connector
import os
import smtplib
from email.mime.text import MIMEText
import redis
import json
from datetime import datetime

if 'IS_HEROKU' in os.environ:
    # Running on Heroku, load values from Heroku Config Vars
    db_config = {
        'user': os.environ.get('jawsdb_user'),
        'password': os.environ.get('jawsdb_pass'),
        'host': os.environ.get('jawsdb_host'),
        'database': os.environ.get('jawsdb_db'),
        'raise_on_warnings': True
        }
    GMAIL_PASS = os.environ.get('GMAIL_PASS')
    REDIS_HOST = os.environ.get('REDIS_HOST')
    REDIS_PORT = os.environ.get('REDIS_PORT')
    REDIS_PASS = os.environ.get('REDIS_PASS')
else:
    # Running locally, load values from secret_pass.py
    import sys
    script_directory = os.path.dirname(os.path.abspath(__file__))
    root_directory = os.path.dirname(script_directory)
    sys.path.append(root_directory)
    import secret_pass
    db_config = {
        'user': secret_pass.mysql_user,
        'password': secret_pass.mysql_pass,
        'host': secret_pass.mysql_host,
        'database': secret_pass.mysql_db,
        'raise_on_warnings': True
        }
    GMAIL_PASS = secret_pass.GMAIL_PASS
    REDIS_HOST = secret_pass.REDIS_HOST
    REDIS_PORT = secret_pass.REDIS_PORT
    REDIS_PASS = secret_pass.REDIS_PASS

gmail_sender_email = 'james.r.applewhite@gmail.com'
gmail_receiver_email = 'james.r.applewhite@gmail.com'
gmail_subject = 'redis_wordle.py'
gmail_list = []

def print_and_append(statement):
    print(statement)
    gmail_list.append(statement)

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASS, decode_responses=True)


##### pull the data from redis #####

stream_name = 'wordle_logging'

stream_entries = r.xrevrange(stream_name)


##### transform the data #####

parsed_data_list = []

for entry in stream_entries:
    entry_id, entry_data = entry  # Unpack the tuple
    
    datetime_value = entry_data['datetime']
    json_value = entry_data['json']
    json_parsed = json.loads(json_value)
    
    parsed_data_list.append({
        'datetime': datetime_value,
        'data': json_parsed
    })




##### import the data into MySQL #####

try:
    # Establish database connection
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()
    
    # Prepare insert query
    insert_query = "INSERT INTO wordle_revamp_clicks (click_time, data_dict) VALUES (%s, %s)"
    insert_data = [(datetime.strptime(entry['datetime'], "%Y-%m-%d %H:%M:%S"), json.dumps(entry['data'])) for entry in parsed_data_list]

    # Insert data into MySQL
    cursor.executemany(insert_query, insert_data)
    conn.commit()
    print_and_append(f"Inserted {len(insert_data)} rows into MySQL.")

    # Assuming 'stream_name' contains the name of your Redis stream
    stream_name = 'wordle_logging'
    # Delete the data from Redis
    # Note: Adjust this part according to how you want to handle the deletion,
    # e.g., deleting specific entries or the entire stream.
    r.delete(stream_name)
    print_and_append(f"Cleared the Redis stream: {stream_name}")

except mysql.connector.Error as err:
    print_and_append(f"MySQL Error: {err}")
except redis.RedisError as err:
    print_and_append(f"Redis Error: {err}")
finally:
    if conn.is_connected():
        cursor.close()
        conn.close()
        print_and_append("MySQL connection is closed.")




print_and_append("wordle data from Redis are inserted into MySQL. Redis data are deleted")

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

