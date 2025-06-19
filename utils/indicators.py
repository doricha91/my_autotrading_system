# utils/indicators.py
# 🛠️ 기술적 지표 계산을 전담하는 유틸리티 파일입니다.
# pandas-ta 라이브러리를 사용해 다양한 보조지표를 계산합니다.

import pandas as pd
import pandas_ta as ta
import logging

logger = logging.getLogger()


def add_technical_indicators(df: pd.DataFrame, strategies: list) -> pd.DataFrame:
    """
    주어진 데이터프레임에 전략 실행에 필요한 모든 기술적 보조지표를 동적으로 계산하여 추가합니다.

    Args:
        df (pd.DataFrame): 'open', 'high', 'low', 'close', 'volume' 컬럼을 포함하는 OHLCV 데이터
        strategies (list): 실행할 전략 파라미터 딕셔너리의 리스트.
                           이 리스트를 분석하여 필요한 지표만 계산합니다.

    Returns:
        pd.DataFrame: 기술적 지표가 추가된 데이터프레임
    """
    logger.info("기술적 지표 동적 계산을 시작합니다...")
    if df is None or df.empty:
        logger.warning("입력 데이터프레임이 비어있어 지표 계산을 건너뜁니다.")
        return df

    df_copy = df.copy()

    # 1. 실행할 전략들에서 필요한 모든 기간(period) 값을 수집합니다.
    sma_periods, high_low_periods, rsi_periods = set(), set(), set()
    for params in strategies:
        for key, value in params.items():
            if not value or not isinstance(value, (int, float)):
                continue

            # 정수형 값만 기간으로 간주합니다.
            value = int(value)

            if 'sma_period' in key:
                sma_periods.add(value)
            elif any(p in key for p in ['entry_period', 'exit_period', 'breakout_window']):
                high_low_periods.add(value)
            elif 'rsi_period' in key:
                rsi_periods.add(value)

    # 2. 수집된 기간 값으로 지표를 계산합니다.
    # 중복 계산을 피하고 필요한 지표만 효율적으로 계산할 수 있습니다.
    logger.info(f"계산 필요 SMA 기간: {sorted(list(sma_periods))}")
    for period in sorted(list(sma_periods)):
        df_copy.ta.sma(length=period, append=True)

    logger.info(f"계산 필요 High/Low 기간: {sorted(list(high_low_periods))}")
    for period in sorted(list(high_low_periods)):
        df_copy[f'high_{period}d'] = df_copy['high'].rolling(window=period).max()
        df_copy[f'low_{period}d'] = df_copy['low'].rolling(window=period).min()

    logger.info(f"계산 필요 RSI 기간: {sorted(list(rsi_periods))}")
    for period in sorted(list(rsi_periods)):
        df_copy.ta.rsi(length=period, append=True)

    # 3. 모든 전략에서 공통적으로 사용할 수 있는 기본 지표들을 계산합니다.
    logger.info("공통 기본 지표(RSI 14, BBands, ATR, OBV 등)를 계산합니다.")
    df_copy.ta.rsi(length=14, append=True)
    df_copy.ta.bbands(length=20, std=2, append=True)
    df_copy.ta.atr(length=14, append=True, col_names=('ATRr_14',))
    df_copy.ta.obv(append=True)
    df_copy['range'] = df_copy['high'].shift(1) - df_copy['low'].shift(1)

    # 4. 거시 경제 데이터가 있다면, 관련 지표도 추가할 수 있습니다. (예: 이동평균선)
    if 'nasdaq_close' in df_copy.columns:
        df_copy['nasdaq_sma_200'] = df_copy['nasdaq_close'].rolling(window=200).mean()

    logger.info("✅ 모든 기술적 지표 계산이 완료되었습니다.")
    return df_copy