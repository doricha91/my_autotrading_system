# run_scanner_backtest_multi.py
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
from core.strategy import get_strategy_function
from core import scanner_portfolio
from backtester import performance, results_handler

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# EXPERIMENT_CONFIGS와 COMMON_REGIME_PARAMS는 이전과 동일하게 유지합니다.
TEST_SCENARIOS = [
    # {
    #     'scenario_name': 'Bull_Market_Test',
    #     'strategy_name': 'hybrid_trend_strategy',
    #     'target_regimes': ['bull'], # 상승장만 타겟
    #     'param_grid': {
    #         'breakout_window': [480],
    #         'volume_avg_window': [600],
    #         'volume_multiplier': [1.6],
    #         'long_term_sma_period': [1200],
    #         'exit_sma_period': [240],
    #         'short_ma': [180],
    #         'long_ma': [480],
    #         'stop_loss_atr_multiplier': [1.5],
    #         'trailing_stop_percent': [0.2],
    #     }
    # },
    {
        'scenario_name': 'Sideways_Bear_Market_Test',
        'strategy_name': 'bb_rsi_mean_reversion',
        'target_regimes': ['sideways', 'bear'], # 횡보장, 하락장 타겟
        'param_grid': {
            'bb_period': [5],
            'bb_std_dev': [1.5],
            'rsi_period': [7],
            'oversold_level': [30],
            'stop_loss_atr_multiplier': [1.5],
            'trailing_stop_percent': [0.15],
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
    scenario, params, interval = task_info

    if params is None:
        logging.error("작업자 함수에 'params'가 None으로 전달되었습니다. 작업을 건너뜁니다.")
        return

    # ✨ 수정: 시나리오에서 전략 이름과 목표 국면을 가져옵니다.
    strategy_name = scenario['strategy_name']
    target_regimes = scenario['target_regimes']

    # 파라미터 분리 (전략용 buy_params, 공통 청산용 exit_params)
    exit_param_keys = ['stop_loss_atr_multiplier', 'trailing_stop_percent']
    buy_params = {k: v for k, v in params.items() if k not in exit_param_keys}
    exit_params = {k: v for k, v in params.items() if k in exit_param_keys}

    # 하이브리드 전략의 경우 buy_params를 재구성해야 합니다.
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

    base_experiment_name = "_".join(f"{key.replace('_', '')}{value}" for key, value in params.items())
    # ✨ 수정: 실험 이름에 시나리오 이름을 포함하여 구분을 명확하게 합니다.
    experiment_name = f"{scenario['scenario_name']}_{base_experiment_name}_{interval}H"
    logging.info(f"🚀 [작업 시작] {experiment_name}")

    # ✨ 수정: 시나리오에 맞는 전략 함수를 동적으로 가져옵니다.
    strategy_func = get_strategy_function(strategy_name)

    precomputed_signals = {}
    for ticker, df in all_data.items():
        df_with_signal = strategy_func(df.copy(), buy_params)
        # ✨ [핵심 수정] 시나리오의 target_regimes를 사용하여 매수 조건을 동적으로 설정합니다.
        # .isin() 함수는 리스트에 포함된 여러 국면을 한 번에 확인할 수 있게 해줍니다.
        is_target_regime = df_with_signal['regime'].isin(target_regimes)
        buy_mask = (df_with_signal['signal'] == 1) & is_target_regime
        precomputed_signals[ticker] = buy_mask

    # ✨ [진단 로그 추가]
    # 첫 번째 티커(BTC)에 대해서만 신호 생성 과정을 상세히 출력하여 로그가 너무 많아지는 것을 방지합니다.
    first_ticker = list(all_data.keys())[0]
    if first_ticker in all_data:
        df_sample = all_data[first_ticker].copy()

        # 1. 국면 분석 결과 확인
        regime_counts = df_sample['regime'].value_counts()
        logging.info(f"[{experiment_name}] [진단 로그 - {first_ticker}] 국면 분포:\n{regime_counts}")

        # 2. 국면 필터링 전, 순수 전략 신호 확인
        df_with_raw_signal = strategy_func(df_sample, buy_params)
        raw_buy_signals = (df_with_raw_signal['signal'] == 1).sum()
        logging.info(f"[{experiment_name}] [진단 로그 - {first_ticker}] Raw 매수 신호(signal==1) 발생 횟수: {raw_buy_signals}")

        # 3. 국면 필터링 후, 최종 신호 확인
        final_buy_signals = precomputed_signals[first_ticker].sum()
        logging.info(
            f"[{experiment_name}] [진단 로그 - {first_ticker}] 최종 매수 신호(signal==1 & target_regime) 발생 횟수: {final_buy_signals}")

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

    for scenario in TEST_SCENARIOS:
        param_grid = scenario.get('param_grid', {})
        keys, values = list(param_grid.keys()), list(param_grid.values())
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

    tasks = []
    for scenario in TEST_SCENARIOS:
        param_grid = scenario.get('param_grid', {})
        keys, values = list(param_grid.keys()), list(param_grid.values())
        # 각 파라미터 조합 생성
        param_combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]

        # (시나리오, 파라미터 조합, 시간 간격) 튜플을 생성하여 tasks 리스트에 추가
        for params in param_combinations:
            for interval in config.BACKTEST_INTERVALS:
                tasks.append((scenario, params, interval))

    logging.info(f"총 {len(tasks)}개의 백테스트 작업을 시작합니다 (최대 {config.CPU_CORES}개 동시 실행).")

    try:
        num_processes = min(config.CPU_CORES, cpu_count())
        with Pool(processes=num_processes, initializer=init_worker, initargs=(loaded_data,)) as pool:
            pool.map(run_backtest_task, tasks)
    except Exception as e:
        logging.error(f"멀티프로세싱 실행 중 오류 발생: {e}")

    logging.info("모든 백테스팅 작업이 완료되었습니다.")