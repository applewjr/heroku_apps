from flask import Flask, redirect, render_template, url_for, request, redirect, jsonify
import pandas as pd
import os
import math

import matplotlib.pyplot as plt
import datetime
from datetime import date
from datetime import datetime
import io
from PIL import Image
import base64

import yfinance as yf
import numpy as np
import time
pd.options.mode.chained_assignment = None  # default='warn'

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
            not_present1_val=not_present1, not_present2_val=not_present2, not_present3_val=not_present3, not_present4_val=not_present4, not_present5_val=not_present5, \
            suggested="Suggested word(s):")
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
            ,not_present4_1_val=not_present4_1, not_present4_2_val=not_present4_2, not_present4_3_val=not_present4_3, not_present4_4_val=not_present4_4, not_present4_5_val=not_present4_5 \
            ,suggested="Suggested word(s):", all_puzzle="All Puzzle:", puzzle_1="Puzzle 1:", puzzle_2="Puzzle 2:", puzzle_3="Puzzle 3:", puzzle_4="Puzzle 4:")
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


@app.route("/common_denominator", methods=["POST", "GET"])
def run_common_denominator():
    if request.method == "POST":
        min_match_len = request.form["min_match_len"]
        min_match_rate = request.form["min_match_rate"]
        beg_end_str_char = request.form["beg_end_str_char"]
        value_split_char = request.form["value_split_char"]
        user_match_entry = request.form["user_match_entry"]
        # user_nope_match_entry = request.form["user_nope_match_entry"]
        final_match_list, final_out, num_words_entered, comparisons = common_denominator(min_match_len, min_match_rate, beg_end_str_char, value_split_char, user_match_entry, "")
        return render_template("common_denominator.html", min_match_len_val=min_match_len, min_match_rate_val=min_match_rate, beg_end_str_char_val=beg_end_str_char, value_split_char_val=value_split_char, \
            user_match_entry_val=user_match_entry, \
            final_match_list=final_match_list, final_out=final_out, num_words_entered=num_words_entered, comparisons=comparisons, \
            num_word_count="Number of words submitted: ", num_run_count="Number of comparisons run: ", top="Top words: ", all="All words meeting min match rate: ")
    else:
        return render_template("common_denominator.html", min_match_len_val=3, min_match_rate_val=0.5, beg_end_str_char_val="|", value_split_char_val=",", \
            user_match_entry_val="Discectomy, Laminectomy, Foraminotomy, Corpectomy, Spinal (Lumbar) Fusion, Spinal Cord Stimulation", example=" (example set provided)")




@app.route("/wordle_example", methods=["POST", "GET"])
def run_wordle_example():
    if request.method == "POST":
        return render_template("wordle_example.html")
    else:
        return render_template("wordle_example.html")










##################################
##### stock analysis section #####
##################################

def stock_pred(stock_list_init: str, trade_type: str, contrib_amt_init: float, total_weeks: int, buyvalue: float, multiplier: float, nth_week: int, roll_days: str, trade_dow: str):

    stock_list_init = str(stock_list_init)
    trade_type = str(trade_type)
    contrib_amt_init = float(contrib_amt_init)
    total_weeks = int(total_weeks)
    buyvalue = float(buyvalue)
    multiplier = float(multiplier)
    nth_week = int(nth_week)
    roll_days = str(roll_days)
    trade_dow = str(trade_dow)

    # convert a multi stock function into a single stock function
    stock_list = [stock_list_init]
    contrib_amt = [contrib_amt_init]



    invest = float('inf')

    for ind, val in enumerate(stock_list):
        stock_list[ind] = stock_list[ind].upper()

    roll_stock_index = {'month': 21, 'quarter': 65, '2_quarter': 130, 'year': 260}
    roll_crypto = {'month': 30, 'quarter': 90, '2_quarter': 180, 'year': 365}
    roll_dict = {'stock': roll_stock_index, 'index': roll_stock_index, 'crypto': roll_crypto}
    roll_days = roll_dict[trade_type][roll_days]

    # number of years to visualize
    if trade_type == 'crypto':
        day_hist = (total_weeks*7)+roll_days+1 # choose for crypto ~ trading every day
    else:
        day_hist = (total_weeks*5)+roll_days+1 # choose for stocks ~ trading about 5 days per week
    # weeks to actually invest on
    invest_weeks = math.floor(total_weeks/nth_week)

    # duplicate contrib_amt for all stocks if only 1 listed
    if len(contrib_amt) == len(stock_list):
        pass
    elif len(contrib_amt) == 1: 
        contrib_amt = [contrib_amt[0] for x in enumerate(stock_list)]
    else:
        print('Incorrect length of contrib_amt. Make it match the length of the stock list or be 1 value')

    # check every 15 seconds for complete data
    # wait times should only happen for ~1-2 minutes after market open on trading days (right after 0630am PST)
    if trade_type == 'crypto' or trade_type == 'index':
        pass
    else:
        x = 0
        while x < 1:
            df_now = yf.download(
            tickers = stock_list
            ,period = '1d' # set for 'today' instead
            ,interval = '1m'
            )
            # ensures a single stock can pass through, not just 2+ 
            if len(stock_list) == 1:
                df_now[stock_list[0]] = df_now['Open']
                df_now = df_now[[stock_list[0]]]
            else:
                df_now = df_now['Open']
            df_now = df_now.head(1) # open for today
            df_now = df_now.fillna(0)
            # df_now['Open', 'AAPL'] = 0 # force a 0 for testing
            x = 1
            for i in stock_list:
                # x = x * int(df_now['Open'][i])
                x = x * int(df_now[i])
            if x == 0: # wait 15 seconds if data aren't complete
                time.sleep(15)
            else:
                if df_now.index.day == date.today().day:
                    print('Datetime of data available: ', datetime.now().strftime("%B %d, %Y %H:%M:%S"))
                else:
                    print('Warning, today\'s data not yet available')

    # check the traditional open price
    df_open_check = yf.download(
        tickers = stock_list
        # ,start = '2022-01-15'
        # ,end = '2022-01-18'
        ,period = str(day_hist) + 'd'
    )

    ### Overly complex way to pull data, but I have found that 'Open' prices are just a copy of the previous day for the first few minutes of the trading day
    ### This method pulls in the true Open prices for today much quicker (a couple minutes after 6:30am PST)
    if trade_type == 'crypto' or trade_type == 'index':
        df = yf.download(
            tickers = stock_list
            # ,start = '2022-01-15'
            # ,end = '2022-01-18'
            ,period = str(day_hist) + 'd'
        )
        # ensures a single crypto or index can pass through, not just 2+
        if len(stock_list) == 1:
            df[stock_list[0]] = df['Open']
            df = df[[stock_list[0]]]
        else:
            df = df['Open']
    else:
        # Pull all data except for today
        df_bulk = yf.download(
                tickers = stock_list
                # ,start = '2022-01-15'
                # ,end = '2022-01-18'
                ,period = str(day_hist) + 'd'
            )
        # ensures a single stock can pass through, not just 2+ 
        if len(stock_list) == 1:
            df_bulk[stock_list[0]] = df_bulk['Open']
            df_bulk = df_bulk[[stock_list[0]]]
        else:
            df_bulk = df_bulk['Open']
        df_good_index = df_bulk.copy() # used to grab the ideal index
        df_bulk.drop(df_bulk.tail(1).index,inplace=True) # bulk w/o the most recent day
        # join the data (index is still bad)
        df = pd.concat([df_bulk, df_now])
        # sub in a good index
        df = df.reindex_like(df_good_index)
        # sub in good open data for today
        for i in stock_list:
            df[i][len(df)-1] = df_now[i].copy()

    # add an index and useable date
    df['Index'] = np.arange(1,len(df)+1)
    df['date'] = df.index
    # error checking, if a stock doesn't have enough history based on the current needs
    nlist = []
    for i in stock_list:
        if pd.isna(df[i].iloc[0]) == True:
            nlist.append(i)
    if len(nlist) >0:
        print('Stocks with not enough history', nlist)
        for j in nlist:
            print(j, 'missing days:', df['Index'].count()-df[j].count())

    # establishing day of week, week number, trading day
    dow_dict = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4, 'Saturday': 5, 'Sunday': 6}
    # convert 'Today' to actual listed day of the week
    if trade_dow == 'Today':
        trade_dow = list(dow_dict.keys())[date.today().weekday()]
    df['dow'] = df['date'].dt.dayofweek
    if trade_type == 'crypto':
        trade_day_list = []
        for i in range(len(df)):
            if df['dow'][i] == dow_dict[trade_dow]:
                trade_day_list.append(1)
            else:
                trade_day_list.append(0)
        df['trade_day'] = trade_day_list
    elif trade_type != 'crypto' and (trade_dow == 'Saturday' or trade_dow == 'Sunday'):
        print('error - stocks not open on the weekend')
    else:
        for i in range(len(df)):
            df['dow_dynamic'] = df['dow']-dow_dict[trade_dow]
        for i in range(len(df)):
            if df['dow_dynamic'][i] < 0:
                df['dow_dynamic'][i] = df['dow_dynamic'][i]+5
        week_no_list = []
        trade_day_list = []
        week_no_var = 1
        trade_day_var = 0
        for i in range(len(df)):
            if i == 0:
                1
            elif df['dow_dynamic'].iloc[i] > df['dow_dynamic'].iloc[i-1]:
                week_no_var
                trade_day_var = 0
            else:
                week_no_var += 1
                trade_day_var = 1
            week_no_list.append(week_no_var)
            trade_day_list.append(trade_day_var)
        df['week_no'] = week_no_list
        df['trade_day'] = trade_day_list

    # make a list of indices that are the Mondays where the trade should take place
    # always starting with the most recent Monday
    df_trade_days = df['Index'][df['trade_day'] == 1]
    df_trade_days = df_trade_days.tail(total_weeks)
    df_trade_days = df_trade_days.tolist()
    def reverse(rev):
        rev.reverse()
        return rev
    df_trade_days = reverse(df_trade_days)
    nth_wk = df_trade_days[::nth_week]

    df_baseline_source = df.copy()

    # create 1 dataframe per invest week in a dictionary, each the length of the chosen invest period
    # 0 is the most recent
    dataframes = {}
    # for j in stock_list:
    for i in range(invest_weeks):
        x = nth_wk[i]-roll_days # roll_days sets how many rows in each df
        y = nth_wk[i]
        dataframes['data' + str(i)] = df.iloc[x:y]

    # create pred and pred/open list for each of the n dataframes
    # sub in -1 for all calc except the last row. Only the last row of each item in the dictionary will be used
    for j in stock_list:
        for e in range(len(dataframes)):
            nlist = []
            ylist = []
            y = dataframes['data' + str(e)][j]
            for i in range(1,len(dataframes['data0'])+1): # create pred
                if i == len(dataframes['data0']):
                    x = range(1,roll_days+1) # range must be 1-roll_days, not the auto implied 0-(roll_days-1)
                    m, b = np.polyfit(x, y, 1)
                    d = m*i+b
                    nlist.append(d)
                else:
                    nlist.append(-1) # Skip calculating every row except the last one. Only the last is used
            dataframes['data' + str(e)][j + ' pred'] = nlist
            for i in range(1,len(dataframes['data0'])+1): # create pred/open
                if i == len(dataframes['data0']):
                    d = (dataframes['data' + str(e)][j + ' pred'].iloc[i-1])/(dataframes['data' + str(e)][j].iloc[i-1])
                    ylist.append(d)
                else:
                    ylist.append(-1) # Skip calculating every row except the last one. Only the last is used
            dataframes['data' + str(e)][j + ' pred/open'] = ylist

    # pull the last 'open' and pred/open' from each dataframe in dataframes and make a new dataframe out of it
    # each row is the last open price in a given period and the final pred/open derived from the linear trendline
    df = pd.DataFrame()
    add_index = np.arange(1,len(dataframes)+1)
    df['Index'] = add_index
    for j in stock_list:
        nlist = []
        ylist = []
        zlist = []
        datelist = []
        for e in reversed(range(len(dataframes))):
            nlist.append(dataframes['data' + str(e)][j + ' pred/open'].iloc[roll_days-1])
            ylist.append(dataframes['data' + str(e)][j].iloc[roll_days-1])
            zlist.append(dataframes['data' + str(e)][j + ' pred'].iloc[roll_days-1])
            datelist.append(dataframes['data' + str(e)]['date'].iloc[roll_days-1])
        df[j] = pd.DataFrame(ylist)
        df[j + ' pred'] = pd.DataFrame(zlist)
        df[j + ' pred/open'] = pd.DataFrame(nlist)
        df['date'] = pd.DataFrame(datelist)

    # determine the weeks where pred/open is >1 and therefore they are better weeks to buy in
    # steady stocks could be at about 50/50 but stocks exponentially rising could have open to 0 pred/open > 1
    for j in stock_list:
        nlist = []
        for i in range(len(df)):
            if df[j + ' pred/open'].iloc[i] >= 1:
                nlist.append(1)
            else:
                nlist.append(0)
        df[j + ' >1'] = nlist
    # square the pred/open number as a more extreme option for calculations
    for j in stock_list:
        df[j +' pred/open2'] = df[j +' pred/open']**2 # make the value differences a little more pronounced

    # Create all of the strategies to test 
    for j, z in zip(stock_list, contrib_amt):
    # opt5
        df[j +' opt5'] = 0
        df[j +' opt5_stk'] = 0
        v = invest
        for i in range(len(df)):
            if df[j +' pred/open2'].iloc[i] < buyvalue:
                df[j +' opt5'].iloc[i] = z
            else:
                df[j +' opt5'].iloc[i] = round(z * df[j +' pred/open2'].iloc[i] * multiplier,2)
            df[j +' opt5_stk'].iloc[i] = df[j +' opt5'].iloc[i]/df[j].iloc[i]
            v -= z*df[j +' pred/open2'].iloc[i] ### isn't correct for this algorithm, don't worry about it while I'm going with inf invest
            if i == (len(df)-1):
                t = i
            else:
                t = i+1
            if v < z*df[j +' pred/open2'].iloc[t]:
                break

    graph_data = {}
    test_df = pd.DataFrame()
    for j in stock_list:
        graph_data[j] = pd.DataFrame(data={'date': df['date'], 'val': df[str(j)], 'pred': df[str(j) + ' pred']})








    ### duplicate contrib_amt for all stocks if only 1 listed
    if len(contrib_amt) == len(stock_list):
        pass
    elif len(contrib_amt) == 1: 
        contrib_amt = [contrib_amt[0] for x in enumerate(stock_list)]
    else:
        print('Incorrect length of contrib_amt. Make it match the length of the stock list or be 1 value')
        exit()

    ### pull most recent day
    if trade_type == 'crypto' or trade_type == 'index':
        pass
    else:
        x = 0
        while x < 1:
            df_now = yf.download(
            tickers = stock_list
            ,period = '1d' # set for 'today' instead
            ,interval = '1m'
            )

            # ensures a single stock can pass through, not just 2+ 
            if len(stock_list) == 1:
                df_now[stock_list[0]] = df_now['Open']
                df_now = df_now[[stock_list[0]]]
            else:
                df_now = df_now['Open']

            df_now = df_now.head(1) # open for today
            df_now = df_now.fillna(0)

            x = 1
            for i in stock_list:
                x = x * int(df_now[i])

            if x == 0: # wait 15 seconds if data aren't complete
                time.sleep(15)

    # Overly complex way to pull data, but I have found that 'Open' prices are just a 
    # copy of the previous day for the first few minutes of the trading day
    # This method pulls in the true Open prices for today much quicker (a couple minutes after 6:30am PST)

    if trade_type == 'crypto' or trade_type == 'index':
        df = yf.download(
            tickers = stock_list
            ,period = str(roll_days) + 'd'
        )

        # ensures a single crypto or index can pass through, not just 2+ 
        if len(stock_list) == 1:
            df[stock_list[0]] = df['Open']
            df = df[[stock_list[0]]]
        else:
            df = df['Open']
    else:
        # Pull all data except for today
        df_bulk = yf.download(
                tickers = stock_list
                ,period = str(roll_days) + 'd'
            )

        # ensures a single stock can pass through, not just 2+ 
        if len(stock_list) == 1:
            df_bulk[stock_list[0]] = df_bulk['Open']
            df_bulk = df_bulk[[stock_list[0]]]
        else:
            df_bulk = df_bulk['Open']

        df_good_index = df_bulk.copy() # used to grab the ideal index
        df_bulk.drop(df_bulk.tail(1).index,inplace=True) # bulk w/o the most recent day

        # join the data (index is still bad)
        df = pd.concat([df_bulk, df_now])

        # sub in a good index
        df = df.reindex_like(df_good_index)

        # sub in good open data for today
        for i in stock_list:
            df[i][len(df)-1] = df_now[i].copy()
        

    # add an index and useable date
    df['Index'] = np.arange(1,len(df)+1)
    df['date'] = df.index

    # error checking, if a stock doesn't have enough history based on the current needs
    nlist = []
    for i in stock_list:
        if pd.isna(df[i].iloc[0]) == True:
            nlist.append(i)

    if len(nlist) >0:
        print('Stocks with not enough history', nlist)
        for j in nlist:
            print(j, 'missing days:', df['Index'].count()-df[j].count())
        exit() # Maybe not the best to add this. I still want to see the data

    # create pred and pred/open list for each of the n dataframes
    pred_open_list = []
    for j in stock_list:
        x = range(1,len(df[j])+1) # range must be 1-roll_days, not the auto implied 0-(roll_days-1)
        y = df[j]
        m, b = np.polyfit(x, y, 1)
        d = m*len(df[j])+b

        pred_open_list.append(d / df[j][len(df[j])-1] * d / df[j][len(df[j])-1])

    multiplier_list = []
    for i, j in enumerate(stock_list):
        if pred_open_list[i] > buyvalue:
            multiplier_list.append(1)
        else:
            multiplier_list.append(0)

    final_buy_list = []
    for i, j in enumerate(stock_list):
        if multiplier_list[i] == 0:
            final_buy_list.append(contrib_amt[i])
        else:
            final_buy_list.append(round(contrib_amt[i]*pred_open_list[i]*multiplier, 2))

    pred_open_out = f"{round(pred_open_list[0],4)}"
    final_buy_out = f"{final_buy_list[0]}"

    # this call to the dict df is not dynamic for multiple stock tickers
    data_out = graph_data[stock_list[0]]

    return pred_open_out, final_buy_out, data_out, 1







@app.route("/stock_analysis", methods=["POST", "GET"])
def home():
    if request.method == "POST":
        stock_list_init = request.form["stock_list_init"]

        trade_type = request.form["trade_type"]
        # trade_type = ['stock', 'crypto', 'index']
        # colour_return = request.form["colour_return"]

        contrib_amt_init = request.form["contrib_amt_init"]
        total_weeks = request.form["total_weeks"]
        buyvalue = request.form["buyvalue"]
        multiplier = request.form["multiplier"]
        nth_week = request.form["nth_week"]
        roll_days = request.form["roll_days"]
        trade_dow = request.form["trade_dow"]
        pred_open_out, final_buy_out, data_out, valid_graph = stock_pred(stock_list_init, trade_type, contrib_amt_init, total_weeks, buyvalue, multiplier, nth_week, roll_days, trade_dow)
        date = list(str(data_out['date']))
        date = list(range(1, len(data_out)+1))
        val = list(round(data_out['val'],2))
        pred = list(round(data_out['pred'],2))

        return render_template("stock_analysis.html", pred_open_out=pred_open_out, final_buy_out=final_buy_out, date=date, val=val, pred=pred, valid_graph=valid_graph, data_out=data_out, \
            stock_list_init_val=stock_list_init, trade_type_val=trade_type, contrib_amt_init_val=contrib_amt_init, \
            # stock_list_init_val=stock_list_init, contrib_amt_init_val=contrib_amt_init, \
            total_weeks_val=total_weeks, buyvalue_val=buyvalue, multiplier_val=multiplier, nth_week_val=nth_week, roll_days_val=roll_days, trade_dow_val=trade_dow)
    else:
        return render_template("stock_analysis.html", \
            stock_list_init_val='AAPL', trade_type_val='stock', contrib_amt_init_val=100, \
            # stock_list_init_val='AAPL', contrib_amt_init_val=100, \
            total_weeks_val=104, buyvalue_val=1.2, multiplier_val=5, nth_week_val=1, roll_days_val='quarter', trade_dow_val='Monday')




if __name__ == "__main__":
    app.run(debug=True)