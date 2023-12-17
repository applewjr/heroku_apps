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
