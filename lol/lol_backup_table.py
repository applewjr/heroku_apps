import mysql.connector
import os
import smtplib
from email.mime.text import MIMEText
# import logging


if 'IS_HEROKU' in os.environ:
    # Running on Heroku, load values from Heroku Config Vars
    config = {
        'user': os.environ.get('jawsdb_user'),
        'password': os.environ.get('jawsdb_pass'),
        'host': os.environ.get('jawsdb_host'),
        'database': os.environ.get('jawsdb_db'),
        'raise_on_warnings': True
        }
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
    GMAIL_PASS = secret_pass.GMAIL_PASS

gmail_sender_email = 'james.r.applewhite@gmail.com'
gmail_receiver_email = 'james.r.applewhite@gmail.com'
gmail_subject = 'lol_backup_table.py'
gmail_list = []

def print_and_append(statement):
    print(statement)
    gmail_list.append(statement)


conn = mysql.connector.connect(**config)
cursor = conn.cursor()


cursor.execute("""DROP TABLE IF EXISTS lol_summoner_backup;""")
query = """
CREATE TABLE IF NOT EXISTS lol_summoner_backup (
id INT AUTO_INCREMENT PRIMARY KEY,
leagueId VARCHAR(50),
queueType VARCHAR(50),
tier VARCHAR(50),
ranking VARCHAR(10), -- changed from 'rank'
summonerId VARCHAR(64),
summonerName VARCHAR(64),
leaguePoints INT,
wins INT,
losses INT,
veteran VARCHAR(10),
inactive VARCHAR(10),
freshBlood VARCHAR(10),
hotStreak VARCHAR(10),
pulled_dt DATETIME, -- added to source data
pulled_date DATE, -- added to aource data
UNIQUE KEY unique_summoner (leagueId, pulled_date)
) AS SELECT * FROM lol_summoner;
"""
cursor.execute(query)
print_and_append("complete: backup of lol_summoner")



cursor.execute("""DROP TABLE IF EXISTS lol_champion_backup;""")
query = """
CREATE TABLE IF NOT EXISTS lol_champion_backup (
id INT AUTO_INCREMENT PRIMARY KEY,
Name VARCHAR(64),
attack INT,
defense INT,
magic INT,
difficulty INT,
hp INT,
hpperlevel INT,
mp INT,
mpperlevel DOUBLE,
movespeed INT,
armor INT,
armorperlevel DOUBLE,
spellblock INT,
spellblockperlevel DOUBLE,
attackrange INT,
hpregen DOUBLE,
hpregenperlevel DOUBLE,
mpregen DOUBLE,
mpregenperlevel DOUBLE,
crit INT,
critperlevel INT,
attackdamage INT,
attackdamageperlevel DOUBLE,
attackspeedperlevel DOUBLE,
attackspeed DOUBLE,
pulled_dt DATETIME,
pulled_date DATE,
UNIQUE KEY unique_champion (Name)
) AS SELECT * FROM lol_champion;
"""
cursor.execute(query)
print_and_append("complete: backup of lol_champion")



cursor.execute("""DROP TABLE IF EXISTS lol_all_match_backup;""")
query = """
CREATE TABLE IF NOT EXISTS lol_all_match_backup (
id INT AUTO_INCREMENT PRIMARY KEY,
matchId VARCHAR(30),
allMatch JSON,
UNIQUE KEY unique_all_match (matchID)
) AS SELECT * FROM lol_all_match;
"""
cursor.execute(query)
print_and_append("complete: backup of lol_all_match")



cursor.execute("""DROP TABLE IF EXISTS lol_match_backup;""")
query = """
CREATE TABLE IF NOT EXISTS lol_match_backup (
id INT AUTO_INCREMENT PRIMARY KEY,
gameCreation DATETIME,
gameDuration INT,
gameEndTimestamp DATETIME,
gameId BIGINT,
gameMode VARCHAR(30),
gameName VARCHAR(60),
gameStartTimestamp DATETIME,
gameType VARCHAR(30),
gameVersion VARCHAR(30),
mapId INT,
platformId VARCHAR(15),
queueId INT,
tournamentCode VARCHAR(60),
matchId VARCHAR(30),
UNIQUE KEY unique_match (matchID)
) AS SELECT * FROM lol_match;
"""
cursor.execute(query)
print_and_append("complete: backup of lol_match")



cursor.execute("""DROP TABLE IF EXISTS lol_participants_info_backup;""")
query = """
CREATE TABLE IF NOT EXISTS lol_participants_info_backup (
id INT AUTO_INCREMENT PRIMARY KEY,
matchId VARCHAR(30),
summonerId VARCHAR(100),
assists INT,
baronKills INT,
basicPings INT,
bountyLevel INT,
challenges DOUBLE,
champExperience INT,
champLevel INT,
championId INT,
championName VARCHAR(60),
championTransform INT,
consumablesPurchased INT,
damageDealtToBuildings INT,
damageDealtToObjectives INT,
damageDealtToTurrets INT,
damageSelfMitigated INT,
deaths INT,
detectorWardsPlaced INT,
doubleKills INT,
dragonKills INT,
eligibleForProgression VARCHAR(10),
firstBloodAssist VARCHAR(10),
firstBloodKill VARCHAR(10),
firstTowerAssist VARCHAR(10),
firstTowerKill VARCHAR(10),
gameEndedInEarlySurrender VARCHAR(10),
gameEndedInSurrender VARCHAR(10),
goldEarned INT,
goldSpent INT,
individualPosition VARCHAR(30),
inhibitorKills INT,
inhibitorTakedowns INT,
inhibitorsLost INT,
item0 INT,
item1 INT,
item2 INT,
item3 INT,
item4 INT,
item5 INT,
item6 INT,
itemsPurchased INT,
killingSprees INT,
kills INT,
lane VARCHAR(30),
largestCriticalStrike INT,
largestKillingSpree INT,
largestMultiKill INT,
longestTimeSpentLiving INT,
magicDamageDealt INT,
magicDamageDealtToChampions INT,
magicDamageTaken INT,
neutralMinionsKilled INT,
nexusKills INT,
nexusLost INT,
nexusTakedowns INT,
objectivesStolen INT,
objectivesStolenAssists INT,
participantId INT,
pentaKills INT,
perks DOUBLE,
physicalDamageDealt INT,
physicalDamageDealtToChampions INT,
physicalDamageTaken INT,
profileIcon INT,
puuid VARCHAR(100),
quadraKills INT,
riotIdName VARCHAR(100),
riotIdTagline VARCHAR(100),
role VARCHAR(30),
sightWardsBoughtInGame INT,
spell1Casts INT,
spell2Casts INT,
spell3Casts INT,
spell4Casts INT,
summoner1Casts INT,
summoner1Id INT,
summoner2Casts INT,
summoner2Id INT,
summonerLevel INT,
summonerName VARCHAR(64),
teamEarlySurrendered VARCHAR(10),
teamId INT,
teamPosition VARCHAR(30),
timeCCingOthers INT,
timePlayed INT,
totalDamageDealt INT,
totalDamageDealtToChampions INT,
totalDamageShieldedOnTeammates INT,
totalDamageTaken INT,
totalHeal INT,
totalHealsOnTeammates INT,
totalMinionsKilled INT,
totalTimeCCDealt INT,
totalTimeSpentDead INT,
totalUnitsHealed INT,
tripleKills INT,
trueDamageDealt INT,
trueDamageDealtToChampions INT,
trueDamageTaken INT,
turretKills INT,
turretTakedowns INT,
turretsLost INT,
unrealKills INT,
visionScore INT,
visionWardsBoughtInGame INT,
wardsKilled INT,
wardsPlaced INT,
win VARCHAR(10),
UNIQUE KEY participants_info (matchId, summonerId)
) AS SELECT * FROM lol_participants_info;
"""
cursor.execute(query)
print_and_append("complete: backup of lol_participants_info")



cursor.execute("""DROP TABLE IF EXISTS lol_participants_challenges_backup;""")
query = """
CREATE TABLE IF NOT EXISTS lol_participants_challenges_backup (
id INT AUTO_INCREMENT PRIMARY KEY,
matchId VARCHAR(30),
summonerId VARCHAR(100),
assistStreakCount12 DOUBLE, -- previously '12AssistStreakCount'
abilityUses DOUBLE,
acesBefore15Minutes DOUBLE,
alliedJungleMonsterKills DOUBLE,
baronTakedowns DOUBLE,
blastConeOppositeOpponentCount DOUBLE,
bountyGold DOUBLE,
buffsStolen DOUBLE,
completeSupportQuestInTime DOUBLE,
controlWardsPlaced DOUBLE,
damagePerMinute DOUBLE,
damageTakenOnTeamPercentage DOUBLE,
dancedWithRiftHerald DOUBLE,
deathsByEnemyChamps DOUBLE,
dodgeSkillShotsSmallWindow DOUBLE,
doubleAces DOUBLE,
dragonTakedowns DOUBLE,
earliestDragonTakedown DOUBLE,
earlyLaningPhaseGoldExpAdvantage DOUBLE,
effectiveHealAndShielding DOUBLE,
elderDragonKillsWithOpposingSoul DOUBLE,
elderDragonMultikills DOUBLE,
enemyChampionImmobilizations DOUBLE,
enemyJungleMonsterKills DOUBLE,
epicMonsterKillsNearEnemyJungler DOUBLE,
epicMonsterKillsWithin30SecondsOfSpawn DOUBLE,
epicMonsterSteals DOUBLE,
epicMonsterStolenWithoutSmite DOUBLE,
firstTurretKilled DOUBLE,
flawlessAces DOUBLE,
fullTeamTakedown DOUBLE,
gameLength DOUBLE,
getTakedownsInAllLanesEarlyJungleAsLaner DOUBLE,
goldPerMinute DOUBLE,
hadOpenNexus DOUBLE,
immobilizeAndKillWithAlly DOUBLE,
initialBuffCount DOUBLE,
initialCrabCount DOUBLE,
jungleCsBefore10Minutes DOUBLE,
junglerTakedownsNearDamagedEpicMonster DOUBLE,
kTurretsDestroyedBeforePlatesFall DOUBLE,
kda DOUBLE,
killAfterHiddenWithAlly DOUBLE,
killParticipation DOUBLE,
killedChampTookFullTeamDamageSurvived DOUBLE,
killingSpreesChallenges DOUBLE, -- previously 'killingSprees'
killsNearEnemyTurret DOUBLE,
killsOnOtherLanesEarlyJungleAsLaner DOUBLE,
killsOnRecentlyHealedByAramPack DOUBLE,
killsUnderOwnTurret DOUBLE,
killsWithHelpFromEpicMonster DOUBLE,
knockEnemyIntoTeamAndKill DOUBLE,
landSkillShotsEarlyGame DOUBLE,
laneMinionsFirst10Minutes DOUBLE,
laningPhaseGoldExpAdvantage DOUBLE,
legendaryCount DOUBLE,
lostAnInhibitor DOUBLE,
maxCsAdvantageOnLaneOpponent DOUBLE,
maxKillDeficit DOUBLE,
maxLevelLeadLaneOpponent DOUBLE,
mejaisFullStackInTime DOUBLE,
moreEnemyJungleThanOpponent DOUBLE,
multiKillOneSpell DOUBLE,
multiTurretRiftHeraldCount DOUBLE,
multikills DOUBLE,
multikillsAfterAggressiveFlash DOUBLE,
mythicItemUsed DOUBLE,
outerTurretExecutesBefore10Minutes DOUBLE,
outnumberedKills DOUBLE,
outnumberedNexusKill DOUBLE,
perfectDragonSoulsTaken DOUBLE,
perfectGame DOUBLE,
pickKillWithAlly DOUBLE,
playedChampSelectPosition DOUBLE,
poroExplosions DOUBLE,
quickCleanse DOUBLE,
quickFirstTurret DOUBLE,
quickSoloKills DOUBLE,
riftHeraldTakedowns DOUBLE,
saveAllyFromDeath DOUBLE,
scuttleCrabKills DOUBLE,
skillshotsDodged DOUBLE,
skillshotsHit DOUBLE,
snowballsHit DOUBLE,
soloBaronKills DOUBLE,
soloKills DOUBLE,
stealthWardsPlaced DOUBLE,
survivedSingleDigitHpCount DOUBLE,
survivedThreeImmobilizesInFight DOUBLE,
takedownOnFirstTurret DOUBLE,
takedowns DOUBLE,
takedownsAfterGainingLevelAdvantage DOUBLE,
takedownsBeforeJungleMinionSpawn DOUBLE,
takedownsFirstXMinutes DOUBLE,
takedownsInAlcove DOUBLE,
takedownsInEnemyFountain DOUBLE,
teamBaronKills DOUBLE,
teamDamagePercentage DOUBLE,
teamElderDragonKills DOUBLE,
teamRiftHeraldKills DOUBLE,
teleportTakedowns DOUBLE,
tookLargeDamageSurvived DOUBLE,
turretPlatesTaken DOUBLE,
turretTakedownsChallenges DOUBLE, -- previously 'turretTakedowns'
turretsTakenWithRiftHerald DOUBLE,
twentyMinionsIn3SecondsCount DOUBLE,
twoWardsOneSweeperCount DOUBLE,
unseenRecalls DOUBLE,
visionScoreAdvantageLaneOpponent DOUBLE,
visionScorePerMinute DOUBLE,
wardTakedowns DOUBLE,
wardTakedownsBefore20M DOUBLE,
wardsGuarded DOUBLE,
junglerKillsEarlyJungle DOUBLE,
killsOnLanersEarlyJungleAsJungler DOUBLE,
controlWardTimeCoverageInRiverOrEnemyHalf DOUBLE,
baronBuffGoldAdvantageOverThreshold DOUBLE,
earliestBaron DOUBLE,
firstTurretKilledTime DOUBLE,
soloTurretsLategame DOUBLE,
shortestTimeToAceFromFirstTakedown DOUBLE,
fastestLegendary DOUBLE,
highestChampionDamage DOUBLE,
highestWardKills DOUBLE,
highestCrowdControlScore DOUBLE,
fasterSupportQuestCompletion DOUBLE,
thirdInhibitorDestroyedTime DOUBLE,
hadAfkTeammate DOUBLE,
earliestElderDragon DOUBLE,
threeWardsOneSweeperCount DOUBLE,
challengesChallenges DOUBLE, -- previously 'challenges'
UNIQUE KEY participants_challenges (matchId, summonerId)
) AS SELECT * FROM lol_participants_challenges;
"""
cursor.execute(query)
print_and_append("complete: backup of lol_participants_challenges")

cursor.close()
conn.close()

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
