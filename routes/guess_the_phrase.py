"""Guess The Phrase solver data, players' added phrases, and the admin page.

The solver route itself is run_guess_the_phrase in routes/wordgames.py; it
hands a board to solve_request here, and an added phrase to handle_vote.

Self-contained (James, 2026-10-11): the phrase list ships in datasets/ (see
datasets/PHRASE_BANK.md) and nothing is fetched at runtime. Bored Panda's
answers are never fetched or stored. The only phrases added at runtime are
ones players type in after solving.
"""

import os
import threading
import time

from flask import Blueprint, jsonify, redirect, render_template, request

import crowd
from crowd import CROWD_ADD_VOTES
from data import data_folder
from extensions import auth, db_cursor
from functions import phrase_solver as ps
from helpers import ValidationError
from monitoring import alerts

bp = Blueprint('guess_the_phrase', __name__)

# How long a player's added phrase keeps counting toward the two players it
# needs. Not crowd.py's 7 days: players work through old puzzles, so two who
# solve the same one can be weeks apart. monitoring/daily_digest.py repeats
# this number; a test keeps the two equal.
GTP_WINDOW_DAYS = 30

# Request limits. Bored Panda's longest answer so far is 10 words and 37
# letters; these leave room without letting a request scan far.
MAX_BOARD_CHARS = 80
MAX_WORDS = 12
MAX_WORD_LEN = 20


##### solver data #####

# Module memos, not SimpleCache: SimpleCache pickles, so every get would
# rebuild ~23 MB (see routes/smush.py and memory_creep_plan.md).
_static = {'data': None, 'failed_at': 0.0}
_overlay = {'solver': None, 'expires': 0.0}
_load_lock = threading.Lock()
# After a failed load, wait this long before trying again, so a broken file
# costs one attempt per window rather than one per request.
LOAD_RETRY_SECONDS = 300


class _Solver:
    """What phrase_solver.solve() needs, plus the blocklist for added phrases."""

    def __init__(self, static, bank):
        self.bank = bank
        self.words = static.words
        self.bigrams = static.bigrams
        self.blocklist = static.blocklist


def _load_static():
    """The shipped files, read once per process (~1 s, ~23 MB). None if they
    won't load; the page then says the list is unavailable."""
    if _static['data'] is not None:
        return _static['data']
    with _load_lock:
        if _static['data'] is None and time.time() - _static['failed_at'] > LOAD_RETRY_SECONDS:
            try:
                data = ps.SolverData(os.path.join(data_folder, 'phrase_bank.tsv.gz'),
                                     os.path.join(data_folder, 'phrase_words.tsv.gz'),
                                     os.path.join(data_folder, 'phrase_bigrams.tsv.gz'))
                data.blocklist = ps.load_blocklist(os.path.join(data_folder, 'phrase_blocklist.txt'))
                _static['data'] = data
            except Exception as e:
                _static['failed_at'] = time.time()
                alerts.alert_throttled('gtp_bank_failed', sev='crit', exc=type(e).__name__, msg=e)
    return _static['data']


def _forget_overlay():
    """Make the next get_solver() re-read players' and the admin's changes."""
    _overlay['expires'] = 0.0


def get_solver():
    """The shipped phrase list with the gtp_* corrections applied, or None
    if the shipped files won't load.

    Refreshed every 12 hours, or straight away after a change. A DB blip
    keeps the last good corrections (or none) and retries in 5 minutes,
    so the solver itself never depends on the database.
    """
    static = _load_static()
    if static is None:
        return None
    if _overlay['solver'] is not None and time.time() < _overlay['expires']:
        return _overlay['solver']
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute("SELECT word FROM gtp_invalid_words")
            removed = {row[0].upper() for row in cursor.fetchall()}
            cursor.execute("SELECT word FROM gtp_added_words")
            added = {row[0].upper() for row in cursor.fetchall()}
    except Exception as e:
        print(f"Error fetching guess the phrase corrections: {e}")
        if _overlay['solver'] is None:
            _overlay['solver'] = _Solver(static, ps.OverlayBank(static.bank))
        _overlay['expires'] = time.time() + 300
        return _overlay['solver']
    _overlay['solver'] = _Solver(static, ps.OverlayBank(static.bank, added, removed))
    _overlay['expires'] = time.time() + 43200
    return _overlay['solver']


def page_context():
    """What the page template needs besides the SEO blob. The shipped list
    only: rendering the page never touches the database."""
    static = _load_static()
    return {
        'categories': [(cid, label) for cid, label, _, _ in ps.CATEGORIES],
        'bank_size': static.bank.size if static is not None else None,
    }


##### solving #####

def _parse_request(data):
    """(patterns, tried letters, category id) from a POST body."""
    board = data.get('board')
    if not isinstance(board, str) or len(board) > MAX_BOARD_CHARS:
        raise ValidationError(f'board must be text, at most {MAX_BOARD_CHARS} characters')
    try:
        patterns = ps.parse_board(board)
    except ValueError as e:
        raise ValidationError(f'board: {e}')
    if len(patterns) > MAX_WORDS or max(map(len, patterns)) > MAX_WORD_LEN:
        raise ValidationError(f'board must be at most {MAX_WORDS} words of {MAX_WORD_LEN} letters')
    tried = data.get('tried', '')
    if (not isinstance(tried, str) or len(tried) > 26
            or not all(c.isascii() and c.isalpha() for c in tried)):
        raise ValidationError('tried must be letters only')
    category = data.get('category', 'any')
    if category not in ps.CATEGORY_BY_ID:
        raise ValidationError('unknown category')
    return patterns, tried.upper(), category


def _unavailable():
    return jsonify(error='The phrase list is unavailable right now.'), 503


def solve_request(data):
    """The JSON the page renders for one board state."""
    patterns, tried, category = _parse_request(data)
    solver = get_solver()
    if solver is None:
        return _unavailable()
    _, _, tags, mashup = ps.CATEGORY_BY_ID[category]
    res = ps.solve(solver, ' '.join(patterns), tried, tags, mashup)
    return jsonify(
        total=res['total'],
        blanks=res['blanks'],
        advice=res['advice'],
        phrases=[(d, share) for d, _, share in res['candidates']],
        guesses=[(t, share) for t, _, share in res['fallback']],
        words=res['slots'],
    )


##### players' added phrases #####

# "Solved it and we didn't list the phrase? Add it." Two different players
# typing the same phrase adds it (crowd.py, shared with /blossom and /smush;
# never told to players). There is no player flag the other way: phrases are
# only removed by the admin page.
GTP_CROWD = crowd.CrowdList('gtp', on_change=_forget_overlay, window_days=GTP_WINDOW_DAYS)


def _puzzle_tag(category, patterns):
    """Which puzzle a phrase came from, without a date: category and word
    lengths, e.g. 'song:4-1-7-5', so votes on an old puzzle still match."""
    return f"{category}:{'-'.join(str(len(p)) for p in patterns)}"


def handle_vote(data):
    """An added phrase posted to /guess_the_phrase. Replies never say
    whether the vote counted or tipped the phrase in (see crowd.py)."""
    if data.get('action') != 'suggest_missing':
        raise ValidationError('unknown action')
    patterns, tried, category = _parse_request(data)
    phrase = data.get('phrase')
    if not isinstance(phrase, str) or len(phrase) > MAX_BOARD_CHARS:
        raise ValidationError(f'phrase must be text, at most {MAX_BOARD_CHARS} characters')
    key = ps.phrase_key(phrase)
    if not key:
        raise ValidationError('phrase must be letters, spaces and punctuation only')
    # Only an answer this board allows: blocks most typos and all junk.
    if not ps.fits_board(key, patterns, ps.excluded_letters(patterns, tried)):
        return jsonify(status='success', result='not_on_board')
    solver = get_solver()
    if solver is None:
        return _unavailable()
    # Same reply as a counted vote, so the filter can't be probed.
    if ps.is_blocked(key, solver.blocklist):
        return jsonify(status='success', result='recorded')
    if key in solver.bank:
        return jsonify(status='success', result='already_listed')
    GTP_CROWD.record_vote(key, 'missing', _puzzle_tag(category, patterns), category)
    return jsonify(status='success', result='recorded')


##### admin #####

def _admin_form():
    """The phrase's match key and its list, or None for the list if no
    choice was sent. Nothing is assumed: a missing choice must not count as
    a removal."""
    key = ps.phrase_key(request.form.get('word', ''))
    kind = request.form.get('word_type')
    return key, kind if kind in ('invalid', 'missing') else None


NO_CHOICE = 'Choose Remove phrase or Add phrase first'
ADMIN = '/guess_the_phrase_admin'


@bp.route(ADMIN)
@auth.login_required
def gtp_admin():
    """Players' added phrases and the admin's own changes to the list."""
    try:
        view = GTP_CROWD.admin_view(request.args.to_dict(), request.path)
        return render_template('guess_the_phrase_admin.html', **view,
                               crowd_add_votes=CROWD_ADD_VOTES,
                               crowd_window_days=GTP_WINDOW_DAYS)
    except Exception as e:
        print(f"Error loading guess the phrase admin: {e}")
        return render_template('error.html', return_type='Admin Error'), 500


@bp.route(ADMIN + '/add_word', methods=['POST'])
@auth.login_required
def gtp_add_word():
    """Take a phrase out of the list ('invalid') or put one in ('missing')."""
    key, kind = _admin_form()
    if kind is None:
        return redirect(f'{ADMIN}?error={NO_CHOICE}')
    if not key:
        return redirect(f'{ADMIN}?error=Phrase must be letters and spaces')
    try:
        GTP_CROWD.admin_set(key, kind)
    except Exception as e:
        print(f"Error adding guess the phrase phrase: {e}")
        return redirect(f'{ADMIN}?error=Database error')
    done = 'added to the phrase list' if kind == 'missing' else 'removed from the phrase list'
    return redirect(f'{ADMIN}?success={key} {done}')


@bp.route(ADMIN + '/remove_word', methods=['POST'])
@auth.login_required
def gtp_remove_word():
    """Undo an entry in either list, and reset that phrase's votes."""
    key, kind = _admin_form()
    if kind is None:
        return redirect(f'{ADMIN}?error={NO_CHOICE}')
    if not key:
        return redirect(f'{ADMIN}?error=Phrase must be letters and spaces')
    try:
        GTP_CROWD.admin_remove(key, kind)
    except Exception as e:
        print(f"Error removing guess the phrase phrase: {e}")
        return redirect(f'{ADMIN}?error=Database error')
    return redirect(f'{ADMIN}?success={key} undone')
