"""Blossom solver, admin word management, and feedback routes."""

import smtplib
import time
from datetime import datetime
from email.mime.text import MIMEText

import pytz
from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for

import config
import crowd
# The CROWD_MAX_* names are re-exported for tests/test_blossom.py.
from crowd import (CROWD_ADD_VOTES, CROWD_MAX_INVALID_PER_DAY, CROWD_MAX_VOTES_PER_DAY,  # noqa: F401
                   CROWD_REMOVE_VOTES_BY_POP, CROWD_REMOVE_VOTES_RARE,
                   CROWD_WINDOW_DAYS)
from data import word_pop, words_blossom
from extensions import (INTERACTIVE_LIMITS, auth, db_cursor, enqueue_write,
                        limiter, log_page_visit, pst_now_str)
from functions import all_words
from helpers import ValidationError, make_schema_data, parse_int, parse_letters

bp = Blueprint('blossom', __name__)

# Written per keystroke, so it goes through the write-behind queue rather than
# inline. Timestamp is stamped in Python at enqueue time; see pst_now_str.
BLOSSOM_CLICKS_SQL = """
INSERT INTO blossom_solver_clicks (click_time, must_have, may_have, petal_letter, list_len)
VALUES (%s, %s, %s, %s, %s);
"""


@bp.route("/blossom", methods=["POST", "GET"])
@limiter.limit(INTERACTIVE_LIMITS)
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
                # silent: a body that isn't JSON used to raise BadRequest inside
                # the broad except below and come back as a logged 500.
                data = request.get_json(silent=True)
                if not isinstance(data, dict):
                    raise ValidationError("expected a JSON object")
                if data.get('action') == 'toggle_invalid':
                    return _toggle_invalid(data)
                if data.get('action') == 'suggest_missing':
                    return _suggest_missing(data)
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

            # Scanner junk (sqlmap payloads etc.) in these fields -> 400 via
            # ValidationError, instead of a ValueError 500 further down.
            must_have = parse_letters(must_have, "must_have", max_len=10)
            may_have = parse_letters(may_have, "may_have", max_len=10)
            petal_letter = parse_letters(petal_letter, "petal_letter", max_len=10)

            # Handle load more functionality
            current_count = parse_int(request.form.get("current_count"), "current_count",
                                      default=25, min_value=1, max_value=10000)
            if request.form.get("load_more"):
                current_count = min(current_count + 25, 10000)

            # Get used words, and words this player flagged invalid, from session
            used_words = session.get('used_words', [])
            invalid_words = session.get('invalid_words', [])

            # Get the blossom table and modify it to include checkboxes
            words_blossom_filtered = get_filtered_blossom_words()
            blossom_table, total_valid_words, show_load_more, pangrams = all_words.filter_words_blossom_revamp(
                must_have, may_have, petal_letter, current_count, words_blossom_filtered, used_words,
                invalid_words
            )
            valid_word_count = f'Showing {min(current_count, total_valid_words)} of {total_valid_words} words'

            # Make session permanent (4 hours)
            session.permanent = True

            # log clicks and inputs - use actual displayed count. Queued
            # rather than written inline: this POST fires on every keystroke,
            # so an INSERT here would sit between the user typing and the
            # solver answering. enqueue_write never blocks and never raises.
            enqueue_write(BLOSSOM_CLICKS_SQL, (
                pst_now_str(),
                must_have,
                may_have,
                petal_letter,
                min(current_count, total_valid_words),
            ))

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

    except ValidationError:
        # Bad input, not a bug: let the app-level handler answer 400 so the
        # blanket except below can't turn it into a 500 + logged traceback.
        raise
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


def _admin_form():
    """The word and its list, or None for the list if no choice was sent.
    Nothing is assumed: a missing choice must not count as a removal."""
    word = request.form.get('word', '').strip().lower()
    kind = request.form.get('word_type')
    return word, kind if kind in ('invalid', 'missing') else None


NO_CHOICE = 'Choose Remove invalid word or Add missing word first'


@bp.route('/blossom_admin')
@auth.login_required
def blossom_admin():
    """Blossom's corrected word list, what players have reported, and the
    old report form's submissions."""
    try:
        # source is 'admin' (added here) or 'crowd' (enough players agreed -
        # see "crowd corrections" below).
        view = BLOSSOM_CROWD.admin_view(request.args.to_dict(), request.path,
                                        tabs=('reports', 'feedback', 'invalid', 'added'))

        with db_cursor() as (conn, cursor):
            # Get recent feedback
            cursor.execute("""
                SELECT id, submit_time, report_type, reported_words
                FROM feedback_blossom
                ORDER BY submit_time DESC
                LIMIT 10
            """)
            recent_feedback = cursor.fetchall()

        return render_template('blossom_admin.html',
                             **view,
                             recent_feedback=recent_feedback,
                             word_pop=word_pop,
                             crowd_remove_bands=CROWD_REMOVE_VOTES_BY_POP,
                             crowd_remove_rare=CROWD_REMOVE_VOTES_RARE,
                             crowd_add_votes=CROWD_ADD_VOTES,
                             crowd_window_days=CROWD_WINDOW_DAYS)

    except Exception as e:
        print(f"Error loading blossom admin: {e}")
        return render_template('error.html', return_type='Admin Error'), 500


@bp.route('/add_word', methods=['POST'])
@auth.login_required
def add_word():
    """Remove a word from Blossom's list ('invalid') or add one ('missing')."""
    word, kind = _admin_form()
    if kind is None:
        return redirect(f'/blossom_admin?error={NO_CHOICE}')
    if not word.isalpha():
        return redirect('/blossom_admin?error=Word must be letters only')
    try:
        BLOSSOM_CROWD.admin_set(word, kind)
    except Exception as e:
        print(f"Error adding word: {e}")
        return redirect('/blossom_admin?error=Database error')
    done = 'added to the word list' if kind == 'missing' else 'removed from the word list'
    return redirect(f'/blossom_admin?success={word} {done}')


@bp.route('/remove_word', methods=['POST'])
@auth.login_required
def remove_word():
    """Undo an entry in either list, and reset that word's votes."""
    word, kind = _admin_form()
    if kind is None:
        return redirect(f'/blossom_admin?error={NO_CHOICE}')
    if not word.isalpha():
        return redirect('/blossom_admin?error=Word must be letters only')
    try:
        BLOSSOM_CROWD.admin_remove(word, kind)
    except Exception as e:
        print(f"Error removing word: {e}")
        return redirect('/blossom_admin?error=Database error')
    return redirect(f'/blossom_admin?success={word} undone')


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
        except Exception as err:
            # Broad on purpose: feedback_id stays None and the notification
            # email below still goes out, so a database problem costs the row
            # but never the report - and never a 500 for the reporter.
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


# Memoized in the module, not SimpleCache: SimpleCache pickles, so every
# solve unpickled the ~1 MB set into ~9 MB of fresh strings (49 ms of CPU,
# measured 2026-10-10), and that churn fragmented memory: one of the two
# causes of the dyno's creep. Same pattern as routes/smush.py. Holds no copy
# of the word list at all while Blossom's corrections are empty.
_blossom_words = {'value': None, 'expires': 0.0}


def _forget_blossom_words():
    """Make the next get_filtered_blossom_words() re-read the corrections."""
    _blossom_words['expires'] = 0.0


def get_filtered_blossom_words():
    """Blossom's word list with its corrections applied: words removed by
    players or /blossom_admin taken out, words added put in.

    Refreshed every 12 hours, or straight away after a change. Callers share
    one set, so they must only read it. A DB blip keeps serving the last good
    list (the raw one if there has never been one) and retries in 5 minutes.
    """
    if _blossom_words['value'] is not None and time.time() < _blossom_words['expires']:
        return _blossom_words['value']

    try:
        with db_cursor() as (conn, cursor):
            cursor.execute("SELECT word FROM blossom_invalid_words")
            invalid_words = {row[0].lower() for row in cursor.fetchall()}
            cursor.execute("SELECT word FROM blossom_added_words")
            added_words = {row[0].lower() for row in cursor.fetchall()}
    except Exception as e:
        print(f"Error fetching word lists: {e}")
        fallback = _blossom_words['value'] if _blossom_words['value'] is not None else words_blossom
        _blossom_words['value'] = fallback
        _blossom_words['expires'] = time.time() + 300
        return fallback

    # data.py's list is already lowercase; only build a corrected copy when
    # there is something to correct.
    if invalid_words or added_words:
        filtered = (words_blossom - invalid_words) | added_words
    else:
        filtered = words_blossom
    _blossom_words['value'] = filtered
    _blossom_words['expires'] = time.time() + 43200
    return filtered


##### crowd corrections #####

# Players fix the word list themselves: an Invalid box on every result row,
# and a "Blossom accepted a word that isn't listed?" box under the table. The
# mechanics, thresholds and the never-tell-players rule live in crowd.py,
# shared with /smush; only Blossom's own tables are touched here.
#
# Measured against prod on 2026-09-30, before choosing the thresholds:
# - The old report form was right every time: 16 of 16 "invalid" reports on
#   listed words were confirmed, and 13 of 13 "missing" reports were added.
# - It was also rare. Each recently reported word was on screen in 170-350
#   solves on its puzzle day (stuccoers 252, picritic 168, hinnying 285,
#   yatagan 215, figuline 352, ingulfing 354) and drew exactly one report.
# - 63-86% of a day's solves are on the one daily puzzle, so a bad word near
#   the top of today's list collects its flags within hours.
#
# Invalid strikes the row just like Used, so some players will use it to
# cross off words they played. With ~150 players a day on one puzzle, 5 to
# remove holds until about 3% of them do it.

BLOSSOM_CROWD = crowd.CrowdList('blossom', on_change=_forget_blossom_words)
voter_hash = BLOSSOM_CROWD.voter_hash


def _parse_word(value):
    word = parse_letters(value if isinstance(value, str) else None, 'word', max_len=30).lower()
    if len(word) < 4:
        raise ValidationError("word must be at least 4 letters")
    return word


def _parse_puzzle(data):
    """The board a vote came from: (center, petals), lowercase.

    The page sends the letters its results table was built from, so anything
    other than one center letter and six petals is not the page talking.
    """
    center, petals = data.get('center'), data.get('petals')
    center = parse_letters(center if isinstance(center, str) else None, 'center', max_len=1).lower()
    petals = parse_letters(petals if isinstance(petals, str) else None, 'petals', max_len=6).lower()
    if len(center) != 1 or len(petals) != 6:
        raise ValidationError("center must be 1 letter and petals 6 letters")
    return center, petals


def _fits_puzzle(word, center, petals):
    """Could this word be played on this board, by Blossom's own rules?"""
    return len(word) >= 4 and center in word and set(word) <= set(center + petals)


def _puzzle_tag(center, petals):
    return center + ':' + ''.join(sorted(petals))


def _vote_mode(data):
    """'normal' or 'helper' (the page's Helper Mode, which masks the words but
    keeps the Invalid box), for the vote log only. None when the page didn't
    say - never a reason to refuse the vote."""
    mode = data.get('mode')
    return mode if mode in ('normal', 'helper') else None


def _toggle_invalid(data):
    """Tick or untick a word's Invalid box: the player says Blossom rejected it."""
    word = _parse_word(data.get('word'))
    center, petals = _parse_puzzle(data)

    if 'invalid_words' not in session:
        session['invalid_words'] = []
    flagged = session['invalid_words']
    if word in flagged:
        flagged.remove(word)
        BLOSSOM_CROWD.retract_vote(word, 'invalid', _puzzle_tag(center, petals), _vote_mode(data))
    else:
        flagged.append(word)
        # Only a word actually on offer, on a board it fits: anything else is
        # not a result this player could have been shown.
        if word in get_filtered_blossom_words() and _fits_puzzle(word, center, petals):
            BLOSSOM_CROWD.record_vote(word, 'invalid', _puzzle_tag(center, petals), _vote_mode(data))
    session.modified = True
    # Same reply whether or not this vote removed the word (see crowd.py).
    return jsonify({'status': 'success', 'invalid_words': flagged})


def _suggest_missing(data):
    """A word Blossom accepted that the solver doesn't list."""
    word = _parse_word(data.get('word'))
    center, petals = _parse_puzzle(data)
    if not _fits_puzzle(word, center, petals):
        return jsonify({'status': 'success', 'result': 'not_in_puzzle'})
    if word in get_filtered_blossom_words():
        return jsonify({'status': 'success', 'result': 'already_listed'})
    # 'recorded' whether or not this vote added the word (see crowd.py).
    BLOSSOM_CROWD.record_vote(word, 'missing', _puzzle_tag(center, petals), _vote_mode(data))
    return jsonify({'status': 'success', 'result': 'recorded'})
