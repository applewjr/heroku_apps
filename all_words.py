import random

def unused_letters(must_have, may_have):
    """
    Coded in part by ChatGPT on 4/20/2023

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
    Coded in part by ChatGPT on 4/18/2023
    
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
    Coded in part by ChatGPT on 4/18/2023
    
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