import pandas as pd
import numpy as np
from datetime import datetime
import mysql.connector
import os
from riotwatcher import LolWatcher, ApiError
import requests
import time


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

start_num = 0
count_num = 50


#########################
#########################
##### prep
#########################
#########################

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
selected_columns = [
     'leagueId'
    ,'queueType'
    ,'tier'
    ,'ranking'
    ,'summonerId'
    ,'summonerName'
    ,'leaguePoints'
    ,'wins'
    ,'losses'
    ,'veteran'
    ,'inactive'
    ,'freshBlood'
    ,'hotStreak'
    ,'pulled_dt'
    ,'pulled_date'
    ]
summoner_export = pd.DataFrame({col: summoner_export.get(col, np.nan) for col in selected_columns})
summoner_export = summoner_export.reset_index(drop=True)

table_name = 'lol_summoner'
insert_or_replace = 'INSERT' # 'REPLACE'
insert_script = generate_insert_script(summoner_export, table_name, insert_or_replace)
print(f"{table_name}: {len(summoner_export) = }")

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
    # time.sleep(0.25)
cursor.close()
conn.close()
print(f"{table_name}: {commit_count = }, {fail_count = }")
# summoner_export.to_csv('C:/Users/james/OneDrive/Desktop/summoner.csv', index=False)



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
selected_columns = [
     'Name'
    ,'attack'
    ,'defense'
    ,'magic'
    ,'difficulty'
    ,'hp'
    ,'hpperlevel'
    ,'mp'
    ,'mpperlevel'
    ,'movespeed'
    ,'armor'
    ,'armorperlevel'
    ,'spellblock'
    ,'spellblockperlevel'
    ,'attackrange'
    ,'hpregen'
    ,'hpregenperlevel'
    ,'mpregen'
    ,'mpregenperlevel'
    ,'crit'
    ,'critperlevel'
    ,'attackdamage'
    ,'attackdamageperlevel'
    ,'attackspeedperlevel'
    ,'attackspeed'
    ,'pulled_dt'
    ,'pulled_date'
    ]
champion_export = pd.DataFrame({col: champion_export.get(col, np.nan) for col in selected_columns})
champion_export = champion_export.reset_index(drop=True)

table_name = 'lol_champion'
insert_or_replace = 'REPLACE' # 'INSERT'
insert_script = generate_insert_script(champion_export, table_name, insert_or_replace)
print(f"{table_name}: {len(champion_export) = }")

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
    # time.sleep(0.25)
cursor.close()
conn.close()
print(f"{table_name}: {commit_count = }, {fail_count = }")
# champion_export.to_csv('C:/Users/james/OneDrive/Desktop/champion.csv', index=False)



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
print(f"pre cross ref: {len(all_match_ids) = }")

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
print(f"post cross ref: {len(all_match_ids) = }")

master_match_data = {}
for match_id in all_match_ids:
    api_url = f"https://americas.api.riotgames.com/lol/match/v5/matches/{match_id}"
    api_url = api_url + '?api_key=' + LOL_API_KEY
    resp = requests.get(api_url)
    match_data = resp.json()
    master_match_data[match_id] = match_data
    time.sleep(1.25)
print(f"{len(master_match_data) = }")



#########################
#########################
##### lol_match
#########################
#########################

if len(master_match_data) == 0:
    print("skip lol_match, no new data to run")
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
    selected_columns = [
         'gameCreation'
        ,'gameDuration'
        ,'gameEndTimestamp'
        ,'gameId'
        ,'gameMode'
        ,'gameName'
        ,'gameStartTimestamp'
        ,'gameType'
        ,'gameVersion'
        ,'mapId'
        ,'platformId'
        ,'queueId'
        ,'tournamentCode'
        ,'matchId'
        ]
    match_export = pd.DataFrame({col: match_export.get(col, np.nan) for col in selected_columns})
    match_export = match_export.reset_index(drop=True)

    table_name = 'lol_match'
    insert_or_replace = 'INSERT' # 'REPLACE'
    insert_script = generate_insert_script(match_export, table_name, insert_or_replace)
    print(f"{table_name}: {len(match_export) = }")

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
        # time.sleep(0.25)
    cursor.close()
    conn.close()
    
    print(f"{table_name}: {commit_count = }, {fail_count = }")
    # match_export.to_csv('C:/Users/james/OneDrive/Desktop/match.csv', index=False)



#########################
#########################
##### lol_participants_info
#########################
#########################

if len(master_match_data) == 0:
    print("skip lol_participants_info, no new data to run")
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
    selected_columns = [
         'matchId'
        ,'summonerId'
        ,'assists'
        ,'baronKills'
        ,'basicPings'
        ,'bountyLevel'
        ,'challenges'
        ,'champExperience'
        ,'champLevel'
        ,'championId'
        ,'championName'
        ,'championTransform'
        ,'consumablesPurchased'
        ,'damageDealtToBuildings'
        ,'damageDealtToObjectives'
        ,'damageDealtToTurrets'
        ,'damageSelfMitigated'
        ,'deaths'
        ,'detectorWardsPlaced'
        ,'doubleKills'
        ,'dragonKills'
        ,'eligibleForProgression'
        ,'firstBloodAssist'
        ,'firstBloodKill'
        ,'firstTowerAssist'
        ,'firstTowerKill'
        ,'gameEndedInEarlySurrender'
        ,'gameEndedInSurrender'
        ,'goldEarned'
        ,'goldSpent'
        ,'individualPosition'
        ,'inhibitorKills'
        ,'inhibitorTakedowns'
        ,'inhibitorsLost'
        ,'item0'
        ,'item1'
        ,'item2'
        ,'item3'
        ,'item4'
        ,'item5'
        ,'item6'
        ,'itemsPurchased'
        ,'killingSprees'
        ,'kills'
        ,'lane'
        ,'largestCriticalStrike'
        ,'largestKillingSpree'
        ,'largestMultiKill'
        ,'longestTimeSpentLiving'
        ,'magicDamageDealt'
        ,'magicDamageDealtToChampions'
        ,'magicDamageTaken'
        ,'neutralMinionsKilled'
        ,'nexusKills'
        ,'nexusLost'
        ,'nexusTakedowns'
        ,'objectivesStolen'
        ,'objectivesStolenAssists'
        ,'participantId'
        ,'pentaKills'
        ,'perks'
        ,'physicalDamageDealt'
        ,'physicalDamageDealtToChampions'
        ,'physicalDamageTaken'
        ,'profileIcon'
        ,'puuid'
        ,'quadraKills'
        ,'riotIdName'
        ,'riotIdTagline'
        ,'role'
        ,'sightWardsBoughtInGame'
        ,'spell1Casts'
        ,'spell2Casts'
        ,'spell3Casts'
        ,'spell4Casts'
        ,'summoner1Casts'
        ,'summoner1Id'
        ,'summoner2Casts'
        ,'summoner2Id'
        ,'summonerLevel'
        ,'summonerName'
        ,'teamEarlySurrendered'
        ,'teamId'
        ,'teamPosition'
        ,'timeCCingOthers'
        ,'timePlayed'
        ,'totalDamageDealt'
        ,'totalDamageDealtToChampions'
        ,'totalDamageShieldedOnTeammates'
        ,'totalDamageTaken'
        ,'totalHeal'
        ,'totalHealsOnTeammates'
        ,'totalMinionsKilled'
        ,'totalTimeCCDealt'
        ,'totalTimeSpentDead'
        ,'totalUnitsHealed'
        ,'tripleKills'
        ,'trueDamageDealt'
        ,'trueDamageDealtToChampions'
        ,'trueDamageTaken'
        ,'turretKills'
        ,'turretTakedowns'
        ,'turretsLost'
        ,'unrealKills'
        ,'visionScore'
        ,'visionWardsBoughtInGame'
        ,'wardsKilled'
        ,'wardsPlaced'
        ,'win'
        ]
    participants_info_export = pd.DataFrame({col: participants_info_export.get(col, np.nan) for col in selected_columns})
    participants_info_export = participants_info_export.reset_index(drop=True)

    table_name = 'lol_participants_info'
    insert_or_replace = 'INSERT' # 'REPLACE'
    insert_script = generate_insert_script(participants_info_export, table_name, insert_or_replace)
    print(f"{table_name}: {len(participants_info_export) = }")

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
        # time.sleep(0.25)
    cursor.close()
    conn.close()
    
    print(f"{table_name}: {commit_count = }, {fail_count = }")
    # participants_info_export.to_csv('C:/Users/james/OneDrive/Desktop/participants_info.csv', index=False)



#########################
#########################
##### lol_participants_challenges
#########################
#########################

if len(master_match_data) == 0:
    print("skip lol_participants_challenges, no new data to run")
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

    participants_challenges_export = participants_challenges_export.rename(columns={'12AssistStreakCount': 'assistStreakCount12', 'killingSprees': 'killingSpreesChallenges', \
        'turretTakedowns': 'turretTakedownsChallenges', 'challenges': 'challengesChallenges'})
    selected_columns = [
         'matchId'
        ,'summonerId'
        ,'assistStreakCount12'
        ,'abilityUses'
        ,'acesBefore15Minutes'
        ,'alliedJungleMonsterKills'
        ,'baronTakedowns'
        ,'blastConeOppositeOpponentCount'
        ,'bountyGold'
        ,'buffsStolen'
        ,'completeSupportQuestInTime'
        ,'controlWardsPlaced'
        ,'damagePerMinute'
        ,'damageTakenOnTeamPercentage'
        ,'dancedWithRiftHerald'
        ,'deathsByEnemyChamps'
        ,'dodgeSkillShotsSmallWindow'
        ,'doubleAces'
        ,'dragonTakedowns'
        ,'earliestDragonTakedown'
        ,'earlyLaningPhaseGoldExpAdvantage'
        ,'effectiveHealAndShielding'
        ,'elderDragonKillsWithOpposingSoul'
        ,'elderDragonMultikills'
        ,'enemyChampionImmobilizations'
        ,'enemyJungleMonsterKills'
        ,'epicMonsterKillsNearEnemyJungler'
        ,'epicMonsterKillsWithin30SecondsOfSpawn'
        ,'epicMonsterSteals'
        ,'epicMonsterStolenWithoutSmite'
        ,'firstTurretKilled'
        ,'flawlessAces'
        ,'fullTeamTakedown'
        ,'gameLength'
        ,'getTakedownsInAllLanesEarlyJungleAsLaner'
        ,'goldPerMinute'
        ,'hadOpenNexus'
        ,'immobilizeAndKillWithAlly'
        ,'initialBuffCount'
        ,'initialCrabCount'
        ,'jungleCsBefore10Minutes'
        ,'junglerTakedownsNearDamagedEpicMonster'
        ,'kTurretsDestroyedBeforePlatesFall'
        ,'kda'
        ,'killAfterHiddenWithAlly'
        ,'killParticipation'
        ,'killedChampTookFullTeamDamageSurvived'
        ,'killingSpreesChallenges'
        ,'killsNearEnemyTurret'
        ,'killsOnOtherLanesEarlyJungleAsLaner'
        ,'killsOnRecentlyHealedByAramPack'
        ,'killsUnderOwnTurret'
        ,'killsWithHelpFromEpicMonster'
        ,'knockEnemyIntoTeamAndKill'
        ,'landSkillShotsEarlyGame'
        ,'laneMinionsFirst10Minutes'
        ,'laningPhaseGoldExpAdvantage'
        ,'legendaryCount'
        ,'lostAnInhibitor'
        ,'maxCsAdvantageOnLaneOpponent'
        ,'maxKillDeficit'
        ,'maxLevelLeadLaneOpponent'
        ,'mejaisFullStackInTime'
        ,'moreEnemyJungleThanOpponent'
        ,'multiKillOneSpell'
        ,'multiTurretRiftHeraldCount'
        ,'multikills'
        ,'multikillsAfterAggressiveFlash'
        ,'mythicItemUsed'
        ,'outerTurretExecutesBefore10Minutes'
        ,'outnumberedKills'
        ,'outnumberedNexusKill'
        ,'perfectDragonSoulsTaken'
        ,'perfectGame'
        ,'pickKillWithAlly'
        ,'playedChampSelectPosition'
        ,'poroExplosions'
        ,'quickCleanse'
        ,'quickFirstTurret'
        ,'quickSoloKills'
        ,'riftHeraldTakedowns'
        ,'saveAllyFromDeath'
        ,'scuttleCrabKills'
        ,'skillshotsDodged'
        ,'skillshotsHit'
        ,'snowballsHit'
        ,'soloBaronKills'
        ,'soloKills'
        ,'stealthWardsPlaced'
        ,'survivedSingleDigitHpCount'
        ,'survivedThreeImmobilizesInFight'
        ,'takedownOnFirstTurret'
        ,'takedowns'
        ,'takedownsAfterGainingLevelAdvantage'
        ,'takedownsBeforeJungleMinionSpawn'
        ,'takedownsFirstXMinutes'
        ,'takedownsInAlcove'
        ,'takedownsInEnemyFountain'
        ,'teamBaronKills'
        ,'teamDamagePercentage'
        ,'teamElderDragonKills'
        ,'teamRiftHeraldKills'
        ,'teleportTakedowns'
        ,'tookLargeDamageSurvived'
        ,'turretPlatesTaken'
        ,'turretTakedownsChallenges'
        ,'turretsTakenWithRiftHerald'
        ,'twentyMinionsIn3SecondsCount'
        ,'twoWardsOneSweeperCount'
        ,'unseenRecalls'
        ,'visionScoreAdvantageLaneOpponent'
        ,'visionScorePerMinute'
        ,'wardTakedowns'
        ,'wardTakedownsBefore20M'
        ,'wardsGuarded'
        ,'junglerKillsEarlyJungle'
        ,'killsOnLanersEarlyJungleAsJungler'
        ,'controlWardTimeCoverageInRiverOrEnemyHalf'
        ,'baronBuffGoldAdvantageOverThreshold'
        ,'earliestBaron'
        ,'firstTurretKilledTime'
        ,'soloTurretsLategame'
        ,'shortestTimeToAceFromFirstTakedown'
        ,'fastestLegendary'
        ,'highestChampionDamage'
        ,'highestWardKills'
        ,'highestCrowdControlScore'
        ,'fasterSupportQuestCompletion'
        ,'thirdInhibitorDestroyedTime'
        ,'hadAfkTeammate'
        ,'earliestElderDragon'
        ,'threeWardsOneSweeperCount'
        ,'challengesChallenges'
        ]
    participants_challenges_export = pd.DataFrame({col: participants_challenges_export.get(col, np.nan) for col in selected_columns})
    participants_challenges_export = participants_challenges_export.reset_index(drop=True)

    table_name = 'lol_participants_challenges'
    insert_or_replace = 'INSERT' # 'REPLACE'
    insert_script = generate_insert_script(participants_challenges_export, table_name, insert_or_replace)
    print(f"{table_name}: {len(participants_challenges_export) = }")

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
        # time.sleep(0.25)
    cursor.close()
    conn.close()
    
    print(f"{table_name}: {commit_count = }, {fail_count = }")
    # participants_challenges_export.to_csv('C:/Users/james/OneDrive/Desktop/participants_challenges.csv', index=False)

print("^-^")
