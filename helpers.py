"""Small shared helpers used across route blueprints."""

from datetime import datetime, timezone


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
