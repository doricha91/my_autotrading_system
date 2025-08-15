# create_tables.py
import sqlite3
import config

# 생성할 테이블의 SQL 구문
# IF NOT EXISTS를 사용하여, 테이블이 이미 존재하면 오류 없이 넘어갑니다.
CREATE_RETROSPECTION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS retrospection_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    cycle_count INTEGER NOT NULL,
    evaluated_decisions_json TEXT,
    ai_reflection_text TEXT
);
"""


def create_db_tables():
    """
    autotrading_log.db에 필요한 테이블들을 생성합니다.
    """
    try:
        # config.py에 정의된 DB 경로를 사용
        with sqlite3.connect(config.LOG_DB_PATH) as conn:
            cursor = conn.cursor()

            print("▶️ 'retrospection_log' 테이블 생성을 시도합니다...")
            cursor.execute(CREATE_RETROSPECTION_TABLE_SQL)
            print("✅ 'retrospection_log' 테이블이 성공적으로 준비되었습니다.")

            conn.commit()

    except Exception as e:
        print(f"❌ 테이블 생성 중 오류가 발생했습니다: {e}")


if __name__ == '__main__':
    create_db_tables()