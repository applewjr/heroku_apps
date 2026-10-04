"""Daily digest email: the whole dashboard, pushed rather than visited.

Run by Heroku Scheduler:

    python -m monitoring.daily_digest

Two jobs, and the second is the important one.

The obvious job is replacing a daily visit to /etl_dash with an email.

The less obvious job is being a *second, independent* notification channel.
Every alert in this application ends the same way: a log line, a Heroku drain,
Papertrail, an email. That is one path, and it fails silently - a Papertrail
outage, or simply exhausting the free plan's log quota, takes the entire
alerting system down without alerting anyone, because the thing that would
report it is the thing that is down. This job re-runs every health check
itself and puts the numbers in the email over SMTP, touching nothing
Papertrail owns.

So the digest arriving proves the database, the scheduler and mail all work.
The digest *not* arriving is itself a signal, and it is the one signal the
alerting path cannot suppress.

Imports stay lean for the same reason run_checks' do: this runs on a metered
one-off dyno. `data.py` is avoided in particular - importing it loads four CSV
and JSON datasets plus pandas - so the dashboard queries are read straight out
of their YAML, and `extensions` is avoided because importing it builds a
connection pool and pings Redis through a five-attempt retry loop.
"""

import os
import smtplib
import sys
import time
from datetime import datetime
from email.mime.text import MIMEText

import mysql.connector
import pytz
import yaml

import config
from monitoring import alerts, run_checks

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH_PATH = os.path.join(APP_ROOT, 'datasets', 'etl_dash_queries.yaml')

# Gmail clips a message past ~102 KB and hides the rest behind a "view entire
# message" link, which would quietly truncate the bottom of the digest. Cap
# what any one panel can contribute and say so in the output rather than
# letting a panel that grew silently push everything below it out of view.
MAX_ROWS_PER_PANEL = 40

# Largest tables, for the storage section. There is nowhere to record history,
# so a size trend is not available here - the `ok db_size_mb` lines in
# Papertrail carry that. What this answers instead is the more actionable
# question: if storage is climbing, which table is doing it.
TOP_TABLES = 10

PLAN_LIMIT_MB = 1024


def load_dash_queries(path=DASH_PATH):
    with open(path, 'r') as handle:
        return yaml.safe_load(handle) or {}


def _connect():
    conn = mysql.connector.connect(**run_checks.db_config())
    cursor = conn.cursor()
    # Same two session settings the hourly runner uses, for the same reasons:
    # read storage sizes through to the engine rather than from a 24-hour
    # cache, and make sure a hung query cannot cost the whole digest.
    for pragma in ("SET SESSION information_schema_stats_expiry = 0",
                   "SET SESSION max_execution_time = {}".format(
                       run_checks.QUERY_TIMEOUT_MS)):
        try:
            cursor.execute(pragma)
        except Exception:
            # Best effort. A digest with slightly stale sizes beats no digest.
            pass
    return conn, cursor


def _rows(cursor, sql, params=()):
    """Run a read and return (columns, rows). Raises; callers decide."""
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    columns = [d[0] for d in cursor.description] if cursor.description else []
    return columns, rows


##### sections #####

def gather_health(cursor, hour_pst):
    """Re-run every health check and report the value, pass or fail.

    This is what makes the digest independent of Papertrail. It deliberately
    does not alert: run_checks already owns that decision hourly, and a second
    process emitting the same JJ_ALERT lines would double every email.

    Every check is evaluated, including ones outside their `only_between_pst`
    window. That window is a repeat-rate control for the *alerting* path - it
    exists so a slow-moving breach emails once a day instead of hourly - and a
    report that sends exactly once a day has no such problem. Obeying it here
    would mean the digest never showed `db_size_mb` at all, since it runs at
    6am and that check only opens at 9am.

    A breach outside its window is reported as `info` rather than `FAIL`, and
    does not count toward the subject line. Some windows mean "do not email so
    often" but others mean "the answer is meaningless yet" - the ETL checks
    read a legitimate 0 before the midnight job lands - and nothing in the spec
    distinguishes them, so the safe reading is to show the number without
    letting it raise an alarm.
    """
    out = []
    for name, spec in run_checks.load_checks().items():
        in_window = run_checks.in_active_window(spec, hour_pst)
        try:
            sql = spec['sql'].strip()
            if not sql.upper().startswith('SELECT'):
                out.append((name, 'FAIL', None, spec.get('threshold'), None,
                            'check SQL must be a SELECT'))
                continue
            started = time.monotonic()
            cursor.execute(sql)
            row = cursor.fetchone()
            cursor.fetchall()
            ms = int((time.monotonic() - started) * 1000)
            value = row[0] if row else None
            passed, reason = run_checks.evaluate(spec, value)

            if passed:
                status, note = 'ok', ''
            elif in_window:
                status = 'FAIL'
                note = spec.get('description') or reason
            else:
                status = 'info'
                note = 'over, but outside its alerting window {}'.format(
                    spec['only_between_pst'])

            out.append((
                name, status, value,
                '{} {}'.format(spec.get('compare', '>='), spec['threshold']),
                ms, note,
            ))
        except Exception as e:
            out.append((name, 'FAIL', None, spec.get('threshold'), None,
                        '{}: {}'.format(type(e).__name__, e)))
    return out


def gather_latency():
    """Fetch each probe once and report how long it took.

    Live rather than historical: probe timings live in Papertrail, not in any
    table, so there is nothing here to query. One sample a day is enough to
    notice a page that has become slow between digests.
    """
    out = []
    base = config.HEALTH_WEB_URL.rstrip('/')
    try:
        probes = run_checks.load_web_checks()
    except Exception as e:
        return [('(config)', '-', None, '{}: {}'.format(type(e).__name__, e))]

    for name, spec in probes.items():
        try:
            response, ms = run_checks._fetch(base + spec['path'])
            note = ''
            if response.status_code != 200:
                note = 'status {}'.format(response.status_code)
            elif spec.get('expect') and spec['expect'] not in response.text:
                note = 'expected content missing'
            elif ms > spec['max_ms']:
                note = 'slower than {} ms'.format(spec['max_ms'])
            out.append((name, spec['path'], ms, note))
        except Exception as e:
            out.append((name, spec.get('path', '?'), None,
                        '{}: {}'.format(type(e).__name__, e)))
    return out


def gather_storage(cursor):
    """Total size against the plan, and the tables driving it."""
    _cols, rows = _rows(cursor, """
        SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024)
        FROM information_schema.tables WHERE table_schema = DATABASE()
    """)
    total = rows[0][0] if rows else None

    _cols, tables = _rows(cursor, """
        SELECT table_name,
               ROUND((data_length + index_length) / 1024 / 1024, 1) AS mb,
               table_rows
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
        ORDER BY (data_length + index_length) DESC
        LIMIT %s
    """, (TOP_TABLES,))
    return total, tables


def gather_feedback(cursor):
    """Everything submitted in the last day, in full.

    The feedback_new alert is the instant ping and carries only the topic,
    truncated to 200 characters by alerts.scrub. This is the readable copy -
    and the backstop for anything the honeypot suppressed an alert for, which
    would otherwise be invisible until someone went looking.
    """
    return _rows(cursor, """
        SELECT submit_time, feedback_header, feedback_body, referrer
        FROM feedback
        WHERE submit_time >= CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles') - INTERVAL 1 DAY
        ORDER BY id DESC
    """)


def gather_blossom_crowd(cursor):
    """What players did to the Blossom word list in the last day. FYI only.

    Corrections apply on their own once enough different players agree (see
    "crowd corrections" in routes/blossom.py), so this is a record, not a
    to-do list. Remove in /blossom_admin undoes any of it.

    Returns (columns, rows, error). Self-contained on failure, unlike the
    other gather_* functions: build() has no per-section guard, and this
    section must never be the reason the digest does not arrive.
    """
    try:
        columns, rows = _rows(cursor, """
            SELECT v.word,
                   v.vote AS report,
                   COUNT(*) AS players_7d,
                   MAX(v.created_at) AS last_report,
                   CASE
                       WHEN v.vote = 'invalid' AND MAX(i.word) IS NOT NULL
                           THEN CONCAT('removed (', MAX(i.source), ')')
                       WHEN v.vote = 'missing' AND MAX(a.word) IS NOT NULL
                           THEN CONCAT('added (', MAX(a.source), ')')
                       WHEN v.vote = 'invalid' AND MAX(a.source) = 'admin'
                           THEN 'kept: you added it'
                       WHEN v.vote = 'missing' AND MAX(i.source) = 'admin'
                           THEN 'kept out: you removed it'
                       ELSE 'waiting'
                   END AS result
            FROM blossom_word_votes v
            LEFT JOIN blossom_invalid_words i ON i.word = v.word
            LEFT JOIN blossom_added_words a ON a.word = v.word
            WHERE v.created_at >= CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles') - INTERVAL 7 DAY
            GROUP BY v.word, v.vote
            HAVING MAX(v.created_at) >= CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles') - INTERVAL 1 DAY
            ORDER BY players_7d DESC, last_report DESC
        """)
        return columns, rows, None
    except Exception as e:
        return [], [], '{}: {}'.format(type(e).__name__, e)


def gather_dashboard(cursor):
    """Every /etl_dash panel, every round, in file order.

    Unlike the route, a panel that fails is reported rather than dropped.
    routes/dashboards.py swallows the exception with a print and renders the
    page without that table, so a broken panel looks exactly like a panel that
    was never there. In an email nobody is looking at the shape of, that is
    the difference between noticing and not.
    """
    panels = []
    for name, details in load_dash_queries().items():
        sql = (details.get('query') or '').strip()
        try:
            if not sql.upper().startswith('SELECT'):
                raise ValueError('panel SQL must be a SELECT')
            columns, rows = _rows(cursor, sql)
            panels.append((name, details.get('description', ''),
                           columns, rows, None))
        except Exception as e:
            panels.append((name, details.get('description', ''), [], [],
                           '{}: {}'.format(type(e).__name__, e)))
    return panels


##### rendering #####

CSS = """
body{font:13px -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
     color:#222;background:#fff;margin:0;padding:16px}
h1{font-size:18px;margin:0 0 2px}
h2{font-size:15px;margin:26px 0 6px;padding-bottom:4px;border-bottom:2px solid #ddd}
h3{font-size:13px;margin:16px 0 4px}
p.sub{color:#777;margin:0 0 4px}
table{border-collapse:collapse;margin:4px 0 10px;font-size:12px}
th{text-align:left;background:#f4f4f4;padding:4px 8px;border:1px solid #ddd;
   white-space:nowrap}
td{padding:3px 8px;border:1px solid #e4e4e4;vertical-align:top}
tr:nth-child(even) td{background:#fafafa}
.bad{color:#a12; font-weight:bold}
.good{color:#161}
.muted{color:#888}
.note{color:#a12}
pre{white-space:pre-wrap;margin:2px 0;font:12px ui-monospace,Consolas,monospace}
"""


def _esc(value):
    if value is None:
        return '<span class="muted">-</span>'
    return (str(value).replace('&', '&amp;')
                      .replace('<', '&lt;')
                      .replace('>', '&gt;'))


def _table(columns, rows, limit=MAX_ROWS_PER_PANEL):
    if not rows:
        return '<p class="muted">no rows</p>'
    head = ''.join('<th>{}</th>'.format(_esc(c)) for c in columns)
    body = []
    for row in rows[:limit]:
        body.append('<tr>{}</tr>'.format(
            ''.join('<td>{}</td>'.format(_esc(v)) for v in row)))
    extra = ''
    if len(rows) > limit:
        extra = ('<p class="muted">showing {} of {} rows</p>'
                 .format(limit, len(rows)))
    return '<table><tr>{}</tr>{}</table>{}'.format(head, ''.join(body), extra)


def render(now, health, latency, storage, feedback, panels, blossom=((), (), None)):
    total_mb, tables = storage
    ok = sum(1 for r in health if r[1] == 'ok')
    bad = sum(1 for r in health if r[1] == 'FAIL')

    h = ['<html><head><meta charset="utf-8"><style>{}</style></head><body>'.format(CSS)]
    h.append('<h1>JJ Apps daily digest</h1>')
    h.append('<p class="sub">{}</p>'.format(_esc(now.strftime('%Y-%m-%d %H:%M PST'))))

    h.append('<h2>Health checks</h2>')
    h.append('<p class="sub">Re-run here rather than read from Papertrail, so '
             'this section still arrives if Papertrail does not.</p>')
    # Every cell is built as finished HTML here rather than escaped on the way
    # out. An earlier version decided per cell with `v.startswith('<')`, which
    # silently ate the limit column of every `<=` check - "<= 240" starts with
    # a '<' and went out unescaped, and the browser swallowed it as a tag.
    rows = []
    for name, status, value, threshold, ms, note in health:
        cls = {'ok': 'good', 'FAIL': 'bad'}.get(status, 'muted')
        note_html = _esc(note) if note else ''
        if note and status == 'FAIL':
            note_html = '<span class="note">{}</span>'.format(_esc(note))
        cells = (
            '<span class="{}">{}</span>'.format(cls, status),
            _esc(name),
            _esc(value),
            _esc(threshold),
            '' if ms is None else '{} ms'.format(ms),
            note_html,
        )
        rows.append('<tr>{}</tr>'.format(
            ''.join('<td>{}</td>'.format(c) for c in cells)))
    head = ''.join('<th>{}</th>'.format(c) for c in
                   ('', 'check', 'value', 'limit', 'took', 'note'))
    h.append('<table><tr>{}</tr>{}</table>'.format(head, ''.join(rows)))

    h.append('<h2>Page latency</h2>')
    h.append('<p class="sub">Measured now. The hourly run records these too, so '
             '<code>ok web:blossom</code> in Papertrail carries the trend.</p>')
    h.append(_table(('probe', 'path', 'ms', 'note'), latency))

    h.append('<h2>Storage</h2>')
    if total_mb is None:
        h.append('<p class="note">size unavailable</p>')
    else:
        pct = 100.0 * float(total_mb) / PLAN_LIMIT_MB
        h.append('<p>{} MB of {} MB ({:.0f}% of the JawsDB Leopard Shared plan)</p>'
                 .format(_esc(total_mb), PLAN_LIMIT_MB, pct))
    h.append(_table(('table', 'MB', 'approx rows'), tables, limit=TOP_TABLES))

    fb_cols, fb_rows = feedback
    h.append('<h2>Feedback, last 24 hours ({})</h2>'.format(len(fb_rows)))
    if not fb_rows:
        h.append('<p class="muted">none</p>')
    for row in fb_rows:
        submit_time, header, body_text, referrer = row[0], row[1], row[2], row[3]
        h.append('<h3>{}</h3>'.format(_esc(header)))
        h.append('<p class="sub">{} &middot; from {}</p>'
                 .format(_esc(submit_time), _esc(referrer)))
        h.append('<pre>{}</pre>'.format(_esc(body_text)))

    bl_cols, bl_rows, bl_error = blossom
    h.append('<h2>Blossom word fixes by players, last 24 hours ({})</h2>'.format(len(bl_rows)))
    h.append('<p class="sub">Applied on their own once enough different players '
             'agree. Nothing to do here; Remove in /blossom_admin undoes any of it.</p>')
    if bl_error:
        h.append('<p class="note">section failed: {}</p>'.format(_esc(bl_error)))
    else:
        h.append(_table(bl_cols, bl_rows))

    h.append('<h2>ETL dashboard</h2>')
    h.append('<p class="sub">Every panel from /etl_dash, every round. A panel '
             'that fails is shown as an error here; the page silently omits it.</p>')
    for name, description, columns, rows, error in panels:
        h.append('<h3>{}</h3>'.format(_esc(name)))
        if description:
            h.append('<p class="sub">{}</p>'.format(_esc(description)))
        if error:
            h.append('<p class="note">panel failed: {}</p>'.format(_esc(error)))
        else:
            h.append(_table(columns, rows))

    h.append('</body></html>')
    return ''.join(h), ok, bad, total_mb


def send(subject, html):
    msg = MIMEText(html, 'html', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = config.GMAIL_SENDER_EMAIL
    msg['To'] = config.GMAIL_RECEIVER_EMAIL
    with smtplib.SMTP('smtp.gmail.com', 587, timeout=30) as server:
        server.starttls()
        server.login(config.GMAIL_SENDER_EMAIL, config.GMAIL_PASS)
        server.sendmail(config.GMAIL_SENDER_EMAIL, config.GMAIL_RECEIVER_EMAIL,
                        msg.as_string())


def build(now):
    """Gather every section. Returns (html, subject)."""
    conn, cursor = _connect()
    try:
        health = gather_health(cursor, now.hour)
        storage = gather_storage(cursor)
        feedback = gather_feedback(cursor)
        panels = gather_dashboard(cursor)
        # Last, so a failure here can't leave the cursor in a state that
        # costs a section gathered after it.
        blossom = gather_blossom_crowd(cursor)
    finally:
        cursor.close()
        conn.close()

    # Outside the connection: the probes are HTTP and can take seconds, and
    # holding one of fifteen JawsDB connections open across them is rude.
    latency = gather_latency()

    html, ok, bad, total_mb = render(now, health, latency, storage, feedback, panels, blossom)
    # The summary goes in the subject so the phone notification is the report.
    # Most days that is the only part read, and it should be enough.
    subject = 'JJ daily {} - {} ok, {} alert{}, {} MB'.format(
        now.strftime('%Y-%m-%d'), ok, bad, '' if bad == 1 else 's',
        total_mb if total_mb is not None else '?')
    return html, subject


def main():
    started = time.monotonic()
    now = datetime.now(pytz.timezone('America/Los_Angeles'))
    try:
        html, subject = build(now)
        send(subject, html)
    except Exception as e:
        # The one failure this cannot report to itself. Hand it to the other
        # channel: if the digest breaks, Papertrail says so; if Papertrail
        # breaks, the digest still arrives. Neither can hide alone.
        alerts.alert('digest_failed', sev='crit', exc=type(e).__name__, msg=e)
        return 1

    alerts.pulse('digest', ms=int((time.monotonic() - started) * 1000),
                 bytes=len(html))
    print("sent: {}".format(subject), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
