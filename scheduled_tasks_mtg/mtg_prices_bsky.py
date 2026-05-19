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


def get_price_change_summary(df, diff_col, weeks, ascending):
    df_sorted = df.sort_values(by=diff_col, ascending=ascending)
    return {
        'weeks': weeks,
        'name': df_sorted['name'].iloc[0],
        'set_name': df_sorted['set_name'].iloc[0],
        'old_price': df_sorted[f"{weeks.lower()}_ago_price"].iloc[0],
        'new_price': df_sorted["today_price"].iloc[0],
        'tcg_id': df_sorted["tcgplayer_id"].iloc[0],
    }


def truncate(s, max_len):
    return s if len(s) <= max_len else s[:max_len] + "..."


def post_to_bsky(client, header_line, summaries, url):
    tb = client_utils.TextBuilder()
    tb.tag('#MagicTheGathering', 'MagicTheGathering')
    tb.text(f' - {header_line}\n\n')

    for i, s in enumerate(summaries):
        name = truncate(s["name"], MAX_NAME_LEN)
        set_name = truncate(s["set_name"], MAX_SET_LEN)
        tb.text(f'{s["weeks"]}: ')
        tb.link(name, str(s["tcg_id"]))
        tb.text(f' ({set_name}) ${s["old_price"]}\u2192${s["new_price"]}')
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


def summary_to_email_text(s):
    """Rich version for the email. Full names, full clickable URL."""
    return (
        f'{s["weeks"]}: {s["name"]} ({s["set_name"]})\n'
        f'  ${s["old_price"]} \u2192 ${s["new_price"]}\n'
        f'  {s["tcg_id"]}'
    )


# Load configuration
bsky_handle, bsky_app_password, GMAIL_PASS, MTG_PATH = load_config()

# Get bluesky client
try:
    client = get_bsky_client(bsky_handle, bsky_app_password)
except Exception as e:
    send_email(
        gmail_sender_email,
        gmail_receiver_email,
        'FAILED: ' + gmail_subject,
        f'Bluesky client login failed: {type(e).__name__}: {e}',
        GMAIL_PASS,
    )
    raise

# Read in data
df = pd.read_csv(MTG_PATH)
df = df.dropna(subset=['tcgplayer_id', 'name', 'set_name'])

# Date handling
current_date = datetime.strptime(df['today_price_date'].iloc[0], '%Y-%m-%d').strftime('%m/%d')
data_date = datetime.strptime(df['today_price_date'].iloc[0], '%Y-%m-%d').date()
actual_today = date.today()

if data_date == actual_today:
    # Price increase summaries
    increase_1wk = get_price_change_summary(df, '1wk_diff', '1Wk', ascending=False)
    increase_2wk = get_price_change_summary(df, '2wk_diff', '2Wk', ascending=False)
    increase_4wk = get_price_change_summary(df, '4wk_diff', '4Wk', ascending=False)

    # Price decrease summaries
    decrease_1wk = get_price_change_summary(df, '1wk_diff', '1Wk', ascending=True)
    decrease_2wk = get_price_change_summary(df, '2wk_diff', '2Wk', ascending=True)
    decrease_4wk = get_price_change_summary(df, '4wk_diff', '4Wk', ascending=True)

    mtg_url = 'https://www.jamesapplewhite.com/mtg'

    post_results = {}
    post_error = None

    # Post decreases
    try:
        post_to_bsky(
            client,
            f'Top Price Decreases ({current_date})',
            [decrease_1wk, decrease_2wk, decrease_4wk],
            mtg_url,
        )
        post_results['decreases'] = 'OK'
    except Exception as e:
        post_results['decreases'] = f'FAILED: {type(e).__name__}: {e}'
        post_error = e

    # Post increases
    try:
        post_to_bsky(
            client,
            f'Top Price Increases ({current_date})',
            [increase_1wk, increase_2wk, increase_4wk],
            mtg_url,
        )
        post_results['increases'] = 'OK'
    except Exception as e:
        post_results['increases'] = f'FAILED: {type(e).__name__}: {e}'
        post_error = e

    # Email confirmation (always sent, success or fail)
    status_header = 'mtg bluesky post is complete' if not post_error else 'mtg bluesky post FAILED'
    subject = gmail_subject if not post_error else 'FAILED: ' + gmail_subject

    gmail_message = '\n'.join([
        status_header,
        '',
        f'Decreases post: {post_results["decreases"]}',
        f'Increases post: {post_results["increases"]}',
        '',
        f'Decreases ({current_date}):',
        summary_to_email_text(decrease_1wk),
        '',
        summary_to_email_text(decrease_2wk),
        '',
        summary_to_email_text(decrease_4wk),
        '',
        f'Increases ({current_date}):',
        summary_to_email_text(increase_1wk),
        '',
        summary_to_email_text(increase_2wk),
        '',
        summary_to_email_text(increase_4wk),
    ])
    send_email(
        gmail_sender_email,
        gmail_receiver_email,
        subject,
        gmail_message,
        GMAIL_PASS,
    )

    if post_error:
        raise post_error
else:
    gmail_message = f'WARNING: Data is not timely. Data date: {data_date}, Today: {actual_today}. No posts made.'
    send_email(
        gmail_sender_email,
        gmail_receiver_email,
        'WARNING: ' + gmail_subject,
        gmail_message,
        GMAIL_PASS,
    )