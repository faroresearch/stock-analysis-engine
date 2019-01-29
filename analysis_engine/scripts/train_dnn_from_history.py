#!/usr/bin/env python

"""
Train a DNN from a trading history

::

    train_from_history.py -b s3_bucket -k s3_key
"""

import argparse
import datetime
import numpy as np
import numpy.random as np_random
import pandas as pd
import pandas.api.types as pandas_types
import sklearn.model_selection as tt_split
import keras.wrappers.scikit_learn as keras_scikit
import tensorflow as tf
import analysis_engine.consts as ae_consts
import analysis_engine.load_history_dataset as load_history
import analysis_engine.ai.build_regression_dnn as build_dnn
import analysis_engine.ai.build_datasets_using_scalers as build_scaler_datasets
import analysis_engine.ai.build_scaler_dataset_from_df as build_scaler_df
import analysis_engine.ai.plot_dnn_fit_history as plot_fit_history
import analysis_engine.plot_trading_history as plot_trading_history
import spylunking.log.setup_logging as log_utils

# ensure reproducible results
# machinelearningmastery.com/reproducible-results-neural-networks-keras/
np_random.seed(1)

log = log_utils.build_colorized_logger(
    name='train-dnn-from-history')


def train_and_predict_from_history_in_s3():
    """train_and_predict_from_history_in_s3

    Run a derived algorithm with an algorithm config dictionary

    :param config_dict: algorithm config dictionary
    """

    log.debug('start - plot')

    parser = argparse.ArgumentParser(
        description=(
            'train a dnn to predict a column from a'
            'a trading history file in s3'))
    parser.add_argument(
        '-b',
        help=(
            's3 bucket'),
        required=False,
        dest='s3_bucket')
    parser.add_argument(
        '-k',
        help=(
            's3 key'),
        required=False,
        dest='s3_key')
    parser.add_argument(
        '-q',
        help=(
            'disable scaler normalization and '
            'only use high + low + open to '
            'predict the close'),
        required=False,
        dest='disable_scaler',
        action='store_true')
    parser.add_argument(
        '-d',
        help=(
            'debug'),
        required=False,
        dest='debug',
        action='store_true')
    args = parser.parse_args()

    use_scalers = True
    s3_access_key = ae_consts.S3_ACCESS_KEY
    s3_secret_key = ae_consts.S3_SECRET_KEY
    s3_region_name = ae_consts.S3_REGION_NAME
    s3_address = ae_consts.S3_ADDRESS
    s3_secure = ae_consts.S3_SECURE
    compress = True

    s3_bucket = (
        f'algohistory')
    s3_key = (
        f'algo_training_SPY.json')

    debug = False

    if args.s3_bucket:
        s3_bucket = args.s3_bucket
    if args.s3_key:
        s3_key = args.s3_key
    if args.disable_scaler:
        use_scalers = False
    if args.debug:
        debug = True

    load_res = load_history.load_history_dataset(
        s3_enabled=True,
        s3_key=s3_key,
        s3_address=s3_address,
        s3_bucket=s3_bucket,
        s3_access_key=s3_access_key,
        s3_secret_key=s3_secret_key,
        s3_region_name=s3_region_name,
        s3_secure=s3_secure,
        compress=compress)

    algo_config = load_res.get(
        'algo_config_dict',
        None)
    algo_name = load_res.get(
        'algo_name',
        None)
    tickers = load_res.get(
        'tickers',
        [
            'SPY',
        ])
    ticker = tickers[0]

    log.info(
        f'found algo: {algo_name}')

    if debug:
        log.info(
            f'config: {ae_consts.ppj(algo_config)}')

    df = load_res[ticker]
    df['date'] = pd.to_datetime(
        df['date'])
    df['minute'] = pd.to_datetime(
        df['minute'])
    ticker = df['ticker'].iloc[0]
    df_filter = (df['close'] >= 0.1)
    first_date = df[df_filter]['date'].iloc[0]
    end_date = df[df_filter]['date'].iloc[-1]

    if 'minute' in df:
        found_valid_minute = df['minute'].iloc[0]
        if found_valid_minute:
            first_date = df[df_filter]['minute'].iloc[0]
            end_date = df[df_filter]['minute'].iloc[-1]

    num_rows = len(df.index)
    log.info(
        f'prepared training data from '
        f'history {s3_bucket}@{s3_key} '
        f'rows={num_rows} '
        f'dates: {first_date} to {end_date}')

    if debug:
        for i, r in df.iterrows():
            log.info(
                f'{r["minute"]} - {r["close"]}')
        # end of for loop

        log.info(
            f'columns: {df.columns.values}')
        log.info(
            f'rows: {len(df.index)}')
    # end of debug

    dnn_config = {
        'layers': [
            {
                'num_neurons': 150,
                'init': 'uniform',
                'activation': 'relu'
            },
            {
                'num_neurons': 100,
                'init': 'uniform',
                'activation': 'relu'
            },
            {
                'num_neurons': 50,
                'init': 'uniform',
                'activation': 'relu'
            },
            {
                'num_neurons': 1,
                'init': 'uniform',
                'activation': 'relu'
            }
        ]
    }

    compile_config = {
        'loss': 'mse',
        'optimizer': 'adam',
        'metrics': [
            'accuracy',
            'mse',
            'mae',
            'mape',
            'cosine'
        ]
    }

    predict_feature = 'close'
    use_epochs = 10
    use_batch_size = 10
    use_test_size = 0.1
    use_random_state = 1
    use_seed = 7  # change this to random in prod
    use_shuffle = False
    model_verbose = True
    fit_verbose = True

    """
    for scaler-normalized datasets this will
    compile numeric columns and ignore string/non-numeric
    columns as training and test feature columns
    """
    use_all_features = use_scalers
    all_features = []
    train_features = []
    if use_all_features:
        for c in df.columns.values:
            if (
                    pandas_types.is_numeric_dtype(df[c]) and
                    c not in train_features):
                if c != predict_feature:
                    train_features.append(c)
                if c not in all_features:
                    all_features.append(c)

        dnn_config['layers'][-1]['activation'] = (
            'sigmoid')
    else:
        train_features = [
            'high',
            'low',
            'open'
        ]
        all_features = [
            'close'
        ] + train_features

    num_features = len(train_features)
    features_and_minute = [
        'minute'
    ] + all_features

    log.info(
        f'converting columns to floats')

    timeseries_df = df[df_filter][features_and_minute].fillna(-10000.0)
    converted_df = timeseries_df[all_features].astype('float32')

    train_df = None
    test_df = None
    scaler_predictions = None
    if use_all_features:
        scaler_res = build_scaler_datasets.build_datasets_using_scalers(
            train_features=train_features,
            test_feature=predict_feature,
            df=converted_df,
            test_size=use_test_size,
            seed=use_seed)
        if scaler_res['status'] != ae_consts.SUCCESS:
            log.error(
                'failed to build scaler train and test datasets')
            return
        train_df = scaler_res['scaled_train_df']
        test_df = scaler_res['scaled_test_df']
        x_train = scaler_res['x_train']
        x_test = scaler_res['x_test']
        y_train = scaler_res['y_train']
        y_test = scaler_res['y_test']
        scaler_predictions = scaler_res['scaler_test']
    else:
        log.info(
            f'building train and test dfs from subset of features')
        train_df = converted_df[train_features]
        test_df = converted_df[[predict_feature]]

        log.info(
            f'splitting {num_rows} into test and training '
            f'size={use_test_size}')

        (x_train,
         x_test,
         y_train,
         y_test) = tt_split.train_test_split(
            train_df,
            test_df,
            test_size=use_test_size,
            random_state=use_random_state)

    log.info(
        f'split breakdown - '
        f'x_train={len(x_train)} '
        f'x_test={len(x_test)} '
        f'y_train={len(y_train)} '
        f'y_test={len(y_test)}')

    def set_model():
        return build_dnn.build_regression_dnn(
            num_features=num_features,
            compile_config=compile_config,
            model_config=dnn_config)

    estimator = keras_scikit.KerasRegressor(
        build_fn=set_model,
        epochs=use_epochs,
        batch_size=use_batch_size,
        verbose=model_verbose)

    log.info(
        f'fitting estimator - '
        f'predicting={predict_feature} '
        f'epochs={use_epochs} '
        f'batch={use_batch_size} '
        f'test_size={use_test_size} '
        f'seed={use_seed}')

    history = estimator.fit(
        x_train,
        y_train,
        validation_data=(
            x_train,
            y_train),
        epochs=use_epochs,
        batch_size=use_batch_size,
        shuffle=use_shuffle,
        verbose=fit_verbose)

    created_on = (
        datetime.datetime.now().strftime(
            ae_consts.COMMON_TICK_DATE_FORMAT))
    plot_fit_history.plot_dnn_fit_history(
        df=history.history,
        title=(
            f'DNN Errors Over Training Epochs\n'
            f'Training Data: s3://{s3_bucket}/{s3_key}\n'
            f'Created: {created_on}'),
        red='mean_squared_error',
        blue='mean_absolute_error',
        green='acc',
        orange='cosine_proximity')

    # on production use newly fetched pricing data
    # not the training data
    predict_records = []
    if use_all_features:
        prediction_res = build_scaler_df.build_scaler_dataset_from_df(
            df=converted_df[train_features])
        if prediction_res['status'] == ae_consts.SUCCESS:
            predict_records = prediction_res['df']
    else:
        predict_records = converted_df[train_features]

    log.info(
        f'making predictions: {len(predict_records)}')

    predictions = estimator.model.predict(
        predict_records,
        verbose=True)

    np.set_printoptions(threshold=np.nan)
    indexes = tf.argmax(predictions, axis=1)
    data = {}
    data['indexes'] = indexes
    price_predictions = []
    if use_all_features and scaler_predictions:
        price_predictions = [
            ae_consts.to_f(x) for x in
            scaler_predictions.inverse_transform(
                predictions.reshape(-1, 1)).reshape(-1)]
    else:
        price_predictions = [ae_consts.to_f(x[0]) for x in predictions]

    timeseries_df['predicted_close'] = price_predictions
    timeseries_df['error'] = (
        timeseries_df['close'] -
        timeseries_df['predicted_close'])

    output_features = [
        'minute',
        'close',
        'predicted_close',
        'error'
    ]

    date_str = (
        f'Dates: {timeseries_df["minute"].iloc[0]} '
        f'to '
        f'{timeseries_df["minute"].iloc[-1]}')

    log.info(
        f'historical close with predicted close: '
        f'{timeseries_df[output_features]}')
    log.info(
        date_str)
    log.info(
        f'Columns: {output_features}')

    average_error = ae_consts.to_f(
        timeseries_df['error'].sum() / len(timeseries_df.index))

    log.info(
        f'Average historical close '
        f'vs predicted close error: '
        f'{average_error}')

    log.info(
        f'plotting historical close vs predicted close from '
        f'training with columns={num_features}')

    ts_filter = (timeseries_df['close'] > 0.1)
    latest_close = (
        timeseries_df[ts_filter]['close'].iloc[-1])
    latest_predicted_close = (
        timeseries_df[ts_filter]['predicted_close'].iloc[-1])

    log.info(
        f'{end_date} close={latest_close} '
        f'with '
        f'predicted_close={latest_predicted_close}')

    plot_trading_history.plot_trading_history(
        title=(
            f'{ticker} - Historical Close vs Predicted Close\n'
            f'Number of Training Features: {num_features}\n'
            f'{date_str}'),
        df=timeseries_df,
        red='close',
        blue='predicted_close',
        green=None,
        orange=None,
        date_col='minute',
        date_format='%d %H:%M:%S\n%b',
        xlabel='minute',
        ylabel='Historical Close vs Predicted Close',
        df_filter=ts_filter,
        width=8.0,
        height=8.0,
        show_plot=True,
        dropna_for_all=False)
# end of train_and_predict_from_history_in_s3


if __name__ == '__main__':
    train_and_predict_from_history_in_s3()
