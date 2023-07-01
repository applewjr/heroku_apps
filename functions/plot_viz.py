import mysql.connector
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg') # allows Matplotlib to render plots directly to image files without requiring a GUI
import os

def yt_plot():
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
    # plt.show()

    return plt

    # plot_filename = 'plot.png'

    # # return plot_filename

    # plt.savefig(plot_filename)

    # static_folder = 'static/'
    # plot_filename = 'plot.png'
    # plot_path = os.path.join(static_folder, plot_filename)
    # plt.savefig(plot_path)