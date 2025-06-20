# backtester/results_handler.py

import sqlite3
import pandas as pd
import os
import logging

logger = logging.getLogger(__name__)

# 결과 DB 경로 설정
DB_DIR = "backtest_results"
DB_PATH = os.path.join(DB_DIR, "backtest_results.db")


def save_results(results_df: pd.DataFrame, table_name: str):
    """
    백테스트 결과 DataFrame을 SQLite DB의 지정된 테이블에 저장합니다.
    - DB 파일과 테이블은 없으면 자동으로 생성됩니다.
    - 기존에 테이블이 있으면 새로운 결과를 행으로 추가(append)합니다.
    """
    if results_df.empty:
        logger.warning(f"저장할 결과 데이터가 없어 {table_name} 저장을 건너뜁니다.")
        return

    try:
        # DB 디렉토리 생성
        os.makedirs(DB_DIR, exist_ok=True)

        # SQLite DB에 연결
        conn = sqlite3.connect(DB_PATH)

        # 파라미터 컬럼이 딕셔너리 형태일 경우 문자열로 변환
        if '파라미터' in results_df.columns:
            results_df['파라미터'] = results_df['파라미터'].astype(str)

        # DataFrame을 SQL 테이블에 저장
        results_df.to_sql(table_name, conn, if_exists='append', index=False)

        logger.info(f"✅ 백테스트 결과를 '{DB_PATH}'의 '{table_name}' 테이블에 성공적으로 저장했습니다.")

    except Exception as e:
        logger.error(f"DB에 결과를 저장하는 중 오류 발생: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()