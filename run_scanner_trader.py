# run_scanner_trader.py
# 🤖 스캐너 기반 실시간/모의 매매를 실행하는 파일입니다.

import time
import logging
import openai
import pyupbit
import requests

import config
from datetime import datetime
from data import data_manager
from apis import upbit_api, ai_analyzer
from core import strategy, portfolio, trade_executor
from backtester import scanner
from utils import indicators

logger = logging.getLogger()

def _handle_exit_logic(portfolio_manager, upbit_client, df_full, exit_params):
    """
    ✨ [신규 함수] ✨
    자산을 보유하고 있을 때, 빠른 청산 감시 루프를 실행합니다.
    """
    ticker = portfolio_manager.ticker
    logger.info(f"[{ticker}] '청산 감시 모드'를 시작합니다. (빠른 루프 진입)")

    fast_loop_start_time = time.time()

    while True:
        # 1. 느린 루프의 주기가 다 되면 빠른 루프를 탈출하여 전략을 다시 점검합니다.
        elapsed_time = time.time() - fast_loop_start_time
        if elapsed_time >= config.FETCH_INTERVAL_SECONDS:
            logger.info(f"[{ticker}] 전략 점검 시간이 되어 청산 감시 모드를 종료합니다.")
            break

        # 2. 현재가 조회 (Upbit API 호출 최소화)
        current_price = upbit_client.get_current_price(ticker)
        if not current_price:
            time.sleep(config.PRICE_CHECK_INTERVAL_SECONDS)
            continue

        # 3. 포지션 정보와 최고가 업데이트
        # ✨ 중요 ✨: portfolio.py의 PortfolioManager에 실시간 최고가를 업데이트하는 로직 추가가 필요합니다.
        # 예: portfolio_manager.update_highest_price(current_price)
        position = portfolio_manager.get_current_position()

        # 4. 빠른 청산 조건 확인
        should_sell, reason = trade_executor.check_fast_exit_conditions(
            position=position,
            current_price=current_price,
            latest_data=df_full.iloc[-1], # ATR 등은 느린 루프에서 계산한 값을 그대로 사용
            exit_params=exit_params
        )

        # 5. 청산 조건 만족 시 매도 실행
        if should_sell:
            logger.info(f"[{ticker}] 빠른 루프에서 청산 조건 충족! 이유: {reason}")
            trade_executor.execute_trade(
                decision='sell', ratio=1.0, reason=reason, ticker=ticker,
                portfolio_manager=portfolio_manager, upbit_api_client=upbit_client
            )
            break # 매도 후 빠른 루프 종료

        # 6. 짧은 대기
        time.sleep(config.PRICE_CHECK_INTERVAL_SECONDS)


def _execute_trade_cycle_for_ticker(ticker, upbit_client, openai_client):
    """
    [수정] try...finally 구문을 사용하여, 어떤 경우에도 포트폴리오 업데이트가 실행되도록 보장합니다.
    """
    logger.info(f"\n======= 티커 [{ticker}] 거래 로직 시작 =======")

    portfolio_manager = portfolio.PortfolioManager(
        mode=config.RUN_MODE, upbit_api_client=upbit_client,
        initial_capital=config.INITIAL_CAPITAL_PER_TICKER, ticker=ticker
    )

    # ✨ 1. 이 함수가 최종적으로 반환할 결과값을 미리 만듭니다.
    main_logic_was_executed = False

    try:
        current_position = portfolio_manager.get_current_position()

        # --- 보유 자산이 있을 경우: '청산 감시' 로직 실행 ---
        if current_position.get('asset_balance', 0) > 0:
            df_raw = data_manager.load_prepared_data(ticker, config.TRADE_INTERVAL, for_bot=True)
            if df_raw.empty: return False

            all_possible_params = [s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()]
            df_final = indicators.add_technical_indicators(df_raw, all_possible_params)
            exit_params = config.ENSEMBLE_CONFIG.get('common_exit_params', {})
            _handle_exit_logic(portfolio_manager, upbit_client, df_final, exit_params)
            main_logic_was_executed = False  # 청산 로직만 실행했으므로 False

        # --- 보유 자산이 없을 경우: '매수' 로직 실행 ---
        else:
            if hasattr(config, 'BUY_EXECUTION_TIME') and config.BUY_EXECUTION_TIME:
                current_time_str = datetime.now().strftime("%H:%M")
                if current_time_str != config.BUY_EXECUTION_TIME:
                    logger.info(
                        f"[{ticker}] 현재 시간({current_time_str})이 매수 실행 시간({config.BUY_EXECUTION_TIME})이 아니므로 매수 로직을 건너뜁니다.")
                    return False  # ✨ 여기서 return 해도 finally는 실행됩니다.

            logger.info(f"[{ticker}] 매수 실행 시간이 되어 매수 로직을 진행합니다.")

            df_raw = data_manager.load_prepared_data(ticker, config.TRADE_INTERVAL, for_bot=True)
            if df_raw.empty: return False

            all_possible_params = [s.get('params', {}) for s in config.ENSEMBLE_CONFIG['strategies']]
            all_possible_params.extend([s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()])
            df_final = indicators.add_technical_indicators(df_raw, all_possible_params)
            df_final = indicators.define_market_regime(df_final)

            current_regime = df_final.iloc[-1].get('regime', 'sideways')
            if current_regime != 'bull':
                logger.info(f"[{ticker}] 현재 국면 '{current_regime}' (bull 아님). 매수 로직을 중단합니다.")
                return False  # ✨ 여기서 return 해도 finally는 실행됩니다.

            logger.info(f"[{ticker}] 'bull' 국면 확인. 전략 신호 생성을 계속합니다.")

            # 2. 전략 신호 생성
            final_signal_str, signal_score = 'hold', 0.0

            if config.ACTIVE_STRATEGY_MODEL == 'ensemble':
                final_signal_str, signal_score = strategy.get_ensemble_strategy_signal(df_final, config.ENSEMBLE_CONFIG)

            elif config.ACTIVE_STRATEGY_MODEL == 'regime_switching':
                current_regime = df_final.iloc[-1].get('regime', 'sideways')
                strategy_config = config.REGIME_STRATEGY_MAP.get(current_regime)
                if strategy_config:
                    strategy_name = strategy_config.get('name')
                    logger.info(f"[{ticker}] 현재 국면 '{current_regime}' -> '{strategy_name}' 전략 실행")
                    strategy_config['strategy_name'] = strategy_name
                    df_with_signal = strategy.generate_signals(df_final, strategy_config)
                    signal_val = df_with_signal.iloc[-1].get('signal', 0)
                    final_signal_str = 'buy' if signal_val > 0 else 'sell' if signal_val < 0 else 'hold'
                    signal_score = abs(signal_val)
                else:
                    logger.warning(f"[{ticker}] '{current_regime}' 국면에 대한 전략이 정의되지 않았습니다.")

            # 3. AI 분석 및 최종 결정 (기존과 동일)
            ai_decision = ai_analyzer.get_ai_trading_decision(ticker, df_final.tail(30), final_signal_str, signal_score)
            final_decision, ratio, reason = trade_executor.determine_final_action(
                final_signal_str, ai_decision, current_position, df_final.iloc[-1], config.ENSEMBLE_CONFIG
            )

            # ✨✨✨ 디버깅 코드 추가 ✨✨✨
            # 최종 결정을 내리기 직전의 핵심 정보들을 로그로 남겨 확인합니다.
            logger.info("--- 최종 결정 직전 신호 확인 ---")
            logger.info(f"전략 신호 (Ensemble/Regime): {final_signal_str} (점수: {signal_score:.2f})")
            logger.info(f"AI 신호 (OpenAI): {ai_decision}")
            # ✨✨✨ 디버깅 코드 끝 ✨✨✨

            # 4. 매수 결정 시에만 주문 실행
            # if final_decision == 'buy':
            trade_executor.execute_trade(
                decision=final_decision, ratio=ratio, reason=reason, ticker=ticker,
                portfolio_manager=portfolio_manager, upbit_api_client=upbit_client
            )
            main_logic_was_executed = True # ✨ 매수 관련 로직이 끝났으므로 True로 변경

        # ✨ 매수 관련 로직이 모두 성공적으로 끝났으므로 True 반환
        return True

    finally:
        # ✨ 2. 이 finally 블록은 try 블록이 어떻게 끝나든 (return 되더라도) 항상 실행됩니다.
        logger.info(f"[{ticker}] 거래 사이클 종료. 포트폴리오 상태를 업데이트합니다.")
        if config.RUN_MODE == 'simulation':
            current_price = pyupbit.get_current_price(ticker)
            if current_price:
                portfolio_manager.update_and_save_state(current_price)

        # ✨ 3. 함수 맨 마지막에서 최종 결과값을 반환합니다.
        return main_logic_was_executed


def run():
    """스캐너 기반 자동매매 봇의 메인 루프를 실행합니다."""
    logger = logging.getLogger()
    logger.info(f"🚀 스캐너 기반 자동매매 봇을 시작합니다.")

    upbit_client_instance = upbit_api.UpbitAPI(config.UPBIT_ACCESS_KEY, config.UPBIT_SECRET_KEY)
    openai_client_instance = openai.OpenAI(api_key=config.OPENAI_API_KEY) if config.OPENAI_API_KEY else None
    scanner_instance = scanner.Scanner(settings=config.SCANNER_SETTINGS)

    # ✨ 2. Healthchecks.io에서 발급받은 본인의 고유 주소를 여기에 넣습니다.
    HEALTHCHECK_URL = "https://hc-ping.com/fb28952f-9432-4508-bf4b-6525002c249c"

    # ✨ 2. DB 매니저를 생성하고, DB에서 마지막 사이클 횟수를 불러옵니다.
    db_manager = portfolio.DatabaseManager(config.LOG_DB_PATH)
    trade_cycle_count = int(db_manager.get_system_state('scanner_trade_cycle_count', '0'))

    while True:
        try:
            # ✨ 1. 이 틱(60초 주기)에서 9시 매매 로직이 실행되었는지 추적할 플래그
            main_logic_executed_in_this_tick = False

            # ✨ 로그 메시지 수정: '스캔 사이클' -> '시스템 주기 확인'으로 변경하여 혼동 방지
            logger.info(f"\n--- 시스템 주기 확인 시작 (현재 사이클: {trade_cycle_count}) ---")

            target_tickers = scanner_instance.scan_tickers()
            if not target_tickers:
                logger.info("거래 대상 코인을 찾지 못했습니다.")
            else:
                logger.info(f"🎯 스캔 완료! 거래 대상: {target_tickers}")
                for ticker in target_tickers:
                    try:
                        # ✨ 2. 함수가 반환하는 실행 여부 값을 받습니다.
                        was_executed = _execute_trade_cycle_for_ticker(ticker, upbit_client_instance,
                                                                       openai_client_instance)
                        if was_executed:
                            # 한 번이라도 True가 반환되면, 이번 틱에서 매수 로직이 실행된 것으로 간주
                            main_logic_executed_in_this_tick = True
                    except Exception as e:
                        logger.error(f"[{ticker}] 처리 중 오류 발생. 다음 티커로 계속합니다: {e}", exc_info=True)

            # ✨ 3. 9시 매매 로직이 한 번이라도 실행된 경우에만 사이클 카운트를 올립니다.
            if main_logic_executed_in_this_tick:
                logger.info(f"✅ 9시 전략 실행이 완료되어 스캔 사이클을 1 증가시킵니다. (이전: {trade_cycle_count})")
                trade_cycle_count += 1
                db_manager.set_system_state('scanner_trade_cycle_count', trade_cycle_count)
                logger.info(f"✅ 새로운 스캔 사이클: {trade_cycle_count}")

                # 회고 분석도 이 조건 안에서만 체크
                # ✨✨✨ 핵심 수정 부분 ✨✨✨
                # 회고 분석 시스템 호출 로직
                if hasattr(config, 'REFLECTION_INTERVAL_CYCLES') and trade_cycle_count > 0 and \
                        trade_cycle_count % config.REFLECTION_INTERVAL_CYCLES == 0:

                    logger.info("🧠 회고 분석 시스템을 시작합니다...")
                    if hasattr(ai_analyzer, 'perform_retrospective_analysis'):
                        try:
                            # 1. 스캔된 유망 코인이 있을 경우에만 회고 분석을 실행합니다.
                            if target_tickers:
                                # 2. 스캔된 코인 목록의 첫 번째 코인을 대표로 사용합니다.
                                representative_ticker = target_tickers[0]

                                analysis_pm = portfolio.PortfolioManager(
                                    mode=config.RUN_MODE,
                                    ticker=representative_ticker,
                                    upbit_api_client=upbit_client_instance
                                )

                                # 3. 필요한 인자를 모두 전달하여 함수를 호출합니다.
                                ai_analyzer.perform_retrospective_analysis(openai_client_instance, analysis_pm)
                            else:
                                logger.warning("회고 분석을 위한 대표 티커가 없어 건너뜁니다.")

                        except Exception as e:
                            logger.error(f"회고 분석 중 오류 발생: {e}", exc_info=True)
                    else:
                        logger.warning("회고 분석 함수(perform_retrospective_analysis)를 찾을 수 없습니다.")
            logger.info(f"--- 시스템 주기 확인 종료, {config.FETCH_INTERVAL_SECONDS}초 대기 ---")
            try:
                # Healthchecks.io로 GET 요청을 보내 "나 살아있어!"라고 알립니다.
                requests.get(HEALTHCHECK_URL, timeout=10)
                logger.info("✅ Healthcheck Ping 신호 전송 성공.")
            except requests.RequestException as e:
                logger.warning(f"⚠️ Healthcheck Ping 신호 전송 실패: {e}")

            time.sleep(config.FETCH_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            logger.info("사용자에 의해 프로그램이 중단되었습니다.")
            break
        except Exception as e:
            logger.error(f"매매 실행 중 예외 발생: {e}", exc_info=True)
            time.sleep(config.FETCH_INTERVAL_SECONDS)