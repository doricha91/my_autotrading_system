# main.py
# 🤖 자동매매 시스템의 메인 실행 파일입니다.
# 이 파일을 실행하여 데이터 수집, 백테스팅, 실시간 매매를 시작합니다.

import argparse
import logging

# --- 필요한 모듈만 임포트 ---
from logging_setup import setup_logger
from data import data_manager
from backtester import backtest_engine
# 방금 만든 실시간 매매 실행 파일을 임포트합니다.
import run_scanner_trader

def main():
    """
    프로그램의 메인 진입점. 커맨드 라인 인자를 파싱하여 적절한 모드를 실행합니다.
    """
    setup_logger()
    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(description="AI 기반 암호화폐 자동매매 시스템")
    parser.add_argument('mode', choices=['trade', 'collect', 'backtest'],
                        help="실행 모드를 선택하세요: 'trade', 'collect', 'backtest'")
    parser.add_argument('--start_date', type=str, default=None, help="백테스트 시작 날짜 (YYYY-MM-DD 형식)")
    parser.add_argument('--end_date', type=str, default=None, help="백테스트 종료 날짜 (YYYY-MM-DD 형식)")
    args = parser.parse_args()

    logger.info(f"'{args.mode}' 모드를 시작합니다.")

    if args.mode == 'trade':
        # 실시간 매매 로직을 담고 있는 run_scanner_trader 파일의 run 함수를 호출합니다.
        run_scanner_trader.run()

    elif args.mode == 'collect':
        # 데이터 수집 로직을 호출합니다.
        data_manager.run_all_collectors()

    elif args.mode == 'backtest':
        # 백테스트 엔진을 호출합니다.
        backtest_engine.run(start_date=args.start_date, end_date=args.end_date)

    logger.info(f"'{args.mode}' 모드를 종료합니다.")


if __name__ == "__main__":
    main()