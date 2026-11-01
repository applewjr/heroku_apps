# JJ Apps

The code behind [jamesapplewhite.com](https://www.jamesapplewhite.com): a Flask site that is the front end for my data projects, plus a growing set of word game solvers I build for fun.

[Live site](https://www.jamesapplewhite.com) | [LinkedIn](https://www.linkedin.com/in/j-applewhite/) | [MTG price bot on Bluesky](https://bsky.app/profile/mtgprices.bsky.social)

## About

I'm a data analyst working toward data engineering, and casual data science liker. A pipeline that ends as a few database tables nobody sees isn't much fun, so this site is where my backend projects show up: scheduled jobs load the data, and these pages put it in front of people.

The web app itself is a hobby. I'm not trying to become a software engineer, I just enjoy building the place where the data lands. The first page this site deployed, in October 2022, was a Wordle solver, and the solvers have kept coming since.

Building tools for people I'll never meet has convinced me that UX matters. Nobody is there to walk them through it, so the page itself has to show how it works, without a wall of instructions, and it has to be easy to use. My shorthand when I build is "1 click is unfortunate, 2 clicks is a tragedy." It's an exaggeration, but it keeps me honest about friction.

## Data projects

### Magic: The Gathering price tracker

[Price page](https://www.jamesapplewhite.com/mtg) | [Pipeline repo](https://github.com/applewjr/mtg-prices) | [Streamlit dashboard](https://mtg-price-dashboard.streamlit.app)

A daily AWS pipeline tracks prices for about 100K cards and has built up 30M+ price records, for under $1 a month.

- **Ingest:** EventBridge starts a Step Functions workflow every morning. Lambda functions pull Scryfall's bulk card data and convert it to Parquet.
- **Store:** Apache Iceberg tables on S3, queried with Athena. Moving from DynamoDB to Athena cut costs by more than 90%. Snowpipe loads the same data into Snowflake for queries across a card's full history, key files replicate to a backup region, and SNS reports on each run.
- **Serve:** CloudFront publishes a fresh CSV each day. This site reads it for the price page (the biggest risers and fallers), a scheduled job in this repo posts the day's outliers to Bluesky, and the Streamlit dashboard queries Snowflake for set-level trends.

### YouTube Trending

[Trending page](https://www.jamesapplewhite.com/youtube_trending)

A daily job pulls trending videos from the YouTube Data API into MySQL (it began as a web scraper), and a second job keeps a backup copy of the table. The page shows today's top videos and channels, the biggest climbers, and 30-day trends by category.

### Espresso optimizer (private)

A personal tool for dialing in espresso. I log each shot in a Google Form, and a scheduled job pulls the responses from Google Sheets, cleans them, and loads them into MySQL. A k-nearest-neighbors regression, tuned with grid search and k-fold cross-validation, learns how brew settings relate to taste scores (bitterness, sourness, channeling and more), then recommends settings for the next shot. 3D scatter plots help explore the results. The model work is the heaviest thing the app does, so the tool sits behind a password and isn't linked here.

### Site analytics and monitoring

The site is also its own data source.

- **Visit logging:** page views go onto an in-memory queue, and a background thread writes them to MySQL in batches, so a slow database never slows a page down. Wordle and Antiwordle solver requests go to Redis streams, and a scheduled job loads them into MySQL.
- **Dashboards:** SQL views in [mysql_views/dashboard/](mysql_views/dashboard/) feed a password-protected ops dashboard whose panels are defined in one YAML file. A daily digest email carries the same panels.
- **Monitoring:** hourly health checks, also defined in YAML, cover data freshness, database health, and page responses. A problem prints an alert line that Papertrail turns into a notification, and a heartbeat line raises an alert if the checks themselves stop running. Scheduled jobs run through a wrapper that turns a failure into an alert. Details are in [monitoring/README.md](monitoring/README.md).

### Crowd-sourced word lists

No dictionary matches every game exactly, so several solvers learn from their players. Players can flag a word a game rejected or suggest one it accepted, and once enough different players agree, that game's word list changes. Each game keeps its own list, and every vote is logged so the rules can be checked against real history. Players' entries also build each day's board, so later visitors can load today's puzzle instead of typing it in.

## Game solvers

These are the hobby part of the hobby.

| Solver | What it does |
|---|---|
| [Wordle](https://www.jamesapplewhite.com/wordle) | Best next guesses from your clues, ranked by information gain (entropy) with a word popularity prior |
| [Antiwordle](https://www.jamesapplewhite.com/antiwordle) | Guesses that keep you alive as long as possible without hitting the answer |
| [Quordle & Sequence](https://www.jamesapplewhite.com/quordle) | Four Wordle boards at once, plus Sequence mode, where the boards open one at a time |
| [Blossom](https://www.jamesapplewhite.com/blossom) | The highest-scoring words for the petal puzzle |
| [Smush](https://www.jamesapplewhite.com/smush) | Ranks every word by points, tracks letter wear as you play, and plans the endgame |
| [Ribbit](https://www.jamesapplewhite.com/ribbit) | Rebuild the 4x4 board and trace every word through it, longest first |
| [Guess The Phrase](https://www.jamesapplewhite.com/guess_the_phrase) | Spoiler-free hints for the daily phrase, one letter at a time, from a bank of 660K+ phrases built from Wikidata, Wiktionary, and WordNet |
| [Chainle](https://www.jamesapplewhite.com/chainle) | The word that links your clues, found with ConceptNet Numberbatch word vectors and WordNet, with a hint before the answer |
| [Wordiply](https://www.jamesapplewhite.com/wordiply) | The longest words containing a given starter |
| [Last Letter Helper](https://www.jamesapplewhite.com/fixer) | Words that contain specific letters |

## Tech stack

- **Web:** Python, Flask, Jinja, gunicorn, plain HTML, CSS, and JavaScript
- **Data:** MySQL (JawsDB), Redis Cloud, pandas, NumPy
- **Modeling:** scikit-learn, SciPy, matplotlib
- **MTG pipeline:** AWS (EventBridge, Step Functions, Lambda, S3, Athena, Iceberg, CloudFront, SNS), Snowflake, Streamlit
- **APIs:** YouTube Data API, Google Sheets, Scryfall, Bluesky
- **Ops:** Heroku and Heroku Scheduler, GitHub Actions, Papertrail

## Repo layout

| Path | What's there |
|---|---|
| `app.py`, `config.py`, `extensions.py`, `data.py`, `helpers.py` | App setup, config, database access, caching, logging, shared data and helpers |
| `routes/` | Flask blueprints, one per area of the site |
| `functions/` | Solver and analysis logic |
| `crowd.py` | Shared crowd word-voting logic |
| `templates/` | Jinja pages |
| `scheduled_tasks_*/` | Scheduled jobs: MTG Bluesky posts, YouTube loads, espresso import, Redis to MySQL |
| `monitoring/` | Health checks, alerts, job wrapper, daily digest |
| `mysql_tables/`, `mysql_views/` | Table changes and dashboard views |
| `datasets/` | Word lists, phrase and Chainle data, dashboard and health check YAML |
| `scripts/` | Offline data builds, backtests, and evaluations |
| `tests/` | pytest suite |
| `_archived_scheduled_tasks_*/` | Retired jobs (Riot Games, Spotify, a Twitter bot, web scraping), kept for reference |

## Licensing

The code is MIT licensed ([LICENSE](LICENSE)). Two sets of data files in `datasets/` are not, and carry their sources' licences instead:

- The Guess The Phrase files (`phrase_*`): CC BY-SA 4.0, CC BY 4.0, and the WordNet 3.0 licence, listed in [datasets/PHRASE_BANK.md](datasets/PHRASE_BANK.md).
- The Chainle solver's `chainle_data.npz`: CC BY-SA 4.0 and the WordNet 3.0 licence, listed in [datasets/CHAINLE_DATA.md](datasets/CHAINLE_DATA.md).
