# run_scanner_backtest.py
# '다수 코인 스캐너' 전략을 위한 최종 백테스팅 스크립트.
# portfolio, performance, results_handler 모듈과 연동하여 동작합니다.

import pandas as pd
from datetime import datetime
import logging
import itertools

import os

# --- 프로젝트의 핵심 모듈 임포트 ---
import config
from data import data_manager
from utils import indicators
from strategies import strategy_signals
from core import scanner_portfolio
from backtester import performance, results_handler

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# EXPERIMENT_CONFIGS와 COMMON_REGIME_PARAMS는 이전과 동일하게 유지합니다.
EXPERIMENT_CONFIGS = [
    # {
    #     'strategy_name': 'turtle',
    #     'param_grid': {
    #         'entry_period': [10, 20, 30, 50],
    #         'exit_period': [5, 10, 20, 30],
    #         'stop_loss_atr_multiplier': [2.0],
    #           'trailing_stop_percent': [0.1, 0.15],  # 예: 고점 대비 10% 또는 15% 하락 시 청산
    #     }
    # },
    {
        'strategy_name': 'trend_following',
        'param_grid': {
            'breakout_window': [30],
            'volume_multiplier': [1.5],
            'volume_avg_window': [30],
            'long_term_sma_period': [50],
            'stop_loss_atr_multiplier': [1.5],
            'trailing_stop_percent': [0.1],  # 예: 고점 대비 10% 또는 15% 하락 시 청산
        }
    },
]

COMMON_REGIME_PARAMS = {
    'version': 'v1',
    'regime_sma_period': [20],
    'adx_threshold': [20]
}


def perform_single_backtest(params: dict, all_data: dict):
    """하나의 파라미터 조합에 대한 단일 백테스트를 수행하는 함수."""

    experiment_name = "_".join(f"{key.replace('_', '')}{value}" for key, value in params.items())
    logging.info(f"\n{'=' * 80}\n🚀 [실험 시작] {experiment_name}\n{'=' * 80}")

    strategy_name = params.get('strategy_name')
    buy_params = {}
    exit_params = {}

    if strategy_name == 'turtle':
        buy_params = {'entry_period': params.get('entry_period')}
        exit_params = {
            'exit_period': params.get('exit_period'),
            'stop_loss_atr_multiplier': params.get('stop_loss_atr_multiplier'),
            'trailing_stop_percent': params.get('trailing_stop_percent')
        }
    elif strategy_name == 'trend_following':
        buy_params = {
            'breakout_window': params.get('breakout_window'),
            'volume_avg_window': params.get('volume_avg_window'),
            'volume_multiplier': params.get('volume_multiplier'),
            'ling_term_sma_period': params.get('long_term_sma_period'),
        }
        exit_params = {
            'stop_loss_atr_multiplier': params.get('stop_loss_atr_multiplier'),
            'trailing_stop_percent': params.get('trailing_stop_percent')
        }

    initial_capital = config.INITIAL_CAPITAL
    max_trades = config.MAX_CONCURRENT_TRADES

    common_start = max([df.index.min() for df in all_data.values() if not df.empty])
    common_end = min([df.index.max() for df in all_data.values() if not df.empty])
    date_range = pd.date_range(start=common_start, end=common_end, freq='D')
    pm = scanner_portfolio.ScannerPortfolioManager(initial_capital=initial_capital)

    for current_date in date_range:
        print(f"\rProcessing: {current_date.strftime('%Y-%m-%d')}", end="")
        pm.update_portfolio_value(all_data, current_date)

        for ticker in pm.get_open_positions():
            position = pm.get_position(ticker)
            if current_date not in all_data[ticker].index: continue
            data_for_sell = all_data[ticker].loc[all_data[ticker].index <= current_date]

            sell_signal, reason = strategy_signals.get_sell_signal(
                data=data_for_sell, position=position, exit_params=exit_params,
                strategy_name=position.get('strategy'),
                strategy_params=position.get('params')
            )
            if sell_signal:
                pm.execute_sell(ticker, data_for_sell['close'].iloc[-1], current_date, reason)

        if len(pm.get_open_positions()) < max_trades:
            # regime_results = indicators.analyze_regimes_for_all_tickers(
            #     all_data, current_date, **COMMON_REGIME_PARAMS
            # )
            current_regime_params = {
                'version': params.get('version'),
                'regime_sma_period': params.get('regime_sma_period'),
                'adx_threshold': params.get('adx_threshold')
            }
            regime_results = indicators.analyze_regimes_for_all_tickers(
                all_data, current_date, **current_regime_params
            )

            bull_tickers = [t for t, r in regime_results.items() if r == 'bull']
            candidates = indicators.rank_candidates_by_volume(bull_tickers, all_data, current_date)

            for candidate_ticker in candidates:
                if candidate_ticker not in pm.get_open_positions():
                    if current_date not in all_data[candidate_ticker].index: continue
                    data_for_buy = all_data[candidate_ticker].loc[all_data[candidate_ticker].index <= current_date]

                    buy_signal = strategy_signals.get_buy_signal(
                        data=data_for_buy,
                        strategy_name=strategy_name,
                        params=buy_params
                    )
                    if buy_signal:
                        pm.execute_buy(
                            ticker=candidate_ticker, price=data_for_buy['close'].iloc[-1], trade_date=current_date,
                            strategy_info={'strategy': strategy_name, 'params': buy_params},
                            entry_atr=data_for_buy['ATR'].iloc[-1] if 'ATR' in data_for_buy.columns else 0,
                            all_data=all_data
                        )
                        if len(pm.get_open_positions()) >= max_trades: break

    print("\n")
    logging.info(f"--- 🏁 [{experiment_name}] 결과 분석 중... ---")
    trade_log_df = pm.get_trade_log_df()
    if trade_log_df.empty:
        logging.warning("거래가 발생하지 않았습니다.")
        return

    daily_log_df = pm.get_daily_log_df()
    summary = performance.generate_summary_report(trade_log_df, daily_log_df, initial_capital)

    print(f"\n--- [실험: {experiment_name} 최종 요약] ---")
    for key, value in summary.items():
        print(f"{key:<25}: {value}")

    summary_df = pd.DataFrame([summary])
    summary_df['experiment_name'] = experiment_name
    summary_df['run_date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        results_handler.save_results(
            results_df=summary_df,
            table_name='scanner_backtest_summary'
        )
    except Exception as e:
        logging.error(f"오류: [{experiment_name}] 결과를 DB에 저장하는 중 문제가 발생했습니다 - {e}")


if __name__ == '__main__':
    logging.info("데이터 로드 및 보조지표 계산을 시작합니다 (최초 1회 실행)")
    tickers = config.TICKERS_TO_MONITOR
    all_data = data_manager.load_all_ohlcv_data(tickers, interval='day')

    all_params_for_indicators = [
        config.REGIME_STRATEGY_MAP.get('bull', {}).get('params', {}),
        config.COMMON_EXIT_PARAMS
    ]

    common_sma = COMMON_REGIME_PARAMS.get('regime_sma_period')
    if common_sma:
        all_params_for_indicators.append({'long_term_sma_period': common_sma})

    for group in EXPERIMENT_CONFIGS:
        param_grid = group.get('param_grid', {})
        for key, values in param_grid.items():
            if isinstance(values, list):
                for value in values:
                    all_params_for_indicators.append({key: value})
            else:
                all_params_for_indicators.append({key: values})

    for ticker in all_data.keys():
        # --- ✨✨✨ 핵심 수정 부분 ✨✨✨ ---
        # [수정] 함수의 인자 이름을 'strategies'에서 'all_params_list'로 변경합니다.
        all_data[ticker] = indicators.add_technical_indicators(
            df=all_data[ticker],
            all_params_list=all_params_for_indicators
        )
        # --- ✨✨✨ 수정 끝 ✨✨✨ ---
    logging.info("모든 티커의 보조지표 추가 완료.")

    # 1. 공통 국면 파라미터의 모든 조합을 먼저 생성합니다.
    common_keys = COMMON_REGIME_PARAMS.keys()
    common_values = COMMON_REGIME_PARAMS.values()
    # 'version'과 같은 단일 값도 itertools.product가 처리할 수 있도록 리스트로 감싸줍니다.
    processed_common_values = [v if isinstance(v, list) else [v] for v in common_values]
    common_combinations = [dict(zip(common_keys, v)) for v in itertools.product(*processed_common_values)]

    # 2. 모든 실험 조합을 저장할 최종 리스트를 초기화합니다.
    all_experiments = []

    # 3. 생성된 국면 조합 각각에 대해, 모든 전략 파라미터 조합을 합칩니다.
    for common_combo in common_combinations:
        for config_group in EXPERIMENT_CONFIGS:
            strategy_name = config_group['strategy_name']
            param_grid = config_group['param_grid']

            strategy_keys = param_grid.keys()
            strategy_values = param_grid.values()

            strategy_combinations = [dict(zip(strategy_keys, v)) for v in itertools.product(*strategy_values)]

            for strategy_combo in strategy_combinations:
                # [국면 조합] + [전략 조합] + [전략 이름] 으로 완전한 하나의 실험 세트를 만듭니다.
                full_params = {**common_combo, **strategy_combo, 'strategy_name': strategy_name}
                all_experiments.append(full_params)

    # --- 이 아래 부분은 기존 코드와 동일합니다. ---
    logging.info(f"총 {len(all_experiments)}개의 파라미터 조합으로 자동 최적화를 시작합니다.")

    for params in all_experiments:
        perform_single_backtest(params, all_data)
