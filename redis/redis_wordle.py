from datetime import datetime
import mysql.connector
import os
import smtplib
from email.mime.text import MIMEText
import redis
import json
from datetime import datetime
import time

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

max_retries = 5
retry_delay = 2  # seconds

for attempt in range(max_retries):
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASS, decode_responses=True, socket_timeout=10)
        print(r.ping())  # Test the connection
        break  # Connection was successful, exit the loop
    except Exception as e:
        print(f"Attempt {attempt+1}: Error connecting to Redis: {e}")
        if attempt < max_retries - 1:
            print(f"Retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)
        else:
            print("Failed to connect after several attempts.")


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

wordle_redis_insert_count = len(parsed_data_list)
print_and_append(f'{wordle_redis_insert_count = }')



##### import the data into MySQL #####

try:
    # Establish database connection
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()

    # pre row count
    cursor.execute("SELECT COUNT(*) FROM wordle_revamp_clicks")
    wordle_mysql_row_pre_count = cursor.fetchone()[0]
    conn.commit()
    print_and_append(f'{wordle_mysql_row_pre_count = }')
    
    # Prepare insert query
    insert_query = "INSERT INTO wordle_revamp_clicks (click_time, data_dict) VALUES (%s, %s)"
    insert_data = [(datetime.strptime(entry['datetime'], "%Y-%m-%d %H:%M:%S"), json.dumps(entry['data'])) for entry in parsed_data_list]
    cursor.executemany(insert_query, insert_data)
    conn.commit()
    print_and_append(f"Inserted {len(insert_data)} rows into MySQL.")

    # post row count
    cursor.execute("SELECT COUNT(*) FROM wordle_revamp_clicks")
    wordle_mysql_row_post_count = cursor.fetchone()[0]
    conn.commit()
    print_and_append(f'{wordle_mysql_row_post_count = }')

except mysql.connector.Error as err:
    print_and_append(f"MySQL Error: {err}")
finally:
    if conn.is_connected():
        cursor.close()
        conn.close()
        print_and_append("MySQL connection is closed.")



##### Delete the data from Redis #####

if wordle_mysql_row_pre_count + wordle_redis_insert_count == wordle_mysql_row_post_count:
    try:
        r.delete(stream_name)
        print_and_append(f"Cleared the Redis stream: {stream_name}")
    except redis.RedisError as err:
        print_and_append(f"Redis Error: {err}")
else:
    print_and_append('redis data not deleted. row counts do not align')



##### prep email #####

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


# TODO - handle 0 rows seen in redis better. lots of stuff can be skipped
# TODO - intermittend redis connections issues? redis.exceptions.ConnectionError: Error 11002 connecting to ***REDIS_HOST*** getaddrinfo failed.
# TODO - handle the possibility of inserting duplicate data into mysql
