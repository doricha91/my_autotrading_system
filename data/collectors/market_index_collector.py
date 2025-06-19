# data/collectors/market_index_collector.py
# 🚚 여러 티커의 가격을 종합하여 커스텀 시장 지수를 생성하는 모듈입니다.

import pandas as pd
import sqlite3
import logging

logger = logging.getLogger()


def calculate_market_index(con: sqlite3.Connection, tickers: list, interval: str,
                           start_date: str, end_date: str, initial_value: float = 1000.0) -> pd.Series:
    """
    여러 티커의 OHLCV 데이터를 사용하여 동일 가중 시장 지수를 계산합니다.
    (기존 create_market_index.py의 calculate_market_index 함수를 수정)

    Args:
        con (sqlite3.Connection): OHLCV 데이터가 있는 DB의 연결 객체
        tickers (list): 지수 계산에 포함할 티커 리스트
        interval (str): 시간 간격 ('day', 'minute60' 등)
        start_date (str): 데이터 조회 시작일
        end_date (str): 데이터 조회 종료일
        initial_value (float): 지수의 시작 값

    Returns:
        pd.Series: 계산된 시장 지수 시계열 데이터
    """
    logger.info(f"시장 지수 계산 시작 (대상: {len(tickers)}개 코인)...")
    all_close_prices = {}

    for ticker in tickers:
        table_name = f"{ticker.replace('-', '_')}_{interval}"
        try:
            # SQL 쿼리를 수정하여 날짜 범위를 지정
            query = f"SELECT timestamp, close FROM '{table_name}' WHERE timestamp >= '{start_date}' AND timestamp <= '{end_date}'"
            df_ticker = pd.read_sql_query(query, con, index_col='timestamp', parse_dates=['timestamp'])

            # 시간 정보 정규화
            if df_ticker.index.tz is not None:
                df_ticker.index = df_ticker.index.tz_localize(None)
            df_ticker.index = df_ticker.index.normalize()

            if not df_ticker.empty:
                all_close_prices[ticker] = df_ticker['close']
        except Exception as e:
            logger.warning(f"'{table_name}' 테이블 로드 중 오류: {e}")

    if not all_close_prices:
        logger.error("지수 계산을 위한 데이터를 로드하지 못했습니다.")
        return pd.Series(dtype=float)

    df_combined = pd.DataFrame(all_close_prices)
    df_combined.dropna(how='all', inplace=True)  # 모든 데이터가 NaN인 행 제거

    if df_combined.empty:
        logger.error("병합 후 유효한 종가 데이터가 없습니다.")
        return pd.Series(dtype=float)

    # 일일 수익률 계산 (NaN은 0으로 처리)
    daily_returns = df_combined.pct_change().fillna(0)

    # 동일 가중 평균 수익률 계산
    market_daily_returns = daily_returns.mean(axis=1)

    # 지수 계산
    market_index = pd.Series(index=market_daily_returns.index, dtype=float)
    market_index.iloc[0] = initial_value
    for i in range(1, len(market_index)):
        market_index.iloc[i] = market_index.iloc[i - 1] * (1 + market_daily_returns.iloc[i])

    logger.info(f"✅ 시장 지수 계산 완료 (총 {len(market_index)}일).")
    return market_index


def save_to_sqlite(market_index_series: pd.Series, con: sqlite3.Connection, table_name: str):
    """계산된 시장 지수를 SQLite DB에 저장합니다."""
    if market_index_series.empty:
        logger.warning("시장 지수 데이터가 없어 저장을 건너뜁니다.")
        return

    try:
        # 시리즈에 이름을 부여해야 to_sql에서 컬럼명으로 사용됨
        market_index_series.rename("market_index_value").to_sql(table_name, con, if_exists="replace", index=True)
        logger.info(f"✅ 시장 지수가 '{table_name}' 테이블에 성공적으로 저장되었습니다.")
    except Exception as e:
        logger.error(f"시장 지수 DB 저장 중 오류: {e}")