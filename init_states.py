import os
import sqlite3
from dotenv import load_dotenv
from apis.upbit_api import UpbitAPI
import pyupbit
import pandas as pd

print("✅ 기존 보유 자산의 상태 정보 생성을 시작합니다...")

try:
    # --- ✨ [핵심 수정] EC2 환경에 맞게 API 키 로드 ---
    # 1. systemd 서비스의 환경 변수 파일을 먼저 찾습니다.
    env_file_path = '/etc/default/autotrader.env'
    if os.path.exists(env_file_path):
        print(f"'{env_file_path}' 파일에서 API 키를 로드합니다.")
        load_dotenv(dotenv_path=env_file_path)
    else:
        # 2. 만약 위 파일이 없다면, 로컬 테스트를 위해 config_real.py를 사용합니다.
        print("EC2 환경 변수 파일을 찾을 수 없어 'config_real.py'에서 설정을 가져옵니다.")
        from config_real import LOG_DB_PATH

    # .env 또는 config_real에서 로드된 환경 변수를 가져옵니다.
    UPBIT_ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY")
    UPBIT_SECRET_KEY = os.getenv("UPBIT_SECRET_KEY")

    # config_real을 통해 LOG_DB_PATH를 가져오지 못한 경우를 대비
    if 'LOG_DB_PATH' not in locals():
        from config_real import LOG_DB_PATH

    # --- DB 및 API 클라이언트 초기화 ---
    db_path = LOG_DB_PATH
    upbit_client = UpbitAPI(UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY)

    if upbit_client.client is None:
        raise Exception("Upbit API 클라이언트 초기화에 실패했습니다. API 키가 유효한지 확인해주세요.")

    # --- 현재 보유 코인 목록 가져오기 ---
    all_balances = upbit_client.client.get_balances()
    held_tickers = {f"KRW-{b['currency']}" for b in all_balances if b['currency'] != 'KRW' and float(b['balance']) > 0}

    if not held_tickers:
        print("💡 현재 보유 중인 코인이 없습니다. 작업을 종료합니다.")
    else:
        print(f"현재 보유 코인: {list(held_tickers)}")

        # --- DB에 상태 정보 기록 ---
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()

            for ticker in held_tickers:
                # 현재가를 가져와서 '매수 후 최고가'의 초기값으로 사용
                current_price = pyupbit.get_current_price(ticker)
                if current_price is None:
                    print(f"⚠️ [{ticker}] 현재가 조회에 실패하여 건너뜁니다.")
                    continue

                # ON CONFLICT(ticker) DO NOTHING: 이미 데이터가 있으면 무시
                cursor.execute("""
                    INSERT INTO real_portfolio_state (ticker, highest_price_since_buy)
                    VALUES (?, ?)
                    ON CONFLICT(ticker) DO NOTHING
                """, (ticker, current_price))

                print(f"✔️ [{ticker}] 상태 정보를 DB에 추가/확인했습니다. (초기 최고가: {current_price:,.0f} 원)")

        print("\n🎉 모든 보유 자산에 대한 상태 정보 생성이 완료되었습니다.")

except ImportError:
    print("❌ 'config_real.py' 파일을 찾을 수 없습니다. 파일 이름을 확인해주세요.")
except Exception as e:
    print(f"❌ 작업 중 오류 발생: {e}")