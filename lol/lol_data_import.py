import pandas as pd
import numpy as np
from datetime import datetime
import mysql.connector
import os
from riotwatcher import LolWatcher, ApiError
import requests
import time
import json
import smtplib
from email.mime.text import MIMEText


if 'IS_HEROKU' in os.environ:
    # Running on Heroku, load values from Heroku Config Vars
    config = {
        'user': os.environ.get('jawsdb_user'),
        'password': os.environ.get('jawsdb_pass'),
        'host': os.environ.get('jawsdb_host'),
        'database': os.environ.get('jawsdb_db'),
        'raise_on_warnings': True
        }
    LOL_API_KEY = os.environ.get('LOL_API_KEY')
    LOL_SUMMONER = os.environ.get('LOL_SUMMONER')
    LOL_REGION = os.environ.get('LOL_REGION')
    GMAIL_PASS = os.environ.get('GMAIL_PASS')
else:
    # Running locally, load values from secret_pass.py
    import sys
    script_directory = os.path.dirname(os.path.abspath(__file__))
    root_directory = os.path.dirname(script_directory)
    sys.path.append(root_directory)
    import secret_pass
    config = {
        'user': secret_pass.mysql_user,
        'password': secret_pass.mysql_pass,
        'host': secret_pass.mysql_host,
        'database': secret_pass.mysql_bd,
        'raise_on_warnings': True
        }
    LOL_API_KEY = secret_pass.LOL_API_KEY
    LOL_SUMMONER = secret_pass.LOL_SUMMONER
    LOL_REGION = secret_pass.LOL_REGION
    GMAIL_PASS = secret_pass.GMAIL_PASS

start_num = 0
count_num = 50

gmail_sender_email = 'james.r.applewhite@gmail.com'
gmail_receiver_email = 'james.r.applewhite@gmail.com'
gmail_subject = 'lol_data_import.py'
gmail_list = []

def print_and_append(statement):
    print(statement)
    gmail_list.append(statement)


#########################
#########################
##### prep
#########################
#########################

APP_ROOT = os.path.dirname(os.path.abspath(__file__))

file_path = os.path.join(APP_ROOT, 'cols_summoner.txt')
with open(file_path, 'r') as file:
    cols_summoner = [line.strip() for line in file]

file_path = os.path.join(APP_ROOT, 'cols_champion.txt')
with open(file_path, 'r') as file:
    cols_champion = [line.strip() for line in file]

file_path = os.path.join(APP_ROOT, 'cols_match.txt')
with open(file_path, 'r') as file:
    cols_match = [line.strip() for line in file]

file_path = os.path.join(APP_ROOT, 'cols_participants_info.txt')
with open(file_path, 'r') as file:
    cols_participants_info = [line.strip() for line in file]

file_path = os.path.join(APP_ROOT, 'cols_participants_challenges.txt')
with open(file_path, 'r') as file:
    cols_participants_challenges = [line.strip() for line in file]



def generate_insert_script(df, table_name, insert_or_replace):
    statements = {}
    headers = df.columns.tolist()
    for index, row in df.iterrows():
        insert_script = f"{insert_or_replace} INTO {table_name} "
        column_names = ', '.join(headers)
        insert_script += f"({column_names}) VALUES ("
        values = []
        for value in row:
            if pd.isnull(value):
                values.append('NULL')  # Replace NaN values with NULL
            else:
                values.append(f"'{value}'")
        insert_script += f"{', '.join(values)});"
        statements[index] = insert_script
    return statements

def convert_epoch_to_datetime(df):
    for column in df.columns:
        if df[column].dtype == 'int64' and df[column].min() > 1000000000000:
            df[column] = pd.to_datetime(df[column], unit='ms').dt.strftime('%Y-%m-%d %H:%M:%S')
    return df

lol_watcher = LolWatcher(LOL_API_KEY)
summoner = lol_watcher.summoner.by_name(LOL_REGION, LOL_SUMMONER)
puuid = summoner['puuid']



#########################
#########################
##### lol_summoner
#########################
#########################

summoner_export = lol_watcher.league.by_summoner(LOL_REGION, summoner['id'])
summoner_export = pd.DataFrame(summoner_export)
summoner_export['pulled_dt'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
summoner_export['pulled_date'] = datetime.now().strftime('%Y-%m-%d')
summoner_export = summoner_export.rename(columns={'rank': 'ranking'})

# summoner_export = summoner_export[[cols_summoner]]
# cols_existing = [col for col in cols_summoner if col in summoner_export.columns]
# summoner_export = summoner_export[[cols_existing]]

summoner_export = pd.DataFrame({col: summoner_export.get(col, np.nan) for col in cols_summoner})
summoner_export = summoner_export.reset_index(drop=True)

table_name = 'lol_summoner'
insert_or_replace = 'INSERT' # 'REPLACE'
insert_script = generate_insert_script(summoner_export, table_name, insert_or_replace)
print_and_append(f"{table_name}: {len(summoner_export) = }")

conn = mysql.connector.connect(**config)
cursor = conn.cursor()
commit_count = 0
fail_count = 0
for key in insert_script.keys():
    try:
        cursor.execute(insert_script[key])
        conn.commit()
        commit_count += 1
    except mysql.connector.Error as err:
        fail_count += 1
        print_and_append("Error on import:", str(err))
cursor.close()
conn.close()
print_and_append(f"{table_name}: {commit_count = }, {fail_count = }")



#########################
#########################
##### lol_champion
#########################
#########################

versions = lol_watcher.data_dragon.versions_for_region(LOL_REGION)
champions_version = versions['n']['champion']
current_champ_list = lol_watcher.data_dragon.champions(champions_version)
data = current_champ_list['data']
rows = []
for champion, details in data.items():
    row = {
        'Name': champion,
        'attack': details['info']['attack'],
        'defense': details['info']['defense'],
        'magic': details['info']['magic'],
        'difficulty': details['info']['difficulty']
    }
    rows.append(row)
champion_info = pd.DataFrame(rows)
rows = []
for champion, details in data.items():
    row = {
        'Name': champion,
        'hp': details['stats']['hp'],
        'hpperlevel': details['stats']['hpperlevel'],
        'mp': details['stats']['mp'],
        'mpperlevel': details['stats']['mpperlevel'],
        'movespeed': details['stats']['movespeed'],
        'armor': details['stats']['armor'],
        'armorperlevel': details['stats']['armorperlevel'],
        'spellblock': details['stats']['spellblock'],
        'spellblockperlevel': details['stats']['spellblockperlevel'],
        'attackrange': details['stats']['attackrange'],
        'hpregen': details['stats']['hpregen'],
        'hpregenperlevel': details['stats']['hpregenperlevel'],
        'mpregen': details['stats']['mpregen'],
        'mpregenperlevel': details['stats']['mpregenperlevel'],
        'crit': details['stats']['crit'],
        'critperlevel': details['stats']['critperlevel'],
        'attackdamage': details['stats']['attackdamage'],
        'attackdamageperlevel': details['stats']['attackdamageperlevel'],
        'attackspeedperlevel': details['stats']['attackspeedperlevel'],
        'attackspeed': details['stats']['attackspeed']
    }
    rows.append(row)
champion_stats = pd.DataFrame(rows)
champion_export = pd.concat([champion_info, champion_stats.drop(columns='Name')], axis=1)
champion_export['pulled_dt'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
champion_export['pulled_date'] = datetime.now().strftime('%Y-%m-%d')

# champion_export = champion_export[[cols_champion]]
# cols_existing = [col for col in cols_champion if col in champion_export.columns]
# champion_export = champion_export[[cols_existing]]

champion_export = pd.DataFrame({col: champion_export.get(col, np.nan) for col in cols_champion})
champion_export = champion_export.reset_index(drop=True)

table_name = 'lol_champion'
insert_or_replace = 'REPLACE' # 'INSERT'
insert_script = generate_insert_script(champion_export, table_name, insert_or_replace)
print_and_append(f"{table_name}: {len(champion_export) = }")

conn = mysql.connector.connect(**config)
cursor = conn.cursor()
commit_count = 0
fail_count = 0
for key in insert_script.keys():
    try:
        cursor.execute(insert_script[key])
        conn.commit()
        commit_count += 1
    except mysql.connector.Error as err:
        fail_count += 1
        print_and_append("Error on import:", str(err))
cursor.close()
conn.close()
print_and_append(f"{table_name}: {commit_count = }, {fail_count = }")



#########################
#########################
##### match prep
#########################
#########################

api_url = f"https://americas.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start={start_num}&count={count_num}"
api_url = api_url + '&api_key=' + LOL_API_KEY
resp = requests.get(api_url)
match_ids = resp.json()
all_match_ids = match_ids
print_and_append(f"pre cross ref: {len(all_match_ids) = }")

# only return the matchIDs that are not already in the db
conn = mysql.connector.connect(**config)
cursor = conn.cursor()
cursor.execute("""SELECT matchId FROM lol_match;""")
result = cursor.fetchall()
conn.commit()
cursor.close()
conn.close()
mysql_matchid = pd.DataFrame(result, columns=["matchId"])
all_match_ids_set = set(all_match_ids) # Convert the list of IDs to a set for faster membership checking
filtered_ids = [id for id in all_match_ids_set if id not in mysql_matchid["matchId"].values] # Filter the IDs that are in all_match_ids but not in mysql_matchid
all_match_ids = [str(id) for id in filtered_ids] # Convert the filtered IDs to a list of strings
print_and_append(f"post cross ref: {len(all_match_ids) = }")

master_match_data = {}
for match_id in all_match_ids:
    api_url = f"https://americas.api.riotgames.com/lol/match/v5/matches/{match_id}"
    api_url = api_url + '?api_key=' + LOL_API_KEY
    resp = requests.get(api_url)
    match_data = resp.json()
    master_match_data[match_id] = match_data
    time.sleep(1.25)
print_and_append(f"{len(master_match_data) = }")



#########################
#########################
##### lol_all_match
#########################
#########################

if len(master_match_data) == 0:
    print_and_append("skip lol_all_match, no new data to run")
else:
    table_name = 'lol_all_match'
    insert_or_replace = 'INSERT' # 'REPLACE'
    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()
    query = f"{insert_or_replace} INTO {table_name} (matchId, allMatch) VALUES (%s, %s)"
    for match_id, match_data in master_match_data.items():
        match_id = str(match_id)
        cursor.execute(query, (match_id, json.dumps(match_data)))
    conn.commit()
    cursor.close()
    conn.close()
    # print_and_append(f"{table_name}: {commit_count = }, {fail_count = }")
    print_and_append("lol_all_match complete")



#########################
#########################
##### lol_match
#########################
#########################

if len(master_match_data) == 0:
    print_and_append("skip lol_match, no new data to run")
else:
    match_export = pd.DataFrame()  # Create an empty dataframe
    for match in master_match_data:
        info = master_match_data[match]['info'].copy()
        info.pop('participants')
        info.pop('teams')
        info = pd.DataFrame(info, index=[0])
        info['matchId'] = match
        match_export = pd.concat([match_export, info], ignore_index=True)
    match_export = convert_epoch_to_datetime(match_export)

    # match_export = match_export[[cols_match]]
    # cols_existing = [col for col in cols_match if col in match_export.columns]
    # match_export = match_export[[cols_existing]]

    match_export = pd.DataFrame({col: match_export.get(col, np.nan) for col in cols_match})
    match_export = match_export.reset_index(drop=True)

    table_name = 'lol_match'
    insert_or_replace = 'INSERT' # 'REPLACE'
    insert_script = generate_insert_script(match_export, table_name, insert_or_replace)
    print_and_append(f"{table_name}: {len(match_export) = }")

    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()
    commit_count = 0
    fail_count = 0
    for key in insert_script.keys():
        try:
            cursor.execute(insert_script[key])
            conn.commit()
            commit_count += 1
        except mysql.connector.Error as err:
            fail_count += 1
            print_and_append("Error on import:", str(err))
    cursor.close()
    conn.close()
    
    print_and_append(f"{table_name}: {commit_count = }, {fail_count = }")



#########################
#########################
##### lol_participants_info
#########################
#########################

if len(master_match_data) == 0:
    print_and_append("skip lol_participants_info, no new data to run")
else:
    participants_df = pd.DataFrame()
    participants_match = pd.DataFrame()
    participants_info = pd.DataFrame()

    for match in master_match_data:
        all_match_data = master_match_data[match]
        df = pd.DataFrame.from_dict(all_match_data['metadata'])
        participants_match = pd.concat([participants_match, df[['matchId']]])

    for match in master_match_data:
        all_match_data = master_match_data[match]
        match_data = all_match_data['info']['participants']
        df_list = []
        for data in match_data:
            df_list.append(pd.DataFrame(data, index=[0]))
        df = pd.concat(df_list, ignore_index=True)
        participants_info = pd.concat([participants_info, df])
        summ_id = participants_info[['summonerId']]

    participants_info_export = pd.concat([participants_match, participants_info], axis=1)

    # participants_info_export = participants_info_export[[cols_participants_info]]
    # cols_existing = [col for col in cols_participants_info if col in participants_info_export.columns]
    # participants_info_export = participants_info_export[[cols_existing]]

    participants_info_export = pd.DataFrame({col: participants_info_export.get(col, np.nan) for col in cols_participants_info})
    participants_info_export = participants_info_export.reset_index(drop=True)

    table_name = 'lol_participants_info'
    insert_or_replace = 'INSERT' # 'REPLACE'
    insert_script = generate_insert_script(participants_info_export, table_name, insert_or_replace)
    print_and_append(f"{table_name}: {len(participants_info_export) = }")

    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()
    commit_count = 0
    fail_count = 0
    for key in insert_script.keys():
        try:
            cursor.execute(insert_script[key])
            conn.commit()
            commit_count += 1
        except mysql.connector.Error as err:
            fail_count += 1
            print_and_append("Error on import:", str(err))
    cursor.close()
    conn.close()
    
    print_and_append(f"{table_name}: {commit_count = }, {fail_count = }")



#########################
#########################
##### lol_participants_challenges
#########################
#########################

if len(master_match_data) == 0:
    print_and_append("skip lol_participants_challenges, no new data to run")
else:
    participants_challenges = pd.DataFrame()
    for match in master_match_data:
        all_match_data = master_match_data[match]
        match_data = all_match_data['info']['participants']
        df_list = []
        for data in match_data:
            try:
                df_list.append(pd.DataFrame(data['challenges'], index=[0]))
            except KeyError:
                df_list.append(pd.DataFrame({'challenges': [np.nan]}))
        df = pd.concat(df_list, ignore_index=True)
        participants_challenges = pd.concat([participants_challenges, df])

    participants_challenges_export = pd.concat([participants_match, summ_id, participants_challenges], axis=1)
    participants_challenges_export = participants_challenges_export.rename(columns={
        '12AssistStreakCount': 'assistStreakCount12', \
        'killingSprees': 'killingSpreesChallenges', \
        'turretTakedowns': 'turretTakedownsChallenges', \
        'challenges': 'challengesChallenges'})

    # participants_challenges_export = participants_challenges_export[[cols_participants_challenges]]
    # cols_existing = [col for col in cols_participants_challenges if col in participants_challenges_export.columns]
    # participants_challenges_export = participants_challenges_export[[cols_existing]]

    participants_challenges_export = pd.DataFrame({col: participants_challenges_export.get(col, np.nan) for col in cols_participants_challenges})
    participants_challenges_export = participants_challenges_export.reset_index(drop=True)

    table_name = 'lol_participants_challenges'
    insert_or_replace = 'INSERT' # 'REPLACE'
    insert_script = generate_insert_script(participants_challenges_export, table_name, insert_or_replace)
    print_and_append(f"{table_name}: {len(participants_challenges_export) = }")

    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()
    commit_count = 0
    fail_count = 0
    for key in insert_script.keys():
        try:
            cursor.execute(insert_script[key])
            conn.commit()
            commit_count += 1
        except mysql.connector.Error as err:
            fail_count += 1
            print_and_append("Error on import:", str(err))
    cursor.close()
    conn.close()
    
    print_and_append(f"{table_name}: {commit_count = }, {fail_count = }")

print_and_append("^-^")

gmail_message = '\n'.join(gmail_list)
msg = MIMEText(gmail_message)
msg['Subject'] = gmail_subject
msg['From'] = gmail_sender_email
msg['To'] = gmail_receiver_email
with smtplib.SMTP('smtp.gmail.com', 587) as server:
    server.starttls()
    server.login(gmail_sender_email, GMAIL_PASS)
    server.sendmail(gmail_sender_email, gmail_receiver_email, msg.as_string())
    print('email sent')
