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

import pytz
from flask import current_app

from extensions import db_cursor, pst_now_str
from helpers import client_ip
from monitoring import alerts
from monitoring.crowd_report import CROWD_GAMES, report_sql

# Five to remove (raised from 3 on 2026-10-03, James's call). A tap is cheap,
# so some players will use it on words the game took, not ones it refused.
# Those players all tick the same popular words, so the threshold sets how
# much of that the list can absorb. CROWD_MAX_INVALID_PER_DAY below deals with
# the heavy cases outright.
# Two to add because the word has to be typed and has to fit the puzzle
# letters - but not one, because long words score highest and one player's
# typo ("unfulfiling") would otherwise land at the top of everyone's list.
CROWD_REMOVE_VOTES = 5
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


def _pst_days_ago(days):
    """pst_now_str's format, `days` back - for comparing against created_at."""
    then = datetime.now(pytz.timezone('America/Los_Angeles')) - timedelta(days=days)
    return then.strftime('%Y-%m-%d %H:%M:%S')


def _pst_today_start():
    """Midnight Pacific today, in created_at's format."""
    return datetime.now(pytz.timezone('America/Los_Angeles')).strftime('%Y-%m-%d 00:00:00')


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
        self.threshold = {'invalid': CROWD_REMOVE_VOTES, 'missing': CROWD_ADD_VOTES}
        # The list a crowd decision lands in, and the list it takes the word out of.
        self.tables = {
            'invalid': (self.invalid_table, self.added_table),
            'missing': (self.added_table, self.invalid_table),
        }

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
            if self._count_players(cursor, word, 'invalid') < CROWD_REMOVE_VOTES:
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
                elif self._count_players(cursor, word, kind) >= self.threshold[kind]:
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
                if cursor.rowcount > 0 and self._count_players(cursor, word, kind) < self.threshold[kind]:
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
        (word, report, players_7d, last_report, result)."""
        with db_cursor() as (conn, cursor):
            cursor.execute(report_sql(self.game, CROWD_WINDOW_DAYS))
            return cursor.fetchall()
