"""The "what have players done to the word list" query, per game.

Shared by the admin pages (crowd.py) and the daily digest, so both say the
same thing. Lives here, with no imports, because the digest must not import
crowd.py: that pulls in `extensions`, which builds a connection pool and pings
Redis (see the daily_digest docstring).

Each crowd game has the same three tables: <game>_word_votes,
<game>_invalid_words and <game>_added_words.
"""

CROWD_GAMES = ('blossom', 'smush')


def report_sql(game, window_days, recent_days=None):
    """Every word with votes in the last `window_days`, one row per (board,
    word, vote): (word, report, players_7d, last_report, result,
    first_report, board, board_date). With `recent_days`, only words with a
    vote that recent.

    players_7d counts every vote in the window, before the crossing-off rule
    in crowd.py, so it can read above a count that still says 'waiting'.
    board is the puzzle tag (center:outer letters) the reports came from, so
    an admin can tell today's board from an older one.

    Rows are grouped per board rather than merged across boards a word was
    reported on, and boards are sorted newest first - by the most common
    submission date among that board's votes, not the latest one. Most
    votes land on a board the day it is live, but some trickle in later from
    players working through archived boards; using the mode instead of
    MAX(created_at) keeps that lagging tail from making an old board look
    like today's. board_date is that mode date: the estimated day the board
    was actually presented to players.
    """
    if game not in CROWD_GAMES:
        raise ValueError(f'not a crowd game: {game!r}')
    recent = ''
    if recent_days is not None:
        recent = ("HAVING MAX(v.created_at) >= CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles')"
                  f" - INTERVAL {int(recent_days)} DAY")
    window = f"CONVERT_TZ(NOW(), 'UTC', 'America/Los_Angeles') - INTERVAL {int(window_days)} DAY"
    return f"""
        WITH board_dates AS (
            SELECT puzzle,
                   DATE(created_at) AS vote_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY puzzle
                       ORDER BY COUNT(*) DESC, DATE(created_at) DESC
                   ) AS rn
            FROM {game}_word_votes
            WHERE created_at >= {window}
            GROUP BY puzzle, DATE(created_at)
        ),
        boards AS (
            SELECT puzzle, vote_date AS board_date
            FROM board_dates
            WHERE rn = 1
        )
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
               END AS result,
               MIN(v.created_at) AS first_report,
               v.puzzle AS board,
               b.board_date
        FROM {game}_word_votes v
        JOIN boards b ON b.puzzle <=> v.puzzle
        LEFT JOIN {game}_invalid_words i ON i.word = v.word
        LEFT JOIN {game}_added_words a ON a.word = v.word
        WHERE v.created_at >= {window}
        GROUP BY v.puzzle, b.board_date, v.word, v.vote
        {recent}
        ORDER BY b.board_date DESC, v.puzzle DESC, players_7d DESC, last_report DESC
    """
