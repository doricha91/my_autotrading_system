# data/collectors/macro_collector.py
# 🚚 주요 거시 경제 지표를 수집하는 모듈입니다.

import pandas as pd
import yfinance as yf
import pandas_datareader.data as pdr
import sqlite3
import time
import logging

logger = logging.getLogger()


def fetch_macro_data(start_date: str, end_date: str) -> pd.DataFrame:
    """
    주요 거시 경제 지표를 수집하여 하나의 데이터프레임으로 병합 및 전처리합니다.
    (기존 fetch_and_save_macro_data 함수에서 데이터 '수집' 부분만 분리)

    Args:
        start_date (str): 데이터 수집 시작일 (YYYY-MM-DD)
        end_date (str): 데이터 수집 종료일 (YYYY-MM-DD)

    Returns:
        pd.DataFrame: 병합 및 전처리된 거시 경제 데이터프레임
    """
    logger.info("거시 경제 데이터 수집을 시작합니다...")

    try:
        # 1. yfinance를 사용하여 나스닥 및 달러 인덱스 데이터 수집
        logger.info("나스닥 지수(^IXIC)와 달러 인덱스(DX-Y.NYB) 데이터를 수집합니다...")
        tickers = ["^IXIC", "DX-Y.NYB"]
        yf_data = yf.download(tickers, start=start_date, end=end_date, progress=False)
        df_yf = yf_data['Close'].copy()
        df_yf.rename(columns={"^IXIC": "nasdaq_close", "DX-Y.NYB": "dxy_close"}, inplace=True)

        # 2. pandas_datareader를 사용하여 미국 기준금리 데이터 수집 (재시도 로직 포함)
        logger.info("미국 기준금리(DFF) 데이터를 FRED에서 수집합니다...")
        df_fred = None
        max_retries = 3
        for attempt in range(max_retries):
            try:
                df_fred = pdr.get_data_fred('DFF', start=start_date, end=end_date)
                logger.info("FRED 데이터 수집 성공!")
                break
            except Exception as e:
                logger.warning(f"시도 {attempt + 1}/{max_retries}: FRED 데이터 수집 실패. 5초 후 재시도합니다.")
                time.sleep(5)

        if df_fred is None:
            raise Exception("최대 재시도 횟수를 초과하여 FRED 데이터 수집에 최종 실패했습니다.")
        df_fred.rename(columns={"DFF": "us_interest_rate"}, inplace=True)

        # 3. 데이터 병합 및 후처리
        logger.info("수집된 거시 경제 데이터를 병합 및 전처리합니다...")
        df_macro = pd.concat([df_yf, df_fred], axis=1)
        df_macro.fillna(method='ffill', inplace=True)  # 누락된 값은 이전 값으로 채움
        df_macro.dropna(inplace=True)  # 그럼에도 NaN이 남아있으면 해당 행 제거

        logger.info(f"총 {len(df_macro)}일치의 거시 경제 데이터를 성공적으로 처리했습니다.")
        return df_macro

    except Exception as e:
        logger.error(f"거시 경제 데이터 수집 또는 처리 중 오류 발생: {e}")
        return pd.DataFrame()


def save_to_sqlite(df: pd.DataFrame, con: sqlite3.Connection, table_name: str):
    """DataFrame을 주어진 SQLite 연결을 통해 저장합니다."""
    if df.empty:
        logger.warning("거시 경제 데이터가 없어 저장을 건너뜁니다.")
        return

    try:
        df.to_sql(table_name, con, if_exists='replace', index=True)
        logger.info(f"✅ 거시 경제 데이터가 '{table_name}' 테이블에 성공적으로 저장되었습니다.")
    except Exception as e:
        logger.error(f"거시 경제 데이터베이스 저장 중 오류 발생: {e}")