# utils/notifier.py
import requests
import os

def send_telegram_message(message: str):
    """텔레그램으로 메시지를 보냅니다."""
    # 환경 변수에서 토큰과 Chat ID를 읽어옵니다.
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        # logger가 없으므로 print를 사용
        print("텔레그램 토큰 또는 Chat ID가 설정되지 않았습니다.")
        return

    send_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    params = {'chat_id': TELEGRAM_CHAT_ID, 'text': message}

    try:
        requests.get(send_url, params=params, timeout=5)
    except Exception as e:
        print(f"텔레그램 메시지 발송 실패: {e}")