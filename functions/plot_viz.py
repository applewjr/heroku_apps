import mysql.connector
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from adjustText import adjust_text
import matplotlib
matplotlib.use('Agg') # allows Matplotlib to render plots directly to image files without requiring a GUI
import os

if 'IS_HEROKU' in os.environ:
    # Running on Heroku, load values from Heroku Config Vars
    config = {
        'user': os.environ.get('jawsdb_user'),
        'password': os.environ.get('jawsdb_pass'),
        'host': os.environ.get('jawsdb_host'),
        'database': os.environ.get('jawsdb_db'),
        'raise_on_warnings': True
        }
else:
    # Running locally, load values from secret_pass.py
    import secret_pass
    config = {
        'user': secret_pass.mysql_user,
        'password': secret_pass.mysql_pass,
        'host': secret_pass.mysql_host,
        'database': secret_pass.mysql_bd,
        'raise_on_warnings': True
        }

def format_tick_value(value):
    if abs(value) >= 1e9:
        return f'{value/1e9:.0f}B'
    elif abs(value) >= 1e6:
        return f'{value/1e6:.0f}M'
    elif abs(value) >= 1e3:
        return f'{value/1e3:.0f}K'
    else:
        return str(value)
        

def yt_stacked_bar_plot():
    cat_dict = {
        'Film & Animation': 'film_animation'
        ,'Autos & Vehicles': 'autos_vehicles'
        ,'Music': 'music'
        ,'Pets & Animals': 'pets_animals'
        ,'Sports': 'sports'
        ,'Short Movies': 'short_movies'
        ,'Travel & Events': 'travel_events'
        ,'Gaming': 'gaming'
        ,'Videoblogging': 'videoblogging'
        ,'People & Blogs': 'people_blogs'
        ,'Comedy': 'comedy'
        ,'Entertainment': 'entertainment'
        ,'News & Politics': 'news_politics'
        ,'Howto & Style': 'howto_style'
        ,'Education': 'education'
        ,'Science & Technology': 'science_technology'
        ,'Nonprofits & Activism': 'nonprofits_activism'
        ,'Movies': 'movies'
        ,'Anime/Animation': 'anime_animation'
        ,'Action/Adventure': 'action_adventure'
        ,'Classics': 'classics'
        ,'Documentary': 'documentary'
        ,'Drama': 'drama'
        ,'Family': 'family'
        ,'Foreign': 'foreign'
        ,'Horror': 'horror'
        ,'Sci-Fi/Fantasy': 'scifi_fantasy'
        ,'Thriller': 'thriller'
        ,'Shorts': 'shorts'
        ,'Shows': 'shows'
        ,'Trailers': 'trailers'
        }

    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()

    dynamic_query = []
    dynamic_query.append("""SELECT collected_date""")
    for key, value in cat_dict.items():
        dynamic_query.append(f""",COUNT(CASE WHEN category = '{key}' THEN category END) AS '{value}'""")
    dynamic_query.append("""
        FROM youtube_trending AS yt
        LEFT JOIN youtube_cat AS cat ON yt.vid_cat_id = cat.id
        GROUP BY collected_date
        ORDER BY collected_date DESC
        LIMIT 28; -- last 4 weeks
        """)
    sql_query = ' '.join(dynamic_query)

    # Execute the dynamic SQL query
    cursor.execute(sql_query)
    top_categories_data = cursor.fetchall()

    cursor.close()
    conn.close()

    # Convert the fetched data into a DataFrame
    columns = [desc for desc in cat_dict.values()]
    columns.insert(0, 'date')
    top_categories_df = pd.DataFrame(top_categories_data, columns=columns)
    # top_categories_df

    top_categories_df2 = top_categories_df.drop('date', axis=1)
    category_sums = top_categories_df2.sum(axis=0)
    sorted_categories = category_sums.sort_values(ascending=False)
    ordered_categories = sorted_categories.index.tolist()
    # print(ordered_categories)

    # Transpose the DataFrame to have dates as columns
    top_categories_df_transposed = top_categories_df.set_index('date').transpose()

    # Get the dates and categories from the DataFrame
    dates = top_categories_df['date'].tolist()[::-1]  # Reverse the order of dates
    # categories = top_categories_df.columns[1:]

    # Sort categories based on the sum of their values across all dates
    # category_sums = top_categories_df.iloc[:, 1:].sum().sort_values(ascending=False)
    top_categories = ordered_categories[:5]
    other_categories = ordered_categories[5:]

    # Create a new DataFrame with top categories and an "Other" category
    new_top_categories_df = top_categories_df.copy()
    new_top_categories_df['Other'] = top_categories_df[other_categories].sum(axis=1)
    new_top_categories = top_categories + ['Other']

    plt.clf()

    # Plot the stacked bar chart
    plt.figure(figsize=(10, 6))  # Adjust the figure size as per your preference

    bottom = np.zeros(len(dates))

    for category in new_top_categories:
        values = new_top_categories_df[category].tolist()[::-1]  # Reverse the order of values
        plt.bar(range(len(dates)), values, bottom=bottom, label=category)
        bottom += np.array(values)

    # Set the x-axis ticks and labels
    plt.xticks(range(len(dates)), dates, rotation=90, ha='right')

    # Add labels and title
    plt.xlabel('Date (Last 4 Weeks)')
    plt.ylabel('Video Count')
    plt.title('Top Categories by Date')

    # Adjust the legend placement
    plt.legend(loc='upper left', bbox_to_anchor=(1, 1))

    # Show the plot
    plt.tight_layout()

    return plt


def yt_video_scatter():
    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()

    sql_query = """
        SELECT MAX(vid_likes), MAX(vid_views), chnl
        FROM youtube_trending
        WHERE collected_date >= CURDATE() - INTERVAL 7 DAY
        GROUP BY chnl;
        """

    cursor.execute(sql_query)
    data = cursor.fetchall()

    cursor.close()
    conn.close()

    # Separate x, y, and names
    x = [item[0] for item in data]
    y = [item[1] for item in data]
    names = [item[2] for item in data]

    plt.clf()

    # Create scatter plot
    plt.scatter(x, y)
    plt.margins(0.1)

    # Find the indices of the 5 most extreme outliers
    extreme_indices = sorted(range(len(y)), key=lambda i: abs(y[i] - sum(y) / len(y)), reverse=True)[:5]

    # Plot the names for the 5 most extreme outliers
    texts = []
    for i in extreme_indices:
        texts.append(plt.text(x[i], y[i], names[i], ha='left', va='bottom'))

    # Adjust the text positions to avoid overlap
    adjust_text(texts, arrowprops=dict(arrowstyle='->', color='red'))

    # Set labels and title
    plt.xlabel('Video Likes')
    plt.ylabel('Video Views')
    plt.title('Channels with Trending Videos, Last 7 Days')

    # Format the x-axis ticks as abbreviated values
    plt.gca().xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: format_tick_value(x)))

    # Format the y-axis ticks as abbreviated values
    plt.gca().yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: format_tick_value(y)))

    # Show the plot
    # plt.show()
    return plt



def yt_chnl_scatter():
    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()

    sql_query = """
        SELECT  MAX(chnl_subs), MAX(chnl_views), chnl
        FROM youtube_trending
        WHERE collected_date >= CURDATE() - INTERVAL 7 DAY
        GROUP BY chnl;
        """

    cursor.execute(sql_query)
    data = cursor.fetchall()

    cursor.close()
    conn.close()

    # Separate x, y, and names
    x = [item[0] for item in data]
    y = [item[1] for item in data]
    names = [item[2] for item in data]

    plt.clf()

    # Create scatter plot
    plt.scatter(x, y)
    plt.margins(0.1)

    # Find the indices of the 5 most extreme outliers
    extreme_indices = sorted(range(len(y)), key=lambda i: abs(y[i] - sum(y) / len(y)), reverse=True)[:5]

    # Plot the names for the 5 most extreme outliers
    texts = []
    for i in extreme_indices:
        texts.append(plt.text(x[i], y[i], names[i], ha='left', va='bottom'))

    # Adjust the text positions to avoid overlap
    adjust_text(texts, arrowprops=dict(arrowstyle='->', color='red'))

    # Set labels and title
    plt.xlabel('Channel Subs')
    plt.ylabel('Channel Views')
    plt.title('Channels with Trending Videos, Last 7 Days')

    # Format the x-axis ticks as abbreviated values
    plt.gca().xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: format_tick_value(x)))

    # Format the y-axis ticks as abbreviated values
    plt.gca().yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: format_tick_value(y)))

    # Show the plot
    # plt.show()
    return plt