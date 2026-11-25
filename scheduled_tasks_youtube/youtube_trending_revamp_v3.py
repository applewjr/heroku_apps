import pandas as pd
from datetime import datetime
import mysql.connector
from mysql.connector import errorcode
import os
from googleapiclient.discovery import build
import pytz
# import logging

# No email since 2026-11-25. A run that fails raises, which run_job turns into
# an alert; a run that never happened, or that landed too few rows, is caught
# by the load checks in datasets/health_checks.yaml.


if 'IS_HEROKU' in os.environ:
    # Running on Heroku, load values from Heroku Config Vars
    config = {
        'user': os.environ.get('jawsdb_user'),
        'password': os.environ.get('jawsdb_pass'),
        'host': os.environ.get('jawsdb_host'),
        'database': os.environ.get('jawsdb_db'),
        'raise_on_warnings': True
        }
    YOUTUBE_API = os.environ.get('YOUTUBE_API')
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
    YOUTUBE_API = secret_pass.YOUTUBE_API



def get_trending_videos(YOUTUBE_API, category_id):
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API)

    request = youtube.videos().list(
        part='snippet,statistics,status',
        chart='mostPopular',
        videoCategoryId=category_id,
        maxResults=50
    )
    response = request.execute()

    video_data = []
    utc = pytz.UTC
    pst = pytz.timezone('America/Los_Angeles')
    now = datetime.now(pst)

    for index, item in enumerate(response['items'], start=1):
        video_title = item['snippet']['title']
        channel_title = item['snippet']['channelTitle']
        try:
            view_count = item['statistics']['viewCount']
        except:
            view_count = -1
        uploaded_date = item['snippet']['publishedAt']
        try:
            like_count = item['statistics']['likeCount']
        except:
            like_count = -1
        try:
            comment_count = item['statistics']['commentCount']
        except:
            comment_count = -1
        cat_id = item['snippet']['categoryId']
        vid_id = item['id']

        channel_id = item['snippet']['channelId']
        channel_request = youtube.channels().list(
            part='statistics',
            id=channel_id
        )
        channel_response = channel_request.execute()
        try:
            channel_subscribers = channel_response['items'][0]['statistics']['subscriberCount']
        except:
            channel_subscribers = -1
        try:
            channel_views = channel_response['items'][0]['statistics']['viewCount']
        except:
            channel_views = -1
        try:
            channel_videos = channel_response['items'][0]['statistics']['videoCount']
        except:
            channel_videos = -1

        # Convert uploaded_date from UTC to PST
        uploaded_date = datetime.strptime(uploaded_date, "%Y-%m-%dT%H:%M:%SZ")
        uploaded_date = utc.localize(uploaded_date)
        uploaded_date = uploaded_date.astimezone(pst)
        uploaded_date = uploaded_date.strftime("%Y-%m-%d %H:%M:%S")

        video_data.append({
            'video': video_title,
            'chnl': channel_title,
            'vid_rank': index,
            'vid_views': view_count,
            'vid_likes': like_count,
            'vid_comments': comment_count,
            'vid_cat_id': cat_id,
            'vid_uploaded_dt': uploaded_date,
            'chnl_subs': channel_subscribers,
            'chnl_views': channel_views,
            'chnl_video_count': channel_videos,
            'collected_dt': now.strftime("%Y-%m-%d %H:%M:%S"),
            'collected_date': now.strftime("%Y-%m-%d"),
            'vid_id': vid_id,
            'chnl_id': channel_id
        })

    videos_df = pd.DataFrame(video_data)
    return videos_df


# Get trending videos from each category
videos_df_now = get_trending_videos(YOUTUBE_API, 0)
videos_df_now['trending_type'] = 'Now'

videos_df_music = get_trending_videos(YOUTUBE_API, 10)
videos_df_music['trending_type'] = 'Music'

videos_df_gaming = get_trending_videos(YOUTUBE_API, 20)
videos_df_gaming['trending_type'] = 'Gaming'

# Concatenate all 3 dataframes
videos_df = pd.concat([videos_df_now, videos_df_music, videos_df_gaming], ignore_index=True)

print(f"df created: {len(videos_df) = }")
print(f"Now: {len(videos_df_now)} videos")
print(f"Music: {len(videos_df_music)} videos")
print(f"Gaming: {len(videos_df_gaming)} videos")
print(f"Total: {len(videos_df)} videos")



conn = mysql.connector.connect(**config)

# Create a cursor object
cursor = conn.cursor()

# Define the table name
table_name = 'youtube_trending_revamp'

# Create the INSERT query
insert_query = """
    INSERT INTO {} (video, chnl, vid_rank, vid_views, vid_likes, vid_comments, vid_cat_id, \
        vid_uploaded_dt, chnl_subs, chnl_views, chnl_video_count, collected_dt, collected_date, \
        vid_id, chnl_id, trending_type)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """.format(table_name)

# Iterate over the rows of the DataFrame and insert the data. A row that is
# already there is a second run on the same day, which is fine. Any other
# failure is kept for the end, so one bad row doesn't cost the rest.
inserted = duplicates = 0
failed = []
for r in videos_df.itertuples(index=False):
    values = (r.video, r.chnl, r.vid_rank, r.vid_views, r.vid_likes, r.vid_comments, r.vid_cat_id, \
        r.vid_uploaded_dt, r.chnl_subs, r.chnl_views, r.chnl_video_count, r.collected_dt, r.collected_date, \
        r.vid_id, r.chnl_id, r.trending_type)
    try:
        cursor.execute(insert_query, values)
        conn.commit()
        inserted += 1
    except mysql.connector.Error as e:
        if e.errno == errorcode.ER_DUP_ENTRY:
            duplicates += 1
        else:
            # Logged below, and a title is anyone's text: the alert words are
            # broken in it, as monitoring/alerts.py breaks them.
            failed.append(f"{r.trending_type} rank {r.vid_rank} - {r.video}: {e}".replace("JJ_", "JJ."))

# Close the cursor and the database connection
cursor.close()
conn.close()

print(f"youtube_trending_revamp: {inserted} rows inserted, {duplicates} already there, {len(failed)} failed")
for line in failed:
    print(line)
if failed:
    raise RuntimeError(f"{len(failed)} of {len(videos_df)} rows failed. First: {failed[0]}")
