"""Front page, static games, feedback, SEO files, redirects, and catch-all."""

import mysql.connector
from flask import Blueprint, current_app, jsonify, redirect, render_template, request, send_from_directory, url_for

from extensions import db_cursor, limiter

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


@bp.route("/tessera")
def tessera_game():
    return render_template("tessera.html")


@bp.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html')


@bp.route("/feedback", methods=["POST", "GET"])
def feedback():
    if request.method == "POST":

        feedback_header = request.form['feedback_header']
        feedback_body = request.form['feedback_body']
        referrer = request.form['referrer']

        # log inputs
        try:
            with db_cursor() as (conn, cursor):
                query = """
                INSERT INTO feedback (submit_time, referrer, feedback_header, feedback_body)
                VALUES (CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), %s, %s, %s);
                """
                cursor.execute(query, (referrer, feedback_header, feedback_body))
                conn.commit()
        except mysql.connector.Error as err:
            print("Error:", err)

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
        '/blossom', '/any_word', '/feedback', '/privacy-policy',
        '/mtg', '/youtube_trending', '/umbra', '/tiltconnect4', '/tessera',
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
@limiter.limit("10 per minute; 30 per hour")
def catch_all(path):
    return render_template('error.html', return_type='404 - Page Not Found'), 404
