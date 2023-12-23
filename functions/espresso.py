import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import json
import numpy as np
from sklearn.neighbors import KNeighborsRegressor
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
import matplotlib.pyplot as plt

def get_naive_espresso_points(roast, dose, espresso_points):

    roast = roast.lower()
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

def clean_espresso_df(user_pred, roast_pred, df_espresso_initial, df_profile):

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
        'p5'
    ]
    df_analyze[float_columns] = df_analyze[float_columns].astype(float)

    df_analyze = df_analyze.loc[(df_analyze["user_name"] == user_pred) & \
                                (df_analyze["coffee_roast"] == roast_pred) & \
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

    return df_analyze

def find_optimal_espresso_parameters(df_analyze):
    # Load the dataset
    data = df_analyze.copy()

    # Define features and target variable
    X = data.drop('final_score', axis=1)
    y = data['final_score']

    # Split the data into training and test sets
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    # Standardize the features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Initialize the KNN regressor
    knn = KNeighborsRegressor(n_neighbors=3)

    # Fit the model
    knn.fit(X_train_scaled, y_train)

    # Find the training instance with the highest predicted score
    predicted_scores = knn.predict(X_train_scaled)
    highest_score_index = predicted_scores.argmax()
    optimal_parameters_scaled = X_train_scaled[highest_score_index]

    # Inverse transform the scaled optimal parameters to the original scale
    optimal_parameters = scaler.inverse_transform([optimal_parameters_scaled])[0]

    # Create a dictionary with the optimal parameters
    optimal_parameters_dict = {col: param_value for col, param_value in zip(X.columns, optimal_parameters)}

    return optimal_parameters_dict

def espresso_dynamic_scatter(df_analyze, espresso_x_col, espresso_y_col):

    plt.clf()
    plt.scatter(df_analyze[espresso_x_col], df_analyze[espresso_y_col])
    plt.title(f'{espresso_x_col} vs {espresso_y_col}')
    plt.xlabel(espresso_x_col)
    plt.ylabel(espresso_y_col)
    plt.show()
    # plt.tight_layout()

    return plt
