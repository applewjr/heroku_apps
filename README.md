# heroku_apps
https://www.jamesapplewhite.com

https://twitter.com/J_R_Applewhite

This is my central hub where I practice:
- Process automation and scheduling
- Data warehousing
- ETL
- Data analysis
- Web app development
- UI
- Tools:
  - Python
  - Flask
  - HTML
  - CSS
  - MySQL/SQLite
  - Heroku
  - Git


### Notable sections

[Wordle Solver](https://www.jamesapplewhite.com/wordle)
- Python function used to input Wordle guesses and find the ideal next guess

[Quordle Solver](https://www.jamesapplewhite.com/quordle)
- Python function which built on the Wordle logic to solve 4 Wordle puzzles at once
- This involved a totally new strategy where you minimize the weights of used letters to ensure a wider variety of letter usage

[Blossom/Spelling Bee Solver](https://www.jamesapplewhite.com/blossom)
- Another Python function that ingests all english words to find the largest words based on inclusion criteria

[Any Word Finder](https://www.jamesapplewhite.com/any_word)
- An expansion of the Blossom solver beyond the game. Find any word you are thinking of by entering a variety of criteria to describe the word

[Common Denominator](https://www.jamesapplewhite.com/common_denominator)
- The desire to create this function stemmed from writing fuzzy logic SQL. Sometimes you have a list of strings you would like to include, a list of strings you do not want to include, and you want to write the ideal matching string to perfectly capture the ideal population

[To Do List (MySQL)](https://www.jamesapplewhite.com/task_mysql)
- Leverages Heroku's JawsDB MySQL add-on
- The user adds, edits, or deletes tasks
- All changes are maintained permanently in a MySQL database

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

[Stock Analysis](https://www.jamesapplewhite.com/stock_analysis)
- Derived from my first large personal Python project in 2021. Optimize stock/crypto buying by adhering to the principles of mean reversion. Trendlines have short term variance but tend to run back to the mean. This function finds when a stock is trading notably lower than the projected price and provides triggers for when it may be advisable to buy in more aggressively (Not trading advice! *I went to healthcare school*)

[Twitter Stock Bot](https://twitter.com/J_R_Applewhite)
- Each morning, a series of .py scripts are automatically run
- They analyze a set of stocks and crypto
- Then post the results to Twitter
- The automation utilizes Heroku's scheduler add-on