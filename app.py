"""App entry point: creates the Flask app, binds extensions, registers
blueprints, and owns app-level handlers. Gunicorn serves this via `app:app`.

Routes live in the routes/ package:
    routes/wordgames.py  - wordle, antiwordle, quordle, fixer, word finders
    routes/blossom.py    - blossom solver, admin, feedback
    routes/smush.py      - smush word list, crowd corrections, admin
    routes/espresso.py   - espresso optimizer pages
    routes/dashboards.py - youtube trending, etl status, mtg prices
    routes/misc.py       - front page, games, feedback, SEO files, redirects

Shared services (db, cache, limiter, auth, redis) live in extensions.py,
env/secret loading in config.py, and startup dataset loads in data.py.
"""

import logging
import sys
from datetime import timedelta

from flask import Flask, jsonify, redirect, render_template, request
from flask_sslify import SSLify
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

import config

if config.IS_HEROKU:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        stream=sys.stdout
    )

from extensions import NOT_FOUND_LIMITS, cache, limiter, log_page_visit
from helpers import ValidationError
from monitoring import alerts
from routes import blossom, dashboards, espresso, misc, smush, wordgames

app = Flask(__name__)

app.secret_key = config.SESSION_KEY
app.permanent_session_lifetime = timedelta(hours=4)  # Sessions last 4 hours

# Cap request bodies app-wide: every endpoint's inputs (form fields, JSON
# search filters) are tiny, so anything larger is junk or abuse. Werkzeug
# rejects an over-limit body with a 413 before it's read into memory.
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024  # 64 KB

##### extensions #####

limiter.init_app(app)
cache.init_app(app, config={'CACHE_TYPE': 'SimpleCache'}) # SimpleCache is fine for single-process environments

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

##### blueprints #####

app.register_blueprint(misc.bp)
app.register_blueprint(wordgames.bp)
app.register_blueprint(blossom.bp)
app.register_blueprint(smush.bp)
app.register_blueprint(espresso.bp)
app.register_blueprint(dashboards.bp)

##### error handlers #####

# Error handler for 404 Not Found
@app.errorhandler(404)
@limiter.limit(NOT_FOUND_LIMITS)
def page_not_found(e):
    # Deliberately does not log. Flask only invokes this when a 404 is
    # *raised*, and routes/misc.py's catch-all returns the status code
    # instead, so almost every 404 bypasses this handler entirely. The
    # logging that feeds vw_prod_errors lives in that catch-all.
    return render_template('error.html', return_type='404 - Page Not Found'), 404

# Bad user input (scanner junk in numeric/letter fields) -> clean 400, no
# traceback. Must be registered above the catch-all Exception handler's scope:
# Flask picks the most specific handler, so ValidationError never hits the 500.
@app.errorhandler(ValidationError)
def handle_validation_error(e):
    return jsonify(error=str(e)), 400

@app.errorhandler(429)
def ratelimit_handler(e):
    return render_template('error.html', return_type='Rate Limit Exceeded - Too Many Requests'), 429

@app.errorhandler(Exception)
def handle_exception(e):
    # A generic Exception handler also receives werkzeug's HTTPExceptions
    # (missing form key -> 400, wrong method -> 405, non-JSON body -> 415).
    # Those are the client's fault: keep their status code instead of logging
    # a traceback and converting them into a 500.
    if isinstance(e, HTTPException):
        return render_template('error.html', return_type=f'{e.code} - {e.name}'), e.code
    app.logger.exception("Unhandled exception: %s", e)
    # Reporting the 500 must never cost the visitor the branded error page.
    # This handler runs when things are already going wrong, which is exactly
    # when starting the drain thread or reaching Papertrail might also fail.
    try:
        log_page_visit(f'error.html (500: {e})')
        # Papertrail's existing rule needs five 500s in ten minutes; this
        # reports the first one. Throttled so a scanner walking a broken route
        # sends one email rather than a hundred.
        alerts.alert_throttled(
            'http_500', sev='crit', path=request.path, exc=type(e).__name__, msg=e
        )
    except Exception:
        app.logger.exception("Failed to report a 500")
    return render_template('error.html', return_type='500 - Error'), 500


if __name__ == "__main__":
    app.run(debug=True, load_dotenv=False)

