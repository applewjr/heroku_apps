import mysql.connector
import os
import smtplib
from email.mime.text import MIMEText
import mysql.connector
import time
import sys

project_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
functions_path = os.path.join(project_path, 'functions')
sys.path.append(functions_path)

import espresso

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
    GOOGLE_SHEETS_JSON = os.environ.get('GOOGLE_SHEETS_JSON')
    GOOGLE_SHEETS_URL_ESPRESSO = os.environ.get('GOOGLE_SHEETS_URL_ESPRESSO')
    GOOGLE_SHEETS_URL_BEAN = os.environ.get('GOOGLE_SHEETS_URL_BEAN')
    GOOGLE_SHEETS_URL_PROFILE = os.environ.get('GOOGLE_SHEETS_URL_PROFILE')
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
    GMAIL_PASS = secret_pass.GMAIL_PASS
    GOOGLE_SHEETS_JSON = secret_pass.GOOGLE_SHEETS_JSON
    GOOGLE_SHEETS_URL_ESPRESSO = secret_pass.GOOGLE_SHEETS_URL_ESPRESSO
    GOOGLE_SHEETS_URL_BEAN = secret_pass.GOOGLE_SHEETS_URL_BEAN
    GOOGLE_SHEETS_URL_PROFILE = secret_pass.GOOGLE_SHEETS_URL_PROFILE

gmail_sender_email = 'james.r.applewhite@gmail.com'
gmail_receiver_email = ['james.r.applewhite@gmail.com']
gmail_subject = 'espresso_data_import.py'
gmail_list = []

def print_and_append(statement):
    print(statement)
    gmail_list.append(statement)

def write_df_to_mysql(df, config, table_name):
    # Establish connection to MySQL database
    connection = mysql.connector.connect(**config)
    cursor = connection.cursor()

    # Check if the original table exists and create it if not
    cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
    result = cursor.fetchone()
    if not result:
        # Create table based on DataFrame structure
        columns = ", ".join([f"`{col}` VARCHAR(255)" for col in df.columns])
        cursor.execute(f"CREATE TABLE {table_name} ({columns})")

    # Create a unique temporary table name
    temp_table_name = f"{table_name}_temp_{int(time.time())}"

    # Create temporary table with same structure as the original table
    cursor.execute(f"CREATE TABLE {temp_table_name} LIKE {table_name}")

    # Insert DataFrame data into the temporary table
    for _, row in df.iterrows():
        columns = ', '.join(f'`{col}`' for col in df.columns)
        placeholders = ', '.join(['%s'] * len(row))
        insert_query = f"INSERT INTO {temp_table_name} ({columns}) VALUES ({placeholders})"
        cursor.execute(insert_query, tuple(row))

    # Rename the existing table and the temporary table
    old_table_name = f"{table_name}_old"
    cursor.execute(f"RENAME TABLE {table_name} TO {old_table_name}, {temp_table_name} TO {table_name}")

    # Optionally, drop the previous old table
    cursor.execute(f"DROP TABLE IF EXISTS {old_table_name}")

    # Commit changes and close the connection
    connection.commit()
    cursor.close()
    connection.close()

    print_and_append(f"write {table_name} to MySQL complete")


google_credentials = espresso.google_sheets_base(GOOGLE_SHEETS_JSON)

df_espresso_initial = espresso.get_google_sheets_espresso(google_credentials, GOOGLE_SHEETS_URL_ESPRESSO)
print_and_append(f'pull espresso data to df complete. {len(df_espresso_initial) = }')
write_df_to_mysql(df_espresso_initial, config, "espresso_data")

df_bean = espresso.get_google_sheets_bean(google_credentials, GOOGLE_SHEETS_URL_BEAN)
print_and_append(f'pull bean data to df complete. {len(df_bean) = }')
write_df_to_mysql(df_bean, config, "espresso_bean")

df_profile = espresso.get_google_sheets_profile(google_credentials, GOOGLE_SHEETS_URL_PROFILE)
print_and_append(f'pull profile data to df complete. {len(df_profile) = }')
write_df_to_mysql(df_profile, config, "espresso_profile")


gmail_message = '\n'.join(gmail_list)
msg = MIMEText(gmail_message)
msg['Subject'] = gmail_subject
msg['From'] = gmail_sender_email
msg['To'] = ', '.join(gmail_receiver_email)
with smtplib.SMTP('smtp.gmail.com', 587) as server:
    server.starttls()
    server.login(gmail_sender_email, GMAIL_PASS)
    server.sendmail(gmail_sender_email, gmail_receiver_email, msg.as_string())
    print('email sent')
