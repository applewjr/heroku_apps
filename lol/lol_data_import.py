import pandas as pd
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
    YOUTUBE_API = os.environ.get('YOUTUBE_API')
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
    LOL_PUUID = secret_pass.LOL_PUUID

start_num = 0
count_num = 20




def convert_epoch_to_datetime(df):
    for column in df.columns:
        if df[column].dtype == 'int64' and df[column].min() > 1000000000000:
            df[column] = pd.to_datetime(df[column], unit='ms').dt.strftime('%Y-%m-%d %H:%M:%S')
    return df



lol_watcher = LolWatcher(LOL_API_KEY)
summoner = lol_watcher.summoner.by_name(LOL_REGION, LOL_SUMMONER)


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
summoner_export = pd.DataFrame({col: summoner_export.get(col, -999) for col in selected_columns})

conn = mysql.connector.connect(**config)
cursor = conn.cursor()
table_name = 'lol_summoner'
insert_query = """
    INSERT INTO {} (
        leagueId,
        queueType,
        tier,
        ranking,
        summonerId,
        summonerName,
        leaguePoints,
        wins,
        losses,
        veteran,
        inactive,
        freshBlood,
        hotStreak,
        pulled_dt,
        pulled_date
        )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
            %s, %s, %s, %s, %s)
    """.format(table_name)
for r in summoner_export.itertuples(index=False):
    values = r[1:]
    try:
        cursor.execute(insert_query, values)
        conn.commit()
    except:
        print(f"Could not commit lol_summoner {r.leagueId}, {r.pulled_date}")
cursor.close()
conn.close()
print("Complete: lol_summoner")







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
champion_export = pd.DataFrame({col: champion_export.get(col, -999) for col in selected_columns})

conn = mysql.connector.connect(**config)
cursor = conn.cursor()
table_name = 'lol_champion'
insert_query = """
    REPLACE INTO {} (
        Name,
        attack,
        defense,
        magic,
        difficulty,
        hp,
        hpperlevel,
        mp,
        mpperlevel,
        movespeed,
        armor,
        armorperlevel,
        spellblock,
        spellblockperlevel,
        attackrange,
        hpregen,
        hpregenperlevel,
        mpregen,
        mpregenperlevel,
        crit,
        critperlevel,
        attackdamage,
        attackdamageperlevel,
        attackspeedperlevel,
        attackspeed,
        pulled_dt,
        pulled_date
        )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s)
    """.format(table_name)
for r in champion_export.itertuples(index=False):
    values = r[1:]
    try:
        cursor.execute(insert_query, values)
        conn.commit()
    except:
        print(f"Could not commit lol_champion {r.Name}, {r.pulled_date}")
cursor.close()
conn.close()
print("Complete: lol_champion")







api_url = f"https://americas.api.riotgames.com/lol/match/v5/matches/by-puuid/{LOL_PUUID}/ids?start={start_num}&count={count_num}"
api_url = api_url + '&api_key=' + LOL_API_KEY
resp = requests.get(api_url)
match_ids = resp.json()
all_match_ids = match_ids


master_match_data = {}
for match_id in all_match_ids:
    api_url = f"https://americas.api.riotgames.com/lol/match/v5/matches/{match_id}"
    api_url = api_url + '?api_key=' + LOL_API_KEY
    resp = requests.get(api_url)
    match_data = resp.json()
    master_match_data[match_id] = match_data
    time.sleep(1.25)
print(f"{len(master_match_data) = }")

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
match_export = pd.DataFrame({col: match_export.get(col, -999) for col in selected_columns})

conn = mysql.connector.connect(**config)
cursor = conn.cursor()
table_name = 'lol_match'
insert_query = """
    INSERT INTO {} (
        gameCreation,
        gameDuration,
        gameEndTimestamp,
        gameId,
        gameMode,
        gameName,
        gameStartTimestamp,
        gameType,
        gameVersion,
        mapId,
        platformId,
        queueId,
        tournamentCode,
        matchId,
        )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
            %s, %s, %s, %s)
    """.format(table_name)
for r in match_export.itertuples(index=False):
    values = r[1:]
    try:
        cursor.execute(insert_query, values)
        conn.commit()
    except:
        print(f"Could not commit lol_match {r.matchId}")
cursor.close()
conn.close()
print("Complete: lol_match")






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
participants_info_export = pd.DataFrame({col: participants_info_export.get(col, -999) for col in selected_columns})

conn = mysql.connector.connect(**config)
cursor = conn.cursor()
table_name = 'lol_participants_info'
insert_query = """
    INSERT INTO {} (
        matchId,
        summonerId,
        assists,
        baronKills,
        basicPings,
        bountyLevel,
        challenges,
        champExperience,
        champLevel,
        championId,
        championName,
        championTransform,
        consumablesPurchased,
        damageDealtToBuildings,
        damageDealtToObjectives,
        damageDealtToTurrets,
        damageSelfMitigated,
        deaths,
        detectorWardsPlaced,
        doubleKills,
        dragonKills,
        eligibleForProgression,
        firstBloodAssist,
        firstBloodKill,
        firstTowerAssist,
        firstTowerKill,
        gameEndedInEarlySurrender,
        gameEndedInSurrender,
        goldEarned,
        goldSpent,
        individualPosition,
        inhibitorKills,
        inhibitorTakedowns,
        inhibitorsLost,
        item0,
        item1,
        item2,
        item3,
        item4,
        item5,
        item6,
        itemsPurchased,
        killingSprees,
        kills,
        lane,
        largestCriticalStrike,
        largestKillingSpree,
        largestMultiKill,
        longestTimeSpentLiving,
        magicDamageDealt,
        magicDamageDealtToChampions,
        magicDamageTaken,
        neutralMinionsKilled,
        nexusKills,
        nexusLost,
        nexusTakedowns,
        objectivesStolen,
        objectivesStolenAssists,
        participantId,
        pentaKills,
        perks,
        physicalDamageDealt,
        physicalDamageDealtToChampions,
        physicalDamageTaken,
        profileIcon,
        puuid,
        quadraKills,
        riotIdName,
        riotIdTagline,
        role,
        sightWardsBoughtInGame,
        spell1Casts,
        spell2Casts,
        spell3Casts,
        spell4Casts,
        summoner1Casts,
        summoner1Id,
        summoner2Casts,
        summoner2Id,
        summonerLevel,
        summonerName,
        teamEarlySurrendered,
        teamId,
        teamPosition,
        timeCCingOthers,
        timePlayed,
        totalDamageDealt,
        totalDamageDealtToChampions,
        totalDamageShieldedOnTeammates,
        totalDamageTaken,
        totalHeal,
        totalHealsOnTeammates,
        totalMinionsKilled,
        totalTimeCCDealt,
        totalTimeSpentDead,
        totalUnitsHealed,
        tripleKills,
        trueDamageDealt,
        trueDamageDealtToChampions,
        trueDamageTaken,
        turretKills,
        turretTakedowns,
        turretsLost,
        unrealKills,
        visionScore,
        visionWardsBoughtInGame,
        wardsKilled,
        wardsPlaced,
        win
        )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """.format(table_name)
for r in participants_info_export.itertuples(index=False):
    values = r[1:]
    try:
        cursor.execute(insert_query, values)
        conn.commit()
    except:
        print(f"Could not commit lol_participants_info {r.matchId}, {r.summonerId}")
cursor.close()
conn.close()
print("Complete: lol_participants_info")






participants_challenges = pd.DataFrame()
for match in master_match_data:
    all_match_data = master_match_data[match]
    match_data = all_match_data['info']['participants']
    df_list = []
    for data in match_data:
        try:
            df_list.append(pd.DataFrame(data['challenges'], index=[0]))
        except KeyError:
            df_list.append(pd.DataFrame({'challenges': [-999]}))  # Insert -999 as the value
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
participants_challenges_export = pd.DataFrame({col: participants_challenges_export.get(col, -999) for col in selected_columns})


conn = mysql.connector.connect(**config)
cursor = conn.cursor()
table_name = 'lol_participants_challenges'
insert_query = """
    INSERT INTO {} (
        matchId,
        summonerId,
        assistStreakCount12,
        abilityUses,
        acesBefore15Minutes,
        alliedJungleMonsterKills,
        baronTakedowns,
        blastConeOppositeOpponentCount,
        bountyGold,
        buffsStolen,
        completeSupportQuestInTime,
        controlWardsPlaced,
        damagePerMinute,
        damageTakenOnTeamPercentage,
        dancedWithRiftHerald,
        deathsByEnemyChamps,
        dodgeSkillShotsSmallWindow,
        doubleAces,
        dragonTakedowns,
        earliestDragonTakedown,
        earlyLaningPhaseGoldExpAdvantage,
        effectiveHealAndShielding,
        elderDragonKillsWithOpposingSoul,
        elderDragonMultikills,
        enemyChampionImmobilizations,
        enemyJungleMonsterKills,
        epicMonsterKillsNearEnemyJungler,
        epicMonsterKillsWithin30SecondsOfSpawn,
        epicMonsterSteals,
        epicMonsterStolenWithoutSmite,
        firstTurretKilled,
        flawlessAces,
        fullTeamTakedown,
        gameLength,
        getTakedownsInAllLanesEarlyJungleAsLaner,
        goldPerMinute,
        hadOpenNexus,
        immobilizeAndKillWithAlly,
        initialBuffCount,
        initialCrabCount,
        jungleCsBefore10Minutes,
        junglerTakedownsNearDamagedEpicMonster,
        kTurretsDestroyedBeforePlatesFall,
        kda,
        killAfterHiddenWithAlly,
        killParticipation,
        killedChampTookFullTeamDamageSurvived,
        killingSpreesChallenges,
        killsNearEnemyTurret,
        killsOnOtherLanesEarlyJungleAsLaner,
        killsOnRecentlyHealedByAramPack,
        killsUnderOwnTurret,
        killsWithHelpFromEpicMonster,
        knockEnemyIntoTeamAndKill,
        landSkillShotsEarlyGame,
        laneMinionsFirst10Minutes,
        laningPhaseGoldExpAdvantage,
        legendaryCount,
        lostAnInhibitor,
        maxCsAdvantageOnLaneOpponent,
        maxKillDeficit,
        maxLevelLeadLaneOpponent,
        mejaisFullStackInTime,
        moreEnemyJungleThanOpponent,
        multiKillOneSpell,
        multiTurretRiftHeraldCount,
        multikills,
        multikillsAfterAggressiveFlash,
        mythicItemUsed,
        outerTurretExecutesBefore10Minutes,
        outnumberedKills,
        outnumberedNexusKill,
        perfectDragonSoulsTaken,
        perfectGame,
        pickKillWithAlly,
        playedChampSelectPosition,
        poroExplosions,
        quickCleanse,
        quickFirstTurret,
        quickSoloKills,
        riftHeraldTakedowns,
        saveAllyFromDeath,
        scuttleCrabKills,
        skillshotsDodged,
        skillshotsHit,
        snowballsHit,
        soloBaronKills,
        soloKills,
        stealthWardsPlaced,
        survivedSingleDigitHpCount,
        survivedThreeImmobilizesInFight,
        takedownOnFirstTurret,
        takedowns,
        takedownsAfterGainingLevelAdvantage,
        takedownsBeforeJungleMinionSpawn,
        takedownsFirstXMinutes,
        takedownsInAlcove,
        takedownsInEnemyFountain,
        teamBaronKills,
        teamDamagePercentage,
        teamElderDragonKills,
        teamRiftHeraldKills,
        teleportTakedowns,
        tookLargeDamageSurvived,
        turretPlatesTaken,
        turretTakedownsChallenges,
        turretsTakenWithRiftHerald,
        twentyMinionsIn3SecondsCount,
        twoWardsOneSweeperCount,
        unseenRecalls,
        visionScoreAdvantageLaneOpponent,
        visionScorePerMinute,
        wardTakedowns,
        wardTakedownsBefore20M,
        wardsGuarded,
        junglerKillsEarlyJungle,
        killsOnLanersEarlyJungleAsJungler,
        controlWardTimeCoverageInRiverOrEnemyHalf,
        baronBuffGoldAdvantageOverThreshold,
        earliestBaron,
        firstTurretKilledTime,
        soloTurretsLategame,
        shortestTimeToAceFromFirstTakedown,
        fastestLegendary,
        highestChampionDamage,
        highestWardKills,
        highestCrowdControlScore,
        fasterSupportQuestCompletion,
        thirdInhibitorDestroyedTime,
        hadAfkTeammate,
        earliestElderDragon,
        threeWardsOneSweeperCount,
        challengesChallenges
        )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """.format(table_name)
for r in participants_challenges_export.itertuples(index=False):
    values = r[1:]
    try:
        cursor.execute(insert_query, values)
        conn.commit()
    except:
        print(f"Could not commit lol_participants_challenges {r.matchId}, {r.summonerId}")
cursor.close()
conn.close()
print("Complete: lol_participants_challenges")

print("^-^")
