from stock_bot_core import process_stock_list, get_dict_policy

def main():
    dict_policy = get_dict_policy()
    stock_list = ['AAPL', 'AMD', 'AMZN', 'ASML', 'GOOG']
    contrib_amt = [dict_policy[amt] for amt in stock_list]
    trade_type = 'stock'
    roll_days_base = 'quarter'
    buyvalue = 1.2
    multiplier = 1.5
    segment_name = 'stock1'

    gmail_sender_email = 'james.r.applewhite@gmail.com'
    gmail_receiver_email = 'james.r.applewhite@gmail.com'
    gmail_subject = 'stock_bot_stock1.py'

    process_stock_list(
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
    )

if __name__ == '__main__':
    main()
