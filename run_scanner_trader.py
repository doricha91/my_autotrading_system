# run_scanner_trader.py
# 🤖 스캐너 기반 실시간/모의 매매를 실행하는 파일입니다. (최종 수정본)

import time
import logging
import openai
import pyupbit
import requests
import sqlite3
import pandas as pd
from datetime import datetime
import traceback  # ✨ 1. 상세한 오류 출력을 위한 traceback 모듈 임포트

import config
from data import data_manager
from apis import upbit_api, ai_analyzer
from core import strategy, portfolio, trade_executor
from backtester import scanner
from utils import indicators, notifier  # ✨ notifier.py 임포트

# 로거를 설정합니다.
logger = logging.getLogger()

# ==============================================================================
# 2. 매수 판단 전용 함수 (✨ 역할 변경: 전략 실행기)
# ==============================================================================
def _execute_buy_logic_for_ticker(ticker, upbit_client, openai_client, current_regime: str):
    """
    [매수 판단 전용 함수]
    전달받은 'current_regime'에 해당하는 전략을 실행하여 최종 매수/매도/보류를 결정합니다.
    """
    logger.info(f"\n======= 티커 [{ticker}], 국면 [{current_regime}] 최종 매수 판단 시작 =======")
    pm = portfolio.PortfolioManager(
        mode=config.RUN_MODE, upbit_api_client=upbit_client,
        initial_capital=config.INITIAL_CAPITAL_PER_TICKER, ticker=ticker
    )
    current_position = pm.get_current_position()

    # 1. 데이터 로드 및 보조지표 추가
    df_raw = data_manager.load_prepared_data(ticker, config.TRADE_INTERVAL, for_bot=True)
    if df_raw.empty:
        logger.warning(f"[{ticker}] 데이터 로드에 실패하여 매수 판단을 중단합니다.")
        return False

    all_possible_params = [s.get('params', {}) for s in config.ENSEMBLE_CONFIG['strategies']]
    all_possible_params.extend([s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()])
    df_final = indicators.add_technical_indicators(df_raw, all_possible_params)

    # 2. 전달받은 국면에 맞는 전략으로 1차 신호를 생성합니다.
    final_signal_str, signal_score = 'hold', 0.0
    if config.ACTIVE_STRATEGY_MODEL == 'regime_switching':
        strategy_config = config.REGIME_STRATEGY_MAP.get(current_regime)
        if strategy_config:
            strategy_name = strategy_config.get('name')
            logger.info(f"[{ticker}] 국면 '{current_regime}' -> '{strategy_name}' 전략 실행")
            strategy_config['strategy_name'] = strategy_name # generate_signals 함수가 사용할 수 있도록 추가
            df_with_signal = strategy.generate_signals(df_final, strategy_config)
            signal_val = df_with_signal.iloc[-1].get('signal', 0)
            final_signal_str = 'buy' if signal_val > 0 else 'sell' if signal_val < 0 else 'hold'
            signal_score = abs(signal_val)

    # 3. AI 분석을 통해 최종 결정을 내립니다.
    ai_decision = ai_analyzer.get_ai_trading_decision(ticker, df_final.tail(30), final_signal_str, signal_score)
    final_decision, ratio, reason = trade_executor.determine_final_action(
        final_signal_str, ai_decision, current_position, df_final.iloc[-1], config.ENSEMBLE_CONFIG
    )

    # 4. 최종 결정에 따라 거래 및 기록을 실행합니다.
    # ✨ 참고: 판단 시점의 가격을 정확히 기록하기 위해 현재가를 한 번 더 조회하거나,
    # df_final에서 가져올 수 있습니다. 여기서는 후자를 사용합니다.
    price_at_decision = df_final.iloc[-1]['close']

    # ✨ 4-1. 어떤 결정이든 먼저 'decision_log'에 기록합니다.
    trade_executor.log_final_decision(
        decision=final_decision,
        reason=reason,
        ticker=ticker,
        price_at_decision=price_at_decision
    )

    # ✨ 4-2. 'buy' 또는 'sell'일 경우에만 '거래'를 실행하고 'paper_trade_log'에 기록합니다.
    trade_executor.execute_trade(
        decision=final_decision,
        ratio=ratio,
        reason=reason,
        ticker=ticker,
        portfolio_manager=pm,
        upbit_api_client=upbit_client
    )

    return True


# ==============================================================================
# 3. 메인 실행 함수 (✨ 역할 변경: Control Tower)
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
    last_exit_check_time = time.time()

    while True:
        try:
            now = datetime.now()
            current_time = time.time()

            # ✨ 2. [구조 변경] 개별 쓰레드 관리 로직을 모두 삭제합니다.

            # --- ✨ 3. [신규 로직] 중앙 집중형 청산 감시 로직 ---
            # PRICE_CHECK_INTERVAL_SECONDS마다 한 번씩 모든 보유 코인을 한꺼번에 확인
            if current_time - last_exit_check_time >= config.PRICE_CHECK_INTERVAL_SECONDS:
                last_exit_check_time = current_time

                # 1. DB에서 현재 보유 중인 모든 코인 목록을 가져옵니다.
                with sqlite3.connect(f"file:{db_manager.db_path}?mode=ro", uri=True) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("SELECT * FROM paper_portfolio_state WHERE asset_balance > 0")
                    all_positions = [dict(row) for row in cursor.fetchall()]

                if all_positions:
                    logger.info(f"중앙 감시: 보유 코인 {len(all_positions)}개에 대한 청산 조건을 확인합니다.")

                    # 2. 단 한 번의 API 호출로 모든 코인의 현재가를 가져옵니다.
                    tickers_to_check = [pos['ticker'] for pos in all_positions]
                    current_prices = pyupbit.get_current_price(tickers_to_check)

                    if not isinstance(current_prices, dict):  # 단일 코인일 경우 float이 반환될 수 있음
                        if current_prices is not None:
                            current_prices = {tickers_to_check[0]: current_prices}
                        else:
                            current_prices = {}

                    # 3. 각 코인을 순회하며 청산 조건 확인
                    for position in all_positions:
                        ticker = position['ticker']
                        current_price = current_prices.get(ticker)

                        if current_price is None:
                            logger.warning(f"[{ticker}] 중앙 감시 중 현재가 조회 실패. 다음 주기에 다시 확인합니다.")
                            continue

                        # 청산 감시에 필요한 최신 데이터 로드 및 지표 계산
                        df_raw = data_manager.load_prepared_data(ticker, config.TRADE_INTERVAL, for_bot=True)
                        if df_raw.empty: continue

                        all_params = [s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()]
                        df_final = indicators.add_technical_indicators(df_raw, all_params)

                        # 포트폴리오 객체를 생성하여 상태 업데이트 및 매도 실행
                        pm = portfolio.PortfolioManager(
                            mode=config.RUN_MODE, upbit_api_client=upbit_client_instance,
                            ticker=ticker
                        )
                        pm.update_highest_price(current_price)  # 이동 손절을 위한 최고가 업데이트

                        should_sell, reason = trade_executor.check_fast_exit_conditions(
                            position=position, current_price=current_price,
                            latest_data=df_final.iloc[-1], exit_params=config.COMMON_EXIT_PARAMS
                        )

                        if should_sell:
                            logger.info(f"[{ticker}] 중앙 감시 중 청산 조건 충족! 이유: {reason}")
                            trade_executor.execute_trade(
                                decision='sell', ratio=1.0, reason=reason, ticker=ticker,
                                portfolio_manager=pm, upbit_api_client=upbit_client_instance
                            )

            # --- 2. 신규 매수 로직 실행 (국면별 전략 분기) ---
            if now.hour % config.TRADE_INTERVAL_HOURS == 0 and now.hour != last_execution_hour:
                logger.info(f"✅ 정해진 매매 시간({now.hour}시)입니다. 유망 코인 스캔 및 매수 판단을 시작합니다.")
                last_execution_hour = now.hour

                # ✨ [핵심 수정 1] 스캐너로부터 필터링 없는 후보군과 국면 분석 결과를 받습니다.
                target_tickers, all_regimes = scanner_instance.scan_tickers()

                if not target_tickers:
                    logger.warning("❌ 스캐너가 유망 코인을 찾지 못했습니다.")
                    message = f"ℹ️ 매매 주기 알림 ({now.hour}시)\n\n스캐너가 유망 코인을 찾지 못해 이번 매매는 건너뜁니다."
                    notifier.send_telegram_message(message.strip())
                else:
                    # ✨ [핵심 수정 2] 메인 루프에서 국면을 재분석하는 로직을 삭제하여 스캐너의 분석을 100% 신뢰합니다.
                    # 이제 realtime_regime_results 대신 all_regimes를 바로 사용합니다.

                    details = [f"- {ticker} ({all_regimes.get(ticker, 'N/A')})" for ticker in target_tickers]
                    details_message = "\n".join(details)
                    message = f"🎯 유망 코인 스캔 완료 ({now.hour}시)\n\n[발견된 코인 및 현재 국면]\n{details_message}\n\n정의된 전략이 있는 코인의 매수 판단을 시작합니다..."
                    notifier.send_telegram_message(message.strip())

                    for ticker in target_tickers:
                        regime = all_regimes.get(ticker)
                        # `config.py`의 `REGIME_STRATEGY_MAP`에 해당 국면(regime)에 대한 전략이 정의되어 있는지 확인
                        if regime in config.REGIME_STRATEGY_MAP:
                            if ticker not in held_tickers:
                                logger.info(f"✅ '{ticker}' ({regime} 국면) 최종 매수 판단을 시작합니다.")
                                try:
                                    # 매수 판단 함수에 `regime`을 인자로 전달
                                    was_executed = _execute_buy_logic_for_ticker(
                                        ticker, upbit_client_instance, openai_client_instance, regime
                                    )
                                    if was_executed:
                                        main_logic_executed_in_this_tick = True
                                except Exception as e:
                                    logger.error(f"[{ticker}] 매수 판단 중 오류 발생: {e}", exc_info=True)
                            else:
                                logger.info(f"❌ '{ticker}' ({regime} 국면)은(는) 이미 보유 중이므로 건너뜁니다.")
                        else:
                            logger.info(f"❌ '{ticker}' ({regime} 국면)에 대한 전략이 `config.py`에 정의되지 않아 건너뜁니다.")
            else:
                logger.info(f"매매 실행 시간(매 {config.TRADE_INTERVAL_HOURS}시간)이 아니므로, 신규 매수 판단을 건너뜁니다.")

            # --- 3. 사이클 카운터 및 회고 분석 ---
            if main_logic_executed_in_this_tick:
                logger.info(f"✅ 매수 판단 로직이 완료되어 스캔 사이클을 1 증가시킵니다.")
                trade_cycle_count += 1
                db_manager.set_system_state('scanner_trade_cycle_count', str(trade_cycle_count))
                logger.info(f"✅ 새로운 스캔 사이클: {trade_cycle_count}")

                # ✨ 수정: 변수가 필요한 시점 직전에 DB에서 값을 불러옵니다.
                last_analysis_timestamp_str = db_manager.get_system_state('last_analysis_timestamp',
                                                                          '1970-01-01T00:00:00')
                last_analysis_dt = datetime.fromisoformat(last_analysis_timestamp_str)
                time_since_last = now - last_analysis_dt

                trigger_by_count = (trade_cycle_count % config.REFLECTION_INTERVAL_CYCLES == 0)
                trigger_by_time = time_since_last.days >= 7

                if hasattr(config, 'REFLECTION_INTERVAL_CYCLES') and trade_cycle_count > 0 and (
                        trigger_by_count or trigger_by_time):
                    logger.info(f"🧠 회고 분석 시스템을 시작합니다. (이유: 횟수충족={trigger_by_count}, 시간충족={trigger_by_time})")
                    if hasattr(ai_analyzer, 'perform_retrospective_analysis'):
                        if 'target_tickers' in locals() and target_tickers:
                            representative_ticker = target_tickers[0]
                            analysis_pm = portfolio.PortfolioManager(
                                mode=config.RUN_MODE, ticker=representative_ticker,
                                upbit_api_client=upbit_client_instance
                            )
                            ai_analyzer.perform_retrospective_analysis(
                                openai_client_instance,
                                analysis_pm,
                                trade_cycle_count
                            )
                            # 분석 후, 현재 시간을 DB에 다시 기록
                            db_manager.set_system_state('last_analysis_timestamp', datetime.now().isoformat())

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