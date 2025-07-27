# run_scanner_trader.py
# 🤖 스캐너 기반 실시간/모의 매매를 실행하는 파일입니다. (최종 수정본)

import time
import logging
import openai
import pyupbit
import requests
import threading # ✨ 1. 동시 처리를 위한 threading 모듈 임포트
import sqlite3
import pandas as pd
from datetime import datetime

import config
from data import data_manager
from apis import upbit_api, ai_analyzer
from core import strategy, portfolio, trade_executor
from backtester import scanner
from utils import indicators, notifier  # ✨ notifier.py 임포트

# 로거를 설정합니다.
logger = logging.getLogger()


# ==============================================================================
# 1. 청산 감시 전용 함수 (독립적인 로봇으로 작동)
# ==============================================================================
def _handle_exit_logic(ticker, upbit_client):
    """
    [청산 감시 전용 쓰레드 함수]
    이 함수는 이제 독립적인 '감시 로봇(쓰레드)'으로 실행됩니다.
    하나의 코인에 대해서만 책임지고, 청산될 때까지 계속 감시합니다.
    """
    try:
        logger.info(f"✅ [{ticker}] 신규 청산 감시 쓰레드를 시작합니다.")

        # 이 감시 로봇을 위한 전용 포트폴리오 매니저를 생성합니다.
        # 이렇게 하면 각 쓰레드가 다른 쓰레드의 데이터에 영향을 주지 않습니다.
        pm = portfolio.PortfolioManager(
            mode=config.RUN_MODE, upbit_api_client=upbit_client,
            initial_capital=config.INITIAL_CAPITAL_PER_TICKER, ticker=ticker
        )

        # config 파일에서 공통 청산 규칙을 가져옵니다.
        exit_params = config.COMMON_EXIT_PARAMS if hasattr(config, 'COMMON_EXIT_PARAMS') else {}

        # 청산되거나, 메인 프로그램이 종료될 때까지 무한 반복합니다.
        while True:
            # 먼저 DB를 확인하여, 포지션이 여전히 유효한지 체크합니다.
            position = pm.get_current_position()
            if position.get('asset_balance', 0) == 0:
                logger.info(f"[{ticker}] 포지션이 청산되어 감시 쓰레드를 종료합니다.")
                break  # 포지션이 없으면 루프 탈출 -> 쓰레드 종료

            # 청산 감시에 필요한 데이터를 주기적으로 업데이트합니다.
            df_raw = data_manager.load_prepared_data(ticker, config.TRADE_INTERVAL, for_bot=True)
            if df_raw.empty:
                time.sleep(config.PRICE_CHECK_INTERVAL_SECONDS)
                continue

            all_possible_params = [s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()]
            df_final = indicators.add_technical_indicators(df_raw, all_possible_params)

            # 현재가를 빠르게 조회합니다.
            current_price = upbit_client.get_current_price(ticker)
            if not current_price:
                time.sleep(config.PRICE_CHECK_INTERVAL_SECONDS)
                continue

            # 포트폴리오 최고가를 업데이트합니다.
            if hasattr(pm, 'update_highest_price'):
                pm.update_highest_price(current_price)

            # 빠른 청산 조건을 확인합니다.
            should_sell, reason = trade_executor.check_fast_exit_conditions(
                position=position, current_price=current_price,
                latest_data=df_final.iloc[-1], exit_params=exit_params
            )

            # 청산 조건이 만족되면, 즉시 매도 주문을 실행하고 루프를 탈출합니다.
            if should_sell:
                logger.info(f"[{ticker}] 청산 조건 충족! 이유: {reason}")
                trade_executor.execute_trade(
                    decision='sell', ratio=1.0, reason=reason, ticker=ticker,
                    portfolio_manager=pm, upbit_api_client=upbit_client
                )
                break

                # 설정된 짧은 주기로 대기합니다.
            time.sleep(config.PRICE_CHECK_INTERVAL_SECONDS)

    except Exception as e:
        logger.error(f"[{ticker}] 청산 감시 쓰레드 실행 중 오류 발생: {e}", exc_info=True)
        # 쓰레드에서 오류 발생 시, 텔레그램 알림을 보낼 수도 있습니다.
        notifier.send_telegram_message(f"🚨 [{ticker}] 청산 감시 중단! 오류: {e}")


# ==============================================================================
# 2. 매수 판단 전용 함수 (기존 로직을 분리)
# ==============================================================================
def _execute_buy_logic_for_ticker(ticker, upbit_client, openai_client):
    """
    [매수 판단 전용 함수]
    보유하지 않은 코인에 대해서만 매수 여부를 판단하고 실행합니다.
    실제로 매수 판단 로직이 실행되었는지 여부(True/False)를 반환합니다.
    """
    logger.info(f"\n======= 티커 [{ticker}] 매수 판단 시작 =======")

    # 매수 판단을 위한 임시 포트폴리오 매니저
    pm = portfolio.PortfolioManager(
        mode=config.RUN_MODE, upbit_api_client=upbit_client,
        initial_capital=config.INITIAL_CAPITAL_PER_TICKER, ticker=ticker
    )
    current_position = pm.get_current_position()

    # --- 아래부터는 기존의 모든 매수 판단 로직이 동일하게 실행됩니다 ---
    # 1. 분석에 필요한 최신 데이터를 로드하고 보조지표를 추가합니다.
    df_raw = data_manager.load_prepared_data(ticker, config.TRADE_INTERVAL, for_bot=True)
    if df_raw.empty:
        # ✨ [진단 로그] 데이터 로드 실패 시 로그
        logger.warning(f"[{ticker}] 데이터 로드에 실패하여 매수 판단을 중단합니다.")
        return False

    all_possible_params = [s.get('params', {}) for s in config.ENSEMBLE_CONFIG['strategies']]
    all_possible_params.extend([s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()])
    df_final = indicators.add_technical_indicators(df_raw, all_possible_params)
    # df_final = indicators.define_market_regime(df_final)
    #
    # # 2. 'bull' 국면이 아니면 매수 판단을 중단합니다.
    # current_regime = df_final.iloc[-1].get('regime', 'sideways')
    # if current_regime != 'bull':
    #     logger.info(f"[{ticker}] 현재 국면 '{current_regime}' (bull 아님). 매수 로직을 중단합니다.")
    #     return False

    # logger.info(f"[{ticker}] 'bull' 국면 확인. 전략 신호 생성을 계속합니다.")

    # 3. 설정된 전략 모델에 따라 1차 신호를 생성합니다.
    current_regime = 'bull'
    final_signal_str, signal_score = 'hold', 0.0
    if config.ACTIVE_STRATEGY_MODEL == 'regime_switching':
        strategy_config = config.REGIME_STRATEGY_MAP.get(current_regime)
        if strategy_config:
            strategy_name = strategy_config.get('name')
            logger.info(f"[{ticker}] 현재 국면 '{current_regime}' -> '{strategy_name}' 전략 실행")
            strategy_config['strategy_name'] = strategy_name
            df_with_signal = strategy.generate_signals(df_final, strategy_config)
            signal_val = df_with_signal.iloc[-1].get('signal', 0)
            final_signal_str = 'buy' if signal_val > 0 else 'sell' if signal_val < 0 else 'hold'
            signal_score = abs(signal_val)

    # 4. AI 분석을 통해 최종 결정을 내립니다.
    ai_decision = ai_analyzer.get_ai_trading_decision(ticker, df_final.tail(30), final_signal_str, signal_score)
    final_decision, ratio, reason = trade_executor.determine_final_action(
        final_signal_str, ai_decision, current_position, df_final.iloc[-1], config.ENSEMBLE_CONFIG
    )

    # 5. 최종 결정에 따라 거래를 실행하고, 이 결과를 텔레그램으로 알립니다.
    trade_executor.execute_trade(
        decision=final_decision, ratio=ratio, reason=reason, ticker=ticker,
        portfolio_manager=pm, upbit_api_client=upbit_client
    )

    # 6. 여기까지 성공적으로 실행되었다면, "매매 로직이 실행되었음"을 알립니다.
    return True


# ==============================================================================
# 3. 메인 실행 함수 (모든 것을 지휘하는 오케스트라)
# ==============================================================================
def run():
    """[메인 실행 함수] 스캐너와 동시 처리 청산 감시 로직을 실행합니다."""
    logger = logging.getLogger()
    logger.info("🚀 스캐너 기반 자동매매 봇을 시작합니다.")
    notifier.send_telegram_message("🤖 자동매매 봇이 시작되었습니다.")

    upbit_client_instance = upbit_api.UpbitAPI(config.UPBIT_ACCESS_KEY, config.UPBIT_SECRET_KEY)
    openai_client_instance = openai.OpenAI(api_key=config.OPENAI_API_KEY) if config.OPENAI_API_KEY else None
    scanner_instance = scanner.Scanner(settings=config.SCANNER_SETTINGS)
    HEALTHCHECK_URL = config.HEALTHCHECK_URL if hasattr(config, 'HEALTHCHECK_URL') else None
    db_manager = portfolio.DatabaseManager(config.LOG_DB_PATH)
    trade_cycle_count = int(db_manager.get_system_state('scanner_trade_cycle_count', '0'))

    exit_monitoring_threads = {}
    last_execution_hour = -1

    # --- 메인 루프 ---
    while True:
        try:
            now = datetime.now()
            logger.info(f"\n--- 시스템 주기 확인 시작 (현재 시간: {now.strftime('%H:%M:%S')}, 사이클: {trade_cycle_count}) ---")

            # ✨ [핵심 수정] 변수를 try 블록 상단에서 미리 선언하여 항상 존재하도록 보장합니다.
            main_logic_executed_in_this_tick = False

            # --- 1. 청산 감시 쓰레드 관리 ---
            with sqlite3.connect(f"file:{db_manager.db_path}?mode=ro", uri=True) as conn:
                all_positions_df = pd.read_sql_query("SELECT ticker FROM paper_portfolio_state WHERE asset_balance > 0",
                                                     conn)
            held_tickers = set(all_positions_df['ticker'].tolist())
            running_threads = set(exit_monitoring_threads.keys())

            tickers_to_start_monitoring = held_tickers - running_threads
            for ticker in tickers_to_start_monitoring:
                thread = threading.Thread(target=_handle_exit_logic, args=(ticker, upbit_client_instance), daemon=True)
                thread.start()
                exit_monitoring_threads[ticker] = thread

            tickers_to_stop_monitoring = running_threads - held_tickers
            for ticker in tickers_to_stop_monitoring:
                if ticker in exit_monitoring_threads:
                    logger.info(f"[{ticker}] 포지션이 청산되어 감시 쓰레드를 정리합니다.")
                    del exit_monitoring_threads[ticker]

            # --- 2. 신규 매수 로직 실행 ---
            # ✨ 이제 main_logic_executed_in_this_tick 변수는 이 블록 바깥에 선언되어 있습니다.
            if now.hour % config.TRADE_INTERVAL_HOURS == 0 and now.hour != last_execution_hour:
                logger.info(f"✅ 정해진 매매 시간({now.hour}시)입니다. 유망 코인 스캔 및 매수 판단을 시작합니다.")
                last_execution_hour = now.hour

                target_tickers, all_regimes = scanner_instance.scan_tickers()

                if not target_tickers:
                    logger.warning("❌ 스캐너가 1차 유망 코인을 찾지 못했습니다.")
                    message = f"ℹ️ 매매 주기 알림 ({now.hour}시)\n\n스캐너가 1차 유망 코인을 찾지 못해 이번 매매는 건너뜁니다."
                    notifier.send_telegram_message(message.strip())
                else:
                    logger.info(f"스캐너 1차 통과 대상: {target_tickers}. 이제 현재 시점의 국면을 정밀 분석합니다...")

                    realtime_regime_results = {}
                    for ticker in target_tickers:
                        df_raw = data_manager.load_prepared_data(ticker, config.TRADE_INTERVAL, for_bot=True)
                        if not df_raw.empty:
                            all_params = [s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()]
                            df_final = indicators.add_technical_indicators(df_raw, all_params)
                            df_final = indicators.define_market_regime(df_final)
                            current_regime = df_final.iloc[-1].get('regime', 'N/A')
                            realtime_regime_results[ticker] = current_regime

                    details = [f"- {ticker} ({regime})" for ticker, regime in realtime_regime_results.items()]
                    details_message = "\n".join(details)

                    message = f"""
                        🎯 유망 코인 정밀 분석 완료 ({now.hour}시)

                        [발견된 코인 및 현재 국면]
                        {details_message}

                        'bull' 국면 코인에 대한 매수 판단을 시작합니다...
                        """
                    notifier.send_telegram_message(message.strip())

                    for ticker, regime in realtime_regime_results.items():
                        if regime == 'bull':
                            if ticker not in held_tickers:
                                logger.info(f"✅ '{ticker}'은(는) Bull 국면이므로 최종 매수 판단을 시작합니다.")
                                try:
                                    was_executed = _execute_buy_logic_for_ticker(ticker, upbit_client_instance,
                                                                                 openai_client_instance)
                                    if was_executed:
                                        main_logic_executed_in_this_tick = True
                                except Exception as e:
                                    logger.error(f"[{ticker}] 매수 판단 중 오류 발생: {e}", exc_info=True)
                            else:
                                logger.info(f"❌ '{ticker}'은(는) Bull 국면이지만 이미 보유 중이므로 건너뜁니다.")
                        else:
                            logger.info(f"❌ '{ticker}'은(는) Bull 국면이 아니므로 건너뜁니다.")
            else:
                logger.info(f"매매 실행 시간(매 {config.TRADE_INTERVAL_HOURS}시간)이 아니므로, 신규 매수 판단을 건너뜁니다.")

            # --- 3. 사이클 카운터 및 회고 분석 ---
            if main_logic_executed_in_this_tick:
                logger.info(f"✅ 매수 판단 로직이 완료되어 스캔 사이클을 1 증가시킵니다.")
                trade_cycle_count += 1
                db_manager.set_system_state('scanner_trade_cycle_count', trade_cycle_count)
                logger.info(f"✅ 새로운 스캔 사이클: {trade_cycle_count}")

                if hasattr(config, 'REFLECTION_INTERVAL_CYCLES') and trade_cycle_count > 0 and \
                        trade_cycle_count % config.REFLECTION_INTERVAL_CYCLES == 0:
                    logger.info("🧠 회고 분석 시스템을 시작합니다...")
                    if hasattr(ai_analyzer, 'perform_retrospective_analysis'):
                        if 'target_tickers' in locals() and target_tickers:
                            representative_ticker = target_tickers[0]
                            analysis_pm = portfolio.PortfolioManager(
                                mode=config.RUN_MODE, ticker=representative_ticker,
                                upbit_api_client=upbit_client_instance
                            )
                            ai_analyzer.perform_retrospective_analysis(openai_client_instance, analysis_pm)

            logger.info(f"--- 시스템 주기 확인 종료, {config.FETCH_INTERVAL_SECONDS}초 대기 ---")

            # --- 4. Healthcheck 및 대기 ---
            if HEALTHCHECK_URL:
                try:
                    requests.get(HEALTHCHECK_URL, timeout=10)
                    logger.info("✅ Healthcheck Ping 신호 전송 성공.")
                except requests.RequestException as e:
                    logger.warning(f"⚠️ Healthcheck Ping 신호 전송 실패: {e}")

            time.sleep(config.FETCH_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            logger.info("사용자에 의해 프로그램이 중단되었습니다.")
            notifier.send_telegram_message("ℹ️ 사용자에 의해 시스템이 중단되었습니다.")
            break
        except Exception as e:
            error_message = f"🚨 시스템 비상! 메인 루프에서 심각한 오류가 발생했습니다.\n\n오류: {e}"
            logger.error(f"매매 실행 중 예외 발생: {e}", exc_info=True)
            notifier.send_telegram_message(error_message)
            time.sleep(config.FETCH_INTERVAL_SECONDS)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    run()