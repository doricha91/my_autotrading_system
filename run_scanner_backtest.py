# run_scanner_backtest.py
# '다수 코인 스캐너' 전략을 위한 최종 백테스팅 스크립트.
# portfolio, performance, results_handler 모듈과 연동하여 동작합니다.

import pandas as pd
from datetime import datetime
import logging # 로깅 추가

import os

# --- 프로젝트의 핵심 모듈 임포트 ---
import config
from data import data_manager
from utils import indicators
from strategies import strategy_signals
from core import scanner_portfolio  # 포트폴리오 관리자 임포트
from backtester import performance, results_handler  # 성과 분석 모듈 임포트

# 로거 설정 (오류 추적을 위해)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def run_scanner_backtest(experiment_name: str = "scanner_v1"):
    """
    메인 백테스팅 함수
    :param experiment_name: 이번 백테스트의 고유 이름 (결과 저장 시 사용)
    """
    logging.info(f"🚀 [실험: {experiment_name}] 다수 코인 스캐너 전략 백테스팅을 시작합니다...")

    # --- 1. 설정값 로드 from config.py ---
    tickers = config.TICKERS_TO_MONITOR
    max_trades = config.MAX_CONCURRENT_TRADES
    buy_strategy = config.BULL_MARKET_STRATEGY
    exit_params = config.COMMON_EXIT_PARAMS
    initial_capital = config.INITIAL_CAPITAL

    # --- 2. 데이터 로드 ---
    logging.info(f"데이터 로딩 중... 대상 티커: {len(tickers)}개")
    all_data = data_manager.load_all_ohlcv_data(tickers, interval='day')
    loaded_tickers = list(all_data.keys())
    logging.info(f"로드 완료된 티커: {len(loaded_tickers)}개")

    # --- ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼ [오류 수정 핵심] ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼ ---
    # --- 3. 데이터 준비: 모든 데이터에 보조지표 추가 ---
    logging.info("데이터 준비 중... 각 티커에 보조지표를 추가합니다.")
    # 국면정의 및 매매전략에 필요한 모든 파라미터를 수집합니다.
    # indicators.py의 add_technical_indicators 함수가 이 정보를 사용합니다.
    required_params = [
        config.BULL_MARKET_STRATEGY.get('params', {}),
        config.COMMON_EXIT_PARAMS
    ]
    # 국면 정의에 필요한 sma_period: 50도 명시적으로 추가해줍니다.
    required_params.append({'long_term_sma_period': 50})

    for ticker in loaded_tickers:
        logging.info(f"  - {ticker} 보조지표 계산 중...")
        all_data[ticker] = indicators.add_technical_indicators(
            df=all_data[ticker],
            strategies=required_params
        )
    logging.info("모든 티커의 보조지표 추가 완료.")
    # --- ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ [오류 수정 핵심] ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ ---

    # 백테스트 기간 자동 설정
    common_start = max([df.index.min() for df in all_data.values() if not df.empty])
    common_end = min([df.index.max() for df in all_data.values() if not df.empty])
    date_range = pd.date_range(start=common_start, end=common_end, freq='D')
    logging.info(f"백테스트 기간: {common_start.date()} ~ {common_end.date()}")

    # --- 4. 포트폴리오 관리자 및 로그 초기화 ---
    pm = scanner_portfolio.ScannerPortfolioManager(initial_capital=initial_capital)

    # --- 5. ✨ 핵심 백테스팅 루프 ✨ ---
    for current_date in date_range:
        print(f"\rProcessing: {current_date.strftime('%Y-%m-%d')}", end="")

        pm.update_portfolio_value(all_data, current_date)

        for ticker in pm.get_open_positions():
            position = pm.get_position(ticker)
            if current_date not in all_data[ticker].index: continue
            data_for_sell = all_data[ticker].loc[all_data[ticker].index <= current_date]

            sell_signal, reason = strategy_signals.get_sell_signal(
                data=data_for_sell, position=position, exit_params=exit_params,
                strategy_name=position.get('strategy', buy_strategy['name']),
                strategy_params=position.get('params', buy_strategy['params'])
            )

            if sell_signal:
                pm.execute_sell(ticker, data_for_sell['close'].iloc[-1], current_date, reason)

        if len(pm.get_open_positions()) < max_trades:
            regime_results = indicators.analyze_regimes_for_all_tickers(all_data, current_date)
            bull_tickers = [t for t, r in regime_results.items() if r == 'bull']
            candidates = indicators.rank_candidates_by_volume(bull_tickers, all_data, current_date)

            for candidate_ticker in candidates:
                if candidate_ticker not in pm.get_open_positions():
                    if current_date not in all_data[candidate_ticker].index: continue
                    data_for_buy = all_data[candidate_ticker].loc[all_data[candidate_ticker].index <= current_date]

                    buy_signal = strategy_signals.get_buy_signal(
                        data=data_for_buy,
                        strategy_name=buy_strategy['name'],
                        params=buy_strategy['params']
                    )
                    if buy_signal:
                        pm.execute_buy(
                            ticker=candidate_ticker, price=data_for_buy['close'].iloc[-1], trade_date=current_date,
                            strategy_info={'strategy': buy_strategy['name'], 'params': buy_strategy['params']},
                            entry_atr=data_for_buy['ATR'].iloc[-1] if 'ATR' in data_for_buy.columns else 0
                        )
                        if len(pm.get_open_positions()) >= max_trades: break

    # --- 6. 백테스트 성과 분석 ---
    print("\n")
    # logging.info("--- 🏁 백테스팅 결과 분석 중... ---")
    trade_log_df = pm.get_trade_log_df()
    if trade_log_df.empty:
        logging.warning("거래가 발생하지 않았습니다.")
        return

    daily_log_df = pm.get_daily_log_df()
    summary = performance.generate_summary_report(trade_log_df, daily_log_df, initial_capital)

    print("\n--- [ 최종 성과 요약 ] ---")
    for key, value in summary.items():
        print(f"{key:<20}: {value}")

    # --- 7. 최종 결과 저장 ---
    logging.info("--- 💾 최종 결과 저장 중... ---")
    summary_df = pd.DataFrame([summary])
    summary_df['experiment_name'] = experiment_name
    summary_df['run_date'] = datetime.now()

    try:
        # --- ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼ [오류 수정 최종] ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼ ---
        # results_handler.py에 있는 save_results 함수를 직접 호출합니다.
        # 이 함수는 내부적으로 DB 연결부터 저장, 연결 종료까지 모두 처리합니다.
        results_handler.save_results(
            results_df=summary_df,
            table_name='scanner_backtest_summary'  # 테이블 이름 지정
        )
        # --- ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ [오류 수정 최종] ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ ---
    except Exception as e:
        logging.error(f"오류: 결과를 DB에 저장하는 중 문제가 발생했습니다 - {e}")


if __name__ == '__main__':
    run_scanner_backtest(experiment_name="Scanner_TrendFollowing_V1.2")