import pandas as pd

# Wordle solver function
def wordle_solver_split(import_df, must_not_be_present: str, 
    present1: str, present2: str, present3: str, present4: str, present5: str,
    not_present1: str, not_present2: str, not_present3: str, not_present4: str, not_present5: str):

    must_not_be_present = must_not_be_present.lower()
    present1 = present1.lower()
    present2 = present2.lower()
    present3 = present3.lower()
    present4 = present4.lower()
    present5 = present5.lower()
    not_present1 = not_present1.lower()
    not_present2 = not_present2.lower()
    not_present3 = not_present3.lower()
    not_present4 = not_present4.lower()
    not_present5 = not_present5.lower()

    # final_out2 = must_not_be_present + present1 + present2 + present3 + present4 + present5 + \
    #     not_present1 + not_present2 + not_present3 + not_present4 + not_present5

    # split individual letters into lists
    must_not_be_present = list(must_not_be_present)
    present = [present1, present2, present3, present4, present5]
    not_present = [not_present1, not_present2, not_present3, not_present4, not_present5]
    must_be_present = (''.join(not_present))

    places = ['one', 'two', 'three', 'four', 'five']
    # df = pd.read_excel(import_df)
    df = import_df.copy()
    total_len = len(df)

    # process the 'must be present' letters
    for j in must_be_present:
        drop_list = []
        for i in range(len(df)):
            drop_list.append(df['word'][i].find(j))
        df['drop_no_' + j] = drop_list
    for j in must_be_present:
        df = df[df['drop_no_' + j] != -1]

    # process the 'must not be present' letters
    for i in places:
        for j in must_not_be_present:
            df = df[df[i] != j]

    # process the 'specific values must be present' letters
    for i, v in enumerate(places):
        if present[i] != '':
            df = df[df[v] == present[i]]

    # process the 'specific values not must be present' letters
    for j, k in enumerate(places):
        if len(not_present[j]) > 0:
            for i in not_present[j]:
                df = df[df[k] != (','.join(i))]

    # pick the best (aka reasonably good) choice by sorting on the highest 'word_score'
    df = df.sort_values(by = 'word_score', ascending = False)

    try:
        final_out1 = 'Pick 1: ' + df.iat[0, 0] # print top 5 in case you get trapped in a narrow path of replacing just 1 letter at a time
    except:
        final_out1 = 'No words found'
    try:
        final_out2 = 'Pick 2: ' + df.iat[1, 0] # print top 5 in case you get trapped in a narrow path of replacing just 1 letter at a time
    except:
        final_out2 = ''
    try:
        final_out3 = 'Pick 3: ' + df.iat[2, 0] # print top 5 in case you get trapped in a narrow path of replacing just 1 letter at a time
    except:
        final_out3 = ''
    try:
        final_out4 = 'Pick 4: ' + df.iat[3, 0] # print top 5 in case you get trapped in a narrow path of replacing just 1 letter at a time
    except:
        final_out4 = ''
    try:
        final_out5 = 'Pick 5: ' + df.iat[4, 0] # print top 5 in case you get trapped in a narrow path of replacing just 1 letter at a time
    except:
        final_out5 = ''
    final_out_end = f'Options remaining: {len(df)}/{total_len} ({round(len(df)/total_len*100,2)}%)'

    return final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end


def wordle_solver_split_revamp(import_df, wordle_data_dict):

    row_letters = {}
    for entry in wordle_data_dict:
        if entry['letter']:  # Check if 'letter' is not empty
            row_letters[entry['row']] = row_letters.get(entry['row'], 0) + 1
    complete_rows = [row for row, count in row_letters.items() if count == 5]
    complete_rows = [int(row) for row in complete_rows]
    # print(complete_rows) # possibly use to make a visual indicator of rows that are considered to be complete

    first_incomplete_row = None
    if complete_rows:
        for i in range(1, max(complete_rows) + 2):  # Check up to one row beyond the last complete row
            if i not in complete_rows:
                first_incomplete_row = i
                break  # Exit the loop once the first incomplete row is found
    else:
        first_incomplete_row = 1
    if first_incomplete_row >= 7:
        first_incomplete_row = None
    # print(first_incomplete_row) # use as a marker of where to populate the chosen word

    must_not_be_present = set()
    present = {i: set() for i in range(1, 6)}  # Use sets for present letters by position
    not_present = {i: set() for i in range(1, 6)}  # Use sets for not present letters by position

    # Populate the sets based on conditions
    for entry in wordle_data_dict:
        letter = entry['letter'].lower()  # Ensure consistent casing
        position = int(entry['position'])
        color = entry['color']
        
        # Letters that must not be present anywhere
        if color == '1' and letter: # grey
            must_not_be_present.add(letter)
        # Letters present in specific positions
        elif color == '3' and letter: # green
            present[position].add(letter)
        # Letters that are not present in specific positions but are in the word
        elif color == '2' and letter: # yellow
            not_present[position].add(letter)

    # Convert the sets back to strings without duplicates
    must_not_be_present = ''.join(sorted(must_not_be_present))
    present1, present2, present3, present4, present5 = (''.join(sorted(present[i])) for i in range(1, 6))
    not_present1, not_present2, not_present3, not_present4, not_present5 = (''.join(sorted(not_present[i])) for i in range(1, 6))

    all_present = present1 + present2 + present3 + present4 + present5 + not_present1 + not_present2 + not_present3 + not_present4 + not_present5
    for letter in all_present:
        must_not_be_present = must_not_be_present.replace(letter, "")
        # account for a letter used twice. one is grey, one is yellow or green

    must_not_be_present, present1, present2, present3, present4, present5, \
    not_present1, not_present2, not_present3, not_present4, not_present5 = \
    map(str.lower, [must_not_be_present, present1, present2, present3, present4, present5,
                    not_present1, not_present2, not_present3, not_present4, not_present5])

    # split individual letters into lists
    must_not_be_present = list(must_not_be_present)
    present = [present1, present2, present3, present4, present5]
    not_present = [not_present1, not_present2, not_present3, not_present4, not_present5]
    must_be_present = (''.join(not_present))

    places = ['one', 'two', 'three', 'four', 'five']
    # df = pd.read_excel(import_df)
    df = import_df.copy()
    total_len = len(df)

    # process the 'must be present' letters
    for j in must_be_present:
        drop_list = []
        for i in range(len(df)):
            drop_list.append(df['word'][i].find(j))
        df['drop_no_' + j] = drop_list
    for j in must_be_present:
        df = df[df['drop_no_' + j] != -1]

    # process the 'must not be present' letters
    for i in places:
        for j in must_not_be_present:
            df = df[df[i] != j]

    # process the 'specific values must be present' letters
    for i, v in enumerate(places):
        if present[i] != '':
            df = df[df[v] == present[i]]

    # process the 'specific values not must be present' letters
    for j, k in enumerate(places):
        if len(not_present[j]) > 0:
            for i in not_present[j]:
                df = df[df[k] != (','.join(i))]

    # pick the best (aka reasonably good) choice by sorting on the highest 'word_score'
    df = df.sort_values(by = 'word_score', ascending = False)

    try:
        final_out1 = 'Pick 1: ' + df.iat[0, 0] # print top 5 in case you get trapped in a narrow path of replacing just 1 letter at a time
    except:
        final_out1 = 'No words found'
    try:
        final_out2 = 'Pick 2: ' + df.iat[1, 0] # print top 5 in case you get trapped in a narrow path of replacing just 1 letter at a time
    except:
        final_out2 = ''
    try:
        final_out3 = 'Pick 3: ' + df.iat[2, 0] # print top 5 in case you get trapped in a narrow path of replacing just 1 letter at a time
    except:
        final_out3 = ''
    try:
        final_out4 = 'Pick 4: ' + df.iat[3, 0] # print top 5 in case you get trapped in a narrow path of replacing just 1 letter at a time
    except:
        final_out4 = ''
    try:
        final_out5 = 'Pick 5: ' + df.iat[4, 0] # print top 5 in case you get trapped in a narrow path of replacing just 1 letter at a time
    except:
        final_out5 = ''
    final_out_end = f'Options remaining: {len(df)}/{total_len} ({round(len(df)/total_len*100,2)}%)'

    return final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end, first_incomplete_row, complete_rows


# Quordle solver function
# main solver function --- for web deploy
def quordle_solver_split(import_df, 

    must_not_be_present1: str, 
    present1_1: str, present1_2: str, present1_3: str, present1_4: str, present1_5: str,
    not_present1_1: str, not_present1_2: str, not_present1_3: str, not_present1_4: str, not_present1_5: str,

    must_not_be_present2: str, 
    present2_1: str, present2_2: str, present2_3: str, present2_4: str, present2_5: str,
    not_present2_1: str, not_present2_2: str, not_present2_3: str, not_present2_4: str, not_present2_5: str,

    must_not_be_present3: str, 
    present3_1: str, present3_2: str, present3_3: str, present3_4: str, present3_5: str,
    not_present3_1: str, not_present3_2: str, not_present3_3: str, not_present3_4: str, not_present3_5: str,

    must_not_be_present4: str, 
    present4_1: str, present4_2: str, present4_3: str, present4_4: str, present4_5: str,
    not_present4_1: str, not_present4_2: str, not_present4_3: str, not_present4_4: str, not_present4_5: str):

    must_not_be_present1 = must_not_be_present1.lower()
    present1_1 = present1_1.lower()
    present1_2 = present1_2.lower()
    present1_3 = present1_3.lower()
    present1_4 = present1_4.lower()
    present1_5 = present1_5.lower()
    present2_1 = present2_1.lower()
    present2_2 = present2_2.lower()
    present2_3 = present2_3.lower()
    present2_4 = present2_4.lower()
    present2_5 = present2_5.lower()
    present3_1 = present3_1.lower()
    present3_2 = present3_2.lower()
    present3_3 = present3_3.lower()
    present3_4 = present3_4.lower()
    present3_5 = present3_5.lower()
    present4_1 = present4_1.lower()
    present4_2 = present4_2.lower()
    present4_3 = present4_3.lower()
    present4_4 = present4_4.lower()
    present4_5 = present4_5.lower()
    not_present1_1 = not_present1_1.lower()
    not_present1_2 = not_present1_2.lower()
    not_present1_3 = not_present1_3.lower()
    not_present1_4 = not_present1_4.lower()
    not_present1_5 = not_present1_5.lower()
    not_present2_1 = not_present2_1.lower()
    not_present2_2 = not_present2_2.lower()
    not_present2_3 = not_present2_3.lower()
    not_present2_4 = not_present2_4.lower()
    not_present2_5 = not_present2_5.lower()
    not_present3_1 = not_present3_1.lower()
    not_present3_2 = not_present3_2.lower()
    not_present3_3 = not_present3_3.lower()
    not_present3_4 = not_present3_4.lower()
    not_present3_5 = not_present3_5.lower()
    not_present4_1 = not_present4_1.lower()
    not_present4_2 = not_present4_2.lower()
    not_present4_3 = not_present4_3.lower()
    not_present4_4 = not_present4_4.lower()
    not_present4_5 = not_present4_5.lower()

    ##### should be a 1-off, need to split the df into 4 or 5 df's
    places = ['one', 'two', 'three', 'four', 'five']
    alpha = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z']
    # df = pd.read_excel(import_path)
    df = import_df.copy()
    df1 = df2 = df3 = df4 = df_all = df.copy()
    total_len = len(df)


    # puzzle 1
    # split individual letters into lists
    must_not_be_present1 = list(must_not_be_present1)
    present1 = [present1_1, present1_2, present1_3, present1_4, present1_5]
    not_present1 = [not_present1_1, not_present1_2, not_present1_3, not_present1_4, not_present1_5]
    must_be_present1 = (''.join(not_present1))
    # process the 'must be present' letters
    for j in must_be_present1:
        drop_list = []
        for i in range(len(df1)):
            drop_list.append(df1['word'][i].find(j))
        df1['drop_no_' + j] = drop_list
    for j in must_be_present1:
        df1 = df1[df1['drop_no_' + j] != -1]
    # process the 'must not be present' letters
    for i in places:
        for j in must_not_be_present1:
            df1 = df1[df1[i] != j]
    # process the 'specific values must be present' letters
    for i, v in enumerate(places):
        if present1[i] != '':
            df1 = df1[df1[v] == present1[i]]
    # process the 'specific values not must be present' letters
    for j, k in enumerate(places):
        if len(not_present1[j]) > 0:
            for i in not_present1[j]:
                df1 = df1[df1[k] != (','.join(i))]
    # pick the best (aka reasonably good) choice by sorting on the highest 'word_score'
    df1 = df1.sort_values(by = 'word_score', ascending =  False)

    # puzzle 2
    # split individual letters into lists
    must_not_be_present2 = list(must_not_be_present2)
    present2 = [present2_1, present2_2, present2_3, present2_4, present2_5]
    not_present2 = [not_present2_1, not_present2_2, not_present2_3, not_present2_4, not_present2_5]
    must_be_present2 = (''.join(not_present2))
    # process the 'must be present' letters
    for j in must_be_present2:
        drop_list = []
        for i in range(len(df2)):
            drop_list.append(df2['word'][i].find(j))
        df2['drop_no_' + j] = drop_list
    for j in must_be_present2:
        df2 = df2[df2['drop_no_' + j] != -1]
    # process the 'must not be present' letters
    for i in places:
        for j in must_not_be_present2:
            df2 = df2[df2[i] != j]
    # process the 'specific values must be present' letters
    for i, v in enumerate(places):
        if present2[i] != '':
            df2 = df2[df2[v] == present2[i]]
    # process the 'specific values not must be present' letters
    for j, k in enumerate(places):
        if len(not_present2[j]) > 0:
            for i in not_present2[j]:
                df2 = df2[df2[k] != (','.join(i))]
    # pick the best (aka reasonably good) choice by sorting on the highest 'word_score'
    df2 = df2.sort_values(by = 'word_score', ascending =  False)

    # puzzle 3
    # split individual letters into lists
    must_not_be_present3 = list(must_not_be_present3)
    present3 = [present3_1, present3_2, present3_3, present3_4, present3_5]
    not_present3 = [not_present3_1, not_present3_2, not_present3_3, not_present3_4, not_present3_5]
    must_be_present3 = (''.join(not_present3))
    # process the 'must be present' letters
    for j in must_be_present3:
        drop_list = []
        for i in range(len(df3)):
            drop_list.append(df3['word'][i].find(j))
        df3['drop_no_' + j] = drop_list
    for j in must_be_present3:
        df3 = df3[df3['drop_no_' + j] != -1]
    # process the 'must not be present' letters
    for i in places:
        for j in must_not_be_present3:
            df3 = df3[df3[i] != j]
    # process the 'specific values must be present' letters
    for i, v in enumerate(places):
        if present3[i] != '':
            df3 = df3[df3[v] == present3[i]]
    # process the 'specific values not must be present' letters
    for j, k in enumerate(places):
        if len(not_present3[j]) > 0:
            for i in not_present3[j]:
                df3 = df3[df3[k] != (','.join(i))]
    # pick the best (aka reasonably good) choice by sorting on the highest 'word_score'
    df3 = df3.sort_values(by = 'word_score', ascending =  False)

    # puzzle 4
    # split individual letters into lists
    must_not_be_present4 = list(must_not_be_present4)
    present4 = [present4_1, present4_2, present4_3, present4_4, present4_5]
    not_present4 = [not_present4_1, not_present4_2, not_present4_3, not_present4_4, not_present4_5]
    must_be_present4 = (''.join(not_present4))
    # process the 'must be present' letters
    for j in must_be_present4:
        drop_list = []
        for i in range(len(df4)):
            drop_list.append(df4['word'][i].find(j))
        df4['drop_no_' + j] = drop_list
    for j in must_be_present4:
        df4 = df4[df4['drop_no_' + j] != -1]
    # process the 'must not be present' letters
    for i in places:
        for j in must_not_be_present4:
            df4 = df4[df4[i] != j]
    # process the 'specific values must be present' letters
    for i, v in enumerate(places):
        if present4[i] != '':
            df4 = df4[df4[v] == present4[i]]
    # process the 'specific values not must be present' letters
    for j, k in enumerate(places):
        if len(not_present4[j]) > 0:
            for i in not_present4[j]:
                df4 = df4[df4[k] != (','.join(i))]
    # pick the best (aka reasonably good) choice by sorting on the highest 'word_score'
    df4 = df4.sort_values(by = 'word_score', ascending =  False)

    # all puzzle calcs

    # basic logic framework
    # all_word_score
        # sum: Letter_score1-5 (not counted if dup)
            # each letter_score: naive letter freq * tried penalty (0.5) * not seen in puzzle penalty for puzzles 1-4 (0.5)

    penalty = 0.5
    known_penalty = 0.25

    not_seen1 = (''.join(must_not_be_present1))
    not_seen2 = (''.join(must_not_be_present2))
    not_seen3 = (''.join(must_not_be_present3))
    not_seen4 = (''.join(must_not_be_present4))
    known_pos1 = present1_1 + present1_2 + present1_3 + present1_4 + present1_5
    known_pos2 = present2_1 + present2_2 + present2_3 + present2_4 + present2_5
    known_pos3 = present3_1 + present3_2 + present3_3 + present3_4 + present3_5
    known_pos4 = present4_1 + present4_2 + present4_3 + present4_4 + present4_5
    not_seen_list1 = []
    not_seen_list2 = []
    not_seen_list3 = []
    not_seen_list4 = []
    known_pos_list1 = []
    known_pos_list2 = []
    known_pos_list3 = []
    known_pos_list4 = []
    for i in alpha:
        if not_seen1.find(i) != -1:
            not_seen_list1.append(penalty)
        else:
            not_seen_list1.append(1)
        if not_seen2.find(i) != -1:
            not_seen_list2.append(penalty)
        else:
            not_seen_list2.append(1)
        if not_seen3.find(i) != -1:
            not_seen_list3.append(penalty)
        else:
            not_seen_list3.append(1)
        if not_seen4.find(i) != -1:
            not_seen_list4.append(penalty)
        else:
            not_seen_list4.append(1)

        if known_pos1.find(i) != -1:
            known_pos_list1.append(known_penalty)
        else:
            known_pos_list1.append(1)
        if known_pos2.find(i) != -1:
            known_pos_list2.append(known_penalty)
        else:
            known_pos_list2.append(1)
        if known_pos3.find(i) != -1:
            known_pos_list3.append(known_penalty)
        else:
            known_pos_list3.append(1)
        if known_pos4.find(i) != -1:
            known_pos_list4.append(known_penalty)
        else:
            known_pos_list4.append(1)

    tried = (''.join(must_not_be_present1)) + present1_1 + present1_2 + present1_3 + present1_4 + present1_5 + not_present1_1 + not_present1_2 + not_present1_3 + not_present1_4 + not_present1_5
    tried.find('')
    tried_list = []
    for i in alpha:
        if tried.find(i) != -1:
            tried_list.append(penalty)
        else:
            tried_list.append(1)

    df_all_puzzle = pd.DataFrame()
    df_all_puzzle['letter'] = df_all['word'].str[:1]
    df_all_puzzle['freq'] = df_all['one_val']
    df_all_puzzle['freq'] = (df_all_puzzle['freq'] - min(df_all_puzzle['freq']))/(max(df_all_puzzle['freq']) - min(df_all_puzzle['freq'])) + 9
    df_all_puzzle = df_all_puzzle.drop_duplicates()
    df_all_puzzle = df_all_puzzle.reset_index(drop = True)
    df_all_puzzle['tried'] = tried_list
    df_all_puzzle['not_seen1'] = not_seen_list1
    df_all_puzzle['not_seen2'] = not_seen_list2
    df_all_puzzle['not_seen3'] = not_seen_list3
    df_all_puzzle['not_seen4'] = not_seen_list4
    df_all_puzzle['known_pos1'] = known_pos_list1
    df_all_puzzle['known_pos2'] = known_pos_list2
    df_all_puzzle['known_pos3'] = known_pos_list3
    df_all_puzzle['known_pos4'] = known_pos_list4
    df_all_puzzle['letter_score'] = df_all_puzzle['freq'] * df_all_puzzle['tried'] * \
        df_all_puzzle['not_seen1'] * df_all_puzzle['not_seen2'] * df_all_puzzle['not_seen3'] * df_all_puzzle['not_seen4'] * \
        df_all_puzzle['known_pos1'] * df_all_puzzle['known_pos2'] * df_all_puzzle['known_pos3'] * df_all_puzzle['known_pos4']
    df_all_puzzle = df_all_puzzle.drop(columns = ['freq', 'tried', 'not_seen1', 'not_seen2', 'not_seen3', 'not_seen4', 'known_pos1', 'known_pos2', 'known_pos3', 'known_pos4'])

    # df_all_puzzle

    df_all_word_rank = pd.DataFrame()
    df_all_word_rank['word'] = df['word'].copy()
    df_all_word_rank['two_match'] = df['two_match'].copy()
    df_all_word_rank['three_match'] = df['three_match'].copy()
    df_all_word_rank['four_match'] = df['four_match'].copy()
    df_all_word_rank['five_match'] = df['five_match'].copy()

    df_all_word_rank['letter1'] = df_all_word_rank['word'].str[:1]
    df_all_word_rank['letter2'] = df_all_word_rank['word'].str[1:2]
    df_all_word_rank['letter3'] = df_all_word_rank['word'].str[2:3]
    df_all_word_rank['letter4'] = df_all_word_rank['word'].str[3:4]
    df_all_word_rank['letter5'] = df_all_word_rank['word'].str[4:5]

    df_all_word_rank = pd.merge(how = 'left', left = df_all_word_rank, right = df_all_puzzle, left_on = 'letter1', right_on = 'letter')
    df_all_word_rank = df_all_word_rank.drop(columns = ['letter'])
    df_all_word_rank = df_all_word_rank.rename(columns = {'letter_score': 'letter_score1'})

    df_all_word_rank = pd.merge(how = 'left', left = df_all_word_rank, right = df_all_puzzle, left_on = 'letter2', right_on = 'letter')
    df_all_word_rank = df_all_word_rank.drop(columns = ['letter'])
    df_all_word_rank = df_all_word_rank.rename(columns = {'letter_score': 'letter_score2'})

    df_all_word_rank = pd.merge(how = 'left', left = df_all_word_rank, right = df_all_puzzle, left_on = 'letter3', right_on = 'letter')
    df_all_word_rank = df_all_word_rank.drop(columns = ['letter'])
    df_all_word_rank = df_all_word_rank.rename(columns = {'letter_score': 'letter_score3'})

    df_all_word_rank = pd.merge(how = 'left', left = df_all_word_rank, right = df_all_puzzle, left_on = 'letter4', right_on = 'letter')
    df_all_word_rank = df_all_word_rank.drop(columns = ['letter'])
    df_all_word_rank = df_all_word_rank.rename(columns = {'letter_score': 'letter_score4'})

    df_all_word_rank = pd.merge(how = 'left', left = df_all_word_rank, right = df_all_puzzle, left_on = 'letter5', right_on = 'letter')
    df_all_word_rank = df_all_word_rank.drop(columns = ['letter'])
    df_all_word_rank = df_all_word_rank.rename(columns = {'letter_score': 'letter_score5'})

    df_all_word_rank['all_word_score'] = round(df_all_word_rank['letter_score1'] + (df_all_word_rank['letter_score2'] * df_all_word_rank['two_match']) + (df_all_word_rank['letter_score3'] * df_all_word_rank['three_match']) + (df_all_word_rank['letter_score4'] * df_all_word_rank['four_match']) + (df_all_word_rank['letter_score5'] * df_all_word_rank['five_match']), 4)
    df_all_word_rank = df_all_word_rank.drop(columns = ['two_match', 'three_match', 'four_match', 'five_match', 'letter1', 'letter2', 'letter3', 'letter4', 'letter5', 'letter_score1', 'letter_score2', 'letter_score3', 'letter_score4', 'letter_score5'])

    df_all_word_rank = df_all_word_rank.sort_values(by = ['all_word_score'], ascending = False)

    # df_all_word_rank

    ##### will be a 1-off, but needs to be highly edited

    # all puzzle
    try:
        final_out_all_1 = 'Pick 1: ' + df_all_word_rank.iat[0, 0]
    except:
        final_out_all_1 = 'No words found'
    try:
        final_out_all_2 = 'Pick 2: ' + df_all_word_rank.iat[1, 0]
    except:
        final_out_all_2 = ''
    try:
        final_out_all_3 = 'Pick 3: ' + df_all_word_rank.iat[2, 0]
    except:
        final_out_all_3 = ''
    try:
        final_out_all_4 = 'Pick 4: ' + df_all_word_rank.iat[3, 0]
    except:
        final_out_all_4 = ''
    try:
        final_out_all_5 = 'Pick 5: ' + df_all_word_rank.iat[4, 0]
    except:
        final_out_all_5 = ''
    final_out_end_all = f'Options remaining: {len(df_all_word_rank)}/{total_len} ({round(len(df_all_word_rank)/total_len*100,2)}%)'

    # puzzle 1
    try:
        final_out1_1 = 'Pick 1: ' + df1.iat[0, 0]
    except:
        final_out1_1 = 'No words found'
    try:
        final_out1_2 = 'Pick 2: ' + df1.iat[1, 0]
    except:
        final_out1_2 = ''
    try:
        final_out1_3 = 'Pick 3: ' + df1.iat[2, 0]
    except:
        final_out1_3 = ''
    try:
        final_out1_4 = 'Pick 4: ' + df1.iat[3, 0]
    except:
        final_out1_4 = ''
    try:
        final_out1_5 = 'Pick 5: ' + df1.iat[4, 0]
    except:
        final_out1_5 = ''
    final_out_end1 = f'Options remaining: {len(df1)}/{total_len} ({round(len(df1)/total_len*100,2)}%)'

    # puzzle 2
    try:
        final_out2_1 = 'Pick 1: ' + df2.iat[0, 0]
    except:
        final_out2_1 = 'No words found'
    try:
        final_out2_2 = 'Pick 2: ' + df2.iat[1, 0]
    except:
        final_out2_2 = ''
    try:
        final_out2_3 = 'Pick 3: ' + df2.iat[2, 0]
    except:
        final_out2_3 = ''
    try:
        final_out2_4 = 'Pick 4: ' + df2.iat[3, 0]
    except:
        final_out2_4 = ''
    try:
        final_out2_5 = 'Pick 5: ' + df2.iat[4, 0]
    except:
        final_out2_5 = ''
    final_out_end2 = f'Options remaining: {len(df2)}/{total_len} ({round(len(df2)/total_len*100,2)}%)'

    # puzzle 3
    try:
        final_out3_1 = 'Pick 1: ' + df3.iat[0, 0]
    except:
        final_out3_1 = 'No words found'
    try:
        final_out3_2 = 'Pick 2: ' + df3.iat[1, 0]
    except:
        final_out3_2 = ''
    try:
        final_out3_3 = 'Pick 3: ' + df3.iat[2, 0]
    except:
        final_out3_3 = ''
    try:
        final_out3_4 = 'Pick 4: ' + df3.iat[3, 0]
    except:
        final_out3_4 = ''
    try:
        final_out3_5 = 'Pick 5: ' + df3.iat[4, 0]
    except:
        final_out3_5 = ''
    final_out_end3 = f'Options remaining: {len(df3)}/{total_len} ({round(len(df3)/total_len*100,2)}%)'

    # puzzle 4
    try:
        final_out4_1 = 'Pick 1: ' + df4.iat[0, 0]
    except:
        final_out4_1 = 'No words found'
    try:
        final_out4_2 = 'Pick 2: ' + df4.iat[1, 0]
    except:
        final_out4_2 = ''
    try:
        final_out4_3 = 'Pick 3: ' + df4.iat[2, 0]
    except:
        final_out4_3 = ''
    try:
        final_out4_4 = 'Pick 4: ' + df4.iat[3, 0]
    except:
        final_out4_4 = ''
    try:
        final_out4_5 = 'Pick 5: ' + df4.iat[4, 0]
    except:
        final_out4_5 = ''
    final_out_end4 = f'Options remaining: {len(df4)}/{total_len} ({round(len(df4)/total_len*100,2)}%)'

    return final_out_all_1, final_out_all_2, final_out_all_3, final_out_all_4, final_out_all_5, final_out_end_all \
     ,final_out1_1, final_out1_2, final_out1_3, final_out1_4, final_out1_5, final_out_end1 \
     ,final_out2_1, final_out2_2, final_out2_3, final_out2_4, final_out2_5, final_out_end2 \
     ,final_out3_1, final_out3_2, final_out3_3, final_out3_4, final_out3_5, final_out_end3 \
     ,final_out4_1, final_out4_2, final_out4_3, final_out4_4, final_out4_5, final_out_end4

def quordle_solver_split_revamp(import_df, quordle_data_dict):
    """
    Quordle solver that follows the wordle revamp pattern
    Takes quordle_data_dict in format: 
    [
        {"letter": "A", "position": "1", "color": "1", "row": "1", "puzzle": "1"},
        {"letter": "R", "position": "2", "color": "2", "row": "1", "puzzle": "1"},
        ...
    ]
    """
    
    # Initialize data structures for each puzzle
    puzzles_data = {
        1: {"must_not_be_present": set(), "present": {i: set() for i in range(1, 6)}, "not_present": {i: set() for i in range(1, 6)}},
        2: {"must_not_be_present": set(), "present": {i: set() for i in range(1, 6)}, "not_present": {i: set() for i in range(1, 6)}},
        3: {"must_not_be_present": set(), "present": {i: set() for i in range(1, 6)}, "not_present": {i: set() for i in range(1, 6)}},
        4: {"must_not_be_present": set(), "present": {i: set() for i in range(1, 6)}, "not_present": {i: set() for i in range(1, 6)}}
    }
    
    # Process the input data
    for entry in quordle_data_dict:
        if entry['letter']:  # Check if 'letter' is not empty
            letter = entry['letter'].lower()
            position = int(entry['position'])
            color = entry['color']
            puzzle = int(entry['puzzle'])
            
            # Letters that must not be present anywhere (gray)
            if color == '1':
                puzzles_data[puzzle]["must_not_be_present"].add(letter)
            # Letters present in specific positions (green)
            elif color == '3':
                puzzles_data[puzzle]["present"][position].add(letter)
            # Letters that are not present in specific positions but are in the word (yellow)
            elif color == '2':
                puzzles_data[puzzle]["not_present"][position].add(letter)
    
    # Convert sets to strings and handle letter conflicts for each puzzle
    puzzle_params = {}
    for puzzle_num in range(1, 5):
        data = puzzles_data[puzzle_num]
        
        # Convert present and not_present to strings
        present_strings = [''.join(sorted(data["present"][i])) for i in range(1, 6)]
        not_present_strings = [''.join(sorted(data["not_present"][i])) for i in range(1, 6)]
        
        # Remove letters that are green or yellow from gray letters
        all_present = ''.join(present_strings + not_present_strings)
        must_not_be_present = ''.join(sorted(data["must_not_be_present"] - set(all_present)))
        
        # Store parameters for this puzzle
        puzzle_params[puzzle_num] = {
            'must_not_be_present': must_not_be_present,
            'present': present_strings,
            'not_present': not_present_strings
        }
    
    # Call the existing quordle_solver_split function with the formatted parameters
    results = quordle_solver_split(
        import_df,
        puzzle_params[1]['must_not_be_present'], 
        *puzzle_params[1]['present'], 
        *puzzle_params[1]['not_present'],
        puzzle_params[2]['must_not_be_present'], 
        *puzzle_params[2]['present'], 
        *puzzle_params[2]['not_present'],
        puzzle_params[3]['must_not_be_present'], 
        *puzzle_params[3]['present'], 
        *puzzle_params[3]['not_present'],
        puzzle_params[4]['must_not_be_present'], 
        *puzzle_params[4]['present'], 
        *puzzle_params[4]['not_present']
    )
    
    return results

# last letter solver
def find_word_with_letters(import_df, must_be_present: str):

    must_be_present = must_be_present.lower()

    df = import_df.copy()

    count_list = []
    for i in range(len(df)):
        counting = 0
        for j in must_be_present:
            if df['word'][i].find(j) == -1:
                counting += 0
            else:
                counting += 1
        count_list.append(counting)
            
    df['char_match_count'] = count_list

    df = df.sort_values(by = ['char_match_count', 'word_score'], ascending =  False)
    df = df[['word', 'char_match_count']]

    try:
        final_out1 = f"Pick 1: {df.iat[0, 0]} ({df['char_match_count'].iloc[0]} match)"
    except:
        final_out1 = 'No words found'
    try:
        final_out2 = f"Pick 2: {df.iat[1, 0]} ({df['char_match_count'].iloc[1]} match)"
    except:
        final_out2 = ''
    try:
        final_out3 = f"Pick 3: {df.iat[2, 0]} ({df['char_match_count'].iloc[2]} match)"
    except:
        final_out3 = ''
    try:
        final_out4 = f"Pick 4: {df.iat[3, 0]} ({df['char_match_count'].iloc[3]} match)"
    except:
        final_out4 = ''
    try:
        final_out5 = f"Pick 5: {df.iat[4, 0]} ({df['char_match_count'].iloc[4]} match)"
    except:
        final_out5 = ''

    return final_out1, final_out2, final_out3, final_out4, final_out5


# Antiwordle solver function
def antiwordle_solver_split(import_df, must_not_be_present: str, 
    present1: str, present2: str, present3: str, present4: str, present5: str,
    not_present1: str, not_present2: str, not_present3: str, not_present4: str, not_present5: str):

    must_not_be_present = must_not_be_present.lower() # gray
    present1 = present1.lower() # green/red
    present2 = present2.lower()
    present3 = present3.lower()
    present4 = present4.lower()
    present5 = present5.lower()
    not_present1 = not_present1.lower() # yellow
    not_present2 = not_present2.lower()
    not_present3 = not_present3.lower()
    not_present4 = not_present4.lower()
    not_present5 = not_present5.lower()

    places = ['one', 'two', 'three', 'four', 'five']
    df = import_df.copy()
    total_len = len(df)

    # gray - letters not in the work
    must_not_be_present = list(must_not_be_present)
    for i in places:
        for j in must_not_be_present:
            df = df[df[i] != j]

    # green - letters contained with a known position
    present = [present1, present2, present3, present4, present5]
    for i, v in enumerate(places):
        if present[i] != '':
            df = df[df[v] == present[i]]

    # yellow - letter contained with an unknown position
    not_present = [not_present1, not_present2, not_present3, not_present4, not_present5]

    must_be_present = (''.join(not_present))
    not_present_joined = ''.join(filter(lambda x: x != '', not_present))

    # yellow round 1 - toss any words that do not have the letter
    df = df[df['word'].apply(lambda x: all(letter in x for letter in not_present_joined))]

    # yellow round 2 - give a bonus if the letter is in the same position
    bonus = 0.75

    for i in range(5):
        condition = locals().get(f'not_present{i + 1}')
        if condition:
            df.loc[df['word'].str[i].isin(list(condition)), 'word_score'] *= bonus


    # finalize
    df = df.sort_values(by = 'word_score', ascending =  True)


    try:
        final_out1 = 'Pick 1: ' + df.iat[0, 0] # print top 5 in case you get trapped in a narrow path of replacing just 1 letter at a time
    except:
        final_out1 = 'No words found'
    try:
        final_out2 = 'Pick 2: ' + df.iat[1, 0] # print top 5 in case you get trapped in a narrow path of replacing just 1 letter at a time
    except:
        final_out2 = ''
    try:
        final_out3 = 'Pick 3: ' + df.iat[2, 0] # print top 5 in case you get trapped in a narrow path of replacing just 1 letter at a time
    except:
        final_out3 = ''
    try:
        final_out4 = 'Pick 4: ' + df.iat[3, 0] # print top 5 in case you get trapped in a narrow path of replacing just 1 letter at a time
    except:
        final_out4 = ''
    try:
        final_out5 = 'Pick 5: ' + df.iat[4, 0] # print top 5 in case you get trapped in a narrow path of replacing just 1 letter at a time
    except:
        final_out5 = ''
    final_out_end = f'Options remaining: {len(df)}/{total_len} ({round(len(df)/total_len*100,2)}%)'

    return final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end


def antiwordle_solver_split_revamp(import_df, wordle_data_dict):

    row_letters = {}
    for entry in wordle_data_dict:
        if entry['letter']:  # Check if 'letter' is not empty
            row_letters[entry['row']] = row_letters.get(entry['row'], 0) + 1
    complete_rows = [row for row, count in row_letters.items() if count == 5]
    complete_rows = [int(row) for row in complete_rows]
    # print(complete_rows) # possibly use to make a visual indicator of rows that are considered to be complete

    first_incomplete_row = None
    if complete_rows:
        for i in range(1, max(complete_rows) + 2):  # Check up to one row beyond the last complete row
            if i not in complete_rows:
                first_incomplete_row = i
                break  # Exit the loop once the first incomplete row is found
    else:
        first_incomplete_row = 1
    if first_incomplete_row >= 7:
        first_incomplete_row = None
    # print(first_incomplete_row) # use as a marker of where to populate the chosen word

    must_not_be_present = set()
    present = {i: set() for i in range(1, 6)}  # Use sets for present letters by position
    not_present = {i: set() for i in range(1, 6)}  # Use sets for not present letters by position

    # Populate the sets based on conditions
    for entry in wordle_data_dict:
        letter = entry['letter'].lower()  # Ensure consistent casing
        position = int(entry['position'])
        color = entry['color']
        
        # Letters that must not be present anywhere
        if color == '1' and letter: # grey
            must_not_be_present.add(letter)
        # Letters present in specific positions
        elif color == '3' and letter: # green
            present[position].add(letter)
        # Letters that are not present in specific positions but are in the word
        elif color == '2' and letter: # yellow
            not_present[position].add(letter)

    # Convert the sets back to strings without duplicates
    must_not_be_present = ''.join(sorted(must_not_be_present))
    present1, present2, present3, present4, present5 = (''.join(sorted(present[i])) for i in range(1, 6))
    not_present1, not_present2, not_present3, not_present4, not_present5 = (''.join(sorted(not_present[i])) for i in range(1, 6))

    all_present = present1 + present2 + present3 + present4 + present5 + not_present1 + not_present2 + not_present3 + not_present4 + not_present5
    for letter in all_present:
        must_not_be_present = must_not_be_present.replace(letter, "")
        # account for a letter used twice. one is grey, one is yellow or green

    must_not_be_present, present1, present2, present3, present4, present5, \
    not_present1, not_present2, not_present3, not_present4, not_present5 = \
    map(str.lower, [must_not_be_present, present1, present2, present3, present4, present5,
                    not_present1, not_present2, not_present3, not_present4, not_present5])

    places = ['one', 'two', 'three', 'four', 'five']
    df = import_df.copy()
    total_len = len(df)

    # gray - letters not in the work
    must_not_be_present = list(must_not_be_present)
    for i in places:
        for j in must_not_be_present:
            df = df[df[i] != j]

    # green - letters contained with a known position
    present = [present1, present2, present3, present4, present5]
    for i, v in enumerate(places):
        if present[i] != '':
            df = df[df[v] == present[i]]

    # yellow - letter contained with an unknown position
    not_present = [not_present1, not_present2, not_present3, not_present4, not_present5]

    must_be_present = (''.join(not_present))
    not_present_joined = ''.join(filter(lambda x: x != '', not_present))

    # yellow round 1 - toss any words that do not have the letter
    if not_present_joined:  # Only filter if there are letters to check
        df = df[df['word'].apply(lambda x: all(letter in x for letter in not_present_joined))]

    # yellow round 2 - give a bonus if the letter is in the same position
    bonus = 0.75

    for i in range(5):
        condition = locals().get(f'not_present{i + 1}')
        if condition and len(df) > 0:  # Check if df is not empty
            df.loc[df['word'].str[i].isin(list(condition)), 'word_score'] *= bonus

    # Check if dataframe is empty before sorting
    if len(df) == 0:
        # Return "No words found" for all outputs when dataframe is empty
        return ('No words found', '', '', '', '', 
                f'Options remaining: 0/{total_len} (0.0%)', 
                first_incomplete_row, complete_rows)

    # finalize - only sort if we have data
    df = df.sort_values(by = 'word_score', ascending = True)

    # Build outputs safely
    final_out1 = 'Pick 1: ' + df.iat[0, 0] if len(df) > 0 else 'No words found'
    final_out2 = 'Pick 2: ' + df.iat[1, 0] if len(df) > 1 else ''
    final_out3 = 'Pick 3: ' + df.iat[2, 0] if len(df) > 2 else ''
    final_out4 = 'Pick 4: ' + df.iat[3, 0] if len(df) > 3 else ''
    final_out5 = 'Pick 5: ' + df.iat[4, 0] if len(df) > 4 else ''
    final_out_end = f'Options remaining: {len(df)}/{total_len} ({round(len(df)/total_len*100,2)}%)'

    return final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end, first_incomplete_row, complete_rows