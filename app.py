"""App entry point: creates the Flask app, binds extensions, registers
blueprints, and owns app-level handlers. Gunicorn serves this via `app:app`.

Routes live in the routes/ package:
    routes/wordgames.py  - wordle, antiwordle, quordle, fixer, word finders
    routes/blossom.py    - blossom solver, admin, feedback
    routes/espresso.py   - espresso optimizer pages
    routes/dashboards.py - youtube trending, etl status, mtg prices
    routes/misc.py       - front page, games, feedback, SEO files, redirects

Shared services (db, cache, limiter, auth, redis) live in extensions.py,
env/secret loading in config.py, and startup dataset loads in data.py.
"""

import logging
import sys
from datetime import timedelta

from flask import Flask, redirect, render_template, request
from flask_sslify import SSLify
from werkzeug.middleware.proxy_fix import ProxyFix

import config

if config.IS_HEROKU:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        stream=sys.stdout
    )

from extensions import cache, limiter
from routes import blossom, dashboards, espresso, misc, wordgames

app = Flask(__name__)

app.secret_key = config.SESSION_KEY
app.permanent_session_lifetime = timedelta(hours=4)  # Sessions last 4 hours

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
app.register_blueprint(espresso.bp)
app.register_blueprint(dashboards.bp)

##### error handlers #####

# Error handler for 404 Not Found
@app.errorhandler(404)
@limiter.limit("10 per minute; 30 per hour")
def page_not_found(e):
    return render_template('error.html', return_type='404 - Page Not Found'), 404

@app.errorhandler(429)
def ratelimit_handler(e):
    return render_template('error.html', return_type='Rate Limit Exceeded - Too Many Requests'), 429

@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.exception("Unhandled exception: %s", e)
    return render_template('error.html', return_type='500 - Error'), 500


if __name__ == "__main__":
    app.run(debug=True, load_dotenv=False)
