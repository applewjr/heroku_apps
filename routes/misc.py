"""Front page, static games, feedback, SEO files, redirects, and catch-all."""

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, send_from_directory, url_for

from extensions import NOT_FOUND_LIMITS, db_cursor, limiter, log_page_visit
from monitoring import alerts

bp = Blueprint('misc', __name__)


@bp.route("/", methods=["POST", "GET"])
def run_index():
    return render_template("index.html")


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
def feedback():
    if request.method == "POST":

        feedback_header = request.form['feedback_header']
        feedback_body = request.form['feedback_body']
        referrer = request.form['referrer']

        # Honeypot: a field hidden from people, so only something filling the
        # form blind fills it. Read with .get() rather than [] on purpose - an
        # older cached copy of the page has no such field, and a visitor on one
        # must not get a 400 for it.
        #
        # This deliberately does NOT reject. Nothing here may ever cost a real
        # person their feedback, and no detection is good enough to bet
        # someone's message on, so a tripped honeypot still writes the row and
        # only declines to send the notification. The daily digest lists every
        # row from the last 24 hours, so a suppressed submission is still in
        # front of you within a day - it just cannot flood the inbox.
        looks_automated = bool(request.form.get('feedback_extra', '').strip())

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
            if looks_automated:
                # Plain line, no token: a flood of these is precisely what the
                # honeypot exists to keep out of the inbox.
                print("feedback suppressed: honeypot filled", flush=True)
            elif not alerts.alert_throttled('feedback_new', sev='info',
                                            header=feedback_header,
                                            referrer=referrer):
                # A second bound that does not depend on detection working.
                # Even a bot that avoids the honeypot cannot turn one form into
                # an inbox full, and real feedback arrives every week or two,
                # so two genuine submissions inside one throttle window is not
                # a case worth optimising for - the digest carries both anyway.
                print("feedback alert throttled", flush=True)

        return render_template("feedback_received.html")
    else:
        return render_template("feedback.html")


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
        '/blossom', '/smush', '/ribbit', '/any_word', '/feedback', '/privacy-policy',
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
