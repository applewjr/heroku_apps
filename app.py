from flask import Flask, redirect, render_template, request, redirect, url_for, Response, jsonify, send_from_directory, session
from flask_caching import Cache
# from flask_socketio import SocketIO, emit
from flask_httpauth import HTTPBasicAuth
from flask_sqlalchemy import SQLAlchemy
from flask_sslify import SSLify
import pandas as pd
import os
import datetime
from datetime import datetime, timedelta
import json
import yaml
import redis
import pytz
import time
from werkzeug.middleware.proxy_fix import ProxyFix
import secrets
import logging
import smtplib
from email.mime.text import MIMEText

########## local functions ##########

import sys
sys.path.append('/functions')

from functions import wordle

# from functions import stocks
from functions import all_words
from functions import data_analysis
from functions import plot_viz
from functions import espresso


########## local data ##########

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
data_folder = os.path.join(APP_ROOT, 'datasets')

df = pd.read_csv(os.path.join(data_folder, 'word_data_created.csv'))

# word_df = pd.read_csv(os.path.join(data_folder, 'all_words.csv'))
# word_df = pd.read_csv(os.path.join(data_folder, 'all_words_scrabble.csv')) # changed to main "word_df" on 1/20/2024
word_df = pd.read_csv(os.path.join(data_folder, 'all_words_blossom.csv')) # changed to main "word_df" on 8/24/2024
words = word_df['0'].to_list()
words = set(words)
words_blossom = all_words.filter_words_for_blossom(words)

df_demo_realtor = pd.read_csv(os.path.join(data_folder, 'realtor_data.csv'))
df_demo_titanic = pd.read_csv(os.path.join(data_folder, 'titanic_dataset.csv'))
df_demo_diabetes = pd.read_csv(os.path.join(data_folder, 'diabetes.csv'))

with open(os.path.join(data_folder, 'espresso_brew_points.json'), 'r') as json_file:
    espresso_points = json.load(json_file)

yaml_file_path = os.path.join(data_folder, 'etl_dash_queries.yaml')
with open(yaml_file_path, 'r') as file:
    etl_dash_queries = yaml.safe_load(file)

########## global variables ##########

import mysql.connector
import mysql.connector.pooling
from mysql.connector import Error

if 'IS_HEROKU' in os.environ:
    # Running on Heroku, load values from Heroku Config Vars
    pool_config = {
        "database": os.environ.get('jawsdb_db'),
        "user": os.environ.get('jawsdb_user'),
        "password": os.environ.get('jawsdb_pass'),
        "host": os.environ.get('jawsdb_host'),
        "pool_name": "mypool",
        "pool_size": 3,
        "pool_reset_session": True,
        "autocommit": True           # Add this to reduce connection time
    }
    GOOGLE_SHEETS_JSON = os.environ.get('GOOGLE_SHEETS_JSON')
    GOOGLE_SHEETS_URL_ESPRESSO = os.environ.get('GOOGLE_SHEETS_URL_ESPRESSO')
    GOOGLE_SHEETS_URL_BEAN = os.environ.get('GOOGLE_SHEETS_URL_BEAN')
    GOOGLE_SHEETS_URL_PROFILE = os.environ.get('GOOGLE_SHEETS_URL_PROFILE')
    ESPRESSO_WATER_TEMP_NA_VAL = os.environ.get('ESPRESSO_WATER_TEMP_NA_VAL')
    GOOGLE_FORM_PASS = os.environ.get('GOOGLE_FORM_PASS')
    GOOGLE_FORM_URL = os.environ.get('GOOGLE_FORM_URL')
    # QUILL_SECRET = os.environ.get('QUILL_SECRET')
    REDIS_HOST = os.environ.get('REDIS_HOST')
    REDIS_PORT = os.environ.get('REDIS_PORT')
    REDIS_PASS = os.environ.get('REDIS_PASS')
    MTG_PATH = os.environ.get('MTG_PATH')
    SESSION_KEY = os.environ.get('SESSION_KEY')
    GMAIL_PASS = os.environ.get('GMAIL_PASS')
    BLOSSOM_EMAIL_FLAG = int(os.environ.get('BLOSSOM_EMAIL_FLAG', '1'))

    # Creating a connection pool
    cnxpool = mysql.connector.pooling.MySQLConnectionPool(**pool_config)

    def get_db_connection():
        return cnxpool.get_connection()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        stream=sys.stdout
    )
else:
    # Running locally, load values from secret_pass.py
    import secret_pass
    mysql_config = {
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
    # QUILL_SECRET = secret_pass.QUILL_SECRET
    REDIS_HOST = secret_pass.REDIS_HOST
    REDIS_PORT = secret_pass.REDIS_PORT
    REDIS_PASS = secret_pass.REDIS_PASS
    MTG_PATH = secret_pass.MTG_PATH
    SESSION_KEY = secret_pass.SESSION_KEY
    GMAIL_PASS = secret_pass.GMAIL_PASS
    BLOSSOM_EMAIL_FLAG = getattr(secret_pass, 'BLOSSOM_EMAIL_FLAG', 1)

    def get_db_connection():
        try:
            connection = mysql.connector.connect(**mysql_config)
            if connection.is_connected():
                return connection
        except Error as e:
            print(f"Error connecting to MySQL: {e}")
            return None

gmail_sender_email = 'james.r.applewhite@gmail.com'
gmail_receiver_email = 'james.r.applewhite@gmail.com'

if not SESSION_KEY:
    if os.environ.get('IS_HEROKU'):
        # Production environment missing session key - this is an error
        raise ValueError("SESSION_KEY environment variable must be set in production")
    else:
        # Development environment - use a fixed dev key
        SESSION_KEY = 'dev-key-change-for-production-' + secrets.token_hex(16)
        print(f"Using development session key. Set SESSION_KEY env var for production.")

def log_page_visit(page_name_value):
    referrer = request.headers.get('Referer', 'No referrer')
    user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
    page_name = page_name_value

    conn = None
    cursor = None
    try:
        conn = get_db_connection()  # Ensure this uses connection pooling
        with conn.cursor() as cursor:  # Context manager ensures closing
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
    except mysql.connector.PoolError:
        # Pool exhausted - just skip logging, don't break the app
        print("Skipping page visit log - connection pool busy")
    except mysql.connector.Error as err:
        print("MySQL Error:", err)
    except Exception as e:
        print("Unexpected Error:", e)
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


########## Other SQL stuff ##########

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///test.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
app.secret_key = SESSION_KEY
app.permanent_session_lifetime = timedelta(hours=4)  # Sessions last 4 hours

##### SSL #####

sslify = SSLify(app)
app.config['SESSION_COOKIE_SECURE'] = True
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


##### Redirect #####

@app.before_request
def redirect_non_www():
    host = request.host.split(':')[0]
    if host == 'jamesapplewhite.com':
        return redirect(request.url.replace('://jamesapplewhite.com', '://www.jamesapplewhite.com'), code=301)


##### socket quill #####

# app.config['SECRET_KEY'] = QUILL_SECRET
# socketio = SocketIO(app)


##### logins #####

auth = HTTPBasicAuth()

@auth.verify_password
def verify_password(username, password):
    return password == GOOGLE_FORM_PASS

@auth.error_handler
def custom_error():
    return Response(
    'Could not verify your access level for that URL.\n'
    'You have to login with proper credentials', 401,
    {'WWW-Authenticate': 'Basic realm="Login Required"'})


##### redis #####

# r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASS, decode_responses=True)
                
max_retries = 5
retry_delay = 2  # seconds

for attempt in range(max_retries):
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASS, decode_responses=True, socket_timeout=10)
        print(r.ping())  # Test the connection
        break  # Connection was successful, exit the loop
    except Exception as e:
        print(f"Attempt {attempt+1}: Error connecting to Redis: {e}")
        if attempt < max_retries - 1:
            print(f"Retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)
        else:
            print("Failed to connect after several attempts.")

def add_data_to_stream(stream_name, data):
    # Current datetime as a string
    pst_timezone = pytz.timezone('America/Los_Angeles')
    current_datetime_pst = datetime.now(pst_timezone).strftime('%Y-%m-%d %H:%M:%S')
    
    # Serialize data to JSON format
    json_text = json.dumps(data)
    
    # Add data to Redis stream with auto-generated ID
    r.xadd(stream_name, {'datetime': current_datetime_pst, 'json': json_text})


##### cache #####

cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache'}) # SimpleCache is fine for single-process environments


##### base.html icons click #####

@app.route('/log-click', methods=['POST'])
def log_click():
    # data = request.get_json()
    # clicked_url = data.get('url')
    # log_page_visit(clicked_url)
    return jsonify({"status": "success"}), 200




######################################
######################################
##### front page
######################################
######################################

@app.route("/", methods=["POST", "GET"])
def run_index():

    if request.method == "POST":
        return render_template("index.html")
    else:
        # log_page_visit('index.html')
        return render_template("index.html")



######################################
######################################
##### high score
######################################
######################################

@app.route('/high_score', methods=['GET', 'POST'])
def game():

    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == 'POST':
        initials = request.form['initials']
        score = request.form['score']
        cursor.execute("INSERT INTO high_scores (initials, score, timelog) VALUES (%s, %s, NOW())", (initials, score))
        conn.commit()

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT hs.initials, hs.score FROM high_scores hs ORDER BY score DESC LIMIT 5")
    scores = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('high_score.html', scores=scores)



######################################
######################################
##### to do list w/ mysql
######################################
######################################

@app.route('/task_mysql')
def task():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Retrieve tasks from the database
    cursor.execute('SELECT * FROM tasks')
    tasks = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('task.html', tasks=tasks)

@app.route('/add_task', methods=['POST'])
def add_task():
    conn = get_db_connection()
    cursor = conn.cursor()
    task = request.form['task']
    # Insert the new task into the database
    cursor.execute('INSERT INTO tasks (task, og_timelog) VALUES (%s, NOW())', (task,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/task_mysql')

@app.route('/delete_task/<int:task_id>', methods=['POST'])
def delete_task(task_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    # Delete the task from the database
    cursor.execute('DELETE FROM tasks WHERE id = %s', (task_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/task_mysql')

@app.route('/edit_task/<int:task_id>', methods=['GET', 'POST'])
def edit_task(task_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        new_task = request.form['task']
        # Update the task in the database
        cursor.execute('UPDATE tasks SET task = %s WHERE id = %s', (new_task, task_id))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect('/task_mysql')
    else:
        # Retrieve the task from the database
        cursor.execute('SELECT * FROM tasks WHERE id = %s', (task_id,))
        task = cursor.fetchone()
        cursor.close()
        conn.close()
        return render_template('edit_task.html', task=task)



######################################
######################################
##### to do list w/ sqlite
######################################
######################################

class Todo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String(200), nullable=False)
    # completed = db.Column(db.Integer, default=0)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return '<Task %r>' % self.id

@app.route('/task_db', methods=['POST', 'GET'])
def index():
    if request.method == 'POST':
        task_content = request.form['content']
        new_task = Todo(content=task_content)

        try:
            db.session.add(new_task)
            db.session.commit()
            return redirect('/task_db')
        except:
            return 'There was an issue adding your task'
    else:
        tasks = Todo.query.order_by(Todo.date_created).all()
        return render_template('task_db.html', tasks=tasks)

@app.route('/delete/<int:id>')
def delete(id):
    task_to_delete = Todo.query.get_or_404(id)

    try:
        db.session.delete(task_to_delete)
        db.session.commit()
        return redirect('/task_db')
    except:
        return 'There was a problem deleting that task'

@app.route('/update/<int:id>', methods=['GET', 'POST'])
def update(id):
    task = Todo.query.get_or_404(id)

    if request.method == 'POST':
        task.content = request.form['content']
        
        try:
            db.session.commit()
            return redirect('/task_db')
        except:
            return 'There was an issue updating your task'
    else:
        return render_template('update.html', task=task)



######################################
######################################
##### all wordle/quordle
######################################
######################################

def abbreviate_keys(data):

    key_map = {'letter': 'l', 'position': 'p', 'color': 'c', 'row': 'r'}
    abbreviated_data = [{key_map[key]: value for key, value in d.items()} for d in data]    

    return abbreviated_data 

@app.route("/wordle", methods=["POST", "GET"])
def run_wordle_revamp():
    if request.method == "POST":
        wordle_data_dict = request.get_json().get('wordle_data')
        final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end, first_incomplete_row, complete_rows = wordle.wordle_solver_split_revamp(df, wordle_data_dict)
        first_incomplete_row = 'First Incomplete Row: ' + str(first_incomplete_row)
        complete_rows = 'Complete Rows: ' + str(complete_rows)

        wordle_data_dict_abbr = abbreviate_keys(wordle_data_dict)

        stream_name = 'wordle_logging'

        # insert into a redis cloud instance
        try:
            add_data_to_stream(stream_name, wordle_data_dict_abbr)
        except:
            print('wordle_logging_failed')

        return jsonify(final_out1=final_out1, final_out2=final_out2, final_out3=final_out3, final_out4=final_out4, final_out5=final_out5, final_out_end=final_out_end, \
            first_incomplete_row=first_incomplete_row, complete_rows=complete_rows)
    else:
        # Call the solver with empty data to get initial recommendations
        empty_data = []
        final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end, first_incomplete_row, complete_rows = wordle.wordle_solver_split_revamp(df, empty_data)
        
        # log_page_visit('wordle_revamp.html')

        # Pass the results to JavaScript on page load
        return render_template("wordle_revamp.html", 
                            initial_out1=final_out1,
                            initial_out2=final_out2, 
                            initial_out3=final_out3,
                            initial_out4=final_out4,
                            initial_out5=final_out5,
                            initial_out_end=final_out_end)



@app.route("/antiwordle", methods=["POST", "GET"])
def run_antiwordle_revamp():
    if request.method == "POST":
        wordle_data_dict = request.get_json().get('wordle_data')
        final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end, first_incomplete_row, complete_rows = wordle.antiwordle_solver_split_revamp(df, wordle_data_dict)
        first_incomplete_row = 'First Incomplete Row: ' + str(first_incomplete_row)
        complete_rows = 'Complete Rows: ' + str(complete_rows)

        wordle_data_dict_abbr = abbreviate_keys(wordle_data_dict)

        stream_name = 'antiwordle_logging'

        # insert into a redis cloud instance
        try:
            add_data_to_stream(stream_name, wordle_data_dict_abbr)
        except:
            print('antiwordle_logging_failed')

        return jsonify(final_out1=final_out1, final_out2=final_out2, final_out3=final_out3, final_out4=final_out4, final_out5=final_out5, final_out_end=final_out_end, \
            first_incomplete_row=first_incomplete_row, complete_rows=complete_rows)
    else:
        # Call the solver with empty data to get initial recommendations
        empty_data = []
        final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end, first_incomplete_row, complete_rows = wordle.antiwordle_solver_split_revamp(df, empty_data)
        
        # log_page_visit('antiwordle_revamp.html')

        return render_template("antiwordle_revamp.html", 
                             initial_out1=final_out1,
                             initial_out2=final_out2, 
                             initial_out3=final_out3,
                             initial_out4=final_out4,
                             initial_out5=final_out5,
                             initial_out_end=final_out_end)



@app.route("/quordle", methods=["POST", "GET"])
def run_quordle_revamp():
    if request.method == "POST":
        quordle_data_dict = request.get_json().get('quordle_data')
        
        # Call the new solver function
        results = wordle.quordle_solver_split_revamp(df, quordle_data_dict)
        
        # Unpack all the results
        (final_out_all_1, final_out_all_2, final_out_all_3, final_out_all_4, final_out_all_5, final_out_end_all,
         final_out1_1, final_out1_2, final_out1_3, final_out1_4, final_out1_5, final_out_end1,
         final_out2_1, final_out2_2, final_out2_3, final_out2_4, final_out2_5, final_out_end2,
         final_out3_1, final_out3_2, final_out3_3, final_out3_4, final_out3_5, final_out_end3,
         final_out4_1, final_out4_2, final_out4_3, final_out4_4, final_out4_5, final_out_end4) = results
        
        # try:
        #     stream_name = 'quordle_logging'
        #     add_data_to_stream(stream_name, quordle_data_dict)
        # except:
        #     print('quordle_logging_failed')
        
        return jsonify(
            # All puzzle results
            final_out_all_1=final_out_all_1, final_out_all_2=final_out_all_2, 
            final_out_all_3=final_out_all_3, final_out_all_4=final_out_all_4, 
            final_out_all_5=final_out_all_5, final_out_end_all=final_out_end_all,
            
            # Puzzle 1 results
            final_out1_1=final_out1_1, final_out1_2=final_out1_2, 
            final_out1_3=final_out1_3, final_out1_4=final_out1_4, 
            final_out1_5=final_out1_5, final_out_end1=final_out_end1,
            
            # Puzzle 2 results
            final_out2_1=final_out2_1, final_out2_2=final_out2_2, 
            final_out2_3=final_out2_3, final_out2_4=final_out2_4, 
            final_out2_5=final_out2_5, final_out_end2=final_out_end2,
            
            # Puzzle 3 results
            final_out3_1=final_out3_1, final_out3_2=final_out3_2, 
            final_out3_3=final_out3_3, final_out3_4=final_out3_4, 
            final_out3_5=final_out3_5, final_out_end3=final_out_end3,
            
            # Puzzle 4 results
            final_out4_1=final_out4_1, final_out4_2=final_out4_2, 
            final_out4_3=final_out4_3, final_out4_4=final_out4_4, 
            final_out4_5=final_out4_5, final_out_end4=final_out_end4
        )
    else:
        # Call the solver with empty data to get initial recommendations
        empty_data = []
        results = wordle.quordle_solver_split_revamp(df, empty_data)
        
        # Unpack results for initial load
        (final_out_all_1, final_out_all_2, final_out_all_3, final_out_all_4, final_out_all_5, final_out_end_all,
         final_out1_1, final_out1_2, final_out1_3, final_out1_4, final_out1_5, final_out_end1,
         final_out2_1, final_out2_2, final_out2_3, final_out2_4, final_out2_5, final_out_end2,
         final_out3_1, final_out3_2, final_out3_3, final_out3_4, final_out3_5, final_out_end3,
         final_out4_1, final_out4_2, final_out4_3, final_out4_4, final_out4_5, final_out_end4) = results
        
        return render_template("quordle.html",
            # Initial all puzzle results
            initial_out_all_1=final_out_all_1, initial_out_all_2=final_out_all_2,
            initial_out_all_3=final_out_all_3, initial_out_all_4=final_out_all_4,
            initial_out_all_5=final_out_all_5, initial_out_end_all=final_out_end_all,
            
            # Initial puzzle 1 results
            initial_out1_1=final_out1_1, initial_out1_2=final_out1_2,
            initial_out1_3=final_out1_3, initial_out1_4=final_out1_4,
            initial_out1_5=final_out1_5, initial_out_end1=final_out_end1,
            
            # Initial puzzle 2 results
            initial_out2_1=final_out2_1, initial_out2_2=final_out2_2,
            initial_out2_3=final_out2_3, initial_out2_4=final_out2_4,
            initial_out2_5=final_out2_5, initial_out_end2=final_out_end2,
            
            # Initial puzzle 3 results
            initial_out3_1=final_out3_1, initial_out3_2=final_out3_2,
            initial_out3_3=final_out3_3, initial_out3_4=final_out3_4,
            initial_out3_5=final_out3_5, initial_out_end3=final_out_end3,
            
            # Initial puzzle 4 results
            initial_out4_1=final_out4_1, initial_out4_2=final_out4_2,
            initial_out4_3=final_out4_3, initial_out4_4=final_out4_4,
            initial_out4_5=final_out4_5, initial_out_end4=final_out_end4
        )

@app.route("/fixer", methods=["POST", "GET"])
def run_wordle_fixer():
    if request.method == "POST":
        must_be_present = request.form["must_be_present"]
        final_out1, final_out2, final_out3, final_out4, final_out5 = wordle.find_word_with_letters(df, must_be_present)
        return render_template("fixer.html", final_out1=final_out1, final_out2=final_out2, final_out3=final_out3, final_out4=final_out4, final_out5=final_out5, must_be_present=must_be_present)
    else:

        # log_page_visit('fixer.html')

        return render_template("fixer.html")


@app.route("/wordle_example", methods=["POST", "GET"])
def run_wordle_example():
    if request.method == "POST":
        return render_template("wordle_example.html")
    else:

        # log_page_visit('wordle_example.html')

        return render_template("wordle_example.html")






######################################
######################################
##### stocks
######################################
######################################

# @app.route("/stock_analysis", methods=["POST", "GET"])
# def stock_analysis():

#     stock_default_values = {
#         'stock_list_init_val': 'AAPL',
#         'trade_type_val': 'stock',
#         'contrib_amt_init_val': 10,
#         'total_weeks_val': 52,
#         'buyvalue_val': 1.2,
#         'multiplier_val': 3,
#         'nth_week_val': 1,
#         'roll_days_val': 'quarter',
#         'trade_dow_val': 'Monday'
#     }

#     if request.method == "POST":
#         stock_list_init = request.form["stock_list_init"]

#         trade_type = request.form["trade_type"]
#         # trade_type = ['stock', 'crypto', 'index']
#         # colour_return = request.form["colour_return"]

#         contrib_amt_init = request.form["contrib_amt_init"]
#         total_weeks = request.form["total_weeks"]
#         buyvalue = request.form["buyvalue"]
#         multiplier = request.form["multiplier"]
#         nth_week = request.form["nth_week"]
#         roll_days = request.form["roll_days"]
#         trade_dow = request.form["trade_dow"]
#         pred_open_out, final_buy_out, data_out, valid_graph = stocks.stock_pred(stock_list_init, trade_type, contrib_amt_init, total_weeks, buyvalue, multiplier, nth_week, roll_days, trade_dow)
#         date = list(str(data_out['date']))
#         date = list(range(1, len(data_out)+1))
#         val = list(round(data_out['val'],2))
#         pred = list(round(data_out['pred'],2))

#         return render_template("stock_analysis.html", pred_open_out=pred_open_out, final_buy_out=final_buy_out, date=date, val=val, pred=pred, valid_graph=valid_graph, data_out=data_out, \
#             stock_list_init_val=stock_list_init, trade_type_val=trade_type, contrib_amt_init_val=contrib_amt_init, \
#             # stock_list_init_val=stock_list_init, contrib_amt_init_val=contrib_amt_init, \
#             total_weeks_val=total_weeks, buyvalue_val=buyvalue, multiplier_val=multiplier, nth_week_val=nth_week, roll_days_val=roll_days, trade_dow_val=trade_dow)
#     else:

#         # For GET requests, use query parameters
#         for key in stock_default_values.keys():
#             stock_default_values[key] = request.args.get(key, stock_default_values[key])

#         log_page_visit('stock_analysis.html')

#         return render_template("stock_analysis.html", **stock_default_values)



######################################
######################################
##### dog counter
######################################
######################################

@app.route('/dogs')
def dogs():

    # log_page_visit('dog_count.html')

    return render_template('dog_count.html')



######################################
######################################
##### text editor
######################################
######################################

# @app.route('/quill')
# def quill():
#     return render_template('quill.html')

# @socketio.on('send_change')
# def handle_change(data):
#     emit('receive_change', data, broadcast=True, include_self=False)


######################################
######################################
##### word solvers
######################################
######################################

@app.route("/common_denominator", methods=["POST", "GET"])
def run_common_denominator():
    if request.method == "POST":
        min_match_len = request.form["min_match_len"]
        min_match_rate = request.form["min_match_rate"]
        beg_end_str_char = request.form["beg_end_str_char"]
        value_split_char = request.form["value_split_char"]
        user_match_entry = request.form["user_match_entry"]
        user_nope_match_entry = request.form["user_nope_match_entry"]
        final_match_list, final_out, num_words_entered, comparisons = all_words.common_denominator(min_match_len, min_match_rate, beg_end_str_char, value_split_char, user_match_entry, user_nope_match_entry)
        return render_template("common_denominator.html", min_match_len_val=min_match_len, min_match_rate_val=min_match_rate, beg_end_str_char_val=beg_end_str_char, value_split_char_val=value_split_char, \
            user_match_entry_val=user_match_entry, user_nope_match_entry_val=user_nope_match_entry, \
            final_match_list=final_match_list, final_out=final_out, num_words_entered=num_words_entered, comparisons=comparisons, \
            num_word_count="Number of entries submitted: ", num_run_count="Number of comparisons run: ", top="Top values(s): ", all="All values meeting min match rate: ")
    else:

        # log_page_visit('common_denominator.html')

        return render_template("common_denominator.html", min_match_len_val=3, min_match_rate_val=0.5, beg_end_str_char_val="|", value_split_char_val=",", \
            user_match_entry_val="Discectomy, Laminectomy, Foraminotomy, Corpectomy, Spinal (Lumbar) Fusion, Spinal Cord Stimulation", example=" (example set provided)")


@app.route("/blossom", methods=["POST", "GET"])
def blossom_solver():
    try:

        try:
            schema_data = {
                "@context": "https://schema.org",
                "@type": "WebApplication",
                "name": "Blossom Word Finder & Solver",
                "description": "Free Blossom word finder & solver. Instantly find all words and answers to solve today's Merriam-Webster Blossom puzzle.",
                "url": "https://jamesapplewhite.com/blossom",
                "applicationCategory": "GameApplication",
                "isAccessibleForFree": True,
                "offers": {
                    "@type": "Offer",
                    "price": "0",
                    "priceCurrency": "USD"
                },
                "creator": {
                    "@type": "Person",
                    "name": "James Applewhite"
                }
            }
        except Exception:
            schema_data = None

        if request.method == "POST":
            # Handle checkbox updates via AJAX
            if request.is_json:
                data = request.get_json()
                if data.get('action') == 'toggle_word':
                    word = data.get('word')
                    if 'used_words' not in session:
                        session['used_words'] = []
                    
                    if word in session['used_words']:
                        session['used_words'].remove(word)
                    else:
                        session['used_words'].append(word)
                    
                    session.modified = True  # Mark session as modified
                    return jsonify({'status': 'success', 'used_words': session['used_words']})
            
            # Handle form submission for word search
            must_have = request.form["must_have"]
            may_have = request.form["may_have"]
            petal_letter = request.form["petal_letter"]

            # Handle load more functionality
            current_count = 25  # Default starting count
            if request.form.get("load_more"):
                current_count = int(request.form.get("current_count", 25)) + 25
            elif request.form.get("current_count"):
                current_count = int(request.form.get("current_count", 25))

            # Get used words from session
            used_words = session.get('used_words', [])

            # Get the blossom table and modify it to include checkboxes
            words_blossom_filtered = get_filtered_blossom_words()
            blossom_table, total_valid_words, show_load_more = all_words.filter_words_blossom_revamp(
                must_have, may_have, petal_letter, current_count, words_blossom_filtered, used_words
            )
            valid_word_count = f'Showing {min(current_count, total_valid_words)} of {total_valid_words} words'

            # Make session permanent (4 hours)
            session.permanent = True

            # log clicks and inputs - use actual displayed count
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                query = """
                INSERT INTO blossom_solver_clicks (click_time, must_have, may_have, petal_letter, list_len) 
                VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s, %s);
                """
                cursor.execute(query, (must_have, may_have, petal_letter, min(current_count, total_valid_words)))
                conn.commit()
            except mysql.connector.Error as err:
                print("Error:", err)
            finally:
                if cursor is not None:
                    cursor.close()
                if conn is not None and conn.is_connected():
                    conn.close()

            return render_template("blossom.html", 
                                blossom_table=blossom_table, 
                                must_have_val=must_have, 
                                may_have_val=may_have, 
                                petal_letter=petal_letter, 
                                valid_word_count=valid_word_count,
                                used_words=used_words,
                                current_count=current_count,
                                show_load_more=show_load_more,
                                schema_data=schema_data)

        else:
            # Initialize session for used words if it doesn't exist
            if 'used_words' not in session:
                session['used_words'] = []
            
            # Make session permanent (4 hours)
            session.permanent = True
            
            used_words = session.get('used_words', [])
            log_page_visit('blossom.html')

            return render_template("blossom.html", 
                                used_words=used_words,
                                current_count=25,
                                show_load_more=False,
                                schema_data=schema_data)

    except Exception as e:
        app.logger.error(f"Error R99 (Blossom failed): {str(e)} - IP: {request.remote_addr}")
        return render_template('error.html', return_type='Blossom Error'), 500


# Add this new route for clearing session data
@app.route("/blossom/reset")
def blossom_reset():
    # Clear the used_words from session
    session.pop('used_words', None)
    session.modified = True
    
    # Redirect back to the main blossom page
    return redirect(url_for('blossom_solver'))

@app.route('/blossom_admin')
@auth.login_required
def blossom_admin():
    """Admin page to manage invalid and missing words"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get invalid words
        cursor.execute("""
            SELECT word, added_date 
            FROM blossom_invalid_words 
            ORDER BY added_date DESC
        """)
        invalid_words = cursor.fetchall()
        
        # Get added words
        cursor.execute("""
            SELECT word, added_date 
            FROM blossom_added_words 
            ORDER BY added_date DESC
        """)
        added_words = cursor.fetchall()
        
        # Get recent feedback
        cursor.execute("""
            SELECT id, submit_time, report_type, reported_words 
            FROM feedback_blossom 
            ORDER BY submit_time DESC 
            LIMIT 10
        """)
        recent_feedback = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return render_template('blossom_admin.html', 
                             invalid_words=invalid_words,
                             added_words=added_words,
                             recent_feedback=recent_feedback)
        
    except Exception as e:
        print(f"Error loading blossom admin: {e}")
        return render_template('error.html', return_type='Admin Error'), 500

@app.route('/add_word', methods=['POST'])
@auth.login_required
def add_word():
    """Add a word to the added words list (for missing words)"""
    try:
        word = request.form.get('word', '').strip().lower()
        word_type = request.form.get('word_type', 'invalid')  # 'invalid' or 'missing'
        
        if not word:
            return redirect('/blossom_admin?error=Word is required')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if word_type == 'missing':
            # Add to blossom_added_words table
            cursor.execute("""
                INSERT IGNORE INTO blossom_added_words (word, added_date)
                VALUES (%s, CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'))
            """, (word,))
        else:
            # Add to blossom_invalid_words table (existing functionality)
            cursor.execute("""
                INSERT IGNORE INTO blossom_invalid_words (word, added_date)
                VALUES (%s, CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'))
            """, (word,))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        # Clear cache to force refresh
        cache.delete('blossom_filtered_words')
        
        action = "added to word list" if word_type == 'missing' else "marked as invalid"
        return redirect(f'/blossom_admin?success=Word {action} successfully')
        
    except Exception as e:
        print(f"Error adding word: {e}")
        return redirect('/blossom_admin?error=Database error')

@app.route('/remove_word', methods=['POST'])
@auth.login_required
def remove_word():
    """Remove a word from either invalid or added words list"""
    try:
        word = request.form.get('word', '').strip().lower()
        word_type = request.form.get('word_type', 'invalid')  # 'invalid' or 'missing'
        
        if not word:
            return redirect('/blossom_admin?error=Word is required')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if word_type == 'missing':
            cursor.execute("DELETE FROM blossom_added_words WHERE word = %s", (word,))
        else:
            cursor.execute("DELETE FROM blossom_invalid_words WHERE word = %s", (word,))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        # Clear cache to force refresh
        cache.delete('blossom_filtered_words')
        
        return redirect('/blossom_admin?success=Word removed successfully')
        
    except Exception as e:
        print(f"Error removing word: {e}")
        return redirect('/blossom_admin?error=Database error')

@app.route("/blossom_feedback", methods=["POST", "GET"])
def blossom_feedback():
    if request.method == "POST":
        report_type = request.form['report_type']
        feedback_body = request.form['feedback_body']
        referrer = request.form['referrer']

        # Log inputs to new feedback_blossom table
        feedback_id = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO feedback_blossom (submit_time, referrer, report_type, reported_words) 
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (referrer, report_type, feedback_body))
            conn.commit()
            feedback_id = cursor.lastrowid  # Get the ID of the inserted record
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()
        
        # Send email notification
        if BLOSSOM_EMAIL_FLAG == 1:
            try:
                pst = pytz.timezone('America/Los_Angeles')
                current_time = datetime.now(pst).strftime("%Y-%m-%d %H:%M:%S PST")
                
                report_type_display = "Invalid Words" if report_type == 'invalid' else "Missing Words"
                
                email_body = f"""New Blossom Word Report Received

Report Type: {report_type_display}
Submission Time: {current_time}
Referrer: {referrer}
Feedback ID: {feedback_id}

Reported Words:
{feedback_body}

---
View admin panel: https://jamesapplewhite.com/blossom_admin
"""
            
                gmail_subject = f'Blossom {report_type_display} Report'
                
                msg = MIMEText(email_body)
                msg['Subject'] = gmail_subject
                msg['From'] = gmail_sender_email
                msg['To'] = gmail_receiver_email
                
                with smtplib.SMTP('smtp.gmail.com', 587) as server:
                    server.starttls()
                    server.login(gmail_sender_email, GMAIL_PASS)
                    server.sendmail(gmail_sender_email, gmail_receiver_email, msg.as_string())
                    print(f'Blossom feedback email sent for {report_type} report')
                    
            except Exception as e:
                print(f"Failed to send email notification: {e}")
        else:
            print("Blossom feedback submitted, email is not configured to send")

        return render_template("blossom_feedback_received.html", report_type=report_type)
    else:
        return render_template("blossom_feedback.html")


def get_filtered_blossom_words():
    """Get word list with invalid words removed and missing words added, using cache for performance"""
    
    # Check cache first
    cached_words = cache.get('blossom_filtered_words')
    if cached_words is not None:
        return cached_words
    
    # Get invalid and added words from database
    invalid_words = set()
    added_words = set()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get invalid words
        cursor.execute("SELECT word FROM blossom_invalid_words")
        results = cursor.fetchall()
        invalid_words = {row[0].lower() for row in results}
        
        # Get added words
        cursor.execute("SELECT word FROM blossom_added_words")
        results = cursor.fetchall()
        added_words = {row[0].lower() for row in results}
        
    except Exception as e:
        print(f"Error fetching word lists: {e}")
        # If database error, use original word list
        invalid_words = set()
        added_words = set()
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()
    
    # Ensure both word lists are lowercase for proper comparison
    words_blossom_lower = {str(word).lower() for word in words_blossom}
    
    # Remove invalid words and add missing words
    filtered_words_lower = (words_blossom_lower - invalid_words) | added_words
    
    # Cache the filtered word list for 12 hours
    cache.set('blossom_filtered_words', filtered_words_lower, timeout=43200)
    
    return filtered_words_lower




@app.route("/any_word", methods=["POST", "GET"])
def any_word():
    if request.method == "POST":
        must_have = request.form["must_have"]
        must_not_have = request.form["must_not_have"]
        first_letter = request.form["first_letter"]
        sort_order = request.form["sort_order"]
        list_len = request.form["list_len"]
        min_length = request.form["min_length"]
        max_length = request.form["max_length"]
        # required_substrings = request.form["required_substrings"]
        # forbidden_substrings = request.form["forbidden_substrings"]
        list_out = all_words.filter_words_all(must_have, must_not_have, first_letter, sort_order, list_len, words, min_length, max_length)#, required_substrings, forbidden_substrings)
        return render_template("any_word.html", list_out=list_out, \
            must_have_val=must_have, must_not_have_val=must_not_have, first_letter_val=first_letter, sort_order_val=sort_order, list_len_val=list_len, \
            min_length_val=min_length, max_length_val=max_length)#, required_substrings_val=required_substrings, forbidden_substrings_val=forbidden_substrings)
    else:

        # log_page_visit('any_word.html')

        return render_template("any_word.html", sort_order_val='Max-Min', list_len_val=10, min_length_val=1, max_length_val=100)





######################################
######################################
##### CSV summary
######################################
######################################

@app.route('/data_summary', methods=['GET', 'POST'])
def data_summ():

    if request.method == 'POST':

        csv_pick = request.form["csv_pick"]

        if csv_pick == 'Realtor':
            df = df_demo_realtor
        elif csv_pick == 'Titanic':
            df = df_demo_titanic
        else:
            df = df_demo_diabetes

        ### disable using CSV imports
        # file = request.files['file']
        # df = pd.read_csv(file)

        summary = data_analysis.summarize_df(df)

        heatmap_data = data_analysis.generate_heatmap(df)

        return render_template('data_summary.html', summary=summary, heatmap_data=heatmap_data, csv_pick_val=csv_pick)

    return render_template('data_summary.html', summary=data_analysis.summarize_df(df_demo_realtor), heatmap_data=data_analysis.generate_heatmap(df_demo_realtor), csv_pick_val='Realtor')




######################################
######################################
##### resume
######################################
######################################

@app.route('/resume')
def resume():

    # log_page_visit('resume.html')

    return render_template('resume.html')








######################################
######################################
##### youtube web scraping display
######################################
######################################

@app.route("/youtube_trending", methods=["POST", "GET"])
def youtube_trending():

    conn = get_db_connection()
    cursor = conn.cursor()
    # today = date.today().strftime("%Y-%m-%d")

    cursor.execute("""SET time_zone = 'America/Los_Angeles';""")

    top_10_today = """
    WITH vid_rank AS (
        SELECT video, MIN(vid_rank) AS best_vid_rank
        FROM youtube_trending
        WHERE vid_rank <= 10
        GROUP BY video
        )
    ,rank_yesterday AS (
        SELECT video, chnl, vid_rank AS rank_yesterday
        FROM youtube_trending
        WHERE collected_date = CURDATE()-1
            AND vid_rank <= 10
        )
    SELECT
     yt.vid_rank
    ,yt.video
    ,yt.chnl
    ,vid_rank.best_vid_rank
    ,CASE WHEN rank_yesterday.rank_yesterday IS NULL THEN "New"
        ELSE rank_yesterday.rank_yesterday
        END AS vid_rank_yesterday
    FROM youtube_trending AS yt
    LEFT JOIN vid_rank ON yt.video = vid_rank.video
    LEFT JOIN rank_yesterday ON yt.video = rank_yesterday.video AND yt.chnl = rank_yesterday.chnl
    WHERE yt.collected_date = curdate()
        AND vid_rank <= 10
    ORDER BY yt.vid_rank ASC;
    """
    cursor.execute(top_10_today)
    top_10_today = cursor.fetchall()
    top_10_today = pd.DataFrame(top_10_today, columns=['Rank', 'Video', 'Channel', 'Best Video Rank', 'Video Rank Yesterday'])


    top_10_title = """
    SELECT
     video
    ,chnl
    ,COUNT(*) AS occurrences
    ,MIN(vid_rank) AS best_vid_rank
    FROM youtube_trending
    WHERE collected_date >= CURDATE() - INTERVAL 30 DAY
    GROUP BY video
    ORDER BY occurrences DESC, best_vid_rank ASC
    LIMIT 10;
    """
    cursor.execute(top_10_title)
    top_10_title = cursor.fetchall()
    top_10_title = pd.DataFrame(top_10_title, columns=['Video', 'Channel', 'Count of Days', 'Best Video Rank'])


    top_10_channel = """
    SELECT
     chnl
    ,COUNT(*) AS occurrences
    ,MIN(vid_rank) AS best_channel_rank
    FROM youtube_trending
    WHERE collected_date >= CURDATE() - INTERVAL 30 DAY
    GROUP BY chnl
    ORDER BY occurrences DESC, best_channel_rank ASC
    LIMIT 10;
    """
    cursor.execute(top_10_channel)
    top_10_channel = cursor.fetchall()
    top_10_channel = pd.DataFrame(top_10_channel, columns=['Channel', 'Count of Video Days', 'Best Channel Rank'])


    top_categories = """
    SELECT
     category
    ,SUM(CASE WHEN vid_rank <= 1 THEN 1 ELSE 0 END) AS top_1_count
    ,SUM(CASE WHEN vid_rank <= 10 THEN 1 ELSE 0 END) AS top_10_count
    ,SUM(CASE WHEN vid_rank <= 50 THEN 1 ELSE 0 END) AS top_50_count
    FROM youtube_trending AS yt
    LEFT JOIN youtube_cat AS cat ON yt.vid_cat_id = cat.id
    WHERE collected_date >= CURDATE() - INTERVAL 30 DAY
    GROUP BY category
    ORDER BY top_1_count DESC, top_10_count DESC, top_50_count DESC  
    ;
    """
    cursor.execute(top_categories)
    top_categories = cursor.fetchall()
    top_categories = pd.DataFrame(top_categories, columns=['Category', 'Top 1 Count', 'Top 10 Count', 'Top 50 Count'])

    cursor.close()
    conn.close()

    yt_video_scatter = plot_viz.yt_video_scatter()
    temp_yt_video_scatter = 'static/yt_video_scatter.png'
    yt_video_scatter.savefig(temp_yt_video_scatter)

    yt_chnl_scatter = plot_viz.yt_chnl_scatter()
    temp_yt_chnl_scatter = 'static/yt_chnl_scatter.png'
    yt_chnl_scatter.savefig(temp_yt_chnl_scatter)

    yt_stacked_bar_plot = plot_viz.yt_stacked_bar_plot() # set last due to custom figsize
    temp_yt_stacked_bar_plot = 'static/yt_stacked_bar_plot.png'
    yt_stacked_bar_plot.savefig(temp_yt_stacked_bar_plot)

    # log_page_visit('youtube_trending.html')

    return render_template("youtube_trending.html", top_10_today=top_10_today, \
        top_10_title=top_10_title, top_10_channel=top_10_channel, top_categories=top_categories, \
        yt_stacked_bar_plot=temp_yt_stacked_bar_plot, yt_video_scatter=temp_yt_video_scatter, yt_chnl_scatter=temp_yt_chnl_scatter
        )






######################################
######################################
##### ETL status dashboard
######################################
######################################

def get_valid_rounds():
    rounds = {details.get('round') for _, details in etl_dash_queries.items() if 'round' in details}
    return rounds

@app.route("/etl_dash")
def etl_dash_redirect():
    return redirect(url_for('etl_status_dash', round=1))

@app.route("/etl_dash/<int:round>", methods=["POST", "GET"])
@auth.login_required
def etl_status_dash(round):

    valid_rounds = get_valid_rounds()
    
    if round not in valid_rounds:
        return f"Invalid round parameter. Return only: {valid_rounds}", 400

    conn = get_db_connection()
    cursor = conn.cursor()

    query_dict = {}
    for name, details in etl_dash_queries.items():
        if details.get('round') == round:
            try:
                query = details['query']
                cursor.execute(query)
                result = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                query_df = pd.DataFrame(result, columns=columns)
                
                # Here we add the description to the dictionary entry for this name
                query_dict[name] = {
                    'description': details['description'],
                    'data': query_df
                }
            except Exception as e:
                print(f'{name} not run due to error: {e}')

    cursor.close()
    conn.close()

    # log_page_visit(f'etl_dash_{round}.html')

    return render_template("etl_dash.html", query_dict=query_dict, valid_rounds=sorted(list(valid_rounds)), round=round)







######################################
######################################
##### upload file - TESTING
######################################
######################################

# def concat_test(filename1, filename2):
#     concatenated_filenames = filename1 + filename2
#     return concatenated_filenames

# @app.route('/upload_test', methods=['GET', 'POST'])
# def upload_files():
#     if request.method == 'POST':
#         file1 = request.files['file1']
#         file2 = request.files['file2']
#         filename1 = file1.filename
#         filename2 = file2.filename

#         # Ensure file1 is a zip file
#         if not filename1.endswith('.zip'):
#             return render_template('upload_test.html', error='File 1 must be a .zip file')

#         # Ensure file2 is a json file
#         if not filename2.endswith('.json'):
#             return render_template('upload_test.html', error='File 2 must be a .json file')

#         concatenated_filenames = concat_test(filename1, filename2)

#         return render_template('upload_test.html', concatenated_filenames=concatenated_filenames)
#     else:
#         return render_template('upload_test.html')



######################################
######################################
##### any word w/ mysql - TESTING
######################################
######################################


# @app.route('/any_word_sql', methods=['GET', 'POST'])
# def all_word_sql():
#     if request.method == 'POST':
#         # Get the SQL query from the form
#         query = request.form['query']

#         conn = get_db_connection()
#         cursor = conn.cursor()
#         # Execute the query
#         cursor.execute(f"SELECT words FROM all_words {query}")

#         # Get the results
#         results = cursor.fetchall()

#         cursor.close()
#         conn.close()

#         return render_template('any_word_sql.html', query=query, results=results)

#     return render_template('any_word_sql.html', query="-- Example:\nWHERE words LIKE 'z%a'\nORDER BY LENGTH(words) DESC\nLIMIT 5;")







######################################
######################################
##### Espresso Optimizer - IP
######################################
######################################

def get_espresso_data():
    # Check if data is in cache
    espresso_data = cache.get('espresso_data')
    if espresso_data is None:
        # Data not in cache, so pull it from Google Sheets
        google_credentials = espresso.google_sheets_base(GOOGLE_SHEETS_JSON)
        df_profile = espresso.get_google_sheets_profile(google_credentials, GOOGLE_SHEETS_URL_PROFILE)
        df_espresso_initial = espresso.get_google_sheets_espresso(google_credentials, GOOGLE_SHEETS_URL_ESPRESSO)
        valid_user_name_list, valid_roast_list, valid_shots_list = espresso.get_user_roast_values(df_espresso_initial)
        scatter_espresso_col_labels = espresso.get_scatter_col_labels()
        espresso_data = {
            'df_profile': df_profile,
            'df_espresso_initial': df_espresso_initial,
            'valid_user_name_list': valid_user_name_list,
            'valid_roast_list': valid_roast_list,
            'valid_shots_list': valid_shots_list,
            'scatter_espresso_col_labels': scatter_espresso_col_labels
        }
        # Cache data for future use
        cache.set('espresso_data', espresso_data, timeout=5 * 120)  # Cache for 10 minutes
    return espresso_data

@app.route('/validate_password', methods=['POST'])
def validate_password():
    user_password = request.form['password']

    google_forms_pass = GOOGLE_FORM_PASS
    google_forms_url = GOOGLE_FORM_URL

    if user_password == google_forms_pass:
        return redirect(google_forms_url)
    else:
        referrer = request.headers.get("Referer")
        return redirect(referrer if referrer else url_for('index'))

@app.route('/espresso/home/', methods=['GET', 'POST'])
def espresso_home():

    # log_page_visit('espresso_home.html')

    return render_template('espresso_home.html')

@app.route('/espresso')
def espresso_home_redirect():
    return redirect(url_for('espresso_home'))

@app.route('/espresso/recommendation/', methods=['GET', 'POST'])
def espresso_recommendation():

    espresso_data = get_espresso_data()
    df_profile = espresso_data['df_profile']
    df_espresso_initial = espresso_data['df_espresso_initial']
    valid_user_name_list = espresso_data['valid_user_name_list']
    valid_roast_list = espresso_data['valid_roast_list']
    valid_shots_list = espresso_data['valid_shots_list']

    user_pred = 'James'
    roast_pred = 'Medium'
    shots_pred = '2'
    water_temp_na_val = ESPRESSO_WATER_TEMP_NA_VAL

    if request.method == "POST":

        if 'user_pred' in request.form:
            user_pred = request.form['user_pred']
        if 'roast_pred' in request.form:
            roast_pred = request.form['roast_pred']
        if 'shots_pred' in request.form:
            shots_pred = request.form['shots_pred']
        df_analyze, df_scatter_blank = espresso.clean_espresso_df(user_pred, roast_pred, shots_pred, df_espresso_initial, df_profile, water_temp_na_val)
        optimal_parameters_dict, good_run, performance_dict = espresso.find_optimal_espresso_parameters(df_analyze)

        return render_template('espresso_recommendation.html', valid_user_name_list=valid_user_name_list, valid_roast_list=valid_roast_list, valid_shots_list=valid_shots_list \
            ,optimal_parameters_dict=optimal_parameters_dict, performance_dict=performance_dict, good_run=good_run, user_pred_val=user_pred, roast_pred_val=roast_pred, shots_pred_val=shots_pred \
            )

    else:
        df_analyze, df_scatter_blank = espresso.clean_espresso_df(user_pred, roast_pred, shots_pred, df_espresso_initial, df_profile, water_temp_na_val)
        optimal_parameters_dict, good_run, performance_dict = espresso.find_optimal_espresso_parameters(df_analyze)

        # log_page_visit('espresso_recommendation.html')

        return render_template('espresso_recommendation.html', valid_user_name_list=valid_user_name_list, valid_roast_list=valid_roast_list, valid_shots_list=valid_shots_list \
            ,optimal_parameters_dict=optimal_parameters_dict, performance_dict=performance_dict, good_run=good_run, user_pred_val=user_pred, roast_pred_val=roast_pred, shots_pred_val=shots_pred \
            )

@app.route('/espresso/plot/', methods=['GET', 'POST'])
def espresso_plot():

    espresso_data = get_espresso_data()
    df_profile = espresso_data['df_profile']
    df_espresso_initial = espresso_data['df_espresso_initial']
    valid_user_name_list = espresso_data['valid_user_name_list']
    valid_roast_list = espresso_data['valid_roast_list']
    valid_shots_list = espresso_data['valid_shots_list']
    scatter_espresso_col_labels = espresso_data['scatter_espresso_col_labels']

    espresso_x_col = 'flow_time_seconds'
    espresso_y_col = 'final_score'
    espresso_z_col = 'final_score'
    user_pred_scatter = 'James'
    roast_pred_scatter = 'Medium'
    shots_pred_scatter = '2'
    water_temp_na_val = ESPRESSO_WATER_TEMP_NA_VAL

    if request.method == "POST":

        if 'espresso_x_col' in request.form:
            espresso_x_col = request.form['espresso_x_col']
        if 'espresso_y_col' in request.form:
            espresso_y_col = request.form['espresso_y_col']
        if 'espresso_z_col' in request.form:
            espresso_z_col = request.form['espresso_z_col']
        if 'user_pred_scatter' in request.form:
            user_pred_scatter = request.form['user_pred_scatter']
        if 'roast_pred_scatter' in request.form:
            roast_pred_scatter = request.form['roast_pred_scatter']
        if 'shots_pred_scatter' in request.form:
            shots_pred_scatter = request.form['shots_pred_scatter']
        df_analyze_blank, df_scatter = espresso.clean_espresso_df(user_pred_scatter, roast_pred_scatter, shots_pred_scatter, df_espresso_initial, df_profile, water_temp_na_val)
        espresso_scatter_plot = espresso.espresso_dynamic_scatter(df_scatter, espresso_x_col, espresso_y_col, espresso_z_col)
        temp_espresso_scatter_plot = 'static/espresso_scatter.png'
        espresso_scatter_plot.savefig(temp_espresso_scatter_plot)

        return render_template('espresso_plot.html', valid_user_name_list=valid_user_name_list, valid_roast_list=valid_roast_list, valid_shots_list=valid_shots_list \
            ,espresso_x_col_val=espresso_x_col, espresso_y_col_val=espresso_y_col, espresso_z_col_val=espresso_z_col \
            ,scatter_espresso_col_labels=scatter_espresso_col_labels \
            ,user_pred_scatter_val=user_pred_scatter, roast_pred_scatter_val=roast_pred_scatter, shots_pred_scatter_val=shots_pred_scatter \
            )

    else:
        df_analyze_blank, df_scatter = espresso.clean_espresso_df(user_pred_scatter, roast_pred_scatter, shots_pred_scatter, df_espresso_initial, df_profile, water_temp_na_val)
        espresso_scatter_plot = espresso.espresso_dynamic_scatter(df_scatter, espresso_x_col, espresso_y_col, espresso_z_col)
        temp_espresso_scatter_plot = 'static/espresso_scatter.png'
        espresso_scatter_plot.savefig(temp_espresso_scatter_plot)

        # log_page_visit('espresso_plot.html')

        return render_template('espresso_plot.html', valid_user_name_list=valid_user_name_list, valid_roast_list=valid_roast_list, valid_shots_list=valid_shots_list \
            ,espresso_x_col_val=espresso_x_col, espresso_y_col_val=espresso_y_col, espresso_z_col_val=espresso_z_col \
            ,scatter_espresso_col_labels=scatter_espresso_col_labels \
            ,user_pred_scatter_val=user_pred_scatter, roast_pred_scatter_val=roast_pred_scatter, shots_pred_scatter_val=shots_pred_scatter \
            )

@app.route('/espresso/explore/', methods=['GET', 'POST'])
def espresso_explore():

    espresso_data = get_espresso_data()
    df_profile = espresso_data['df_profile']
    df_espresso_initial = espresso_data['df_espresso_initial']
    valid_user_name_list = espresso_data['valid_user_name_list']
    valid_roast_list = espresso_data['valid_roast_list']
    valid_shots_list = espresso_data['valid_shots_list']

    distance_user = "James"
    distance_roast = "Medium"
    distance_shots = "2"

    distance_grind_min = "11"
    distance_grind_max = "13"
    distance_grind_granularity = "0.5"

    distance_coffee_g_min = "16"
    distance_coffee_g_max = "18"
    distance_coffee_g_granularity = "0.1"

    distance_espresso_g_min = "32"
    distance_espresso_g_max = "42"
    distance_espresso_g_granularity = "0.1"

    water_temp_na_val = ESPRESSO_WATER_TEMP_NA_VAL

    if request.method == "POST":

        if 'distance_user' in request.form:
            distance_user = request.form['distance_user']
        if 'distance_roast' in request.form:
            distance_roast = request.form['distance_roast']
        if 'distance_shots' in request.form:
            distance_shots = request.form['distance_shots']

        if 'distance_grind_min' in request.form:
            distance_grind_min = request.form['distance_grind_min']
        if 'distance_grind_max' in request.form:
            distance_grind_max = request.form['distance_grind_max']
        if 'distance_grind_granularity' in request.form:
            distance_grind_granularity = request.form['distance_grind_granularity']

        if 'distance_coffee_g_min' in request.form:
            distance_coffee_g_min = request.form['distance_coffee_g_min']
        if 'distance_coffee_g_max' in request.form:
            distance_coffee_g_max = request.form['distance_coffee_g_max']
        if 'distance_coffee_g_granularity' in request.form:
            distance_coffee_g_granularity = request.form['distance_coffee_g_granularity']

        if 'distance_espresso_g_min' in request.form:
            distance_espresso_g_min = request.form['distance_espresso_g_min']
        if 'distance_espresso_g_max' in request.form:
            distance_espresso_g_max = request.form['distance_espresso_g_max']
        if 'distance_espresso_g_granularity' in request.form:
            distance_espresso_g_granularity = request.form['distance_espresso_g_granularity']

        df_analyze, df_scatter = espresso.clean_espresso_df(distance_user, distance_roast, distance_shots, df_espresso_initial, df_profile, water_temp_na_val)
        df_furthest = df_scatter[['niche_grind_setting','ground_coffee_grams','espresso_out_grams']]
        furthest_point = espresso.get_furthest_point_multidimensional(df_furthest
            ,distance_grind_min, distance_grind_max, distance_grind_granularity
            ,distance_coffee_g_min, distance_coffee_g_max, distance_coffee_g_granularity
            ,distance_espresso_g_min, distance_espresso_g_max, distance_espresso_g_granularity
            )
        scatter_3d = espresso.plot_3d_scatter(df_scatter)
        temp_scatter_3d_plot = 'static/scatter_3d.png'
        scatter_3d.savefig(temp_scatter_3d_plot)

        return render_template('espresso_explore.html' \
            ,valid_user_name_list=valid_user_name_list, valid_roast_list=valid_roast_list, valid_shots_list=valid_shots_list \
            ,furthest_point=furthest_point, scatter_3d=temp_scatter_3d_plot \
            ,distance_user_val=distance_user, distance_roast_val=distance_roast, distance_shots_val=distance_shots \
            ,distance_grind_min_val=distance_grind_min, distance_grind_max_val=distance_grind_max, distance_grind_granularity_val=distance_grind_granularity \
            ,distance_coffee_g_min_val=distance_coffee_g_min, distance_coffee_g_max_val=distance_coffee_g_max, distance_coffee_g_granularity_val=distance_coffee_g_granularity \
            ,distance_espresso_g_min_val=distance_espresso_g_min, distance_espresso_g_max_val=distance_espresso_g_max, distance_espresso_g_granularity_val=distance_espresso_g_granularity
            )

    else:
        df_analyze, df_scatter = espresso.clean_espresso_df(distance_user, distance_roast, distance_shots, df_espresso_initial, df_profile, water_temp_na_val)
        df_furthest = df_scatter[['niche_grind_setting','ground_coffee_grams','espresso_out_grams']]
        furthest_point = espresso.get_furthest_point_multidimensional(df_furthest
            ,distance_grind_min, distance_grind_max, distance_grind_granularity
            ,distance_coffee_g_min, distance_coffee_g_max, distance_coffee_g_granularity
            ,distance_espresso_g_min, distance_espresso_g_max, distance_espresso_g_granularity
            )
        scatter_3d = espresso.plot_3d_scatter(df_scatter)
        temp_scatter_3d_plot = 'static/scatter_3d.png'
        scatter_3d.savefig(temp_scatter_3d_plot)

        # log_page_visit('espresso_explore.html')

        return render_template('espresso_explore.html' \
            ,valid_user_name_list=valid_user_name_list, valid_roast_list=valid_roast_list, valid_shots_list=valid_shots_list \
            ,furthest_point=furthest_point, scatter_3d=temp_scatter_3d_plot \
            ,distance_user_val=distance_user, distance_roast_val=distance_roast, distance_shots_val=distance_shots \
            ,distance_grind_min_val=distance_grind_min, distance_grind_max_val=distance_grind_max, distance_grind_granularity_val=distance_grind_granularity \
            ,distance_coffee_g_min_val=distance_coffee_g_min, distance_coffee_g_max_val=distance_coffee_g_max, distance_coffee_g_granularity_val=distance_coffee_g_granularity \
            ,distance_espresso_g_min_val=distance_espresso_g_min, distance_espresso_g_max_val=distance_espresso_g_max, distance_espresso_g_granularity_val=distance_espresso_g_granularity
            )

@app.route('/espresso/baseline/', methods=['GET', 'POST'])
def espresso_baseline():

    roast_options = ["Light", "Medium", "Medium Dark", "Dark"]
    dose_options = ["1", "2", "3"]

    roast = 'Medium'
    dose = "2"

    if request.method == "POST":

        if 'roast' in request.form:
            roast = request.form['roast']
        if 'dose' in request.form:
            dose = request.form['dose']
        naive_espresso_info = espresso.get_naive_espresso_points(roast, dose, espresso_points)

        return render_template('espresso_baseline.html', naive_espresso_info=naive_espresso_info, roast_val=roast, dose_val=dose \
            ,roast_options=roast_options, dose_options=dose_options)

    else:
        naive_espresso_info = espresso.get_naive_espresso_points(roast, dose, espresso_points)

        # log_page_visit('espresso_baseline.html')

        return render_template('espresso_baseline.html', naive_espresso_info=naive_espresso_info, roast_val=roast, dose_val=dose \
            ,roast_options=roast_options, dose_options=dose_options)




######################################
######################################
##### MTG Price Trends
######################################
######################################

@app.route("/mtg", methods=["POST", "GET"])
@cache.cached()  # Cache the entire view for the default timeout
def mtg_prices():
    df = pd.read_csv(MTG_PATH)

    today_price_date_str = df['today_price_date'].head(1).values[0]

    df = df[df['tcgplayer_id'].notnull()]
    df = df[df['1wk_diff'].notnull()]
    df = df[['name', 'set_name', 'set_type', 'released_at', 'today_price', '1wk_diff', '2wk_diff', '4wk_diff', 'tcgplayer_id']]
    df.columns = ['Card Name', 'Set Name', 'Set Type', 'Release Date', 'Current Price', '1 Week Change', '2 Week Change', '4 Week Change', 'TCG Link']
    df['TCG Link'] = df['TCG Link'].apply(lambda x: f'<a href="{x}" target="_blank" style="color:blue; text-decoration:underline;">Link</a>')

    df_increase = df.sort_values(by='1 Week Change', ascending=False).head(10).copy()
    df_increase = df_increase.reset_index(drop=True)

    df_decrease = df.sort_values(by='1 Week Change', ascending=True).head(10).copy()
    df_decrease = df_decrease.reset_index(drop=True)

    # log_page_visit('mtg_prices.html')

    return render_template('mtg_prices.html', tables_increase=[df_increase.to_html(escape=False)], tables_decrease=[df_decrease.to_html(escape=False)], titles=df.columns.values, today_price_date_str=today_price_date_str)







######################################
######################################
##### Hex Game
######################################
######################################

@app.route("/hex")
def hex_game():

    # log_page_visit('hex.html')

    return render_template("hex.html")




######################################
######################################
##### privacy policy
######################################
######################################

@app.route('/privacy-policy')
def privacy_policy():

    # log_page_visit('privacy_policy.html')

    return render_template('privacy_policy.html')






@app.route("/feedback", methods=["POST", "GET"])
def feedback():
    if request.method == "POST":

        feedback_header = request.form['feedback_header']
        feedback_body = request.form['feedback_body']
        referrer = request.form['referrer']

        # log inputs
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO feedback (submit_time, referrer, feedback_header, feedback_body) 
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (referrer, feedback_header, feedback_body))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        # TODO - maybe create an email notification as well?

        return render_template("feedback_received.html")
    else:

        # log_page_visit('feedback.html')

        return render_template("feedback.html")

@app.route("/feedback_received", methods=["GET"])
def feedback_received():
    return render_template("feedback_received.html")









@app.route('/robots.txt')
def robots_txt():
    # log_page_visit('robots.txt')
    return send_from_directory(app.static_folder, 'robots.txt', mimetype='text/plain')

@app.route('/ads.txt')
def ads_txt():
    # log_page_visit('ads.txt')
    return send_from_directory(app.static_folder, 'ads.txt', mimetype='text/plain')

@app.route('/<path:icon_name>.png')
def serve_png_icon(icon_name):
    # log_page_visit(f'{icon_name}.png')
    return redirect(url_for('static', filename=f'{icon_name}.png'), code=302)

@app.route('/favicon.ico')
def favicon_ico():
    # log_page_visit('favicon.ico')
    return redirect(url_for('static', filename='favicon.ico'), code=302)


# Error handler for 404 Not Found
@app.errorhandler(404)
def page_not_found(e):
    return_type = '404 - Page Not Found'

    referrer = request.headers.get('Referer', '')
    if 'blossom' in referrer.lower():
        log_page_visit(f'error.html (404: {e})')

    print(e)
    return render_template('error.html', return_type=return_type), 404

@app.errorhandler(Exception)
def handle_exception(e):
    return_type = '500 - Error'

    referrer = request.headers.get('Referer', '')
    if 'blossom' in referrer.lower():
        log_page_visit(f'error.html (500: {e})')

    print(e)
    return render_template('error.html', return_type=return_type), 500

# Optional: Catch-all route for undefined paths
@app.route('/<path:path>')
def catch_all(path):
    return_type = '404 - Undefined Path'

    referrer = request.headers.get('Referer', '')
    if 'blossom' in referrer.lower():
        log_page_visit(f'error.html (undefined path: {path})')

    return render_template('error.html', return_type=return_type), 404




@app.route('/antiwordle_og')
def antiwordle_og_redirect():
    return redirect('/antiwordle', code=301)

@app.route('/blossom_bee')
def blossom_bee_redirect():
    return redirect('/blossom', code=301)

@app.route('/quordle_mobile')
def quordle_mobile_redirect():
    return redirect('/quordle', code=301)

@app.route('/wordle_og')
def wordle_og_redirect():
    return redirect('/wordle', code=301)



if __name__ == "__main__":
    app.run(debug=True, load_dotenv=False)


# venv

# Activate the virtual environment on Windows
# env\Scripts\activate

# Install dependencies (from requirements.txt)
# pip install -r requirements.txt

# pip freeze > requirements.txt

### buildpacks previously used, currently removed
# https://github.com/heroku/heroku-buildpack-google-chrome
# https://github.com/heroku/heroku-buildpack-chromedriver