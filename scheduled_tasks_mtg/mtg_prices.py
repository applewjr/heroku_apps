import pandas as pd
import tweepy
from datetime import datetime
import os
import smtplib
from email.mime.text import MIMEText

pd.options.mode.chained_assignment = None  # default='warn'


gmail_sender_email = 'james.r.applewhite@gmail.com'
gmail_receiver_email = 'james.r.applewhite@gmail.com'
gmail_subject = 'mtg_prices.py'
MAX_NAME_LEN = 20


def load_config():
    if 'IS_HEROKU' in os.environ:
        # Running on Heroku, load values from Heroku Config Vars
        consumer_key = os.environ.get('tw_consumer_key')
        consumer_secret = os.environ.get('tw_consumer_secret')
        access_token = os.environ.get('tw_access_token')
        access_token_secret = os.environ.get('tw_access_token_secret')
        bearer_token = os.environ.get('tw_bearer_token')
        GMAIL_PASS = os.environ.get('GMAIL_PASS')
        MTG_PATH = os.environ.get('MTG_PATH')
    else:
        # Running locally, load values from secret_pass.py
        import sys
        script_directory = os.path.dirname(os.path.abspath(__file__))
        root_directory = os.path.dirname(script_directory)
        sys.path.append(root_directory)
        import secret_pass
        consumer_key = secret_pass.consumer_key
        consumer_secret = secret_pass.consumer_secret
        access_token = secret_pass.access_token
        access_token_secret = secret_pass.access_token_secret
        bearer_token = secret_pass.bearer_token
        GMAIL_PASS = secret_pass.GMAIL_PASS
        MTG_PATH = secret_pass.MTG_PATH
    return consumer_key, consumer_secret, access_token, access_token_secret, bearer_token, GMAIL_PASS, MTG_PATH

def get_tweepy_client(consumer_key, consumer_secret, access_token, access_token_secret, bearer_token):
    api = tweepy.Client(
        bearer_token=bearer_token,
        access_token=access_token,
        access_token_secret=access_token_secret,
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
    )
    return api

def post_tweet(api, update):
    api.create_tweet(text=update)

def send_email(gmail_sender_email, gmail_receiver_email, gmail_subject, gmail_message, GMAIL_PASS):
    msg = MIMEText(gmail_message)
    msg['Subject'] = gmail_subject
    msg['From'] = gmail_sender_email
    msg['To'] = gmail_receiver_email
    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(gmail_sender_email, GMAIL_PASS)
        server.sendmail(gmail_sender_email, gmail_receiver_email, msg.as_string())
        print('Email sent')

def get_price_change_summary(df, diff_col, weeks, ascending, max_name_len):
    df_sorted = df.sort_values(by=diff_col, ascending=ascending)
    name = df_sorted['name'].iloc[0]
    if len(name) > max_name_len:
        name = name[:max_name_len] + "..."
    summary = f'{weeks}: {name} ${df_sorted[f"{weeks.lower()}_ago_price"].iloc[0]} \u27F6 {df_sorted["today_price"].iloc[0]} {df_sorted["tcgplayer_id"].iloc[0]}'
    return summary

# Load configuration
(
    consumer_key,
    consumer_secret,
    access_token,
    access_token_secret,
    bearer_token,
    GMAIL_PASS,
    MTG_PATH,
) = load_config()

# Get tweepy client
api = get_tweepy_client(
    consumer_key,
    consumer_secret,
    access_token,
    access_token_secret,
    bearer_token,
)

# read in data
df = pd.read_csv(MTG_PATH)
df = df.dropna(subset=['tcgplayer_id', 'name', 'set_name'])

# Get current date
name = df['today_price_date'].iloc[0]
current_date = datetime.strptime(name, '%Y-%m-%d').strftime('%m/%d')
current_date


# Price increase summaries
increase_1wk = get_price_change_summary(df, '1wk_diff', '1Wk', ascending=False, max_name_len=MAX_NAME_LEN)
increase_2wk = get_price_change_summary(df, '2wk_diff', '2Wk', ascending=False, max_name_len=MAX_NAME_LEN)
increase_4wk = get_price_change_summary(df, '4wk_diff', '4Wk', ascending=False, max_name_len=MAX_NAME_LEN)

# Price decrease summaries
decrease_1wk = get_price_change_summary(df, '1wk_diff', '1Wk', ascending=True, max_name_len=MAX_NAME_LEN)
decrease_2wk = get_price_change_summary(df, '2wk_diff', '2Wk', ascending=True, max_name_len=MAX_NAME_LEN)
decrease_4wk = get_price_change_summary(df, '4wk_diff', '4Wk', ascending=True, max_name_len=MAX_NAME_LEN)


# Post MTG decrease
tweet_text_decrease =f"""
#MagicTheGathering - Top Price Decreases ({current_date})

{decrease_4wk}
{decrease_2wk}
{decrease_1wk}

https://www.jamesapplewhite.com/mtg"""

post_tweet(api, tweet_text_decrease)


# Post MTG increase
tweet_text_increase =f"""
#MagicTheGathering - Top Price Increases ({current_date})

{increase_4wk}
{increase_2wk}
{increase_1wk}

https://www.jamesapplewhite.com/mtg"""

post_tweet(api, tweet_text_increase)


# # Send email
gmail_message = '\n'.join(['mtg tweet is complete', tweet_text_decrease, tweet_text_increase])
send_email(
    gmail_sender_email,
    gmail_receiver_email,
    gmail_subject,
    gmail_message,
    GMAIL_PASS,
)
