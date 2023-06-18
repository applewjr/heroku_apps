import requests
import base64
import pandas as pd
import pytz
from datetime import datetime, date
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import time
import mysql.connector
import os
from googleapiclient.discovery import build
import pytz
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
    SPOTIFY_ID = os.environ.get('SPOTIFY_ID')
    SPOTIFY_SECRET = os.environ.get('SPOTIFY_SECRET')
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
        'database': secret_pass.mysql_bd,
        'raise_on_warnings': True
        }
    SPOTIFY_ID = secret_pass.SPOTIFY_ID
    SPOTIFY_SECRET = secret_pass.SPOTIFY_SECRET
    GMAIL_PASS = secret_pass.GMAIL_PASS

pst = pytz.timezone('America/Los_Angeles')
now = datetime.now(pst)

today = date.today()
formatted_date = today.strftime("%Y%m%d")

gmail_sender_email = 'james.r.applewhite@gmail.com'
gmail_receiver_email = 'james.r.applewhite@gmail.com'
gmail_subject = 'spotify_playlists.py'
gmail_list = []

def print_and_append(statement):
    print(statement)
    gmail_list.append(statement)

# Base64 encoded client credentials
client_credentials = base64.b64encode(f'{SPOTIFY_ID}:{SPOTIFY_SECRET}'.encode()).decode('utf-8')

# Get access token
auth_url = 'https://accounts.spotify.com/api/token'
auth_headers = {'Authorization': f'Basic {client_credentials}'}
auth_data = {'grant_type': 'client_credentials'}
auth_response = requests.post(auth_url, headers=auth_headers, data=auth_data)
access_token = auth_response.json()['access_token']

# Trending endpoint
trending_url = 'https://api.spotify.com/v1/browse/categories/toplists/playlists'
trending_headers = {'Authorization': f'Bearer {access_token}'}

# Request trending playlists
trending_response = requests.get(trending_url, headers=trending_headers)
trending_data = trending_response.json()

# Set up the Spotify API client
client_credentials_manager = SpotifyClientCredentials(client_id=SPOTIFY_ID, client_secret=SPOTIFY_SECRET)
sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)









data = []

# Extract playlist details and songs
if 'playlists' in trending_data:
    playlists = trending_data['playlists']['items']
    day_index = 1
    for playlist in playlists:
        playlist_index = 1  # Initialize the playlist index
        playlist_name = playlist['name']
        playlist_uri = playlist['uri']

        # Retrieve playlist tracks
        playlist_tracks_url = f'https://api.spotify.com/v1/playlists/{playlist_uri.split(":")[2]}/tracks'
        playlist_tracks_response = requests.get(playlist_tracks_url, headers=trending_headers)
        playlist_tracks_data = playlist_tracks_response.json()

        if 'items' in playlist_tracks_data:
            tracks = playlist_tracks_data['items']
            for track in tracks:
                track_name = track['track']['name']
                track_artist = track['track']['artists'][0]['name']
                track_album = track['track']['album']['name']
                # track_duration_ms = track['track']['duration_ms']
                # track_release_date = track['track']['album']['release_date']
                track_added_at = track['added_at']
                track_popularity = track['track']['popularity']
                track_id = track['track']['id']
                track_artist_id = track['track']['artists'][0]['id']
                track_album_id = track['track']['album']['id']

                # Convert duration from milliseconds to minutes:seconds format
                # track_duration = f'{int(track_duration_ms / 3600000):02d}:{int((track_duration_ms / 60000) % 60):02d}:{int((track_duration_ms / 1000) % 60):02d}'
                
                # # Parse release date and format as YYYY-MM-DD
                # if len(track_release_date) == 4:
                #     # Only year provided, append "-01-01" to make it YYYY-MM-DD
                #     release_date_formatted = f'{track_release_date}-01-01'
                # else:
                #     release_date_formatted = datetime.datetime.strptime(track_release_date, "%Y-%m-%d").date().isoformat()

                # Convert 'Added At' to 'YYYY-MM-DD HH:MM:SS' format
                # added_at_datetime = datetime.datetime.strptime(track_added_at, "%Y-%m-%dT%H:%M:%SZ")
                added_at_datetime = datetime.strptime(track_added_at, "%Y-%m-%dT%H:%M:%SZ")
                added_at_formatted = added_at_datetime.strftime("%Y-%m-%d %H:%M:%S")

                # Append playlist and song data
                data.append({
                    'day_index': day_index,
                    'playlist_name': playlist_name,
                    'playlist_ranking': playlist_index,
                    'playlist_uri': playlist_uri,
                    'artist': track_artist,
                    'artist_id': track_artist_id,
                    'album': track_album,
                    'album_id': track_album_id,
                    'track_name': track_name,
                    'track_id': track_id,
                    'track_added_playlist_at': added_at_formatted,
                    'track_popularity': track_popularity,
                    'collected_dt': now.strftime("%Y-%m-%d %H:%M:%S"),
                    'collected_date': now.strftime("%Y-%m-%d")
                })

                playlist_index += 1 
                day_index += 1

playlist_df = pd.DataFrame(data)
# playlist_df

print_and_append(f"df created: {len(playlist_df) = }")





conn = mysql.connector.connect(**config)
cursor = conn.cursor()

import_fail_count = 0
import_success_count = 0
table_name = 'spotify_playlists'
insert_query = """
    INSERT INTO {} (day_index, playlist_name, playlist_ranking, playlist_uri, artist, artist_id, album, album_id, \
    track_name, track_id, track_added_playlist_at, track_popularity, collected_dt, collected_date)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """.format(table_name)
for r in playlist_df.itertuples(index=False):
    values = (r.day_index, r.playlist_name, r.playlist_ranking, r.playlist_uri, r.artist, r.artist_id, r.album, r.album_id, \
    r.track_name, r.track_id, r.track_added_playlist_at, r.track_popularity, r.collected_dt, r.collected_date)
    try:
        cursor.execute(insert_query, values)
        conn.commit()
        import_success_count += 1
    except:
        import_fail_count += 1

print_and_append(f"{table_name}: {import_success_count = }, {import_fail_count = }")

cursor.close()
conn.close()












def get_track_metadata_bulk(track_ids, batch_size=50):
    # Drop duplicates in the track IDs list
    track_ids = list(set(track_ids))

    try:
        # Split track IDs into batches
        batches = [track_ids[i:i + batch_size] for i in range(0, len(track_ids), batch_size)]
        
        metadata = []
        
        for batch in batches:
            # Retrieve track information in bulk
            track_info = sp.tracks(batch)['tracks']
            
            for info in track_info:
                # Retrieve audio features of the track
                audio_features = sp.audio_features(info['id'])[0]
    
                track_duration_ms = audio_features['duration_ms']
                track_duration = f'{int(track_duration_ms / 3600000):02d}:{int((track_duration_ms / 60000) % 60):02d}:{int((track_duration_ms / 1000) % 60):02d}'
                
                # Parse release date and format as YYYY-MM-DD
                track_release_date = info['album']['release_date']
                if len(track_release_date) == 4:
                    # Only year provided, append "-01-01" to make it YYYY-MM-DD
                    release_date_formatted = f'{track_release_date}-01-01'
                else:
                    release_date_formatted = datetime.strptime(track_release_date, "%Y-%m-%d").date().isoformat()

                # Create a dictionary with track metadata
                track_metadata = {
                    'track_id': info['id'],
                    'track_name': info['name'],
                    'track_release_date': release_date_formatted,
                    'track_release_date_precision': info['album']['release_date_precision'],
                    'danceability': audio_features['danceability'],
                    'energy': audio_features['energy'],
                    'trk_key': audio_features['key'],
                    'loudness': audio_features['loudness'],
                    'trk_mode': audio_features['mode'],
                    'speechiness': audio_features['speechiness'],
                    'acousticness': audio_features['acousticness'],
                    'instrumentalness': audio_features['instrumentalness'],
                    'liveness': audio_features['liveness'],
                    'valence': audio_features['valence'],
                    'tempo': audio_features['tempo'],
                    'duration': track_duration,
                    'time_signature': audio_features['time_signature'],
                    'collected_dt': now.strftime("%Y-%m-%d %H:%M:%S"),
                    'collected_date': now.strftime("%Y-%m-%d")
                }
    
                metadata.append(track_metadata)
        
        # Convert the list of dictionaries to a pandas DataFrame
        df = pd.DataFrame(metadata)
        
        return df
    
    except spotipy.exceptions.SpotifyException:
        print("Unable to connect to the Spotify API.")

# track_ids = df['track_id'].to_list()[0:10]  # Replace with a list of track IDs

track_ids = playlist_df['track_id'].to_list()
track_df = get_track_metadata_bulk(track_ids)
# track_df

print_and_append(f"df created: {len(track_df) = }")


conn = mysql.connector.connect(**config)
cursor = conn.cursor()

import_fail_count = 0
import_success_count = 0
table_name = 'spotify_tracks'
insert_query = """
    INSERT INTO {} (track_id, track_name, track_release_date, track_release_date_precision, danceability, energy, trk_key, loudness, \
trk_mode, speechiness, acousticness, instrumentalness, liveness, valence, tempo, duration, time_signature, collected_dt, collected_date)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """.format(table_name)
for r in track_df.itertuples(index=False):
    values = (r.track_id, r.track_name, r.track_release_date, r.track_release_date_precision, r.danceability, r.energy, r.trk_key, r.loudness, \
r.trk_mode, r.speechiness, r.acousticness, r.instrumentalness, r.liveness, r.valence, r.tempo, r.duration, r.time_signature, r.collected_dt, r.collected_date)
    try:
        cursor.execute(insert_query, values)
        conn.commit()
        import_success_count += 1
    except:
        import_fail_count += 1

print_and_append(f"{table_name}: {import_success_count = }, {import_fail_count = }")

cursor.close()
conn.close()













def get_artist_info(artist_ids, batch_size=50):

    metadata = []

    try:
        if isinstance(artist_ids, str):
            # Single artist ID provided
            artist_ids = [artist_ids]  # Convert to list for consistency

        # Drop duplicates in the artist IDs list
        artist_ids = list(set(artist_ids))

        # Split artist IDs into batches
        batches = [artist_ids[i:i + batch_size] for i in range(0, len(artist_ids), batch_size)]

        for batch in batches:
            # Retrieve artist information in bulk
            artists = sp.artists(batch)['artists']

            for artist in artists:
                artist_id = artist['id']
                genre = artist['genres'][0] if artist['genres'] else None

                # Create a dictionary with artist info
                artist_info = {
                    'artist_id': artist_id,
                    'artist_name': artist['name'],
                    'genre': genre,
                    'followers': artist['followers']['total'],
                    'popularity': artist['popularity'],
                    'collected_dt': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'collected_date': datetime.now().strftime("%Y-%m-%d")
                }

                metadata.append(artist_info)

                # time.sleep(0.25)

        # Convert the list of dictionaries to a pandas DataFrame
        df = pd.DataFrame(metadata)
        return df

    except spotipy.exceptions.SpotifyException:
        print("Unable to connect to the Spotify API.")

artist_ids = playlist_df['artist_id'].to_list()
artist_df = get_artist_info(artist_ids)
# artist_df


print_and_append(f"df created: {len(artist_df) = }")


conn = mysql.connector.connect(**config)
cursor = conn.cursor()

replace_fail_count = 0
import_success_count = 0
table_name = 'spotify_artists'
insert_query = """
    REPLACE INTO {} (artist_id, artist_name, genre, followers, popularity, collected_dt, collected_date)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    """.format(table_name)
for r in artist_df.itertuples(index=False):
    values = (r.artist_id, r.artist_name, r.genre, r.followers, r.popularity, r.collected_dt, r.collected_date)
    try:
        cursor.execute(insert_query, values)
        conn.commit()
        import_success_count += 1
    except:
        import_fail_count += 1

print_and_append(f"{table_name}: {import_success_count = }, {import_fail_count = }")

cursor.close()
conn.close()




# TODO
# artists more data
# album more data








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

