# data/data_manager.py
# 🗄️ 데이터베이스 연결, 데이터 수집 실행, 데이터 로딩을 총괄하는 모듈입니다.
# 다른 모듈들은 이 파일을 통해 데이터에 접근합니다.

import sqlite3
import pandas as pd
import numpy as np # numpy import 추가
import logging
from datetime import datetime
from utils import indicators # indicators 모듈 import 추가
# 같은 data 폴더 내의 collectors 패키지에서 각 모듈을 가져옵니다.
from .collectors import ohlcv_collector, fng_collector, macro_collector, market_index_collector

logger = logging.getLogger()


def run_all_collectors(config):
    """
    모든 데이터 수집기를 순차적으로 실행하여 DB를 최신 상태로 업데이트합니다.
    이 함수는 main.py에서 'collect' 모드로 실행 시 호출됩니다.
    """
    logger.info("🚀 모든 데이터 수집 및 전처리 작업을 시작합니다...")

    # 1. OHLCV 데이터 수집 및 시장 지수 생성
    # 두 작업 모두 동일한 DB 파일을 사용하므로 하나의 connection으로 처리합니다.
    try:
        with sqlite3.connect(config.OHLCV_DB_PATH) as con:
            # 1-1. OHLCV 데이터 업데이트
            for interval in config.OHLCV_INTERVALS_TO_COLLECT:
                logger.info(f"--- {interval} 간격 OHLCV 데이터 처리 시작 ---")
                for ticker in config.TICKERS_TO_COLLECT_OHLCV:
                    ohlcv_collector.update_ohlcv_db(con, ticker, interval)
            logger.info("✅ OHLCV 데이터 수집 완료.")

            # 1-2. 시장 지수 생성/업데이트
            logger.info("--- 시장 지수 데이터 생성 시작 ---")
            market_index_series = market_index_collector.calculate_market_index(
                con=con,
                tickers=config.BLUE_CHIP_TICKERS_FOR_INDEX,
                interval="day",  # 시장 지수는 보통 일봉 기준으로 생성
                start_date="2017-09-01",  # 데이터가 존재하는 가장 이른 시점
                end_date=datetime.today().strftime('%Y-%m-%d')
            )
            market_index_collector.save_to_sqlite(market_index_series, con, config.MARKET_INDEX_TABLE)

    except Exception as e:
        logger.error(f"❌ OHLCV 또는 시장 지수 처리 중 오류 발생: {e}", exc_info=True)

    # 2. 공포탐욕지수 데이터 수집
    try:
        with sqlite3.connect(config.FNG_DB_PATH) as con:
            fng_df = fng_collector.fetch_all_fng_data()
            if not fng_df.empty:
                fng_collector.save_to_sqlite(fng_df, con, config.FNG_TABLE)
    except Exception as e:
        logger.error(f"❌ 공포탐욕지수 데이터 수집 중 오류 발생: {e}", exc_info=True)

    # 3. 거시경제지표 데이터 수집
    try:
        with sqlite3.connect(config.MACRO_DB_PATH) as con:
            start_date = "2017-01-01"
            end_date = datetime.today().strftime('%Y-%m-%d')
            macro_df = macro_collector.fetch_macro_data(start_date, end_date)
            if not macro_df.empty:
                macro_collector.save_to_sqlite(macro_df, con, config.MACRO_TABLE)
    except Exception as e:
        logger.error(f"❌ 거시경제지표 데이터 수집 중 오류 발생: {e}", exc_info=True)

    logger.info("🎉 모든 데이터 준비 작업이 완료되었습니다.")


def load_prepared_data(config, ticker: str, interval: str, for_bot: bool = False) -> pd.DataFrame:
    """
    자동매매 봇 또는 백테스터를 위해 필요한 모든 데이터를 로드하고 병합합니다.
    (기존 autotrading.py와 advanced_backtest.py의 load_and_prepare_data 함수를 통합)

    Args:
        ticker (str): 데이터를 로드할 메인 티커 (예: "KRW-BTC")
        interval (str): 시간 간격 (예: "day", "minute60")
        for_bot (bool): 봇 실행용인지 여부. True이면 최근 350개 데이터만 로드.

    Returns:
        pd.DataFrame: 모든 데이터가 병합되고 전처리된 최종 데이터프레임
    """
    logger.info(f"데이터 로딩 및 병합 시작 (Ticker: {ticker}, Interval: {interval})")
    ohlcv_table = f"{ticker.replace('-', '_')}_{interval}"

    try:
        # 1. 각 DB에서 데이터 로드
        with sqlite3.connect(config.OHLCV_DB_PATH) as con:
            # 봇 실행 시에는 모든 데이터를 불러올 필요 없이 최근 데이터만 사용
            query = f'SELECT * FROM "{ohlcv_table}"'
            if for_bot:
                query += " ORDER BY timestamp DESC LIMIT 2000"
            df_ohlcv = pd.read_sql_query(query, con, index_col='timestamp', parse_dates=['timestamp'])

            # 봇 실행 시에는 시간 역순으로 가져왔으므로 다시 정순으로 정렬
            if for_bot:
                df_ohlcv = df_ohlcv.sort_index()

            df_market_index = pd.read_sql_query(f'SELECT * FROM "{config.MARKET_INDEX_TABLE}"', con,
                                                index_col='timestamp', parse_dates=['timestamp'])

        with sqlite3.connect(config.FNG_DB_PATH) as con:
            df_fng = pd.read_sql_query(f'SELECT * FROM "{config.FNG_TABLE}"', con, index_col='timestamp',
                                       parse_dates=['timestamp'])

        with sqlite3.connect(config.MACRO_DB_PATH) as con:
            df_macro = pd.read_sql_query(f'SELECT * FROM "{config.MACRO_TABLE}"', con, index_col='index',
                                         parse_dates=['index'])
            df_macro.index.name = 'timestamp'  # 인덱스 이름 통일

        logger.info(
            f" -> 각 DB에서 데이터 로드 완료: OHLCV({len(df_ohlcv)}), 시장지수({len(df_market_index)}), F&G({len(df_fng)}), 거시경제({len(df_macro)})")

        # 2. 시간대 정보 통일 (데이터 병합 전 필수)
        for df in [df_ohlcv, df_fng, df_market_index, df_macro]:
            if not df.empty and df.index.tz:
                df.index = df.index.tz_localize(None)

        # 3. 데이터 병합 (OHLCV를 기준으로)
        df_merged = df_ohlcv
        if not df_market_index.empty:
            df_merged = df_merged.join(df_market_index[['market_index_value']], how='left')
        if not df_fng.empty:
            df_merged = df_merged.join(df_fng[['fng_value']], how='left')
        if not df_macro.empty:
            df_merged = df_merged.join(df_macro, how='left')

        # 4. 데이터 후처리
        # ffill(): 누락된 값(NaN)을 바로 이전 값으로 채웁니다. (주말 등 데이터가 없는 날 처리)
        df_merged.ffill(inplace=True)
        # 필수 컬럼에 데이터가 없는 행은 제거합니다.
        df_merged.dropna(subset=['close'], inplace=True)

        logger.info(f"✅ 데이터 병합 및 전처리 완료. 최종 데이터: {len(df_merged)} 행")
        return df_merged

    except Exception as e:
        logger.error(f"데이터 로드 또는 병합 중 오류 발생: {e}", exc_info=True)
        return pd.DataFrame()

def load_all_ohlcv_data(tickers: list, interval: str) -> dict:
    """
    지정된 모든 티커에 대한 OHLCV 데이터를 딕셔너리 형태로 불러옵니다.
    """
    all_data = {}
    for ticker in tickers:
        try:
            # 기존 load_prepared_data 함수를 재활용합니다.
            df = load_prepared_data(ticker, interval)
            if not df.empty:
                all_data[ticker] = df
        except Exception as e:
            print(f"{ticker} 데이터 로드 중 오류 발생: {e}")  # 로깅으로 대체 권장
    return all_data