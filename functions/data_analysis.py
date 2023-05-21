import pandas as pd
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
import io
import base64

def summarize_df(df):
    summary = {}
    df_summ = pd.DataFrame()

    # summary['df'] = df
    summary['shape'] = df.shape
    
    df_summ['Name'] = df.columns.tolist()
    df_summ['Data Type'] = df.dtypes.tolist()
    df_summ['Null Count'] = df.isna().sum().tolist()
    df_summ['Null Percent'] = round((df.isna().sum()/df.shape[0]),4).tolist()
    df_summ['Mode'] = df.mode().loc[0].tolist()

    min, mean, median, max, std, skew, kurt = [], [], [], [], [], [], []
    for col in df.columns:
        if np.issubdtype(df[col].dtype, np.number):
            min.append(df[col].min())
            mean.append(df[col].mean())
            median.append(df[col].median())
            max.append(df[col].max())
            std.append(df[col].std())
            skew.append(df[col].skew())
            kurt.append(df[col].kurtosis())
        else:
            min.append('')
            mean.append('')
            median.append('')
            max.append('')
            std.append('')
            skew.append('')
            kurt.append('')

    df_summ['Min'] = min
    df_summ['Mean'] = mean
    df_summ['Median'] = median
    df_summ['Max'] = max
    df_summ['SD'] = std
    df_summ['Skew'] = skew
    df_summ['Kurtosis'] = kurt

    summary['df_summ'] = df_summ.to_html()
    
    # Pairwise correlations
    numeric_cols = df.select_dtypes(include=['float', 'int']).columns.tolist()
    num_pairs = [(numeric_cols[i], numeric_cols[j]) for i in range(len(numeric_cols)) for j in range(i+1, len(numeric_cols))]
    corr_dict = {}
    for pair in num_pairs:
        corr = round(df[pair[0]].corr(df[pair[1]]),4)
        corr_dict[f'{pair[0]} vs {pair[1]}'] = corr
    summary['pairwise_correlations'] = corr_dict
    summary['pairwise_correlations'] = dict(sorted(summary['pairwise_correlations'].items(), key=lambda x: abs(x[1]), reverse=True)[:10])
        # top 10, highest absolute value
    
    return summary




def generate_heatmap(df):
    df = pd.DataFrame(df)

    # Calculate the correlation matrix
    corr_matrix = df.corr()

    # Create the heatmap
    plt.figure(figsize=(8, 6))
    sns.heatmap(corr_matrix, annot=True, cmap='coolwarm')
    
    # Save the plot to a buffer
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)

    # Convert the plot buffer to base64 encoding
    plot_data = base64.b64encode(buf.getvalue()).decode('utf-8')
    plt.close()

    return plot_data