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
    # Running locally or in CI. Prefer secret_pass.py on a developer machine; if
    # it is absent (e.g. CI), fall back to environment variables so the app can be
    # imported for tests without real credentials.
    try:
        import secret_pass
    except ModuleNotFoundError:
        secret_pass = None

    def _secret(name, default=None):
        if secret_pass is not None and hasattr(secret_pass, name):
            return getattr(secret_pass, name)
        return os.environ.get(name, default)

    MYSQL_CONFIG = {
        "database": _secret('mysql_db'),
        "user": _secret('mysql_user'),
        "password": _secret('mysql_pass'),
        "host": _secret('mysql_host'),
    }
    GOOGLE_SHEETS_JSON = _secret('GOOGLE_SHEETS_JSON')
    GOOGLE_SHEETS_URL_ESPRESSO = _secret('GOOGLE_SHEETS_URL_ESPRESSO')
    GOOGLE_SHEETS_URL_BEAN = _secret('GOOGLE_SHEETS_URL_BEAN')
    GOOGLE_SHEETS_URL_PROFILE = _secret('GOOGLE_SHEETS_URL_PROFILE')
    ESPRESSO_WATER_TEMP_NA_VAL = _secret('ESPRESSO_WATER_TEMP_NA_VAL')
    GOOGLE_FORM_PASS = _secret('GOOGLE_FORM_PASS')
    GOOGLE_FORM_URL = _secret('GOOGLE_FORM_URL')
    REDIS_HOST = _secret('REDIS_HOST', 'localhost')
    REDIS_PORT = _secret('REDIS_PORT', '6379')
    REDIS_PASS = _secret('REDIS_PASS')
    MTG_PATH = _secret('MTG_PATH')
    SESSION_KEY = _secret('SESSION_KEY')
    GMAIL_PASS = _secret('GMAIL_PASS')
    BLOSSOM_EMAIL_FLAG = int(_secret('BLOSSOM_EMAIL_FLAG', 1))

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
