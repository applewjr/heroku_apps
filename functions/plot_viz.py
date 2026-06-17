import mysql.connector
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from adjustText import adjust_text
import matplotlib
matplotlib.use('Agg') # allows Matplotlib to render plots directly to image files without requiring a GUI
import os
import io
import base64

import config as app_config
from functions.youtube_stats import NOW_FEED

# Reuse the central secret loading (Heroku env vars, or secret_pass.py / env
# fallback locally and in CI) instead of importing secret_pass directly.
_mysql = app_config.MYSQL_POOL_CONFIG if app_config.IS_HEROKU else app_config.MYSQL_CONFIG
config = {
    'user': _mysql['user'],
    'password': _mysql['password'],
    'host': _mysql['host'],
    'database': _mysql['database'],
    'raise_on_warnings': True,
}

# Shared chart styling so all dashboard plots match the site (teal brand,
# muted gridlines, no top/right spines). Set once at import.
YT_BRAND = '#02484d'
YT_PALETTE = ['#02484d', '#1a7f86', '#3fb0a0', '#8ec9a8', '#c9b458', '#9ca3af']
YT_ANNOTATION = '#6b7280'

plt.rcParams.update({
    'figure.facecolor': 'white',
    'axes.facecolor': 'white',
    'axes.edgecolor': '#9ca3af',
    'axes.labelcolor': '#374151',
    'text.color': '#374151',
    'xtick.color': '#6b7280',
    'ytick.color': '#6b7280',
    'axes.spines.top': False,
    'axes.spines.right': False,
    'axes.grid': True,
    'axes.axisbelow': True,
    'grid.color': '#e5e7eb',
    'grid.linewidth': 0.8,
    'font.size': 11,
})
plt.rcParams['axes.prop_cycle'] = matplotlib.cycler(color=YT_PALETTE)


def format_tick_value(value):
    if abs(value) >= 1e9:
        return f'{value/1e9:.0f}B'
    elif abs(value) >= 1e6:
        return f'{value/1e6:.0f}M'
    elif abs(value) >= 1e3:
        return f'{value/1e3:.0f}K'
    else:
        return str(value)


def fig_to_data_uri(fig):
    """Render a matplotlib figure to a base64 PNG data URI and close it.

    Returns a self-contained 'data:image/png;base64,...' string so cached
    pages embed the plot inline instead of pointing at a file on the
    ephemeral per-dyno filesystem.
    """
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close(fig)
    encoded = base64.b64encode(buf.getvalue()).decode('ascii')
    return f'data:image/png;base64,{encoded}'


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
    dynamic_query.append(f"""
        FROM {NOW_FEED} AS yt
        LEFT JOIN youtube_cat AS cat ON yt.vid_cat_id = cat.id
        GROUP BY collected_date
        ORDER BY collected_date DESC
        LIMIT 30;
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

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.xaxis.grid(False)  # keep horizontal gridlines only on the bar chart

    bottom = np.zeros(len(dates))

    for category in new_top_categories:
        values = new_top_categories_df[category].tolist()[::-1]  # Reverse the order of values
        ax.bar(range(len(dates)), values, bottom=bottom, label=category)
        bottom += np.array(values)

    ax.set_xticks(range(len(dates)))
    ax.set_xticklabels(dates, rotation=90, ha='center')

    ax.set_xlabel('Date (Last 30 Days)')
    ax.set_ylabel('Video Count')
    fig.suptitle('Daily 50 Trending Videos By Category', fontsize=16, color=YT_BRAND)

    ax.legend(loc='upper left', bbox_to_anchor=(1, 1), frameon=False)

    fig.tight_layout()

    return fig

def yt_video_scatter(latest_date):
    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()

    # Window off the latest available day (not CURDATE()) so the chart matches the
    # rest of the page on an ETL-gap day.
    sql_query = f"""
        SELECT MAX(vid_likes), MAX(vid_views), chnl
        FROM {NOW_FEED} AS yt
        WHERE collected_date >= %s - INTERVAL 7 DAY
        GROUP BY chnl;
        """

    cursor.execute(sql_query, (latest_date,))
    data = cursor.fetchall()

    cursor.close()
    conn.close()

    # Separate x, y, and names
    x = [item[0] for item in data]
    y = [item[1] for item in data]
    names = [item[2] for item in data]

    fig, ax = plt.subplots()

    ax.scatter(x, y, alpha=0.75, edgecolors='white', linewidths=0.5, s=45)

    extreme_indices = sorted(range(len(y)), key=lambda i: abs(y[i] - sum(y) / len(y)), reverse=True)[:5]

    texts = []
    for i in extreme_indices:
        texts.append(ax.text(x[i], y[i], names[i], ha='left', va='bottom'))

    adjust_text(texts, arrowprops=dict(arrowstyle='->', color=YT_ANNOTATION))

    ax.set_xlabel('Video Likes')
    ax.set_ylabel('Video Views')
    fig.suptitle('Trending Videos: Views ~ Likes', fontsize=16, color=YT_BRAND)
    ax.text(0.5, -0.17, 'Last 7 days, Top 5 Outlier Video Channels Tagged', fontsize=10, ha='center', transform=ax.transAxes)

    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: format_tick_value(x)))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: format_tick_value(y)))

    fig.tight_layout()
    return fig



def yt_chnl_scatter(latest_date):
    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()

    # Window off the latest available day (not CURDATE()) so the chart matches the
    # rest of the page on an ETL-gap day.
    sql_query = f"""
        SELECT  MAX(chnl_subs), MAX(chnl_views), chnl
        FROM {NOW_FEED} AS yt
        WHERE collected_date >= %s - INTERVAL 7 DAY
        GROUP BY chnl;
        """

    cursor.execute(sql_query, (latest_date,))
    data = cursor.fetchall()

    cursor.close()
    conn.close()

    # Separate x, y, and names
    x = [item[0] for item in data]
    y = [item[1] for item in data]
    names = [item[2] for item in data]

    fig, ax = plt.subplots()

    ax.scatter(x, y, alpha=0.75, edgecolors='white', linewidths=0.5, s=45)

    extreme_indices = sorted(range(len(y)), key=lambda i: abs(y[i] - sum(y) / len(y)), reverse=True)[:5]

    texts = []
    for i in extreme_indices:
        texts.append(ax.text(x[i], y[i], names[i], ha='left', va='bottom'))

    adjust_text(texts, arrowprops=dict(arrowstyle='->', color=YT_ANNOTATION))

    ax.set_xlabel('Channel Subs')
    ax.set_ylabel('Channel Views')
    fig.suptitle('Channels with Trending Videos: Views ~ Subs', fontsize=16, color=YT_BRAND)
    ax.text(0.5, -0.17, 'Last 7 days, Top 5 Outlier Channels Tagged', fontsize=10, ha='center', transform=ax.transAxes)

    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: format_tick_value(x)))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: format_tick_value(y)))

    fig.tight_layout()
    return fig
