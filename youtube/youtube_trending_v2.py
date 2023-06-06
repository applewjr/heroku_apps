import pandas as pd
from datetime import datetime
import mysql.connector
import os
# import logging

from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime
import pytz


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
        'database': secret_pass.mysql_bd,
        'raise_on_warnings': True
        }
    YOUTUBE_API = secret_pass.YOUTUBE_API





def get_trending_videos(YOUTUBE_API):
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API)

    request = youtube.videos().list(
        part='snippet,statistics,status',
        chart='mostPopular',
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
        view_count = item['statistics']['viewCount']
        uploaded_date = item['snippet']['publishedAt']
        # channel_subscribers = item['statistics']['subscriberCount']
        like_count = item['statistics']['likeCount']
        comment_count = item['statistics']['commentCount']
        cat_id = item['snippet']['categoryId']
        vid_id = item['id']

        channel_id = item['snippet']['channelId']
        channel_request = youtube.channels().list(
            part='statistics',
            id=channel_id
        )
        channel_response = channel_request.execute()
        channel_subscribers = channel_response['items'][0]['statistics']['subscriberCount']
        channel_views = channel_response['items'][0]['statistics']['viewCount']
        channel_videos = channel_response['items'][0]['statistics']['videoCount']

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


videos_df = get_trending_videos(YOUTUBE_API)
print(f"df created: {len(videos_df) = }")










conn = mysql.connector.connect(**config)

# Create a cursor object
cursor = conn.cursor()

# Define the table name
table_name = 'youtube_trending'

# Create the INSERT query
insert_query = """
    INSERT INTO {} (video, chnl, vid_rank, vid_views, vid_likes, vid_comments, vid_cat_id, \
        vid_uploaded_dt, chnl_subs, chnl_views, chnl_video_count, collected_dt, collected_date, \
        vid_id, chnl_id)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """.format(table_name)

# Iterate over the rows of the DataFrame and insert the data
for r in videos_df.itertuples(index=False):
    values = (r.video, r.chnl, r.vid_rank, r.vid_views, r.vid_likes, r.vid_comments, r.vid_cat_id, \
        r.vid_uploaded_dt, r.chnl_subs, r.chnl_views, r.chnl_video_count, r.collected_dt, r.collected_date, \
        r.vid_id. r.chnl_id)
    try:
        cursor.execute(insert_query, values)
        conn.commit()
    except:
        print(f"Duplicate entry. Commit not run on: rank {r.vid_rank} - {r.video}")

# Close the cursor and the database connection
cursor.close()
conn.close()

print("youtube_trending data insert complete")


