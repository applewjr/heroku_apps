import yfinance as yf
import pandas as pd
import numpy as np
import time
import itertools
import tweepy
import datetime
from datetime import date
from datetime import datetime
import calendar
import random
import math
import matplotlib.pyplot as plt
import os
pd.options.mode.chained_assignment = None  # default='warn'

file_ymd = str(date.today().year) + str(date.today().month).zfill(2) + str(date.today().day).zfill(2)
text_ymd = str(date.today().year) + '-' + str(date.today().month).zfill(2) + '-' + str(date.today().day).zfill(2)

if 'IS_HEROKU' in os.environ:
    # Running on Heroku, load values from Heroku Config Vars
    consumer_key = os.environ.get('tw_consumer_key')
    consumer_secret = os.environ.get('tw_consumer_secret')
    access_token = os.environ.get('tw_access_token')
    access_token_secret = os.environ.get('tw_access_token_secret')
else:
    # Running locally, load values from secret_pass.py
    import secret_pass
    consumer_key = secret_pass.consumer_key
    consumer_secret = secret_pass.consumer_secret
    access_token = secret_pass.access_token
    access_token_secret = secret_pass.access_token_secret






auth = tweepy.OAuthHandler(consumer_key, consumer_secret)
auth.set_access_token(access_token, access_token_secret)
api = tweepy.API(auth, wait_on_rate_limit = True)

today = pd.to_datetime(date.today())

file_ymdt = file_ymd + '_' + datetime.now().strftime('%H%M%S')
text_ymdt = text_ymd + ' ' + datetime.now().strftime('%H:%M:%S')


api.update_status(f"Testing: {text_ymdt}")










