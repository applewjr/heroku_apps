import yfinance as yf
import pandas as pd
import numpy as np
import time
import tweepy
from datetime import datetime
import os
import pytz
import smtplib
from email.mime.text import MIMEText

pd.options.mode.chained_assignment = None  # default='warn'


def load_config():
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
    return consumer_key, consumer_secret, access_token, access_token_secret, bearer_token, GMAIL_PASS


def get_tweepy_client(consumer_key, consumer_secret, access_token, access_token_secret, bearer_token):
    api = tweepy.Client(
        bearer_token=bearer_token,
        access_token=access_token,
        access_token_secret=access_token_secret,
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
    )
    return api


def get_current_time():
    current_time_utc = datetime.utcnow()
    pst = pytz.timezone('US/Pacific')
    current_time_pst = current_time_utc.replace(tzinfo=pytz.utc).astimezone(pst)
    file_ymd = current_time_pst.strftime('%Y%m%d')
    text_ymd = current_time_pst.strftime('%Y-%m-%d')
    today = pd.to_datetime(current_time_pst.strftime('%Y-%m-%d'))
    text_ymdt = current_time_pst.strftime('%Y-%m-%d %H:%M:%S')
    return current_time_pst, file_ymd, text_ymd, today, text_ymdt


def get_dict_policy():
    df_policy = pd.DataFrame(
        {
            'fund': [
                'AAPL',
                'AMD',
                'AMZN',
                'ASML',
                'GOOG',
                'INTC',
                'MSFT',
                'NVDA',
                'TSLA',
                'TSM',
                'BTC-USD',
                'ETH-USD',
                'BNB-USD',
                'DOGE-USD',
            ],
            'amt': [5] * 14,
        }
    )
    df_policy = df_policy.set_index('fund')
    dict_policy = df_policy['amt'].to_dict()
    return dict_policy


def get_roll_days(trade_type, roll_days_base):
    roll_stock_index = {'month': 21, 'quarter': 65, '2_quarter': 130, 'year': 260}
    roll_crypto = {'month': 30, 'quarter': 90, '2_quarter': 180, 'year': 365}
    roll_dict = {'stock': roll_stock_index, 'index': roll_stock_index, 'crypto': roll_crypto}
    return roll_dict[trade_type][roll_days_base]


def ensure_contrib_amt_length(stock_list, contrib_amt):
    if len(contrib_amt) == len(stock_list):
        return contrib_amt
    elif len(contrib_amt) == 1:
        return [contrib_amt[0] for _ in stock_list]
    else:
        raise ValueError(
            'Incorrect length of contrib_amt. Make it match the length of the stock list or be 1 value'
        )


def retrieve_data(stock_list, trade_type, roll_days):
    if trade_type in ('crypto', 'index'):
        df = yf.download(tickers=stock_list, period=f'{roll_days}d')
        if len(stock_list) == 1:
            df[stock_list[0]] = df['Open']
            df = df[[stock_list[0]]]
        else:
            df = df['Open']
        return df, 0
    else:
        sleep_count = 0
        while True:
            df_now = yf.download(tickers=stock_list, period='1d', interval='1m')
            if len(stock_list) == 1:
                df_now[stock_list[0]] = df_now['Open']
                df_now = df_now[[stock_list[0]]]
            else:
                df_now = df_now['Open']
            df_now = df_now.head(1).fillna(0)
            if all(df_now.iloc[0] != 0):
                break
            time.sleep(15)
            sleep_count += 1
        df_bulk = yf.download(tickers=stock_list, period=f'{roll_days}d')
        if len(stock_list) == 1:
            df_bulk[stock_list[0]] = df_bulk['Open']
            df_bulk = df_bulk[[stock_list[0]]]
        else:
            df_bulk = df_bulk['Open']
        df_bulk = df_bulk.iloc[:-1]
        df = pd.concat([df_bulk, df_now])
        return df, sleep_count


def add_index_and_date(df):
    df = df.copy()
    df['Index'] = np.arange(1, len(df) + 1)
    df['date'] = df.index
    return df


def check_data_completeness(df, stock_list):
    incomplete_stocks = [stock for stock in stock_list if pd.isna(df[stock].iloc[0])]
    if incomplete_stocks:
        missing_info = {
            stock: df['Index'].count() - df[stock].count() for stock in incomplete_stocks
        }
        raise ValueError(f"Stocks with not enough history: {missing_info}")


def calculate_predictions(df, stock_list):
    pred_open_list = []
    for stock in stock_list:
        x_vals = np.arange(1, len(df[stock]) + 1)
        y_vals = df[stock].values
        m, b = np.polyfit(x_vals, y_vals, 1)
        predicted = m * len(df[stock]) + b
        ratio = (predicted / df[stock].iloc[-1]) ** 2
        pred_open_list.append(ratio)
    return pred_open_list


def determine_multipliers(pred_open_list, buyvalue):
    return [1 if ratio > buyvalue else 0 for ratio in pred_open_list]


def calculate_final_buy_list(contrib_amt, pred_open_list, multiplier_list, multiplier):
    final_buy_list = []
    for c_amt, ratio, apply_multiplier in zip(contrib_amt, pred_open_list, multiplier_list):
        if apply_multiplier:
            final_buy_list.append(round(c_amt * ratio * multiplier, 2))
        else:
            final_buy_list.append(c_amt)
    return final_buy_list


def prepare_stocks_output(stock_list, final_buy_list, pred_open_list, contrib_amt):
    stocks_output = []
    for stock, final_buy, ratio, c_amt in zip(stock_list, final_buy_list, pred_open_list, contrib_amt):
        if final_buy == c_amt:
            stocks_output.append(f'\n{stock} ({round(ratio, 2)}): {int(c_amt)}')
        else:
            additional = int(round(final_buy - c_amt, 0))
            stocks_output.append(f'\n{stock} ({round(ratio, 2)}): {int(c_amt)} + {additional}')
    return ''.join(stocks_output)


def prepare_update_message(text_ymdt, today, roll_days, buyvalue, multiplier, stocks_output):
    update = (
        f"{text_ymdt} ({today.strftime('%a')})\n"
        f"Roll Hist Days = {roll_days}, Pred/Open^2 Threshold = {buyvalue}, Multiplier = {multiplier}\n"
        f"Stk (Pred/Open^2): Buy Value{stocks_output}"
    )
    return update


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


def process_stock_list(
    stock_list,
    contrib_amt,
    trade_type,
    roll_days_base,
    buyvalue,
    multiplier,
    segment_name,
    gmail_sender_email,
    gmail_receiver_email,
    gmail_subject,
):
    gmail_list = []

    def print_and_append(statement):
        print(statement)
        gmail_list.append(statement)

    # Load configuration
    (
        consumer_key,
        consumer_secret,
        access_token,
        access_token_secret,
        bearer_token,
        GMAIL_PASS,
    ) = load_config()

    # Get tweepy client
    api = get_tweepy_client(
        consumer_key, consumer_secret, access_token, access_token_secret, bearer_token
    )

    # Get current time
    current_time_pst, file_ymd, text_ymd, today, text_ymdt = get_current_time()

    # Convert roll days into the proper number
    roll_days = get_roll_days(trade_type, roll_days_base)

    # Ensure contrib_amt length matches stock_list
    try:
        contrib_amt = ensure_contrib_amt_length(stock_list, contrib_amt)
    except ValueError as e:
        print_and_append(str(e))
        return

    # Retrieve data
    try:
        df, sleep_count = retrieve_data(stock_list, trade_type, roll_days)
    except Exception as e:
        print_and_append(f"Data retrieval failed: {e}")
        return

    # Add index and date
    df = add_index_and_date(df)

    # Check data completeness
    try:
        check_data_completeness(df, stock_list)
    except ValueError as e:
        print_and_append(str(e))
        return

    # Calculate predictions
    pred_open_list = calculate_predictions(df, stock_list)

    # Determine multipliers
    multiplier_list = determine_multipliers(pred_open_list, buyvalue)

    # Calculate final buy list
    final_buy_list = calculate_final_buy_list(contrib_amt, pred_open_list, multiplier_list, multiplier)

    # Prepare stocks output
    stocks_output = prepare_stocks_output(stock_list, final_buy_list, pred_open_list, contrib_amt)

    # Prepare update message
    update = prepare_update_message(text_ymdt, today, roll_days, buyvalue, multiplier, stocks_output)

    # Exports
    # Adjusted date comparison
    df_date = pd.to_datetime(df['date'].iloc[-1])

    if df_date.tzinfo is None or df_date.tzinfo.utcoffset(df_date) is None:
        df_date = df_date.tz_localize('UTC')
    df_date = df_date.tz_convert('US/Pacific').normalize()

    today_date = pd.to_datetime(today)
    if today_date.tzinfo is None or today_date.tzinfo.utcoffset(today_date) is None:
        today_date = today_date.tz_localize('US/Pacific')
    today_date = today_date.normalize()

    # Debug statements
    print_and_append(f"df_date: {df_date} (tzinfo: {df_date.tzinfo})")
    print_and_append(f"today_date: {today_date} (tzinfo: {today_date.tzinfo})")

    if df_date == today_date:
        # Post tweet
        post_tweet(api, update)

        print_and_append(f'{segment_name} complete')
        print_and_append(update)
        print_and_append('')
        print_and_append(f'sleep_count = {sleep_count}')
        print_and_append('')

        total_weeks_val = 52
        for stock, c_amt in zip(stock_list, contrib_amt):
            print_and_append(stock)
            print_and_append(
                f"https://www.jamesapplewhite.com/stock_analysis?stock_list_init_val={stock}&trade_type_val={trade_type}"
                f"&contrib_amt_init_val={int(c_amt)}&total_weeks_val={total_weeks_val}&buyvalue_val={buyvalue}"
                f"&multiplier_val={multiplier}&nth_week_val=1&roll_days_val={roll_days_base}"
                f"&trade_dow_val={datetime.now().strftime('%A')}"
            )
            print_and_append("")
    else:
        print_and_append(f'{segment_name} not open (most recent date pull != today)')

    # Send email
    gmail_message = '\n'.join(gmail_list)
    send_email(
        gmail_sender_email,
        gmail_receiver_email,
        gmail_subject,
        gmail_message,
        GMAIL_PASS,
    )
