# data/collectors/ohlcv_collector.py
# 🚚 업비트에서 OHLCV(캔들) 데이터를 수집하는 모듈입니다.
# (수정: 중단 시점까지의 데이터 저장을 위해 로직 변경)

import pyupbit
import pandas as pd
import time
import logging
import sqlite3

logger = logging.getLogger()


def update_ohlcv_db(con: sqlite3.Connection, ticker: str, interval: str):
    """
    OHLCV 데이터를 SQLite DB에 증분 또는 전체 업데이트합니다.
    - 전체 수집 시, 200개씩 가져올 때마다 즉시 DB에 저장하여 중단 시에도 데이터를 보존합니다.
    """
    table_name = f"{ticker.replace('-', '_')}_{interval}"
    last_date = None

    try:
        # 1. DB에 저장된 마지막 데이터 시점 확인
        query = f'SELECT MAX("timestamp") FROM "{table_name}"'
        last_date_str = pd.read_sql_query(query, con).iloc[0, 0]
        if last_date_str:
            last_date = pd.to_datetime(last_date_str)
            logger.info(f"DB에 저장된 '{table_name}' 테이블의 마지막 데이터 시점: {last_date}")
        else:
            logger.info(f"'{table_name}' 테이블이 비어있습니다. 전체 데이터 수집을 시작합니다.")

    except (sqlite3.OperationalError, pd.io.sql.DatabaseError):
        logger.info(f"'{table_name}' 테이블이 DB에 존재하지 않습니다. 전체 데이터 수집을 시작합니다.")

    try:
        if last_date is None:
            # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
            # 2a. 테이블이 없을 때 전체 데이터 수집 (즉시 저장 방식으로 변경)
            logger.info(f"'{ticker}'의 전체 {interval} 데이터를 수집하여 '{table_name}'에 저장합니다...")

            # 가장 처음 200개 데이터 가져오기
            df = pyupbit.get_ohlcv(ticker, interval=interval, count=200)
            if df is None or df.empty:
                logger.warning(f"'{ticker}'의 초기 데이터 수집에 실패했습니다.")
                return

            df.index.name = 'timestamp'
            # 첫 데이터 DB에 저장 (테이블 생성)
            df.to_sql(table_name, con, if_exists='replace', index=True)
            logger.info(f"✅ '{table_name}' 테이블 생성 및 초기 데이터 {len(df)}건 저장 완료.")

            oldest_date = df.index[0]
            previous_oldest_date = None

            while True:
                time.sleep(0.4)
                df_more = pyupbit.get_ohlcv(ticker, interval=interval, to=oldest_date, count=200)

                if df_more is None or df_more.empty:
                    logger.info("API로부터 더 이상 데이터를 반환받지 못했습니다. 수집을 완료합니다.")
                    break

                if previous_oldest_date == df_more.index[0]:
                    logger.info("데이터의 가장 시작점에 도달하여 수집을 중단합니다.")
                    break

                df_more = df_more.iloc[:-1]
                if df_more.empty:
                    break

                # 가져온 데이터를 즉시 DB에 추가 (append)
                df_more.index.name = 'timestamp'
                df_more.to_sql(table_name, con, if_exists='append', index=True)

                previous_oldest_date = oldest_date
                oldest_date = df_more.index[0]

                logger.info(f"'{table_name}'에 {len(df_more)}건 추가 저장. ({oldest_date} 이전 데이터 수집 중...)")

            logger.info(f"'{ticker}' 전체 데이터 수집 및 저장 완료.")
            # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

        else:
            # 2b. 테이블이 있으면 증분 업데이트 (기존 로직과 거의 동일)
            logger.info(f"'{table_name}' 테이블에 대한 증분 업데이트를 시도합니다...")
            time.sleep(0.4)
            df_new = pyupbit.get_ohlcv(ticker, interval=interval, count=200)

            if df_new is not None and not df_new.empty:
                df_new.index.name = 'timestamp'
                if df_new.index.tz is not None:
                    df_new.index = df_new.index.tz_localize(None)

                df_to_append = df_new[df_new.index > last_date]

                if not df_to_append.empty:
                    df_to_append.to_sql(table_name, con, if_exists='append', index=True)
                    logger.info(f"✅ '{table_name}' 테이블에 새로운 데이터 {len(df_to_append)}건을 추가했습니다.")
                else:
                    logger.info(f"'{table_name}' 테이블에 대한 새로운 데이터가 없습니다.")
            else:
                logger.warning(f"'{ticker}'에 대한 최신 데이터를 가져오지 못했습니다.")

    except KeyboardInterrupt:
        # 사용자가 Ctrl+C로 중단한 경우
        logger.warning(f"데이터 수집 중단됨. 현재까지 수집된 데이터는 '{table_name}'에 저장되었습니다.")
        # KeyboardInterrupt를 다시 발생시켜 상위 프로그램이 인지하도록 함
        raise
    except Exception as e:
        logger.error(f"'{ticker}' OHLCV DB 업데이트 중 심각한 오류 발생: {e}", exc_info=True)