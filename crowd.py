"""Crowd word corrections: players fix a solver's word list themselves.

Shared by /blossom (routes/blossom.py) and /smush (routes/smush.py). Each game
has its own three tables - its votes, its removed words, its added words - so
one game's corrections never touch another's list (James's call, 2026-10-04).
Only the mechanics here are shared.

A player says the game refused a word we list ('invalid') or accepted one we
don't ('missing'). A change applies once enough *different* players agree.
Nobody reviews it; the game's admin page Remove is the undo (it also resets the
word's votes).

Players are never told these numbers, or that their vote was the one that
tipped a word over: page copy only says reports are taken into account, and
the JSON replies are identical either way. Knowing the count is the first thing
anyone gaming this would want. Admin pages and the daily digest show them.
"""

import hashlib
import hmac
import ipaddress
from datetime import datetime, timedelta
from urllib.parse import urlencode

import pytz
from flask import current_app

from data import word_pop
from extensions import db_cursor, pst_now_str
from helpers import client_ip
from monitoring import alerts
from monitoring.crowd_report import CROWD_GAMES, report_sql

# Five to remove a common word (raised from 3 on 2026-10-03, James's call). A
# tap is cheap, so some players will use it on words the game took, not ones
# it refused. Those players all tick the same popular words, so the threshold
# sets how much of that the list can absorb. CROWD_MAX_INVALID_PER_DAY below
# deals with the heavy cases outright.
# Rarer words need fewer (CROWD_REMOVE_VOTES_BY_POP, 2026-10-10, James's
# call): the games refuse obscure words far more often, so a flag on one is
# more likely right. Not below 3: replaying the first week of Smush votes, 4
# of the 6 rare words with exactly 2 flags were ones James had checked and
# added by hand, and 3 of those were flagged by the same pair within seconds.
# Two to add because the word has to be typed and has to fit the puzzle
# letters - but not one, because long words score highest and one player's
# typo ("unfulfiling") would otherwise land at the top of everyone's list.
CROWD_REMOVE_VOTES = 5
# (lowest Zipf popularity, players needed to remove), most common first. The
# floors are two of the Smush solver's tier floors (functions/all_words.py
# pop_tiers), so "needs 3" lines up with its rare tag. A word below every
# floor, or missing from data.word_pop, needs CROWD_REMOVE_VOTES_RARE.
CROWD_REMOVE_VOTES_BY_POP = ((3.3, CROWD_REMOVE_VOTES), (2.0, 4))
CROWD_REMOVE_VOTES_RARE = 3
CROWD_ADD_VOTES = 2
# A player who flags more than this many words Invalid in one day (Pacific,
# midnight to midnight) is crossing words off, not reporting rejections:
# genuine Blossom ones run 1-2 per puzzle (9/29 hinnying and yatagan, 9/30
# figuline and ingulfing). None of that player's Invalid flags from that day
# count - including the ones made before they passed the limit. James's call,
# 2026-10-03; check it against real per-player counts once there are some.
CROWD_MAX_INVALID_PER_DAY = 10
# Older votes stop counting, so occasional mis-taps on a good word cannot add
# up across months of puzzles. A genuinely bad word gets its votes on its
# puzzle day anyway. The daily digest repeats this number (it can't import
# this module); a test keeps the two equal.
CROWD_WINDOW_DAYS = 7
# Per player, per rolling day. Real players flag a handful of words a puzzle;
# this bounds what anyone holding three IP addresses could do.
CROWD_MAX_VOTES_PER_DAY = 30

_CROWD_OPPOSITE = {'invalid': 'missing', 'missing': 'invalid'}

# Admin pages: rows per page, and the report results a change has already
# settled. Settled reports are hidden from the list unless the admin asks.
ADMIN_PAGE_SIZE = 100
SETTLED_RESULTS = ('removed (crowd)', 'kept: you added it',
                   'kept out: you removed it', 'added (crowd)')
# Query args an admin page carries from one link to the next. Not 'success' or
# 'error': those are one-off messages from a redirect.
_ADMIN_CARRIED_ARGS = ('q', 'settled', 'tab', 'rpage', 'ipage', 'apage')


def remove_votes_needed(word):
    """Players needed to take `word` out of a game's list: fewer the rarer it is."""
    pop = word_pop.get(word)
    for floor, needed in CROWD_REMOVE_VOTES_BY_POP:
        if pop is not None and pop >= floor:
            return needed
    return CROWD_REMOVE_VOTES_RARE


def _pst_days_ago(days):
    """pst_now_str's format, `days` back - for comparing against created_at."""
    then = datetime.now(pytz.timezone('America/Los_Angeles')) - timedelta(days=days)
    return then.strftime('%Y-%m-%d %H:%M:%S')


def _pst_today_start():
    """Midnight Pacific today, in created_at's format."""
    return datetime.now(pytz.timezone('America/Los_Angeles')).strftime('%Y-%m-%d 00:00:00')


def _admin_url(path, args, **changes):
    """The admin page's own URL with its carried args, plus `changes`.
    A change set to None drops that arg."""
    query = {k: args[k] for k in _ADMIN_CARRIED_ARGS if args.get(k)}
    query.update(changes)
    query = {k: v for k, v in query.items() if v is not None}
    return path + ('?' + urlencode(query) if query else '')


def _pager(path, args, tab, param, page, pages):
    """Prev / numbers / Next for one list. `param` is the query arg holding
    this list's page number, so each tab pages on its own."""
    def url(n):
        return _admin_url(path, args, tab=tab, **{param: str(n)})
    return {
        'page': page,
        'pages': pages,
        'prev': url(page - 1) if page > 1 else None,
        'next': url(page + 1) if page < pages else None,
        'numbers': [(n, url(n)) for n in range(1, pages + 1)],
    }


def _paginate(rows, page):
    """(this page's rows, page number, page count). A page outside the list
    clamps to the nearest real one, so a stale link still shows something."""
    pages = max(1, -(-len(rows) // ADMIN_PAGE_SIZE))
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = min(max(page, 1), pages)
    start = (page - 1) * ADMIN_PAGE_SIZE
    return rows[start:start + ADMIN_PAGE_SIZE], page, pages


def _matching(rows, query):
    """Rows whose word (first column) contains `query`, case-blind."""
    if not query:
        return rows
    return [row for row in rows if query in row[0].lower()]


class CrowdList:
    """One game's crowd-corrected word list, kept in <game>_word_votes (one
    row per word, kind of vote and player), <game>_invalid_words (removed
    from the game's list) and <game>_added_words (added to it).

    game      -- also names the failure alert ('<game>_vote_failed') and
                 salts the voter hash, so the same visitor is a different
                 player in each game
    on_change -- called after any change to the two word tables, to drop
                 that game's cached word list
    """

    def __init__(self, game, on_change):
        if game not in CROWD_GAMES:
            raise ValueError(f'not a crowd game: {game!r}')
        self.game = game
        self.votes_table = f'{game}_word_votes'
        self.invalid_table = f'{game}_invalid_words'
        self.added_table = f'{game}_added_words'
        self.on_change = on_change
        # The list a crowd decision lands in, and the list it takes the word out of.
        self.tables = {
            'invalid': (self.invalid_table, self.added_table),
            'missing': (self.added_table, self.invalid_table),
        }

    @staticmethod
    def threshold(word, kind):
        """Players needed before the crowd's `kind` of change to `word` applies."""
        return remove_votes_needed(word) if kind == 'invalid' else CROWD_ADD_VOTES

    def voter_hash(self):
        """Who is voting: an HMAC of the visitor's IP, never the IP itself.

        HMAC rather than a plain hash because the IPv4 space is small enough to
        brute-force a bare SHA-256 straight back to the address. IPv6 is cut to
        its /64: privacy extensions rotate the low 64 bits, so otherwise one
        phone would count as a new player every day.
        """
        ip = client_ip()
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.version == 6:
            if parsed.ipv4_mapped is not None:
                ip = str(parsed.ipv4_mapped)
            else:
                ip = str(ipaddress.ip_network(f'{parsed}/64', strict=False).network_address)
        key = str(current_app.secret_key).encode()
        salt = f'{self.game}-vote|'.encode()
        return hmac.new(key, salt + ip.encode(), hashlib.sha256).hexdigest()[:32]

    def _count_players(self, cursor, word, kind):
        """Distinct players behind a change to this word, inside the vote window.

        For Invalid, a vote only counts if its player flagged no more than
        CROWD_MAX_INVALID_PER_DAY words on the Pacific day they cast it.
        """
        sql = (f"SELECT COUNT(*) FROM {self.votes_table} v "
               "WHERE v.word = %s AND v.vote = %s AND v.created_at >= %s")
        params = [word, kind, _pst_days_ago(CROWD_WINDOW_DAYS)]
        if kind == 'invalid':
            sql += (f" AND (SELECT COUNT(*) FROM {self.votes_table} d"
                    " WHERE d.voter_hash = v.voter_hash AND d.vote = 'invalid'"
                    " AND d.created_at >= DATE(v.created_at)"
                    " AND d.created_at < DATE(v.created_at) + INTERVAL 1 DAY) <= %s")
            params.append(CROWD_MAX_INVALID_PER_DAY)
        cursor.execute(sql, tuple(params))
        return cursor.fetchone()[0]

    def _put_back_unsupported(self, cursor, words):
        """Undo crowd removals among `words` that no longer have enough players.

        Called when a player passes CROWD_MAX_INVALID_PER_DAY: their earlier
        flags that day may have helped remove a word before they were known to
        be crossing words off. True if anything was put back.
        """
        put_back = False
        for word in set(words):
            if self._count_players(cursor, word, 'invalid') < remove_votes_needed(word):
                cursor.execute(
                    f"DELETE FROM {self.invalid_table} WHERE word = %s AND source = 'crowd'", (word,))
                put_back = put_back or cursor.rowcount > 0
        return put_back

    def _apply_crowd_change(self, cursor, word, kind):
        """Move the word into the list the crowd voted for. True if anything changed.

        James's own entry in the other list wins: the crowd never reverses it.
        """
        target, other = self.tables[kind]
        cursor.execute(f"SELECT source FROM {other} WHERE word = %s", (word,))
        row = cursor.fetchone()
        if row is not None and row[0] != 'crowd':
            return False
        cursor.execute(f"SELECT 1 FROM {target} WHERE word = %s", (word,))
        if cursor.fetchone() is not None:
            return False
        cursor.execute(f"DELETE FROM {other} WHERE word = %s AND source = 'crowd'", (word,))
        cursor.execute(f"INSERT INTO {target} (word, added_date, source) VALUES (%s, %s, 'crowd')",
                       (word, pst_now_str()))
        # Votes the other way were about the word before this change. Left in
        # place, one more of them would flip it straight back.
        cursor.execute(f"DELETE FROM {self.votes_table} WHERE word = %s AND vote = %s",
                       (word, _CROWD_OPPOSITE[kind]))
        return True

    def record_vote(self, word, kind, puzzle):
        """Count one player's vote, and apply the change once enough agree.

        Returns True if this vote is the one that applied it. Written inline
        rather than through the write-behind queue because the vote, the count
        and the change must happen together, and the change has to clear this
        process's word-list cache. Best-effort: a database problem costs the
        vote, never the request.
        """
        voter = self.voter_hash()
        applied = put_back = False
        try:
            with db_cursor() as (conn, cursor):
                cursor.execute(
                    f"SELECT COUNT(*) FROM {self.votes_table} WHERE voter_hash = %s AND created_at >= %s",
                    (voter, _pst_days_ago(1)))
                if cursor.fetchone()[0] >= CROWD_MAX_VOTES_PER_DAY:
                    return False
                # A repeat vote refreshes the old one rather than adding a second.
                # Recorded even when it won't count, so real per-player numbers
                # are there to check CROWD_MAX_INVALID_PER_DAY against later.
                cursor.execute(
                    f"""INSERT INTO {self.votes_table} (word, vote, voter_hash, puzzle, created_at)
                       VALUES (%s, %s, %s, %s, %s) AS new
                       ON DUPLICATE KEY UPDATE created_at = new.created_at, puzzle = new.puzzle""",
                    (word, kind, voter, puzzle, pst_now_str()))
                crossing_off = False
                if kind == 'invalid':
                    cursor.execute(
                        f"SELECT COUNT(*) FROM {self.votes_table} "
                        "WHERE voter_hash = %s AND vote = 'invalid' AND created_at >= %s",
                        (voter, _pst_today_start()))
                    crossing_off = cursor.fetchone()[0] > CROWD_MAX_INVALID_PER_DAY
                if crossing_off:
                    # None of this player's flags today count, so anything their
                    # earlier ones helped remove goes back if it no longer has
                    # enough players without them.
                    cursor.execute(
                        f"SELECT word FROM {self.votes_table} "
                        "WHERE voter_hash = %s AND vote = 'invalid' AND created_at >= %s",
                        (voter, _pst_today_start()))
                    put_back = self._put_back_unsupported(cursor, [w for (w,) in cursor.fetchall()])
                elif self._count_players(cursor, word, kind) >= self.threshold(word, kind):
                    applied = self._apply_crowd_change(cursor, word, kind)
                conn.commit()
        except Exception as e:
            alerts.alert_throttled(f'{self.game}_vote_failed', exc=type(e).__name__, msg=e)
            return False
        if applied or put_back:
            self.on_change()
        return applied

    def retract_vote(self, word, kind):
        """Take back this player's vote.

        If that leaves too few players behind a change the crowd made, the
        change is undone too: taking back a mis-tap that tipped a word over
        should put the word back. (Votes only age out of the count; that never
        reverts anything.)
        """
        reverted = False
        try:
            with db_cursor() as (conn, cursor):
                cursor.execute(
                    f"DELETE FROM {self.votes_table} WHERE word = %s AND vote = %s AND voter_hash = %s",
                    (word, kind, self.voter_hash()))
                # Only a vote that was actually counted can undo anything: a
                # flag that never recorded one (the word was already gone, say)
                # can't.
                if cursor.rowcount > 0 and self._count_players(cursor, word, kind) < self.threshold(word, kind):
                    target = self.tables[kind][0]
                    cursor.execute(f"DELETE FROM {target} WHERE word = %s AND source = 'crowd'", (word,))
                    reverted = cursor.rowcount > 0
                conn.commit()
        except Exception as e:
            alerts.alert_throttled(f'{self.game}_vote_failed', exc=type(e).__name__, msg=e)
            return
        if reverted:
            self.on_change()

    ##### admin #####

    def admin_set(self, word, kind):
        """Put the word in the list for `kind` ('invalid' = remove it from the
        game's list, 'missing' = add it), as James's own call.

        Your call is the final one: marked 'admin' (upgrading any crowd row)
        so the crowd never reverses it, taken out of the other list so the two
        can't disagree, and the crowd's votes on it start over.
        """
        table, other = self.tables[kind]
        with db_cursor() as (conn, cursor):
            cursor.execute(f"""
                INSERT INTO {table} (word, added_date, source)
                VALUES (%s, CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles'), 'admin')
                ON DUPLICATE KEY UPDATE source = 'admin'
            """, (word,))
            cursor.execute(f"DELETE FROM {other} WHERE word = %s", (word,))
            cursor.execute(f"DELETE FROM {self.votes_table} WHERE word = %s", (word,))
            conn.commit()
        self.on_change()

    def admin_remove(self, word, kind):
        """Take the word out of the list for `kind`.

        Clearing its votes makes this a clean undo of a crowd change too: the
        crowd starts again from zero rather than re-applying it on the next
        vote.
        """
        table = self.tables[kind][0]
        with db_cursor() as (conn, cursor):
            cursor.execute(f"DELETE FROM {table} WHERE word = %s", (word,))
            cursor.execute(f"DELETE FROM {self.votes_table} WHERE word = %s", (word,))
            conn.commit()
        self.on_change()

    def list_rows(self):
        """(invalid rows, added rows), each (word, added_date, source), newest first."""
        with db_cursor() as (conn, cursor):
            cursor.execute(f"""
                SELECT word, added_date, source
                FROM {self.invalid_table}
                ORDER BY added_date DESC
            """)
            invalid_words = cursor.fetchall()
            cursor.execute(f"""
                SELECT word, added_date, source
                FROM {self.added_table}
                ORDER BY added_date DESC
            """)
            added_words = cursor.fetchall()
        return invalid_words, added_words

    def recent_reports(self):
        """Every word players reported in the vote window, for the admin page:
        (word, report, players_7d, last_report, result, first_report, board,
        board_date), one row per board rather than merged across boards,
        newest board first. board_date is the estimated day that board was
        presented, not just the last vote's date."""
        with db_cursor() as (conn, cursor):
            cursor.execute(report_sql(self.game, CROWD_WINDOW_DAYS))
            return cursor.fetchall()

    def admin_view(self, args, path, tabs=('reports', 'invalid', 'added')):
        """What an admin page shows of this game's lists, from its query args.

        q filters every list by word. settled=1 shows the reports
        SETTLED_RESULTS hides. rpage / ipage / apage pick a page of the
        reports, removed and added lists. tab is the tab to open, if it is one
        of `tabs`. `path` is the admin page's own path, for the links.
        """
        query = (args.get('q') or '').strip().lower()
        show_settled = args.get('settled') == '1'
        active_tab = args.get('tab') if args.get('tab') in tabs else 'reports'

        invalid_all, added_all = self.list_rows()
        reports_all = self.recent_reports()
        settled = [r for r in reports_all if r[4] in SETTLED_RESULTS]
        open_reports = [r for r in reports_all if r[4] not in SETTLED_RESULTS]
        reports, reports_page, reports_pages = _paginate(
            _matching(reports_all if show_settled else open_reports, query), args.get('rpage'))
        invalid_words, invalid_page, invalid_pages = _paginate(
            _matching(invalid_all, query), args.get('ipage'))
        added_words, added_page, added_pages = _paginate(
            _matching(added_all, query), args.get('apage'))

        # Normally a word is in one list only (each change clears the other).
        # If one ever is in both, the solver still plays it, because the added
        # list wins: say so, rather than let the admin page disagree with it.
        in_both = sorted({w for w, *_ in invalid_all} & {w for w, *_ in added_all})

        return {
            'reports': reports,
            'invalid_words': invalid_words,
            'added_words': added_words,
            'reports_pager': _pager(path, args, 'reports', 'rpage', reports_page, reports_pages),
            'invalid_pager': _pager(path, args, 'invalid', 'ipage', invalid_page, invalid_pages),
            'added_pager': _pager(path, args, 'added', 'apage', added_page, added_pages),
            'open_count': len(open_reports),
            'settled_count': len(settled),
            'invalid_total': len(invalid_all),
            'added_total': len(added_all),
            'show_settled': show_settled,
            'settled_url': _admin_url(path, args, tab='reports',
                                      settled=None if show_settled else '1', rpage=None),
            'query': query,
            'clear_url': _admin_url(path, {'tab': active_tab, 'settled': args.get('settled')}),
            'active_tab': active_tab,
            'in_both': in_both,
            'votes_needed': self.threshold,
        }
