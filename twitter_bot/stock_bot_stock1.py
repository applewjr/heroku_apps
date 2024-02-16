import yfinance as yf
import pandas as pd
import numpy as np
import time
import tweepy
import datetime
from datetime import datetime
import os
import pytz
import smtplib
from email.mime.text import MIMEText
pd.options.mode.chained_assignment = None  # default='warn'

if 'IS_HEROKU' in os.environ:
    # Running on Heroku, load values from Heroku Config Vars
    consumer_key = os.environ.get('tw_consumer_key')
    consumer_secret = os.environ.get('tw_consumer_secret')
    access_token = os.environ.get('tw_access_token')
    access_token_secret = os.environ.get('tw_access_token_secret')
    bearer_token = os.environ.get('tw_bearer_token')
    GMAIL_PASS = os.environ.get('GMAIL_PASS')
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

api = tweepy.Client(bearer_token=bearer_token,
    access_token=access_token,
    access_token_secret=access_token_secret,
    consumer_key=consumer_key,
    consumer_secret=consumer_secret)

current_time_utc = datetime.utcnow()
pst = pytz.timezone('US/Pacific')
current_time_pst = current_time_utc.replace(tzinfo=pytz.utc).astimezone(pst)

file_ymd = current_time_pst.strftime('%Y%m%d')
text_ymd = current_time_pst.strftime('%Y-%m-%d')
today = pd.to_datetime(current_time_pst.strftime('%Y-%m-%d'))
text_ymdt = current_time_pst.strftime('%Y-%m-%d %H:%M:%S')

gmail_sender_email = 'james.r.applewhite@gmail.com'
gmail_receiver_email = 'james.r.applewhite@gmail.com'
gmail_subject = 'stock_bot_stock1.py'
gmail_list = []

def print_and_append(statement):
    print(statement)
    gmail_list.append(statement)



######################################
######################################
##### segment_name = master value assignments
######################################
######################################

df_policy = pd.DataFrame({'fund': ['AAPL', 'AMD', 'AMZN', 'GOOG', 'INTC', 'MSFT', 'NVDA', 'TSLA', 'BTC-USD', 'ETH-USD', 'BNB-USD', 'DOGE-USD'], \
    'amt': [10.00, 10.00, 10.00, 10.00, 10.00, 10.00, 10.00, 10.00, 10.00, 10.00, 10.00, 10.00]})
df_policy = df_policy.set_index('fund')
df_policy = df_policy[['amt']]
dict_policy = df_policy.to_dict()

######################################
######################################
##### stock1 (Export to Twitter)
######################################
######################################

stock_list = ['AAPL', 'AMD', 'GOOG', 'INTC', 'NVDA', 'TSLA']
contrib_amt = [dict_policy['amt'][amt] for amt in stock_list]
trade_type = 'stock'
roll_days_base = 'quarter'
buyvalue = 1.2
multiplier = 3
segment_name = 'stock1'

# stock_list = ['BTC-USD', 'ETH-USD', 'BNB-USD', 'DOGE-USD'] # just for weekend testing. not for prod deploy
# contrib_amt = [dict_policy['amt'][amt] for amt in stock_list]
# trade_type = 'crypto'
# roll_days_base = 'quarter'
# buyvalue = 1.2
# multiplier = 3
# segment_name = 'crypto1'

# convert roll days into the proper number, with respect to stock/index vs crypto
roll_stock_index = {'month': 21, 'quarter': 65, '2_quarter': 130, 'year': 260}
roll_crypto = {'month': 30, 'quarter': 90, '2_quarter': 180, 'year': 365}
roll_dict = {'stock': roll_stock_index, 'index': roll_stock_index, 'crypto': roll_crypto}

roll_days = roll_dict[trade_type][roll_days_base]

### duplicate contrib_amt for all stocks if only 1 listed
if len(contrib_amt) == len(stock_list):
    pass
elif len(contrib_amt) == 1: 
    contrib_amt = [contrib_amt[0] for x in enumerate(stock_list)]
else:
    print_and_append('Incorrect length of contrib_amt. Make it match the length of the stock list or be 1 value')
    exit()

### pull most recent day
sleep_count = 0
if trade_type == 'crypto' or trade_type == 'index':
    pass
else:
    x = 0
    while x < 1:
        df_now = yf.download(
        tickers = stock_list
        ,period = '1d' # set for 'today' instead
        ,interval = '1m'
        )

        # ensures a single stock can pass through, not just 2+ 
        if len(stock_list) == 1:
            df_now[stock_list[0]] = df_now['Open']
            df_now = df_now[[stock_list[0]]]
        else:
            df_now = df_now['Open']

        df_now = df_now.head(1) # open for today
        df_now = df_now.fillna(0)

        x = 1
        for i in stock_list:
            x = x * int(df_now[i])

        if x == 0: # wait 15 seconds if data aren't complete
            time.sleep(15)
            sleep_count += 1

# Overly complex way to pull data, but I have found that 'Open' prices are just a 
# copy of the previous day for the first few minutes of the trading day
# This method pulls in the true Open prices for today much quicker (a couple minutes after 6:30am PST)

if trade_type == 'crypto' or trade_type == 'index':
    df = yf.download(
        tickers = stock_list
        ,period = str(roll_days) + 'd'
    )

    # ensures a single crypto or index can pass through, not just 2+ 
    if len(stock_list) == 1:
        df[stock_list[0]] = df['Open']
        df = df[[stock_list[0]]]
    else:
        df = df['Open']
else:
    # Pull all data except for today
    df_bulk = yf.download(
            tickers = stock_list
            ,period = str(roll_days) + 'd'
        )

    # ensures a single stock can pass through, not just 2+ 
    if len(stock_list) == 1:
        df_bulk[stock_list[0]] = df_bulk['Open']
        df_bulk = df_bulk[[stock_list[0]]]
    else:
        df_bulk = df_bulk['Open']

    df_good_index = df_bulk.copy() # used to grab the ideal index
    df_bulk.drop(df_bulk.tail(1).index,inplace=True) # bulk w/o the most recent day

    # join the data (index is still bad)
    df = pd.concat([df_bulk, df_now])

    # sub in a good index
    df = df.reindex_like(df_good_index)

    # sub in good open data for today
    for i in stock_list:
        df[i][len(df)-1] = df_now[i].copy()
    

# add an index and useable date
df['Index'] = np.arange(1,len(df)+1)
df['date'] = df.index

# error checking, if a stock doesn't have enough history based on the current needs
nlist = []
for i in stock_list:
    if pd.isna(df[i].iloc[0]) == True:
        nlist.append(i)

if len(nlist) >0:
    print_and_append(f"Stocks with not enough history {nlist}")
    for j in nlist:
        print_and_append(f"{j} missing days: {df['Index'].count()-df[j].count()}")
    exit() # Maybe not the best to add this. I still want to see the data

# create pred and pred/open list for each of the n dataframes
pred_open_list = []
for j in stock_list:
    x = range(1,len(df[j])+1) # range must be 1-roll_days, not the auto implied 0-(roll_days-1)
    y = df[j]
    m, b = np.polyfit(x, y, 1)
    d = m*len(df[j])+b

    pred_open_list.append(d / df[j][len(df[j])-1] * d / df[j][len(df[j])-1])

multiplier_list = []
for i, j in enumerate(stock_list):
    if pred_open_list[i] > buyvalue:
        multiplier_list.append(1)
    else:
        multiplier_list.append(0)

final_buy_list = []
for i, j in enumerate(stock_list):
    if multiplier_list[i] == 0:
        final_buy_list.append(contrib_amt[i])
    else:
        final_buy_list.append(round(contrib_amt[i]*pred_open_list[i]*multiplier, 2))

trade_day_date = df.tail(1)['date'].item().strftime('%Y.%m.%d')

stocks = []
for i, j, k, m in zip(stock_list, final_buy_list, pred_open_list, contrib_amt):
    if j == m:
        stocks.append(f'\n{i} ({round(k, 2)}): {m}')
    else:
        stocks.append(f'\n{i} ({round(k, 2)}): {m} + {round(j-m,2)}')

file_ymdt = file_ymd + '_' + datetime.now().strftime('%H%M%S')
text_ymdt = text_ymd + ' ' + datetime.now().strftime('%H:%M:%S')

update = (f"{text_ymdt} ({today.strftime('%a')})\nRoll Hist Days = {roll_days}, Pred/Open^2 Threshold = {buyvalue}, Multiplier = {multiplier}\nStk (Pred/Open^2): Buy Value{(''.join(str(a) for a in stocks))}")

# exports
if df['date'][len(df)-1] == today:

    api.create_tweet(text=update)

    print_and_append(f'{segment_name} complete')
    print_and_append(update)

else:
    print_and_append(f'{segment_name} not open (most recent date pull != today)')

print_and_append(f'')
print_and_append(f'{sleep_count = }')
print_and_append(f'')

total_weeks_val = 52
for stock, contrib_amt in zip(stock_list, contrib_amt):
    print_and_append(stock)
    print_and_append(f"https://www.jamesapplewhite.com/stock_analysis?stock_list_init_val={stock}&trade_type_val={trade_type}&contrib_amt_init_val={int(contrib_amt)}&total_weeks_val={total_weeks_val}&buyvalue_val={buyvalue}&multiplier_val={multiplier}&nth_week_val=1&roll_days_val={roll_days_base}&trade_dow_val={datetime.now().strftime('%A')}")
    print_and_append("")

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