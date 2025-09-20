# create_tables.py
import sqlite3
import config

# --- 각 테이블 생성을 위한 SQL 구문 정의 ---

# 1. 모든 판단을 기록하는 테이블
CREATE_DECISION_LOG_SQL = """
CREATE TABLE IF NOT EXISTS decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    ticker TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    price_at_decision REAL NOT NULL
);
"""

# 2. AI 회고 분석 결과를 저장하는 테이블
CREATE_RETROSPECTION_LOG_SQL = """
CREATE TABLE IF NOT EXISTS retrospection_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    cycle_count INTEGER NOT NULL,
    evaluated_decisions_json TEXT,
    ai_reflection_text TEXT
);
"""

# 3. '모의투자' 거래만 기록하는 테이블
CREATE_PAPER_TRADE_LOG_SQL = """
CREATE TABLE IF NOT EXISTS paper_trade_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    ticker TEXT,
    action TEXT,
    price REAL,
    amount REAL,
    krw_value REAL,
    fee REAL,
    profit REAL,
    context TEXT
);
"""

# ✨ 4. '실제투자' 거래만 기록하는 테이블 (추가)
CREATE_REAL_TRADE_LOG_SQL = """
CREATE TABLE IF NOT EXISTS real_trade_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    action TEXT,
    ticker TEXT,
    upbit_uuid TEXT UNIQUE,
    price REAL,
    amount REAL,
    krw_value REAL,
    profit REAL,
    reason TEXT,
    context TEXT,
    upbit_response TEXT
);
"""

# 5. 각 코인의 모의투자 포트폴리오 상태를 저장하는 테이블
CREATE_PAPER_PORTFOLIO_STATE_SQL = """
CREATE TABLE IF NOT EXISTS paper_portfolio_state (
    id INTEGER PRIMARY KEY,
    ticker TEXT UNIQUE,
    krw_balance REAL,
    asset_balance REAL,
    avg_buy_price REAL,
    initial_capital REAL,
    fee_rate REAL,
    roi_percent REAL,
    highest_price_since_buy REAL,
    last_updated TEXT,
    trade_cycle_count INTEGER DEFAULT 0
);
"""
# ✨ [신규 추가] 6. 실제 투자 포트폴리오의 '상태'를 저장하는 테이블
CREATE_REAL_PORTFOLIO_STATE_SQL = """
CREATE TABLE IF NOT EXISTS real_portfolio_state (
    ticker TEXT PRIMARY KEY,
    highest_price_since_buy REAL,
    last_updated TEXT
);
"""

# 6. 시스템의 전체 상태 (예: 스캐너 사이클)를 저장하는 테이블
CREATE_SYSTEM_STATE_SQL = """
CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def create_db_tables():
    """
    autotrading_log.db에 필요한 모든 테이블들을 생성합니다.
    """
    try:
        with sqlite3.connect(config.LOG_DB_PATH) as conn:
            cursor = conn.cursor()

            print("▶️ 테이블 생성을 시작합니다...")

            cursor.execute(CREATE_DECISION_LOG_SQL)
            print("✅ 'decision_log' 테이블이 준비되었습니다.")

            cursor.execute(CREATE_RETROSPECTION_LOG_SQL)
            print("✅ 'retrospection_log' 테이블이 준비되었습니다.")

            cursor.execute(CREATE_PAPER_TRADE_LOG_SQL)
            print("✅ 'paper_trade_log' 테이블이 준비되었습니다.")

            # ✨ 'real_trade_log' 생성 로직 추가
            cursor.execute(CREATE_REAL_TRADE_LOG_SQL)
            print("✅ 'real_trade_log' 테이블이 준비되었습니다.")

            cursor.execute(CREATE_PAPER_PORTFOLIO_STATE_SQL)
            print("✅ 'paper_portfolio_state' 테이블이 준비되었습니다.")

            # ✨ [신규 추가] real_portfolio_state 테이블 생성 로직
            cursor.execute(CREATE_REAL_PORTFOLIO_STATE_SQL)
            print("✅ 'real_portfolio_state' 테이블이 준비되었습니다.")

            cursor.execute(CREATE_SYSTEM_STATE_SQL)
            print("✅ 'system_state' 테이블이 준비되었습니다.")

            conn.commit()
            print("\n🎉 모든 테이블이 성공적으로 준비되었습니다.")

    except Exception as e:
        print(f"❌ 테이블 생성 중 오류가 발생했습니다: {e}")


if __name__ == '__main__':
    create_db_tables()