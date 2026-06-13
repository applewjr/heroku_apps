"""Environment / secret loading. All values come from Heroku config vars in
production or secret_pass.py locally."""

import os
import secrets

IS_HEROKU = 'IS_HEROKU' in os.environ

if IS_HEROKU:
    # Running on Heroku, load values from Heroku Config Vars
    MYSQL_POOL_CONFIG = {
        "database": os.environ.get('jawsdb_db'),
        "user": os.environ.get('jawsdb_user'),
        "password": os.environ.get('jawsdb_pass'),
        "host": os.environ.get('jawsdb_host'),
        "pool_name": "mypool",
        "pool_size": 3,
        "pool_reset_session": True,
        "autocommit": True
    }
    GOOGLE_SHEETS_JSON = os.environ.get('GOOGLE_SHEETS_JSON')
    GOOGLE_SHEETS_URL_ESPRESSO = os.environ.get('GOOGLE_SHEETS_URL_ESPRESSO')
    GOOGLE_SHEETS_URL_BEAN = os.environ.get('GOOGLE_SHEETS_URL_BEAN')
    GOOGLE_SHEETS_URL_PROFILE = os.environ.get('GOOGLE_SHEETS_URL_PROFILE')
    ESPRESSO_WATER_TEMP_NA_VAL = os.environ.get('ESPRESSO_WATER_TEMP_NA_VAL')
    GOOGLE_FORM_PASS = os.environ.get('GOOGLE_FORM_PASS')
    GOOGLE_FORM_URL = os.environ.get('GOOGLE_FORM_URL')
    REDIS_HOST = os.environ.get('REDIS_HOST')
    REDIS_PORT = os.environ.get('REDIS_PORT')
    REDIS_PASS = os.environ.get('REDIS_PASS')
    MTG_PATH = os.environ.get('MTG_PATH')
    SESSION_KEY = os.environ.get('SESSION_KEY')
    GMAIL_PASS = os.environ.get('GMAIL_PASS')
    BLOSSOM_EMAIL_FLAG = int(os.environ.get('BLOSSOM_EMAIL_FLAG', '1'))
else:
    # Running locally, load values from secret_pass.py
    import secret_pass
    MYSQL_CONFIG = {
        "database": secret_pass.mysql_db,
        "user": secret_pass.mysql_user,
        "password": secret_pass.mysql_pass,
        "host": secret_pass.mysql_host
    }
    GOOGLE_SHEETS_JSON = secret_pass.GOOGLE_SHEETS_JSON
    GOOGLE_SHEETS_URL_ESPRESSO = secret_pass.GOOGLE_SHEETS_URL_ESPRESSO
    GOOGLE_SHEETS_URL_BEAN = secret_pass.GOOGLE_SHEETS_URL_BEAN
    GOOGLE_SHEETS_URL_PROFILE = secret_pass.GOOGLE_SHEETS_URL_PROFILE
    ESPRESSO_WATER_TEMP_NA_VAL = secret_pass.ESPRESSO_WATER_TEMP_NA_VAL
    GOOGLE_FORM_PASS = secret_pass.GOOGLE_FORM_PASS
    GOOGLE_FORM_URL = secret_pass.GOOGLE_FORM_URL
    REDIS_HOST = secret_pass.REDIS_HOST
    REDIS_PORT = secret_pass.REDIS_PORT
    REDIS_PASS = secret_pass.REDIS_PASS
    MTG_PATH = secret_pass.MTG_PATH
    SESSION_KEY = secret_pass.SESSION_KEY
    GMAIL_PASS = secret_pass.GMAIL_PASS
    BLOSSOM_EMAIL_FLAG = getattr(secret_pass, 'BLOSSOM_EMAIL_FLAG', 1)

GMAIL_SENDER_EMAIL = 'james.r.applewhite@gmail.com'
GMAIL_RECEIVER_EMAIL = 'james.r.applewhite@gmail.com'

if not SESSION_KEY:
    if IS_HEROKU:
        # Production environment missing session key - this is an error
        raise ValueError("SESSION_KEY environment variable must be set in production")
    else:
        # Development environment - use a fixed dev key
        SESSION_KEY = 'dev-key-change-for-production-' + secrets.token_hex(16)
        print(f"Using development session key. Set SESSION_KEY env var for production.")
