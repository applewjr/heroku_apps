from flask import Flask, redirect, render_template, request, redirect
import pandas as pd
import numpy as np
import os
import datetime
from datetime import date
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_sslify import SSLify
from werkzeug.middleware.proxy_fix import ProxyFix


########## local functions ##########

import sys
sys.path.append('/functions')

from functions import wordle

from functions import stocks
from functions import all_words
from functions import data_analysis


########## local data ##########

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
data_folder = os.path.join(APP_ROOT, 'datasets')

df = pd.read_csv(os.path.join(data_folder, 'word_data_created.csv'))

word_df = pd.read_csv(os.path.join(data_folder, 'all_words.csv'))
words = word_df['0'].to_list()
words = set(words)

df_demo_realtor = pd.read_csv(os.path.join(data_folder, 'realtor_data.csv'))
df_demo_titanic = pd.read_csv(os.path.join(data_folder, 'titanic_dataset.csv'))
df_demo_diabetes = pd.read_csv(os.path.join(data_folder, 'diabetes.csv'))


########## MySQL stuff ##########

import mysql.connector

if 'IS_HEROKU' in os.environ:
    # Running on Heroku, load values from Heroku Config Vars
    config = {
        'user': os.environ.get('jawsdb_user'),
        'password': os.environ.get('jawsdb_pass'),
        'host': os.environ.get('jawsdb_host'),
        'database': os.environ.get('jawsdb_db'),
        'raise_on_warnings': True
        }
else:
    # Running locally, load values from secret_pass.py
    import secret_pass
    config = {
        'user': secret_pass.mysql_user,
        'password': secret_pass.mysql_pass,
        'host': secret_pass.mysql_host,
        'database': secret_pass.mysql_bd,
        'raise_on_warnings': True
        }


########## Other SQL stuff ##########

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///test.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


##### SSL #####

sslify = SSLify(app)
app.config['SESSION_COOKIE_SECURE'] = True
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)











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
        return render_template("index.html")



######################################
######################################
##### high score
######################################
######################################

@app.route('/high_score', methods=['GET', 'POST'])
def game():

    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()

    if request.method == 'POST':
        initials = request.form['initials']
        score = request.form['score']
        cursor.execute("INSERT INTO high_scores (initials, score, timelog) VALUES (%s, %s, NOW())", (initials, score))
        conn.commit()

    conn = mysql.connector.connect(**config)
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
    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()
    # Retrieve tasks from the database
    cursor.execute('SELECT * FROM tasks')
    tasks = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('task.html', tasks=tasks)

@app.route('/add_task', methods=['POST'])
def add_task():
    conn = mysql.connector.connect(**config)
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
    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()
    # Delete the task from the database
    cursor.execute('DELETE FROM tasks WHERE id = %s', (task_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/task_mysql')

@app.route('/edit_task/<int:task_id>', methods=['GET', 'POST'])
def edit_task(task_id):
    conn = mysql.connector.connect(**config)
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
        return render_template("wordle.html", final_out1=final_out1, final_out2=final_out2, final_out3=final_out3, final_out4=final_out4, final_out5=final_out5, final_out_end=final_out_end, \
            must_not_be_present_val=must_not_be_present, present1_val=present1, present2_val=present2, present3_val=present3, present4_val=present4, present5_val=present5, \
            not_present1_val=not_present1, not_present2_val=not_present2, not_present3_val=not_present3, not_present4_val=not_present4, not_present5_val=not_present5, \
            suggested="Suggested word(s):")
    else:
        return render_template("wordle.html")

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
        return render_template("quordle.html")


@app.route("/fixer", methods=["POST", "GET"])
def run_wordle_fixer():
    if request.method == "POST":
        must_be_present = request.form["must_be_present"]
        final_out1, final_out2, final_out3, final_out4, final_out5 = wordle.find_word_with_letters(df, must_be_present)
        return render_template("fixer.html", final_out1=final_out1, final_out2=final_out2, final_out3=final_out3, final_out4=final_out4, final_out5=final_out5, must_be_present=must_be_present)
    else:
        return render_template("fixer.html")


@app.route("/wordle_example", methods=["POST", "GET"])
def run_wordle_example():
    if request.method == "POST":
        return render_template("wordle_example.html")
    else:
        return render_template("wordle_example.html")



######################################
######################################
##### stocks
######################################
######################################

@app.route("/stock_analysis", methods=["POST", "GET"])
def home():
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
        return render_template("stock_analysis.html", \
            stock_list_init_val='AAPL', trade_type_val='stock', contrib_amt_init_val=100, \
            # stock_list_init_val='AAPL', contrib_amt_init_val=100, \
            total_weeks_val=104, buyvalue_val=1.2, multiplier_val=5, nth_week_val=1, roll_days_val='quarter', trade_dow_val='Monday')



######################################
######################################
##### dog counter
######################################
######################################

@app.route('/dogs')
def dogs():
    return render_template('dog_count.html')



######################################
######################################
##### text editor
######################################
######################################

@app.route('/quill')
def quill():
    return render_template('quill.html')



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
        return render_template("common_denominator.html", min_match_len_val=3, min_match_rate_val=0.5, beg_end_str_char_val="|", value_split_char_val=",", \
            user_match_entry_val="Discectomy, Laminectomy, Foraminotomy, Corpectomy, Spinal (Lumbar) Fusion, Spinal Cord Stimulation", example=" (example set provided)")

@app.route("/blossom", methods=["POST", "GET"])
def blossom():
    if request.method == "POST":
        must_have = request.form["must_have"]
        may_have = request.form["may_have"]
        list_len = request.form["list_len"]
        list_out = all_words.filter_words_blossom(must_have, all_words.unused_letters(must_have, may_have), list_len, words)
        return render_template("blossom.html", list_out=list_out, must_have_val=must_have, may_have_val=may_have, list_len_val=list_len)
    else:
        return render_template("blossom.html", list_len_val=10)

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
        list_out = all_words.filter_words_all(must_have, must_not_have, first_letter, sort_order, list_len, words, min_length, max_length)
        return render_template("any_word.html", list_out=list_out, \
            must_have_val=must_have, must_not_have_val=must_not_have, first_letter_val=first_letter, sort_order_val=sort_order, list_len_val=list_len, \
            min_length_val=min_length, max_length_val=max_length)
    else:
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
    return render_template('resume.html')








######################################
######################################
##### youtube web scraping display
######################################
######################################

@app.route("/youtube_trending", methods=["POST", "GET"])
def youtube_trending():

    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()
    today = date.today().strftime("%Y-%m-%d")

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
    WHERE vid_rank <= 50
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
    WHERE vid_rank <= 50
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
    GROUP BY category
    ORDER BY top_1_count DESC, top_10_count DESC, top_50_count DESC  
    ;
    """
    cursor.execute(top_categories)
    top_categories = cursor.fetchall()
    top_categories = pd.DataFrame(top_categories, columns=['Category', 'Top 1 Count', 'Top 10 Count', 'Top 50 Count'])




























    oldest_date = """
    SELECT collected_date
    FROM youtube_trending
    ORDER BY collected_date ASC
    LIMIT 1
    """
    cursor.execute(oldest_date)
    oldest_date = cursor.fetchone()
    oldest_date = oldest_date[0].strftime("%Y-%m-%d")

    newest_date = """
    SELECT collected_date
    FROM youtube_trending
    ORDER BY collected_date DESC
    LIMIT 1
    """
    cursor.execute(newest_date)
    newest_date = cursor.fetchone()
    newest_date = newest_date[0].strftime("%Y-%m-%d")

    day_count = """
    SELECT COUNT(DISTINCT collected_date) AS dist_date
    FROM youtube_trending
    LIMIT 1
    """
    cursor.execute(day_count)
    day_count = cursor.fetchone()
    day_count = day_count[0]

    cursor.close()
    conn.close()

    return render_template("youtube_trending.html", oldest_date=oldest_date, day_count=day_count, top_10_today=top_10_today, \
        top_10_title=top_10_title, top_10_channel=top_10_channel, newest_date=newest_date, top_categories=top_categories)








######################################
######################################
##### ETL status dashboard
######################################
######################################

@app.route("/etl_dash", methods=["POST", "GET"])
def etl_status_dash():

    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()
    today = date.today().strftime("%Y-%m-%d")

    cursor.execute("""SET time_zone = 'America/Los_Angeles';""")
    youtube_dash = """
    SELECT
    collected_dt
    ,collected_date
    ,count(1) cnt
    FROM youtube_trending
    GROUP BY collected_dt, collected_date
    ORDER BY collected_dt DESC
    LIMIT 10;
    """
    cursor.execute(youtube_dash)
    youtube_dash = cursor.fetchall()
    youtube_dash = pd.DataFrame(youtube_dash, columns=['Collected Datetime', 'Collected Date', 'Count of Rows'])


    lol_dash = """
    SELECT 'lol_summoner' AS table_name,  COUNT(1) AS row_count, (DATEDIFF(CURDATE(), MIN(pulled_date)) + 1) * 2 AS expected_cnt, MIN(pulled_dt) AS min_pulled_dt, MAX(pulled_dt) AS max_pulled_dt, NULL AS min_matchId, NULL AS max_matchId FROM lol_summoner
        UNION ALL SELECT 'lol_champion' AS table_name, COUNT(1), 163, MIN(pulled_dt), MAX(pulled_dt), NULL, NULL FROM lol_champion
        UNION ALL SELECT 'lol_all_match' AS table_name, COUNT(1), NULL, NULL, NULL, MIN(matchId), MAX(matchId) FROM lol_all_match
        UNION ALL SELECT 'lol_match' AS table_name, COUNT(1), (SELECT COUNT(1) FROM lol_all_match), MIN(gameCreation), MAX(gameCreation), MIN(matchId), MAX(matchId) FROM lol_match
        UNION ALL SELECT 'lol_participants_info' AS table_name, COUNT(1), (SELECT COUNT(1) * 10 FROM lol_all_match), NULL, NULL, MIN(matchId), MAX(matchId) FROM lol_participants_info
        UNION ALL SELECT 'lol_participants_challenges' AS table_name, COUNT(1), (SELECT COUNT(1) * 10 FROM lol_all_match), NULL, NULL, MIN(matchId), MAX(matchId) FROM lol_participants_challenges;
    """
    cursor.execute(lol_dash)
    lol_dash = cursor.fetchall()
    lol_dash = pd.DataFrame(lol_dash, columns=['Table Name', 'Row Cnt', 'Expect Cnt', 'Min Datetime', 'Max Datetime', 'Min ID', 'Max ID'])


    spotify_dash = """
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
    cursor.execute(spotify_dash)
    spotify_dash = cursor.fetchall()
    spotify_dash = pd.DataFrame(spotify_dash, columns=['Collected Date', 'Playlist Count', 'Artist Count', 'Track Count'])

    cursor.close()
    conn.close()

    return render_template("etl_dash.html", youtube_dash=youtube_dash, lol_dash=lol_dash, spotify_dash=spotify_dash)










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

#         conn = mysql.connector.connect(**config)
#         cursor = conn.cursor()
#         # Execute the query
#         cursor.execute(f"SELECT words FROM all_words {query}")

#         # Get the results
#         results = cursor.fetchall()

#         cursor.close()
#         conn.close()

#         return render_template('any_word_sql.html', query=query, results=results)

#     return render_template('any_word_sql.html', query="-- Example:\nWHERE words LIKE 'z%a'\nORDER BY LENGTH(words) DESC\nLIMIT 5;")











if __name__ == "__main__":
    app.run(debug=True)




# env\Scripts\activate
# pip freeze > requirements.txt

### buildpacks previously used, currently removed
# https://github.com/heroku/heroku-buildpack-google-chrome
# https://github.com/heroku/heroku-buildpack-chromedriver


