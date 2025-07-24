# run_scanner_backtest_vector.py
# '다수 코인 스캐너' 전략을 위한 최종 백테스팅 스크립트.
# portfolio, performance, results_handler 모듈과 연동하여 동작합니다.

import pandas as pd
from datetime import datetime
import logging
import itertools
from multiprocessing import Pool, cpu_count

# --- 프로젝트의 핵심 모듈 임포트 ---
import config
from data import data_manager
from utils import indicators
from strategies import strategy_signals
from core.strategy import hybrid_trend_strategy
from core import scanner_portfolio
from backtester import performance, results_handler

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# EXPERIMENT_CONFIGS와 COMMON_REGIME_PARAMS는 이전과 동일하게 유지합니다.
EXPERIMENT_CONFIGS = [
    {
        'strategy_name': 'hybrid_trend_strategy',
        'param_grid': {
            'breakout_window': [480],
            'volume_avg_window': [600],
            'volume_multiplier': [1.6],
            'long_term_sma_period': [1200],
            'exit_sma_period': [240],
            'short_ma': [180],
            'long_ma': [480],
            'stop_loss_atr_multiplier': [1.5],
            'trailing_stop_percent': [0.2],
        }
    },
]

COMMON_REGIME_PARAMS = {
    'version': 'v1',
    'regime_sma_period': [240],
    'adx_threshold': [20]
}


# ✨ [멀티프로세싱 수정] 작업자(worker) 프로세스를 초기화하는 함수
def init_worker(data):
    """
    각 자식 프로세스가 시작될 때 한 번만 호출되어,
    전역 변수 all_data를 초기화합니다.
    """
    global all_data
    all_data = data


def run_backtest_task(task_info):
    """
    하나의 (파라미터 + 시간 간격) 조합에 대한 백테스트를 수행하는 '작업자(Worker)' 함수입니다.
    멀티프로세싱 Pool에 의해 호출됩니다.
    """
    params, interval = task_info

    if params is None:
        logging.error("작업자 함수에 'params'가 None으로 전달되었습니다. 작업을 건너뜁니다.")
        return

    strategy_name = params.get('strategy_name')

    buy_params = {}
    exit_params = {}
    if strategy_name == 'hybrid_trend_strategy':
        buy_params = {
            'trend_following_params': {
                'breakout_window': params.get('breakout_window'),
                'volume_avg_window': params.get('volume_avg_window'),
                'volume_multiplier': params.get('volume_multiplier'),
                'long_term_sma_period': params.get('long_term_sma_period'),
                'exit_sma_period': params.get('exit_sma_period')
            },
            'ma_trend_params': {
                'short_ma': params.get('short_ma'),
                'long_ma': params.get('long_ma'),
            }
        }
        exit_params = {
            'stop_loss_atr_multiplier': params.get('stop_loss_atr_multiplier'),
            'trailing_stop_percent': params.get('trailing_stop_percent')
        }

    base_experiment_name = "_".join(f"{key.replace('_', '')}{value}" for key, value in params.items())
    experiment_name = f"{base_experiment_name}_{interval}H"
    logging.info(f"🚀 [작업 시작] {experiment_name}")

    precomputed_signals = {}
    for ticker, df in all_data.items():
        df_with_signal = hybrid_trend_strategy(df, buy_params)
        buy_mask = (df_with_signal['signal'] == 1) & (df_with_signal['regime'] == 'bull')
        precomputed_signals[ticker] = buy_mask

    initial_capital = config.INITIAL_CAPITAL
    max_trades = config.MAX_CONCURRENT_TRADES
    common_start = max([df.index.min() for df in all_data.values() if not df.empty])
    common_end = min([df.index.max() for df in all_data.values() if not df.empty])
    date_range = pd.date_range(start=common_start, end=common_end, freq='h')
    pm = scanner_portfolio.ScannerPortfolioManager(initial_capital=initial_capital)

    for current_date in date_range:
        if current_date.hour % interval != 0:
            continue

        pm.update_portfolio_value(all_data, current_date)

        open_positions_copy = list(pm.get_open_positions())
        for ticker in open_positions_copy:
            position = pm.get_position(ticker)
            if current_date not in all_data[ticker].index: continue
            data_for_sell = all_data[ticker].loc[all_data[ticker].index <= current_date]
            if data_for_sell.empty: continue

            sell_signal, reason = strategy_signals.get_sell_signal(
                data=data_for_sell, position=position, exit_params=exit_params,
                strategy_name=position.get('strategy'), strategy_params=position.get('params')
            )
            if sell_signal:
                pm.execute_sell(ticker, data_for_sell['close'].iloc[-1], current_date, reason)

        if len(pm.get_open_positions()) < max_trades:
            tickers_with_buy_signal = [
                ticker for ticker, signals in precomputed_signals.items() if signals.get(current_date, False)
            ]
            if hasattr(config, 'COINS_TO_EXCLUDE') and config.COINS_TO_EXCLUDE:
                tickers_with_buy_signal = [
                    t for t in tickers_with_buy_signal if t not in config.COINS_TO_EXCLUDE
                ]
            if tickers_with_buy_signal:
                candidates = indicators.rank_candidates_by_volume(
                    tickers_with_buy_signal, all_data, current_date, interval
                )
                for candidate_ticker in candidates:
                    if len(pm.get_open_positions()) >= max_trades: break
                    if candidate_ticker not in pm.get_open_positions():
                        data_for_buy = all_data[candidate_ticker].loc[
                            all_data[candidate_ticker].index <= current_date]
                        pm.execute_buy(
                            ticker=candidate_ticker, price=data_for_buy['close'].iloc[-1],
                            trade_date=current_date, strategy_info={'strategy': strategy_name, 'params': buy_params},
                            entry_atr=data_for_buy['ATR'].iloc[-1] if 'ATR' in data_for_buy.columns else 0,
                            all_data=all_data
                        )

    trade_log_df = pm.get_trade_log_df()
    if trade_log_df.empty:
        logging.warning(f"[{experiment_name}] 거래가 발생하지 않았습니다.")
        return

    daily_log_df = pm.get_daily_log_df()
    summary = performance.generate_summary_report(trade_log_df, daily_log_df, initial_capital)

    logging.info(f"--- 🏁 [{experiment_name}] 결과 분석 완료 ---")

    summary_df = pd.DataFrame([summary])
    summary_df['experiment_name'] = experiment_name
    summary_df['run_date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        results_handler.save_results(
            results_df=summary_df, table_name='scanner_backtest_summary'
        )
    except Exception as e:
        logging.error(f"오류: [{experiment_name}] 결과를 DB에 저장하는 중 문제가 발생했습니다 - {e}")


if __name__ == '__main__':
    logging.info("데이터 로드 및 보조지표 계산을 시작합니다 (최초 1회 실행)")
    tickers = config.TICKERS_TO_MONITOR

    # 메인 프로세스에서만 데이터를 로드합니다.
    loaded_data = data_manager.load_all_ohlcv_data(tickers, interval='minute60')

    logging.info("데이터 클리닝: 인덱스 중복을 제거합니다.")
    for ticker, df in loaded_data.items():
        if df.index.has_duplicates:
            loaded_data[ticker] = df.groupby(df.index).last()

    all_params_to_calculate = []
    all_params_to_calculate.append({'sma_period': COMMON_REGIME_PARAMS['regime_sma_period'][0]})

    for group in EXPERIMENT_CONFIGS:
        param_grid = group.get('param_grid', {})
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        for v_combination in itertools.product(*values):
            all_params_to_calculate.append(dict(zip(keys, v_combination)))
    if hasattr(config, 'COMMON_EXIT_PARAMS'):
        all_params_to_calculate.append(config.COMMON_EXIT_PARAMS)

    for ticker in loaded_data.keys():
        loaded_data[ticker] = indicators.add_technical_indicators(
            df=loaded_data[ticker], all_params_list=all_params_to_calculate
        )
        loaded_data[ticker] = indicators.define_market_regime(
            df=loaded_data[ticker], adx_threshold=COMMON_REGIME_PARAMS['adx_threshold'][0],
            sma_period=COMMON_REGIME_PARAMS['regime_sma_period'][0]
        )
    logging.info("✅ 모든 보조지표 및 시장 국면 정의 완료.")

    all_experiments = []
    for group in EXPERIMENT_CONFIGS:
        strategy_name = group['strategy_name']
        param_grid = group.get('param_grid', {})
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        for v_combination in itertools.product(*values):
            strategy_combo = dict(zip(keys, v_combination))
            full_params = {**COMMON_REGIME_PARAMS, **strategy_combo, 'strategy_name': strategy_name}
            all_experiments.append(full_params)

    tasks = list(itertools.product(all_experiments, config.BACKTEST_INTERVALS))

    logging.info(f"총 {len(all_experiments)}개의 파라미터 조합과 {len(config.BACKTEST_INTERVALS)}개의 시간 간격으로,")
    logging.info(f"총 {len(tasks)}개의 백테스트 작업을 시작합니다 (최대 {config.CPU_CORES}개 동시 실행).")

    try:
        num_processes = min(config.CPU_CORES, cpu_count())
        # ✨ [멀티프로세싱 수정] initializer를 사용하여 각 프로세스에 데이터 전달
        with Pool(processes=num_processes, initializer=init_worker, initargs=(loaded_data,)) as pool:
            pool.map(run_backtest_task, tasks)
    except Exception as e:
        logging.error(f"멀티프로세싱 실행 중 오류 발생: {e}")

    logging.info("모든 백테스팅 작업이 완료되었습니다.")