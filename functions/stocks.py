import pandas as pd
import numpy as np
import yfinance as yf
import math
from datetime import date
from datetime import datetime
import time
pd.options.mode.chained_assignment = None  # default='warn'



def stock_pred(stock_list_init: str, trade_type: str, contrib_amt_init: float, total_weeks: int, buyvalue: float, multiplier: float, nth_week: int, roll_days: str, trade_dow: str):

    stock_list_init = str(stock_list_init)
    trade_type = str(trade_type).lower()
    contrib_amt_init = float(contrib_amt_init)
    total_weeks = int(total_weeks)
    buyvalue = float(buyvalue)
    multiplier = float(multiplier)
    nth_week = int(nth_week)
    roll_days = str(roll_days)
    trade_dow = str(trade_dow)

    # convert a multi stock function into a single stock function
    stock_list = [stock_list_init]
    contrib_amt = [contrib_amt_init]



    invest = float('inf')

    for ind, val in enumerate(stock_list):
        stock_list[ind] = stock_list[ind].upper()

    roll_stock_index = {'month': 21, 'quarter': 65, '2_quarter': 130, 'year': 260}
    roll_crypto = {'month': 30, 'quarter': 90, '2_quarter': 180, 'year': 365}
    roll_dict = {'stock': roll_stock_index, 'index': roll_stock_index, 'crypto': roll_crypto}
    roll_days = roll_dict[trade_type][roll_days]

    # number of years to visualize
    if trade_type == 'crypto':
        day_hist = (total_weeks*7)+roll_days+1 # choose for crypto ~ trading every day
    else:
        day_hist = (total_weeks*5)+roll_days+1 # choose for stocks ~ trading about 5 days per week
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

    graph_data = {}
    test_df = pd.DataFrame()
    for j in stock_list:
        graph_data[j] = pd.DataFrame(data={'date': df['date'], 'val': df[str(j)], 'pred': df[str(j) + ' pred']})








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

    pred_open_out = f"{round(pred_open_list[0],4)}"
    final_buy_out = f"{final_buy_list[0]}"

    # this call to the dict df is not dynamic for multiple stock tickers
    data_out = graph_data[stock_list[0]]

    return pred_open_out, final_buy_out, data_out, 1




