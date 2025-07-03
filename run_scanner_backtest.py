# run_scanner_backtest.py
# '다수 코인 스캐너' 전략을 위한 최종 백테스팅 스크립트.
# portfolio, performance, results_handler 모듈과 연동하여 동작합니다.

import pandas as pd
from datetime import datetime
import logging # 로깅 추가
import itertools # 모든 파라미터 조합을 만들기 위해 파이썬 기본 라이브러리인 itertools를 임포트합니다.

import os

# --- 프로젝트의 핵심 모듈 임포트 ---
import config # API 키, DB 경로 등 주요 설정값을 관리하는 파일
from data import data_manager # 데이터베이스에서 데이터를 불러오는 역할
from utils import indicators # 기술적 보조지표를 계산하고 국면을 판단하는 역할
from strategies import strategy_signals # 실제 매수/매도 신호를 결정하는 로직
from core import scanner_portfolio  # 여러 자산을 동시에 관리하는 스캐너 전용 포트폴리오
from backtester import performance, results_handler  # 백테스트 결과를 분석하여 성과 지표를 계산하는 역할, 분석된 최종 결과를 DB에 저장하는 역할

# 로거 설정 (오류 추적을 위해)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# DEBUG_TARGET_DATE = "2021-02-01" # 예: 2021년 2월 1일의 상황을 정밀 분석

# ==============================================================================
# ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼ [설계도: 실험 설정 리스트] ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
# ==============================================================================
# 각 딕셔너리는 하나의 '실험 그룹'을 의미합니다.
# 그룹별로 다른 전략과 그에 맞는 파라미터를 테스트할 수 있습니다.
EXPERIMENT_CONFIGS = [
    # --- 실험 그룹 1: 터틀 트레이딩 전략 ---
    # '단순한 파수꾼' 전략의 최적값을 찾습니다.
    {
        'strategy_name': 'turtle',
        'param_grid': {
            'entry_period': [20, 40],
            'exit_period': [10, 20],
            'stop_loss_atr_multiplier': [2.0, 2.5],  # 터틀 전략용 손절 규칙
        }
    },

    # --- 실험 그룹 2: 추세 추종 전략 ---
    # '신중한 정예 파수꾼' 전략의 최적값을 찾습니다.
    {
        'strategy_name': 'trend_following',
        'param_grid': {
            'breakout_window': [20, 30],
            'volume_multiplier': [1.5, 2.0],
            'stop_loss_atr_multiplier': [2.0, 2.5],  # 추세 추종 전략용 손절 규칙
        }
    },
]

# --- 모든 실험에 공통으로 적용될 국면 판단 파라미터 ---
# 이 부분은 이전에 찾은 최적의 값으로 고정하여 사용합니다.
COMMON_REGIME_PARAMS = {
    'version': 'v1',
    'regime_sma_period': 10,
    'adx_threshold': 20,
}


# ==============================================================================
# ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ [설계도: 실험 설정 리스트] ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
# ==============================================================================


def perform_single_backtest(params: dict, all_data: dict):
    """하나의 파라미터 조합에 대한 단일 백테스트를 수행하는 함수."""

    # 파라미터 조합으로 고유한 실험 이름 생성
    experiment_name = "_".join(f"{key.replace('_', '')}{value}" for key, value in params.items())
    logging.info(f"\n{'=' * 80}\n🚀 [실험 시작] {experiment_name}\n{'=' * 80}")

    # --- 1. 파라미터 및 설정값 로드 ---
    strategy_name = params.get('strategy_name')

    # ✨ 전략에 따라 다른 파라미터 딕셔너리를 구성합니다.
    buy_params = {}
    exit_params = {}

    if strategy_name == 'turtle':
        buy_params = {'entry_period': params.get('entry_period')}
        exit_params = {
            'exit_period': params.get('exit_period'),
            'stop_loss_atr_multiplier': params.get('stop_loss_atr_multiplier')
        }
    elif strategy_name == 'trend_following':
        buy_params = {
            'breakout_window': params.get('breakout_window'),
            'volume_avg_window': 20,  # 고정값
            'volume_multiplier': params.get('volume_multiplier'),
            'long_term_sma_period': 50  # 고정값
        }
        exit_params = {
            'stop_loss_atr_multiplier': params.get('stop_loss_atr_multiplier'),
            'trailing_stop_percent': config.COMMON_EXIT_PARAMS.get('trailing_stop_percent')  # config에서 가져옴
        }

    initial_capital = config.INITIAL_CAPITAL
    max_trades = config.MAX_CONCURRENT_TRADES

    # --- 2. 기간 설정 및 포트폴리오 초기화 ---
    common_start = max([df.index.min() for df in all_data.values() if not df.empty])
    common_end = min([df.index.max() for df in all_data.values() if not df.empty])
    date_range = pd.date_range(start=common_start, end=common_end, freq='D')
    pm = scanner_portfolio.ScannerPortfolioManager(initial_capital=initial_capital)

    # --- 3. 핵심 백테스팅 루프 ---
    for current_date in date_range:
        print(f"\rProcessing: {current_date.strftime('%Y-%m-%d')}", end="")
        pm.update_portfolio_value(all_data, current_date)

        for ticker in pm.get_open_positions():
            position = pm.get_position(ticker)
            if current_date not in all_data[ticker].index: continue
            data_for_sell = all_data[ticker].loc[all_data[ticker].index <= current_date]

            # ✨ 청산 시에도 현재 실험의 전략과 파라미터를 사용합니다.
            sell_signal, reason = strategy_signals.get_sell_signal(
                data=data_for_sell, position=position, exit_params=exit_params,
                strategy_name=position.get('strategy'),  # 진입 시 사용했던 전략 이름
                strategy_params=position.get('params')  # 진입 시 사용했던 파라미터
            )
            if sell_signal:
                pm.execute_sell(ticker, data_for_sell['close'].iloc[-1], current_date, reason)

        if len(pm.get_open_positions()) < max_trades:
            # ✨ 국면 판단은 공통 파라미터를 사용합니다.
            regime_results = indicators.analyze_regimes_for_all_tickers(
                all_data, current_date, **COMMON_REGIME_PARAMS
            )

            ##################################디버깅 모드############################
            # log_this_date = (DEBUG_TARGET_DATE and pd.to_datetime(DEBUG_TARGET_DATE) == current_date)
            # bull_tickers = [t for t, r in regime_results.items() if r == 'bull']
            #
            # if log_this_date or bull_tickers:
            #     print(f"\n--- [{current_date.strftime('%Y-%m-%d')}] ---")
            #     # 1. 국면 판단 결과 전체를 확인합니다.
            #     print(f"  > 국면 판단 전체 결과: {regime_results}")
            #
            #     if not bull_tickers:
            #         print("  > 상승장 후보 없음. 매수 시도 안함.")
            #         continue  # 다음 날로 넘어감
            #
            #     print(f"  > 상승장 후보 발견: {bull_tickers}")
            #     candidates = indicators.rank_candidates_by_volume(bull_tickers, all_data, current_date)
            #     print(f"  > 우선순위 정렬: {candidates}")
            #
            #     for candidate_ticker in candidates:
            #         if candidate_ticker not in pm.get_open_positions():
            #             print(f"    - 후보 '{candidate_ticker}' 매수 신호 확인 중...")
            #             if current_date not in all_data[candidate_ticker].index: continue
            #             data_for_buy = all_data[candidate_ticker].loc[all_data[candidate_ticker].index <= current_date]
            #
            #             # 2. 매수 신호 함수를 직접 호출하여 결과를 확인합니다.
            #             buy_signal = strategy_signals.get_buy_signal(
            #                 data=data_for_buy,
            #                 strategy_name=buy_strategy['name'],
            #                 params=buy_strategy['params']
            #             )
            #             print(f"      > 매수 신호 결과: {buy_signal}")
            #
            #             if buy_signal:
            #                 print(f"      ✨✨✨ 매수 신호 발생! '{candidate_ticker}' 매수를 실행합니다. ✨✨✨")
            #                 pm.execute_buy(
            #                     ticker=candidate_ticker, price=data_for_buy['close'].iloc[-1], trade_date=current_date,
            #                     strategy_info={'strategy': buy_strategy['name'], 'params': buy_strategy['params']},
            #                     entry_atr=data_for_buy['ATR'].iloc[-1] if 'ATR' in data_for_buy.columns else 0
            #                 )
            #                 if len(pm.get_open_positions()) >= max_trades: break

            bull_tickers = [t for t, r in regime_results.items() if r == 'bull']
            candidates = indicators.rank_candidates_by_volume(bull_tickers, all_data, current_date)

            for candidate_ticker in candidates:
                if candidate_ticker not in pm.get_open_positions():
                    if current_date not in all_data[candidate_ticker].index: continue
                    data_for_buy = all_data[candidate_ticker].loc[all_data[candidate_ticker].index <= current_date]

                    # ✨ 매수 시에도 현재 실험의 전략과 파라미터를 사용합니다.
                    buy_signal = strategy_signals.get_buy_signal(
                        data=data_for_buy,
                        strategy_name=strategy_name,
                        params=buy_params
                    )
                    if buy_signal:
                        # --- ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼ [이 부분만 수정됩니다] ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼ ---
                        # 매수 실행 함수에 `all_data`를 전달해줍니다.
                        pm.execute_buy(
                            ticker=candidate_ticker, price=data_for_buy['close'].iloc[-1], trade_date=current_date,
                            strategy_info={'strategy': strategy_name, 'params': buy_params},
                            entry_atr=data_for_buy['ATR'].iloc[-1] if 'ATR' in data_for_buy.columns else 0,
                            all_data=all_data  # ✨ 추가된 인자
                        )
                        # --- ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ [이 부분만 수정됩니다] ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ ---
                        if len(pm.get_open_positions()) >= max_trades: break

    # --- 4. 시뮬레이션 종료 후, 성과 분석 및 결과 저장 ---
    print("\n")
    logging.info(f"--- 🏁 [{experiment_name}] 결과 분석 중... ---")
    trade_log_df = pm.get_trade_log_df()
    if trade_log_df.empty:
        logging.warning("거래가 발생하지 않았습니다.")
        return

    # performance 모듈을 호출하여 최종 성과 리포트를 생성합니다.
    daily_log_df = pm.get_daily_log_df()
    summary = performance.generate_summary_report(trade_log_df, daily_log_df, initial_capital)

    # 생성된 리포트를 화면에 출력합니다.
    print(f"\n--- [실험: {experiment_name} 최종 요약] ---")
    for key, value in summary.items():
        print(f"{key:<25}: {value}")

    # results_handler 모듈을 호출하여 최종 결과를 DB에 저장합니다.
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


# ==============================================================================
# ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼ [프로그램 시작 지점] ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
# ==============================================================================
if __name__ == '__main__':
    # 이 스크립트가 직접 실행될 때 아래 코드가 동작합니다.

    # --- 1. 데이터 로드 및 준비 (전체 테스트 과정에서 딱 한 번만 실행하여 효율성 확보) ---
    logging.info("데이터 로드 및 보조지표 계산을 시작합니다 (최초 1회 실행)")
    tickers = config.TICKERS_TO_MONITOR
    all_data = data_manager.load_all_ohlcv_data(tickers, interval='day')

    # --- ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼ [오류 수정 핵심] ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼ ---
    # 필요한 모든 지표를 계산하기 위해, 모든 파라미터 값을 수집합니다.
    all_params_for_indicators = [
        config.BULL_MARKET_STRATEGY.get('params', {}),
        config.COMMON_EXIT_PARAMS
    ]

    # COMMON_REGIME_PARAMS에서 단일 값을 가져와서 추가합니다.
    # 'regime_sma_period'는 리스트가 아닌 단일 정수(int)이므로, for 루프를 사용하지 않습니다.
    common_sma = COMMON_REGIME_PARAMS.get('regime_sma_period')
    if common_sma:
        all_params_for_indicators.append({'long_term_sma_period': common_sma})

    # EXPERIMENT_CONFIGS에 있는 모든 파라미터 리스트를 순회하며 값을 수집합니다.
    for group in EXPERIMENT_CONFIGS:
        param_grid = group.get('param_grid', {})
        for key, values in param_grid.items():
            if isinstance(values, list):
                for value in values:
                    all_params_for_indicators.append({key: value})
            else:  # 혹시 리스트가 아닌 단일 값이 들어올 경우도 대비
                all_params_for_indicators.append({key: values})
    # --- ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ [오류 수정 핵심] ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ ---

    for ticker in all_data.keys():
        all_data[ticker] = indicators.add_technical_indicators(
            df=all_data[ticker],
            strategies=all_params_for_indicators
        )
    logging.info("모든 티커의 보조지표 추가 완료.")

    # --- 2. 자동화된 백테스트 실행 ---
    all_experiments = []
    for config_group in EXPERIMENT_CONFIGS:
        strategy_name = config_group['strategy_name']
        param_grid = config_group['param_grid']
        keys = param_grid.keys()
        values = param_grid.values()

        combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
        for combo in combinations:
            # 공통 파라미터와 현재 조합을 합쳐서 완전한 파라미터 셋을 만듭니다.
            full_params = {**COMMON_REGIME_PARAMS, **combo, 'strategy_name': strategy_name}
            all_experiments.append(full_params)

    logging.info(f"총 {len(all_experiments)}개의 파라미터 조합으로 자동 최적화를 시작합니다.")

    for params in all_experiments:
        perform_single_backtest(params, all_data)