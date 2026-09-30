"""Alert emission for SolarWinds Papertrail.

Alerts are log lines, not emails. Every problem prints one line carrying a
distinctive token to stdout; Heroku's log drain carries it to Papertrail,
which owns the "who gets notified, and how" decision. Two consequences worth
the indirection:

* Switching channel later (Slack, Pushover) is a Papertrail UI setting rather
  than a deploy.
* The alerting path has no SMTP failure mode of its own, unlike the
  scheduled tasks that send their own Gmail.

Two tokens, matched by Papertrail as plain substrings:

    JJ_ALERT check=youtube_trending_today sev=crit actual=0 threshold=45
    JJ_PULSE source=checks ok=11 alert=0 ms=940

JJ_PULSE feeds Papertrail's *inactivity* alert ("trigger when no new events
match"). That is the only check that survives the thing being monitored
dying - a dead dyno, an unreachable database, a failed boot, a broken drain -
so it is what makes running unattended safe. Everything else can only report
problems it is alive to see.

This module must stay side-effect free on import: it is imported by the web
app and by one-off scheduler dynos alike, so it must not touch the database,
Redis, or the network.
"""

import sys
import threading
import time

ALERT_TOKEN = "JJ_ALERT"
PULSE_TOKEN = "JJ_PULSE"

# Long enough to carry a useful exception message, short enough that one
# alert stays one readable line in Papertrail's event viewer.
MAX_VALUE_LEN = 200

# Default spacing for alert_throttled. Fifteen minutes is short enough to
# confirm a problem is ongoing and long enough that a scanner hammering a
# broken route cannot turn one bug into a hundred emails.
DEFAULT_THROTTLE_SECONDS = 900

_last_emit = {}
_last_emit_lock = threading.Lock()


def scrub(value):
    """Make an arbitrary value safe to interpolate into a log line.

    Values routinely carry user input: exception messages built from form
    data, feedback headers, referrers. Three things have to be neutralised.

    Public because run_checks formats its own `ok` and `skip` state lines with
    it. Those carry measured values straight out of the database and need the
    same three guarantees; a second implementation would drift from this one.
    """
    text = str(value)

    # One alert is one line. A newline in an exception message would split it
    # in two, and the second half would no longer match Papertrail's search.
    text = " ".join(text.split())

    # An embedded token would let a visitor forge or spam alerts by typing one
    # into a feedback box. Both tokens share the JJ_ prefix, so breaking that
    # prefix is enough - and leaves the text readable, which redaction would not.
    text = text.replace("JJ_", "JJ.")

    if len(text) > MAX_VALUE_LEN:
        text = text[:MAX_VALUE_LEN - 3] + "..."

    # k=v is only legible if v has no bare spaces.
    if " " in text:
        text = '"{}"'.format(text.replace('"', "'"))

    return text


def _emit(token, **fields):
    """Print exactly one token line to stdout.

    flush=True is load-bearing: Python block-buffers stdout when it is not a
    TTY, which is exactly the case on a dyno. A process that crashes with
    buffered output loses the very alert explaining why it crashed.
    """
    parts = " ".join("{}={}".format(k, scrub(v)) for k, v in fields.items())
    print("{} {}".format(token, parts), file=sys.stdout, flush=True)


def alert(check, sev="warn", **fields):
    """Report that something is wrong. Matched by Papertrail's JJ_ALERT alert.

    ``sev`` is informational - one saved search catches every severity, which
    keeps the alert count inside the free plan's quota. Split it later by
    searching for ``sev=crit`` if crit should reach a different destination.
    """
    _emit(ALERT_TOKEN, check=check, sev=sev, **fields)


def pulse(source, **fields):
    """Report that a process ran to completion.

    Emitted even when checks failed: "something is wrong" (JJ_ALERT) and
    "nothing is reporting" (JJ_PULSE inactivity) have to stay independent
    signals, or an outage would suppress the alert that detects outages.
    """
    _emit(PULSE_TOKEN, source=source, **fields)


def alert_throttled(check, sev="warn", min_interval=DEFAULT_THROTTLE_SECONDS, **fields):
    """alert(), but at most once per ``min_interval`` seconds for this check.

    For alerts fired from the request path, where the trigger rate is set by
    traffic rather than by the problem - a scanner walking a broken route would
    otherwise turn one bug into a hundred emails.

    Returns True if the alert was emitted, False if it was suppressed. State
    is per-process and in-memory, which is exactly right for one gunicorn
    worker (see the Procfile) and degrades to one alert per worker if that
    ever changes.
    """
    now = time.monotonic()
    with _last_emit_lock:
        previous = _last_emit.get(check)
        if previous is not None and (now - previous) < min_interval:
            return False
        _last_emit[check] = now

    alert(check, sev=sev, **fields)
    return True


def reset_throttle():
    """Forget every throttle deadline. For tests."""
    with _last_emit_lock:
        _last_emit.clear()
