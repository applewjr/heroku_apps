import pandas as pd
from atproto import Client, client_utils
from datetime import datetime, date
import os
import smtplib
from email.mime.text import MIMEText
import httpx

pd.options.mode.chained_assignment = None


gmail_sender_email = 'james.r.applewhite@gmail.com'
gmail_receiver_email = 'james.r.applewhite@gmail.com'
gmail_subject = 'mtg_prices.py'
MAX_NAME_LEN = 22
MAX_SET_LEN = 12
BSKY_LIMIT = 300


def load_config():
    if 'IS_HEROKU' in os.environ:
        bsky_handle = os.environ.get('bsky_handle')
        bsky_app_password = os.environ.get('bsky_app_password')
        GMAIL_PASS = os.environ.get('GMAIL_PASS')
        MTG_PATH = os.environ.get('MTG_PATH')
    else:
        import sys
        script_directory = os.path.dirname(os.path.abspath(__file__))
        root_directory = os.path.dirname(script_directory)
        sys.path.append(root_directory)
        import secret_pass
        bsky_handle = secret_pass.bsky_handle
        bsky_app_password = secret_pass.bsky_app_password
        GMAIL_PASS = secret_pass.GMAIL_PASS
        MTG_PATH = secret_pass.MTG_PATH
    return bsky_handle, bsky_app_password, GMAIL_PASS, MTG_PATH


def get_bsky_client(handle, app_password):
    client = Client()
    if 'IS_HEROKU' in os.environ:
        proxy_url = os.environ.get('QUOTAGUARDSTATIC_URL')
        if proxy_url:
            client.request._client = httpx.Client(
                proxy=proxy_url,
                timeout=30.0,
            )
    client.login(handle, app_password)
    return client


def get_price_change_summary(df, diff_col, weeks, ascending, max_name_len, max_set_len):
    df_sorted = df.sort_values(by=diff_col, ascending=ascending)
    name = df_sorted['name'].iloc[0]
    if len(name) > max_name_len:
        name = name[:max_name_len] + "..."
    set_name = df_sorted['set_name'].iloc[0]
    if len(set_name) > max_set_len:
        set_name = set_name[:max_set_len] + "..."
    return {
        'weeks': weeks,
        'name': name,
        'set_name': set_name,
        'old_price': df_sorted[f"{weeks.lower()}_ago_price"].iloc[0],
        'new_price': df_sorted["today_price"].iloc[0],
        'tcg_id': df_sorted["tcgplayer_id"].iloc[0],
    }


def post_to_bsky(client, header_line, summaries, url):
    tb = client_utils.TextBuilder()
    tb.tag('#MagicTheGathering', 'MagicTheGathering')
    tb.text(f' - {header_line}\n\n')

    for i, s in enumerate(summaries):
        tb.text(f'{s["weeks"]}: ')
        tb.link(s["name"], str(s["tcg_id"]))
        tb.text(f' ({s["set_name"]}) ${s["old_price"]}\u2192${s["new_price"]}')
        if i < len(summaries) - 1:
            tb.text('\n')

    tb.text('\n\n')
    tb.link(url, url)

    # Belt-and-suspenders: should never hit, but if it does, fail loud
    if len(tb.build_text()) > BSKY_LIMIT:
        raise ValueError(f'Post too long: {len(tb.build_text())} chars\n{tb.build_text()}')

    client.send_post(tb)


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


def summary_to_text(s):
    """Plain-text version of a summary dict, for the confirmation email."""
    tcg_url = str(s["tcg_id"])
    tcg_id = tcg_url.rsplit("/", 1)[-1]
    return f'{s["weeks"]}: {s["name"]} ${s["old_price"]}\u2192${s["new_price"]} #{tcg_id}'


# Load configuration
bsky_handle, bsky_app_password, GMAIL_PASS, MTG_PATH = load_config()

# Get bluesky client
client = get_bsky_client(bsky_handle, bsky_app_password)

# Read in data
df = pd.read_csv(MTG_PATH)
df = df.dropna(subset=['tcgplayer_id', 'name', 'set_name'])

# Date handling
current_date = datetime.strptime(df['today_price_date'].iloc[0], '%Y-%m-%d').strftime('%m/%d')
data_date = datetime.strptime(df['today_price_date'].iloc[0], '%Y-%m-%d').date()
actual_today = date.today()

if data_date == actual_today:
    # Price increase summaries
    increase_1wk = get_price_change_summary(df, '1wk_diff', '1Wk', ascending=False, max_name_len=MAX_NAME_LEN, max_set_len=MAX_SET_LEN)
    increase_2wk = get_price_change_summary(df, '2wk_diff', '2Wk', ascending=False, max_name_len=MAX_NAME_LEN, max_set_len=MAX_SET_LEN)
    increase_4wk = get_price_change_summary(df, '4wk_diff', '4Wk', ascending=False, max_name_len=MAX_NAME_LEN, max_set_len=MAX_SET_LEN)

    # Price decrease summaries
    decrease_1wk = get_price_change_summary(df, '1wk_diff', '1Wk', ascending=True, max_name_len=MAX_NAME_LEN, max_set_len=MAX_SET_LEN)
    decrease_2wk = get_price_change_summary(df, '2wk_diff', '2Wk', ascending=True, max_name_len=MAX_NAME_LEN, max_set_len=MAX_SET_LEN)
    decrease_4wk = get_price_change_summary(df, '4wk_diff', '4Wk', ascending=True, max_name_len=MAX_NAME_LEN, max_set_len=MAX_SET_LEN)

    mtg_url = 'https://www.jamesapplewhite.com/mtg'

    # Post decreases
    post_to_bsky(
        client,
        f'Top Price Decreases ({current_date})',
        [decrease_1wk, decrease_2wk, decrease_4wk],
        mtg_url,
    )

    # Post increases
    post_to_bsky(
        client,
        f'Top Price Increases ({current_date})',
        [increase_1wk, increase_2wk, increase_4wk],
        mtg_url,
    )

    # Email confirmation
    gmail_message = '\n'.join([
        'mtg bluesky post is complete',
        '',
        f'Decreases ({current_date}):',
        summary_to_text(decrease_1wk),
        summary_to_text(decrease_2wk),
        summary_to_text(decrease_4wk),
        '',
        f'Increases ({current_date}):',
        summary_to_text(increase_1wk),
        summary_to_text(increase_2wk),
        summary_to_text(increase_4wk),
    ])
    send_email(
        gmail_sender_email,
        gmail_receiver_email,
        gmail_subject,
        gmail_message,
        GMAIL_PASS,
    )
else:
    gmail_message = f'WARNING: Data is not timely. Data date: {data_date}, Today: {actual_today}. No posts made.'
    send_email(
        gmail_sender_email,
        gmail_receiver_email,
        'WARNING: ' + gmail_subject,
        gmail_message,
        GMAIL_PASS,
    )