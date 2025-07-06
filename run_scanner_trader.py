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


def _execute_trade_cycle_for_ticker(ticker, upbit_client, openai_client):
    """
    단일 티커에 대한 한 번의 거래 사이클을 실행합니다.
    """
    logger = logging.getLogger()
    logger.info(f"\n======= 티커 [{ticker}] 거래 로직 시작 =======")

    portfolio_manager = portfolio.PortfolioManager(
        mode=config.RUN_MODE,
        upbit_api_client=upbit_client,
        initial_capital=config.INITIAL_CAPITAL_PER_TICKER,
        ticker=ticker
    )

    # 2. 데이터 준비
    df_raw = data_manager.load_prepared_data(ticker, config.TRADE_INTERVAL, for_bot=True)
    if df_raw.empty:
        logger.warning(f"[{ticker}] 데이터 준비 실패. 다음 코인으로 넘어갑니다.")
        return

    # 3. 기술적 지표 추가 및 시장 국면 정의
    all_possible_params = []
    all_possible_params.extend([s.get('params', {}) for s in config.ENSEMBLE_CONFIG['strategies']])
    all_possible_params.extend([s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()])
    df_final = indicators.add_technical_indicators(df_raw, all_possible_params)
    df_final = indicators.define_market_regime(df_final)

    # 4. 현재 포지션 확인
    current_position = portfolio_manager.get_current_position()

    # 5. 선택된 모델에 따라 신호 생성
    final_signal_str = 'hold'
    signal_score = 0.0

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

    # 6. AI 분석 및 최종 결정
    ai_decision = ai_analyzer.get_ai_trading_decision(
        ticker, df_final.tail(30), final_signal_str, signal_score
    )
    final_decision, ratio, reason = trade_executor.determine_final_action(
        final_signal_str, ai_decision, current_position, df_final.iloc[-1], config.ENSEMBLE_CONFIG
    )

    # --- ✨✨✨ 핵심 수정 부분 (run_scanner_trader.py) ✨✨✨ ---
    # 7. 주문 실행
    # [수정] execute_trade 함수에 'ticker' 인자를 전달합니다.
    trade_executor.execute_trade(
        decision=final_decision,
        ratio=ratio,
        reason=reason,
        ticker=ticker, # <-- 인자 추가
        portfolio_manager=portfolio_manager,
        upbit_api_client=upbit_client
    )
    # --- ✨✨✨ 수정 끝 ✨✨✨ ---

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

    trade_cycle_count = 0
    while True:
        try:
            logger.info(f"\n--- 전체 스캔 사이클 {trade_cycle_count + 1} 시작 ---")
            logger.info("📈 유망 코인 스캔을 시작합니다...")
            target_tickers = scanner_instance.scan_tickers()

            if not target_tickers:
                logger.info("거래 대상 코인을 찾지 못했습니다. 다음 사이클까지 대기합니다.")
            else:
                logger.info(f"🎯 스캔 완료! 거래 대상: {target_tickers}")
                for ticker in target_tickers:
                    _execute_trade_cycle_for_ticker(ticker, upbit_client_instance, openai_client_instance)

            trade_cycle_count += 1
            logger.info(f"--- 전체 스캔 사이클 종료, {config.FETCH_INTERVAL_SECONDS}초 대기 ---")
            time.sleep(config.FETCH_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            logger.info("사용자에 의해 프로그램이 중단되었습니다.")
            break
        except Exception as e:
            logger.error(f"매매 실행 중 예외 발생: {e}", exc_info=True)
            time.sleep(config.FETCH_INTERVAL_SECONDS)