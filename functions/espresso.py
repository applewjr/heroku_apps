import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import json
import numpy as np
from sklearn.neighbors import KNeighborsRegressor
from sklearn.model_selection import KFold, cross_val_score, GridSearchCV
from sklearn.preprocessing import StandardScaler
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from scipy.spatial.distance import cdist

def get_naive_espresso_points(roast, dose, espresso_points):

    roast = roast.lower().replace(" ", "_")
    dose = int(dose)

    roast_cut = espresso_points['roast_variable'][roast]
    profile_cut = espresso_points['roast_variable'][roast]['brew_profile']

    # Calculate and store the data in a dictionary
    naive_espresso_data = {
        "style": f"{roast} roast, {dose} shot",
        "coffee_grams_in": f"{roast_cut['coffee_grams_dose']['current']*dose} (range {roast_cut['coffee_grams_dose']['min']*dose}-{roast_cut['coffee_grams_dose']['max']*dose})",
        "water_temp_f": f"{roast_cut['water_temp_f']['current']} (range {roast_cut['water_temp_f']['min']}-{roast_cut['water_temp_f']['max']})",
        "brew_profile": {
            "part1": f"{profile_cut['part_1']['seconds']} seconds at {profile_cut['part_1']['bar']} bar",
            "part2": f"{profile_cut['part_2']['seconds']} seconds at {profile_cut['part_2']['bar']} bar",
            "part3": f"{profile_cut['part_3']['seconds']} seconds at {profile_cut['part_3']['bar']} bar",
            "part4": f"{profile_cut['part_4']['seconds']} seconds at {profile_cut['part_4']['bar']} bar",
            "part5": f"{profile_cut['part_5']['seconds']} seconds at {profile_cut['part_5']['bar']} bar",
            "total_seconds": round(profile_cut['part_1']['seconds'] + profile_cut['part_2']['seconds'] + profile_cut['part_3']['seconds'] + profile_cut['part_4']['seconds'] + profile_cut['part_5']['seconds'], 1)
        },
        "niche_grind_setting": roast_cut['niche_grind_setting'],
        "coffee_to_espresso_ratio": f"1:{roast_cut['coffee_to_espresso_ratio']}",
        "espresso_grams_out": f"{roast_cut['coffee_grams_dose']['current']*dose*roast_cut['coffee_to_espresso_ratio']} (range {roast_cut['coffee_grams_dose']['min']*dose*roast_cut['coffee_to_espresso_ratio']}-{roast_cut['coffee_grams_dose']['max']*dose*roast_cut['coffee_to_espresso_ratio']})"
    }

    return naive_espresso_data

def google_sheets_base(GOOGLE_SHEETS_JSON):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    credentials_dict = json.loads(GOOGLE_SHEETS_JSON, strict=False)
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
    google_credentials = gspread.authorize(credentials)
    return google_credentials

def get_google_sheets_bean(google_credentials, GOOGLE_SHEETS_URL_BEAN):
    gc = google_credentials
    sheet = gc.open_by_url(GOOGLE_SHEETS_URL_BEAN)
    worksheet = sheet.get_worksheet(0)
    data = worksheet.get_all_values()
    df_bean = pd.DataFrame(data[1:], columns=data[0])
    return df_bean
    
def get_google_sheets_profile(google_credentials, GOOGLE_SHEETS_URL_PROFILE):
    gc = google_credentials
    sheet = gc.open_by_url(GOOGLE_SHEETS_URL_PROFILE)
    worksheet = sheet.get_worksheet(0)
    data = worksheet.get_all_values()
    df_profile = pd.DataFrame(data[1:], columns=data[0])
    df_profile['profile_start_date'] = pd.to_datetime(df_profile['profile_start_date'], format='%m/%d/%Y %H:%M:%S')
    df_profile['profile_stop_date'] = pd.to_datetime(df_profile['profile_stop_date'], format='%m/%d/%Y %H:%M:%S')
    return df_profile
    
def get_google_sheets_espresso(google_credentials, GOOGLE_SHEETS_URL_ESPRESSO):
    gc = google_credentials
    sheet = gc.open_by_url(GOOGLE_SHEETS_URL_ESPRESSO)
    worksheet = sheet.get_worksheet(0)
    data = worksheet.get_all_values()
    df_espresso_initial = pd.DataFrame(data[1:], columns=data[0])
    len(df_espresso_initial)
    return df_espresso_initial


def get_user_roast_values(df_espresso_initial):

    df_espresso = df_espresso_initial.rename(columns={
        'User\'s Name': 'user_name',
        'Coffee Roast': 'coffee_roast',
        'Number of Shots': 'number_shots'
    })
    valid_user_name_list = df_espresso['user_name'].unique().tolist()
    valid_roast_list = df_espresso['coffee_roast'].unique().tolist()
    valid_shots_list = df_espresso['number_shots'].unique().tolist()

    return valid_user_name_list, valid_roast_list, valid_shots_list

def clean_espresso_df(user_pred, roast_pred, shots_pred, df_espresso_initial, df_profile):

    # user_pred = str(user_pred)
    # roast_pred = str(roast_pred)

    df_espresso = df_espresso_initial.rename(columns={
        'Timestamp': 'timestamp',
        'User\'s Name': 'user_name',
        'Coffee Roast': 'coffee_roast',
        'Number of Shots': 'number_shots',
        'Niche Grind Setting': 'niche_grind_setting',
        'Rocket Profile [Profile]': 'rocket_profile',
        'Ground Coffee in Portafilter Grams': 'ground_coffee_grams',
        'Espresso Liquid Out Grams': 'espresso_out_grams',
        'Extraction Time in Seconds': 'extraction_time_seconds',
        'Outcomes [Bitterness]': 'outcomes_bitterness',
        'Outcomes [Sourness]': 'outcomes_sourness',
        'Outcomes [Crema]': 'outcomes_crema',
        'Outcomes [Sweetness]': 'outcomes_sweetness',
        'Outcomes [Mouthfeel]': 'outcomes_mouthfeel',
        'Outcomes [Overall Taste]': 'outcomes_overall_taste',
        'Water Temp F': 'water_temp_f',
        'Standard Tools [WDT]': 'standard_tools_wdt',
        'Standard Tools [Tamp]': 'standard_tools_tamp',
        'Standard Tools [Filtered Water]': 'standard_tools_filtered_water',
        'Coffee Brand': 'coffee_brand',
        'Coffee Roast Name': 'coffee_roast_name',
        'Any Other Notes': 'any_other_notes',
        'Outcomes [Channelling]': 'outcomes_channelling',
        'Standard Tools [Pre-wetting whole beans]': 'standard_tools_wet_beans',
        'Espresso Flow Time in Seconds': 'flow_time_seconds',
        'Standard Tools [Pre-warm portafilter]': 'standard_tools_prewarm_filter'
    })

    # Replace values in specified columns
    columns_to_replace = ['standard_tools_wdt', 'standard_tools_tamp', 'standard_tools_filtered_water', 'standard_tools_wet_beans', 'standard_tools_prewarm_filter']
    replace_dict = {'Yes': 1, 'No': 0, '': 1}
    df_espresso[columns_to_replace] = df_espresso[columns_to_replace].replace(replace_dict)

    df_espresso['water_temp_f'] = df_espresso['water_temp_f'].replace('', np.nan).fillna(197.6)

    # Reorder columns
    column_order = [
        'timestamp'
        ,'user_name','coffee_roast'
        ,'number_shots','niche_grind_setting','rocket_profile','ground_coffee_grams','espresso_out_grams','extraction_time_seconds','flow_time_seconds','water_temp_f'
        ,'standard_tools_wdt','standard_tools_tamp','standard_tools_filtered_water','standard_tools_wet_beans','standard_tools_prewarm_filter'
        ,'outcomes_bitterness','outcomes_sourness','outcomes_channelling'
        ,'outcomes_crema','outcomes_sweetness','outcomes_mouthfeel','outcomes_overall_taste'
        ,'coffee_brand','coffee_roast_name','any_other_notes'
    ]
    df_espresso = df_espresso[column_order]

    df_espresso['timestamp'] = pd.to_datetime(df_espresso['timestamp'], format='%m/%d/%Y %H:%M:%S')

    # Perform the merge based on rocket_profile and timestamp conditions
    df_espresso = pd.merge(
        df_espresso,
        df_profile,
        on='rocket_profile',
        how='left'
    )

    # Filter rows based on timestamp conditions
    df_espresso = df_espresso[
        (df_espresso['timestamp'] >= df_espresso['profile_start_date']) &
        (df_espresso['timestamp'] <= df_espresso['profile_stop_date'])
        | df_espresso['profile_start_date'].isnull()
    ]

    df_espresso.reset_index(drop=True, inplace=True)

    df_analyze = df_espresso.copy()

    columns_to_keep = [
        'niche_grind_setting', 'ground_coffee_grams', 'espresso_out_grams',
        'extraction_time_seconds', 'flow_time_seconds', 'water_temp_f', 'standard_tools_wdt', 'standard_tools_tamp',
        'standard_tools_filtered_water', 'standard_tools_wet_beans', 'standard_tools_prewarm_filter', 't1', 'p1', 't2', 'p2', 't3', 'p3', 't4', 'p4', 't5', 'p5'
        ,'outcomes_bitterness'
        ,'outcomes_sourness'
        ,'outcomes_channelling'
        ,'outcomes_crema'
        ,'outcomes_sweetness'
        ,'outcomes_mouthfeel'
        ,'outcomes_overall_taste'
        ,'user_name'
        ,'coffee_roast'
        ,'rocket_profile'
        ,'number_shots'
    ]
    df_analyze = df_analyze[columns_to_keep]

    df_analyze.replace("", np.nan, inplace=True)
    df_analyze.replace(-1, np.nan, inplace=True)
    df_analyze.replace("-1", np.nan, inplace=True)
    df_analyze = df_analyze.dropna()

    float_columns = [
        'niche_grind_setting',
        'ground_coffee_grams',
        'espresso_out_grams',
        'extraction_time_seconds',
        'flow_time_seconds',
        'water_temp_f',
        'standard_tools_wdt',
        'standard_tools_tamp',
        'standard_tools_filtered_water',
        'standard_tools_wet_beans',
        'standard_tools_prewarm_filter',
        'outcomes_bitterness',
        'outcomes_sourness',
        'outcomes_channelling',
        'outcomes_crema',
        'outcomes_sweetness',
        'outcomes_mouthfeel',
        'outcomes_overall_taste',
        't1',
        'p1',
        't2',
        'p2',
        't3',
        'p3',
        't4',
        'p4',
        't5',
        'p5',
        'number_shots'
    ]
    df_analyze[float_columns] = df_analyze[float_columns].astype(float)

    df_analyze = df_analyze.loc[(df_analyze["user_name"] == user_pred) & \
                                (df_analyze["coffee_roast"] == roast_pred) & \
                                (df_analyze["number_shots"] == int(shots_pred)) & \
                                (df_analyze["rocket_profile"] != 'Custom')
                                ]

    df_analyze['profile_time_sum'] = df_analyze[['t1', 't2', 't3', 't4', 't5']].sum(axis=1)

    numeric_columns = ['outcomes_bitterness', 'outcomes_sourness', 'outcomes_channelling',
                        'outcomes_crema', 'outcomes_sweetness', 'outcomes_mouthfeel', 'outcomes_overall_taste']
    df_analyze[numeric_columns] = df_analyze[numeric_columns].apply(pd.to_numeric, errors='coerce')
    df_analyze['final_score'] = (
        (10 - df_analyze['outcomes_bitterness']) + # convert a negative score
        (10 - df_analyze['outcomes_sourness']) + # convert a negative score
        (10 - df_analyze['outcomes_channelling']) + # convert a negative score
        df_analyze['outcomes_crema'] +
        df_analyze['outcomes_sweetness'] +
        df_analyze['outcomes_mouthfeel'] +
        (df_analyze['outcomes_overall_taste'] * 6) # make 1/2 of the importance
    ) / 12

    df_analyze['espresso_coffee_ratio'] = (df_analyze['espresso_out_grams'] / df_analyze['ground_coffee_grams']).round(4)
    df_analyze['extract_flow_ratio'] = (df_analyze['extraction_time_seconds'] / df_analyze['flow_time_seconds']).round(4)
    df_analyze['extract_flow_rate'] = (df_analyze['espresso_out_grams'] / df_analyze['flow_time_seconds']).round(4)

    df_scatter = df_analyze.copy()
    scatter_columns_to_keep = [
        'niche_grind_setting',
        'ground_coffee_grams',
        'espresso_out_grams',
        'extraction_time_seconds',
        'flow_time_seconds',
        'water_temp_f',
        'espresso_coffee_ratio',
        'extract_flow_ratio',
        'extract_flow_rate',
        'standard_tools_wdt',
        'standard_tools_tamp',
        'standard_tools_filtered_water',
        'standard_tools_wet_beans',
        'standard_tools_prewarm_filter',
        'outcomes_bitterness',
        'outcomes_sourness',
        'outcomes_channelling',
        'outcomes_crema',
        'outcomes_sweetness',
        'outcomes_mouthfeel',
        'outcomes_overall_taste',
        'final_score'
    ]
    df_scatter = df_scatter[scatter_columns_to_keep]

    columns_to_keep = [
        'niche_grind_setting', 'espresso_coffee_ratio',
        'extraction_time_seconds', 'flow_time_seconds', 'extract_flow_ratio', 'extract_flow_rate',
        'water_temp_f',
        # 'standard_tools_wdt', 'standard_tools_tamp','standard_tools_filtered_water','standard_tools_wet_beans','standard_tools_prewarm_filter',
        # 't1', 'p1', 't2', 'p2', 't3', 'p3', 't4', 'p4', 't5', 'p5',
        'final_score'
    ]
    df_analyze = df_analyze[columns_to_keep]

    df_analyze.reset_index(drop=True, inplace=True)

    return df_analyze, df_scatter

def find_optimal_espresso_parameters(df_analyze):
    # Check if the dataset has enough data
    min_data_threshold = 10
    if df_analyze.shape[0] < min_data_threshold:
        return {col: f"Need {min_data_threshold - df_analyze.shape[0]} more data points to complete this analysis" for col in df_analyze.columns if col != 'final_score'}, False, None

    # Define features and target variable
    X = df_analyze.drop('final_score', axis=1)
    y = df_analyze['final_score']

    # Initialize cross-validator
    kf = KFold(n_splits=5, shuffle=True, random_state=42)

    # Define a range of hyperparameters for tuning
    param_grid = {
        'n_neighbors': range(1, 10),
        'weights': ['uniform', 'distance'],
        'metric': ['euclidean', 'manhattan']
    }

    # Initialize the KNN regressor
    knn = KNeighborsRegressor()

    # Create grid search object with cross-validation
    grid_search = GridSearchCV(knn, param_grid, cv=kf, scoring='neg_mean_squared_error')

    # Fit the grid search to the data
    grid_search.fit(X, y)

    # Get the best estimator and its parameters
    best_knn = grid_search.best_estimator_
    best_params = grid_search.best_params_

    # Evaluate performance of the best model
    mse_scores = cross_val_score(best_knn, X, y, cv=kf, scoring='neg_mean_squared_error')
    r2_scores = cross_val_score(best_knn, X, y, cv=kf, scoring='r2')
    mae_scores = cross_val_score(best_knn, X, y, cv=kf, scoring='neg_mean_absolute_error')

    # Average performance across folds
    performance_dict = {
        'Mean Squared Error': round(-np.mean(mse_scores), 4),
        'R-squared': round(np.mean(r2_scores), 4),
        'Mean Absolute Error': round(-np.mean(mae_scores), 4),
        'Observations': df_analyze.shape[0],
        'Optimal Number of Neighbors': best_params['n_neighbors'],
        'Optimal Weight Method': best_params['weights'],
        'Optimal Metric': best_params['metric']
    }

    # Find optimal parameters with the best model
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    best_knn.fit(X_scaled, y)
    predicted_scores = best_knn.predict(X_scaled)
    highest_score_index = predicted_scores.argmax()
    optimal_parameters_scaled = X_scaled[highest_score_index]
    optimal_parameters = scaler.inverse_transform([optimal_parameters_scaled])[0]
    optimal_parameters_dict = {col: param_value for col, param_value in zip(X.columns, optimal_parameters)}

    return optimal_parameters_dict, True, performance_dict

def get_scatter_col_labels():
    scatter_espresso_col_labels = {
         'niche_grind_setting': 'Niche Grind Setting'
        ,'ground_coffee_grams': 'Ground Coffee g'
        ,'espresso_out_grams': 'Espresso Out g'
        ,'extraction_time_seconds': 'Extraction Time in Seconds'
        ,'flow_time_seconds': 'Flow Time in Seconds'
        ,'water_temp_f': 'Water Temp F'
        ,'espresso_coffee_ratio': 'Espresso g / Coffee g'
        ,'extract_flow_ratio': 'Espresso g / Flow Seconds'
        ,'extract_flow_rate': 'Extraction Seconds / Flow Seconds'
        ,'standard_tools_wdt': 'WDT'
        ,'standard_tools_tamp': 'Tamp'
        ,'standard_tools_filtered_water': 'Filtered Water'
        ,'standard_tools_wet_beans': 'Pre-wet whole beans'
        ,'standard_tools_prewarm_filter': 'Pre-warm filter'
        ,'outcomes_bitterness': 'Outcome Bitterness'
        ,'outcomes_sourness': 'Outcome Sourness'
        ,'outcomes_channelling': 'Outcome Channelling'
        ,'outcomes_crema': 'Outcome Crema'
        ,'outcomes_sweetness': 'Outcome Sweetness'
        ,'outcomes_mouthfeel': 'Outcome Mouthfeel'
        ,'outcomes_overall_taste': 'Outcome Overall Taste'
        ,'final_score': 'Final Score'
        }
    return scatter_espresso_col_labels

def espresso_dynamic_scatter(df_analyze, espresso_x_col, espresso_y_col):

    col_labels = get_scatter_col_labels()

    # Normalize 'final_score' values to range between 1 and 10
    norm = plt.Normalize(1, 10)

    # Create a custom colormap from orange to blue
    colors = [(0, 'orange'), (1, 'blue')]
    cmap = mcolors.LinearSegmentedColormap.from_list('custom_cmap', colors)

    plt.clf()

    # Scatter plot with color based on 'final_score'
    plt.scatter(df_analyze[espresso_x_col], df_analyze[espresso_y_col], 
                c=df_analyze['final_score'], cmap=cmap, norm=norm, alpha=0.8)

    # Use user-friendly labels for axes and title
    x_label = col_labels.get(espresso_x_col, espresso_x_col)
    y_label = col_labels.get(espresso_y_col, espresso_y_col)
    plt.xlabel(x_label)
    plt.ylabel(y_label)
    plt.title(f'{x_label} vs {y_label}')

    plt.colorbar(label='Final Score')  # Optional: add a colorbar
    # plt.show()

    return plt

def get_furthest_point_multidimensional(df
    ,distance_grind_min, distance_grind_max, distance_grind_default
    ,distance_coffee_g_min, distance_coffee_g_max, distance_coffee_g_default
    ,distance_espresso_g_min, distance_espresso_g_max, distance_espresso_g_default
    ):

    allowed_ranges = [(float(distance_grind_min), float(distance_grind_max)), \
        (float(distance_coffee_g_min), float(distance_coffee_g_max)), \
        (float(distance_espresso_g_min), float(distance_espresso_g_max))
        ]
    granularities = [float(distance_grind_default), float(distance_coffee_g_default), float(distance_espresso_g_default)]

    # Check if dimensions match
    if len(allowed_ranges) != len(granularities) or df.shape[1] != len(allowed_ranges):
        raise ValueError("Dimensions of allowed_ranges, granularities, and DataFrame columns must match.")

    # Generate a grid of potential points within the allowed ranges with specified granularities
    grid_ranges = [np.arange(start, stop + gran, gran) for (start, stop), gran in zip(allowed_ranges, granularities)]
    mesh = np.array(np.meshgrid(*grid_ranges))
    potential_points = mesh.T.reshape(-1, len(allowed_ranges))

    # Calculate distances between potential points and existing points
    existing_points = df.to_numpy()
    distances = cdist(potential_points, existing_points, metric='euclidean')

    # Find the minimum distance to existing points for each potential point
    min_distances = np.min(distances, axis=1)

    # Identify the potential point with the maximum of these minimum distances
    furthest_point_index = np.argmax(min_distances)
    furthest_point = potential_points[furthest_point_index]

    furthest_point_rounded = [round(value, 0) if value.is_integer() else round(value, 1) for value in furthest_point]

    return furthest_point_rounded

def plot_3d_scatter(df):
    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')

    ax.scatter(df['niche_grind_setting'], df['ground_coffee_grams'], df['espresso_out_grams'])

    ax.set_xlabel('Grind')
    ax.set_ylabel('Coffee Grams')
    ax.set_zlabel('Espresso Grams')
    # plt.show()

    return plt