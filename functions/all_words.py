import random
import pandas as pd
from datetime import date
import math
import time

# common denominator
def common_denominator(min_match_len: int, min_match_rate: float, beg_end_str_char: str, value_split_char: str, user_match_entry: str, user_nope_match_entry: str):

    text_ymd = str(date.today().year) + '-' + str(date.today().month).zfill(2) + '-' + str(date.today().day).zfill(2)
    today = pd.to_datetime(date.today())

    # user_match_entry_preserve = user_match_entry.copy()
    min_match_len = int(min_match_len)
    min_match_rate = float(min_match_rate)


    # process user_match_entry
    var_list = user_match_entry.split(value_split_char)
    var_list = list(map(lambda x: x.strip(), var_list))
    for ind, val in enumerate(var_list):
        var_list[ind] = beg_end_str_char + var_list[ind] + beg_end_str_char
    var_list = list(map(lambda x: x.lower().strip(), var_list))
    # var_list

    # process user_no_match_entry
    var_nope_list = user_nope_match_entry.split(value_split_char)
    var_nope_list = list(map(lambda x: x.strip(), var_nope_list))
    for ind, val in enumerate(var_nope_list):
        var_nope_list[ind] = beg_end_str_char + var_nope_list[ind] + beg_end_str_char
    var_nope_list = list(map(lambda x: x.lower().strip(), var_nope_list))
    # var_nope_list

    # create the dict of all word segments
    # and a master list of all segments from all words
    var_dict = {}
    master_ref_list = set()

    for word in var_list:
        test_set = set()
        for start in range(len(word)):
            for end in range(1, len(word)+1):
                if start > end:
                    pass
                if len(word[start:end]) < min_match_len:
                    pass
                else:
                    test_set.add((word[start:end].strip()))
                    master_ref_list.add((word[start:end].strip()))
        var_dict[word] = test_set
    # master_ref_list

    # create the dict of all word segments - for nope list
    # and a master list of all segments from all words - for nope list
    var_nope_dict = {}
    master_nope_ref_list = set()

    for word in var_nope_list:
        test_set = set()
        for start in range(len(word)):
            for end in range(1, len(word)+1):
                if start > end:
                    pass
                if len(word[start:end]) < min_match_len:
                    pass
                else:
                    test_set.add((word[start:end].strip()))
                    master_nope_ref_list.add((word[start:end].strip()))
        var_nope_dict[word] = test_set
    # master_nope_ref_list

    triangle_dict = {}
    for ind, val in enumerate(var_list):
        triangle_dict[ind+1] = (ind)*(ind+1)//2
    # triangle_dict

    triangle_dict_reverse = {}
    for ind, val in enumerate(var_list):
        triangle_dict_reverse[(ind)*(ind+1)//2] = ind+1
    # triangle_dict_reverse

    min_match_number = triangle_dict[math.ceil(len(var_list)*min_match_rate)]
    # min_match_number


    # # pass 3: everything compared against the previous
    # # this should make the final full dict ranking better

    final_match_dict = {}
    for i in master_ref_list:
        final_match_dict[i] = 0
    x = 0

    for first_ind, first_val in enumerate(var_list):
        for second_ind, second_val in enumerate(var_list):
            if first_ind <= second_ind:
                pass
            else:
                for first in var_dict[var_list[first_ind]]:
                    for second in var_dict[var_list[second_ind]]:
                        x+=1
                        if first == second:
                            try:
                                final_match_dict[first] += 1
                            except:
                                pass
    comparisons = f'{x:,}'

    final_match_list = []
    for key, value in final_match_dict.items():
        # if value == (len(var_list)-1)*len((var_list))//2:
        if value >= min_match_number:
            final_match_list.append(key)

    # print(x)

    final_match_list = sorted(final_match_list, key=len, reverse=True)

    # remove values found in the nope list
    final_match_list_temp = list(final_match_list)
    for val in final_match_list_temp:
        for nope_val in master_nope_ref_list:
            if val == nope_val:
                try:
                    final_match_list.remove(val)
                except:
                    pass
    # remove values found in the nope list
    final_match_dict_temp = final_match_dict.copy()
    for val in final_match_dict_temp:
        for nope_val in master_nope_ref_list:
            if val == nope_val:
                try:
                    del final_match_dict[val]
                except:
                    pass

    # final_match_list

    # cut out the smaller findings when there is a bigger chunk of text found
    final_match_list_temp = final_match_list.copy()
    x = 0
    for pri in final_match_list_temp:
        for sec in final_match_list_temp:
            if pri.find(sec) > -1 and pri != sec:
                try:
                    final_match_list.remove(sec)
                except:
                    x += 1

    # final_match_list

    # first main return
    # list of string parts that
        # have a match rate greater than the set minimum
        # are not part of a larger, also included string (e.g. if 'dog' is already included, 'og' will be excluded)
    # a bigger word part will however knock off a smaller word part with a higher match rate


    final_out = dict()
    for (key, value) in final_match_dict.items():
        if value >= min_match_number:
            final_out[key] = round(triangle_dict_reverse[value]/len(var_list),4)

    final_out # maybe this one will be better to return in app?

    # final_out = pd.DataFrame.from_dict(final_out, orient='index')
    # final_out = final_out.rename(columns={0:'Match Rate'})
    # final_out = final_out.sort_values(by=['Match Rate'], ascending=False)
    # final_out = final_out.to_html

    # final_out

    # second main return
    # dictionary/df with all valid string parts and how often they match

    num_words_entered = int(len(var_list))

    return final_match_list, final_out, num_words_entered, comparisons
    # return final_match_list, final_out, num_words_entered, user_match_entry

def unused_letters(must_have, may_have):
    """
    Returns a list of letters that were not called out.
    
    Args:
    called_out (list): A list of letters that were called out.
    
    Returns:
    unused (list): A list of letters that were not called out.
    """
    called_out = must_have + may_have
    called_out = [char.lower() for char in called_out]

    # letters = list('abcdefghijklmnopqrstuvwxyz')
    letters = 'abcdefghijklmnopqrstuvwxyz'
    unused = []
    for letter in letters:
        if letter not in called_out:
            unused.append(letter)

    return [''.join(unused)]

def filter_words_blossom(required_letters, forbidden_letters, list_len, words):
    """
    Filter a list of words by required and forbidden letters, and an optional first letter.

    Args:
        words (list): A list of words to filter.
        required_letters (list): A list of letters that must be present in the words.
        forbidden_letters (list): A list of letters that must not be present in the words.
        first_letter (str): An optional letter that must be the first letter of the words.
        sort_order (str): The sorting order of the output. Possible values are 'a-z', 'z-a', 'min-max', and 'max-min'.

    Returns:
        list: A list of valid words that contain all the required letters, none of the forbidden letters, and have the optional first letter (if specified), sorted according to the specified sorting order.
    """
    # words = get_english_words_set(['web2'], lower=True)

    required_letters = [char.lower() for char in required_letters]
    forbidden_letters = [char.lower() for char in forbidden_letters]
    # required_letters = list(required_letters[0])
    # forbidden_letters = list(forbidden_letters[0])
    list_len = int(list_len)

    valid_words = []
    for word in words:
        word = str(word)
        # word = word.lower()
        if all(letter in word for letter in required_letters[0]) and all(letter not in word for letter in forbidden_letters[0]):
            # if first_letter is None or word.startswith(first_letter):
            valid_words.append(word)

    valid_words.sort(key=len, reverse=True)

    return valid_words[:list_len]

def filter_words_all(required_letters, forbidden_letters, first_letter, sort_order, list_len, words, min_length, max_length):
    """
    Filter a list of words by required and forbidden letters, and an optional first letter.

    Args:
        words (list): A list of words to filter.
        required_letters (list): A list of letters that must be present in the words.
        forbidden_letters (list): A list of letters that must not be present in the words.
        first_letter (str): An optional letter that must be the first letter of the words.
        sort_order (str): The sorting order of the output. Possible values are 'a-z', 'z-a', 'min-max', and 'max-min'.
        min_length (int): The minimum length of the words to return.
        max_length (int): The maximum length of the words to return.
                
    Returns:
        list: A list of valid words that contain all the required letters, none of the forbidden letters, and have the optional first letter (if specified), sorted according to the specified sorting order.
    """

    required_letters = [char.lower() for char in required_letters]
    forbidden_letters = [char.lower() for char in forbidden_letters]
    first_letter = first_letter.lower()
    min_length = int(min_length)
    max_length= int(max_length)
    # words = get_english_words_set(['web2'], lower=True)
    # words = words
    # required_letters = list(required_letters[0])
    # try:
    #     forbidden_letters = list(forbidden_letters[0])
    # except:
    #     forbidden_letters = forbidden_letters
    list_len = int(list_len)

    valid_words = []
    for word in words:
        word = str(word)
        if all(letter in word for letter in required_letters) and all(letter not in word for letter in forbidden_letters):
            if (first_letter is None or word.startswith(first_letter)) and \
                    (min_length is None or len(word) >= min_length) and \
                    (max_length is None or len(word) <= max_length):
                valid_words.append(word)

    if sort_order == 'A-Z':
        valid_words.sort()
    elif sort_order == 'Z-A':
        valid_words.sort(reverse=True)
    elif sort_order == 'Min-Max':
        valid_words.sort(key=len)
    elif sort_order == 'Max-Min':
        valid_words.sort(key=len, reverse=True)
    elif sort_order == 'Random':
        random.shuffle(valid_words)

    return valid_words[:list_len]

def unused_letters_revamp(must_have, may_have, petal):
    called_out = (must_have + may_have + petal).lower()
    unused = set('abcdefghijklmnopqrstuvwxyz') - set(called_out)
    return [''.join(unused)]

def is_pangram_revamp(word, required_letters):
    return 7 if set(word.lower()) >= required_letters else 0

def length_score(word_length):
    if word_length == 4:
        return 2
    elif word_length == 5:
        return 4
    elif word_length == 6:
        return 6
    elif word_length == 7:
        return 12
    else:
        return 12 + (word_length - 7) * 3  # 3 points per letter beyond 7

def filter_words_blossom_revamp(must_have, may_have, petal, list_len, words, used_words=None):
    """
    Enhanced version that includes checkbox column for session-based word tracking
    and supports load more functionality with hyperlinked words
    """
    if used_words is None:
        used_words = []
        
    required_letters = set((must_have + may_have + petal).lower())
    forbidden_letters = set(unused_letters_revamp(must_have, may_have, petal)[0])
    must_have_set = set(must_have.lower())
        
    # Create a list of tuples with word, score, and pangram status
    valid_words_scores = [
        (
            str(word),
            length_score(len(str(word))) + (str(word).lower().count(petal.lower()) * 5 if petal else 0) + is_pangram_revamp(str(word), required_letters),
            'Yes' if is_pangram_revamp(str(word), required_letters) > 0 else 'No'
        )
        for word in words
        if must_have_set.issubset(str(word).lower()) 
        and not set(str(word).lower()) & forbidden_letters 
        and len(str(word)) >= 4
    ]

    # Create DataFrame with 'Word', 'Score', and 'Pangram' columns
    df = pd.DataFrame(valid_words_scores, columns=['Word', 'Score', 'Pangram'])
    df.sort_values(by='Score', ascending=False, inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Get total count of all valid words
    total_valid_words = len(df)

    # Extract all pangrams before truncation
    pangrams = df[df['Pangram'] == 'Yes']['Word'].tolist()

    # Determine if there are more words to show
    show_load_more = total_valid_words > list_len
    
    # Get only the words to display (up to list_len)
    top_df = df.head(list_len).copy()
    
    # Add checkbox column with session-based checking
    checkbox_html = []
    for word in top_df['Word']:
        checked = 'checked' if word in used_words else ''
        checkbox_html.append(f'<input type="checkbox" class="word-checkbox" data-word="{word}" {checked}>')
    
    top_df.insert(0, 'Used', checkbox_html)
    
    # Convert words to hyperlinks that open in new tab
    word_links = []
    for word in top_df['Word']:
        word_link = f'<a href="https://www.merriam-webster.com/dictionary/{word}" target="_blank" rel="noopener noreferrer" style="color: #0066cc; text-decoration: none;" onmouseover="this.style.textDecoration=\'underline\'; this.style.color=\'#0052a3\'" onmouseout="this.style.textDecoration=\'none\'; this.style.color=\'#0066cc\'">{word}</a>'
        word_links.append(word_link)
    
    # Replace the Word column with the hyperlinked version
    top_df['Word'] = word_links
    top_df.rename(columns={'Word': 'Word', 'Pangram': '🌸'}, inplace=True)

    # Convert to HTML with escape=False to render HTML checkboxes and links
    blossom_table = top_df.to_html(index=False, columns=['Used', 'Word', 'Score', '🌸'], escape=False, classes='blossom-results').replace(' border="1"', '')

    # Style pangram rows and replace Yes/No with visual indicator
    rows = blossom_table.split('</tr>')
    processed_rows = []
    for row in rows:
        if '<td>Yes</td>' in row:
            row = row.replace('<tr>', '<tr class="pangram-row">')
            row = row.replace('<td>Yes</td>', '<td>🌸</td>')
        else:
            row = row.replace('<td>No</td>', '<td></td>')
        processed_rows.append(row)
    blossom_table = '</tr>'.join(processed_rows)

    # Return table, total count, and load more flag
    return blossom_table, total_valid_words, show_load_more, pangrams

def filter_words_for_blossom(words):
    """
    Convert raw word list to blossom-optimized word list
    Removes words that are impossible in any blossom puzzle
    """
    filtered_words = []
    removed_count = 0
    
    for word in words:
        word_str = str(word).lower()
        word_len = len(word_str)
        unique_letters = len(set(word_str))
        
        # Keep words that could be valid in ANY blossom puzzle
        if (word_len >= 4 and                    # Minimum length
            word_str.isalpha() and               # Only letters
            unique_letters <= 7):                # Max 7 unique letters (bulletproof filter)
            filtered_words.append(word_str)
        else:
            removed_count += 1
    
    return set(filtered_words)

def wordiply_solver(search_string, words, list_len=15):

    search_string = search_string.lower().strip()
    
    # Return empty list if no search string
    if not search_string:
        return []
    
    valid_words = []
    
    for word in words:
        word_str = str(word).lower()
        # Check if the search string appears in the word
        if search_string in word_str:
            valid_words.append(word_str)
    
    # Sort by length (longest first), then alphabetically for ties
    valid_words.sort(key=lambda x: (-len(x), x))
    
    # Return top results
    return valid_words[:list_len]

# Letter values used by Smush (hankgreen.com/smush) - Scrabble-style points.
SMUSH_LETTER_VALUES = {
    'a': 1, 'b': 3, 'c': 3, 'd': 2, 'e': 1, 'f': 4, 'g': 2, 'h': 4,
    'i': 1, 'j': 8, 'k': 5, 'l': 1, 'm': 3, 'n': 1, 'o': 1, 'p': 3,
    'q': 10, 'r': 1, 's': 1, 't': 1, 'u': 1, 'v': 4, 'w': 4, 'x': 8,
    'y': 4, 'z': 10,
}


def smush_word_score(word):
    """Base Smush score: sum of letter values over every letter (center
    letter uses count toward the score even though they cost nothing)."""
    return sum(SMUSH_LETTER_VALUES.get(ch, 1) for ch in word)


def smush_solver(center, outer_uses, spicy, first_word, words, list_len=400,
                 exclude=None, played=None, popularity=None):
    """Rank every playable Smush word for the current board state.

    Smush rules (verified against the game's source): a word must contain the
    gold center letter, may only use the 9 board letters, and each non-center
    letter has limited uses (5 at game start, spent per occurrence; the center
    is free). Bonuses ADD to a single multiplier (they don't compound):
    +1 per use of the spicy letter, +1 per letter smushed flat (its last
    remaining uses spent), +2 for a pangram (a word using all 9 letters),
    +4 instead if the pangram is the first word played.

    center      -- the gold center letter (lowercase str)
    outer_uses  -- {letter: remaining uses 0..5} for the 8 outer tiles
    spicy       -- the currently spicy outer letter, or '' if unknown
    first_word  -- True if nothing has been played yet (pangram x5 vs x3)
    list_len    -- max results to return; None returns every playable word
    exclude     -- words the game refused; dropped entirely, so they don't
                   count toward totals or pangram reachability
    played      -- words already played; a word can't be played twice, so
                   these are dropped from the results too
    popularity  -- optional {word: Zipf score}; attached to each result as
                   'pop' (0.0 when unknown) so callers can judge how likely
                   Smush is to accept the word

    Returns (results, total_playable, pangram_status) where results is a list
    of dicts sorted by points desc and pangram_status is one of 'found' (a
    played word was the pangram), 'affordable', 'out_of_reach' (a pangram
    exists but uses are too depleted), or 'none' (the word list has no
    pangram for these letters).
    """
    center = str(center).lower()
    outer_uses = {str(l).lower(): u for l, u in outer_uses.items()
                  if str(l).lower() != center}
    board_letters = set(outer_uses) | {center}
    exclude = {str(w).lower() for w in exclude} if exclude else set()
    played = {str(w).lower() for w in played} if played else set()
    exclude |= played

    results = []
    pangram_exists = False
    pangram_affordable = False

    for word in words:
        w = str(word).lower()
        if not (3 <= len(w) <= 15) or center not in w or w in exclude:
            continue
        letters = set(w)
        if not letters <= board_letters:
            continue

        cost = {l: w.count(l) for l in letters if l != center}
        is_pangram = letters == board_letters
        if is_pangram:
            pangram_exists = True
        if any(n > outer_uses[l] for l, n in cost.items()):
            continue
        if is_pangram:
            pangram_affordable = True

        smushes = sum(1 for l, n in cost.items()
                      if outer_uses[l] > 0 and n == outer_uses[l])
        spicy_uses = cost.get(spicy, 0) if spicy else 0
        pangram_bonus = (4 if first_word else 2) if is_pangram else 0
        mult = 1 + spicy_uses + smushes + pangram_bonus
        base = smush_word_score(w)

        results.append({
            'word': w,
            'base': base,
            'mult': mult,
            'pts': base * mult,
            'spicy_uses': spicy_uses,
            'smushes': smushes,
            'pangram': is_pangram,
            'cost': cost,
            'pop': popularity.get(w, 0.0) if popularity else 0.0,
        })

    results.sort(key=lambda r: (-r['pts'], -len(r['word']), r['word']))
    total_playable = len(results)

    if any(set(w) == board_letters for w in played):
        pangram_status = 'found'
    elif pangram_affordable:
        pangram_status = 'affordable'
    elif pangram_exists:
        pangram_status = 'out_of_reach'
    else:
        pangram_status = 'none'

    return results[:list_len], total_playable, pangram_status


def smush_all_plan(results, outer_uses, run_budget=1500, time_limit=1.5,
                   pop_tiers=(3.3, 2.7, 2.0)):
    """Plan a set of currently playable words that spends EVERY remaining use
    of every outer letter, smushing the whole board flat.

    results     -- untruncated smush_solver output for the current state; the
                   plan draws its words (cost / points / popularity) from
                   here, so rejected and played words are already excluded
    outer_uses  -- {letter: remaining uses 0..5} for the 8 outer tiles
    pop_tiers   -- descending Zipf popularity floors to try before allowing
                   the whole dictionary in

    A plan only ever constrains letter TOTALS: a complete plan's per-letter
    costs sum exactly to the remaining uses, and a partial plan's never
    exceed them. Totals bound every prefix, so the words can be played in
    any order without a letter running out early.

    Smush's real word list is stricter than ours, and one refused word breaks
    an all-8 run, so the plan's weakest word decides its odds. The search
    therefore maximins popularity: it first tries to complete a plan using
    only words at or above the highest Zipf floor, relaxing tier by tier, and
    only opens the full dictionary when no popular-only plan exists. Pangrams
    are exempt from the floor (every board has an authored pangram, and the
    pangram-first PERFECT bonus doubles the final score), and within a cost
    signature the most popular word is always chosen first.

    Each attempt is a multi-dimensional subset-sum solved by depth-first
    search over remaining-uses vectors: words are grouped by cost signature,
    the search branches on the scarcest unfinished letter, dead states are
    memoized, and each run stops after run_budget nodes or when time_limit
    seconds elapse overall. In the final unrestricted attempt, if the board
    can't be fully flattened the exact requirement is relaxed one letter at
    a time - scarcest first - and a greedy pass spends what it still can.

    Returns (plan, leftover): plan is a list of result dicts and leftover
    maps each letter the plan fails to flatten to its stranded uses ({}
    means a complete smush). The plan is ordered for play: pangram first
    (PERFECT needs it as the game's first word), then least popular to most
    popular, so any refusal lands while the board still has enough letters
    left to re-plan around.
    """
    norm = {str(l).lower(): int(n) for l, n in outer_uses.items()}
    letters = sorted(norm)
    idx = {l: i for i, l in enumerate(letters)}
    n_letters = len(letters)
    start = tuple(norm[l] for l in letters)
    deadline = time.monotonic() + time_limit

    tagged = []
    for r in results:
        sig = [0] * n_letters
        for l, n in r['cost'].items():
            sig[idx[l]] = n
        sig = tuple(sig)
        if any(sig):
            tagged.append((sig, r))

    def solve(cands, exact_only):
        """Try to plan from one candidate set. exact_only: return
        (plan, {}) only for a fully flat board, else None. Otherwise run
        the relax chain plus greedy top-up and always return (plan, leftover).
        """
        # Words with the same cost signature are interchangeable for the
        # search; grouping them collapses the branching factor. The most
        # popular member fronts each group so plans favor accepted words.
        groups = {}
        pangram_sigs = []
        for sig, r in cands:
            groups.setdefault(sig, []).append(r)
            if r['pangram'] and sig not in pangram_sigs:
                pangram_sigs.append(sig)
        for members in groups.values():
            members.sort(key=lambda r: (-r.get('pop', 0.0), -r['pts']))
        sigs = list(groups)
        by_letter = [[s for s in sigs if s[i]] for i in range(n_letters)]
        supply = [sum(s[i] * len(groups[s]) for s in by_letter[i])
                  for i in range(n_letters)]

        used = {s: 0 for s in sigs}
        path = []

        def reset():
            for s in used:
                used[s] = 0
            del path[:]

        def dfs(state, req, failed, budget):
            if all(state[i] == 0 for i in req):
                return True
            if state in failed:
                return False
            budget[0] -= 1
            if budget[0] < 0 or time.monotonic() > deadline:
                budget[0] = -1
                return False
            pivot = min((i for i in req if state[i]),
                        key=lambda i: len(by_letter[i]))
            options = [s for s in by_letter[pivot]
                       if used[s] < len(groups[s])
                       and all(c <= n for c, n in zip(s, state))]
            options.sort(key=lambda s: (-s[pivot], -sum(s)))
            for s in options:
                used[s] += 1
                path.append(s)
                if dfs(tuple(n - c for n, c in zip(state, s)), req, failed, budget):
                    return True
                path.pop()
                used[s] -= 1
            if budget[0] >= 0:
                # Keyed on the remaining-uses vector alone: two paths reaching
                # the same vector may have consumed different word supplies, so
                # this can rarely prune a solvable state - acceptable under a
                # budget.
                failed.add(state)
            return False

        unfinished = {i for i in range(n_letters) if start[i]}
        # A letter whose entire word supply can't cover its remaining uses can
        # never be flattened by this candidate set.
        coverable = {i for i in unfinished if supply[i] >= start[i]}
        if exact_only and coverable != unfinished:
            return None

        # Pangram-first attempts: consume a pangram up front and solve the
        # rest exactly. Kept only when fully flat, so completeness never
        # regresses and the clean plate is never traded for the pangram.
        solved = False
        for psig in pangram_sigs[:3]:
            if any(c > n for c, n in zip(psig, start)):
                continue
            state0 = tuple(n - c for n, c in zip(start, psig))
            req0 = {i for i in range(n_letters) if state0[i]}
            # the seeded word is gone from the supply; bail early if a
            # letter can no longer be covered
            if any(supply[i] - psig[i] < state0[i] for i in req0):
                continue
            reset()
            used[psig] = 1
            path.append(psig)
            if dfs(state0, req0, set(), [run_budget]):
                solved = True
                break

        if not solved and exact_only:
            reset()
            if not dfs(start, unfinished, set(), [run_budget]):
                return None
            solved = True

        if not solved:
            req = set(coverable)
            while True:
                reset()
                if dfs(start, req, set(), [run_budget]) or not req:
                    break
                req.remove(min(req, key=lambda i: (supply[i] - start[i],
                                                   len(by_letter[i]))))

        state = list(start)
        for s in path:
            for i, c in enumerate(s):
                state[i] -= c

        if not exact_only:
            # The exact search stops once the required letters are flat;
            # greedily keep spending whatever the relaxed letters have left,
            # flattening when we can.
            while True:
                fits = [s for s in sigs
                        if used[s] < len(groups[s])
                        and all(c <= n for c, n in zip(s, state))]
                if not fits:
                    break
                best = max(fits, key=lambda s: (
                    sum(1 for c, n in zip(s, state) if c and c == n), sum(s)))
                used[best] += 1
                path.append(best)
                for i, c in enumerate(best):
                    state[i] -= c

        take = {}
        plan = []
        for s in path:
            plan.append(groups[s][take.get(s, 0)])
            take[s] = take.get(s, 0) + 1
        # Pangram up front (play it first for PERFECT), then least popular
        # first: a refusal early leaves letters to re-plan around, while a
        # refusal on a nearly-flat board can make all 8 impossible.
        plan.sort(key=lambda r: (not r['pangram'], r.get('pop', 0.0), -r['pts']))
        leftover = {letters[i]: state[i]
                    for i in range(n_letters) if state[i] > 0}
        return plan, leftover

    # Popularity floors first: tiers are nested (thresholds descend), so an
    # unchanged candidate count means an identical set already tried.
    tried_sizes = set()
    for floor in pop_tiers:
        if time.monotonic() > deadline:
            break
        cands = [(s, r) for s, r in tagged
                 if r.get('pop', 0.0) >= floor or r['pangram']]
        if not cands or len(cands) in tried_sizes or len(cands) == len(tagged):
            continue
        tried_sizes.add(len(cands))
        found = solve(cands, exact_only=True)
        if found is not None:
            return found

    return solve(tagged, exact_only=False)
