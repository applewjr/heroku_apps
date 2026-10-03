"""Blossom solver, admin word management, and feedback routes."""

import hashlib
import hmac
import ipaddress
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText

import pytz
from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for

import config
from data import words_blossom
from extensions import (INTERACTIVE_LIMITS, auth, cache, db_cursor, enqueue_write,
                        limiter, log_page_visit, pst_now_str)
from functions import all_words
from helpers import ValidationError, client_ip, make_schema_data, parse_int, parse_letters
from monitoring import alerts

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


@bp.route('/blossom_admin')
@auth.login_required
def blossom_admin():
    """Admin page to manage invalid and missing words"""
    try:
        with db_cursor() as (conn, cursor):
            # Get invalid words. source is 'admin' (added here) or 'crowd'
            # (enough players agreed - see "crowd corrections" below).
            cursor.execute("""
                SELECT word, added_date, source
                FROM blossom_invalid_words
                ORDER BY added_date DESC
            """)
            invalid_words = cursor.fetchall()

            # Get added words
            cursor.execute("""
                SELECT word, added_date, source
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
                             recent_feedback=recent_feedback,
                             crowd_remove_votes=CROWD_REMOVE_VOTES,
                             crowd_add_votes=CROWD_ADD_VOTES)

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
            table, other = 'blossom_added_words', 'blossom_invalid_words'
        else:
            table, other = 'blossom_invalid_words', 'blossom_added_words'

        # Your call is the final one: marked 'admin' (upgrading any crowd row)
        # so the crowd never reverses it, taken out of the other list so the
        # two can't disagree, and the crowd's votes on it start over.
        with db_cursor() as (conn, cursor):
            cursor.execute(f"""
                INSERT INTO {table} (word, added_date, source)
                VALUES (%s, CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), 'admin')
                ON DUPLICATE KEY UPDATE source = 'admin'
            """, (word,))
            cursor.execute(f"DELETE FROM {other} WHERE word = %s", (word,))
            cursor.execute("DELETE FROM blossom_word_votes WHERE word = %s", (word,))
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

        # Clearing its votes makes this a clean undo of a crowd change too:
        # the crowd starts again from zero rather than re-applying it on the
        # next vote.
        with db_cursor() as (conn, cursor):
            cursor.execute(f"DELETE FROM {table} WHERE word = %s", (word,))
            cursor.execute("DELETE FROM blossom_word_votes WHERE word = %s", (word,))
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


def get_filtered_blossom_words():
    """Get word list with invalid words removed and missing words added, using cache for performance"""

    # Check cache first
    cached_words = cache.get('blossom_filtered_words')
    if cached_words is not None:
        return cached_words

    # Get invalid and added words from database
    invalid_words = set()
    added_words = set()
    db_ok = True
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute("SELECT word FROM blossom_invalid_words")
            invalid_words = {row[0].lower() for row in cursor.fetchall()}

            cursor.execute("SELECT word FROM blossom_added_words")
            added_words = {row[0].lower() for row in cursor.fetchall()}
    except Exception as e:
        print(f"Error fetching word lists: {e}")
        # If database error, use original word list
        db_ok = False
        invalid_words = set()
        added_words = set()

    # Ensure both word lists are lowercase for proper comparison
    words_blossom_lower = {str(word).lower() for word in words_blossom}

    # Remove invalid words and add missing words
    filtered_words_lower = (words_blossom_lower - invalid_words) | added_words

    # Cache the filtered list for 12 hours - but only for 5 minutes when the
    # DB read failed, so a blip doesn't serve the uncorrected list all day.
    cache.set('blossom_filtered_words', filtered_words_lower,
              timeout=43200 if db_ok else 300)

    return filtered_words_lower


##### crowd corrections #####

# Players fix the word list themselves: an Invalid box on every result row,
# and a "Blossom accepted a word that isn't listed?" box under the table. A
# change applies once enough *different* players agree. Nobody reviews it;
# /blossom_admin's Remove is the undo (it also resets the word's votes).
#
# Players are never told these numbers, or that their vote was the one that
# tipped a word over: page copy only says reports are taken into account, and
# the JSON replies below are identical either way. Knowing the count is the
# first thing anyone gaming this would want. Admin pages show them freely.
#
# Measured against prod on 2026-09-30, before choosing these numbers:
# - The old report form was right every time: 16 of 16 "invalid" reports on
#   listed words were confirmed, and 13 of 13 "missing" reports were added.
# - It was also rare. Each recently reported word was on screen in 170-350
#   solves on its puzzle day (stuccoers 252, picritic 168, hinnying 285,
#   yatagan 215, figuline 352, ingulfing 354) and drew exactly one report.
# - 63-86% of a day's solves are on the one daily puzzle, so a bad word near
#   the top of today's list collects its flags within hours.
#
# Five to remove (raised from 3 on 2026-10-03, James's call). A tap is cheap,
# and Invalid strikes the row just like Used, so some players will use it to
# cross off words they played. Those players all tick the same popular words,
# so the threshold sets how much of that the list can absorb: with ~150
# players a day on one puzzle, 5 holds until about 3% of them do it.
# CROWD_MAX_INVALID_PER_DAY below deals with the heavy cases outright.
# Two to add because the word has to be typed and has to fit the puzzle
# letters - but not one, because long words score highest and one player's
# typo ("unfulfiling") would otherwise land at the top of everyone's list.
CROWD_REMOVE_VOTES = 5
CROWD_ADD_VOTES = 2
# A player who ticks Invalid on more than this many words in one day (Pacific,
# midnight to midnight) is crossing words off, not reporting rejections:
# genuine ones run 1-2 per puzzle (9/29 hinnying and yatagan, 9/30 figuline and
# ingulfing). None of that player's Invalid ticks from that day count -
# including the ones made before they passed the limit. James's call,
# 2026-10-03; check it against real per-player counts once there are some.
CROWD_MAX_INVALID_PER_DAY = 10
# Older votes stop counting, so occasional mis-taps on a good word cannot add
# up across months of puzzles. A genuinely bad word gets its votes on its
# puzzle day anyway.
CROWD_WINDOW_DAYS = 7
# Per player, per rolling day. Real players flag a handful of words a puzzle;
# this bounds what anyone holding three IP addresses could do.
CROWD_MAX_VOTES_PER_DAY = 30

_CROWD_THRESHOLD = {'invalid': CROWD_REMOVE_VOTES, 'missing': CROWD_ADD_VOTES}
_CROWD_OPPOSITE = {'invalid': 'missing', 'missing': 'invalid'}
# The list a crowd decision lands in, and the list it takes the word out of.
_CROWD_TABLES = {
    'invalid': ('blossom_invalid_words', 'blossom_added_words'),
    'missing': ('blossom_added_words', 'blossom_invalid_words'),
}


def voter_hash():
    """Who is voting: an HMAC of the visitor's IP, never the IP itself.

    HMAC rather than a plain hash because the IPv4 space is small enough to
    brute-force a bare SHA-256 straight back to the address. IPv6 is cut to
    its /64: privacy extensions rotate the low 64 bits, so otherwise one
    phone would count as a new player every day.
    """
    ip = client_ip()
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        parsed = None
    if parsed is not None and parsed.version == 6:
        if parsed.ipv4_mapped is not None:
            ip = str(parsed.ipv4_mapped)
        else:
            ip = str(ipaddress.ip_network(f'{parsed}/64', strict=False).network_address)
    key = str(current_app.secret_key).encode()
    return hmac.new(key, b'blossom-vote|' + ip.encode(), hashlib.sha256).hexdigest()[:32]


def _pst_days_ago(days):
    """pst_now_str's format, `days` back - for comparing against created_at."""
    then = datetime.now(pytz.timezone('America/Los_Angeles')) - timedelta(days=days)
    return then.strftime('%Y-%m-%d %H:%M:%S')


def _pst_today_start():
    """Midnight Pacific today, in created_at's format."""
    return datetime.now(pytz.timezone('America/Los_Angeles')).strftime('%Y-%m-%d 00:00:00')


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


def _count_players(cursor, word, kind):
    """Distinct players behind a change to this word, inside the vote window.

    For Invalid, a vote only counts if its player ticked no more than
    CROWD_MAX_INVALID_PER_DAY words on the Pacific day they cast it.
    """
    sql = ("SELECT COUNT(*) FROM blossom_word_votes v "
           "WHERE v.word = %s AND v.vote = %s AND v.created_at >= %s")
    params = [word, kind, _pst_days_ago(CROWD_WINDOW_DAYS)]
    if kind == 'invalid':
        sql += (" AND (SELECT COUNT(*) FROM blossom_word_votes d"
                " WHERE d.voter_hash = v.voter_hash AND d.vote = 'invalid'"
                " AND d.created_at >= DATE(v.created_at)"
                " AND d.created_at < DATE(v.created_at) + INTERVAL 1 DAY) <= %s")
        params.append(CROWD_MAX_INVALID_PER_DAY)
    cursor.execute(sql, tuple(params))
    return cursor.fetchone()[0]


def _put_back_unsupported(cursor, words):
    """Undo crowd removals among `words` that no longer have enough players.

    Called when a player passes CROWD_MAX_INVALID_PER_DAY: their earlier ticks
    that day may have helped remove a word before they were known to be
    crossing words off. True if anything was put back.
    """
    put_back = False
    for word in set(words):
        if _count_players(cursor, word, 'invalid') < CROWD_REMOVE_VOTES:
            cursor.execute(
                "DELETE FROM blossom_invalid_words WHERE word = %s AND source = 'crowd'", (word,))
            put_back = put_back or cursor.rowcount > 0
    return put_back


def _apply_crowd_change(cursor, word, kind):
    """Move the word into the list the crowd voted for. True if anything changed.

    James's own entry in the other list wins: the crowd never reverses it.
    """
    target, other = _CROWD_TABLES[kind]
    cursor.execute(f"SELECT source FROM {other} WHERE word = %s", (word,))
    row = cursor.fetchone()
    if row is not None and row[0] != 'crowd':
        return False
    cursor.execute(f"SELECT 1 FROM {target} WHERE word = %s", (word,))
    if cursor.fetchone() is not None:
        return False
    cursor.execute(f"DELETE FROM {other} WHERE word = %s AND source = 'crowd'", (word,))
    cursor.execute(f"INSERT INTO {target} (word, added_date, source) VALUES (%s, %s, 'crowd')",
                   (word, pst_now_str()))
    # Votes the other way were about the word before this change. Left in
    # place, one more of them would flip it straight back.
    cursor.execute("DELETE FROM blossom_word_votes WHERE word = %s AND vote = %s",
                   (word, _CROWD_OPPOSITE[kind]))
    return True


def _record_vote(word, kind, puzzle):
    """Count one player's vote, and apply the change once enough agree.

    Returns True if this vote is the one that applied it. Written inline
    rather than through the write-behind queue because the vote, the count
    and the change must happen together, and the change has to clear this
    process's word-list cache. Best-effort: a database problem costs the vote,
    never the request.
    """
    voter = voter_hash()
    applied = put_back = False
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute(
                "SELECT COUNT(*) FROM blossom_word_votes WHERE voter_hash = %s AND created_at >= %s",
                (voter, _pst_days_ago(1)))
            if cursor.fetchone()[0] >= CROWD_MAX_VOTES_PER_DAY:
                return False
            # A repeat vote refreshes the old one rather than adding a second.
            # Recorded even when it won't count, so real per-player numbers
            # are there to check CROWD_MAX_INVALID_PER_DAY against later.
            cursor.execute(
                """INSERT INTO blossom_word_votes (word, vote, voter_hash, puzzle, created_at)
                   VALUES (%s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE created_at = new.created_at, puzzle = new.puzzle""",
                (word, kind, voter, puzzle, pst_now_str()))
            crossing_off = False
            if kind == 'invalid':
                cursor.execute(
                    "SELECT COUNT(*) FROM blossom_word_votes "
                    "WHERE voter_hash = %s AND vote = 'invalid' AND created_at >= %s",
                    (voter, _pst_today_start()))
                crossing_off = cursor.fetchone()[0] > CROWD_MAX_INVALID_PER_DAY
            if crossing_off:
                # None of this player's ticks today count, so anything their
                # earlier ones helped remove goes back if it no longer has
                # enough players without them.
                cursor.execute(
                    "SELECT word FROM blossom_word_votes "
                    "WHERE voter_hash = %s AND vote = 'invalid' AND created_at >= %s",
                    (voter, _pst_today_start()))
                put_back = _put_back_unsupported(cursor, [w for (w,) in cursor.fetchall()])
            elif _count_players(cursor, word, kind) >= _CROWD_THRESHOLD[kind]:
                applied = _apply_crowd_change(cursor, word, kind)
            conn.commit()
    except Exception as e:
        alerts.alert_throttled('blossom_vote_failed', exc=type(e).__name__, msg=e)
        return False
    if applied or put_back:
        cache.delete('blossom_filtered_words')
    return applied


def _retract_vote(word, kind):
    """Take back this player's vote.

    If that leaves too few players behind a change the crowd made, the change
    is undone too: unticking a mis-tap that tipped a word over should put the
    word back. (Votes only age out of the count; that never reverts anything.)
    """
    reverted = False
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute(
                "DELETE FROM blossom_word_votes WHERE word = %s AND vote = %s AND voter_hash = %s",
                (word, kind, voter_hash()))
            # Only a vote that was actually counted can undo anything: a tick
            # that never recorded one (the word was already gone, say) can't.
            if cursor.rowcount > 0 and _count_players(cursor, word, kind) < _CROWD_THRESHOLD[kind]:
                target = _CROWD_TABLES[kind][0]
                cursor.execute(f"DELETE FROM {target} WHERE word = %s AND source = 'crowd'", (word,))
                reverted = cursor.rowcount > 0
            conn.commit()
    except Exception as e:
        alerts.alert_throttled('blossom_vote_failed', exc=type(e).__name__, msg=e)
        return
    if reverted:
        cache.delete('blossom_filtered_words')


def _toggle_invalid(data):
    """Tick or untick a word's Invalid box: the player says Blossom rejected it."""
    word = _parse_word(data.get('word'))
    center, petals = _parse_puzzle(data)

    if 'invalid_words' not in session:
        session['invalid_words'] = []
    flagged = session['invalid_words']
    if word in flagged:
        flagged.remove(word)
        _retract_vote(word, 'invalid')
    else:
        flagged.append(word)
        # Only a word actually on offer, on a board it fits: anything else is
        # not a result this player could have been shown.
        if word in get_filtered_blossom_words() and _fits_puzzle(word, center, petals):
            _record_vote(word, 'invalid', _puzzle_tag(center, petals))
    session.modified = True
    # Same reply whether or not this vote removed the word (see the note above
    # CROWD_REMOVE_VOTES).
    return jsonify({'status': 'success', 'invalid_words': flagged})


def _suggest_missing(data):
    """A word Blossom accepted that the solver doesn't list."""
    word = _parse_word(data.get('word'))
    center, petals = _parse_puzzle(data)
    if not _fits_puzzle(word, center, petals):
        return jsonify({'status': 'success', 'result': 'not_in_puzzle'})
    if word in get_filtered_blossom_words():
        return jsonify({'status': 'success', 'result': 'already_listed'})
    # 'recorded' whether or not this vote added the word (see the note above
    # CROWD_REMOVE_VOTES).
    _record_vote(word, 'missing', _puzzle_tag(center, petals))
    return jsonify({'status': 'success', 'result': 'recorded'})
