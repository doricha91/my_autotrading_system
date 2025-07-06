# run_scanner_trader.py
# 🤖 스캐너 기반 실시간/모의 매매를 실행하는 파일입니다.

import time
import logging
import openai
import pyupbit

import config
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
    단일 티커에 대한 한 번의 거래 사이클을 실행합니다.
    자산 보유 여부에 따라 매수 로직 또는 청산 감시 로직을 실행합니다.
    """
    logger.info(f"\n======= 티커 [{ticker}] 거래 로직 시작 =======")

    portfolio_manager = portfolio.PortfolioManager(
        mode=config.RUN_MODE, upbit_api_client=upbit_client,
        initial_capital=config.INITIAL_CAPITAL_PER_TICKER, ticker=ticker
    )
    current_position = portfolio_manager.get_current_position()

    # --- 보유 자산이 있을 경우: '청산 감시' 로직 실행 ---
    if current_position.get('asset_balance', 0) > 0:
        # 청산 감시에 필요한 최신 데이터와 파라미터 준비
        df_raw = data_manager.load_prepared_data(ticker, config.TRADE_INTERVAL, for_bot=True)
        if df_raw.empty: return

        all_possible_params = [s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()]
        df_final = indicators.add_technical_indicators(df_raw, all_possible_params)
        exit_params = config.ENSEMBLE_CONFIG.get('common_exit_params', {})

        _handle_exit_logic(portfolio_manager, upbit_client, df_final, exit_params)

    # --- 보유 자산이 없을 경우: '매수' 로직 실행 ---
    else:
        # 1. 데이터 준비 및 분석 (기존과 동일, AI 분석 포함)
        df_raw = data_manager.load_prepared_data(ticker, config.TRADE_INTERVAL, for_bot=True)
        if df_raw.empty: return

        all_possible_params = [s.get('params', {}) for s in config.ENSEMBLE_CONFIG['strategies']]
        all_possible_params.extend([s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()])
        df_final = indicators.add_technical_indicators(df_raw, all_possible_params)
        df_final = indicators.define_market_regime(df_final)

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
        if final_decision == 'buy':
            trade_executor.execute_trade(
                decision=final_decision, ratio=ratio, reason=reason, ticker=ticker,
                portfolio_manager=portfolio_manager, upbit_api_client=upbit_client
            )


    # 8. 포트폴리오 상태 저장 및 로그
    if config.RUN_MODE == 'simulation':
        current_price = pyupbit.get_current_price(ticker)
        if current_price:
            portfolio_manager.update_and_save_state(current_price)


def run():
    """스캐너 기반 자동매매 봇의 메인 루프를 실행합니다."""
    logger = logging.getLogger()
    logger.info(f"🚀 스캐너 기반 자동매매 봇을 시작합니다.")

    upbit_client_instance = upbit_api.UpbitAPI(config.UPBIT_ACCESS_KEY, config.UPBIT_SECRET_KEY)
    openai_client_instance = openai.OpenAI(api_key=config.OPENAI_API_KEY) if config.OPENAI_API_KEY else None
    scanner_instance = scanner.Scanner(settings=config.SCANNER_SETTINGS)

    # ✨ 2. DB 매니저를 생성하고, DB에서 마지막 사이클 횟수를 불러옵니다.
    db_manager = portfolio.DatabaseManager(config.LOG_DB_PATH)
    trade_cycle_count = int(db_manager.get_system_state('scanner_trade_cycle_count', '0'))

    while True:
        try:
            logger.info(f"\n--- 전체 스캔 사이클 {trade_cycle_count + 1} 시작 ---")
            logger.info("📈 유망 코인 스캔을 시작합니다...")
            target_tickers = scanner_instance.scan_tickers()

            if not target_tickers:
                logger.info("거래 대상 코인을 찾지 못했습니다.")
            else:
                logger.info(f"🎯 스캔 완료! 거래 대상: {target_tickers}")
                for ticker in target_tickers:
                    try:
                        _execute_trade_cycle_for_ticker(ticker, upbit_client_instance, openai_client_instance)
                    except Exception as e:
                        logger.error(f"[{ticker}] 처리 중 오류 발생. 다음 티커로 계속합니다: {e}", exc_info=True)

            # ✨ 3. 사이클 카운트를 1 증가시키고, 그 결과를 즉시 DB에 저장합니다.
            trade_cycle_count += 1
            db_manager.set_system_state('scanner_trade_cycle_count', trade_cycle_count)

            # 회고 분석 시스템 호출 로직
            if hasattr(config, 'REFLECTION_INTERVAL_CYCLES') and trade_cycle_count > 0 and \
               trade_cycle_count % config.REFLECTION_INTERVAL_CYCLES == 0:
                logger.info("🧠 회고 분석 시스템을 시작합니다...")
                if hasattr(ai_analyzer, 'perform_retrospective_analysis'):
                    ai_analyzer.perform_retrospective_analysis(openai_client_instance)
                else:
                    logger.warning("회고 분석 함수(perform_retrospective_analysis)를 찾을 수 없습니다.")

            logger.info(f"--- 전체 스캔 사이클 종료, {config.FETCH_INTERVAL_SECONDS}초 대기 ---")
            time.sleep(config.FETCH_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            logger.info("사용자에 의해 프로그램이 중단되었습니다.")
            break
        except Exception as e:
            logger.error(f"매매 실행 중 예외 발생: {e}", exc_info=True)
            time.sleep(config.FETCH_INTERVAL_SECONDS)