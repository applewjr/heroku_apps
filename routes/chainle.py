"""Chainle solver data, the solve request, and /chainle_admin.

The solver route itself is run_chainle in routes/wordgames.py; it hands a
round to solve_request here.

Self-contained: the word data ships in datasets/ (see datasets/CHAINLE_DATA.md)
and nothing is fetched at runtime. The solver never contacts any Chainle site.

/chainle_admin has the words taken off or kept on the solver's list, and a
log of rounds the admin plays, to tune the ranking in functions/chainle.py
against. Logged rounds stay in the database: the solver never shows them and
never uses them as answers.
"""

import hashlib
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

import numpy as np
import pytz
from flask import Blueprint, jsonify, redirect, render_template, request

import crowd
from crowd import CROWD_REMOVE_VOTES_BY_POP, CROWD_REMOVE_VOTES_RARE, CROWD_WINDOW_DAYS
from data import data_folder, word_pop
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


def solver_version():
    """A short code for the solver in this process: a hash of the engine's
    code and its word data, so it changes whenever either does. The admin
    saves it with each round, to tell which version made the picks. None if
    the data won't load."""
    data = get_data()
    if data is None:
        return None
    if _static.get('version') is None:
        h = hashlib.sha1()
        with open(ch.__file__, 'rb') as fh:
            h.update(fh.read())
        for name in ch.FIELDS:
            a = np.ascontiguousarray(getattr(data, name))
            h.update(f'{name} {a.dtype.str} {a.shape}'.encode())
            h.update(a)
        _static['version'] = h.hexdigest()[:8]
    return _static['version']


def page_context():
    """What the page template needs besides the SEO blob. Rendering the page
    never touches the database."""
    data = get_data()
    return {'vocab_size': data.size if data is not None else None}


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
    removed, kept = get_corrections()
    return jsonify(ch.solve(solver, clues, kind, removed=removed, kept=kept))


##### word corrections #####

# /chainle_admin's Remove takes a word off the solver's suggestions, and its
# Allow ('missing' in crowd.py) keeps one on that the -s filter would drop
# (PHYSICS). One list for every Chainle site. Players have no way to report
# words (James's call, 2026-10-22): the -s form and spelling filters cover
# every refusal the round log has seen. The lists use crowd.py's tables like
# the other word games', so the admin page works the same way; with no
# player votes, its Player Reports tab stays empty.

# (removed, kept) word sets, memoized in the module like routes/smush.py's
# word list. The memo of how the solver rates logged rounds is dropped with
# it, since corrections change those ranks.
_corrections = {'value': None, 'expires': 0.0}
NO_CORRECTIONS = (frozenset(), frozenset())


def _forget_corrections():
    """Make the next get_corrections() re-read the lists."""
    _corrections['expires'] = 0.0
    _assessed.clear()


def get_corrections():
    """(removed, kept): the words /chainle_admin took off the list, and the
    words it keeps on it, lower case. Refreshed every
    12 hours, or straight away after a change."""
    if _corrections['value'] is not None and time.time() < _corrections['expires']:
        return _corrections['value']
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute("SELECT word FROM chainle_invalid_words")
            removed = frozenset(row[0].lower() for row in cursor.fetchall())
            cursor.execute("SELECT word FROM chainle_added_words")
            kept = frozenset(row[0].lower() for row in cursor.fetchall())
    except Exception as e:
        print(f"Error fetching chainle word corrections: {e}")
        # A DB blip must not drop the corrections for 12 hours: keep the
        # last good lists (or none) and retry soon.
        _corrections['value'] = _corrections['value'] or NO_CORRECTIONS
        _corrections['expires'] = time.time() + 300
        return _corrections['value']
    _corrections['value'] = (removed, kept)
    _corrections['expires'] = time.time() + 43200
    return _corrections['value']


CHAINLE_CROWD = crowd.CrowdList('chainle', on_change=_forget_corrections)

MAX_WORD_LEN = 40


##### admin: the round log #####

ADMIN = '/chainle_admin'
SITES = ('chainle.io', 'chaindle.org', 'other')
# The day's puzzle, an unlimited one, or a past day's from the archive.
MODES = ('daily', 'unlimited', 'archive')
# The daily game's rounds: 1-3 two-word bridges, 4 a three-word bridge, 5 the
# closest meaning. The form can override the kind for a site that differs.
ROUND_KIND = {1: 'bridge', 2: 'bridge', 3: 'bridge', 4: 'bridge', 5: 'meaning'}
ROUND_CLUES = {1: 2, 2: 2, 3: 2, 4: 3, 5: 1}
# The summaries cover the last SHOW_ROUNDS rounds; the page lists only the
# newest RECENT_ROUNDS of them.
SHOW_ROUNDS = 100
RECENT_ROUNDS = 10
MAX_NOTES = 200
FIRST_DAY = date(2026, 1, 1)

# A row is one round, keyed by the day played, site, round and clues
# (mysql_tables/chainle_rounds_unlimited.sql): saving the same round again
# replaces it, and another puzzle the same day adds a row.
ROUND_COLUMNS = ('played_on', 'site', 'mode', 'round_no', 'kind', 'clues', 'answer', 'guess',
                 'score', 'closeness', 'game_rank', 'rank_of', 'notes', 'created_at')
ROUND_KEY = ('played_on', 'site', 'round_no', 'clues')
# What the solver made of a round when it was logged
# (mysql_tables/chainle_rounds_version.sql): its version (solver_version()),
# its first pick and its rank for the game's answer. Saved and shown once
# that SQL has run; until then the admin works as before without them.
VERSION_COLUMNS = ('solver_version', 'solver_top', 'answer_rank')


def _upsert(columns):
    return (f"INSERT INTO chainle_rounds ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))}) AS new ON DUPLICATE KEY UPDATE "
            + ', '.join(f'{c} = new.{c}' for c in columns if c not in ROUND_KEY))


UPSERT_ROUND = _upsert(ROUND_COLUMNS)
UPSERT_ROUND_VERSIONED = _upsert(ROUND_COLUMNS + VERSION_COLUMNS)
# Per solver version, newest first: rounds, your average score, rounds it
# could be judged on, its first pick right, its answer in the top 3, and
# rounds you took its first pick.
VERSION_SUMMARY = """
SELECT solver_version, COUNT(*), AVG(score), SUM(solver_top IS NOT NULL AND answer IS NOT NULL),
    SUM(solver_top = answer), SUM(answer_rank <= 3), SUM(guess = solver_top), MIN(played_on), MAX(played_on)
FROM chainle_rounds WHERE solver_version IS NOT NULL
GROUP BY solver_version ORDER BY MAX(created_at) DESC LIMIT 10
"""


def _has_version_columns(cursor):
    """True once mysql_tables/chainle_rounds_version.sql has run."""
    cursor.execute("SHOW COLUMNS FROM chainle_rounds LIKE 'solver_version'")
    return bool(cursor.fetchall())


def _versions(rows):
    return [{'version': v, 'rounds': n, 'avg': round(avg) if avg is not None else None,
             'judged': int(judged or 0), 'top1': int(top1 or 0), 'top3': int(top3 or 0),
             'took': int(took or 0), 'first': str(first), 'last': str(last)}
            for v, n, avg, judged, top1, top3, took, first, last in rows]


def _today():
    return datetime.now(pytz.timezone('America/Los_Angeles')).date()


def _back_to_log(**message):
    """Back to the admin page's Round Log tab, with a success or error
    message (and any form defaults: see _form_defaults)."""
    message = {k: v for k, v in dict(message, tab='log').items() if v}
    return redirect(ADMIN + '?' + urlencode(message))


def _back(**message):
    """Back to /chainle_admin as the admin left it after a word action, with
    a message (see crowd.admin_return_url)."""
    return redirect(crowd.admin_return_url(ADMIN, request.form, **message))


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
        removed, kept = get_corrections()
        _assessed[key] = ch.assess(data, clues.lower().split(), kind, [answer, guess],
                                   removed=removed, kept=kept)
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
         rank_of, notes, version, top_then, rank_then) in rows:
        got = _assess(data, kind, clues, answer, guess) if data is not None else None
        r = {
            'id': rid, 'played_on': str(played_on), 'site': site, 'mode': mode, 'round_no': round_no,
            'type': _round_type(kind, clues.split()), 'clues': clues.split(), 'answer': answer,
            'guess': guess, 'score': score, 'closeness': closeness.split() if closeness else [],
            'game_rank': game_rank, 'rank_of': rank_of, 'notes': notes, 'known': got is not None,
            'top': got['top'] if got else None,
            'rank': got['ranks'].get(answer) if got and answer else None,
            'guess_rank': got['ranks'].get(guess) if got and guess else None,
            # The version it was logged with, and what that version said.
            'version': version, 'top_then': top_then, 'rank_then': rank_then,
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


ADMIN_TABS = ('reports', 'invalid', 'added', 'log')


def _empty_view(active_tab):
    """The word tabs when their tables can't be read, so the round log still
    works (and the page says what's wrong)."""
    pager = {'page': 1, 'pages': 1, 'prev': None, 'next': None, 'numbers': []}
    return {'reports': [], 'invalid_words': [], 'added_words': [],
            'reports_pager': pager, 'invalid_pager': pager, 'added_pager': pager,
            'open_count': 0, 'settled_count': 0, 'invalid_total': 0, 'added_total': 0,
            'show_settled': False, 'settled_url': ADMIN, 'query': '', 'clear_url': ADMIN,
            'keep': {'tab': active_tab}, 'active_tab': active_tab, 'in_both': [],
            'votes_needed': CHAINLE_CROWD.threshold}


def _round_log():
    """(context, error) for the Round Log tab: the newest logged rounds with
    how the solver rates each, and the summary by solver version."""
    try:
        with db_cursor() as (conn, cursor):
            versioned = _has_version_columns(cursor)
            cursor.execute("SELECT COUNT(*) FROM chainle_rounds")
            total = cursor.fetchone()[0]
            cursor.execute(
                "SELECT id, played_on, site, mode, round_no, kind, clues, answer, guess, score, "
                "closeness, game_rank, rank_of, notes, "
                + (', '.join(VERSION_COLUMNS) if versioned else 'NULL, NULL, NULL') +
                " FROM chainle_rounds ORDER BY played_on DESC, created_at DESC, id DESC LIMIT %s",
                (SHOW_ROUNDS,))
            rows = cursor.fetchall()
            versions = []
            if versioned:
                cursor.execute(VERSION_SUMMARY)
                versions = _versions(cursor.fetchall())
    except Exception as e:
        print(f"Error loading chainle round log: {e}")
        return ({'total': 0, 'versioned': False, 'versions': [], 'rounds': [], 'summary': [],
                 'avg_score': None}, f'{type(e).__name__}: {e}')
    return {'total': total, 'versioned': versioned, 'versions': versions,
            **assess_rounds(get_data(), rows)}, None


@bp.route(ADMIN)
@auth.login_required
def chainle_admin():
    """What players reported, the removed and allowed words, and the round
    log. Each half still shows if the other's tables can't be read."""
    args = request.args.to_dict()
    # Players can't report words, so the round log is what's worth opening on.
    args.setdefault('tab', 'log')
    try:
        view, crowd_error = CHAINLE_CROWD.admin_view(args, request.path, tabs=ADMIN_TABS), None
    except Exception as e:
        print(f"Error loading chainle admin word lists: {e}")
        view = _empty_view(args['tab'] if args.get('tab') in ADMIN_TABS else 'reports')
        crowd_error = f'{type(e).__name__}: {e}'
    log, log_error = _round_log()
    form_date, form_round = _form_defaults(request.args)
    return render_template('chainle_admin.html', **view, **log, crowd_error=crowd_error,
                           log_error=log_error, form_date=form_date, form_round=form_round,
                           sites=SITES, modes=MODES, round_clues=ROUND_CLUES,
                           data_ok=get_data() is not None, recent=RECENT_ROUNDS,
                           version=solver_version(), word_pop=word_pop,
                           crowd_remove_bands=CROWD_REMOVE_VOTES_BY_POP,
                           crowd_remove_rare=CROWD_REMOVE_VOTES_RARE,
                           crowd_window_days=CROWD_WINDOW_DAYS)


@bp.route(ADMIN + '/log_round', methods=['POST'])
@auth.login_required
def chainle_log_round():
    """Save a round. Logging the same date, site and round again replaces it."""
    keep = {'date': request.form.get('played_on'), 'round': request.form.get('round_no')}
    try:
        row = parse_round(request.form)
    except ValidationError as e:
        return _back_to_log(error=str(e), **keep)
    data = get_data()
    got = _assess(data, row['kind'], row['clues'], row['answer'], row['guess']) if data is not None else None
    row.update(solver_version=solver_version(), solver_top=got['top'] if got else None,
               answer_rank=got['ranks'].get(row['answer']) if got and row['answer'] else None)
    try:
        with db_cursor() as (conn, cursor):
            if _has_version_columns(cursor):
                cursor.execute(UPSERT_ROUND_VERSIONED, tuple(row[c] for c in ROUND_COLUMNS + VERSION_COLUMNS))
            else:
                cursor.execute(UPSERT_ROUND, tuple(row[c] for c in ROUND_COLUMNS))
            conn.commit()
    except Exception as e:
        print(f"Error logging chainle round: {e}")
        return _back_to_log(error='Database error', **keep)
    saved = f"Saved round {row['round_no']} ({row['mode']})"
    if row['round_no'] < max(ROUND_KIND):
        return _back_to_log(success=saved, date=row['played_on'], round=row['round_no'] + 1)
    return _back_to_log(success=f'{saved}: that was the last one')


@bp.route(ADMIN + '/delete_round', methods=['POST'])
@auth.login_required
def chainle_delete_round():
    try:
        rid = int(request.form.get('id', ''))
    except ValueError:
        return _back_to_log(error='No round chosen')
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute("DELETE FROM chainle_rounds WHERE id = %s", (rid,))
            conn.commit()
    except Exception as e:
        print(f"Error deleting chainle round: {e}")
        return _back_to_log(error='Database error')
    return _back_to_log(success='Round deleted')


def _admin_form():
    """The word and its list, or None for the list if no choice was sent.
    Nothing is assumed: a missing choice must not count as a removal."""
    word = request.form.get('word', '').strip().lower()
    kind = request.form.get('word_type')
    return word, kind if kind in ('invalid', 'missing') else None


NO_CHOICE = 'Choose Remove word or Allow word first'


def _check_word(word):
    """An error message for a word the lists can't take, or None."""
    if not word.isalpha():
        return 'Word must be letters only'
    data = get_data()
    if data is not None and data.id(word) < 0:
        return f"{word} isn't in the solver's word list, so it is never suggested anyway"
    return None


@bp.route(ADMIN + '/add_word', methods=['POST'])
@auth.login_required
def chainle_add_word():
    """Take a word off the list ('invalid') or keep one on it ('missing':
    the -s filter and player reports leave it alone)."""
    word, kind = _admin_form()
    if kind is None:
        return _back(error=NO_CHOICE)
    error = _check_word(word)
    if error:
        return _back(error=error)
    try:
        CHAINLE_CROWD.admin_set(word, kind)
    except Exception as e:
        print(f"Error adding chainle word: {e}")
        return _back(error='Database error')
    done = 'allowed: it stays on the list' if kind == 'missing' else 'taken off the list'
    return _back(success=f'{word} {done}')


@bp.route(ADMIN + '/remove_word', methods=['POST'])
@auth.login_required
def chainle_remove_word():
    """Undo an entry in either list, and reset that word's votes."""
    word, kind = _admin_form()
    if kind is None:
        return _back(error=NO_CHOICE)
    if not word.isalpha():
        return _back(error='Word must be letters only')
    try:
        CHAINLE_CROWD.admin_remove(word, kind)
    except Exception as e:
        print(f"Error removing chainle word: {e}")
        return _back(error='Database error')
    return _back(success=f'{word} undone')
