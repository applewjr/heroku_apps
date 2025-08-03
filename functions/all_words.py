import random
import pandas as pd
from datetime import date
import math

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
    and supports load more functionality
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
            length_score(len(str(word))) + (str(word).lower().count(petal.lower()) * 5) + is_pangram_revamp(str(word), required_letters),
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
    
    # Determine if there are more words to show
    show_load_more = total_valid_words > list_len
    
    # Get only the words to display (up to list_len)
    top_df = df.head(list_len)
    
    # Add checkbox column with session-based checking
    checkbox_html = []
    for word in top_df['Word']:
        checked = 'checked' if word in used_words else ''
        checkbox_html.append(f'<input type="checkbox" class="word-checkbox" data-word="{word}" {checked}>')
    
    top_df.insert(0, 'Used', checkbox_html)
    
    # Convert to HTML with escape=False to render HTML checkboxes
    # blossom_table = top_df.to_html(index=False, columns=['Used', 'Word', 'Score', 'Pangram'], escape=False)
    blossom_table = top_df.to_html(
        index=False, 
        columns=['Used', 'Word', 'Score', 'Pangram'], 
        escape=False,
        classes='blossom-info',  # This adds your existing CSS class
        table_id='blossom-results-table'
    )

    # Return table, total count, and load more flag
    return blossom_table, total_valid_words, show_load_more

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