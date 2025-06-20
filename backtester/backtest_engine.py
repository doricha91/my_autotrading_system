# backtester/backtest_engine.py
# ⚙️ 백테스팅 실행을 총괄하는 엔진입니다.
# 그리드 서치, 다수 티커 테스트 등의 시나리오를 실행합니다.

import pandas as pd
import numpy as np
import itertools
import os
import logging

import config
from data import data_manager
from utils import indicators
from core import strategy
from . import performance  # 같은 backtester 폴더 내의 performance.py import

logger = logging.getLogger()

def _log_results_to_csv(result_data, log_file="advanced_backtest_log.csv"):
    """백테스팅 결과를 CSV 파일에 기록합니다."""
    try:
        is_file_exist = os.path.exists(log_file)
        df_result = pd.DataFrame([result_data])
        df_result.to_csv(log_file, index=False, mode='a', header=not is_file_exist, encoding='utf-8-sig')
    except Exception as e:
        logger.error(f"CSV 로그 파일 저장 중 오류 발생: {e}")

def _run_single_backtest(df_full_data: pd.DataFrame, params: dict) -> tuple:
    """
    단일 파라미터 조합에 대한 백테스팅을 실행합니다.
    (기존 advanced_backtest.py의 run_backtest 함수)
    """
    # 1. 신호 생성
    df_signals = strategy.generate_signals(df_full_data.copy(), params)

    # 2. 포트폴리오 및 거래 변수 초기화
    krw_balance = config.INITIAL_CAPITAL
    asset_balance = 0.0
    asset_avg_buy_price = 0.0
    trade_log = []
    portfolio_history = []
    highest_price_since_buy = 0
    partial_profit_taken = False

    # 3. 데이터 루프 실행
    for timestamp, row in df_signals.iterrows():
        current_price = row['close']
        atr = row.get('ATRr_14', 0)

        # 현재가 데이터가 없으면 포트폴리오 가치만 기록하고 건너뜀
        if pd.isna(current_price) or current_price <= 0:
            last_value = portfolio_history[-1]['portfolio_value'] if portfolio_history else config.INITIAL_CAPITAL
            portfolio_history.append({'timestamp': timestamp, 'portfolio_value': last_value})
            continue

        should_sell = False
        # 4. 청산 로직 (자산 보유 시)
        if asset_balance > 0:
            highest_price_since_buy = max(highest_price_since_buy, current_price)

            # 부분 익절 로직
            profit_target = params.get('partial_profit_target')
            if profit_target and not partial_profit_taken and (
                    current_price / asset_avg_buy_price - 1) >= profit_target:
                asset_to_sell = asset_balance * params.get('partial_profit_ratio', 0.5)
                if asset_to_sell * current_price >= config.MIN_ORDER_KRW:
                    krw_balance += (asset_to_sell * current_price * (1 - config.FEE_RATE));
                    asset_balance -= asset_to_sell;
                    partial_profit_taken = True
                    trade_log.append({'timestamp': timestamp, 'type': 'partial_sell', 'price': current_price,
                                      'amount': asset_to_sell})
                    portfolio_history.append(
                        {'timestamp': timestamp, 'portfolio_value': krw_balance + (asset_balance * current_price)})
                    continue

            # 1. ATR 손절매 (값이 있을 때만 실행)
            stop_loss = params.get('stop_loss_atr_multiplier')
            if not should_sell and stop_loss and atr > 0 and current_price < (
                    asset_avg_buy_price - (stop_loss * atr)): should_sell = True

            # 2. 트레일링 스탑 (값이 있을 때만 실행)
            trailing_stop = params.get('trailing_stop_percent')
            if not should_sell and trailing_stop and current_price < highest_price_since_buy * (
                    1 - trailing_stop): should_sell = True

            # 3. SMA 이탈 청산 (값이 있을 때만 실행)
            exit_sma_period = params.get('exit_sma_period')
            if not should_sell and exit_sma_period and exit_sma_period > 0:
                if current_price < row.get(f"SMA_{exit_sma_period}", float('inf')):
                    should_sell = True

            # 4. 터틀 전략 고유 청산 (값이 있을 때만 실행)
            if not should_sell and params.get('strategy_name') == 'turtle_trading':
                exit_period = params.get('exit_period')
                if exit_period and current_price < row.get(f'low_{exit_period}d', float('inf')): should_sell = True

            # 전략이 직접 매도 신호를 보냈을 경우
            if not should_sell and row.get('signal') == -1: should_sell = True

        # 5. 거래 실행
        if should_sell and asset_balance > 0:
            # 전량 매도
            krw_balance += (asset_balance * current_price * (1 - config.FEE_RATE))
            trade_log.append({'timestamp': timestamp, 'type': 'sell', 'price': current_price, 'amount': asset_balance})
            asset_balance = 0.0

        elif row.get('signal') == 1 and asset_balance == 0:
            # 매수
            buy_amount_krw = krw_balance * 1  # 예시: 95% 비중으로 매수
            if buy_amount_krw > config.MIN_ORDER_KRW:
                asset_acquired = (buy_amount_krw * (1 - config.FEE_RATE)) / current_price
                krw_balance -= buy_amount_krw
                asset_balance += asset_acquired
                asset_avg_buy_price = current_price
                highest_price_since_buy, partial_profit_taken = current_price, False
                trade_log.append(
                    {'timestamp': timestamp, 'type': 'buy', 'price': current_price, 'amount': asset_acquired})

        # 6. 포트폴리오 가치 기록
        portfolio_history.append(
            {'timestamp': timestamp, 'portfolio_value': krw_balance + (asset_balance * current_price)})

    return pd.DataFrame(trade_log), pd.DataFrame(portfolio_history)


def run_grid_search(start_date: str = None, end_date: str = None):
    """`config.py`의 그리드 서치 설정을 기반으로 백테스팅을 실행합니다."""
    logger.info("===== 그리드 서치 모드로 백테스팅을 시작합니다. =====")
    cfg = config.GRID_SEARCH_CONFIG
    ticker = cfg['target_ticker']
    interval = cfg['target_interval']

    # 1. 데이터 로드 및 지표 계산 (한 번만 실행)
    df_raw = data_manager.load_prepared_data(ticker, interval)
    if df_raw.empty:
        logger.error(f"{ticker} 데이터 로드 실패. 그리드 서치를 종료합니다.")
        return

    # 2. 파라미터 조합 생성
    keys = cfg['param_grid'].keys()
    values = cfg['param_grid'].values()
    param_combinations = [dict(zip(keys, combo)) for combo in itertools.product(*values)]

    all_strategies_to_run = []
    for i, combo_params in enumerate(param_combinations):
        params = {**cfg['base_params'], **combo_params}
        exp_name_parts = [f"{key[:4]}{val}" for key, val in combo_params.items()]
        params['experiment_name'] = f"GS_{cfg['target_strategy_name'][:5]}_{'_'.join(exp_name_parts)}_{i}"
        params['strategy_name'] = cfg['target_strategy_name']
        all_strategies_to_run.append(params)

    # 3. 지표 계산 (모든 조합에 필요한 지표를 한 번에 계산)
    df_with_indicators = indicators.add_technical_indicators(df_raw, all_strategies_to_run)
    df_ready = df_with_indicators

    if start_date and end_date:
        logger.info(f"백테스트 기간을 {start_date}부터 {end_date}까지로 제한합니다.")
        df_ready = df_ready.loc[start_date:end_date].copy()
        if df_ready.empty:
            logger.error("지정된 기간에 해당하는 데이터가 없어 백테스트를 중단합니다.")
            return

    # 4. 각 조합에 대해 백테스팅 실행
    all_results = []
    for params_to_run in all_strategies_to_run:
        logger.info(f"\n--- 실험 시작: {params_to_run['experiment_name']} ---")
        trade_log, portfolio_history = _run_single_backtest(df_ready.copy(), params_to_run)

        if not portfolio_history.empty:
            summary = performance.analyze_performance(portfolio_history, trade_log, config.INITIAL_CAPITAL, interval)
            summary.update({'실험명': params_to_run['experiment_name'], '파라미터': str(params_to_run)})
            all_results.append(summary)

    # 5. 최종 결과 출력 및 저장
    if all_results:
        results_df = pd.DataFrame(all_results).sort_values(by='Calmar', ascending=False)
        logger.info("\n\n" + "=" * 80 + "\n" + "💰 그리드 서치 최종 결과 요약 (Calmar 기준 정렬)" + "\n" + "=" * 80)
        print(results_df)
        results_df.to_csv("grid_search_results.csv", index=False, encoding='utf-8-sig')


def run_multi_ticker_test(start_date: str = None, end_date: str = None):
    """`config.py`의 다수 티커 설정을 기반으로 '왕중왕전' 백테스팅을 실행합니다."""
    logger.info("===== 다수 티커 '왕중왕전' 모드로 백테스팅을 시작합니다. =====")
    cfg = config.MULTI_TICKER_CONFIG

    # 1. 실행할 모든 전략 조합 생성
    strategies_to_run = []
    strategies_to_run = []
    for ticker in cfg['tickers_to_test']:
        for champ_config in cfg['champions_to_run']:
            params = champ_config.copy()
            strategy_params = params.pop('params', {})

            final_params = {
                **strategy_params,
                'strategy_name': params['strategy_name'],
                'experiment_name': f"{ticker}_{params['experiment_name_prefix']}",
                'ticker_tested': ticker
            }
            strategies_to_run.append(final_params)

    logger.info(f"총 {len(strategies_to_run)}개의 '티커-전략' 조합으로 테스트를 진행합니다.")

    # 2. 데이터 캐시 및 결과 리스트 초기화
    data_cache = {}
    all_results = []

    # 3. 각 티커-전략 조합에 대해 백테스팅 순차 실행
    for strategy_params in strategies_to_run:
        ticker = strategy_params['ticker_tested']
        interval = cfg['target_interval']

        if ticker not in data_cache:
            logger.info(f"\n\n===== {ticker} ({interval}) 데이터 로딩 및 지표 계산 =====")
            df_raw = data_manager.load_prepared_data(ticker, interval)
            if df_raw.empty:
                logger.error(f"{ticker} 데이터 로드 실패. 이 티커에 대한 테스트를 건너뜁니다.")
                continue

            strategies_for_this_ticker = [s for s in strategies_to_run if s.get('ticker_tested') == ticker]
            data_cache[ticker] = indicators.add_technical_indicators(df_raw, strategies_for_this_ticker)

        df_with_indicators = data_cache[ticker]

        # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
        # 작업: 날짜 필터링 기능 추가
        df_ready = df_with_indicators.copy()  # 원본 보존을 위해 복사
        if start_date and end_date:
            logger.info(f"백테스트 기간을 {start_date}부터 {end_date}까지로 제한합니다.")
            df_ready = df_ready.loc[start_date:end_date].copy()
            if df_ready.empty:
                logger.warning(f"{strategy_params['experiment_name']} 실험: 지정된 기간에 데이터가 없어 건너뜁니다.")
                continue

        # 4. 백테스팅 실행
        logger.info(f"\n--- 실험 시작: {strategy_params['experiment_name']} ---")
        trade_log_df, portfolio_history_df = _run_single_backtest(df_ready.copy(), strategy_params)

        # 5. 성과 분석 및 결과 저장
        if not portfolio_history_df.empty:
            summary = performance.analyze_performance(
                portfolio_history_df,
                trade_log_df,
                config.INITIAL_CAPITAL,
                interval
            )

            summary.update({
                '티커': ticker,
                '실험명': strategy_params['experiment_name'],
                '전략명': strategy_params['strategy_name'],
                '파라미터': str({k: v for k, v in strategy_params.items() if
                             k not in ['strategy_name', 'experiment_name', 'ticker_tested']})
            })

            all_results.append(summary)
            _log_results_to_csv(summary, log_file="multi_ticker_results.csv")

    # 6. 최종 결과 출력
    if all_results:
        results_df = pd.DataFrame(all_results)
        # 티커별, 그리고 Calmar 지수별로 정렬하여 보기 좋게 출력
        results_df = results_df.sort_values(by=['티커', 'Calmar'], ascending=[True, False])

        logger.info("\n\n" + "=" * 90 + "\n" + "<<< 👑 다수 티커 최종 결과 요약 (Calmar 기준 정렬) 👑 >>>".center(85) + "\n" + "=" * 90)

        # 출력할 컬럼 순서 지정
        cols_to_display = ['티커', '실험명', '전략명', 'ROI (%)', 'MDD (%)', 'Calmar', 'Sharpe', 'Profit Factor',
                           'Win Rate (%)', 'Total Trades']
        # DataFrame에 실제 존재하는 컬럼만 필터링하여 오류 방지
        cols_to_print = [col for col in cols_to_display if col in results_df.columns]

        print(results_df[cols_to_print].to_string())
        print("=" * 90)
    logger.info("===== 다수 티커 테스트 모드는 여기에 구현됩니다. =====")