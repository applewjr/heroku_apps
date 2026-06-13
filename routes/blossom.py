"""Blossom solver, admin word management, and feedback routes."""

import smtplib
from datetime import datetime
from email.mime.text import MIMEText

import mysql.connector
import pytz
from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for

import config
from data import words_blossom
from extensions import auth, cache, db_cursor, limiter, log_page_visit
from functions import all_words
from helpers import make_schema_data

bp = Blueprint('blossom', __name__)


@bp.route("/blossom", methods=["POST", "GET"])
@limiter.limit("30 per minute")
def blossom_solver():
    try:

        schema_data = make_schema_data(
            "Blossom Word Finder & Solver",
            "Free Blossom word finder & solver. Instantly find all words and answers to solve today's Merriam-Webster Blossom puzzle.",
            "https://jamesapplewhite.com/blossom",
            operating_system=None
        )

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
            must_have = request.form.get("must_have")
            may_have = request.form.get("may_have")
            petal_letter = request.form.get("petal_letter")

            if must_have is None or may_have is None or petal_letter is None:
                current_app.logger.error(
                    f"Blossom POST missing fields - content_type={request.content_type!r} "
                    f"form_keys={list(request.form.keys())} "
                    f"data={request.get_data(as_text=True)[:500]!r} "
                    f"ua={request.headers.get('User-Agent')!r} "
                    f"ip={request.remote_addr}"
                )
                return render_template('error.html', return_type='Blossom Error'), 400

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
            blossom_table, total_valid_words, show_load_more, pangrams = all_words.filter_words_blossom_revamp(
                must_have, may_have, petal_letter, current_count, words_blossom_filtered, used_words
            )
            valid_word_count = f'Showing {min(current_count, total_valid_words)} of {total_valid_words} words'

            # Make session permanent (4 hours)
            session.permanent = True

            # log clicks and inputs - use actual displayed count
            try:
                with db_cursor() as (conn, cursor):
                    query = """
                    INSERT INTO blossom_solver_clicks (click_time, must_have, may_have, petal_letter, list_len)
                    VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s, %s);
                    """
                    cursor.execute(query, (must_have, may_have, petal_letter, min(current_count, total_valid_words)))
                    conn.commit()
            except mysql.connector.PoolError:
                # Pool exhausted - just skip logging, don't break the app
                print("Skipping blossom click log - connection pool busy")
            except mysql.connector.Error as err:
                print("Error:", err)

            return render_template("blossom.html",
                                blossom_table=blossom_table,
                                must_have_val=must_have,
                                may_have_val=may_have,
                                petal_letter=petal_letter,
                                valid_word_count=valid_word_count,
                                used_words=used_words,
                                current_count=current_count,
                                show_load_more=show_load_more,
                                pangrams=pangrams,
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
        current_app.logger.error(
            f"Error R99 (Blossom failed): {str(e)} - IP: {request.remote_addr} "
            f"method={request.method} content_type={request.content_type!r} "
            f"form_keys={list(request.form.keys())}",
            exc_info=True
        )
        return render_template('error.html', return_type='Blossom Error'), 500


@bp.route("/blossom/reset")
def blossom_reset():
    # Clear the used_words from session
    session.pop('used_words', None)
    session.modified = True

    # Redirect back to the main blossom page
    return redirect(url_for('blossom.blossom_solver'))


@bp.route('/blossom_admin')
@auth.login_required
def blossom_admin():
    """Admin page to manage invalid and missing words"""
    try:
        with db_cursor() as (conn, cursor):
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

        return render_template('blossom_admin.html',
                             invalid_words=invalid_words,
                             added_words=added_words,
                             recent_feedback=recent_feedback)

    except Exception as e:
        print(f"Error loading blossom admin: {e}")
        return render_template('error.html', return_type='Admin Error'), 500


@bp.route('/add_word', methods=['POST'])
@auth.login_required
def add_word():
    """Add a word to the added words list (for missing words)"""
    try:
        word = request.form.get('word', '').strip().lower()
        word_type = request.form.get('word_type', 'invalid')  # 'invalid' or 'missing'

        if not word:
            return redirect('/blossom_admin?error=Word is required')

        if word_type == 'missing':
            table = 'blossom_added_words'
        else:
            table = 'blossom_invalid_words'

        with db_cursor() as (conn, cursor):
            cursor.execute(f"""
                INSERT IGNORE INTO {table} (word, added_date)
                VALUES (%s, CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'))
            """, (word,))
            conn.commit()

        # Clear cache to force refresh
        cache.delete('blossom_filtered_words')

        action = "added to word list" if word_type == 'missing' else "marked as invalid"
        return redirect(f'/blossom_admin?success=Word {action} successfully')

    except Exception as e:
        print(f"Error adding word: {e}")
        return redirect('/blossom_admin?error=Database error')


@bp.route('/remove_word', methods=['POST'])
@auth.login_required
def remove_word():
    """Remove a word from either invalid or added words list"""
    try:
        word = request.form.get('word', '').strip().lower()
        word_type = request.form.get('word_type', 'invalid')  # 'invalid' or 'missing'

        if not word:
            return redirect('/blossom_admin?error=Word is required')

        if word_type == 'missing':
            table = 'blossom_added_words'
        else:
            table = 'blossom_invalid_words'

        with db_cursor() as (conn, cursor):
            cursor.execute(f"DELETE FROM {table} WHERE word = %s", (word,))
            conn.commit()

        # Clear cache to force refresh
        cache.delete('blossom_filtered_words')

        return redirect('/blossom_admin?success=Word removed successfully')

    except Exception as e:
        print(f"Error removing word: {e}")
        return redirect('/blossom_admin?error=Database error')


@bp.route("/blossom_feedback", methods=["POST", "GET"])
def blossom_feedback():
    if request.method == "POST":
        report_type = request.form['report_type']
        feedback_body = request.form['feedback_body']
        referrer = request.form['referrer']

        # Log inputs to new feedback_blossom table
        feedback_id = None
        try:
            with db_cursor() as (conn, cursor):
                query = """
                INSERT INTO feedback_blossom (submit_time, referrer, report_type, reported_words)
                VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
                """
                cursor.execute(query, (referrer, report_type, feedback_body))
                conn.commit()
                feedback_id = cursor.lastrowid  # Get the ID of the inserted record
        except mysql.connector.Error as err:
            print("Error:", err)

        # Send email notification
        if config.BLOSSOM_EMAIL_FLAG == 1:
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
                msg['From'] = config.GMAIL_SENDER_EMAIL
                msg['To'] = config.GMAIL_RECEIVER_EMAIL

                with smtplib.SMTP('smtp.gmail.com', 587) as server:
                    server.starttls()
                    server.login(config.GMAIL_SENDER_EMAIL, config.GMAIL_PASS)
                    server.sendmail(config.GMAIL_SENDER_EMAIL, config.GMAIL_RECEIVER_EMAIL, msg.as_string())
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
        with db_cursor() as (conn, cursor):
            cursor.execute("SELECT word FROM blossom_invalid_words")
            invalid_words = {row[0].lower() for row in cursor.fetchall()}

            cursor.execute("SELECT word FROM blossom_added_words")
            added_words = {row[0].lower() for row in cursor.fetchall()}
    except Exception as e:
        print(f"Error fetching word lists: {e}")
        # If database error, use original word list
        invalid_words = set()
        added_words = set()

    # Ensure both word lists are lowercase for proper comparison
    words_blossom_lower = {str(word).lower() for word in words_blossom}

    # Remove invalid words and add missing words
    filtered_words_lower = (words_blossom_lower - invalid_words) | added_words

    # Cache the filtered word list for 12 hours
    cache.set('blossom_filtered_words', filtered_words_lower, timeout=43200)

    return filtered_words_lower
