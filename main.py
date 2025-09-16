# main.py
# 🤖 자동매매 시스템의 메인 실행 파일입니다.
# 이 파일을 실행하여 데이터 수집, 백테스팅, 실시간 매매를 시작합니다.

import argparse
import logging
import importlib  # <-- [수정] 동적 모듈 로딩을 위한 라이브러리 임포트

# --- 필요한 모듈만 임포트 ---
from logging_setup import setup_logger
from data import data_manager
from backtester import backtest_engine
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

    # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
    # [핵심 수정 1] --config 옵션 추가
    # 사용자가 터미널에서 어떤 설정 파일을 쓸지 지정할 수 있게 합니다.
    # 기본값은 'config'이므로, 옵션을 주지 않으면 기존처럼 config.py를 사용합니다.
    parser.add_argument('--config', type=str, default='config',
                        help="사용할 설정 파일 이름 (예: 'config_real', 'config_sim')")
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

    parser.add_argument('--start_date', type=str, default=None, help="백테스트 시작 날짜 (YYYY-MM-DD 형식)")
    parser.add_argument('--end_date', type=str, default=None, help="백테스트 종료 날짜 (YYYY-MM-DD 형식)")
    args = parser.parse_args()

    # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
    # [핵심 수정 2] --config 옵션으로 지정된 설정 파일을 동적으로 불러오기
    try:
        # 문자열로 된 파일 이름(예: 'config_real')을 실제 파이썬 모듈로 불러옵니다.
        config_module = importlib.import_module(args.config)
        logger.info(f"✅ '{args.config}.py' 설정 파일을 성공적으로 불러왔습니다.")
    except ImportError:
        logger.error(f"❌ 지정된 설정 파일 '{args.config}.py'을(를) 찾을 수 없습니다. 프로그램을 종료합니다.")
        return
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

    logger.info(f"'{args.mode}' 모드를 시작합니다.")

    # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
    # [핵심 수정 3] 각 실행 함수에 불러온 'config_module'을 인자로 전달
    # 이제 각 모듈은 'import config' 대신 전달받은 설정을 사용하게 됩니다.
    if args.mode == 'trade':
        # 이 부분이 작동하려면 run_scanner_trader.py 파일의 run() 함수도 수정이 필요합니다. (아래 2단계 참고)
        run_scanner_trader.run(config_module)

    elif args.mode == 'collect':
        # 이 부분이 작동하려면 data_manager.py 파일의 run_all_collectors() 함수도 수정이 필요합니다.
        data_manager.run_all_collectors(config_module)

    elif args.mode == 'backtest':
        # 이 부분이 작동하려면 backtest_engine.py 파일의 run() 함수도 수정이 필요합니다.
        backtest_engine.run(config_module, start_date=args.start_date, end_date=args.end_date)
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

    logger.info(f"'{args.mode}' 모드를 종료합니다.")


if __name__ == "__main__":
    main()