"""Small shared helpers used across route blueprints."""

import ctypes
import hashlib
import hmac
import ipaddress
import math
import re
from datetime import date, datetime, timezone

import pytz
from flask import current_app, request
from itsdangerous import URLSafeTimedSerializer


# glibc mallopt parameter numbers (malloc.h).
_M_TRIM_THRESHOLD = -1
_M_MMAP_THRESHOLD = -3
MALLOC_THRESHOLD = 128 * 1024


def pin_malloc_thresholds():
    """Stop glibc holding freed memory: call once, at startup, before the
    big imports. The cause of the dyno's creep (2026-10-10 diagnosis):

    glibc raises its mmap threshold (up to 32 MB) each time a large block is
    freed, and its trim threshold to twice that. After a 4.8 MB MTG page or
    a 5.4 MB matplotlib canvas is freed, each of gunicorn's 8 thread arenas
    keeps that much free memory, which malloc_trim can't reach. It ratchets
    up to the largest block freed so far: the +9-13 MB/hour staircase.

    Pinning both at 128 KB (glibc's own default) also turns off that
    dynamic adjustment, so blocks of 128 KB or more are mmapped and go
    straight back to the OS when freed. In a WSL replay of 5,000 requests,
    this plus the module memos (no per-request unpickling) cut RSS growth
    from +107-120 MB to +8 MB, with no slowdown. Does nothing without glibc
    (Windows, macOS).
    """
    try:
        libc = ctypes.CDLL("libc.so.6")
    except OSError:
        return
    libc.mallopt(_M_TRIM_THRESHOLD, MALLOC_THRESHOLD)
    libc.mallopt(_M_MMAP_THRESHOLD, MALLOC_THRESHOLD)


def release_memory():
    """Give freed memory back to the OS. Dropping a large structure frees it,
    but on Linux (the dyno) glibc keeps freed blocks for reuse, so the
    process only shrinks once malloc_trim hands them back. Does nothing where
    there's no glibc (Windows, macOS)."""
    try:
        libc = ctypes.CDLL("libc.so.6")
    except OSError:
        return
    libc.malloc_trim(0)


def rss_mb():
    """This process's resident memory in MB, from /proc (Linux: the dyno), or
    None where there's no /proc (Windows, macOS)."""
    try:
        with open('/proc/self/status') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


class ValidationError(ValueError):
    """User-supplied form input failed validation.

    app.py registers an error handler that turns this into a 400 response, so
    routes can raise it (or let parse_* raise it) instead of 500ing on junk
    input from scanners.
    """


def parse_int(value, field, default, min_value=None, max_value=None):
    """Parse a form value as an int.

    Missing/blank input returns ``default``; non-numeric input raises
    ValidationError (-> 400). Out-of-range values are clamped to the bounds
    rather than rejected, so a legit-but-odd value degrades gracefully.
    """
    if value is None or str(value).strip() == '':
        return default
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a whole number")
    if min_value is not None:
        parsed = max(parsed, min_value)
    if max_value is not None:
        parsed = min(parsed, max_value)
    return parsed


def parse_float(value, field, default, min_value=None, max_value=None):
    """Float twin of parse_int: same blank/junk/clamping behaviour."""
    if value is None or str(value).strip() == '':
        return default
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a number")
    # nan/inf parse fine but defeat the clamps and range checks (every nan
    # comparison is False), so they must be rejected, not clamped.
    if not math.isfinite(parsed):
        raise ValidationError(f"{field} must be a finite number")
    if min_value is not None:
        parsed = max(parsed, min_value)
    if max_value is not None:
        parsed = min(parsed, max_value)
    return parsed


##### bot screening for the public feedback forms #####

# /feedback and /blossom_feedback drop a bot's post rather than store it
# (James, 2026-10-13). Until then they stored everything and only held back
# the notification, and on 2026-10-13 a SQL-injection scanner (sqlmap-style
# probes: 1', 1"), 98766) put 21 rows into each form's table within seconds,
# as one had into /feedback on 2026-09-29. A dropped post still gets the
# usual thank-you page, so a bot learns nothing, and log_dropped keeps enough
# of it to recover a person's message if a check ever misfires.

# Minimum plausible gap between a form rendering and a person submitting it.
# Two seconds sits far below any real interaction - reading two labels, typing a
# topic and a sentence - and far above what a scanner spends: the sqlmap run on
# 2026-09-29 put twenty submissions through inside a single second.
MIN_SUBMIT_SECONDS = 2

# Token lifetime. Generous: a form left open in a tab overnight is a person,
# and an expired token only re-shows the form with the visitor's text, so a
# long life costs nothing.
FORM_TOKEN_MAX_AGE = 60 * 60 * 24

# What a re-shown form's token carries instead of 't'. Its visitor has typed
# their message already, so pressing Submit straight away isn't too fast.
_RESUBMIT = 'again'

# Most rows a feedback form stores per rolling hour and day before it drops
# the rest: the bound that holds even for a bot that passes every check. The
# busiest genuine hour and day on record (2024-01 to 2026-10, the two bot
# bursts left out) were 2 and 2 for /feedback, 3 and 3 for Blossom.
FEEDBACK_MAX_PER_HOUR = 10
FEEDBACK_MAX_PER_DAY = 20


def _form_serializer(salt):
    # Signed with the app secret, which is a stable env var in production, so
    # tokens survive Heroku's daily dyno cycling and deploys. In dev the key is
    # random per boot, which invalidates tokens across a reload - harmless,
    # because an unverifiable token only re-shows the form.
    return URLSafeTimedSerializer(current_app.secret_key, salt=salt)


def issue_form_token(salt='form', resubmit=False):
    """A signed, timestamped token to embed in a form that a bot might flood.
    resubmit=True for a form re-shown with the visitor's text in it."""
    return _form_serializer(salt).dumps(_RESUBMIT if resubmit else 't')


def form_token_state(token, salt='form', min_seconds=MIN_SUBMIT_SECONDS):
    """'ok', 'too fast' (it verifies, and came back sooner than anyone types),
    or 'stale' (missing, unverifiable or expired).

    'stale' is never read as a bot: a cached copy of the page, a rotated
    secret, a tab left open past FORM_TOKEN_MAX_AGE and a token from the
    future (clock skew across a restart, which itsdangerous refuses) all have
    innocent explanations, so the form is shown again with the visitor's text
    and a fresh token instead. A bot that drops or mangles the token gets the
    same page and stores nothing either way.

    Signed rather than a plain timestamp because an unsigned field is one a bot
    can simply backdate.
    """
    if not token:
        return 'stale'
    try:
        value, issued_at = _form_serializer(salt).loads(
            token, max_age=FORM_TOKEN_MAX_AGE, return_timestamp=True)
    except Exception:
        # Broad on purpose: BadSignature, SignatureExpired, malformed base64
        # and a missing secret key all mean the same thing here.
        return 'stale'
    if value == _RESUBMIT:
        return 'ok'
    elapsed = (datetime.now(timezone.utc) - issued_at).total_seconds()
    return 'too fast' if elapsed < min_seconds else 'ok'


def bot_reason(form, honeypot, token_field, salt):
    """Why this feedback post looks automated, or None if it looks like a
    person. In order:

    'honeypot' - the field hidden from people was filled in. A scanner fills
      every field it finds.
    'referrer' - the page's script fills referrer from document.referrer,
      which is empty or a URL. Every row of both bot bursts had a junk one
      ('1', "1'", '98766'); all 177 genuine rows were empty or http(s).
    'too fast' or 'stale' - from the form token (see form_token_state). The
      caller re-shows the form for 'stale' rather than dropping it.
    """
    if form.get(honeypot, '').strip():
        return 'honeypot'
    referrer = form.get('referrer', '')
    if referrer and not referrer.startswith(('http://', 'https://')):
        return 'referrer'
    state = form_token_state(form.get(token_field), salt=salt)
    return None if state == 'ok' else state


def feedback_flooded(db_cursor, table):
    """Has `table` already stored FEEDBACK_MAX_PER_HOUR rows in the last
    hour, or FEEDBACK_MAX_PER_DAY in the last day? submit_time is Pacific.

    Takes the route's db_cursor so a test's stub of it covers this too.
    Fails open: a count that can't be read never costs a person their
    message (the insert after it would most likely fail as well).
    """
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute(f"""
                SELECT COUNT(*),
                       COALESCE(SUM(submit_time >= CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles') - INTERVAL 1 HOUR), 0)
                FROM {table}
                WHERE submit_time >= CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles') - INTERVAL 1 DAY
            """)
            day, hour = cursor.fetchone()
    except Exception as e:
        print(f"{table} flood check failed: {e}", flush=True)
        return False
    return hour >= FEEDBACK_MAX_PER_HOUR or day >= FEEDBACK_MAX_PER_DAY


def log_dropped(form_name, reason, text):
    """One plain log line per dropped post (no JJ_ALERT, so no email), with
    enough of it to recover a person's message from Papertrail."""
    print(f"{form_name} dropped ({reason}): {text[:60]!r}", flush=True)


def parse_choice(value, field, options, default):
    """Parse a form value that must be one of a known set.

    The twin of parse_int for the app's other kind of input: a value the form
    offers as a <select>, which downstream code then uses as a dict key or a
    DataFrame column. Those lookups raise KeyError on anything unexpected, and
    an uncaught KeyError is a 500 - so a scanner posting junk reads as a server
    fault, fires http_500, and lands in vw_prod_errors. That is exactly what
    happened to /espresso/baseline on 2026-09-29.

    Blank input returns ``default``; anything outside ``options`` raises
    ValidationError (-> 400). Unlike parse_int there is deliberately no
    clamping: a choice has no nearest valid neighbour to fall back to, and
    silently substituting one would answer a question the visitor did not ask.

    ``options`` may be any container supporting ``in`` - a list, or a dict
    whose keys are the valid values.
    """
    if value is None or str(value).strip() == '':
        return default
    value = str(value).strip()
    if value not in options:
        # The valid set is already visible in the form's own markup, so naming
        # it here discloses nothing and makes a real mistake self-explanatory.
        raise ValidationError(
            f"{field} must be one of: {', '.join(str(o) for o in options)}")
    return value


def json_object(silent=False):
    """The request's JSON body, which must be an object: anything else raises
    ValidationError (-> 400). silent=True reads a body that isn't JSON as
    None, so that is a 400 too, rather than get_json's own error."""
    data = request.get_json(silent=silent)
    if not isinstance(data, dict):
        raise ValidationError('expected a JSON object')
    return data


def parse_letters(value, field, max_len=25, default=''):
    """Parse a form value that should be a short run of letters (solver
    inputs). Blank input returns ``default``; anything non-alphabetic or
    over ``max_len`` raises ValidationError (-> 400)."""
    if value is None:
        return default
    value = str(value).strip()
    if value == '':
        return default
    if not value.isalpha():
        raise ValidationError(f"{field} must contain letters only")
    if len(value) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} letters")
    return value


PACIFIC = pytz.timezone('America/Los_Angeles')
ISO_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')


def pacific_today():
    """Today's date on the Pacific coast: the site's own calendar."""
    return datetime.now(PACIFIC).date()


def parse_iso_day(value):
    """An admin form's YYYY-MM-DD date, or None if it isn't a real one."""
    if not (isinstance(value, str) and ISO_DATE.fullmatch(value)):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_played_on(value, today):
    """A daily-puzzle page's `played_on`, the player's own date (their game
    rolls over at their own midnight), or None if it can't be today anywhere
    on Earth: more than a day from `today`, Pacific's. That is a wrong clock,
    not a reason to 400. A value that isn't a real YYYY-MM-DD date raises
    ValidationError (-> 400).

    `today` is the caller's: routes/ribbit.py and routes/blossom.py each pass
    their own module-level _pacific_today(), so tests can set the day."""
    if not (isinstance(value, str) and ISO_DATE.fullmatch(value)):
        raise ValidationError('played_on must be a date, YYYY-MM-DD')
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise ValidationError('played_on must be a real date')
    return day if abs((day - today).days) <= 1 else None


def make_schema_data(name, description, url, operating_system='Web'):
    """Build the schema.org WebApplication JSON-LD blob used for SEO."""
    schema = {
        "@context": "https://schema.org",
        "@type": "WebApplication",
        "name": name,
        "description": description,
        "url": url,
        "applicationCategory": "GameApplication",
        "isAccessibleForFree": True,
        "dateModified": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "offers": {
            "@type": "Offer",
            "price": "0",
            "priceCurrency": "USD"
        },
        "creator": {
            "@type": "Person",
            "name": "James Applewhite"
        }
    }
    if operating_system:
        schema["operatingSystem"] = operating_system
    return schema


# Solvers tagged NEW in the site's solver links, and the last day (Pacific)
# each keeps the tag. It drops off by itself: no deploy, nothing to remember.
NEW_UNTIL = {
    '/chainle': date(2026, 12, 31),
    '/guess_the_phrase': date(2026, 12, 31),
}


def is_new(path):
    """True while the page at `path` is still tagged NEW (see NEW_UNTIL).
    Templates call it: app.py registers it as a template global."""
    until = NEW_UNTIL.get(path)
    return until is not None and pacific_today() <= until


def make_trending_jsonld(items, list_name="YouTube Trending - Top videos today"):
    """Build a schema.org ItemList of trending videos for SEO.

    ``items`` is an iterable of ``(position, video_id, title)`` tuples.
    """
    return {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": list_name,
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": int(position),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "name": title,
            }
            for position, video_id, title in items
        ],
    }


# Cloudflare's published edge ranges, https://www.cloudflare.com/ips/ as of
# 2026-09-30. A stale list fails safe: visitors arriving through a new edge
# range read as that edge's own address, so they merge into one visitor -
# undercounting people, never inventing extra ones.
_CLOUDFLARE_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22',
    '141.101.64.0/18', '108.162.192.0/18', '190.93.240.0/20', '188.114.96.0/20',
    '197.234.240.0/22', '198.41.128.0/17', '162.158.0.0/15', '104.16.0.0/13',
    '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22',
    '2400:cb00::/32', '2606:4700::/32', '2803:f800::/32', '2405:b500::/32',
    '2405:8100::/32', '2a06:98c0::/29', '2c0f:f248::/32',
))


def _parse_ip(value):
    try:
        return ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None


def client_ip():
    """The visitor's real IP address as a string, or '' if it can't be read.

    request.remote_addr is not the visitor on Heroku - ProxyFix runs without
    x_for (see app.py and the note above extensions.INTERACTIVE_LIMITS).

    The last X-Forwarded-For hop is whoever connected to Heroku's router.
    Heroku appends it, so a client cannot forge it. When that hop is a
    Cloudflare edge, the visitor is in CF-Connecting-IP. When it is not -
    someone calling the herokuapp.com origin directly - CF-Connecting-IP is
    whatever they chose to send, so the hop itself is the answer. That is
    what stops one person minting unlimited "different visitors" by setting
    a header.

    With no X-Forwarded-For at all (local runs, tests) remote_addr is used.
    """
    forwarded = request.headers.get('X-Forwarded-For', '')
    hop = forwarded.rsplit(',', 1)[-1] if forwarded.strip() else request.remote_addr
    hop_ip = _parse_ip(hop or '')
    if hop_ip is None:
        return ''
    if any(hop_ip in net for net in _CLOUDFLARE_NETWORKS):
        visitor = _parse_ip(request.headers.get('CF-Connecting-IP', ''))
        if visitor is not None:
            return str(visitor)
    return str(hop_ip)


def hashable_ip(v6_bits):
    """client_ip() as the visitor HMACs key on it: an IPv6 address cut to its
    first `v6_bits`, an IPv4-mapped one read as the IPv4 address, IPv4 whole,
    and '' if it can't be read. Only ever an HMAC's input: never store or log
    it."""
    ip = client_ip()
    parsed = _parse_ip(ip)
    if parsed is not None and parsed.version == 6:
        if parsed.ipv4_mapped is not None:
            return str(parsed.ipv4_mapped)
        return str(ipaddress.ip_network(f'{parsed}/{v6_bits}', strict=False).network_address)
    return ip


def visitor_hash(day, user_agent):
    """Who is visiting on `day` (the Pacific date), for counting people in
    app_visits: 16 hex characters of an HMAC of the visitor's IP and user
    agent, keyed on the app secret. Never the IP itself: HMAC rather than a
    bare hash because the IPv4 space is small enough to brute-force a bare
    SHA-256 back to the address. IPv6 is cut to its /64, as
    crowd.player_hash cuts it, since privacy extensions rotate the low 64
    bits.

    The day is in the salt, so the same visitor gets a new value every day
    and can't be followed from one day to the next. The salt's shape,
    'visit-<date>|', keeps these apart from crowd's '<game>-<kind>|' keys.
    """
    key = str(current_app.secret_key).encode()
    message = f'visit-{day.isoformat()}|{hashable_ip(64)}|{user_agent}'
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()[:16]
