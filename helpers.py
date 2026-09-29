"""Small shared helpers used across route blueprints."""

import math
from datetime import datetime, timezone

from flask import current_app
from itsdangerous import URLSafeTimedSerializer


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


# Minimum plausible gap between a form rendering and a person submitting it.
# Two seconds sits far below any real interaction - reading two labels, typing a
# topic and a sentence - and far above what a scanner spends: the sqlmap run on
# 2026-09-29 put twenty submissions through inside a single second.
MIN_SUBMIT_SECONDS = 2

# Token lifetime. Deliberately generous: a form left open in a tab overnight is
# a person, not a bot, and an expired token is read as "no signal" rather than
# as suspicion, so a long life costs nothing and a short one would misjudge.
FORM_TOKEN_MAX_AGE = 60 * 60 * 24


def _form_serializer(salt):
    # Signed with the app secret, which is a stable env var in production, so
    # tokens survive the twice-daily dyno restart. In dev the key is random per
    # boot, which invalidates tokens across a reload - harmless, because an
    # unverifiable token is treated as no signal.
    return URLSafeTimedSerializer(current_app.secret_key, salt=salt)


def issue_form_token(salt='form'):
    """A signed, timestamped token to embed in a form that a bot might flood."""
    return _form_serializer(salt).dumps('t')


def submitted_too_fast(token, salt='form', min_seconds=MIN_SUBMIT_SECONDS):
    """Did this submission arrive implausibly soon after the form rendered?

    Returns True *only* when the token verifies and the gap is under the
    threshold. Every other outcome returns False, and that asymmetry is the
    whole design: a missing token (a cached page, an older copy of the form), a
    bad signature (a rotated secret), an expired one (a tab left open) and a
    clock that ran backwards all have innocent explanations. Nothing here may
    act against a person on a guess, so every failure mode is "no signal"
    rather than "suspicious".

    Signed rather than a plain timestamp because an unsigned field is one a bot
    can simply backdate. Signing costs nothing - itsdangerous already ships
    with Flask - and makes the check worth having against something that reads
    the form before filling it.
    """
    if not token:
        return False
    try:
        _value, issued_at = _form_serializer(salt).loads(
            token, max_age=FORM_TOKEN_MAX_AGE, return_timestamp=True)
    except Exception:
        # Broad on purpose: BadSignature, SignatureExpired, malformed base64
        # and a missing secret key all mean the same thing here - we learned
        # nothing, so we accuse no one.
        return False
    elapsed = (datetime.now(timezone.utc) - issued_at).total_seconds()
    # A negative gap means clock skew, not speed. Treat it as no signal too.
    return 0 <= elapsed < min_seconds


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
