"""Wordle / Antiwordle / Quordle / misc word-solver routes."""

from flask import Blueprint, jsonify, render_template, request

from data import df, word_pop, words
from extensions import INTERACTIVE_LIMITS, add_data_to_stream, auth, limiter, log_page_visit
from functions import all_words, wordle
from helpers import ValidationError, json_object, make_schema_data, parse_float, parse_int
from routes import chainle
from routes import guess_the_phrase as gtp
from routes import ribbit
from routes import smush as smush_crowd
from routes.ribbit import parse_ribbit_board
from routes.smush import get_smush_words

bp = Blueprint('wordgames', __name__)

NO_WORDS_END = 'Options remaining: 0/5710 (0.0%)'


def abbreviate_keys(data):
    key_map = {'letter': 'l', 'position': 'p', 'color': 'c', 'row': 'r'}
    abbreviated_data = [{key_map[key]: value for key, value in d.items()} for d in data]
    return abbreviated_data


@bp.route("/wordle", methods=["POST", "GET"])
@limiter.limit(INTERACTIVE_LIMITS)
def run_wordle_revamp():

    schema_data = make_schema_data(
        "Wordle Solver - Beat Daily Wordle Puzzle Instantly",
        "Free Wordle solver and strategy tool. Get the best word suggestions to beat today's NYT Wordle puzzle. Smart algorithm finds optimal guesses instantly.",
        "https://www.jamesapplewhite.com/wordle"
    )

    if request.method == "POST":
        try:
            wordle_data_dict = request.get_json().get('wordle_data')
            final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end, first_incomplete_row, complete_rows, \
                show_alt_picks, alt_out1, alt_out2, alt_out3, alt_out4, alt_out5 = wordle.wordle_solver_split_revamp(df, wordle_data_dict)
            first_incomplete_row = 'First Incomplete Row: ' + str(first_incomplete_row)
            complete_rows = 'Complete Rows: ' + str(complete_rows)

            try:
                wordle_data_dict_abbr = abbreviate_keys(wordle_data_dict)
                add_data_to_stream('wordle_logging', wordle_data_dict_abbr)
            except Exception:
                print('wordle_logging_failed')

            return jsonify(final_out1=final_out1, final_out2=final_out2, final_out3=final_out3, final_out4=final_out4, final_out5=final_out5, final_out_end=final_out_end, \
                first_incomplete_row=first_incomplete_row, complete_rows=complete_rows, \
                show_alt_picks=show_alt_picks, alt_out1=alt_out1, alt_out2=alt_out2, alt_out3=alt_out3, alt_out4=alt_out4, alt_out5=alt_out5)
        except Exception:
            return jsonify(final_out1='No words found', final_out2='', final_out3='', final_out4='', final_out5='',
                           final_out_end=NO_WORDS_END,
                           first_incomplete_row='First Incomplete Row: None', complete_rows='Complete Rows: []',
                           show_alt_picks=False, alt_out1='', alt_out2='', alt_out3='', alt_out4='', alt_out5='')
    else:
        # Call the solver with empty data to get initial recommendations
        empty_data = []
        final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end, first_incomplete_row, complete_rows, \
            show_alt_picks, alt_out1, alt_out2, alt_out3, alt_out4, alt_out5 = wordle.wordle_solver_split_revamp(df, empty_data)

        # Pass the results to JavaScript on page load
        log_page_visit('wordle_revamp.html')
        return render_template("wordle_revamp.html",
                            initial_out1=final_out1,
                            initial_out2=final_out2,
                            initial_out3=final_out3,
                            initial_out4=final_out4,
                            initial_out5=final_out5,
                            initial_out_end=final_out_end,
                            initial_show_alt_picks=show_alt_picks,
                            initial_alt_out1=alt_out1,
                            initial_alt_out2=alt_out2,
                            initial_alt_out3=alt_out3,
                            initial_alt_out4=alt_out4,
                            initial_alt_out5=alt_out5,
                            schema_data=schema_data)


@bp.route("/antiwordle", methods=["POST", "GET"])
@limiter.limit(INTERACTIVE_LIMITS)
def run_antiwordle_revamp():

    schema_data = make_schema_data(
        "Antiwordle Solver - Beat Antiwordle Game Instantly",
        "Free Antiwordle solver and strategy tool. Get the best word suggestions to beat Antiwordle game. Smart algorithm finds optimal moves instantly.",
        "https://www.jamesapplewhite.com/antiwordle"
    )

    if request.method == "POST":
        try:
            wordle_data_dict = request.get_json().get('wordle_data')
            final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end, first_incomplete_row, complete_rows = wordle.antiwordle_solver_split_revamp(df, wordle_data_dict)
            first_incomplete_row = 'First Incomplete Row: ' + str(first_incomplete_row)
            complete_rows = 'Complete Rows: ' + str(complete_rows)

            try:
                wordle_data_dict_abbr = abbreviate_keys(wordle_data_dict)
                add_data_to_stream('antiwordle_logging', wordle_data_dict_abbr)
            except Exception:
                print('antiwordle_logging_failed')

            return jsonify(final_out1=final_out1, final_out2=final_out2, final_out3=final_out3, final_out4=final_out4, final_out5=final_out5, final_out_end=final_out_end, \
                first_incomplete_row=first_incomplete_row, complete_rows=complete_rows)
        except Exception:
            return jsonify(final_out1='No words found', final_out2='', final_out3='', final_out4='', final_out5='',
                           final_out_end=NO_WORDS_END,
                           first_incomplete_row='First Incomplete Row: None', complete_rows='Complete Rows: []')
    else:
        # Call the solver with empty data to get initial recommendations
        empty_data = []
        final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end, first_incomplete_row, complete_rows = wordle.antiwordle_solver_split_revamp(df, empty_data)

        log_page_visit('antiwordle_revamp.html')
        return render_template("antiwordle_revamp.html",
                             initial_out1=final_out1,
                             initial_out2=final_out2,
                             initial_out3=final_out3,
                             initial_out4=final_out4,
                             initial_out5=final_out5,
                             initial_out_end=final_out_end,
                             schema_data=schema_data)


# The quordle solver returns a flat 30-tuple: five words plus a summary line
# for the combined view and for each of the four puzzles, in this order.
QUORDLE_RESULT_KEYS = []
for _p in ('_all', '1', '2', '3', '4'):
    QUORDLE_RESULT_KEYS += [f'final_out{_p}_{_i}' for _i in range(1, 6)]
    QUORDLE_RESULT_KEYS.append(f'final_out_end{_p}')


def quordle_no_words_payload():
    vals = []
    for _ in range(5):
        vals += ['No words found', '', '', '', '', NO_WORDS_END]
    return dict(zip(QUORDLE_RESULT_KEYS, vals))


@bp.route("/quordle", methods=["POST", "GET"])
@limiter.limit(INTERACTIVE_LIMITS)
def run_quordle_revamp():

    schema_data = make_schema_data(
        "Quordle Solver - Solve 4 Wordles at Once Instantly",
        "Free Quordle solver and strategy tool. Solve all 4 Wordle puzzles simultaneously with smart word suggestions. Beat daily Quordle and Sequence games easily.",
        "https://www.jamesapplewhite.com/quordle"
    )

    if request.method == "POST":
        try:
            body = request.get_json()
            mode = 'sequence' if body.get('mode') == 'sequence' else 'quordle'
            results = wordle.quordle_solver_split_revamp(df, body.get('quordle_data'), mode=mode)
            return jsonify(**dict(zip(QUORDLE_RESULT_KEYS, results)))
        except Exception:
            return jsonify(**quordle_no_words_payload())
    else:
        # Call the solver with empty data to get initial recommendations
        empty_data = []
        results = wordle.quordle_solver_split_revamp(df, empty_data)

        # Template expects the same values under initial_* names
        initial_context = {key.replace('final', 'initial', 1): value
                           for key, value in zip(QUORDLE_RESULT_KEYS, results)}

        log_page_visit('quordle.html')
        return render_template("quordle.html", schema_data=schema_data, **initial_context)


@bp.route("/fixer", methods=["POST", "GET"])
def run_wordle_fixer():
    if request.method == "POST":
        must_be_present = request.form["must_be_present"]
        final_out1, final_out2, final_out3, final_out4, final_out5 = wordle.find_word_with_letters(df, must_be_present)
        return render_template("fixer.html", final_out1=final_out1, final_out2=final_out2, final_out3=final_out3, final_out4=final_out4, final_out5=final_out5, must_be_present=must_be_present)
    else:
        return render_template("fixer.html")


@bp.route("/common_denominator", methods=["POST", "GET"])
def run_common_denominator():
    if request.method == "POST":
        # Parsed here (not just downstream) so junk input 400s instead of
        # 500ing on the int()/float() casts inside common_denominator.
        min_match_len = parse_int(request.form["min_match_len"], "min_match_len",
                                  default=3, min_value=1, max_value=100)
        min_match_rate = parse_float(request.form["min_match_rate"], "min_match_rate",
                                     default=0.5, min_value=0.0, max_value=1.0)
        beg_end_str_char = request.form["beg_end_str_char"]
        value_split_char = request.form["value_split_char"]
        user_match_entry = request.form["user_match_entry"]
        user_nope_match_entry = request.form["user_nope_match_entry"]
        final_match_list, final_out, num_words_entered, comparisons = all_words.common_denominator(min_match_len, min_match_rate, beg_end_str_char, value_split_char, user_match_entry, user_nope_match_entry)
        return render_template("common_denominator.html", min_match_len_val=min_match_len, min_match_rate_val=min_match_rate, beg_end_str_char_val=beg_end_str_char, value_split_char_val=value_split_char, \
            user_match_entry_val=user_match_entry, user_nope_match_entry_val=user_nope_match_entry, \
            final_match_list=final_match_list, final_out=final_out, num_words_entered=num_words_entered, comparisons=comparisons, \
            num_word_count="Number of entries submitted: ", num_run_count="Number of comparisons run: ", top="Top values(s): ", all="All values meeting min match rate: ")
    else:
        return render_template("common_denominator.html", min_match_len_val=3, min_match_rate_val=0.5, beg_end_str_char_val="|", value_split_char_val=",", \
            user_match_entry_val="Discectomy, Laminectomy, Foraminotomy, Corpectomy, Spinal (Lumbar) Fusion, Spinal Cord Stimulation", example=" (example set provided)")


ANY_WORD_SORTS = {'Max-Min', 'Min-Max', 'A-Z', 'Z-A', 'Random',
                  'Common', 'Uncommon', 'Score'}


@bp.route("/any_word", methods=["POST", "GET"])
@limiter.limit(INTERACTIVE_LIMITS)
def any_word():
    schema_data = make_schema_data(
        "Any Word Finder - Search Words by Pattern",
        "Free word finder. Search a large dictionary by starts-with, ends-with, "
        "contained text, wildcard pattern, letters, and length.",
        "https://www.jamesapplewhite.com/any_word",
    )

    if request.method == "POST":
        # Live-search endpoint (see any_word.html): a JSON body of filters in,
        # ranked matches out. get_json() yields a clean 415 for non-JSON bodies;
        # a JSON null / non-dict payload would 500 on .get, so guard the shape.
        data = request.get_json()
        if not isinstance(data, dict):
            data = {}

        def text_field(key, max_len=30):
            val = data.get(key, '')
            if not isinstance(val, str) or len(val) > max_len:
                raise ValidationError(
                    f"{key} must be a string of at most {max_len} characters")
            return val

        sort_order = text_field('sort_order', 10)
        if sort_order not in ANY_WORD_SORTS:
            sort_order = 'Max-Min'
        # Parsed defensively so junk input 400s instead of 500ing on int() casts.
        min_length = parse_int(data.get('min_length'), 'min_length',
                               default=1, min_value=1, max_value=100)
        max_length = parse_int(data.get('max_length'), 'max_length',
                               default=100, min_value=1, max_value=100)

        results, total = all_words.search_words(
            words,
            starts_with=text_field('starts_with'),
            ends_with=text_field('ends_with'),
            contains=text_field('contains'),
            contains_letters=text_field('contains_letters'),
            excludes_letters=text_field('excludes_letters'),
            pattern=text_field('pattern'),
            min_length=min_length, max_length=max_length,
            sort_order=sort_order, list_len=300, popularity=word_pop,
        )
        # Enrich each returned word with its Zipf popularity (0.0 = absent from
        # the frequency corpora, i.e. obscure) and Scrabble score. Only the
        # <=300 shown words are enriched, not every match.
        enriched = [
            {'w': w, 'p': round(word_pop.get(w, 0.0), 1),
             's': all_words.scrabble_score(w)}
            for w in results
        ]
        return jsonify(results=enriched, total=total)

    return render_template("any_word.html", schema_data=schema_data)


def parse_smush_word_list(data, key):
    """Validate an optional list of words (played / rejected) in the smush
    POST body."""
    values = data.get(key, [])
    if not (isinstance(values, list) and len(values) <= 500):
        raise ValidationError(f'{key} must be a list of at most 500 words')
    for w in values:
        if not (isinstance(w, str) and w.isalpha() and len(w) <= 15):
            raise ValidationError(f'{key} entries must be words of at most 15 letters')
    return values


def parse_solve(data):
    """A Smush solve's board and pile, from a /smush or /smush_dev POST:
    (center, outer_uses, spicy, first_word, rejected, played, want_plan), all
    lowercase."""
    center = data.get('center', '')
    if not (isinstance(center, str) and len(center) == 1 and 'a' <= center.lower() <= 'z'):
        raise ValidationError('center must be a single letter a-z')

    # A Smush board is always the center plus 8 distinct outer letters;
    # enforce that so the solver never scores a collapsed/partial board.
    outer_uses = data.get('outer_uses')
    if not (isinstance(outer_uses, dict) and len(outer_uses) == 8):
        raise ValidationError('outer_uses must contain exactly 8 letters')
    seen_letters = set()
    for letter, uses in outer_uses.items():
        if not (isinstance(letter, str) and len(letter) == 1 and 'a' <= letter.lower() <= 'z'):
            raise ValidationError('outer_uses keys must be single letters a-z')
        if not (isinstance(uses, int) and not isinstance(uses, bool) and 0 <= uses <= 5):
            raise ValidationError('outer_uses values must be whole numbers 0-5')
        seen_letters.add(letter.lower())
    if len(seen_letters) != 8 or center.lower() in seen_letters:
        raise ValidationError('outer_uses must be 8 distinct letters, none matching the center')

    spicy = data.get('spicy', '')
    if not (isinstance(spicy, str) and (spicy == '' or (len(spicy) == 1 and spicy.isalpha()))):
        raise ValidationError('spicy must be a single letter or empty')

    first_word = data.get('first_word', False)
    if not isinstance(first_word, bool):
        raise ValidationError('first_word must be a boolean')

    # rejected: words the game refused (cost nothing, hidden for good).
    # played: words already in the pile (a word can't be played twice).
    rejected = parse_smush_word_list(data, 'rejected')
    played = parse_smush_word_list(data, 'played')

    # plan: also compute a set of words that smushes the whole board flat.
    want_plan = data.get('plan', False)
    if not isinstance(want_plan, bool):
        raise ValidationError('plan must be a boolean')

    # A non-empty pile contradicts first_word; trust the pile so the
    # first-word pangram bonus can't be inflated.
    first_word = first_word and not played
    return (center.lower(), {letter.lower(): uses for letter, uses in outer_uses.items()},
            spicy.lower(), first_word, [w.lower() for w in rejected],
            [w.lower() for w in played], want_plan)


@bp.route("/smush", methods=["POST", "GET"])
@limiter.limit(INTERACTIVE_LIMITS)
def run_smush():
    """The page, a solve, or a JSON action. Today's board (routes/smush.py)
    shows while SMUSH_BOARD_LIVE."""
    show_board = smush_crowd.SMUSH_BOARD_LIVE
    schema_data = make_schema_data(
        "Smush Solver - Find Today's Pangram & Best Words",
        "Find today's Smush pangram in seconds - enter your nine letters and see every playable word ranked by points. Free solver for Hank Green's daily word game.",
        "https://www.jamesapplewhite.com/smush"
    )

    if request.method == "POST":
        data = json_object()

        # Logging the board and asking for today's, and the crowd votes: ✗ on
        # a result, tapping a hidden word back, and the "Smush accepted a word
        # that isn't listed?" box. Not a solve.
        if 'action' in data:
            if data.get('action') in smush_crowd.BOARD_ACTIONS:
                return smush_crowd.handle_board_action(data)
            return smush_crowd.handle_vote(data)

        center, outer_uses, spicy, first_word, rejected, played, want_plan = parse_solve(data)

        # Solve untruncated: the all-8 planner needs the cheap low-point words
        # that the ranked response cuts off.
        results, total_playable, pangram_status = all_words.smush_solver(
            center, outer_uses, spicy, first_word, get_smush_words(),
            list_len=None, exclude=rejected, played=played, popularity=word_pop)

        plan = None
        if want_plan:
            plan_words, leftover = all_words.smush_all_plan(results, outer_uses)
            plan = {'complete': not leftover, 'words': plan_words,
                    'leftover': leftover}

        return jsonify(results=results[:400], total_playable=total_playable,
                       pangram_status=pangram_status, plan=plan)
    else:
        log_page_visit('smush.html')
        # The Pacific date's board, for search engines and the first paint;
        # the page asks again for the player's own date if theirs differs.
        todays_board, board_date = smush_crowd.page_board(show_board)
        return render_template("smush.html", schema_data=schema_data,
                               show_board=show_board, todays_board=todays_board,
                               board_date=board_date)


@bp.route("/ribbit", methods=["POST", "GET"])
@limiter.limit(INTERACTIVE_LIMITS)
def run_ribbit():
    """The page, a solve, or an `action` (shared boards and crowd votes, in
    routes/ribbit.py)."""
    if request.method == "POST":
        data = json_object()

        if 'action' in data:
            return ribbit.handle_action(data)

        nodes, edges, disabled = parse_ribbit_board(data)

        # rejected: words Ribbit refused; played: words already found. Both
        # are simply dropped from suggestions - Ribbit has no per-letter
        # budget, so a found word costs nothing beyond not being suggested
        # again.
        rejected = parse_smush_word_list(data, 'rejected')
        played = parse_smush_word_list(data, 'played')

        min_len = parse_int(data.get('min_len'), 'min_len',
                            default=4, min_value=3, max_value=15)

        # Ribbit's own list: the dictionary with Ribbit's crowd corrections,
        # never Smush's or Blossom's - those are about their games' lists.
        results, total_playable = all_words.ribbit_solver(
            nodes, edges, ribbit.get_ribbit_words(), min_len=min_len,
            exclude=set(rejected) | set(played), disabled=disabled,
            popularity=word_pop, list_len=500)

        return jsonify(results=results, total_playable=total_playable)

    schema_data = make_schema_data(
        "Ribbit Solver - Today's Answers for Puzzmo & Yahoo Ribbit",
        "Solve Puzzmo's Ribbit in seconds - recreate the board, then see every word you can trace, longest first, with rare words flagged. Free solver for the daily frog word game.",
        "https://www.jamesapplewhite.com/ribbit"
    )
    log_page_visit('ribbit.html')
    return render_template("ribbit.html", schema_data=schema_data,
                           shared_boards=ribbit.SHARED_BOARDS_LIVE, post_url='/ribbit')


@bp.route("/guess_the_phrase", methods=["POST", "GET"])
@limiter.limit(INTERACTIVE_LIMITS)
def run_guess_the_phrase():

    schema_data = make_schema_data(
        "Guess The Phrase Solver - Hints for the Daily Phrase Puzzle",
        "Stuck on Bored Panda's Guess The Phrase? Enter the board and get spoiler-free help: the best letter to call next, whether to buy a vowel, and possible answers revealed one letter at a time. Works for today's puzzle and every past one.",
        "https://www.jamesapplewhite.com/guess_the_phrase"
    )

    if request.method == "POST":
        data = json_object()
        if 'action' in data:
            return gtp.handle_vote(data)
        return gtp.solve_request(data)
    else:
        log_page_visit('guess_the_phrase.html')
        return render_template("guess_the_phrase.html", schema_data=schema_data, **gtp.page_context())


@bp.route("/chainle", methods=["POST", "GET"])
@limiter.limit(INTERACTIVE_LIMITS)
def run_chainle():
    return _chainle_page(dev=False)


@bp.route("/chainle_dev", methods=["POST", "GET"])
@limiter.limit(INTERACTIVE_LIMITS)
@auth.login_required
def run_chainle_dev():
    """James's copy of /chainle, with today's rounds on even while
    CHAINLE_ROUNDS_LIVE is off: the page as it will look once they go live
    (James asked, 2026-10-28). It posts to itself and logs none of his
    rounds, so his own play stays out of the players' counts; his reports
    count like any player's, tagged dev in the vote log. Not a page visit."""
    return _chainle_page(dev=True)


def _chainle_page(dev):
    """/chainle, or /chainle_dev with `dev`: the page, a solve, or a JSON
    action. Today's rounds (routes/chainle.py) show wherever rounds_on is:
    always on /chainle_dev, and on /chainle once CHAINLE_ROUNDS_LIVE."""
    schema_data = None if dev else make_schema_data(
        "Chainle Answers & Hints - Solver for Today's Rounds",
        "Stuck on a Chainle round? Enter the clue words and get spoiler-free help: a hint first, then the words that link your clues, revealed one letter at a time. Works for bridge and closest-meaning rounds.",
        "https://www.jamesapplewhite.com/chainle"
    )

    if request.method == "POST":
        data = json_object()
        # A round to log or today's rounds to fetch, or a player's report of
        # a word the game refused: not a round to solve.
        if 'action' in data:
            if data.get('action') in chainle.ROUND_ACTIONS:
                return chainle.handle_rounds_action(data, dev=dev)
            return chainle.handle_vote(data, dev=dev)
        return chainle.solve_request(data)
    else:
        if not dev:
            log_page_visit('chainle.html')
        return render_template("chainle.html", schema_data=schema_data, dev=dev,
                               post_url='/chainle_dev' if dev else '/chainle',
                               rounds_on=dev or chainle.CHAINLE_ROUNDS_LIVE, **chainle.page_context())


@bp.route("/wordiply", methods=["POST", "GET"])
@limiter.limit(INTERACTIVE_LIMITS)
def run_wordiply():

    schema_data = make_schema_data(
        "Wordiply Solver - Today's Answers & Longest Words",
        "Free Wordiply solver and word finder. Find the longest words containing your letters for today's Wordiply puzzle. Smart algorithm finds optimal answers instantly.",
        "https://www.jamesapplewhite.com/wordiply"
    )

    if request.method == "POST":
        # get_json() raises a clean 415 for non-JSON bodies (via the app-level
        # HTTPException handling); a JSON `null` body or a non-dict payload
        # would 500 on .get, so guard the shape here too.
        data = request.get_json()
        search_string = data.get('search_string', '') if isinstance(data, dict) else ''
        if not isinstance(search_string, str) or len(search_string) > 100:
            raise ValidationError('search_string must be a string of at most 100 characters')

        results = all_words.wordiply_solver(search_string, words, 90)

        return jsonify(results=results)
    else:
        log_page_visit('wordiply.html')
        return render_template("wordiply.html", schema_data=schema_data)
