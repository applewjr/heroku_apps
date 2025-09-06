# Production Web Applications & Data Tools

*Collection of 10+ live applications serving real users with automated data pipelines*

🌐 **Live Portfolio**: <a href="https://www.jamesapplewhite.com" target="_blank" rel="noopener noreferrer">jamesapplewhite.com</a>  
🐦 **Automated Social**: <a href="https://twitter.com/J_R_Applewhite" target="_blank" rel="noopener noreferrer">@J_R_Applewhite</a>

## Featured Applications

### 📈 <a href="https://www.jamesapplewhite.com/mtg" target="_blank" rel="noopener noreferrer">Magic: The Gathering Price Tracker</a>
*Production-scale financial data platform processing 90K+ records daily at <$1/month operational cost*
- **Full Pipeline**: <a href="https://github.com/applewjr/mtg-prices" target="_blank" rel="noopener noreferrer">AWS Architecture</a> | <a href="https://mtg-price-dashboard.streamlit.app" target="_blank" rel="noopener noreferrer">Streamlit Dashboard</a>
- **Cost Optimization**: Reduced infrastructure costs 90%+ through strategic migration (DynamoDB → Athena)
- **Scalable Architecture**: EMR Serverless/PySpark processing with Apache Iceberg tables for efficient querying
- **Data Pipeline**: Daily ETL via Step Functions orchestrating Lambda functions and Snowflake integration
- **Multi-Platform Distribution**: CloudFront CDN serving Flask web app, Streamlit analytics, and automated Twitter bot
- **Enterprise Features**: Cross-region S3 replication, SNS monitoring, and comprehensive error handling

### ☕ <a href="https://www.jamesapplewhite.com/espresso" target="_blank" rel="noopener noreferrer">Espresso Optimizer</a>
*Machine learning-powered brewing parameter recommendations*
- **Tech Stack**: Google Sheets API, KNN algorithm, MySQL, 3D visualization
- Real-time data import and cleaning pipeline from Google Forms
- Lightweight ML model optimized for live predictions with small datasets
- Dynamic data visualization with interactive 3D scatter plots
- *Current optimal recipe: 17.1g in, 32.9g out, 27 seconds*

### 🎯 <a href="https://www.jamesapplewhite.com/blossom" target="_blank" rel="noopener noreferrer">Blossom Game Solver</a>
*High-traffic word game optimization tool*
- **Impact**: 2,200+ Google clicks in 28 days (April 2024)
- Dynamic scoring algorithm processing entire English dictionary
- Responsive UI with real-time score calculations
- Demonstrates organic traffic growth through practical utility

### 🎮 <a href="https://www.jamesapplewhite.com/wordle" target="_blank" rel="noopener noreferrer">Wordle Solver</a>
*Streamlined puzzle assistance with usage analytics*
- Optimal next-guess algorithm with clean, intuitive interface
- Live logging pipeline: User actions → Redis Cloud → MySQL
- Low-latency data capture for user behavior analysis

## Data & Automation Projects

### 🔄 <a href="https://github.com/applewjr/heroku_apps/blob/main/scheduled_tasks_lol/lol_data_import.py" target="_blank" rel="noopener noreferrer">API-to-MySQL ETL Pipeline</a>
*Automated daily data ingestion for Power BI dashboard*
- Riot Games API integration with automated error handling
- Multi-table data parsing: players, champions, match history
- Automated email notifications for pipeline status monitoring

### 📊 <a href="https://www.jamesapplewhite.com/data_summary" target="_blank" rel="noopener noreferrer">CSV Analytics Tool</a>
*Instant statistical analysis for uploaded datasets*
- Comprehensive stats: descriptive, correlation, distribution analysis
- Advanced imputation: MICE for numeric, frequency-based for categorical
- Multiple linear regression with R-squared and p-value reporting
- Automated correlation heatmaps and significance testing

### 📺 <a href="https://www.jamesapplewhite.com/youtube_trending" target="_blank" rel="noopener noreferrer">YouTube Trending Analytics</a>
*Daily trending content analysis and aggregation*
- Migration from web scraping → official YouTube API for better data quality
- Automated daily collection with MySQL storage and email reporting
- Trend analysis across time periods with historical comparisons

### 🐕 <a href="https://www.jamesapplewhite.com/dogs" target="_blank" rel="noopener noreferrer">Dog Counter</a>
*Because sometimes you just need to count dogs*

## Technical Stack

### **Languages & Frameworks**
- **Python**: Flask, Pandas, Scikit-learn, NumPy, Matplotlib, SciPy
- **Frontend**: HTML, CSS, JavaScript
- **Data**: MySQL, SQLite, Redis Cloud

### **Infrastructure & Tools**
- **Deployment**: Heroku (with scheduler add-ons)
- **APIs**: Google Sheets, YouTube, Riot Games, Scryfall
- **Cloud Services**: <a href="https://github.com/applewjr/mtg-prices" target="_blank" rel="noopener noreferrer">AWS (Full architecture)</a>
- **Data Formats**: JSON, YAML, CSV, Parquet
- **Monitoring**: Papertail, SNS notifications

### **Key Capabilities**
- **Process Automation**: Scheduled pipelines with error handling
- **Data Engineering**: ETL processes, API integrations, data warehousing  
- **Machine Learning**: KNN, regression analysis, statistical modeling
- **Web Development**: Full-stack applications with responsive UI
- **Data Visualization**: Interactive charts, correlation analysis, 3D plotting

## Architecture Highlights
- **Scalability**: Serverless architecture handling 90K+ daily records
- **Reliability**: Automated monitoring, error handling, and notification systems
- **User Experience**: Clean interfaces optimized for practical utility
- **Real Impact**: Applications serving actual users with measurable engagement