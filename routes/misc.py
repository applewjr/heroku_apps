"""Front page, static games, feedback, SEO files, redirects, and catch-all."""

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, send_from_directory, url_for

from data import etl_dash_queries
from extensions import FEEDBACK_POST_LIMITS, NOT_FOUND_LIMITS, auth, db_cursor, limiter, log_page_visit
from helpers import bot_reason, client_ip, feedback_flooded, issue_form_token, log_dropped
from monitoring import alerts
from routes.dashboards import etl_round_name

# Namespaces the signature, so a token minted for this form cannot be replayed
# into another form that later uses the same mechanism.
FEEDBACK_TOKEN_SALT = 'feedback-form'

bp = Blueprint('misc', __name__)


@bp.route("/", methods=["POST", "GET"])
def run_index():
    return render_template("index.html")


@bp.route('/jj_admin')
@auth.login_required
def jj_admin():
    """Every password-protected page, on one page. They share its password,
    so the browser doesn't ask again on the way through. A test fails if a
    protected page isn't linked here."""
    rounds = {}
    for name, details in etl_dash_queries.items():
        if 'round' in details:
            rounds.setdefault(details['round'], []).append(details.get('label') or name)
    etl_rounds = [(r, etl_round_name(r), panels) for r, panels in sorted(rounds.items())]
    return render_template('jj_admin.html', etl_rounds=etl_rounds)


@bp.route('/log-click', methods=['POST'])
def log_click():
    return jsonify({"status": "success"}), 200


@bp.route('/dogs')
def dogs():
    return render_template('dog_count.html')


@bp.route("/hex")
def hex_game_redirect():
    return redirect("/umbra", code=301)


@bp.route("/umbra")
def umbra_game():
    return render_template("hex.html")


@bp.route("/tiltconnect4")
def connect4tilt_game():
    return render_template("tilt_connect4.html")


@bp.route("/kintsugi")
def kintsugi_game():
    return render_template("kintsugi.html")


@bp.route("/thread")
def thread_game():
    return render_template("thread.html")


@bp.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html')


@bp.route("/feedback", methods=["POST", "GET"])
@limiter.limit(FEEDBACK_POST_LIMITS, key_func=client_ip, methods=["POST"])
def feedback():
    if request.method == "POST":

        feedback_header = request.form['feedback_header']
        feedback_body = request.form['feedback_body']
        referrer = request.form['referrer']

        # A bot's post is dropped, not stored (helpers.bot_reason has the
        # checks and the 2026-10-13 scanner that prompted them). It still
        # gets the thank-you page, so it learns nothing.
        reason = bot_reason(request.form, 'feedback_extra', 'feedback_token',
                            FEEDBACK_TOKEN_SALT)
        if reason == 'stale':
            # No usable token: a person on an old copy of the page or a tab
            # left open past a day. Show the form again with their text.
            return render_template(
                "feedback.html", resubmit=True, referrer=referrer,
                feedback_header=feedback_header, feedback_body=feedback_body,
                feedback_token=issue_form_token(FEEDBACK_TOKEN_SALT, resubmit=True))
        if reason is None and feedback_flooded(db_cursor, 'feedback'):
            reason = 'flood'
            alerts.alert_throttled('feedback_flood', referrer=referrer)
        if reason:
            log_dropped('feedback', reason, f'{feedback_header} | {feedback_body}')
            return render_template("feedback_received.html")

        # log inputs. Written inline rather than through the write-behind
        # queue: feedback arrives every week or two and matters, so it is
        # worth waiting on the confirmation that a page-visit row is not.
        try:
            with db_cursor() as (conn, cursor):
                query = """
                INSERT INTO feedback (submit_time, referrer, feedback_header, feedback_body)
                VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
                """
                cursor.execute(query, (referrer, feedback_header, feedback_body))
                conn.commit()
        except Exception as err:
            # Deliberately broad. mysql.connector.Error covers the pool and
            # connection failures, but the visitor is told their feedback was
            # received either way, so nothing here may turn into a 500.
            print("Error:", err)
            # The row is gone, so the alert has to carry the content itself or
            # the feedback is lost - the visitor is told it was received.
            alerts.alert('feedback_write_failed', sev='crit',
                         header=feedback_header, body=feedback_body, msg=err)
        else:
            # Notify on write rather than by polling the table: instant, and
            # with no "which rows have I already seen" bookkeeping to get wrong.
            if not alerts.alert_throttled('feedback_new', sev='info',
                                          header=feedback_header,
                                          referrer=referrer):
                # A bound that does not depend on detection working. Even a
                # bot that passes every check cannot turn one form into an
                # inbox full, and real feedback arrives every week or two, so
                # two genuine submissions inside one throttle window is not a
                # case worth optimising for - the digest carries both anyway.
                print("feedback alert throttled", flush=True)

        return render_template("feedback_received.html")
    else:
        return render_template("feedback.html",
                               feedback_token=issue_form_token(FEEDBACK_TOKEN_SALT))


@bp.route("/feedback_received", methods=["GET"])
def feedback_received():
    return render_template("feedback_received.html")


@bp.route('/robots.txt')
def robots_txt():
    return send_from_directory(current_app.static_folder, 'robots.txt', mimetype='text/plain')


@bp.route('/sitemap.xml')
def sitemap():
    pages = [
        '/', '/wordiply', '/wordle', '/antiwordle', '/quordle',
        '/blossom', '/smush', '/ribbit', '/guess_the_phrase', '/chainle', '/any_word', '/feedback',
        '/privacy-policy',
        '/mtg', '/youtube_trending', '/umbra', '/tiltconnect4', '/kintsugi',
        '/thread',
    ]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for page in pages:
        xml.append(f'  <url><loc>https://www.jamesapplewhite.com{page}</loc></url>')
    xml.append('</urlset>')
    return '\n'.join(xml), 200, {'Content-Type': 'application/xml'}


@bp.route('/ads.txt')
def ads_txt():
    return send_from_directory(current_app.static_folder, 'ads.txt', mimetype='text/plain')


@bp.route('/<path:icon_name>.png')
def serve_png_icon(icon_name):
    return redirect(url_for('static', filename=f'{icon_name}.png'), code=302)


@bp.route('/favicon.ico')
def favicon_ico():
    return redirect(url_for('static', filename='favicon.ico'), code=302)


##### legacy URL redirects #####

@bp.route('/antiwordle_og')
def antiwordle_og_redirect():
    return redirect('/antiwordle', code=301)


@bp.route('/blossom_bee')
def blossom_bee_redirect():
    return redirect('/blossom', code=301)


@bp.route('/quordle_mobile')
def quordle_mobile_redirect():
    return redirect('/quordle', code=301)


@bp.route('/wordle_og')
def wordle_og_redirect():
    return redirect('/wordle', code=301)


# Catch-all route for undefined paths
@bp.route('/<path:path>')
@limiter.limit(NOT_FOUND_LIMITS)
def catch_all(path):
    # This, not app.py's errorhandler(404), is where essentially every 404
    # lands: returning the status code never invokes the handler.
    #
    # Only referred 404s are logged. Unreferred ones are scanner traffic, and
    # NOT_FOUND_LIMITS permits 600/hour per rate-limit key - at ~196 bytes a
    # row that is most of a gigabyte a year into a 1 GB database, with nothing
    # pruning it. A referrer means a real link led somewhere broken, which is
    # the only version of this worth keeping; vw_prod_blossom_errors filters
    # on referrer anyway, so unreferred rows could never satisfy it.
    if request.headers.get('Referer'):
        log_page_visit(f'error.html (404: {path})')
    return render_template('error.html', return_type='404 - Page Not Found'), 404
