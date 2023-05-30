import pandas as pd
from datetime import datetime
import mysql.connector
import os

from selenium.webdriver.chrome.service import Service
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager


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


YOUTUBE_TRENDING_URL = 'https://www.youtube.com/feed/trending'

def get_driver():
    chrome_options = Options()
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-dev-shm-usage')
    # chrome_options.add_argument('--disable-gpu')
    # chrome_options.add_argument('--disable-features=Permissions-Policy')
    driver_service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=driver_service, options=chrome_options)
    return driver

def get_videos(driver):
    driver.get(YOUTUBE_TRENDING_URL)
    VIDEO_DIV_TAG = "ytd-video-renderer"
    videos = driver.find_elements(By.TAG_NAME, VIDEO_DIV_TAG)
    return videos

def parse_video(video):
    title_tag = video.find_element(By.ID,'video-title')
    title = title_tag.text

    channel_tag = video.find_element(By.ID, 'channel-name')
    channel_name = channel_tag.text

    metadata_tag = video.find_element(By.ID, 'metadata-line').text
    metadata_tag = metadata_tag.splitlines()

    views = metadata_tag[0]
    uploaded = metadata_tag[1]
    return {
        'title': title,
        'channel': channel_name,
        'views': views,
        'uploaded': uploaded
    }

driver = get_driver()
videos = get_videos(driver)
video_data = [parse_video(video) for video in videos[:10]]

videos_df = pd.DataFrame(video_data)

current_datetime = datetime.now()
formatted_datetime = current_datetime.strftime('%Y-%m-%d %H:%M:%S')
videos_df['datetime'] = formatted_datetime

videos_df['date'] = datetime.today().date()

videos_df['ranking'] = videos_df.reset_index().index + 1
# videos_df






conn = mysql.connector.connect(**config)

# Create a cursor object
cursor = conn.cursor()

# Define the table name
table_name = 'youtube_trending_exp'

# Create the INSERT query
insert_query = """
    INSERT INTO {} (title, channel, views, uploaded, datetime, date, ranking)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    """.format(table_name)

# Iterate over the rows of the DataFrame and insert the data
for row in videos_df.itertuples(index=False):
    values = (row.title, row.channel, row.views, row.uploaded, row.datetime, row.date, row.ranking)
    try:
        cursor.execute(insert_query, values)
        conn.commit()
    except:
        print(f"Duplicate entry. Commit not run on: {row.title}")

# Close the cursor and the database connection
cursor.close()
conn.close()

print("youtube_trending_exp data insert complete")