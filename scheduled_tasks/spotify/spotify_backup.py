import requests
import base64
import pandas as pd
import pytz
from datetime import datetime, date
import time
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


pst = pytz.timezone('America/Los_Angeles')
now = datetime.now(pst)

today = date.today()
formatted_date = today.strftime("%Y%m%d")

gmail_sender_email = 'james.r.applewhite@gmail.com'
gmail_receiver_email = 'james.r.applewhite@gmail.com'
gmail_subject = 'spotify_backup.py'
gmail_list = []

def print_and_append(statement):
    print(statement)
    gmail_list.append(statement)









conn = mysql.connector.connect(**config)
cursor = conn.cursor()

cursor.execute("""DROP TABLE IF EXISTS spotify_playlists_backup;""")
query = """
CREATE TABLE IF NOT EXISTS spotify_playlists_backup (
	id INT AUTO_INCREMENT PRIMARY KEY,
	day_index INT,
	playlist_name VARCHAR(32),
	playlist_ranking INT,
	playlist_uri VARCHAR(64),
	artist VARCHAR(64),
	artist_id VARCHAR(32),
	album VARCHAR(256),
	album_id VARCHAR(32),
	track_name VARCHAR(256),
	track_id VARCHAR(32),
	track_added_playlist_at DATETIME,
	track_popularity INT,
	collected_dt DATETIME,
	collected_date DATE,
    UNIQUE KEY unique_spotify_playlists (playlist_name, playlist_ranking, collected_date)
) AS SELECT * FROM spotify_playlists;
"""
cursor.execute(query)
print_and_append("complete: backup of spotify_playlists")





cursor.execute("""DROP TABLE IF EXISTS spotify_tracks_backup;""")
query = """
CREATE TABLE IF NOT EXISTS spotify_tracks_backup (
	id INT AUTO_INCREMENT PRIMARY KEY,
	track_id VARCHAR(32),
	track_name VARCHAR(256),
	track_release_date DATE,
	track_release_date_precision VARCHAR(32),
	danceability FLOAT,
	energy FLOAT,
	trk_key TINYINT,
	loudness FLOAT,
	trk_mode TINYINT,
	speechiness FLOAT,
	acousticness FLOAT,
	instrumentalness FLOAT,
	liveness FLOAT,
	valence FLOAT,
	tempo FLOAT,
	duration TIME,
	time_signature TINYINT,
	collected_dt DATETIME,
	collected_date DATE,
    UNIQUE KEY unique_spotify_tracks (track_id)
) AS SELECT * FROM spotify_tracks;
"""
cursor.execute(query)
print_and_append("complete: backup of spotify_tracks")






cursor.execute("""DROP TABLE IF EXISTS spotify_artists_backup;""")
query = """
CREATE TABLE IF NOT EXISTS spotify_artists_backup (
	id INT AUTO_INCREMENT PRIMARY KEY,
	artist_id VARCHAR(32),
	artist_name VARCHAR(64),
	genre VARCHAR(64),
	followers INT,
	popularity TINYINT,
	collected_dt DATETIME,
	collected_date DATE,
    UNIQUE KEY unique_spotify_artists (artist_id)
) AS SELECT * FROM spotify_artists;
"""
cursor.execute(query)
print_and_append("complete: backup of spotify_artists")






cursor.close()
conn.close()



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
