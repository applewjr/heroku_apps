"""Wordle / Antiwordle / Quordle / misc word-solver routes."""

import re

from flask import Blueprint, jsonify, render_template, request

from data import df, words
from extensions import add_data_to_stream
from functions import all_words, wordle
from helpers import make_schema_data

bp = Blueprint('wordgames', __name__)

NO_WORDS_END = 'Options remaining: 0/5710 (0.0%)'


def abbreviate_keys(data):
    key_map = {'letter': 'l', 'position': 'p', 'color': 'c', 'row': 'r'}
    abbreviated_data = [{key_map[key]: value for key, value in d.items()} for d in data]
    return abbreviated_data


@bp.route("/wordle", methods=["POST", "GET"])
def run_wordle_revamp():

    schema_data = make_schema_data(
        "Wordle Solver - Beat Daily Wordle Puzzle Instantly",
        "Free Wordle solver and strategy tool. Get the best word suggestions to beat today's NYT Wordle puzzle. Smart algorithm finds optimal guesses instantly.",
        "https://jamesapplewhite.com/wordle"
    )

    if request.method == "POST":
        try:
            wordle_data_dict = request.get_json().get('wordle_data')
            final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end, first_incomplete_row, complete_rows, gray_letters, guessed_word_set = wordle.wordle_solver_split_revamp(df, wordle_data_dict)
            is_final_guess = first_incomplete_row == 6
            row_num = first_incomplete_row  # numeric, before reassignment
            first_incomplete_row = 'First Incomplete Row: ' + str(first_incomplete_row)
            complete_rows = 'Complete Rows: ' + str(complete_rows)

            try:
                wordle_data_dict_abbr = abbreviate_keys(wordle_data_dict)
                add_data_to_stream('wordle_logging', wordle_data_dict_abbr)
            except Exception:
                print('wordle_logging_failed')

            if is_final_guess:
                show_alt_picks, alt_out1, alt_out2, alt_out3, alt_out4, alt_out5 = False, '', '', '', '', ''
            else:
                _remaining_m = re.search(r'(\d+)/', final_out_end)
                _remaining_count = int(_remaining_m.group(1)) if _remaining_m else 9999
                _guesses_left = 6 - (row_num or 1)
                _min_match = 2 if _guesses_left <= 1 else (3 if _guesses_left <= 2 else 4)
                _force = _remaining_count > _guesses_left * 4 and _guesses_left <= 3
                show_alt_picks, alt_out1, alt_out2, alt_out3, alt_out4, alt_out5 = wordle.compute_alt_picks(
                    df, [final_out1, final_out2, final_out3, final_out4, final_out5], gray_letters, guessed_word_set,
                    min_match=_min_match, force=_force
                )
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
        final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end, first_incomplete_row, complete_rows, gray_letters, guessed_word_set = wordle.wordle_solver_split_revamp(df, empty_data)
        show_alt_picks, alt_out1, alt_out2, alt_out3, alt_out4, alt_out5 = wordle.compute_alt_picks(
            df, [final_out1, final_out2, final_out3, final_out4, final_out5], gray_letters, guessed_word_set
        )

        # Pass the results to JavaScript on page load
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
def run_antiwordle_revamp():

    schema_data = make_schema_data(
        "Antiwordle Solver - Beat Antiwordle Game Instantly",
        "Free Antiwordle solver and strategy tool. Get the best word suggestions to beat Antiwordle game. Smart algorithm finds optimal moves instantly.",
        "https://jamesapplewhite.com/antiwordle"
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
def run_quordle_revamp():

    schema_data = make_schema_data(
        "Quordle Solver - Solve 4 Wordles at Once Instantly",
        "Free Quordle solver and strategy tool. Solve all 4 Wordle puzzles simultaneously with smart word suggestions. Beat daily Quordle and Sequence games easily.",
        "https://jamesapplewhite.com/quordle"
    )

    if request.method == "POST":
        try:
            quordle_data_dict = request.get_json().get('quordle_data')
            results = wordle.quordle_solver_split_revamp(df, quordle_data_dict)
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
        min_match_len = request.form["min_match_len"]
        min_match_rate = request.form["min_match_rate"]
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


@bp.route("/any_word", methods=["POST", "GET"])
def any_word():
    if request.method == "POST":
        must_have = request.form["must_have"]
        must_not_have = request.form["must_not_have"]
        first_letter = request.form["first_letter"]
        sort_order = request.form["sort_order"]
        list_len = request.form["list_len"]
        min_length = request.form["min_length"]
        max_length = request.form["max_length"]
        list_out = all_words.filter_words_all(must_have, must_not_have, first_letter, sort_order, list_len, words, min_length, max_length)
        return render_template("any_word.html", list_out=list_out, \
            must_have_val=must_have, must_not_have_val=must_not_have, first_letter_val=first_letter, sort_order_val=sort_order, list_len_val=list_len, \
            min_length_val=min_length, max_length_val=max_length)
    else:
        return render_template("any_word.html", sort_order_val='Max-Min', list_len_val=10, min_length_val=1, max_length_val=100)


@bp.route("/wordiply", methods=["POST", "GET"])
def run_wordiply():

    schema_data = make_schema_data(
        "Wordiply Solver - Find the Longest Words Instantly",
        "Free Wordiply solver and word finder. Find the longest words containing your letters for today's Wordiply puzzle. Smart algorithm finds optimal answers instantly.",
        "https://jamesapplewhite.com/wordiply"
    )

    if request.method == "POST":
        data = request.get_json()
        search_string = data.get('search_string', '')

        results = all_words.wordiply_solver(search_string, words, 90)

        return jsonify(results=results)
    else:
        return render_template("wordiply.html", schema_data=schema_data)
