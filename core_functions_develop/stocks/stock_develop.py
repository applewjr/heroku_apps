import yfinance as yf
import pandas as pd
import numpy as np
import time
import itertools
# import tweepy
import datetime
from datetime import date
from datetime import datetime
import calendar
import random
import math
import matplotlib.pyplot as plt
pd.options.mode.chained_assignment = None  # default='warn'

file_ymd = str(date.today().year) + str(date.today().month).zfill(2) + str(date.today().day).zfill(2)
text_ymd = str(date.today().year) + '-' + str(date.today().month).zfill(2) + '-' + str(date.today().day).zfill(2)

consumer_key = ''
consumer_secret = ''
access_token = ''
access_token_secret = ''

# auth = tweepy.OAuthHandler(consumer_key, consumer_secret)
# auth.set_access_token(access_token, access_token_secret)
# api = tweepy.API(auth, wait_on_rate_limit = True)

today = pd.to_datetime(date.today())




# stock choices

trade_type = 'stock'
    # options, 'stock', 'crypto'
stock_list = ['AAPL'] # core list
contrib_amt = 20
# stock_list1 = ['AAPL', 'AMD', 'AMZN', 'CRM'] # core list
# stock_list2 = ['GOOG', 'INTC', 'MSFT', 'NVDA'] # core list
# stock_list3 = ['ORCL', 'SBUX', 'TSLA', 'TSM'] # core list
total_weeks = 104
multiplier = 5
# segment_name = 'stock graph 1-4'
nth_week = 1
roll_days = 'quarter'
baseline_roll_days = '2_quarter'
trade_dow = 'Today'
buyvalue = 1.2



invest = float('inf')

if trade_type == 'stock' and (today.strftime('%a') == 'Sat' or today.strftime('%a') == 'Sun'):
    pass
else:
    # convert roll days into the proper number, with respect to stock/index vs crypto
    roll_stock_index = {'month': 21, 'quarter': 65, '2_quarter': 130, 'year': 260}
    roll_crypto = {'month': 30, 'quarter': 90, '2_quarter': 180, 'year': 365}
    roll_dict = {'stock': roll_stock_index, 'index': roll_stock_index, 'crypto': roll_crypto}
    roll_days = roll_dict[trade_type][roll_days]
    baseline_roll_days = roll_dict[trade_type][baseline_roll_days]

    # number of years to visualize
    if trade_type == 'crypto':
        day_hist = (total_weeks*7)+baseline_roll_days+1 # choose for crypto ~ trading every day
    else:
        day_hist = (total_weeks*5)+baseline_roll_days+1 # choose for stocks ~ trading about 5 days per week
    # weeks to actually invest on
    invest_weeks = math.floor(total_weeks/nth_week)

    # duplicate contrib_amt for all stocks if only 1 listed
    if len(contrib_amt) == len(stock_list):
        pass
    elif len(contrib_amt) == 1: 
        contrib_amt = [contrib_amt[0] for x in enumerate(stock_list)]
    else:
        print('Incorrect length of contrib_amt. Make it match the length of the stock list or be 1 value')

    # check every 15 seconds for complete data
    # wait times should only happen for ~1-2 minutes after market open on trading days (right after 0630am PST)
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
            # df_now['Open', 'AAPL'] = 0 # force a 0 for testing
            x = 1
            for i in stock_list:
                # x = x * int(df_now['Open'][i])
                x = x * int(df_now[i])
            if x == 0: # wait 15 seconds if data aren't complete
                time.sleep(15)
            else:
                if df_now.index.day == date.today().day:
                    print('Datetime of data available: ', datetime.now().strftime("%B %d, %Y %H:%M:%S"))
                else:
                    print('Warning, today\'s data not yet available')

    # check the traditional open price
    df_open_check = yf.download(
        tickers = stock_list
        # ,start = '2022-01-15'
        # ,end = '2022-01-18'
        ,period = str(day_hist) + 'd'
    )

    ### Overly complex way to pull data, but I have found that 'Open' prices are just a copy of the previous day for the first few minutes of the trading day
    ### This method pulls in the true Open prices for today much quicker (a couple minutes after 6:30am PST)
    if trade_type == 'crypto' or trade_type == 'index':
        df = yf.download(
            tickers = stock_list
            # ,start = '2022-01-15'
            # ,end = '2022-01-18'
            ,period = str(day_hist) + 'd'
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
                # ,start = '2022-01-15'
                # ,end = '2022-01-18'
                ,period = str(day_hist) + 'd'
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
        print('Stocks with not enough history', nlist)
        for j in nlist:
            print(j, 'missing days:', df['Index'].count()-df[j].count())

    # establishing day of week, week number, trading day
    dow_dict = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4, 'Saturday': 5, 'Sunday': 6}
    # convert 'Today' to actual listed day of the week
    if trade_dow == 'Today':
        trade_dow = list(dow_dict.keys())[date.today().weekday()]
    df['dow'] = df['date'].dt.dayofweek
    if trade_type == 'crypto':
        trade_day_list = []
        for i in range(len(df)):
            if df['dow'][i] == dow_dict[trade_dow]:
                trade_day_list.append(1)
            else:
                trade_day_list.append(0)
        df['trade_day'] = trade_day_list
    elif trade_type != 'crypto' and (trade_dow == 'Saturday' or trade_dow == 'Sunday'):
        print('error - stocks not open on the weekend')
    else:
        for i in range(len(df)):
            df['dow_dynamic'] = df['dow']-dow_dict[trade_dow]
        for i in range(len(df)):
            if df['dow_dynamic'][i] < 0:
                df['dow_dynamic'][i] = df['dow_dynamic'][i]+5
        week_no_list = []
        trade_day_list = []
        week_no_var = 1
        trade_day_var = 0
        for i in range(len(df)):
            if i == 0:
                1
            elif df['dow_dynamic'].iloc[i] > df['dow_dynamic'].iloc[i-1]:
                week_no_var
                trade_day_var = 0
            else:
                week_no_var += 1
                trade_day_var = 1
            week_no_list.append(week_no_var)
            trade_day_list.append(trade_day_var)
        df['week_no'] = week_no_list
        df['trade_day'] = trade_day_list

    # make a list of indices that are the Mondays where the trade should take place
    # always starting with the most recent Monday
    df_trade_days = df['Index'][df['trade_day'] == 1]
    df_trade_days = df_trade_days.tail(total_weeks)
    df_trade_days = df_trade_days.tolist()
    def reverse(rev):
        rev.reverse()
        return rev
    df_trade_days = reverse(df_trade_days)
    nth_wk = df_trade_days[::nth_week]

    df_baseline_source = df.copy()

    # create 1 dataframe per invest week in a dictionary, each the length of the chosen invest period
    # 0 is the most recent
    dataframes = {}
    # for j in stock_list:
    for i in range(invest_weeks):
        x = nth_wk[i]-roll_days # roll_days sets how many rows in each df
        y = nth_wk[i]
        dataframes['data' + str(i)] = df.iloc[x:y]

    # create pred and pred/open list for each of the n dataframes
    # sub in -1 for all calc except the last row. Only the last row of each item in the dictionary will be used
    for j in stock_list:
        for e in range(len(dataframes)):
            nlist = []
            ylist = []
            y = dataframes['data' + str(e)][j]
            for i in range(1,len(dataframes['data0'])+1): # create pred
                if i == len(dataframes['data0']):
                    x = range(1,roll_days+1) # range must be 1-roll_days, not the auto implied 0-(roll_days-1)
                    m, b = np.polyfit(x, y, 1)
                    d = m*i+b
                    nlist.append(d)
                else:
                    nlist.append(-1) # Skip calculating every row except the last one. Only the last is used
            dataframes['data' + str(e)][j + ' pred'] = nlist
            for i in range(1,len(dataframes['data0'])+1): # create pred/open
                if i == len(dataframes['data0']):
                    d = (dataframes['data' + str(e)][j + ' pred'].iloc[i-1])/(dataframes['data' + str(e)][j].iloc[i-1])
                    ylist.append(d)
                else:
                    ylist.append(-1) # Skip calculating every row except the last one. Only the last is used
            dataframes['data' + str(e)][j + ' pred/open'] = ylist

    # pull the last 'open' and pred/open' from each dataframe in dataframes and make a new dataframe out of it
    # each row is the last open price in a given period and the final pred/open derived from the linear trendline
    df = pd.DataFrame()
    add_index = np.arange(1,len(dataframes)+1)
    df['Index'] = add_index
    for j in stock_list:
        nlist = []
        ylist = []
        zlist = []
        datelist = []
        for e in reversed(range(len(dataframes))):
            nlist.append(dataframes['data' + str(e)][j + ' pred/open'].iloc[roll_days-1])
            ylist.append(dataframes['data' + str(e)][j].iloc[roll_days-1])
            zlist.append(dataframes['data' + str(e)][j + ' pred'].iloc[roll_days-1])
            datelist.append(dataframes['data' + str(e)]['date'].iloc[roll_days-1])
        df[j] = pd.DataFrame(ylist)
        df[j + ' pred'] = pd.DataFrame(zlist)
        df[j + ' pred/open'] = pd.DataFrame(nlist)
        df['date'] = pd.DataFrame(datelist)

    # dictionary for baseline contrib math
    dataframes_baseline = {}
    # for j in stock_list:
    for i in range(invest_weeks):
        x = nth_wk[i]-baseline_roll_days # roll_days sets how many rows in each df
        y = nth_wk[i]
        dataframes_baseline['data' + str(i)] = df_baseline_source.iloc[x:y].copy()
    for j in stock_list:
        for i in range(invest_weeks):
            dataframes_baseline['data' + str(i)][j + ' norm'] = dataframes_baseline['data' + str(i)][j].copy()/dataframes_baseline['data' + str(i)][j].iloc[0].copy()
    for j in stock_list:
        for e in range(len(dataframes_baseline)):
            nlist = []
            ylist = []
            y = dataframes_baseline['data' + str(e)][j + ' norm']
            for i in range(1,len(dataframes_baseline['data0'])+1):
                if i == len(dataframes_baseline['data0']):
                    x = range(1,baseline_roll_days+1)
                    m, b = np.polyfit(x, y, 1)
                    # d = m*i+b
                    nlist.append(m*1000) # x1000 for easier view. It doesn't change the final math
                else:
                    nlist.append(-1) # Skip calculating every row except the last one. Only the last is used
            dataframes_baseline['data' + str(e)][j + ' slope'] = nlist

    # determine the weeks where pred/open is >1 and therefore they are better weeks to buy in
    # steady stocks could be at about 50/50 but stocks exponentially rising could have open to 0 pred/open > 1
    for j in stock_list:
        nlist = []
        for i in range(len(df)):
            if df[j + ' pred/open'].iloc[i] >= 1:
                nlist.append(1)
            else:
                nlist.append(0)
        df[j + ' >1'] = nlist
    # square the pred/open number as a more extreme option for calculations
    for j in stock_list:
        df[j +' pred/open2'] = df[j +' pred/open']**2 # make the value differences a little more pronounced

    # Create all of the strategies to test 
    for j, z in zip(stock_list, contrib_amt):
    # opt5
        df[j +' opt5'] = 0
        df[j +' opt5_stk'] = 0
        v = invest
        for i in range(len(df)):
            if df[j +' pred/open2'].iloc[i] < buyvalue:
                df[j +' opt5'].iloc[i] = z
            else:
                df[j +' opt5'].iloc[i] = round(z * df[j +' pred/open2'].iloc[i] * multiplier,2)
            df[j +' opt5_stk'].iloc[i] = df[j +' opt5'].iloc[i]/df[j].iloc[i]
            v -= z*df[j +' pred/open2'].iloc[i] ### isn't correct for this algorithm, don't worry about it while I'm going with inf invest
            if i == (len(df)-1):
                t = i
            else:
                t = i+1
            if v < z*df[j +' pred/open2'].iloc[t]:
                break

    file_ymdt = file_ymd + '_' + datetime.now().strftime('%H%M%S')
    # text_ymdt = text_ymd + ' ' + datetime.now().strftime('%H:%M:%S')
    text_ymdt = text_ymd + ' ' + datetime.now().strftime('%H:%M')

    # plot for each stock the open price and the prediction price
    # open (blue) below prediction (orange) = stock is underperforming
    for j in stock_list:
        x = df['date']
        plt.xticks(rotation=90)
        y = df[str(j)]
        plt.plot(x, y)
        x = df['date']
        # plt.xticks(rotation=90)
        y = df[str(j) + ' pred']
        plt.plot(x, y)
        # plt.margins(0.1)
        plt.title(j)
        plt.xlabel(f"{text_ymdt} ({today.strftime('%a')}), Weeks = {total_weeks}\nPred/open^2 = {round(df[j + ' pred/open2'][len(df)-1],4)}, Buy value = {df[j + ' opt5'][len(df)-1]}")
        plt.savefig(f'C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/graphs/{j}.jpg', bbox_inches='tight')
        # plt.show()
        plt.clf()


    ### currently hard coded #####

    # if trade_type == 'stock':
    #     AAPL = api.media_upload('C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/graphs/AAPL.jpg')
    #     AMD = api.media_upload('C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/graphs/AMD.jpg')
    #     AMZN = api.media_upload('C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/graphs/AMZN.jpg')
    #     CRM = api.media_upload('C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/graphs/CRM.jpg')

    #     GOOG = api.media_upload('C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/graphs/GOOG.jpg')
    #     INTC = api.media_upload('C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/graphs/INTC.jpg')
    #     MSFT = api.media_upload('C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/graphs/MSFT.jpg')
    #     NVDA = api.media_upload('C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/graphs/NVDA.jpg')

    #     ORCL = api.media_upload('C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/graphs/ORCL.jpg')
    #     SBUX = api.media_upload('C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/graphs/SBUX.jpg')
    #     TSLA = api.media_upload('C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/graphs/TSLA.jpg')
    #     TSM = api.media_upload('C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/graphs/TSM.jpg')
    # else:
    #     BTC_USD = api.media_upload('C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/graphs/BTC-USD.jpg')

    ##### currently hard coded #####

    # # exports
    # if df['date'][len(df)-1] == today:
    #     # text_file = open(f'C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/export/{segment_name}_{file_ymdt}.txt', 'w')
    #     # text_file.write(update)
    #     # text_file.close()

    #     if trade_type == 'stock':
    #         api.update_status(status = f"{text_ymdt} ({today.strftime('%a')})\n{', '.join(stock_list1)}", media_ids = [AAPL.media_id, AMD.media_id, AMZN.media_id, CRM.media_id])
    #         api.update_status(status = f"{text_ymdt} ({today.strftime('%a')})\n{', '.join(stock_list2)}", media_ids = [GOOG.media_id, INTC.media_id, MSFT.media_id, NVDA.media_id])
    #         api.update_status(status = f"{text_ymdt} ({today.strftime('%a')})\n{', '.join(stock_list3)}", media_ids = [ORCL.media_id, SBUX.media_id, TSLA.media_id, TSM.media_id])
    #     else:
    #         api.update_status(status = f"{text_ymdt} ({today.strftime('%a')})\n{', '.join(stock_list1)}", media_ids = [BTC_USD.media_id])

    #     print(f'{segment_name} complete')

    # else: print(f'{segment_name} not open (most recent date pull != today)')















######################################
######################################
##### stocks part 1 (Export to Twitter, txt)
######################################
######################################


stock_list = ['AAPL', 'AMD', 'AMZN', 'CRM']
contrib_amt = 20
trade_type = 'stock'
roll_days = 'quarter'
buyvalue = 1.2
multiplier = 5
segment_name = 'stock1'

# convert roll days into the proper number, with respect to stock/index vs crypto
roll_stock_index = {'month': 21, 'quarter': 65, '2_quarter': 130, 'year': 260}
roll_crypto = {'month': 30, 'quarter': 90, '2_quarter': 180, 'year': 365}
roll_dict = {'stock': roll_stock_index, 'index': roll_stock_index, 'crypto': roll_crypto}

roll_days = roll_dict[trade_type][roll_days]

### duplicate contrib_amt for all stocks if only 1 listed
if len(contrib_amt) == len(stock_list):
    pass
elif len(contrib_amt) == 1: 
    contrib_amt = [contrib_amt[0] for x in enumerate(stock_list)]
else:
    print('Incorrect length of contrib_amt. Make it match the length of the stock list or be 1 value')
    exit()

### pull most recent day
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
    print('Stocks with not enough history', nlist)
    for j in nlist:
        print(j, 'missing days:', df['Index'].count()-df[j].count())
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

final_df = pd.DataFrame()
final_df['stock'] = stock_list
final_df['buy_in_amt'] = final_buy_list
final_df['pred_open'] = pred_open_list

trade_day_date = df.tail(1)['date'].item().strftime('%Y.%m.%d')

stocks = []
for i, j, k, m in zip(stock_list, final_buy_list, pred_open_list, contrib_amt):
    if j == m:
        stocks.append(f'\n{i} ({round(k, 2)}): {j}')
    else:
        stocks.append(f'\n{i} ({round(k, 2)}): *{j}*')

file_ymdt = file_ymd + '_' + datetime.now().strftime('%H%M%S')
text_ymdt = text_ymd + ' ' + datetime.now().strftime('%H:%M:%S')

update = (f"{text_ymdt} ({today.strftime('%a')})\nRoll Hist Days = {roll_days}, Pred/Open^2 Threshold = {buyvalue}, Multiplier = {multiplier}\nStk (Pred/Open^2): Buy Value{(''.join(str(a) for a in stocks))}")

# exports
if df['date'][len(df)-1] == today:
    text_file = open(f'C:/Users/james/OneDrive/Desktop/Projects/twitter_bot/export/{segment_name}_{file_ymdt}.txt', 'w')
    text_file.write(update)
    text_file.close()

    # api.update_status(status = update, media_ids = [AAPL.media_id, AMD.media_id, AMZN.media_id, CRM.media_id])

    print(f'{segment_name} complete')

else: print(f'{segment_name} not open (most recent date pull != today)')






