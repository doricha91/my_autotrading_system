# data/collectors/fng_collector.py
# 🚚 공포탐욕(Fear & Greed) 지수 데이터를 수집하는 모듈입니다.

import requests
import pandas as pd
import logging
import sqlite3

logger = logging.getLogger()

def fetch_all_fng_data() -> pd.DataFrame:
    """
    alternative.me API를 사용하여 모든 기간의 공포-탐욕 지수를 가져옵니다.
    (기존 collect_fng.py의 fetch_all_fng_data 함수와 동일)

    Returns:
        pd.DataFrame: 전처리된 공포-탐욕 지수 데이터프레임. 실패 시 빈 프레임 반환.
    """
    logger.info("alternative.me API에서 전체 공포-탐욕 지수 데이터 수집을 시작합니다...")
    url = "https://api.alternative.me/fng/?limit=0"

    try:
        response = requests.get(url)
        response.raise_for_status()  # HTTP 오류 발생 시 예외 발생

        json_data = response.json()
        data_list = json_data.get('data', [])

        if not data_list:
            logger.warning("API에서 F&G 데이터를 가져오지 못했습니다.")
            return pd.DataFrame()

        df = pd.DataFrame(data_list)
        logger.info(f"총 {len(df)}일치 F&G 데이터를 수집했습니다.")

        # --- 데이터 전처리 ---
        df['value'] = pd.to_numeric(df['value'])
        # 날짜 기준으로 데이터를 병합하기 위해 시간 정보는 제거 (normalize)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s').dt.normalize()
        df.rename(columns={'value': 'fng_value', 'value_classification': 'fng_classification'}, inplace=True)
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)

        logger.info("F&G 데이터 전처리를 완료했습니다.")
        return df

    except requests.exceptions.RequestException as e:
        logger.error(f"F&G API 요청 중 오류 발생: {e}")
        return pd.DataFrame()

def save_to_sqlite(df: pd.DataFrame, con: sqlite3.Connection, table_name: str):
    """
    DataFrame을 주어진 SQLite 연결(connection)을 통해 저장합니다.
    (기존 함수에서 db_path 대신 connection 객체를 받도록 수정)

    Args:
        df (pd.DataFrame): 저장할 데이터프레임
        con (sqlite3.Connection): SQLite DB 연결 객체
        table_name (str): 저장할 테이블 이름
    """
    if df.empty:
        logger.warning("F&G 데이터가 없어 저장을 건너뜁니다.")
        return

    try:
        # if_exists='replace': 테이블이 이미 존재하면 기존 테이블을 삭제하고 새로 만듭니다.
        # 이 스크립트는 항상 전체 데이터를 가져오므로 'replace'가 적합합니다.
        df.to_sql(table_name, con, if_exists='replace', index=True)
        logger.info(f"✅ F&G 데이터가 '{table_name}' 테이블에 성공적으로 저장되었습니다.")
    except Exception as e:
        logger.error(f"F&G 데이터베이스 저장 중 오류 발생: {e}")