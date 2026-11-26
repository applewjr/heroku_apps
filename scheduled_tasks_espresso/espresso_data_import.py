import mysql.connector
import os
import sys
import traceback

# Copies the three espresso sheets into MySQL. No email since 2026-11-25: an
# error leaves that sheet's table as it was and, once the others have had their
# turn, raises, which run_job turns into an alert. A run that never happened is
# caught by the espresso_import check in datasets/health_checks.yaml.

project_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
functions_path = os.path.join(project_path, 'functions')
sys.path.append(functions_path)

import espresso

if 'IS_HEROKU' in os.environ:
    # Running on Heroku, load values from Heroku Config Vars
    config = {
        'user': os.environ.get('jawsdb_user'),
        'password': os.environ.get('jawsdb_pass'),
        'host': os.environ.get('jawsdb_host'),
        'database': os.environ.get('jawsdb_db'),
        'raise_on_warnings': True
        }
    GOOGLE_SHEETS_JSON = os.environ.get('GOOGLE_SHEETS_JSON')
    GOOGLE_SHEETS_URL_ESPRESSO = os.environ.get('GOOGLE_SHEETS_URL_ESPRESSO')
    GOOGLE_SHEETS_URL_BEAN = os.environ.get('GOOGLE_SHEETS_URL_BEAN')
    GOOGLE_SHEETS_URL_PROFILE = os.environ.get('GOOGLE_SHEETS_URL_PROFILE')
else:
    # Running locally, load values from secret_pass.py
    import sys
    script_directory = os.path.dirname(os.path.abspath(__file__))
    root_directory = os.path.dirname(script_directory)
    sys.path.append(root_directory)
    import secret_pass
    config = {
        'user': secret_pass.mysql_user,
        'password': secret_pass.mysql_pass,
        'host': secret_pass.mysql_host,
        'database': secret_pass.mysql_db,
        'raise_on_warnings': True
        }
    GOOGLE_SHEETS_JSON = secret_pass.GOOGLE_SHEETS_JSON
    GOOGLE_SHEETS_URL_ESPRESSO = secret_pass.GOOGLE_SHEETS_URL_ESPRESSO
    GOOGLE_SHEETS_URL_BEAN = secret_pass.GOOGLE_SHEETS_URL_BEAN
    GOOGLE_SHEETS_URL_PROFILE = secret_pass.GOOGLE_SHEETS_URL_PROFILE

def table_exists(cursor, table_name):
    cursor.execute("SELECT COUNT(*) FROM information_schema.tables "
                   "WHERE table_schema = DATABASE() AND table_name = %s", (table_name,))
    return bool(cursor.fetchone()[0])


def drop_leftover(cursor, table_name):
    """Drop one of this script's own working tables, if it is there. Looked up
    first: DROP TABLE IF EXISTS on a missing table is a warning, which
    raise_on_warnings makes an error."""
    if table_exists(cursor, table_name):
        cursor.execute(f"DROP TABLE {table_name}")


def write_df_to_mysql(df, config, temp_table_name):
    connection = mysql.connector.connect(**config)
    try:
        cursor = connection.cursor()
        drop_leftover(cursor, temp_table_name)

        # Define columns and create temp table
        columns = ", ".join([f"`{col}` VARCHAR(255)" for col in df.columns])
        cursor.execute(f"CREATE TABLE {temp_table_name} ({columns})")

        # Insert data
        for _, row in df.iterrows():
            columns = ', '.join(f'`{col}`' for col in df.columns)
            placeholders = ', '.join(['%s'] * len(row))
            insert_query = f"INSERT INTO {temp_table_name} ({columns}) VALUES ({placeholders})"
            cursor.execute(insert_query, tuple(row))

        connection.commit()
        print(f"Data written to {temp_table_name} successfully.")
    finally:
        connection.close()


def replace_mysql_table(config, table_name, temp_table_name):
    connection = mysql.connector.connect(**config)
    try:
        cursor = connection.cursor()

        backup_table_name = f"{table_name}_backup"
        if table_exists(cursor, table_name):
            # Swap the new table in with one statement, so there is never a
            # moment (or a half-failed run) with no table.
            drop_leftover(cursor, backup_table_name)
            cursor.execute(f"RENAME TABLE {table_name} TO {backup_table_name}, "
                           f"{temp_table_name} TO {table_name}")
        else:
            # No table to swap out, so any backup is the only copy there is:
            # it stays until the new one is in place.
            cursor.execute(f"RENAME TABLE {temp_table_name} TO {table_name}")
        drop_leftover(cursor, backup_table_name)

        connection.commit()
        print(f"{table_name} replaced with {temp_table_name} successfully.")
    finally:
        connection.close()


google_credentials = espresso.google_sheets_base(GOOGLE_SHEETS_JSON)

# (sheet, how it is read, where it is, the table it replaces)
SHEETS = (
    ('espresso', espresso.get_google_sheets_espresso, GOOGLE_SHEETS_URL_ESPRESSO, 'espresso_data'),
    ('bean', espresso.get_google_sheets_bean, GOOGLE_SHEETS_URL_BEAN, 'espresso_bean'),
    ('profile', espresso.get_google_sheets_profile, GOOGLE_SHEETS_URL_PROFILE, 'espresso_profile'),
)

errors = []
for sheet, get_sheet, url, table in SHEETS:
    # One sheet's trouble doesn't cost the others their copy.
    try:
        df = get_sheet(google_credentials, url)
        print(f'pull {sheet} data to df complete. {len(df) = }')
        write_df_to_mysql(df, config, f"temp_{table}")
        replace_mysql_table(config, table, f"temp_{table}")
    except Exception as e:
        traceback.print_exc()
        errors.append(f'{table}: {type(e).__name__}: {e}')
if errors:
    raise RuntimeError('; '.join(errors))
