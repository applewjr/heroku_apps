"""Chainle solver data, the solve request, and the admin's round log.

The solver route itself is run_chainle in routes/wordgames.py; it hands a
round to solve_request here.

Self-contained: the word data ships in datasets/ (see datasets/CHAINLE_DATA.md)
and nothing is fetched at runtime. The solver never contacts any Chainle site.

/chainle_admin keeps a log of rounds the admin plays, to tune the ranking in
functions/chainle.py against. Logged rounds stay in the database: the solver
never shows them and never uses them as answers.
"""

import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

import pytz
from flask import Blueprint, jsonify, redirect, render_template, request

from data import data_folder
from extensions import auth, db_cursor, pst_now_str
from functions import chainle as ch
from helpers import ValidationError, release_memory
from monitoring import alerts

bp = Blueprint('chainle', __name__)

# Chainle's clues are single words: a bridge round has two or three, the
# closest-meaning round one.
CLUES_PER_KIND = {'bridge': (2, 3), 'meaning': (1, 1)}
MAX_CLUE_LEN = 20
_CLUE = re.compile(r'[A-Za-z]{1,%d}' % MAX_CLUE_LEN)


##### solver data #####

# A module memo, not SimpleCache: SimpleCache pickles, so every get would
# copy ~14 MB of arrays (see routes/guess_the_phrase.py).
_static = {'data': None, 'failed_at': 0.0}
_load_lock = threading.Lock()
# After a failed load, wait this long before trying again, so a broken file
# costs one attempt per window rather than one per request.
LOAD_RETRY_SECONDS = 300


def get_data():
    """The shipped word data, read once per process (~0.1 s, ~14 MB). None
    if it won't load; the page then says the solver is unavailable."""
    if _static['data'] is not None:
        return _static['data']
    with _load_lock:
        if _static['data'] is None and time.time() - _static['failed_at'] > LOAD_RETRY_SECONDS:
            try:
                _static['data'] = ch.Data.from_file(os.path.join(data_folder, 'chainle_data.npz'))
                # Decompressing the file goes through buffers glibc would
                # otherwise keep.
                release_memory()
            except Exception as e:
                _static['failed_at'] = time.time()
                alerts.alert_throttled('chainle_data_failed', sev='crit', exc=type(e).__name__, msg=e)
    return _static['data']


# The day the solver's accuracy last improved. The page's beta note shows it,
# and search engines get it as dateModified. Bump it when a tuning change
# ships, not on every deploy.
ACCURACY_UPDATED = date(2026, 10, 15)


def page_context():
    """What the page template needs besides the SEO blob. Rendering the page
    never touches the database."""
    data = get_data()
    return {'vocab_size': data.size if data is not None else None,
            'accuracy_updated': ACCURACY_UPDATED}


##### solving #####

def _clue_list(raw, kind):
    """The clue words, lower case, or raise ValidationError."""
    lo, hi = CLUES_PER_KIND[kind]
    if not isinstance(raw, list) or not lo <= len(raw) <= hi:
        count = f'{lo} or {hi} words' if lo != hi else f'{lo} word'
        raise ValidationError(f'a {kind} round takes {count}')
    clues = []
    for clue in raw:
        if not isinstance(clue, str) or not _CLUE.fullmatch(clue.strip()):
            raise ValidationError(f'each clue must be one word of at most {MAX_CLUE_LEN} letters')
        clues.append(clue.strip().lower())
    if len(set(clues)) != len(clues):
        raise ValidationError('the clues must be different words')
    return clues


def parse_request(data):
    """(clues, kind) from a POST body."""
    kind = data.get('kind', 'bridge')
    if kind not in CLUES_PER_KIND:
        raise ValidationError('kind must be bridge or meaning')
    return _clue_list(data.get('clues'), kind), kind


def solve_request(data):
    """The JSON the page renders for one round."""
    clues, kind = parse_request(data)
    solver = get_data()
    if solver is None:
        return jsonify(error='The solver is unavailable right now.'), 503
    return jsonify(ch.solve(solver, clues, kind))


##### admin: the round log #####

ADMIN = '/chainle_admin'
SITES = ('chainle.io', 'chaindle.org', 'other')
# The day's puzzle, an unlimited one, or a past day's from the archive.
MODES = ('daily', 'unlimited', 'archive')
# The daily game's rounds: 1-3 two-word bridges, 4 a three-word bridge, 5 the
# closest meaning. The form can override the kind for a site that differs.
ROUND_KIND = {1: 'bridge', 2: 'bridge', 3: 'bridge', 4: 'bridge', 5: 'meaning'}
ROUND_CLUES = {1: 2, 2: 2, 3: 2, 4: 3, 5: 1}
SHOW_ROUNDS = 100
MAX_WORD_LEN = 40
MAX_NOTES = 200
FIRST_DAY = date(2026, 1, 1)

# A row is one round, keyed by the day played, site, round and clues
# (mysql_tables/chainle_rounds_unlimited.sql): saving the same round again
# replaces it, and another puzzle the same day adds a row.
ROUND_COLUMNS = ('played_on', 'site', 'mode', 'round_no', 'kind', 'clues', 'answer', 'guess',
                 'score', 'closeness', 'game_rank', 'rank_of', 'notes', 'created_at')
UPSERT_ROUND = f"""
INSERT INTO chainle_rounds ({', '.join(ROUND_COLUMNS)})
VALUES ({', '.join(['%s'] * len(ROUND_COLUMNS))}) AS new
ON DUPLICATE KEY UPDATE mode = new.mode, kind = new.kind, answer = new.answer,
    guess = new.guess, score = new.score, closeness = new.closeness, game_rank = new.game_rank,
    rank_of = new.rank_of, notes = new.notes, created_at = new.created_at
"""


def _today():
    return datetime.now(pytz.timezone('America/Los_Angeles')).date()


def _back(**message):
    """Back to the admin page, with a success or error message (and any
    form defaults: see _form_defaults)."""
    message = {k: v for k, v in message.items() if v}
    return redirect(ADMIN + ('?' + urlencode(message) if message else ''))


def _form_defaults(args):
    """The log form's date and round. After a save it's the same day's next
    round, so playing all five is one flow; otherwise today's round 1."""
    day = args.get('date', '')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        day = _today().isoformat()
    try:
        round_no = int(args.get('round', ''))
    except ValueError:
        round_no = 1
    return day, round_no if round_no in ROUND_KIND else 1


def _word(form, field, label):
    """An optional single word from the form, upper case, or None."""
    value = form.get(field, '').strip()
    if not value:
        return None
    if not re.fullmatch(r'[A-Za-z]{1,%d}' % MAX_WORD_LEN, value):
        raise ValidationError(f'{label} must be one word, letters only')
    return value.upper()


def _closeness(form, count):
    """The game's closeness to each clue, as it shows them ('0.54 0.08'), or
    None. One number per clue or none at all, each from 0 to 1."""
    raw = [form.get(f'link{n}', '').strip() for n in range(1, count + 1)]
    if not any(raw):
        return None
    values = []
    for r in raw:
        try:
            v = float(r)
        except ValueError:
            v = -1.0
        if not 0 <= v <= 1:
            raise ValidationError('Closeness takes a number from 0 to 1 for every clue')
        values.append(f'{v:.3f}'.rstrip('0').rstrip('.'))
    return ' '.join(values)


def _game_rank(form):
    """(the guess's rank in the game's list, the list's size), either None."""
    rank = form.get('game_rank', '').strip().lstrip('#')
    of = form.get('rank_of', '').strip().replace(',', '')
    for value, label in ((rank, "The game's rank"), (of, 'The word count')):
        if value and not (value.isdigit() and int(value) >= 1):
            raise ValidationError(f'{label} must be a whole number')
    rank, of = (int(rank) if rank else None), (int(of) if of else None)
    if rank and of and rank > of:
        raise ValidationError("The game's rank can't be past its word count")
    return rank, of


def parse_round(form):
    """A round from the admin form, as {column: value} (ROUND_COLUMNS), or
    raise ValidationError with a message for the page. The date is the day
    it was played: unlimited puzzles have no date of their own."""
    raw_day = form.get('played_on', '').strip()
    try:
        played_on = datetime.strptime(raw_day, '%Y-%m-%d').date() if raw_day else _today()
    except ValueError:
        raise ValidationError('Date must look like 2026-10-14')
    if not FIRST_DAY <= played_on <= _today() + timedelta(days=1):
        raise ValidationError('Date is out of range')
    site = form.get('site', '')
    if site not in SITES:
        raise ValidationError('Choose the site you played on')
    mode = form.get('mode') or 'daily'
    if mode not in MODES:
        raise ValidationError('Choose daily or unlimited')
    try:
        round_no = int(form.get('round_no', ''))
    except ValueError:
        round_no = 0
    if round_no not in ROUND_KIND:
        raise ValidationError('Round must be 1 to 5')
    kind = form.get('kind') or ROUND_KIND[round_no]
    if kind not in CLUES_PER_KIND:
        raise ValidationError('Unknown round type')
    clues = [form.get(f'clue{n}', '').strip() for n in (1, 2, 3)]
    try:
        clues = _clue_list([c for c in clues if c], kind)
    except ValidationError as e:
        raise ValidationError(str(e).capitalize())
    answer = _word(form, 'answer', "The game's answer")
    guess = _word(form, 'guess', 'Your guess')
    raw_score = form.get('score', '').strip()
    if raw_score and not (raw_score.isdigit() and int(raw_score) <= 1000):
        raise ValidationError('Score must be a whole number from 0 to 1000')
    notes = form.get('notes', '').strip()[:MAX_NOTES] or None
    game_rank, rank_of = _game_rank(form)
    return {
        'played_on': played_on.isoformat(), 'site': site, 'mode': mode, 'round_no': round_no,
        'kind': kind, 'clues': ' '.join(c.upper() for c in clues), 'answer': answer, 'guess': guess,
        'score': int(raw_score) if raw_score else None, 'closeness': _closeness(form, len(clues)),
        'game_rank': game_rank, 'rank_of': rank_of, 'notes': notes, 'created_at': pst_now_str(),
    }


# How the solver rates each logged round, by (kind, clues, answer, guess):
# the rows don't change, so the admin page only pays for new ones. Cleared
# when full; the data itself only changes on a deploy.
_assessed = {}
ASSESS_MEMO_MAX = 1000


def _assess(data, kind, clues, answer, guess):
    key = (kind, clues, answer, guess)
    if key not in _assessed:
        if len(_assessed) >= ASSESS_MEMO_MAX:
            _assessed.clear()
        _assessed[key] = ch.assess(data, clues.lower().split(), kind, [answer, guess])
    return _assessed[key]


def _round_type(kind, clues):
    if kind == 'meaning':
        return 'Closest meaning'
    return 'Three-word bridge' if len(clues) == 3 else 'Two-word bridge'


def assess_rounds(data, rows):
    """The admin page's rows and summary: for each logged round, where the
    solver ranks the game's answer and your guess, and its own first pick."""
    rounds, tally, scores = [], {}, []
    for (rid, played_on, site, mode, round_no, kind, clues, answer, guess, score, closeness, game_rank,
         rank_of, notes) in rows:
        got = _assess(data, kind, clues, answer, guess) if data is not None else None
        r = {
            'id': rid, 'played_on': str(played_on), 'site': site, 'mode': mode, 'round_no': round_no,
            'type': _round_type(kind, clues.split()), 'clues': clues.split(), 'answer': answer,
            'guess': guess, 'score': score, 'closeness': closeness.split() if closeness else [],
            'game_rank': game_rank, 'rank_of': rank_of, 'notes': notes, 'known': got is not None,
            'top': got['top'] if got else None,
            'rank': got['ranks'].get(answer) if got and answer else None,
            'guess_rank': got['ranks'].get(guess) if got and guess else None,
        }
        rounds.append(r)
        if score is not None:
            scores.append(score)
        if got and answer:
            t = tally.setdefault(r['type'], {'rounds': 0, 'top1': 0, 'top3': 0, 'top10': 0})
            t['rounds'] += 1
            for name, cut in (('top1', 1), ('top3', 3), ('top10', 10)):
                t[name] += r['rank'] is not None and r['rank'] <= cut
    order = ('Two-word bridge', 'Three-word bridge', 'Closest meaning')
    return {
        'rounds': rounds,
        'summary': [(name, tally[name]) for name in order if name in tally],
        'avg_score': round(sum(scores) / len(scores)) if scores else None,
    }


@bp.route(ADMIN)
@auth.login_required
def chainle_admin():
    """The round log: a form to add a round, and how the solver rates each."""
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute("SELECT COUNT(*) FROM chainle_rounds")
            total = cursor.fetchone()[0]
            cursor.execute(
                "SELECT id, played_on, site, mode, round_no, kind, clues, answer, guess, score, "
                "closeness, game_rank, rank_of, notes "
                "FROM chainle_rounds ORDER BY played_on DESC, created_at DESC, id DESC LIMIT %s",
                (SHOW_ROUNDS,))
            rows = cursor.fetchall()
    except Exception as e:
        print(f"Error loading chainle admin: {e}")
        return render_template('error.html', return_type='Admin Error'), 500
    data = get_data()
    form_date, form_round = _form_defaults(request.args)
    return render_template('chainle_admin.html', total=total, form_date=form_date,
                           form_round=form_round, sites=SITES, modes=MODES, round_clues=ROUND_CLUES,
                           data_ok=data is not None, shown=SHOW_ROUNDS, **assess_rounds(data, rows))


@bp.route(ADMIN + '/log_round', methods=['POST'])
@auth.login_required
def chainle_log_round():
    """Save a round. Logging the same date, site and round again replaces it."""
    keep = {'date': request.form.get('played_on'), 'round': request.form.get('round_no')}
    try:
        row = parse_round(request.form)
    except ValidationError as e:
        return _back(error=str(e), **keep)
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute(UPSERT_ROUND, tuple(row[c] for c in ROUND_COLUMNS))
            conn.commit()
    except Exception as e:
        print(f"Error logging chainle round: {e}")
        return _back(error='Database error', **keep)
    saved = f"Saved round {row['round_no']} ({row['mode']})"
    if row['round_no'] < max(ROUND_KIND):
        return _back(success=saved, date=row['played_on'], round=row['round_no'] + 1)
    return _back(success=f'{saved}: that was the last one')


@bp.route(ADMIN + '/delete_round', methods=['POST'])
@auth.login_required
def chainle_delete_round():
    try:
        rid = int(request.form.get('id', ''))
    except ValueError:
        return _back(error='No round chosen')
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute("DELETE FROM chainle_rounds WHERE id = %s", (rid,))
            conn.commit()
    except Exception as e:
        print(f"Error deleting chainle round: {e}")
        return _back(error='Database error')
    return _back(success='Round deleted')
