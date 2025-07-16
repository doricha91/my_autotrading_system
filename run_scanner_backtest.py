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
    {
        'strategy_name': 'hybrid_trend_strategy',
        'param_grid': {
            # --- 1차 전략 (신고가 돌파) 파라미터 ---
            'breakout_window': [30],  # 20일 또는 30일 신고가
            'volume_avg_window': [30],
            'volume_multiplier': [1.5],  # 거래량 1.5배
            'long_term_sma_period': [50],  # 50일 이평선으로 추세 판단
            'exit_sma_period': [10],  # ✨ trend_following의 청산 주기를 명시적으로 추가

            # --- 2차 전략 (추세 지속) 파라미터 ---
            'short_ma': [5, 10, 20],  # 20일 단기 이평선
            'long_ma': [30, 50, 70, 90],  # 60일 장기 이평선

            # --- 공통 청산 파라미터 ---
            'stop_loss_atr_multiplier': [1.5],
            'trailing_stop_percent': [0.1],
        }
    },
    # {
    #     'strategy_name': 'turtle',
    #     'param_grid': {
    #         'entry_period': [10, 20, 30, 50],
    #         'exit_period': [5, 10, 20, 30],
    #         'stop_loss_atr_multiplier': [2.0],
    #           'trailing_stop_percent': [0.1, 0.15],  # 예: 고점 대비 10% 또는 15% 하락 시 청산
    #     }
    # },
    # {
    #     'strategy_name': 'trend_following',
    #     'param_grid': {
    #         'breakout_window': [30],
    #         'volume_multiplier': [1.5],
    #         'volume_avg_window': [30],
    #         'long_term_sma_period': [50],
    #         'stop_loss_atr_multiplier': [1.5],
    #         'trailing_stop_percent': [0.1],  # 예: 고점 대비 10% 또는 15% 하락 시 청산
    #     }
    # },
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
    elif strategy_name == 'hybrid_trend_strategy':
        # 하이브리드 전략은 두 개의 하위 전략 파라미터를 모두 필요로 합니다.
        # 이 구조는 core/strategy.py의 hybrid_trend_strategy_signal 함수와 약속된 형태입니다.
        buy_params = {
            'trend_following_params': {
                'breakout_window': params.get('breakout_window'),
                'volume_avg_window': params.get('volume_avg_window'),
                'volume_multiplier': params.get('volume_multiplier'),
                'long_term_sma_period': params.get('long_term_sma_period'),
            },
            'ma_trend_params': {
                'short_ma': params.get('short_ma'),
                'long_ma': params.get('long_ma'),
            }
        }
        # 청산 파라미터는 공통으로 사용합니다.
        exit_params = {
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
            # ✨ 1. [핵심 수정] analyze_regimes_for_all_tickers 함수 호출을 제거합니다.
            #    대신, 미리 계산된 'regime' 컬럼에서 현재 날짜의 값을 직접 조회합니다.
            regime_results = {}
            for ticker, df in all_data.items():
                if current_date in df.index:
                    regime_results[ticker] = df.loc[current_date, 'regime']

            # 2. Bull 국면인 코인만 필터링합니다. (기존과 동일)
            bull_tickers = [t for t, r in regime_results.items() if r == 'bull']
            candidates = indicators.rank_candidates_by_volume(bull_tickers, all_data, current_date)

            # 3. 각 후보에 대해 매수 신호를 확인합니다. (기존과 동일)
            for candidate_ticker in candidates:
                if candidate_ticker not in pm.get_open_positions():
                    if current_date not in all_data[candidate_ticker].index: continue

                    # 이제 data_for_buy는 보조지표가 모두 계산된 완전한 데이터를 포함합니다.
                    data_for_buy = all_data[candidate_ticker].loc[all_data[candidate_ticker].index <= current_date]

                    # 'SMA_10' 오류가 더 이상 발생하지 않습니다.
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

    # --- 1. 백테스트에 필요한 '모든' 파라미터를 명확하게 수집합니다 ---
    all_params_to_calculate = []

    # (A) 국면 판단에 필요한 파라미터 수집
    # 'regime_sma_period' -> 'sma_period'로 키 이름을 변경하여 전달
    all_params_to_calculate.append({'sma_period': COMMON_REGIME_PARAMS['regime_sma_period'][0]})

    # (B) 실험할 모든 파라미터 조합을 수집
    for group in EXPERIMENT_CONFIGS:
        param_grid = group.get('param_grid', {})
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        for v_combination in itertools.product(*values):
            all_params_to_calculate.append(dict(zip(keys, v_combination)))

    # (C) 공통 청산 규칙 파라미터도 추가
    all_params_to_calculate.append(config.COMMON_EXIT_PARAMS)

    # --- 2. 수집된 파라미터로 모든 보조지표와 국면을 미리 계산합니다 ---
    for ticker in all_data.keys():
        # (A) 일반 보조지표 추가
        # 이제 all_params_to_calculate에는 SMA_10, SMA_20 등이 모두 포함되어 있습니다.
        all_data[ticker] = indicators.add_technical_indicators(
            df=all_data[ticker],
            all_params_list=all_params_to_calculate
        )
        # (B) 시장 국면('regime' 컬럼) 정의
        all_data[ticker] = indicators.define_market_regime(
            df=all_data[ticker],
            adx_threshold=COMMON_REGIME_PARAMS['adx_threshold'][0],
            sma_period=COMMON_REGIME_PARAMS['regime_sma_period'][0]
        )
    logging.info("✅ 모든 보조지표 및 시장 국면 정의 완료.")

    # --- 3. 파라미터 조합 생성 및 백테스팅 루프 실행 (기존과 유사하게 재구성) ---
    all_experiments = []
    for group in EXPERIMENT_CONFIGS:
        strategy_name = group['strategy_name']
        param_grid = group.get('param_grid', {})
        keys = list(param_grid.keys())
        values = list(param_grid.values())

        # itertools.product를 사용하여 모든 파라미터 조합 생성
        for v_combination in itertools.product(*values):
            strategy_combo = dict(zip(keys, v_combination))
            # 공통 국면 파라미터와 전략 파라미터를 합쳐 최종 실험 세트 구성
            full_params = {**COMMON_REGIME_PARAMS, **strategy_combo, 'strategy_name': strategy_name}
            all_experiments.append(full_params)

    logging.info(f"총 {len(all_experiments)}개의 파라미터 조합으로 자동 최적화를 시작합니다.")
    for params in all_experiments:
        perform_single_backtest(params, all_data)