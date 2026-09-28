"""Health checks and alert emission.

Nothing in this package may have import side effects: it is imported both by
the web app and by one-off scheduler dynos, so importing it must not open a
database connection or ping Redis the way ``extensions`` does.
"""
