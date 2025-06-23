# main.py
# 🤖 자동매매 시스템의 메인 실행 파일입니다.
# 이 파일을 실행하여 데이터 수집, 백테스팅, 실제/모의 매매를 시작합니다.

import argparse
import time
import logging
import openai

import config
from logging_setup import setup_logger
from data import data_manager
from apis import upbit_api, ai_analyzer
from core import strategy, portfolio, trade_executor
from backtester import backtest_engine
from utils import indicators


def run_trading_bot():
    """자동매매 봇의 메인 루프를 실행합니다."""
    logger = logging.getLogger()
    logger.info(f"🚀 자동매매 봇을 시작합니다. (모드: {config.RUN_MODE.upper()})")
    logger.info(f"선택된 전략 모델: {config.ACTIVE_STRATEGY_MODEL.upper()}")
    # 1. API 및 포트폴리오 관리자 초기화
    upbit_client = upbit_api.UpbitAPI(config.UPBIT_ACCESS_KEY, config.UPBIT_SECRET_KEY)
    portfolio_manager = portfolio.PortfolioManager(config.RUN_MODE, upbit_client, config.INITIAL_CAPITAL)

    # OpenAI 클라이언트 초기화 (회고 분석용)
    openai_client = openai.OpenAI(api_key=config.OPENAI_API_KEY) if config.OPENAI_API_KEY else None
    if not openai_client:
        logger.warning("OpenAI API 키가 없어 회고 분석 기능이 비활성화됩니다.")

    trade_cycle_count = portfolio_manager.state.get('trade_cycle_count', 0)

    while True:
        try:
            logger.info(f"\n--- 사이클 {trade_cycle_count + 1} 시작 ---")

            # 2. 데이터 준비
            df_raw = data_manager.load_prepared_data(config.TICKER_TO_TRADE, config.TRADE_INTERVAL, for_bot=True)
            if df_raw.empty:
                logger.warning("데이터 준비 실패. 다음 사이클까지 대기합니다.")
                time.sleep(config.FETCH_INTERVAL_SECONDS)
                continue

            # 3. 기술적 지표 추가
            all_possible_params = []
            all_possible_params.extend([s.get('params', {}) for s in config.ENSEMBLE_CONFIG['strategies']])
            all_possible_params.extend([s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()])
            df_final = indicators.add_technical_indicators(df_raw, all_possible_params)
            df_final = indicators.define_market_regime(df_final)  # 국면 전환 모델을 위해 국면 정의
            df_final['regime'] = df_final['regime_adx']  # 대표 국면 설정

            # 4. 현재 포지션 확인
            current_position = portfolio_manager.get_current_position()

            # 5. 선택된 모델에 따라 신호 생성
            final_signal_str = 'hold'
            final_score = 0.0

            if config.ACTIVE_STRATEGY_MODEL == 'ensemble':
                # 5-A. 앙상블 모델 실행
                final_signal_str, final_score = strategy.get_ensemble_strategy_signal(df_final, config.ENSEMBLE_CONFIG)

            elif config.ACTIVE_STRATEGY_MODEL == 'regime_switching':
                # 5-B. 국면 전환 모델 실행
                current_regime = df_final.iloc[-1].get('regime', 'sideways')
                logger.info(f"현재 시장 국면 감지: {current_regime.upper()}")
                strategy_config_for_regime = config.REGIME_STRATEGY_MAP.get(current_regime)

                if strategy_config_for_regime:
                    logger.info(f"'{strategy_config_for_regime['name']}' 전략을 실행합니다.")
                    df_with_signal = strategy.generate_signals(df_final, strategy_config_for_regime)
                    signal_val = df_with_signal.iloc[-1].get('signal', 0)
                    final_signal_str = 'buy' if signal_val > 0 else 'sell' if signal_val < 0 else 'hold'
                    final_score = abs(signal_val)  # 점수는 신호의 절대값으로 사용 (예시)
                else:
                    logger.warning(f"'{current_regime}' 국면에 대한 전략이 config.py에 정의되지 않았습니다. 관망합니다.")
                    final_signal_str = 'hold'

            # 6. AI 분석 및 최종 결정
            ai_decision = ai_analyzer.get_ai_trading_decision(
                config.TICKER_TO_TRADE, df_final.tail(30), final_signal_str, final_score
            )
            final_decision, ratio, reason = trade_executor.determine_final_action(
                final_signal_str,
                ai_decision,
                current_position,
                df_final.iloc[-1],
                config.ENSEMBLE_CONFIG  # 앙상블 설정 전체를 전달
            )
            # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

            # 7. 주문 실행
            trade_executor.execute_trade(
                decision=final_decision, ratio=ratio, reason=reason,
                portfolio_manager=portfolio_manager, upbit_api_client=upbit_client
            )

            # 7. 사이클 카운트 및 상태 저장
            trade_cycle_count += 1
            if config.RUN_MODE == 'simulation':
                portfolio_manager.state['trade_cycle_count'] = trade_cycle_count
                # 수익률 계산 및 저장은 portfolio_manager 내부에서 처리됨
                portfolio_manager.update_portfolio_on_trade(None)  # 수익률 계산 및 저장을 위해 호출 (trade_result가 None이어도 동작)

            # 8. 회고 분석 주기 확인 및 실행
            if openai_client and trade_cycle_count > 0 and trade_cycle_count % config.REFLECTION_INTERVAL_CYCLES == 0:
                logger.info(f"{config.REFLECTION_INTERVAL_CYCLES} 사이클마다 회고 분석을 수행합니다.")
                # 포트폴리오 매니저 객체를 전달하여 현재 ROI 등 상태를 조회할 수 있도록 함
                ai_analyzer.perform_retrospective_analysis(openai_client, portfolio_manager)

            logger.info(f"--- 사이클 종료, {config.FETCH_INTERVAL_SECONDS}초 대기 ---")
            time.sleep(config.FETCH_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            logger.info("사용자에 의해 프로그램이 중단되었습니다.")
            break
        except Exception as e:
            logger.error(f"메인 루프에서 예외 발생: {e}", exc_info=True)
            time.sleep(config.FETCH_INTERVAL_SECONDS)


if __name__ == "__main__":
    # 1. 로거 설정
    setup_logger()
    logger = logging.getLogger(__name__) # <--- 이 줄을 추가하여 logger를 정의합니다.

    # 2. 커맨드라인 인자 파서(Parser) 생성
    parser = argparse.ArgumentParser(description="AI 기반 암호화폐 자동매매 시스템")
    parser.add_argument('mode', choices=['trade', 'collect', 'backtest'],
                        help="실행 모드를 선택하세요: 'trade', 'collect', 'backtest'")
    parser.add_argument('--start_date', type=str, default=None,
                        help="백테스트 시작 날짜 (YYYY-MM-DD 형식)")
    parser.add_argument('--end_date', type=str, default=None,
                        help="백테스트 종료 날짜 (YYYY-MM-DD 형식)")

    args = parser.parse_args()

    # 3. 선택된 모드에 따라 해당 기능 실행
    if args.mode == 'trade':
        run_trading_bot()

    elif args.mode == 'collect':
        data_manager.run_all_collectors()

    elif args.mode == 'backtest':
        # 백테스트 모드 실행 시, backtest_engine의 run() 함수만 호출합니다.
        # start_date와 end_date 인자를 전달합니다.
        logger.info("백테스트 모드를 시작합니다.")
        backtest_engine.run(start_date=args.start_date, end_date=args.end_date)
        logger.info("백테스트 모드를 종료합니다.")