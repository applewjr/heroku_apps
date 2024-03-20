from flask import Flask, redirect, render_template, request, redirect, url_for, Response
import pandas as pd
import numpy as np
import os
import datetime
import json
from datetime import date
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_sslify import SSLify
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_socketio import SocketIO, emit
from flask_httpauth import HTTPBasicAuth


########## local functions ##########

import sys
sys.path.append('/functions')

from functions import wordle

from functions import stocks
from functions import all_words
from functions import data_analysis
from functions import plot_viz
from functions import espresso


########## local data ##########

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
data_folder = os.path.join(APP_ROOT, 'datasets')

df = pd.read_csv(os.path.join(data_folder, 'word_data_created.csv'))

# word_df = pd.read_csv(os.path.join(data_folder, 'all_words.csv'))
word_df = pd.read_csv(os.path.join(data_folder, 'all_words_scrabble.csv')) # changed to main "word_df" on 1/20/2024
words = word_df['0'].to_list()
words = set(words)

df_demo_realtor = pd.read_csv(os.path.join(data_folder, 'realtor_data.csv'))
df_demo_titanic = pd.read_csv(os.path.join(data_folder, 'titanic_dataset.csv'))
df_demo_diabetes = pd.read_csv(os.path.join(data_folder, 'diabetes.csv'))

with open(os.path.join(data_folder, 'espresso_brew_points.json'), 'r') as json_file:
    espresso_points = json.load(json_file)

########## global variables ##########

import mysql.connector
import mysql.connector.pooling

if 'IS_HEROKU' in os.environ:
    # Running on Heroku, load values from Heroku Config Vars
    pool_config = {
        "database": os.environ.get('jawsdb_db'),
        "user": os.environ.get('jawsdb_user'),
        "password": os.environ.get('jawsdb_pass'),
        "host": os.environ.get('jawsdb_host'),
        "pool_name": "mypool",
        "pool_size": 3
    }
    GOOGLE_SHEETS_JSON = os.environ.get('GOOGLE_SHEETS_JSON')
    GOOGLE_SHEETS_URL_ESPRESSO = os.environ.get('GOOGLE_SHEETS_URL_ESPRESSO')
    GOOGLE_SHEETS_URL_BEAN = os.environ.get('GOOGLE_SHEETS_URL_BEAN')
    GOOGLE_SHEETS_URL_PROFILE = os.environ.get('GOOGLE_SHEETS_URL_PROFILE')
    ESPRESSO_WATER_TEMP_NA_VAL = os.environ.get('ESPRESSO_WATER_TEMP_NA_VAL')
    GOOGLE_FORM_PASS = os.environ.get('GOOGLE_FORM_PASS')
    GOOGLE_FORM_URL = os.environ.get('GOOGLE_FORM_URL')
    QUILL_SECRET = os.environ.get('QUILL_SECRET')
else:
    # Running locally, load values from secret_pass.py
    import secret_pass
    pool_config = {
        "database": secret_pass.mysql_db,
        "user": secret_pass.mysql_user,
        "password": secret_pass.mysql_pass,
        "host": secret_pass.mysql_host,
        "pool_name": "mypool",
        "pool_size": 1 # more than 1 not needed for local
    }
    GOOGLE_SHEETS_JSON = secret_pass.GOOGLE_SHEETS_JSON
    GOOGLE_SHEETS_URL_ESPRESSO = secret_pass.GOOGLE_SHEETS_URL_ESPRESSO
    GOOGLE_SHEETS_URL_BEAN = secret_pass.GOOGLE_SHEETS_URL_BEAN
    GOOGLE_SHEETS_URL_PROFILE = secret_pass.GOOGLE_SHEETS_URL_PROFILE
    ESPRESSO_WATER_TEMP_NA_VAL = secret_pass.ESPRESSO_WATER_TEMP_NA_VAL
    GOOGLE_FORM_PASS = secret_pass.GOOGLE_FORM_PASS
    GOOGLE_FORM_URL = secret_pass.GOOGLE_FORM_URL
    QUILL_SECRET = secret_pass.QUILL_SECRET


# Creating a connection pool
cnxpool = mysql.connector.pooling.MySQLConnectionPool(pool_reset_session=True, **pool_config)

def get_pool_db_connection():
    return cnxpool.get_connection()

########## Other SQL stuff ##########

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///test.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


##### SSL #####

sslify = SSLify(app)
app.config['SESSION_COOKIE_SECURE'] = True
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


##### socket quill #####

app.config['SECRET_KEY'] = QUILL_SECRET
socketio = SocketIO(app)


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

        # log visits
        referrer = request.headers.get('Referer', 'No referrer')
        user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
        page_name = 'index.html'
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("index.html")



######################################
######################################
##### high score
######################################
######################################

@app.route('/high_score', methods=['GET', 'POST'])
def game():

    conn = get_pool_db_connection()
    cursor = conn.cursor()

    if request.method == 'POST':
        initials = request.form['initials']
        score = request.form['score']
        cursor.execute("INSERT INTO high_scores (initials, score, timelog) VALUES (%s, %s, NOW())", (initials, score))
        conn.commit()

    conn = get_pool_db_connection()
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
    conn = get_pool_db_connection()
    cursor = conn.cursor()
    # Retrieve tasks from the database
    cursor.execute('SELECT * FROM tasks')
    tasks = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('task.html', tasks=tasks)

@app.route('/add_task', methods=['POST'])
def add_task():
    conn = get_pool_db_connection()
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
    conn = get_pool_db_connection()
    cursor = conn.cursor()
    # Delete the task from the database
    cursor.execute('DELETE FROM tasks WHERE id = %s', (task_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/task_mysql')

@app.route('/edit_task/<int:task_id>', methods=['GET', 'POST'])
def edit_task(task_id):
    conn = get_pool_db_connection()
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

@app.route("/wordle", methods=["POST", "GET"])
def run_wordle():
    if request.method == "POST":
        must_not_be_present = request.form["must_not_be_present"]
        present1 = request.form["present1"]
        present2 = request.form["present2"]
        present3 = request.form["present3"]
        present4 = request.form["present4"]
        present5 = request.form["present5"]
        not_present1 = request.form["not_present1"]
        not_present2 = request.form["not_present2"]
        not_present3 = request.form["not_present3"]
        not_present4 = request.form["not_present4"]
        not_present5 = request.form["not_present5"]
        final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end = wordle.wordle_solver_split(df, must_not_be_present, \
            present1, present2, present3, present4, present5, not_present1, not_present2, not_present3, not_present4, not_present5)

        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO wordle_clicks (
                click_time, solver_name, present1, present2, present3, present4, present5,
                not_present1, not_present2, not_present3, not_present4, not_present5,
                must_not_be_present
            ) 
            VALUES (
                CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            );
            """
            cursor.execute(query, (
                'wordle',
                present1, present2, present3, present4, present5,
                not_present1, not_present2, not_present3, not_present4, not_present5,
                must_not_be_present
            ))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("wordle.html", final_out1=final_out1, final_out2=final_out2, final_out3=final_out3, final_out4=final_out4, final_out5=final_out5, final_out_end=final_out_end, \
            must_not_be_present_val=must_not_be_present, present1_val=present1, present2_val=present2, present3_val=present3, present4_val=present4, present5_val=present5, \
            not_present1_val=not_present1, not_present2_val=not_present2, not_present3_val=not_present3, not_present4_val=not_present4, not_present5_val=not_present5, \
            suggested="Suggested word(s):")
    else:
        must_not_be_present = ""
        present1 = ""
        present2 = ""
        present3 = ""
        present4 = ""
        present5 = ""
        not_present1 = ""
        not_present2 = ""
        not_present3 = ""
        not_present4 = ""
        not_present5 = ""
        final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end = wordle.wordle_solver_split(df, must_not_be_present, \
            present1, present2, present3, present4, present5, not_present1, not_present2, not_present3, not_present4, not_present5)

        # log visits
        referrer = request.headers.get('Referer', 'No referrer')
        user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
        page_name = 'wordle.html'
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("wordle.html", final_out1=final_out1, final_out2=final_out2, final_out3=final_out3, final_out4=final_out4, final_out5=final_out5, final_out_end=final_out_end, \
            must_not_be_present_val=must_not_be_present, present1_val=present1, present2_val=present2, present3_val=present3, present4_val=present4, present5_val=present5, \
            not_present1_val=not_present1, not_present2_val=not_present2, not_present3_val=not_present3, not_present4_val=not_present4, not_present5_val=not_present5, \
            suggested="Suggested word(s):")


@app.route("/antiwordle", methods=["POST", "GET"])
def run_antiwordle():
    if request.method == "POST":
        must_not_be_present = request.form["must_not_be_present"]
        present1 = request.form["present1"]
        present2 = request.form["present2"]
        present3 = request.form["present3"]
        present4 = request.form["present4"]
        present5 = request.form["present5"]
        not_present1 = request.form["not_present1"]
        not_present2 = request.form["not_present2"]
        not_present3 = request.form["not_present3"]
        not_present4 = request.form["not_present4"]
        not_present5 = request.form["not_present5"]
        final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end = wordle.antiwordle_solver_split(df, must_not_be_present, \
            present1, present2, present3, present4, present5, not_present1, not_present2, not_present3, not_present4, not_present5)

        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO wordle_clicks (
                click_time, solver_name, present1, present2, present3, present4, present5,
                not_present1, not_present2, not_present3, not_present4, not_present5,
                must_not_be_present
            ) 
            VALUES (
                CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            );
            """
            cursor.execute(query, (
                'antiwordle',
                present1, present2, present3, present4, present5,
                not_present1, not_present2, not_present3, not_present4, not_present5,
                must_not_be_present
            ))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("antiwordle.html", final_out1=final_out1, final_out2=final_out2, final_out3=final_out3, final_out4=final_out4, final_out5=final_out5, final_out_end=final_out_end, \
            must_not_be_present_val=must_not_be_present, present1_val=present1, present2_val=present2, present3_val=present3, present4_val=present4, present5_val=present5, \
            not_present1_val=not_present1, not_present2_val=not_present2, not_present3_val=not_present3, not_present4_val=not_present4, not_present5_val=not_present5, \
            suggested="Suggested word(s):")
    else:
        must_not_be_present = ""
        present1 = ""
        present2 = ""
        present3 = ""
        present4 = ""
        present5 = ""
        not_present1 = ""
        not_present2 = ""
        not_present3 = ""
        not_present4 = ""
        not_present5 = ""
        final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end = wordle.antiwordle_solver_split(df, must_not_be_present, \
            present1, present2, present3, present4, present5, not_present1, not_present2, not_present3, not_present4, not_present5)

        # log visits
        referrer = request.headers.get('Referer', 'No referrer')
        user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
        page_name = 'antiwordle.html'
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("antiwordle.html", final_out1=final_out1, final_out2=final_out2, final_out3=final_out3, final_out4=final_out4, final_out5=final_out5, final_out_end=final_out_end, \
            must_not_be_present_val=must_not_be_present, present1_val=present1, present2_val=present2, present3_val=present3, present4_val=present4, present5_val=present5, \
            not_present1_val=not_present1, not_present2_val=not_present2, not_present3_val=not_present3, not_present4_val=not_present4, not_present5_val=not_present5, \
            suggested="Suggested word(s):")

@app.route("/quordle", methods=["POST", "GET"])
def run_quordle():
    if request.method == "POST":
        must_not_be_present1 = request.form["must_not_be_present1"]
        must_not_be_present2 = request.form["must_not_be_present2"]
        must_not_be_present3 = request.form["must_not_be_present3"]
        must_not_be_present4 = request.form["must_not_be_present4"]

        present1_1 = request.form["present1_1"]
        present1_2 = request.form["present1_2"]
        present1_3 = request.form["present1_3"]
        present1_4 = request.form["present1_4"]
        present1_5 = request.form["present1_5"]
        present2_1 = request.form["present2_1"]
        present2_2 = request.form["present2_2"]
        present2_3 = request.form["present2_3"]
        present2_4 = request.form["present2_4"]
        present2_5 = request.form["present2_5"]
        present3_1 = request.form["present3_1"]
        present3_2 = request.form["present3_2"]
        present3_3 = request.form["present3_3"]
        present3_4 = request.form["present3_4"]
        present3_5 = request.form["present3_5"]
        present4_1 = request.form["present4_1"]
        present4_2 = request.form["present4_2"]
        present4_3 = request.form["present4_3"]
        present4_4 = request.form["present4_4"]
        present4_5 = request.form["present4_5"]

        not_present1_1 = request.form["not_present1_1"]
        not_present1_2 = request.form["not_present1_2"]
        not_present1_3 = request.form["not_present1_3"]
        not_present1_4 = request.form["not_present1_4"]
        not_present1_5 = request.form["not_present1_5"]
        not_present2_1 = request.form["not_present2_1"]
        not_present2_2 = request.form["not_present2_2"]
        not_present2_3 = request.form["not_present2_3"]
        not_present2_4 = request.form["not_present2_4"]
        not_present2_5 = request.form["not_present2_5"]
        not_present3_1 = request.form["not_present3_1"]
        not_present3_2 = request.form["not_present3_2"]
        not_present3_3 = request.form["not_present3_3"]
        not_present3_4 = request.form["not_present3_4"]
        not_present3_5 = request.form["not_present3_5"]
        not_present4_1 = request.form["not_present4_1"]
        not_present4_2 = request.form["not_present4_2"]
        not_present4_3 = request.form["not_present4_3"]
        not_present4_4 = request.form["not_present4_4"]
        not_present4_5 = request.form["not_present4_5"]

        final_out_all_1, final_out_all_2, final_out_all_3, final_out_all_4, final_out_all_5, final_out_end_all \
        ,final_out1_1, final_out1_2, final_out1_3, final_out1_4, final_out1_5, final_out_end1 \
        ,final_out2_1, final_out2_2, final_out2_3, final_out2_4, final_out2_5, final_out_end2 \
        ,final_out3_1, final_out3_2, final_out3_3, final_out3_4, final_out3_5, final_out_end3 \
        ,final_out4_1, final_out4_2, final_out4_3, final_out4_4, final_out4_5, final_out_end4 = wordle.quordle_solver_split(df, \
        must_not_be_present1, present1_1, present1_2, present1_3, present1_4, present1_5, not_present1_1, not_present1_2, not_present1_3, not_present1_4, not_present1_5, \
        must_not_be_present2, present2_1, present2_2, present2_3, present2_4, present2_5, not_present2_1, not_present2_2, not_present2_3, not_present2_4, not_present2_5, \
        must_not_be_present3, present3_1, present3_2, present3_3, present3_4, present3_5, not_present3_1, not_present3_2, not_present3_3, not_present3_4, not_present3_5, \
        must_not_be_present4, present4_1, present4_2, present4_3, present4_4, present4_5, not_present4_1, not_present4_2, not_present4_3, not_present4_4, not_present4_5)

        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO quordle_clicks (
                click_time, solver_name,
                present1_1, present1_2, present1_3, present1_4, present1_5,
                not_present1_1, not_present1_2, not_present1_3, not_present1_4, not_present1_5,
                must_not_be_present1,
                present2_1, present2_2, present2_3, present2_4, present2_5,
                not_present2_1, not_present2_2, not_present2_3, not_present2_4, not_present2_5,
                must_not_be_present2,
                present3_1, present3_2, present3_3, present3_4, present3_5,
                not_present3_1, not_present3_2, not_present3_3, not_present3_4, not_present3_5,
                must_not_be_present3,
                present4_1, present4_2, present4_3, present4_4, present4_5,
                not_present4_1, not_present4_2, not_present4_3, not_present4_4, not_present4_5,
                must_not_be_present4
            ) VALUES (
                CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            );
            """
            cursor.execute(query, (
                'quordle',
                present1_1, present1_2, present1_3, present1_4, present1_5,
                not_present1_1, not_present1_2, not_present1_3, not_present1_4, not_present1_5,
                must_not_be_present1,
                present2_1, present2_2, present2_3, present2_4, present2_5,
                not_present2_1, not_present2_2, not_present2_3, not_present2_4, not_present2_5,
                must_not_be_present2,
                present3_1, present3_2, present3_3, present3_4, present3_5,
                not_present3_1, not_present3_2, not_present3_3, not_present3_4, not_present3_5,
                must_not_be_present3,
                present4_1, present4_2, present4_3, present4_4, present4_5,
                not_present4_1, not_present4_2, not_present4_3, not_present4_4, not_present4_5,
                must_not_be_present4
            ))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("quordle.html", \
            final_out_all_1=final_out_all_1, final_out_all_2=final_out_all_2, final_out_all_3=final_out_all_3, final_out_all_4=final_out_all_4, final_out_all_5=final_out_all_5, final_out_end_all=final_out_end_all \
            ,final_out1_1=final_out1_1, final_out1_2=final_out1_2, final_out1_3=final_out1_3, final_out1_4=final_out1_4, final_out1_5=final_out1_5, final_out_end1=final_out_end1 \
            ,final_out2_1=final_out2_1, final_out2_2=final_out2_2, final_out2_3=final_out2_3, final_out2_4=final_out2_4, final_out2_5=final_out2_5, final_out_end2=final_out_end2 \
            ,final_out3_1=final_out3_1, final_out3_2=final_out3_2, final_out3_3=final_out3_3, final_out3_4=final_out3_4, final_out3_5=final_out3_5, final_out_end3=final_out_end3 \
            ,final_out4_1=final_out4_1, final_out4_2=final_out4_2, final_out4_3=final_out4_3, final_out4_4=final_out4_4, final_out4_5=final_out4_5, final_out_end4=final_out_end4 \

            ,must_not_be_present1_val=must_not_be_present1, present1_1_val=present1_1, present1_2_val=present1_2, present1_3_val=present1_3, present1_4_val=present1_4, present1_5_val=present1_5 \
            ,not_present1_1_val=not_present1_1, not_present1_2_val=not_present1_2, not_present1_3_val=not_present1_3, not_present1_4_val=not_present1_4, not_present1_5_val=not_present1_5 \
            ,must_not_be_present2_val=must_not_be_present2, present2_1_val=present2_1, present2_2_val=present2_2, present2_3_val=present2_3, present2_4_val=present2_4, present2_5_val=present2_5 \
            ,not_present2_1_val=not_present2_1, not_present2_2_val=not_present2_2, not_present2_3_val=not_present2_3, not_present2_4_val=not_present2_4, not_present2_5_val=not_present2_5 \
            ,must_not_be_present3_val=must_not_be_present3, present3_1_val=present3_1, present3_2_val=present3_2, present3_3_val=present3_3, present3_4_val=present3_4, present3_5_val=present3_5 \
            ,not_present3_1_val=not_present3_1, not_present3_2_val=not_present3_2, not_present3_3_val=not_present3_3, not_present3_4_val=not_present3_4, not_present3_5_val=not_present3_5 \
            ,must_not_be_present4_val=must_not_be_present4, present4_1_val=present4_1, present4_2_val=present4_2, present4_3_val=present4_3, present4_4_val=present4_4, present4_5_val=present4_5 \
            ,not_present4_1_val=not_present4_1, not_present4_2_val=not_present4_2, not_present4_3_val=not_present4_3, not_present4_4_val=not_present4_4, not_present4_5_val=not_present4_5 \
            ,suggested="Suggested word(s):", all_puzzle="All Puzzle:", puzzle_1="Puzzle 1:", puzzle_2="Puzzle 2:", puzzle_3="Puzzle 3:", puzzle_4="Puzzle 4:")
    else:
        must_not_be_present1 = ""
        must_not_be_present2 = ""
        must_not_be_present3 = ""
        must_not_be_present4 = ""

        present1_1 = ""
        present1_2 = ""
        present1_3 = ""
        present1_4 = ""
        present1_5 = ""
        present2_1 = ""
        present2_2 = ""
        present2_3 = ""
        present2_4 = ""
        present2_5 = ""
        present3_1 = ""
        present3_2 = ""
        present3_3 = ""
        present3_4 = ""
        present3_5 = ""
        present4_1 = ""
        present4_2 = ""
        present4_3 = ""
        present4_4 = ""
        present4_5 = ""

        not_present1_1 = ""
        not_present1_2 = ""
        not_present1_3 = ""
        not_present1_4 = ""
        not_present1_5 = ""
        not_present2_1 = ""
        not_present2_2 = ""
        not_present2_3 = ""
        not_present2_4 = ""
        not_present2_5 = ""
        not_present3_1 = ""
        not_present3_2 = ""
        not_present3_3 = ""
        not_present3_4 = ""
        not_present3_5 = ""
        not_present4_1 = ""
        not_present4_2 = ""
        not_present4_3 = ""
        not_present4_4 = ""
        not_present4_5 = ""

        final_out_all_1, final_out_all_2, final_out_all_3, final_out_all_4, final_out_all_5, final_out_end_all \
        ,final_out1_1, final_out1_2, final_out1_3, final_out1_4, final_out1_5, final_out_end1 \
        ,final_out2_1, final_out2_2, final_out2_3, final_out2_4, final_out2_5, final_out_end2 \
        ,final_out3_1, final_out3_2, final_out3_3, final_out3_4, final_out3_5, final_out_end3 \
        ,final_out4_1, final_out4_2, final_out4_3, final_out4_4, final_out4_5, final_out_end4 = wordle.quordle_solver_split(df, \
        must_not_be_present1, present1_1, present1_2, present1_3, present1_4, present1_5, not_present1_1, not_present1_2, not_present1_3, not_present1_4, not_present1_5, \
        must_not_be_present2, present2_1, present2_2, present2_3, present2_4, present2_5, not_present2_1, not_present2_2, not_present2_3, not_present2_4, not_present2_5, \
        must_not_be_present3, present3_1, present3_2, present3_3, present3_4, present3_5, not_present3_1, not_present3_2, not_present3_3, not_present3_4, not_present3_5, \
        must_not_be_present4, present4_1, present4_2, present4_3, present4_4, present4_5, not_present4_1, not_present4_2, not_present4_3, not_present4_4, not_present4_5)

        # log visits
        referrer = request.headers.get('Referer', 'No referrer')
        user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
        page_name = 'quordle.html'
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("quordle.html", \
            final_out_all_1=final_out_all_1, final_out_all_2=final_out_all_2, final_out_all_3=final_out_all_3, final_out_all_4=final_out_all_4, final_out_all_5=final_out_all_5, final_out_end_all=final_out_end_all \
            ,final_out1_1=final_out1_1, final_out1_2=final_out1_2, final_out1_3=final_out1_3, final_out1_4=final_out1_4, final_out1_5=final_out1_5, final_out_end1=final_out_end1 \
            ,final_out2_1=final_out2_1, final_out2_2=final_out2_2, final_out2_3=final_out2_3, final_out2_4=final_out2_4, final_out2_5=final_out2_5, final_out_end2=final_out_end2 \
            ,final_out3_1=final_out3_1, final_out3_2=final_out3_2, final_out3_3=final_out3_3, final_out3_4=final_out3_4, final_out3_5=final_out3_5, final_out_end3=final_out_end3 \
            ,final_out4_1=final_out4_1, final_out4_2=final_out4_2, final_out4_3=final_out4_3, final_out4_4=final_out4_4, final_out4_5=final_out4_5, final_out_end4=final_out_end4 \

            ,must_not_be_present1_val=must_not_be_present1, present1_1_val=present1_1, present1_2_val=present1_2, present1_3_val=present1_3, present1_4_val=present1_4, present1_5_val=present1_5 \
            ,not_present1_1_val=not_present1_1, not_present1_2_val=not_present1_2, not_present1_3_val=not_present1_3, not_present1_4_val=not_present1_4, not_present1_5_val=not_present1_5 \
            ,must_not_be_present2_val=must_not_be_present2, present2_1_val=present2_1, present2_2_val=present2_2, present2_3_val=present2_3, present2_4_val=present2_4, present2_5_val=present2_5 \
            ,not_present2_1_val=not_present2_1, not_present2_2_val=not_present2_2, not_present2_3_val=not_present2_3, not_present2_4_val=not_present2_4, not_present2_5_val=not_present2_5 \
            ,must_not_be_present3_val=must_not_be_present3, present3_1_val=present3_1, present3_2_val=present3_2, present3_3_val=present3_3, present3_4_val=present3_4, present3_5_val=present3_5 \
            ,not_present3_1_val=not_present3_1, not_present3_2_val=not_present3_2, not_present3_3_val=not_present3_3, not_present3_4_val=not_present3_4, not_present3_5_val=not_present3_5 \
            ,must_not_be_present4_val=must_not_be_present4, present4_1_val=present4_1, present4_2_val=present4_2, present4_3_val=present4_3, present4_4_val=present4_4, present4_5_val=present4_5 \
            ,not_present4_1_val=not_present4_1, not_present4_2_val=not_present4_2, not_present4_3_val=not_present4_3, not_present4_4_val=not_present4_4, not_present4_5_val=not_present4_5 \
            ,suggested="Suggested word(s):", all_puzzle="All Puzzle:", puzzle_1="Puzzle 1:", puzzle_2="Puzzle 2:", puzzle_3="Puzzle 3:", puzzle_4="Puzzle 4:")

@app.route("/quordle_mobile", methods=["POST", "GET"])
def run_quordle_mobile():
    if request.method == "POST":
        must_not_be_present1 = request.form["must_not_be_present1"]
        must_not_be_present2 = request.form["must_not_be_present2"]
        must_not_be_present3 = request.form["must_not_be_present3"]
        must_not_be_present4 = request.form["must_not_be_present4"]

        present1_1 = request.form["present1_1"]
        present1_2 = request.form["present1_2"]
        present1_3 = request.form["present1_3"]
        present1_4 = request.form["present1_4"]
        present1_5 = request.form["present1_5"]
        present2_1 = request.form["present2_1"]
        present2_2 = request.form["present2_2"]
        present2_3 = request.form["present2_3"]
        present2_4 = request.form["present2_4"]
        present2_5 = request.form["present2_5"]
        present3_1 = request.form["present3_1"]
        present3_2 = request.form["present3_2"]
        present3_3 = request.form["present3_3"]
        present3_4 = request.form["present3_4"]
        present3_5 = request.form["present3_5"]
        present4_1 = request.form["present4_1"]
        present4_2 = request.form["present4_2"]
        present4_3 = request.form["present4_3"]
        present4_4 = request.form["present4_4"]
        present4_5 = request.form["present4_5"]

        not_present1_1 = request.form["not_present1_1"]
        not_present1_2 = request.form["not_present1_2"]
        not_present1_3 = request.form["not_present1_3"]
        not_present1_4 = request.form["not_present1_4"]
        not_present1_5 = request.form["not_present1_5"]
        not_present2_1 = request.form["not_present2_1"]
        not_present2_2 = request.form["not_present2_2"]
        not_present2_3 = request.form["not_present2_3"]
        not_present2_4 = request.form["not_present2_4"]
        not_present2_5 = request.form["not_present2_5"]
        not_present3_1 = request.form["not_present3_1"]
        not_present3_2 = request.form["not_present3_2"]
        not_present3_3 = request.form["not_present3_3"]
        not_present3_4 = request.form["not_present3_4"]
        not_present3_5 = request.form["not_present3_5"]
        not_present4_1 = request.form["not_present4_1"]
        not_present4_2 = request.form["not_present4_2"]
        not_present4_3 = request.form["not_present4_3"]
        not_present4_4 = request.form["not_present4_4"]
        not_present4_5 = request.form["not_present4_5"]

        final_out_all_1, final_out_all_2, final_out_all_3, final_out_all_4, final_out_all_5, final_out_end_all \
        ,final_out1_1, final_out1_2, final_out1_3, final_out1_4, final_out1_5, final_out_end1 \
        ,final_out2_1, final_out2_2, final_out2_3, final_out2_4, final_out2_5, final_out_end2 \
        ,final_out3_1, final_out3_2, final_out3_3, final_out3_4, final_out3_5, final_out_end3 \
        ,final_out4_1, final_out4_2, final_out4_3, final_out4_4, final_out4_5, final_out_end4 = wordle.quordle_solver_split(df, \
        must_not_be_present1, present1_1, present1_2, present1_3, present1_4, present1_5, not_present1_1, not_present1_2, not_present1_3, not_present1_4, not_present1_5, \
        must_not_be_present2, present2_1, present2_2, present2_3, present2_4, present2_5, not_present2_1, not_present2_2, not_present2_3, not_present2_4, not_present2_5, \
        must_not_be_present3, present3_1, present3_2, present3_3, present3_4, present3_5, not_present3_1, not_present3_2, not_present3_3, not_present3_4, not_present3_5, \
        must_not_be_present4, present4_1, present4_2, present4_3, present4_4, present4_5, not_present4_1, not_present4_2, not_present4_3, not_present4_4, not_present4_5)

        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO quordle_clicks (
                click_time, solver_name,
                present1_1, present1_2, present1_3, present1_4, present1_5,
                not_present1_1, not_present1_2, not_present1_3, not_present1_4, not_present1_5,
                must_not_be_present1,
                present2_1, present2_2, present2_3, present2_4, present2_5,
                not_present2_1, not_present2_2, not_present2_3, not_present2_4, not_present2_5,
                must_not_be_present2,
                present3_1, present3_2, present3_3, present3_4, present3_5,
                not_present3_1, not_present3_2, not_present3_3, not_present3_4, not_present3_5,
                must_not_be_present3,
                present4_1, present4_2, present4_3, present4_4, present4_5,
                not_present4_1, not_present4_2, not_present4_3, not_present4_4, not_present4_5,
                must_not_be_present4
            ) VALUES (
                CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            );
            """
            cursor.execute(query, (
                'quordle_mobile',
                present1_1, present1_2, present1_3, present1_4, present1_5,
                not_present1_1, not_present1_2, not_present1_3, not_present1_4, not_present1_5,
                must_not_be_present1,
                present2_1, present2_2, present2_3, present2_4, present2_5,
                not_present2_1, not_present2_2, not_present2_3, not_present2_4, not_present2_5,
                must_not_be_present2,
                present3_1, present3_2, present3_3, present3_4, present3_5,
                not_present3_1, not_present3_2, not_present3_3, not_present3_4, not_present3_5,
                must_not_be_present3,
                present4_1, present4_2, present4_3, present4_4, present4_5,
                not_present4_1, not_present4_2, not_present4_3, not_present4_4, not_present4_5,
                must_not_be_present4
            ))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("quordle_mobile.html", \
            final_out_all_1=final_out_all_1, final_out_all_2=final_out_all_2, final_out_all_3=final_out_all_3, final_out_all_4=final_out_all_4, final_out_all_5=final_out_all_5, final_out_end_all=final_out_end_all \
            ,final_out1_1=final_out1_1, final_out1_2=final_out1_2, final_out1_3=final_out1_3, final_out1_4=final_out1_4, final_out1_5=final_out1_5, final_out_end1=final_out_end1 \
            ,final_out2_1=final_out2_1, final_out2_2=final_out2_2, final_out2_3=final_out2_3, final_out2_4=final_out2_4, final_out2_5=final_out2_5, final_out_end2=final_out_end2 \
            ,final_out3_1=final_out3_1, final_out3_2=final_out3_2, final_out3_3=final_out3_3, final_out3_4=final_out3_4, final_out3_5=final_out3_5, final_out_end3=final_out_end3 \
            ,final_out4_1=final_out4_1, final_out4_2=final_out4_2, final_out4_3=final_out4_3, final_out4_4=final_out4_4, final_out4_5=final_out4_5, final_out_end4=final_out_end4 \

            ,must_not_be_present1_val=must_not_be_present1, present1_1_val=present1_1, present1_2_val=present1_2, present1_3_val=present1_3, present1_4_val=present1_4, present1_5_val=present1_5 \
            ,not_present1_1_val=not_present1_1, not_present1_2_val=not_present1_2, not_present1_3_val=not_present1_3, not_present1_4_val=not_present1_4, not_present1_5_val=not_present1_5 \
            ,must_not_be_present2_val=must_not_be_present2, present2_1_val=present2_1, present2_2_val=present2_2, present2_3_val=present2_3, present2_4_val=present2_4, present2_5_val=present2_5 \
            ,not_present2_1_val=not_present2_1, not_present2_2_val=not_present2_2, not_present2_3_val=not_present2_3, not_present2_4_val=not_present2_4, not_present2_5_val=not_present2_5 \
            ,must_not_be_present3_val=must_not_be_present3, present3_1_val=present3_1, present3_2_val=present3_2, present3_3_val=present3_3, present3_4_val=present3_4, present3_5_val=present3_5 \
            ,not_present3_1_val=not_present3_1, not_present3_2_val=not_present3_2, not_present3_3_val=not_present3_3, not_present3_4_val=not_present3_4, not_present3_5_val=not_present3_5 \
            ,must_not_be_present4_val=must_not_be_present4, present4_1_val=present4_1, present4_2_val=present4_2, present4_3_val=present4_3, present4_4_val=present4_4, present4_5_val=present4_5 \
            ,not_present4_1_val=not_present4_1, not_present4_2_val=not_present4_2, not_present4_3_val=not_present4_3, not_present4_4_val=not_present4_4, not_present4_5_val=not_present4_5 \
            ,suggested="Suggested word(s):", all_puzzle="All Puzzle:", puzzle_1="Puzzle 1:", puzzle_2="Puzzle 2:", puzzle_3="Puzzle 3:", puzzle_4="Puzzle 4:")
    else:
        must_not_be_present1 = ""
        must_not_be_present2 = ""
        must_not_be_present3 = ""
        must_not_be_present4 = ""

        present1_1 = ""
        present1_2 = ""
        present1_3 = ""
        present1_4 = ""
        present1_5 = ""
        present2_1 = ""
        present2_2 = ""
        present2_3 = ""
        present2_4 = ""
        present2_5 = ""
        present3_1 = ""
        present3_2 = ""
        present3_3 = ""
        present3_4 = ""
        present3_5 = ""
        present4_1 = ""
        present4_2 = ""
        present4_3 = ""
        present4_4 = ""
        present4_5 = ""

        not_present1_1 = ""
        not_present1_2 = ""
        not_present1_3 = ""
        not_present1_4 = ""
        not_present1_5 = ""
        not_present2_1 = ""
        not_present2_2 = ""
        not_present2_3 = ""
        not_present2_4 = ""
        not_present2_5 = ""
        not_present3_1 = ""
        not_present3_2 = ""
        not_present3_3 = ""
        not_present3_4 = ""
        not_present3_5 = ""
        not_present4_1 = ""
        not_present4_2 = ""
        not_present4_3 = ""
        not_present4_4 = ""
        not_present4_5 = ""

        final_out_all_1, final_out_all_2, final_out_all_3, final_out_all_4, final_out_all_5, final_out_end_all \
        ,final_out1_1, final_out1_2, final_out1_3, final_out1_4, final_out1_5, final_out_end1 \
        ,final_out2_1, final_out2_2, final_out2_3, final_out2_4, final_out2_5, final_out_end2 \
        ,final_out3_1, final_out3_2, final_out3_3, final_out3_4, final_out3_5, final_out_end3 \
        ,final_out4_1, final_out4_2, final_out4_3, final_out4_4, final_out4_5, final_out_end4 = wordle.quordle_solver_split(df, \
        must_not_be_present1, present1_1, present1_2, present1_3, present1_4, present1_5, not_present1_1, not_present1_2, not_present1_3, not_present1_4, not_present1_5, \
        must_not_be_present2, present2_1, present2_2, present2_3, present2_4, present2_5, not_present2_1, not_present2_2, not_present2_3, not_present2_4, not_present2_5, \
        must_not_be_present3, present3_1, present3_2, present3_3, present3_4, present3_5, not_present3_1, not_present3_2, not_present3_3, not_present3_4, not_present3_5, \
        must_not_be_present4, present4_1, present4_2, present4_3, present4_4, present4_5, not_present4_1, not_present4_2, not_present4_3, not_present4_4, not_present4_5)

        # log visits
        referrer = request.headers.get('Referer', 'No referrer')
        user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
        page_name = 'quordle_mobile.html'
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("quordle_mobile.html", \
            final_out_all_1=final_out_all_1, final_out_all_2=final_out_all_2, final_out_all_3=final_out_all_3, final_out_all_4=final_out_all_4, final_out_all_5=final_out_all_5, final_out_end_all=final_out_end_all \
            ,final_out1_1=final_out1_1, final_out1_2=final_out1_2, final_out1_3=final_out1_3, final_out1_4=final_out1_4, final_out1_5=final_out1_5, final_out_end1=final_out_end1 \
            ,final_out2_1=final_out2_1, final_out2_2=final_out2_2, final_out2_3=final_out2_3, final_out2_4=final_out2_4, final_out2_5=final_out2_5, final_out_end2=final_out_end2 \
            ,final_out3_1=final_out3_1, final_out3_2=final_out3_2, final_out3_3=final_out3_3, final_out3_4=final_out3_4, final_out3_5=final_out3_5, final_out_end3=final_out_end3 \
            ,final_out4_1=final_out4_1, final_out4_2=final_out4_2, final_out4_3=final_out4_3, final_out4_4=final_out4_4, final_out4_5=final_out4_5, final_out_end4=final_out_end4 \

            ,must_not_be_present1_val=must_not_be_present1, present1_1_val=present1_1, present1_2_val=present1_2, present1_3_val=present1_3, present1_4_val=present1_4, present1_5_val=present1_5 \
            ,not_present1_1_val=not_present1_1, not_present1_2_val=not_present1_2, not_present1_3_val=not_present1_3, not_present1_4_val=not_present1_4, not_present1_5_val=not_present1_5 \
            ,must_not_be_present2_val=must_not_be_present2, present2_1_val=present2_1, present2_2_val=present2_2, present2_3_val=present2_3, present2_4_val=present2_4, present2_5_val=present2_5 \
            ,not_present2_1_val=not_present2_1, not_present2_2_val=not_present2_2, not_present2_3_val=not_present2_3, not_present2_4_val=not_present2_4, not_present2_5_val=not_present2_5 \
            ,must_not_be_present3_val=must_not_be_present3, present3_1_val=present3_1, present3_2_val=present3_2, present3_3_val=present3_3, present3_4_val=present3_4, present3_5_val=present3_5 \
            ,not_present3_1_val=not_present3_1, not_present3_2_val=not_present3_2, not_present3_3_val=not_present3_3, not_present3_4_val=not_present3_4, not_present3_5_val=not_present3_5 \
            ,must_not_be_present4_val=must_not_be_present4, present4_1_val=present4_1, present4_2_val=present4_2, present4_3_val=present4_3, present4_4_val=present4_4, present4_5_val=present4_5 \
            ,not_present4_1_val=not_present4_1, not_present4_2_val=not_present4_2, not_present4_3_val=not_present4_3, not_present4_4_val=not_present4_4, not_present4_5_val=not_present4_5 \
            ,suggested="Suggested word(s):", all_puzzle="All Puzzle:", puzzle_1="Puzzle 1:", puzzle_2="Puzzle 2:", puzzle_3="Puzzle 3:", puzzle_4="Puzzle 4:")

@app.route("/fixer", methods=["POST", "GET"])
def run_wordle_fixer():
    if request.method == "POST":
        must_be_present = request.form["must_be_present"]
        final_out1, final_out2, final_out3, final_out4, final_out5 = wordle.find_word_with_letters(df, must_be_present)
        return render_template("fixer.html", final_out1=final_out1, final_out2=final_out2, final_out3=final_out3, final_out4=final_out4, final_out5=final_out5, must_be_present=must_be_present)
    else:

        # log visits
        referrer = request.headers.get('Referer', 'No referrer')
        user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
        page_name = 'fixer.html'
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("fixer.html")


@app.route("/wordle_example", methods=["POST", "GET"])
def run_wordle_example():
    if request.method == "POST":
        return render_template("wordle_example.html")
    else:

        # log visits
        referrer = request.headers.get('Referer', 'No referrer')
        user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
        page_name = 'wordle_example.html'
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("wordle_example.html")



######################################
######################################
##### stocks
######################################
######################################

@app.route("/stock_analysis", methods=["POST", "GET"])
def stock_analysis():

    stock_default_values = {
        'stock_list_init_val': 'AAPL',
        'trade_type_val': 'stock',
        'contrib_amt_init_val': 10,
        'total_weeks_val': 52,
        'buyvalue_val': 1.2,
        'multiplier_val': 3,
        'nth_week_val': 1,
        'roll_days_val': 'quarter',
        'trade_dow_val': 'Monday'
    }

    if request.method == "POST":
        stock_list_init = request.form["stock_list_init"]

        trade_type = request.form["trade_type"]
        # trade_type = ['stock', 'crypto', 'index']
        # colour_return = request.form["colour_return"]

        contrib_amt_init = request.form["contrib_amt_init"]
        total_weeks = request.form["total_weeks"]
        buyvalue = request.form["buyvalue"]
        multiplier = request.form["multiplier"]
        nth_week = request.form["nth_week"]
        roll_days = request.form["roll_days"]
        trade_dow = request.form["trade_dow"]
        pred_open_out, final_buy_out, data_out, valid_graph = stocks.stock_pred(stock_list_init, trade_type, contrib_amt_init, total_weeks, buyvalue, multiplier, nth_week, roll_days, trade_dow)
        date = list(str(data_out['date']))
        date = list(range(1, len(data_out)+1))
        val = list(round(data_out['val'],2))
        pred = list(round(data_out['pred'],2))

        return render_template("stock_analysis.html", pred_open_out=pred_open_out, final_buy_out=final_buy_out, date=date, val=val, pred=pred, valid_graph=valid_graph, data_out=data_out, \
            stock_list_init_val=stock_list_init, trade_type_val=trade_type, contrib_amt_init_val=contrib_amt_init, \
            # stock_list_init_val=stock_list_init, contrib_amt_init_val=contrib_amt_init, \
            total_weeks_val=total_weeks, buyvalue_val=buyvalue, multiplier_val=multiplier, nth_week_val=nth_week, roll_days_val=roll_days, trade_dow_val=trade_dow)
    else:

        # For GET requests, use query parameters
        for key in stock_default_values.keys():
            stock_default_values[key] = request.args.get(key, stock_default_values[key])


        # log visits
        referrer = request.headers.get('Referer', 'No referrer')
        user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
        page_name = 'stock_analysis.html'
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("stock_analysis.html", **stock_default_values)



######################################
######################################
##### dog counter
######################################
######################################

@app.route('/dogs')
def dogs():

    # log visits
    referrer = request.headers.get('Referer', 'No referrer')
    user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
    page_name = 'dog_count.html'
    try:
        conn = get_pool_db_connection()
        cursor = conn.cursor()
        query = """
        INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
        VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
        """
        cursor.execute(query, (page_name, referrer, user_agent))
        conn.commit()
    except mysql.connector.Error as err:
        print("Error:", err)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()

    return render_template('dog_count.html')



######################################
######################################
##### text editor
######################################
######################################

@app.route('/quill')
def quill():
    return render_template('quill.html')

@socketio.on('send_change')
def handle_change(data):
    emit('receive_change', data, broadcast=True, include_self=False)


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

        # log visits
        referrer = request.headers.get('Referer', 'No referrer')
        user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
        page_name = 'common_denominator.html'
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("common_denominator.html", min_match_len_val=3, min_match_rate_val=0.5, beg_end_str_char_val="|", value_split_char_val=",", \
            user_match_entry_val="Discectomy, Laminectomy, Foraminotomy, Corpectomy, Spinal (Lumbar) Fusion, Spinal Cord Stimulation", example=" (example set provided)")

@app.route("/blossom_bee", methods=["POST", "GET"])
def blossom():
    if request.method == "POST":

        must_have = request.form["must_have"]
        may_have = request.form["may_have"]
        list_len = request.form["list_len"]
        list_out = all_words.filter_words_blossom(must_have, all_words.unused_letters(must_have, may_have), list_len, words)

        # log clicks and inputs
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO blossom_clicks (click_time, must_have, may_have, list_len) 
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (must_have, may_have, list_len))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("blossom_bee.html", list_out=list_out, must_have_val=must_have, may_have_val=may_have, list_len_val=list_len)

    else:

        # log visits
        referrer = request.headers.get('Referer', 'No referrer')
        user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
        page_name = 'blossom_bee.html'
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("blossom_bee.html", list_len_val=25)


@app.route("/blossom", methods=["POST", "GET"])
def blossom_solver():
    if request.method == "POST":

        must_have = request.form["must_have"]
        may_have = request.form["may_have"]
        petal_letter = request.form["petal_letter"]
        list_len = request.form["list_len"]
        list_len = int(list_len)
        blossom_table, valid_word_count = all_words.filter_words_blossom_revamp(must_have, may_have, petal_letter, list_len, words)
        valid_word_count = f'Valid Word Count: {valid_word_count}'

        # log clicks and inputs
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO blossom_solver_clicks (click_time, must_have, may_have, petal_letter, list_len) 
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s, %s);
            """
            cursor.execute(query, (must_have, may_have, petal_letter, list_len))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("blossom.html", blossom_table=blossom_table, must_have_val=must_have, may_have_val=may_have, list_len_val=list_len, petal_letter=petal_letter, valid_word_count=valid_word_count)

    else:

        # log visits
        referrer = request.headers.get('Referer', 'No referrer')
        user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
        page_name = 'blossom.html'
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("blossom.html", list_len_val=25)

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

        # log visits
        referrer = request.headers.get('Referer', 'No referrer')
        user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
        page_name = 'any_word.html'
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

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

    # log visits
    referrer = request.headers.get('Referer', 'No referrer')
    user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
    page_name = 'resume.html'
    try:
        conn = get_pool_db_connection()
        cursor = conn.cursor()
        query = """
        INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
        VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
        """
        cursor.execute(query, (page_name, referrer, user_agent))
        conn.commit()
    except mysql.connector.Error as err:
        print("Error:", err)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()

    return render_template('resume.html')








######################################
######################################
##### youtube web scraping display
######################################
######################################

@app.route("/youtube_trending", methods=["POST", "GET"])
def youtube_trending():

    conn = get_pool_db_connection()
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

    # log visits
    referrer = request.headers.get('Referer', 'No referrer')
    user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
    page_name = 'youtube_trending.html'
    try:
        conn = get_pool_db_connection()
        cursor = conn.cursor()
        query = """
        INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
        VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
        """
        cursor.execute(query, (page_name, referrer, user_agent))
        conn.commit()
    except mysql.connector.Error as err:
        print("Error:", err)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()

    return render_template("youtube_trending.html", top_10_today=top_10_today, \
        top_10_title=top_10_title, top_10_channel=top_10_channel, top_categories=top_categories, \
        yt_stacked_bar_plot=temp_yt_stacked_bar_plot, yt_video_scatter=temp_yt_video_scatter, yt_chnl_scatter=temp_yt_chnl_scatter
        )








######################################
######################################
##### ETL status dashboard
######################################
######################################

# @app.route("/etl_dash", methods=["POST", "GET"])
# def etl_status_dash():

#     conn = get_pool_db_connection()
#     cursor = conn.cursor()
#     today = date.today().strftime("%Y-%m-%d")

#     cursor.execute("""SET time_zone = 'America/Los_Angeles';""")

#     all_dash = """
#     SELECT 'YouTube' AS table_name, MIN(collected_dt) AS min, MAX(collected_dt) AS max, COUNT(*) AS rows_count FROM youtube_trending
#     UNION ALL SELECT 'Spotify Playlists', MIN(collected_dt), MAX(collected_dt), COUNT(*) FROM spotify_playlists
#     UNION ALL SELECT 'Spotify Artists', MIN(collected_dt), MAX(collected_dt), COUNT(*) FROM spotify_artists
#     UNION ALL SELECT 'Spotify Tracks', MIN(collected_dt), MAX(collected_dt), COUNT(*) FROM spotify_tracks
#     UNION ALL SELECT 'LoL Summoner', MIN(pulled_dt), MAX(pulled_dt), COUNT(*) FROM lol_summoner
#     UNION ALL SELECT 'LoL Champion', MIN(pulled_dt), MAX(pulled_dt), COUNT(*) FROM lol_champion
#     UNION ALL SELECT 'LoL Match',
#         CONVERT_TZ(MIN(gameStartTimestamp), 'UTC', 'America/Los_Angeles') AS min_pst,
#         CONVERT_TZ(MAX(gameStartTimestamp), 'UTC', 'America/Los_Angeles') AS max_pst,
#         COUNT(*)
#     FROM lol_match;
#     """
#     cursor.execute(all_dash)
#     all_dash = cursor.fetchall()
#     all_dash = pd.DataFrame(all_dash, columns=['Table Name', 'Min Datetime', 'Max Datetime', 'Row Count'])

#     youtube_dash = """
#     SELECT
#     collected_dt
#     ,collected_date
#     ,count(1) cnt
#     FROM youtube_trending
#     GROUP BY collected_dt, collected_date
#     ORDER BY collected_dt DESC
#     LIMIT 10;
#     """
#     cursor.execute(youtube_dash)
#     youtube_dash = cursor.fetchall()
#     youtube_dash = pd.DataFrame(youtube_dash, columns=['Collected Datetime', 'Collected Date', 'Count of Rows'])


#     lol_dash = """
#     SELECT 'lol_summoner' AS table_name,  COUNT(1) AS row_count, (DATEDIFF(CURDATE(), MIN(pulled_date)) + 1) * 2 AS expected_cnt, MIN(pulled_dt) AS min_pulled_dt, MAX(pulled_dt) AS max_pulled_dt, NULL AS min_matchId, NULL AS max_matchId FROM lol_summoner
#         UNION ALL SELECT 'lol_champion' AS table_name, COUNT(1), 163, MIN(pulled_dt), MAX(pulled_dt), NULL, NULL FROM lol_champion
#         UNION ALL SELECT 'lol_all_match' AS table_name, COUNT(1), NULL, NULL, NULL, MIN(matchId), MAX(matchId) FROM lol_all_match
#         UNION ALL SELECT 'lol_match' AS table_name, COUNT(1), (SELECT COUNT(1) FROM lol_all_match), MIN(gameCreation), MAX(gameCreation), MIN(matchId), MAX(matchId) FROM lol_match
#         UNION ALL SELECT 'lol_participants_info' AS table_name, COUNT(1), (SELECT COUNT(1) * 10 FROM lol_all_match), NULL, NULL, MIN(matchId), MAX(matchId) FROM lol_participants_info
#         UNION ALL SELECT 'lol_participants_challenges' AS table_name, COUNT(1), (SELECT COUNT(1) * 10 FROM lol_all_match), NULL, NULL, MIN(matchId), MAX(matchId) FROM lol_participants_challenges;
#     """
#     cursor.execute(lol_dash)
#     lol_dash = cursor.fetchall()
#     lol_dash = pd.DataFrame(lol_dash, columns=['Table Name', 'Row Cnt', 'Expect Cnt', 'Min Datetime', 'Max Datetime', 'Min ID', 'Max ID'])


#     spotify_dash = """
#     WITH all_dates AS (
#         SELECT collected_date FROM spotify_playlists
#         UNION SELECT collected_date FROM spotify_artists
#         UNION SELECT collected_date FROM spotify_tracks
#         )
#     SELECT
#      all_dates.collected_date
#     ,COALESCE(p.playlist_count, 0) AS playlist_count
#     ,COALESCE(a.artist_count, 0) AS artist_count
#     ,COALESCE(t.track_count, 0) AS track_count
#     FROM all_dates
#     LEFT JOIN (
#         SELECT collected_date, COUNT(*) AS playlist_count
#         FROM spotify_playlists
#         GROUP BY collected_date
#         ) p ON all_dates.collected_date = p.collected_date
#     LEFT JOIN (
#         SELECT collected_date, COUNT(*) AS artist_count
#         FROM spotify_artists
#         GROUP BY collected_date
#         ) a ON all_dates.collected_date = a.collected_date
#     LEFT JOIN (
#         SELECT collected_date, COUNT(*) AS track_count
#         FROM spotify_tracks
#         GROUP BY collected_date
#         ) t ON all_dates.collected_date = t.collected_date
#     ORDER BY all_dates.collected_date DESC
#     LIMIT 10;
#     """
#     cursor.execute(spotify_dash)
#     spotify_dash = cursor.fetchall()
#     spotify_dash = pd.DataFrame(spotify_dash, columns=['Collected Date', 'Playlist Count', 'Artist Count', 'Track Count'])

#     cursor.close()
#     conn.close()

#     # log visits
#     referrer = request.headers.get('Referer', 'No referrer')
#     user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
#     page_name = 'etl_dash.html'
#     try:
#         conn = get_pool_db_connection()
#         cursor = conn.cursor()
#         query = """
#         INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
#         VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
#         """
#         cursor.execute(query, (page_name, referrer, user_agent))
#         conn.commit()
#     except mysql.connector.Error as err:
#         print("Error:", err)
#     finally:
#         if cursor is not None:
#             cursor.close()
#         if conn is not None and conn.is_connected():
#             conn.close()

#     return render_template("etl_dash.html", youtube_dash=youtube_dash, lol_dash=lol_dash, spotify_dash=spotify_dash, all_dash=all_dash)


@app.route("/etl_dash", methods=["POST", "GET"])
@auth.login_required
def etl_status_dash():

    conn = get_pool_db_connection()
    cursor = conn.cursor()

    queries = {
         'Blossom':
            """
            WITH all_clicks AS (
                SELECT 
                    DATE(click_time) AS calendar_day,
                    COUNT(*) AS clicks
                FROM blossom_solver_clicks
                WHERE DATE(click_time) NOT IN ('2024-02-11', '2024-02-12', '2024-02-13')
                GROUP BY DATE(click_time)
            ),
            timed_clicks AS (
                SELECT 
                    DATE(click_time) AS calendar_day,
                    COUNT(*) AS clicks
                FROM blossom_solver_clicks
                WHERE TIME(click_time) <= TIME(CONVERT_TZ(CURTIME(), 'UTC', 'America/Los_Angeles'))
                GROUP BY DATE(click_time)
            ),
            clicks_combined AS (
                SELECT 
                    all_clicks.calendar_day,
                    timed_clicks.clicks AS clicks_by_time,
                    all_clicks.clicks AS total_clicks
                FROM all_clicks
                LEFT JOIN timed_clicks ON all_clicks.calendar_day = timed_clicks.calendar_day
            ),
            minmax_values AS (
                SELECT
                    MIN(clicks_by_time) AS min_clicks_by_time,
                    MAX(clicks_by_time) AS max_clicks_by_time,
                    MIN(total_clicks) AS min_total_clicks,
                    MAX(total_clicks) AS max_total_clicks
                FROM clicks_combined
            )
            SELECT 
                c.calendar_day,
                SUBSTRING(DAYNAME(c.calendar_day), 1, 3) AS day_of_week, -- Extracts the three-letter abbreviation of the weekday
                c.clicks_by_time,
                CASE 
                    WHEN m.max_clicks_by_time = m.min_clicks_by_time THEN 1
                    ELSE ROUND((c.clicks_by_time - m.min_clicks_by_time) / (m.max_clicks_by_time - m.min_clicks_by_time), 2)
                END AS clicks_by_time_minmax,
                c.total_clicks,
                CASE 
                    WHEN m.max_total_clicks = m.min_total_clicks THEN 1
                    ELSE ROUND((c.total_clicks - m.min_total_clicks) / (m.max_total_clicks - m.min_total_clicks), 2)
                END AS total_clicks_minmax
            FROM clicks_combined c, minmax_values m
            ORDER BY c.calendar_day DESC
            LIMIT 25;
            """

        ,'Blossom Most Recent':
            """
            SELECT
            99999 AS id,
            CONVERT_TZ(NOW(), @@session.time_zone, 'America/Los_Angeles') AS click_time,
            TIMESTAMPDIFF(MINUTE, MAX(click_time), CONVERT_TZ(NOW(), @@session.time_zone, 'America/Los_Angeles')) AS lag_minute
            FROM blossom_solver_clicks
            UNION ALL
            SELECT
            id,
            click_time,
            TIMESTAMPDIFF(MINUTE, LAG(click_time) OVER (ORDER BY click_time), click_time) AS lag_minute
            FROM blossom_solver_clicks
            ORDER BY click_time DESC, id DESC
            LIMIT 11;
            """

        ,'Blossom Bee':
            """
            SELECT 
            DATE(click_time) AS calendar_day
            ,COUNT(*) AS clicks
            FROM blossom_clicks
            GROUP BY DATE(click_time)
            ORDER BY calendar_day desc
            LIMIT 10;
            """

        ,'Errors (Last 24 Hours)':
            """
            SELECT
            av.submit_time
            ,av.page_name
            ,av.referrer
            FROM app_visits AS av
            where page_name LIKE 'error.html%'
                AND page_name NOT LIKE 'error.html (undefined%'
                AND submit_time >= CONVERT_TZ(NOW() - INTERVAL 24 HOUR, '+00:00', '-08:00')
            ORDER BY page_name;
            """
    }

    query_dict = {}
    for name, query in queries.items():
        try:
            cursor.execute(query)
            result = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            query_df = pd.DataFrame(result, columns=columns)
            query_dict[name] = query_df
        except:
            print(f'{name} not run')

    cursor.close()
    conn.close()

    # log visits
    referrer = request.headers.get('Referer', 'No referrer')
    user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
    page_name = 'etl_dash.html'
    try:
        conn = get_pool_db_connection()
        cursor = conn.cursor()
        query = """
        INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
        VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
        """
        cursor.execute(query, (page_name, referrer, user_agent))
        conn.commit()
    except mysql.connector.Error as err:
        print("Error:", err)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()

    return render_template("etl_dash.html", query_dict=query_dict)


@app.route("/etl_dash2", methods=["POST", "GET"])
@auth.login_required
def etl_status_dash2():

    conn = get_pool_db_connection()
    cursor = conn.cursor()

    queries = {
         'High Level ETL':
            """
            SELECT 'YouTube' AS table_name, MIN(collected_dt) AS min, MAX(collected_dt) AS max, COUNT(*) AS rows_count FROM youtube_trending
            UNION ALL SELECT 'Spotify Playlists', MIN(collected_dt), MAX(collected_dt), COUNT(*) FROM spotify_playlists
            UNION ALL SELECT 'Spotify Artists', MIN(collected_dt), MAX(collected_dt), COUNT(*) FROM spotify_artists
            UNION ALL SELECT 'Spotify Tracks', MIN(collected_dt), MAX(collected_dt), COUNT(*) FROM spotify_tracks
            UNION ALL SELECT 'LoL Summoner', MIN(pulled_dt), MAX(pulled_dt), COUNT(*) FROM lol_summoner
            UNION ALL SELECT 'LoL Champion', MIN(pulled_dt), MAX(pulled_dt), COUNT(*) FROM lol_champion
            UNION ALL SELECT 'LoL Match',
                CONVERT_TZ(MIN(gameStartTimestamp), 'UTC', 'America/Los_Angeles') AS min_pst,
                CONVERT_TZ(MAX(gameStartTimestamp), 'UTC', 'America/Los_Angeles') AS max_pst,
                COUNT(*)
            FROM lol_match;
            """

        ,'Youtube Trending':
            """
            SELECT
            collected_dt
            ,collected_date
            ,count(1) cnt
            FROM youtube_trending
            GROUP BY collected_dt, collected_date
            ORDER BY collected_dt DESC
            LIMIT 10;
            """

        ,'Spotify':
            """
            WITH all_dates AS (
                SELECT collected_date FROM spotify_playlists
                UNION SELECT collected_date FROM spotify_artists
                UNION SELECT collected_date FROM spotify_tracks
                )
            SELECT
            all_dates.collected_date
            ,COALESCE(p.playlist_count, 0) AS playlist_count
            ,COALESCE(a.artist_count, 0) AS artist_count
            ,COALESCE(t.track_count, 0) AS track_count
            FROM all_dates
            LEFT JOIN (
                SELECT collected_date, COUNT(*) AS playlist_count
                FROM spotify_playlists
                GROUP BY collected_date
                ) p ON all_dates.collected_date = p.collected_date
            LEFT JOIN (
                SELECT collected_date, COUNT(*) AS artist_count
                FROM spotify_artists
                GROUP BY collected_date
                ) a ON all_dates.collected_date = a.collected_date
            LEFT JOIN (
                SELECT collected_date, COUNT(*) AS track_count
                FROM spotify_tracks
                GROUP BY collected_date
                ) t ON all_dates.collected_date = t.collected_date
            ORDER BY all_dates.collected_date DESC
            LIMIT 10;
            """

        ,'LoL':
            """
            SELECT 'lol_summoner' AS table_name,  COUNT(1) AS row_count, MIN(pulled_dt) AS min_pulled_dt, MAX(pulled_dt) AS max_pulled_dt, NULL AS min_matchId, NULL AS max_matchId FROM lol_summoner
                UNION ALL SELECT 'lol_champion' AS table_name, COUNT(1), MIN(pulled_dt), MAX(pulled_dt), NULL, NULL FROM lol_champion
                UNION ALL SELECT 'lol_all_match' AS table_name, COUNT(1), NULL, NULL, MIN(matchId), MAX(matchId) FROM lol_all_match
                UNION ALL SELECT 'lol_match' AS table_name, COUNT(1), MIN(gameCreation), MAX(gameCreation), MIN(matchId), MAX(matchId) FROM lol_match
                UNION ALL SELECT 'lol_participants_info' AS table_name, COUNT(1), NULL, NULL, MIN(matchId), MAX(matchId) FROM lol_participants_info
                UNION ALL SELECT 'lol_participants_challenges' AS table_name, COUNT(1), NULL, NULL, MIN(matchId), MAX(matchId) FROM lol_participants_challenges;
            """
    }

    query_dict = {}
    for name, query in queries.items():
        try:
            cursor.execute(query)
            result = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            query_df = pd.DataFrame(result, columns=columns)
            query_dict[name] = query_df
        except:
            print(f'{name} not run')

    cursor.close()
    conn.close()

    # log visits
    referrer = request.headers.get('Referer', 'No referrer')
    user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
    page_name = 'etl_dash.html'
    try:
        conn = get_pool_db_connection()
        cursor = conn.cursor()
        query = """
        INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
        VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
        """
        cursor.execute(query, (page_name, referrer, user_agent))
        conn.commit()
    except mysql.connector.Error as err:
        print("Error:", err)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()

    return render_template("etl_dash.html", query_dict=query_dict)








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

#         conn = get_pool_db_connection()
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

@app.route('/espresso', methods=['GET', 'POST'])
def espresso_route():

    google_credentials = espresso.google_sheets_base(GOOGLE_SHEETS_JSON)
    df_profile = espresso.get_google_sheets_profile(google_credentials, GOOGLE_SHEETS_URL_PROFILE)
    df_espresso_initial = espresso.get_google_sheets_espresso(google_credentials, GOOGLE_SHEETS_URL_ESPRESSO)
    scatter_espresso_col_labels = espresso.get_scatter_col_labels()
    valid_user_name_list, valid_roast_list, valid_shots_list = espresso.get_user_roast_values(df_espresso_initial)

    roast_options = ["Light", "Medium", "Medium Dark", "Dark"]
    dose_options = ["1", "2", "3"]

    user_pred = 'James'
    roast_pred = 'Medium'
    shots_pred = '2'
    espresso_x_col = 'flow_time_seconds'
    espresso_y_col = 'final_score'
    espresso_z_col = 'final_score'
    user_pred_scatter = 'James'
    roast_pred_scatter = 'Medium'
    shots_pred_scatter = '2'
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

        return render_template('espresso.html', valid_user_name_list=valid_user_name_list, valid_roast_list=valid_roast_list, valid_shots_list=valid_shots_list \
            ,optimal_parameters_dict=optimal_parameters_dict, performance_dict=performance_dict, good_run=good_run, user_pred_val=user_pred, roast_pred_val=roast_pred, shots_pred_val=shots_pred \
            ,espresso_scatter_plot=temp_espresso_scatter_plot, espresso_x_col_val=espresso_x_col, espresso_y_col_val=espresso_y_col, espresso_z_col_val=espresso_z_col \
            ,scatter_espresso_col_labels=scatter_espresso_col_labels, roast_options=roast_options, dose_options=dose_options \
            ,user_pred_scatter_val=user_pred_scatter, roast_pred_scatter_val=roast_pred_scatter, shots_pred_scatter_val=shots_pred_scatter \
            )

    else:
        df_analyze, df_scatter_blank = espresso.clean_espresso_df(user_pred, roast_pred, shots_pred, df_espresso_initial, df_profile, water_temp_na_val)
        optimal_parameters_dict, good_run, performance_dict = espresso.find_optimal_espresso_parameters(df_analyze)

        df_analyze_blank, df_scatter = espresso.clean_espresso_df(user_pred_scatter, roast_pred_scatter, shots_pred_scatter, df_espresso_initial, df_profile, water_temp_na_val)
        espresso_scatter_plot = espresso.espresso_dynamic_scatter(df_scatter, espresso_x_col, espresso_y_col, espresso_z_col)
        temp_espresso_scatter_plot = 'static/espresso_scatter.png'
        espresso_scatter_plot.savefig(temp_espresso_scatter_plot)

        # log visits
        referrer = request.headers.get('Referer', 'No referrer')
        user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
        page_name = 'espresso.html'
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template('espresso.html', valid_user_name_list=valid_user_name_list, valid_roast_list=valid_roast_list, valid_shots_list=valid_shots_list \
            ,optimal_parameters_dict=optimal_parameters_dict, performance_dict=performance_dict, good_run=good_run, user_pred_val=user_pred, roast_pred_val=roast_pred, shots_pred_val=shots_pred \
            ,espresso_scatter_plot=temp_espresso_scatter_plot, espresso_x_col_val=espresso_x_col, espresso_y_col_val=espresso_y_col, espresso_z_col_val=espresso_z_col \
            ,scatter_espresso_col_labels=scatter_espresso_col_labels, roast_options=roast_options, dose_options=dose_options \
            ,user_pred_scatter_val=user_pred_scatter, roast_pred_scatter_val=roast_pred_scatter, shots_pred_scatter_val=shots_pred_scatter \
            )


@app.route('/espresso_input', methods=['GET', 'POST'])
def espresso_input_route():

    google_credentials = espresso.google_sheets_base(GOOGLE_SHEETS_JSON)
    df_profile = espresso.get_google_sheets_profile(google_credentials, GOOGLE_SHEETS_URL_PROFILE)
    df_espresso_initial = espresso.get_google_sheets_espresso(google_credentials, GOOGLE_SHEETS_URL_ESPRESSO)
    valid_user_name_list, valid_roast_list, valid_shots_list = espresso.get_user_roast_values(df_espresso_initial)

    roast_options = ["Light", "Medium", "Medium Dark", "Dark"]
    dose_options = ["1", "2", "3"]

    roast = 'Medium'
    dose = "2"

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

        if 'roast' in request.form:
            roast = request.form['roast']
        if 'dose' in request.form:
            dose = request.form['dose']
        naive_espresso_info = espresso.get_naive_espresso_points(roast, dose, espresso_points)


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

        return render_template('espresso_input.html', naive_espresso_info=naive_espresso_info, roast_val=roast, dose_val=dose \
            ,valid_user_name_list=valid_user_name_list, valid_roast_list=valid_roast_list, valid_shots_list=valid_shots_list \
            ,roast_options=roast_options, dose_options=dose_options \
            ,furthest_point=furthest_point, scatter_3d=temp_scatter_3d_plot \
            ,distance_user_val=distance_user, distance_roast_val=distance_roast, distance_shots_val=distance_shots \
            ,distance_grind_min_val=distance_grind_min, distance_grind_max_val=distance_grind_max, distance_grind_granularity_val=distance_grind_granularity \
            ,distance_coffee_g_min_val=distance_coffee_g_min, distance_coffee_g_max_val=distance_coffee_g_max, distance_coffee_g_granularity_val=distance_coffee_g_granularity \
            ,distance_espresso_g_min_val=distance_espresso_g_min, distance_espresso_g_max_val=distance_espresso_g_max, distance_espresso_g_granularity_val=distance_espresso_g_granularity)

    else:
        naive_espresso_info = espresso.get_naive_espresso_points(roast, dose, espresso_points)

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

        # log visits
        referrer = request.headers.get('Referer', 'No referrer')
        user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
        page_name = 'espresso_input.html'
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template('espresso_input.html', naive_espresso_info=naive_espresso_info, roast_val=roast, dose_val=dose \
            ,valid_user_name_list=valid_user_name_list, valid_roast_list=valid_roast_list, valid_shots_list=valid_shots_list \
            ,roast_options=roast_options, dose_options=dose_options \
            ,furthest_point=furthest_point, scatter_3d=temp_scatter_3d_plot \
            ,distance_user_val=distance_user, distance_roast_val=distance_roast, distance_shots_val=distance_shots \
            ,distance_grind_min_val=distance_grind_min, distance_grind_max_val=distance_grind_max, distance_grind_granularity_val=distance_grind_granularity \
            ,distance_coffee_g_min_val=distance_coffee_g_min, distance_coffee_g_max_val=distance_coffee_g_max, distance_coffee_g_granularity_val=distance_coffee_g_granularity \
            ,distance_espresso_g_min_val=distance_espresso_g_min, distance_espresso_g_max_val=distance_espresso_g_max, distance_espresso_g_granularity_val=distance_espresso_g_granularity)






@app.route("/feedback", methods=["POST", "GET"])
def feedback():
    if request.method == "POST":

        feedback_header = request.form['feedback_header']
        feedback_body = request.form['feedback_body']
        referrer = request.form['referrer']

        # log inputs
        try:
            conn = get_pool_db_connection()
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

        # log visits
        referrer = request.headers.get('Referer', 'No referrer')
        user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
        page_name = 'feedback.html'
        try:
            conn = get_pool_db_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
            VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
            """
            cursor.execute(query, (page_name, referrer, user_agent))
            conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        return render_template("feedback.html")

@app.route("/feedback_received", methods=["GET"])
def feedback_received():
    return render_template("feedback_received.html")












# Error handler for 404 Not Found
@app.errorhandler(404)
def page_not_found(e):
    return_type = '404 - Page Not Found'

    # log visits
    referrer = request.headers.get('Referer', 'No referrer')
    user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
    page_name = f'error.html (404: {e})'
    try:
        conn = get_pool_db_connection()
        cursor = conn.cursor()
        query = """
        INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
        VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
        """
        cursor.execute(query, (page_name, referrer, user_agent))
        conn.commit()
    except mysql.connector.Error as err:
        print("Error:", err)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()

    return render_template('error.html', return_type=return_type), 404

@app.errorhandler(Exception)
def handle_exception(e):
    return_type = '500 - Error'

    # log visits
    referrer = request.headers.get('Referer', 'No referrer')
    user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
    page_name = f'error.html (500: {e})'
    try:
        conn = get_pool_db_connection()
        cursor = conn.cursor()
        query = """
        INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
        VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
        """
        cursor.execute(query, (page_name, referrer, user_agent))
        conn.commit()
    except mysql.connector.Error as err:
        print("Error:", err)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()

    return render_template('error.html', return_type=return_type), 500

# Optional: Catch-all route for undefined paths
@app.route('/<path:path>')
def catch_all(path):
    return_type = '404 - Undefined Path'

    # log visits
    referrer = request.headers.get('Referer', 'No referrer')
    user_agent = request.user_agent.string if request.user_agent.string else 'No User-Agent'
    page_name = f'error.html (undefined path: {path})'
    try:
        conn = get_pool_db_connection()
        cursor = conn.cursor()
        query = """
        INSERT INTO app_visits (submit_time, page_name, referrer, user_agent)
        VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
        """
        cursor.execute(query, (page_name, referrer, user_agent))
        conn.commit()
    except mysql.connector.Error as err:
        print("Error:", err)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()

    return render_template('error.html', return_type=return_type), 404

if __name__ == "__main__":
    app.run(debug=True)




# env\Scripts\activate
# pip freeze > requirements.txt

### buildpacks previously used, currently removed
# https://github.com/heroku/heroku-buildpack-google-chrome
# https://github.com/heroku/heroku-buildpack-chromedriver


