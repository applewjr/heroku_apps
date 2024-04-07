# heroku_apps
https://www.jamesapplewhite.com

https://twitter.com/J_R_Applewhite

This is my central hub where I practice:
- Process automation and scheduling
- Data warehousing
- ETL
- Data analysis
- Web app development
- Web scraping
- UI
- Tools:
  - Python
  - Flask
  - HTML
  - CSS
  - MySQL/SQLite
  - Redis Cloud
  - Heroku
  - Git
  - JSON/YAML


### Notable sections

[Espresso Optimizer](https://www.jamesapplewhite.com/espresso)
- Implemented Google Forms and Google Sheets API integration for real-time data import to allow for immediate analysis
- Applied data cleaning techniques to prepare espresso brewing data from Google Sheets, ensuring high-quality inputs for model training and analysis
- Utilized the K-Nearest Neighbors (KNN) machine learning algorithm, including tuning hyperparameters and evaluating model performance, to predict optimal espresso brewing parameters
- Engineered a system for dynamic data visualization, including 3D scatter plots, to facilitate in-depth exploratory data analysis and insights discovery
- Designed and implemented a data pipeline that incorporates data import, cleaning, analysis, and visualization
- Long term data storage managed in MySQL
- In case anyone is curious, my ideal medium roast espresso as of this writing is 17.1 grams in, 32.9 grams out, in 27 seconds

[Wordle Game Solver](https://www.jamesapplewhite.com/wordle)
- Python function used to input Wordle guesses and find the ideal next guess
- A UI designed for a streamlined, simple workflow (We're cheating at Wordle after all. No reason to make it difficult to use)
- Live, low latency logging of user entries into a Redis Cloud instance
- Daily transfer of logging data from Redis to MySQL to long term storage

[Blossom Game Solver](https://www.jamesapplewhite.com/blossom)
- Another Python function that ingests all English words to find the largest words based on inclusion criteria
- Implemented a revamped UI that dynamically calculates the final scores based on the current game dynamics
- As of April 2024, this page has driven my website usage through Google to up 2,000 clicks in 28 days

[API to MySQL Automated ETL](https://github.com/applewjr/heroku_apps/blob/main/lol/lol_data_import.py)
- Pulls fresh data each morning from Riot Games' API and inserts or replaces into a MySQL instance. Used for interfacing with a Power BI dashboard
- Pulls player (summoner), character (champion), and match data
- Parse all data from source JSON into a series of dataframes, taking care to replace values that don't always show up
- Auto emails sent to myself and my customer with pass/fail status messages

[Stock Analysis](https://www.jamesapplewhite.com/stock_analysis)
- Derived from my first large personal Python project in 2021. Optimize stock/crypto buying by adhering to the principles of mean reversion. Trendlines have short term variance but tend to run back to the mean. This function finds when a stock is trading notably lower than the projected price and provides triggers for when it may be advisable to buy in more aggressively (Not trading advice! *I went to healthcare school*)

[Twitter Stock Bot](https://twitter.com/J_R_Applewhite)
- Each morning, a series of .py scripts are automatically run
- They analyze a set of stocks and crypto
- Then post the results to Twitter
- The automation utilizes Heroku's scheduler add-on

[YouTube Trending Summary](https://www.jamesapplewhite.com/youtube_trending)
- Automated web scraping of YouTube's trending page (top 10 videos) every day at 12 am PST
  - I have since dropped the web scraping and switched to the official YouTube API for more complete data capture
- Present the data in aggregate by running queries against my MySQL table
- All logs are maintained and constructed as an email to myself

[Auto Data Summary](https://www.jamesapplewhite.com/data_summary)
- Ingest a CSV and return summary statistics
- Includes:
  - Data type, nulls, min, max
  - Standard deviation, skewness, kurtosis
  - Dynamically arranged most significant Pearson correlations present
  - Heatmap correlation
- Not shown because it demolished my web app's memory allowance
  - Impute missing numeric values with MICE
  - Impute missing non-numeric values with a frequency approach
  - Run multiple linear regressions, treating each numeric column as the dependent variable. Display R-squared, P-values, intercept, coefficients

[Dog Counter](https://www.jamesapplewhite.com/dogs)
- Live fast, count dogs
