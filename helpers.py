"""Small shared helpers used across route blueprints."""

import math
from datetime import datetime, timezone


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
