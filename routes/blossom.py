"""Blossom solver (and /blossom_dev, James's copy of it), today's board,
admin word management, and feedback routes."""

import time
from datetime import date, timedelta

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for

import crowd
# The CROWD_MAX_* names are re-exported for tests/test_blossom.py.
from crowd import (CROWD_ADD_VOTES, CROWD_MAX_INVALID_PER_DAY, CROWD_MAX_VOTES_PER_DAY,  # noqa: F401
                   CROWD_REMOVE_VOTES_BY_POP, CROWD_REMOVE_VOTES_RARE,
                   CROWD_WINDOW_DAYS)
from data import word_pop, words_blossom
from extensions import (FEEDBACK_POST_LIMITS, INTERACTIVE_LIMITS, auth, db_cursor, enqueue_write,
                        limiter, log_page_visit, pst_now_str)
from functions import all_words
from helpers import ISO_DATE as _DATE
from helpers import (ValidationError, bot_reason, client_ip, feedback_flooded, issue_form_token,
                     log_dropped, make_schema_data, parse_int, parse_letters, parse_played_on)
# _pacific_today is a name of this module's own so tests can set the day.
from helpers import pacific_today as _pacific_today
from monitoring import alerts

bp = Blueprint('blossom', __name__)

# Written per keystroke, so it goes through the write-behind queue rather than
# inline. Timestamp is stamped in Python at enqueue time; see pst_now_str.
BLOSSOM_CLICKS_SQL = """
INSERT INTO blossom_solver_clicks (click_time, must_have, may_have, petal_letter, list_len)
VALUES (%s, %s, %s, %s, %s);
"""

# Words the results list shows at first, and how many more each "Show more"
# adds. A short first list puts the end of the results, and what follows it,
# within reach without much scrolling. list_len in blossom_solver_clicks
# shows how often players ask for more.
BLOSSOM_LIST_FIRST = 15
BLOSSOM_LIST_STEP = 25


@bp.route("/blossom", methods=["POST", "GET"])
@limiter.limit(INTERACTIVE_LIMITS)
def blossom_solver():
    return _blossom_page(dev=False)


@bp.route("/blossom_dev", methods=["POST", "GET"])
@limiter.limit(INTERACTIVE_LIMITS)
@auth.login_required
def blossom_dev():
    """James's copy of /blossom with today's board and hints on, to watch
    them against his own game for a while before players see them (James's
    call, 2026-10-23), as /ribbit_dev did for Ribbit's boards. It posts to
    itself, so its votes are tagged dev in the vote log; its boards and
    votes count like any player's. Not a page visit, and its solves are not
    logged as solver clicks."""
    return _blossom_page(dev=True)


def _blossom_page(dev):
    """/blossom, or /blossom_dev with `dev`: the page, a solve (the form
    POST) or a JSON action. Today's board shows wherever show_board is on:
    always on /blossom_dev, and on /blossom once TODAYS_BOARD_LIVE."""
    post_url = '/blossom_dev' if dev else '/blossom'
    show_board = dev or TODAYS_BOARD_LIVE
    try:

        schema_data = None if dev else make_schema_data(
            "Blossom Solver & Word Finder - Today's Hints & Answers",
            "Free Blossom word finder & solver. Instantly find all words and answers to solve today's Merriam-Webster Blossom puzzle.",
            "https://www.jamesapplewhite.com/blossom",
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
                    return _toggle_invalid(data, dev)
                if data.get('action') == 'suggest_missing':
                    return _suggest_missing(data, dev)
                if data.get('action') == 'log_board':
                    return _log_board(data)
                if data.get('action') == 'todays_board':
                    return _todays_board_reply(data, show_board)
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
                                      default=BLOSSOM_LIST_FIRST, min_value=1, max_value=10000)
            if request.form.get("load_more"):
                current_count = min(current_count + BLOSSOM_LIST_STEP, 10000)

            # How the player got these letters (see "today's board"), handed
            # back so the results page still knows a loaded board is one, and
            # never logs it as their own. Anything else reads as typed.
            board_source = request.form.get('board_source')
            if board_source not in ('typed', 'loaded', 'edited'):
                board_source = 'typed'

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
            # Not from /blossom_dev: James's own checks aren't player traffic.
            if not dev:
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
                                list_step=BLOSSOM_LIST_STEP,
                                pangrams=pangrams,
                                schema_data=schema_data,
                                post_url=post_url, dev=dev, show_board=show_board,
                                board_source=board_source)

        else:
            # Initialize session for used words if it doesn't exist
            if 'used_words' not in session:
                session['used_words'] = []

            # Make session permanent (4 hours)
            session.permanent = True

            used_words = session.get('used_words', [])
            if not dev:
                log_page_visit('blossom.html')

            # Today's board for the Pacific date, which is what search
            # engines and the first paint get; the page asks again for the
            # player's own date if theirs differs. board_date goes out even
            # with no board, so the page knows which date it was told about.
            todays_board, board_date = None, ''
            if show_board:
                today = _pacific_today()
                board_date = today.isoformat()
                try:
                    todays_board = _board_for(today)
                except Exception as e:
                    # _board_for doesn't raise; this guard is the page's own,
                    # so nothing about today's board can ever take it down.
                    alerts.alert_throttled('blossom_board_read_failed',
                                           exc=type(e).__name__, msg=e)

            return render_template("blossom.html",
                                used_words=used_words,
                                current_count=BLOSSOM_LIST_FIRST,
                                show_load_more=False,
                                schema_data=schema_data,
                                post_url=post_url, dev=dev, show_board=show_board,
                                todays_board=todays_board, board_date=board_date)

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

    # Back to the page the Reset came from: /blossom_dev sends to=dev. A
    # fixed choice, never a URL, so this can't redirect anywhere else.
    if request.args.get('to') == 'dev':
        return redirect(url_for('blossom.blossom_dev'))
    return redirect(url_for('blossom.blossom_solver'))


def _admin_form():
    """The word and its list, or None for the list if no choice was sent.
    Nothing is assumed: a missing choice must not count as a removal."""
    word = request.form.get('word', '').strip().lower()
    kind = request.form.get('word_type')
    return word, kind if kind in ('invalid', 'missing') else None


NO_CHOICE = 'Choose Remove invalid word or Add missing word first'


ADMIN = '/blossom_admin'
# Today's Board first: approving the day's board is the daily visit.
ADMIN_TABS = ('board', 'reports', 'feedback', 'invalid', 'added')


def _back(**message):
    """Back to /blossom_admin as the admin left it, with a message."""
    return redirect(crowd.admin_return_url(ADMIN, request.form, **message))


def _empty_view(active_tab):
    """The word-list half of the page when its tables can't be read, so the
    Today's Board tab still works (and says what's wrong)."""
    pager = {'page': 1, 'pages': 1, 'prev': None, 'next': None, 'numbers': []}
    return {'reports': [], 'invalid_words': [], 'added_words': [],
            'reports_pager': pager, 'invalid_pager': pager, 'added_pager': pager,
            'open_count': 0, 'settled_count': 0, 'invalid_total': 0, 'added_total': 0,
            'show_settled': False, 'settled_url': ADMIN, 'query': '', 'clear_url': ADMIN,
            'keep': {'tab': active_tab}, 'active_tab': active_tab, 'in_both': [],
            'votes_needed': BLOSSOM_CROWD.threshold}


@bp.route(ADMIN)
@auth.login_required
def blossom_admin():
    """Today's board, Blossom's corrected word list, what players have
    reported, and the old report form's submissions. Each part reads its
    own tables, so one that fails leaves the others working."""
    args = request.args.to_dict()
    args.setdefault('tab', 'board')
    try:
        # source is 'admin' (added here) or 'crowd' (enough players agreed -
        # see "crowd corrections" below).
        view, crowd_error = BLOSSOM_CROWD.admin_view(args, request.path, tabs=ADMIN_TABS), None
    except Exception as e:
        print(f"Error loading blossom admin word lists: {e}")
        view = _empty_view(args['tab'] if args['tab'] in ADMIN_TABS else 'board')
        crowd_error = f'{type(e).__name__}: {e}'

    try:
        with db_cursor() as (conn, cursor):
            # Get recent feedback
            cursor.execute("""
                SELECT id, submit_time, report_type, reported_words
                FROM feedback_blossom
                ORDER BY submit_time DESC
                LIMIT 10
            """)
            recent_feedback = cursor.fetchall()
    except Exception as e:
        print(f"Error loading blossom feedback: {e}")
        recent_feedback = []

    days, load_error = _admin_days()
    try:
        return render_template('blossom_admin.html',
                             **view,
                             crowd_error=crowd_error,
                             recent_feedback=recent_feedback,
                             days=days, load_error=load_error,
                             board_live=TODAYS_BOARD_LIVE,
                             serve_crowd=SERVE_CROWD,
                             pacific_today=_pacific_today(),
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
        return _back(error=NO_CHOICE)
    if not word.isalpha():
        return _back(error='Word must be letters only')
    try:
        BLOSSOM_CROWD.admin_set(word, kind)
    except Exception as e:
        print(f"Error adding word: {e}")
        return _back(error='Database error')
    done = 'added to the word list' if kind == 'missing' else 'removed from the word list'
    return _back(success=f'{word} {done}')


@bp.route('/remove_word', methods=['POST'])
@auth.login_required
def remove_word():
    """Undo an entry in either list, and reset that word's votes."""
    word, kind = _admin_form()
    if kind is None:
        return _back(error=NO_CHOICE)
    if not word.isalpha():
        return _back(error='Word must be letters only')
    try:
        BLOSSOM_CROWD.admin_remove(word, kind)
    except Exception as e:
        print(f"Error removing word: {e}")
        return _back(error='Database error')
    return _back(success=f'{word} undone')


# Namespaces this form's token, as FEEDBACK_TOKEN_SALT does /feedback's.
BLOSSOM_FEEDBACK_TOKEN_SALT = 'blossom-feedback'


@bp.route("/blossom_feedback", methods=["POST", "GET"])
@limiter.limit(FEEDBACK_POST_LIMITS, key_func=client_ip, methods=["POST"])
def blossom_feedback():
    if request.method == "POST":
        report_type = request.form['report_type']
        feedback_body = request.form['feedback_body']
        referrer = request.form['referrer']

        # Same screening as /feedback (helpers.bot_reason): a bot's post is
        # dropped, not stored, and still gets the thank-you page. On
        # 2026-10-13 a scanner put 21 rows in here within seconds.
        reason = bot_reason(request.form, 'feedback_extra', 'feedback_token',
                            BLOSSOM_FEEDBACK_TOKEN_SALT)
        if reason == 'stale':
            # No usable token: an old copy of the page or a tab left open.
            return render_template(
                "blossom_feedback.html", resubmit=True, referrer=referrer,
                report_type=report_type, feedback_body=feedback_body,
                feedback_token=issue_form_token(BLOSSOM_FEEDBACK_TOKEN_SALT, resubmit=True))
        if reason is None and report_type not in ('invalid', 'missing'):
            # The page's radios send nothing else; all 6 blank ones in the
            # table came from the 2026-10-13 scanner.
            reason = 'report type'
        if reason is None and feedback_flooded(db_cursor, 'feedback_blossom'):
            reason = 'flood'
            alerts.alert_throttled('blossom_feedback_flood', referrer=referrer)
        if reason:
            log_dropped('blossom feedback', reason, f'{report_type} | {feedback_body}')
            return render_template("blossom_feedback_received.html", report_type=report_type)

        # Stored only: no email since 2026-10-13 (James). The reports are on
        # /blossom_admin's Recent Feedback tab.
        try:
            with db_cursor() as (conn, cursor):
                query = """
                INSERT INTO feedback_blossom (submit_time, referrer, report_type, reported_words)
                VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
                """
                cursor.execute(query, (referrer, report_type, feedback_body))
                conn.commit()
        except Exception as err:
            # Broad on purpose: the reporter is thanked either way, so this
            # must never be a 500. The words go to the log so the report
            # isn't lost with the row.
            print(f"Blossom feedback not saved ({err}): {report_type} | {feedback_body[:200]!r}",
                  flush=True)

        return render_template("blossom_feedback_received.html", report_type=report_type)
    else:
        return render_template("blossom_feedback.html",
                               feedback_token=issue_form_token(BLOSSOM_FEEDBACK_TOKEN_SALT))


# Memoized in the module, not SimpleCache: SimpleCache pickles, so every
# solve unpickled the ~1 MB set into ~9 MB of fresh strings (49 ms of CPU,
# measured 2026-10-10), and that churn fragmented memory: one of the two
# causes of the dyno's creep. Same pattern as routes/smush.py. Holds no copy
# of the word list at all while Blossom's corrections are empty.
_blossom_words = {'value': None, 'expires': 0.0}


def _forget_blossom_words():
    """Make the next get_filtered_blossom_words() re-read the corrections,
    and the next today's board recompute its hints, which come from them."""
    _blossom_words['expires'] = 0.0
    _board_cache.clear()


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


def _vote_mode(data, dev=False):
    """'normal' or 'helper' (the page's Helper Mode, which masks the words but
    keeps the Invalid box), for the vote log only: 'dev_normal' / 'dev_helper'
    from /blossom_dev. None when the page didn't say - never a reason to
    refuse the vote."""
    mode = data.get('mode')
    if mode not in ('normal', 'helper'):
        return None
    return ('dev_' if dev else '') + mode


def _toggle_invalid(data, dev=False):
    """Tick or untick a word's Invalid box: the player says Blossom rejected it."""
    word = _parse_word(data.get('word'))
    center, petals = _parse_puzzle(data)

    if 'invalid_words' not in session:
        session['invalid_words'] = []
    flagged = session['invalid_words']
    if word in flagged:
        flagged.remove(word)
        BLOSSOM_CROWD.retract_vote(word, 'invalid', _puzzle_tag(center, petals), _vote_mode(data, dev))
    else:
        flagged.append(word)
        # Only a word actually on offer, on a board it fits: anything else is
        # not a result this player could have been shown.
        if word in get_filtered_blossom_words() and _fits_puzzle(word, center, petals):
            BLOSSOM_CROWD.record_vote(word, 'invalid', _puzzle_tag(center, petals), _vote_mode(data, dev))
    session.modified = True
    # Same reply whether or not this vote removed the word (see crowd.py).
    return jsonify({'status': 'success', 'invalid_words': flagged})


def _suggest_missing(data, dev=False):
    """A word Blossom accepted that the solver doesn't list."""
    word = _parse_word(data.get('word'))
    center, petals = _parse_puzzle(data)
    if not _fits_puzzle(word, center, petals):
        return jsonify({'status': 'success', 'result': 'not_in_puzzle'})
    if word in get_filtered_blossom_words():
        return jsonify({'status': 'success', 'result': 'already_listed'})
    # 'recorded' whether or not this vote added the word (see crowd.py).
    BLOSSOM_CROWD.record_vote(word, 'missing', _puzzle_tag(center, petals), _vote_mode(data, dev))
    return jsonify({'status': 'success', 'result': 'recorded'})


##### today's board #####

# Players who type today's board into /blossom feed it to everyone after
# them: once the board is known, later players get "Today's board:"
# with a Load it button that fills the flower, and a box of spoiler-free
# hints. Anyone can still type their own letters (an archive puzzle, say).
# Modelled on Ribbit's shared boards (routes/ribbit.py), with James's rules
# (2026-10-22):
# - James approves the day's board in /blossom_admin, in the petal spots his
#   game shows. An approval always wins.
# - Otherwise the crowd. A board *state* is the center letter plus the
#   letter in each petal spot, in order. Once one state of a puzzle has
#   SERVE_CROWD players it is served; after that, whichever state has the
#   most players, a tie going to the one that got there first.
#
# Replayed on blossom_solver_clicks on 2026-10-22, with sessions (the same
# board within 30 minutes) standing in for players, since that table has no
# player id:
# - Players rarely type the petals in the same spots. The most common exact
#   order covered 10-26% of a puzzle's players over 21 days, even counting
#   turns and mirror images as one. So the approval is the backbone, and the
#   crowd rule only ever counts exact states.
# - The 5-player rule settled 19 of the last 20 puzzles, usually around
#   3-7am Pacific on the puzzle's day. 3 settled the evening before (Europe
#   and the US East Coast), the latest at 2:26pm, and one never did. The top
#   state ends a day with 5-11 players. On 3 days another petal order of the
#   same letters overtook the first to reach 5, so the served order can
#   change once in a day.
#
# Blossom gives each player a new board at their own midnight, so everything
# keys on the player's own date (played_on), as on Ribbit: Europe's evening
# settles the next day's board before the US wakes. A puzzle first seen on an
# earlier date is an archive game, never served for a later day, though
# anyone can still type it.
#
# Shipped dark (James's call, 2026-10-23): /blossom logs boards but serves
# none until TODAYS_BOARD_LIVE, and /blossom_dev, his password-only copy,
# serves them, so he can watch them against his own game first. Players are
# never told how a board is chosen (see crowd.py on why).

# Players on one exact state that serve it, when nothing is approved (James,
# 2026-10-22).
SERVE_CROWD = 5
# Boards one player can log on one day: an archive binge plus typo fixes,
# with room to spare, as on Ribbit. A typo is another puzzle (other letters),
# so this also bounds what one person can add to a day.
BOARDS_PER_PLAYER_PER_DAY = 10
# 'typed' = entered by the player; 'edited' = loaded from other players,
# then changed. A board loaded and left as it is is never logged, so the
# served board can't vote for itself.
BOARD_SOURCES = ('typed', 'edited')
# An approval from /blossom_admin is a row of its own in blossom_boards, with
# this as its source and in place of a player hash: a real hash is 32 hex
# characters, and players can only send BOARD_SOURCES. It never counts as a
# player.
APPROVAL = 'admin'
# Off: /blossom logs boards but serves none, even to a direct call, and only
# /blossom_dev shows them (James's call, 2026-10-23, as Ribbit's
# SHARED_BOARDS_LIVE started out). True puts the board and the hints on
# /blossom.
TODAYS_BOARD_LIVE = False
# A date counts toward a puzzle's first_seen only once this many networks
# logged it then, so one device with a wrong clock can't make today's puzzle
# look like an archive game. A real archive puzzle had dozens on its day.
ARCHIVE_MIN_NETWORKS = 2
# How long a day's board is reused before it is read again, and how long a
# failed read waits before the next try. Gunicorn runs one worker (Procfile),
# so this module's memo is the only copy.
BOARD_CACHE_SECONDS = 60
BOARD_RETRY_SECONDS = 300
# The days the admin tab shows: Pacific's last three, and tomorrow for
# players whose date is already ahead.
ADMIN_DAYS_BACK = 2

# The columns every read of blossom_boards returns, in this order.
# prefix_hash (crowd.prefix_hash) is NULL on approvals.
_COLUMNS = 'letters, board, source, voter_hash, prefix_hash, played_on, updated_at'
LETTERS, BOARD, SOURCE, VOTER, PREFIX, PLAYED_ON, UPDATED = range(7)

# day -> (expires, board): what _board_for last served for that date, hints
# included, or None. time.monotonic() clock.
_board_cache = {}


def _played_on(data):
    """The player's own date, or None if it can't be today anywhere on Earth
    (more than a day from Pacific's) - a wrong clock, not a reason to 400.
    See helpers.parse_played_on."""
    return parse_played_on(data.get('played_on'), _pacific_today())


def _parse_board(data):
    """(letters, board) for a whole board: letters is the puzzle (the center,
    then the petals sorted) and board its state (the center, then the petals
    in spots petal-0..5, the flower's reading order). Seven different letters
    a-z: parse_letters on its own lets an accented letter through."""
    center, petals = _parse_puzzle(data)
    board = center + petals
    if not (board.isascii() and len(set(board)) == 7):
        raise ValidationError("a board needs seven different letters a-z")
    return center + ''.join(sorted(petals)), board


def _is_letters(value):
    """A puzzle's letters, as the Today's Board tab's forms send them."""
    return (len(value) == 7 and value.isascii() and value.isalpha() and value.islower()
            and len(set(value)) == 7)


def board_text(board):
    """'E · P S R O I U': a board as the page and the admin tab show it."""
    return board[0].upper() + ' · ' + ' '.join(board[1:].upper())


def _fetch(sql, params):
    with db_cursor() as (conn, cursor):
        cursor.execute(sql, params)
        return cursor.fetchall()


def rows_with_letters(letters):
    """Every row for one puzzle, any date."""
    return _fetch(f"SELECT {_COLUMNS} FROM blossom_boards WHERE letters = %s", (letters,))


def rows_logged_between(first, last):
    """Every row, any date, for each puzzle logged between these days."""
    return _fetch(
        f"SELECT {_COLUMNS} FROM blossom_boards WHERE letters IN "
        "(SELECT letters FROM blossom_boards WHERE played_on BETWEEN %s AND %s)",
        (first, last))


def store_board(day, voter, prefix, letters, board, source):
    """Log one player's board, replacing their earlier state of that puzzle
    that day. `voter` is their crowd.player_hash, `prefix` their network's
    crowd.prefix_hash. Inline, not on the write-behind queue: a missing
    table there would drop the rest of its batch, page visits included.
    Best-effort, like a vote."""
    now = pst_now_str()
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute(
                "SELECT COUNT(*) FROM blossom_boards "
                "WHERE voter_hash = %s AND played_on = %s AND letters <> %s",
                (voter, day, letters))
            if cursor.fetchone()[0] >= BOARDS_PER_PLAYER_PER_DAY:
                return
            cursor.execute(
                """INSERT INTO blossom_boards (played_on, voter_hash, prefix_hash, letters, board,
                       source, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) AS new
                   ON DUPLICATE KEY UPDATE board = new.board, source = new.source,
                       updated_at = new.updated_at, prefix_hash = new.prefix_hash""",
                (day, voter, prefix, letters, board, source, now, now))
            conn.commit()
    except Exception as e:
        alerts.alert_throttled('blossom_board_log_failed', exc=type(e).__name__, msg=e)


def store_approval(day, letters, board):
    """Approve this board (see APPROVAL) for players whose date is `day`,
    replacing any approval of its puzzle that day. Raises, unlike
    store_board: the admin page says whether it worked."""
    now = pst_now_str()
    with db_cursor() as (conn, cursor):
        cursor.execute(
            """INSERT INTO blossom_boards (played_on, voter_hash, letters, board, source,
                   created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s) AS new
               ON DUPLICATE KEY UPDATE board = new.board, updated_at = new.updated_at""",
            (day, APPROVAL, letters, board, APPROVAL, now, now))
        conn.commit()


def remove_approval(letters, day):
    """Take back the approval of this puzzle on `day`; players' boards stay.
    Returns how many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute(
            "DELETE FROM blossom_boards WHERE letters = %s AND played_on = %s AND source = %s",
            (letters, day, APPROVAL))
        removed = cursor.rowcount
        conn.commit()
    return removed


def remove_state(letters, board, day):
    """Delete the players' entries of one board state on `day`: test boards
    (staging writes this table too) or junk. An approval stays. Returns how
    many rows went."""
    with db_cursor() as (conn, cursor):
        cursor.execute(
            "DELETE FROM blossom_boards "
            "WHERE letters = %s AND board = %s AND played_on = %s AND source <> %s",
            (letters, board, day, APPROVAL))
        removed = cursor.rowcount
        conn.commit()
    return removed


def consensus(rows, day):
    """What one puzzle's rows add up to on `day`.

    rows -- blossom_boards rows (see _COLUMNS) sharing one `letters`, from
            any dates. On `day`, each player's latest board is the one that
            counts, and players on one network (crowd.prefix_hash) count
            once: the latest board among them.

    Returns a dict: states (each board state with its players and
    reached_at, when its SERVE_CROWD-th player came in; most players first),
    players (networks on `day`), board (the state to serve, or None), why
    (None when served, else what it waits for; admin wording), approved /
    approved_board / approved_at (`day`'s approval, served as it is) and
    first_seen (see ARCHIVE_MIN_NETWORKS).
    """
    approvals = [row for row in rows if row[SOURCE] == APPROVAL and row[PLAYED_ON] == day]
    player_rows = [row for row in rows if row[SOURCE] in BOARD_SOURCES]

    networks_by_day = {}
    for row in player_rows:
        networks_by_day.setdefault(row[PLAYED_ON], set()).add(row[PREFIX] or row[VOTER])
    busy_days = [d for d, nets in networks_by_day.items() if len(nets) >= ARCHIVE_MIN_NETWORKS]
    first_seen = min(busy_days) if busy_days else min(row[PLAYED_ON] for row in rows)

    latest = {}
    for row in player_rows:
        if row[PLAYED_ON] != day:
            continue
        mine = latest.get(row[VOTER])
        if mine is None or row[UPDATED] > mine[UPDATED]:
            latest[row[VOTER]] = row
    # Then one per network. A second step, so a player who moved between
    # networks still counts once.
    networks = {}
    for row in latest.values():
        network = row[PREFIX] or row[VOTER]
        if network not in networks or row[UPDATED] > networks[network][UPDATED]:
            networks[network] = row

    times_by_board = {}
    for row in networks.values():
        times_by_board.setdefault(row[BOARD], []).append(row[UPDATED])
    states = []
    for board, times in times_by_board.items():
        times.sort()
        states.append({'board': board, 'players': len(times),
                       'reached_at': times[SERVE_CROWD - 1] if len(times) >= SERVE_CROWD else None})
    # Most players first; among equals, the first to reach SERVE_CROWD.
    states.sort(key=lambda s: (-s['players'], s['reached_at'] is None, str(s['reached_at']),
                               s['board']))

    approval = max(approvals, key=lambda row: row[UPDATED]) if approvals else None
    board, why = None, None
    if approval is not None:
        board = approval[BOARD]
    elif states and states[0]['players'] >= SERVE_CROWD:
        board = states[0]['board']
    elif states:
        short = SERVE_CROWD - states[0]['players']
        why = f"needs {short} more player{'' if short == 1 else 's'} on one board"
    else:
        why = 'no player board on this day yet'
    for state in states:
        state['served'] = state['board'] == board
        state['text'] = board_text(state['board'])
    return {
        'states': states,
        'players': len(networks),
        'board': board,
        'why': why,
        'approved': approval is not None,
        'approved_board': approval[BOARD] if approval is not None else None,
        'approved_at': approval[UPDATED] if approval is not None else None,
        'first_seen': first_seen,
    }


def todays_board(rows, day):
    """The board to offer players whose date is `day`, and what the admin tab
    shows about that day.

    rows -- every blossom_boards row (any date) for each puzzle logged on
            `day` (see rows_logged_between).

    Blossom has one puzzle a day, so one board is served: `day`'s latest
    approval, whatever the players typed (an approval always wins, even
    over a stray row that makes its puzzle look older); otherwise the
    served state of the puzzle most players logged that day. Never a puzzle
    first seen on an earlier day (an archive game), and never a smaller
    puzzle while the biggest has no state settled yet: that could be some
    other game's board. Returns (board, groups): board is {'date', 'center',
    'petals', 'text'} or None; groups is every puzzle of the day with its
    consensus, 'archive' and 'served', most players first.
    """
    by_letters = {}
    for row in rows:
        by_letters.setdefault(row[LETTERS], []).append(row)

    groups = []
    for letters, group_rows in by_letters.items():
        if not any(row[PLAYED_ON] == day for row in group_rows):
            continue
        result = consensus(group_rows, day)
        result['letters'] = letters
        result['archive'] = result['first_seen'] < day
        result['served'] = False
        if result['archive'] and not result['approved']:
            result['why'] = f"older puzzle, first seen {result['first_seen']:%m-%d}"
        groups.append(result)
    groups.sort(key=lambda g: (-g['players'], g['letters']))

    approved = [g for g in groups if g['approved']]
    current = [g for g in groups if not g['archive']]
    if approved:
        chosen = max(approved, key=lambda g: g['approved_at'])
    elif current and current[0]['board'] is not None:
        chosen = current[0]
    else:
        chosen = None
    for g in groups:
        if g is chosen:
            continue
        # Its own winner isn't the day's board.
        for state in g['states']:
            state['served'] = False
        if g['board'] is not None and not g['archive']:
            g['why'] = ('a later approval is served' if g['approved']
                        else 'another board is served')
    if chosen is None:
        return None, groups
    chosen['served'] = True
    board = chosen['board']
    return {'date': day.isoformat(), 'center': board[0], 'petals': board[1:],
            'text': board_text(board)}, groups


def _and_list(items):
    """'a', 'a and b', 'a, b and c'."""
    items = list(items)
    if len(items) < 2:
        return ''.join(items)
    return ', '.join(items[:-1]) + ' and ' + items[-1]


def _pattern(word):
    """'SP_____ (7)': the first two letters, a blank for each one after."""
    return word[:2].upper() + '_' * (len(word) - 2) + f' ({len(word)})'


# The most words hints 2 and 3 go through one by one. Letters as common as
# R P A E N S T give this list 29 pangrams, which as patterns is a wall of
# text; the words themselves are all in the last step.
HINT_LIST_MAX = 10


def blossom_hints(center, petals, day):
    """Spoiler-free hints for one board, from Blossom's word list with its
    corrections (get_filtered_blossom_words): its pangrams (words with all
    seven letters), or its longest words on a day with no pangram.

    Returns the facts (pangram_lengths, longest, starts, patterns,
    pangrams, words) and the wording both the page and its script show:
    a heading and four steps, hints opened one at a time, the last
    revealing the words. None if no word fits the board. No total word
    count: this list isn't Blossom's own (players correct it), and a day's
    total runs to hundreds, which tells a player nothing.
    """
    letters = set(center + petals)
    words = [w for w in get_filtered_blossom_words() if _fits_puzzle(w, center, petals)]
    if not words:
        return None
    longest = max(len(w) for w in words)
    pangrams = sorted((w for w in words if set(w) >= letters), key=lambda w: (len(w), w))
    if pangrams:
        shown = pangrams
        lengths = [len(w) for w in pangrams]
        if len(pangrams) == 1:
            first = f"1 pangram today: {lengths[0]} letters."
        elif len(pangrams) <= 3:
            first = f"{len(pangrams)} pangrams today: {_and_list(str(n) for n in lengths)} letters."
        else:
            first = f"{len(pangrams)} pangrams today, {lengths[0]} to {lengths[-1]} letters long."
        first += f" Longest word: {longest} letters."
        what = 'pangram' if len(pangrams) == 1 else 'pangrams'
    else:
        shown = sorted(w for w in words if len(w) == longest)
        first = f"No pangram today. Longest word: {longest} letters."
        what = 'longest word' if len(shown) == 1 else 'longest words'
    listed = shown[:HINT_LIST_MAX]
    starts = [w[0].upper() for w in listed]
    patterns = [_pattern(w) for w in listed]
    if len(shown) == 1:
        second = f"The {what} starts with {starts[0]}."
    elif len(shown) <= HINT_LIST_MAX:
        second = f"The {what} start with {_and_list(starts)}."
    elif pangrams:
        second = f"The {HINT_LIST_MAX} shortest pangrams start with {_and_list(starts)}."
    else:
        second = f"{HINT_LIST_MAX} of the longest words start with {_and_list(starts)}."
    third = ', '.join(patterns)
    if len(shown) > HINT_LIST_MAX:
        third += f", and {len(shown) - HINT_LIST_MAX} more"
    return {
        'pangram_lengths': [len(w) for w in pangrams],
        'longest': longest,
        'starts': starts,
        'patterns': patterns,
        'pangrams': pangrams,
        'words': [w.upper() for w in shown],
        'heading': f"Today's Blossom hints - {day:%A, %B} {day.day}",
        'steps': [
            {'summary': 'Hint 1', 'text': first},
            {'summary': 'Hint 2', 'text': second},
            {'summary': 'Hint 3', 'text': third},
            {'summary': f'Show the {what}', 'text': ', '.join(w.upper() for w in shown)},
        ],
    }


def _board_for(day):
    """Today's board for players whose date is `day`, with its hints, or
    None. Served from the memo while fresh (BOARD_CACHE_SECONDS), else read
    again; never raises. When an entry goes stale, the first request pushes
    it out 15 seconds before reading, and every other request keeps the
    stale board meanwhile, so a slow database holds up one request, not all
    of them. A failed read is remembered too, for BOARD_RETRY_SECONDS."""
    now = time.monotonic()
    entry = _board_cache.get(day)
    if entry is not None and now < entry[0]:
        return entry[1]
    if entry is not None:
        _board_cache[day] = (now + 15, entry[1])
    try:
        board, _groups = todays_board(rows_logged_between(day, day), day)
        if board is not None:
            board['hints'] = blossom_hints(board['center'], board['petals'], day)
        _board_cache[day] = (time.monotonic() + BOARD_CACHE_SECONDS, board)
    except Exception as e:
        alerts.alert_throttled('blossom_board_read_failed', exc=type(e).__name__, msg=e)
        board = None
        _board_cache[day] = (time.monotonic() + BOARD_RETRY_SECONDS, None)
    for old in [d for d in list(_board_cache) if abs((d - day).days) > 2]:
        _board_cache.pop(old, None)
    return board


def _log_board(data):
    """A board a player entered, sent when they first tap a petal with all
    seven letters in. Always the same reply, stored or not."""
    letters, board = _parse_board(data)
    source = data.get('source')
    if source not in BOARD_SOURCES:
        raise ValidationError('source must be typed or edited')
    day = _played_on(data)
    if day is not None:
        store_board(day, crowd.player_hash('blossom'), crowd.prefix_hash('blossom'),
                    letters, board, source)
    return jsonify(status='success')


def _todays_board_reply(data, show_board):
    """Today's board for the player's own date, hints included, or null. Null
    on a page where today's board is still dark, even to a direct call."""
    day = _played_on(data)
    if not show_board or day is None:
        return jsonify(board=None)
    return jsonify(board=_board_for(day))


def _admin_days():
    """(days, error): each recent day's puzzles for the Today's Board tab,
    newest first, each with every state's players and what is served."""
    today = _pacific_today()
    first, last = today - timedelta(days=ADMIN_DAYS_BACK), today + timedelta(days=1)
    try:
        rows = rows_logged_between(first, last)
    except Exception as e:
        print(f"Error loading blossom admin boards: {e}")
        return [], f'{type(e).__name__}: {e}'
    days = []
    day = last
    while day >= first:
        _board, groups = todays_board(rows, day)
        for g in groups:
            g['approved_text'] = board_text(g['approved_board']) if g['approved'] else ''
        if groups:
            days.append({'day': day, 'groups': groups})
        day -= timedelta(days=1)
    return days, None


def _board_form():
    """(letters, board, day) from a Today's Board form, or None if it isn't
    one. A state row's buttons send letters, board and played_on; the
    Approve form sends its seven spots (center, p0-p5) and a date. The same
    rules as a player's board."""
    played_on = request.form.get('played_on', '')
    if not _DATE.fullmatch(played_on):
        return None
    try:
        day = date.fromisoformat(played_on)
    except ValueError:
        return None
    if 'board' in request.form:
        board = request.form.get('board', '').strip().lower()
        center, petals = board[:1], board[1:]
    else:
        center = request.form.get('center', '').strip().lower()
        petals = ''.join(request.form.get(f'p{i}', '').strip().lower() for i in range(6))
    try:
        letters, board = _parse_board({'center': center, 'petals': petals})
    except ValidationError:
        return None
    if 'letters' in request.form and request.form['letters'] != letters:
        return None
    return letters, board, day


@bp.route(ADMIN + '/approve_board', methods=['POST'])
@auth.login_required
def blossom_approve_board():
    """Serve this board to every player whose date is `played_on`, straight
    away and whatever the players typed: James's own board, in the petal
    spots his game shows (James's call, 2026-10-22). Approving the same
    puzzle again, in other spots, replaces it."""
    form = _board_form()
    if form is None:
        return _back(error='Not a board: seven different letters, center first, and a date')
    letters, board, day = form
    try:
        store_approval(day, letters, board)
    except Exception as e:
        print(f"Error approving blossom board: {e}")
        return _back(error='Database error')
    _board_cache.clear()
    return _back(success=f"Approved {board_text(board)} for {day:%m-%d}")


@bp.route(ADMIN + '/unapprove_board', methods=['POST'])
@auth.login_required
def blossom_unapprove_board():
    """Undo the approval of one puzzle on one day: the players' boards
    decide again, and none of them is touched."""
    letters = request.form.get('letters', '')
    played_on = request.form.get('played_on', '')
    if not (_is_letters(letters) and _DATE.fullmatch(played_on)):
        return _back(error='Not a board')
    try:
        day = date.fromisoformat(played_on)
    except ValueError:
        return _back(error='Not a board')
    try:
        removed = remove_approval(letters, day)
    except Exception as e:
        print(f"Error undoing blossom approval: {e}")
        return _back(error='Database error')
    _board_cache.clear()
    if not removed:
        return _back(error='That board was not approved')
    return _back(success="Approval undone: the players' boards decide again")


@bp.route(ADMIN + '/remove_board', methods=['POST'])
@auth.login_required
def blossom_remove_board():
    """Delete the players' entries of one board state on one day: test
    boards (staging writes the same table) or junk. An approval stays."""
    form = _board_form()
    if form is None:
        return _back(error='Not a board')
    letters, board, day = form
    try:
        removed = remove_state(letters, board, day)
    except Exception as e:
        print(f"Error removing blossom board: {e}")
        return _back(error='Database error')
    _board_cache.clear()
    return _back(success=f"Removed {removed} entr{'y' if removed == 1 else 'ies'} "
                         f"of {board_text(board)} on {day:%m-%d}")
