"""Smush word list, crowd word corrections, and /smush_admin.

The solver route itself is run_smush in routes/wordgames.py; it hands any
JSON body carrying an `action` to handle_vote here.
"""

import time

from flask import Blueprint, jsonify, redirect, render_template, request

import crowd
from crowd import CROWD_ADD_VOTES, CROWD_REMOVE_VOTES, CROWD_WINDOW_DAYS
from data import word_pop, words
from extensions import auth, db_cursor
from helpers import ValidationError, parse_letters

bp = Blueprint('smush', __name__)


##### word list #####

# Memoized in the module (not SimpleCache: that pickles, so every cache.get
# would rebuild the ~17MB set per request). Holds at most one extra copy of
# the word list per process, and none at all while Smush's corrections are
# empty.
_smush_words = {'value': None, 'expires': 0.0}


def _forget_smush_words():
    """Make the next get_smush_words() re-read the corrections."""
    _smush_words['expires'] = 0.0


def get_smush_words():
    """The full word list with Smush's own corrections applied: words removed
    by players or /smush_admin taken out, words added put in.

    Only smush_* tables. Blossom's corrections stay in Blossom: until
    2026-10-04 this read blossom_invalid_words / blossom_added_words, which
    James never meant to reach Smush. Falls back to the raw list if the DB is
    unreachable. Refreshed every 12 hours, or straight away after a change.
    """
    if _smush_words['value'] is not None and time.time() < _smush_words['expires']:
        return _smush_words['value']

    invalid_words, added_words = set(), set()
    try:
        with db_cursor() as (conn, cursor):
            cursor.execute("SELECT word FROM smush_invalid_words")
            invalid_words = {row[0].lower() for row in cursor.fetchall()}
            cursor.execute("SELECT word FROM smush_added_words")
            added_words = {row[0].lower() for row in cursor.fetchall()}
    except Exception as e:
        print(f"Error fetching smush word corrections: {e}")
        # A DB blip at refresh time must not evict the corrections for 12
        # hours: keep serving the last good list (or the raw list if there
        # has never been one) and retry soon.
        fallback = _smush_words['value'] if _smush_words['value'] is not None else words
        _smush_words['value'] = fallback
        _smush_words['expires'] = time.time() + 300
        return fallback

    # data.py's word set is already lowercase; only build a corrected copy
    # when there is actually something to correct.
    if invalid_words or added_words:
        smush_words = (words - invalid_words) | added_words
    else:
        smush_words = words

    _smush_words['value'] = smush_words
    _smush_words['expires'] = time.time() + 43200
    return smush_words


##### crowd corrections #####

# Players fix the word list themselves: the ✗ on a result ("Smush didn't
# accept this word") votes it out, and the "Smush accepted a word that isn't
# listed?" box under the results votes one in. Mechanics, thresholds and the
# never-tell-players rule live in crowd.py, shared with /blossom; only Smush's
# own tables are touched here.
#
# ✗ was already there before it voted: it hides a word from this player's
# results at no cost, and the All 8 plan re-plans around it. So some players
# will use it to steer the plan rather than to report a refusal. The
# per-player daily limit in crowd.py catches the heavy cases; the first ✗ in a
# browser also asks players to keep it for refusals.

SMUSH_CROWD = crowd.CrowdList('smush', on_change=_forget_smush_words)

# Smush's own word lengths, same as the played-word box on the page.
SMUSH_MIN_LEN, SMUSH_MAX_LEN = 3, 15


def _parse_word(value):
    word = parse_letters(value if isinstance(value, str) else None, 'word',
                         max_len=SMUSH_MAX_LEN).lower()
    if len(word) < SMUSH_MIN_LEN:
        raise ValidationError(f"word must be at least {SMUSH_MIN_LEN} letters")
    return word


def _parse_board(data):
    """The board a vote came from: (center, outer), lowercase.

    The page sends its nine tiles, so anything other than one center letter
    and eight distinct others is not the page talking.
    """
    center, outer = data.get('center'), data.get('outer')
    center = parse_letters(center if isinstance(center, str) else None, 'center', max_len=1).lower()
    outer = parse_letters(outer if isinstance(outer, str) else None, 'outer', max_len=8).lower()
    if len(center) != 1 or len(outer) != 8 or len(set(outer)) != 8 or center in outer:
        raise ValidationError("center must be 1 letter and outer 8 other distinct letters")
    return center, outer


def _fits_board(word, center, outer):
    """Could this word be played on this board, by Smush's own rules?
    (Letter wear aside: a word can be refused or accepted at any point.)"""
    return center in word and set(word) <= set(center + outer)


def _puzzle_tag(center, outer):
    return center + ':' + ''.join(sorted(outer))


SMUSH_VOTE_ACTIONS = ('reject', 'restore', 'suggest_missing')


def handle_vote(data):
    """A crowd vote posted to /smush. Every reply is the same whether or not
    the vote changed the list (see crowd.py)."""
    action = data.get('action')
    if action not in SMUSH_VOTE_ACTIONS:
        raise ValidationError('unknown action')
    word = _parse_word(data.get('word'))
    center, outer = _parse_board(data)

    if action == 'reject':
        # Only a word actually on offer, on a board it fits: anything else is
        # not a result this player could have been shown.
        if _fits_board(word, center, outer) and word in get_smush_words():
            SMUSH_CROWD.record_vote(word, 'invalid', _puzzle_tag(center, outer))
        return jsonify({'status': 'success'})

    if action == 'restore':
        # Tapping a hidden word back: takes back this player's ✗, if it
        # counted.
        SMUSH_CROWD.retract_vote(word, 'invalid')
        return jsonify({'status': 'success'})

    if not _fits_board(word, center, outer):
        return jsonify({'status': 'success', 'result': 'not_in_puzzle'})
    if word in get_smush_words():
        return jsonify({'status': 'success', 'result': 'already_listed'})
    SMUSH_CROWD.record_vote(word, 'missing', _puzzle_tag(center, outer))
    return jsonify({'status': 'success', 'result': 'recorded'})


##### admin #####

def _admin_form():
    """The word and its list, or None for the list if no choice was sent.
    Nothing is assumed: a missing choice must not count as a removal."""
    word = request.form.get('word', '').strip().lower()
    kind = request.form.get('word_type')
    return word, kind if kind in ('invalid', 'missing') else None


NO_CHOICE = 'Choose Remove invalid word or Add missing word first'


@bp.route('/smush_admin')
@auth.login_required
def smush_admin():
    """Smush's corrected word list and what players have reported."""
    try:
        view = SMUSH_CROWD.admin_view(request.args.to_dict(), request.path)
        return render_template('smush_admin.html',
                               **view,
                               word_pop=word_pop,
                               crowd_remove_votes=CROWD_REMOVE_VOTES,
                               crowd_add_votes=CROWD_ADD_VOTES,
                               crowd_window_days=CROWD_WINDOW_DAYS)
    except Exception as e:
        print(f"Error loading smush admin: {e}")
        return render_template('error.html', return_type='Admin Error'), 500


@bp.route('/smush_admin/add_word', methods=['POST'])
@auth.login_required
def smush_add_word():
    """Remove a word from Smush's list ('invalid') or add one ('missing')."""
    word, kind = _admin_form()
    if kind is None:
        return redirect(f'/smush_admin?error={NO_CHOICE}')
    if not word.isalpha():
        return redirect('/smush_admin?error=Word must be letters only')
    try:
        SMUSH_CROWD.admin_set(word, kind)
    except Exception as e:
        print(f"Error adding smush word: {e}")
        return redirect('/smush_admin?error=Database error')
    done = 'added to the word list' if kind == 'missing' else 'removed from the word list'
    return redirect(f'/smush_admin?success={word} {done}')


@bp.route('/smush_admin/remove_word', methods=['POST'])
@auth.login_required
def smush_remove_word():
    """Undo an entry in either list, and reset that word's votes."""
    word, kind = _admin_form()
    if kind is None:
        return redirect(f'/smush_admin?error={NO_CHOICE}')
    if not word.isalpha():
        return redirect('/smush_admin?error=Word must be letters only')
    try:
        SMUSH_CROWD.admin_remove(word, kind)
    except Exception as e:
        print(f"Error removing smush word: {e}")
        return redirect('/smush_admin?error=Database error')
    return redirect(f'/smush_admin?success={word} undone')
