from flask import Flask, redirect, render_template, url_for, request, redirect, jsonify
import pandas as pd
import os

app = Flask(__name__)


# Current directory for Flask app
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
file_path = os.path.join(APP_ROOT, 'word_data_created.csv')
df = pd.read_csv(file_path)



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
    df = df.sort_values(by = 'word_score', ascending =  False)

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










@app.route("/", methods=["POST", "GET"])
def run_index():
    if request.method == "POST":
        return render_template("index.html")
    else:
        return render_template("index.html")


@app.route("/wordle", methods=["POST", "GET"])
def run_wordle():
    if request.method == "POST":
        must_not_be_present = request.form["must_not_be_present"]
        present1 = request.form["present1"]
        present2 = request.form["present2"]
        present3 = request.form["present3"]
        present4 = request.form["present4"]
        present5 = request.form["present5"]
        not_present1 = request.form["not_present1"]
        not_present2 = request.form["not_present2"]
        not_present3 = request.form["not_present3"]
        not_present4 = request.form["not_present4"]
        not_present5 = request.form["not_present5"]
        final_out1, final_out2, final_out3, final_out4, final_out5, final_out_end = wordle_solver_split(df, must_not_be_present, \
            present1, present2, present3, present4, present5, not_present1, not_present2, not_present3, not_present4, not_present5)
        return render_template("wordle.html", final_out1=final_out1, final_out2=final_out2, final_out3=final_out3, final_out4=final_out4, final_out5=final_out5, final_out_end=final_out_end, \
            must_not_be_present_val=must_not_be_present, present1_val=present1, present2_val=present2, present3_val=present3, present4_val=present4, present5_val=present5, \
            not_present1_val=not_present1, not_present2_val=not_present2, not_present3_val=not_present3, not_present4_val=not_present4, not_present5_val=not_present5)
    else:
        return render_template("wordle.html")


@app.route("/quordle", methods=["POST", "GET"])
def run_quordle():
    if request.method == "POST":
        must_not_be_present1 = request.form["must_not_be_present1"]
        must_not_be_present2 = request.form["must_not_be_present2"]
        must_not_be_present3 = request.form["must_not_be_present3"]
        must_not_be_present4 = request.form["must_not_be_present4"]

        present1_1 = request.form["present1_1"]
        present1_2 = request.form["present1_2"]
        present1_3 = request.form["present1_3"]
        present1_4 = request.form["present1_4"]
        present1_5 = request.form["present1_5"]
        present2_1 = request.form["present2_1"]
        present2_2 = request.form["present2_2"]
        present2_3 = request.form["present2_3"]
        present2_4 = request.form["present2_4"]
        present2_5 = request.form["present2_5"]
        present3_1 = request.form["present3_1"]
        present3_2 = request.form["present3_2"]
        present3_3 = request.form["present3_3"]
        present3_4 = request.form["present3_4"]
        present3_5 = request.form["present3_5"]
        present4_1 = request.form["present4_1"]
        present4_2 = request.form["present4_2"]
        present4_3 = request.form["present4_3"]
        present4_4 = request.form["present4_4"]
        present4_5 = request.form["present4_5"]

        not_present1_1 = request.form["not_present1_1"]
        not_present1_2 = request.form["not_present1_2"]
        not_present1_3 = request.form["not_present1_3"]
        not_present1_4 = request.form["not_present1_4"]
        not_present1_5 = request.form["not_present1_5"]
        not_present2_1 = request.form["not_present2_1"]
        not_present2_2 = request.form["not_present2_2"]
        not_present2_3 = request.form["not_present2_3"]
        not_present2_4 = request.form["not_present2_4"]
        not_present2_5 = request.form["not_present2_5"]
        not_present3_1 = request.form["not_present3_1"]
        not_present3_2 = request.form["not_present3_2"]
        not_present3_3 = request.form["not_present3_3"]
        not_present3_4 = request.form["not_present3_4"]
        not_present3_5 = request.form["not_present3_5"]
        not_present4_1 = request.form["not_present4_1"]
        not_present4_2 = request.form["not_present4_2"]
        not_present4_3 = request.form["not_present4_3"]
        not_present4_4 = request.form["not_present4_4"]
        not_present4_5 = request.form["not_present4_5"]

        final_out_all_1, final_out_all_2, final_out_all_3, final_out_all_4, final_out_all_5, final_out_end_all \
        ,final_out1_1, final_out1_2, final_out1_3, final_out1_4, final_out1_5, final_out_end1 \
        ,final_out2_1, final_out2_2, final_out2_3, final_out2_4, final_out2_5, final_out_end2 \
        ,final_out3_1, final_out3_2, final_out3_3, final_out3_4, final_out3_5, final_out_end3 \
        ,final_out4_1, final_out4_2, final_out4_3, final_out4_4, final_out4_5, final_out_end4 = quordle_solver_split(df, \
        must_not_be_present1, present1_1, present1_2, present1_3, present1_4, present1_5, not_present1_1, not_present1_2, not_present1_3, not_present1_4, not_present1_5, \
        must_not_be_present2, present2_1, present2_2, present2_3, present2_4, present2_5, not_present2_1, not_present2_2, not_present2_3, not_present2_4, not_present2_5, \
        must_not_be_present3, present3_1, present3_2, present3_3, present3_4, present3_5, not_present3_1, not_present3_2, not_present3_3, not_present3_4, not_present3_5, \
        must_not_be_present4, present4_1, present4_2, present4_3, present4_4, present4_5, not_present4_1, not_present4_2, not_present4_3, not_present4_4, not_present4_5)

        return render_template("quordle.html", \
            final_out_all_1=final_out_all_1, final_out_all_2=final_out_all_2, final_out_all_3=final_out_all_3, final_out_all_4=final_out_all_4, final_out_all_5=final_out_all_5, final_out_end_all=final_out_end_all \
            ,final_out1_1=final_out1_1, final_out1_2=final_out1_2, final_out1_3=final_out1_3, final_out1_4=final_out1_4, final_out1_5=final_out1_5, final_out_end1=final_out_end1 \
            ,final_out2_1=final_out2_1, final_out2_2=final_out2_2, final_out2_3=final_out2_3, final_out2_4=final_out2_4, final_out2_5=final_out2_5, final_out_end2=final_out_end2 \
            ,final_out3_1=final_out3_1, final_out3_2=final_out3_2, final_out3_3=final_out3_3, final_out3_4=final_out3_4, final_out3_5=final_out3_5, final_out_end3=final_out_end3 \
            ,final_out4_1=final_out4_1, final_out4_2=final_out4_2, final_out4_3=final_out4_3, final_out4_4=final_out4_4, final_out4_5=final_out4_5, final_out_end4=final_out_end4 \

            ,must_not_be_present1_val=must_not_be_present1, present1_1_val=present1_1, present1_2_val=present1_2, present1_3_val=present1_3, present1_4_val=present1_4, present1_5_val=present1_5 \
            ,not_present1_1_val=not_present1_1, not_present1_2_val=not_present1_2, not_present1_3_val=not_present1_3, not_present1_4_val=not_present1_4, not_present1_5_val=not_present1_5 \
            ,must_not_be_present2_val=must_not_be_present2, present2_1_val=present2_1, present2_2_val=present2_2, present2_3_val=present2_3, present2_4_val=present2_4, present2_5_val=present2_5 \
            ,not_present2_1_val=not_present2_1, not_present2_2_val=not_present2_2, not_present2_3_val=not_present2_3, not_present2_4_val=not_present2_4, not_present2_5_val=not_present2_5 \
            ,must_not_be_present3_val=must_not_be_present3, present3_1_val=present3_1, present3_2_val=present3_2, present3_3_val=present3_3, present3_4_val=present3_4, present3_5_val=present3_5 \
            ,not_present3_1_val=not_present3_1, not_present3_2_val=not_present3_2, not_present3_3_val=not_present3_3, not_present3_4_val=not_present3_4, not_present3_5_val=not_present3_5 \
            ,must_not_be_present4_val=must_not_be_present4, present4_1_val=present4_1, present4_2_val=present4_2, present4_3_val=present4_3, present4_4_val=present4_4, present4_5_val=present4_5 \
            ,not_present4_1_val=not_present4_1, not_present4_2_val=not_present4_2, not_present4_3_val=not_present4_3, not_present4_4_val=not_present4_4, not_present4_5_val=not_present4_5)
    else:
        return render_template("quordle.html")


@app.route("/fixer", methods=["POST", "GET"])
def run_wordle_fixer():
    if request.method == "POST":
        must_be_present = request.form["must_be_present"]
        final_out1, final_out2, final_out3, final_out4, final_out5 = find_word_with_letters(df, must_be_present)
        return render_template("fixer.html", final_out1=final_out1, final_out2=final_out2, final_out3=final_out3, final_out4=final_out4, final_out5=final_out5, must_be_present=must_be_present)
    else:
        return render_template("fixer.html")






if __name__ == "__main__":
    app.run(debug=True)