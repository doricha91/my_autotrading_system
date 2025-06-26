# utils/indicators.py
# 🛠️ 기술적 지표 계산을 전담하는 유틸리티 파일입니다.
# pandas-ta 라이브러리를 사용해 다양한 보조지표를 계산합니다.

import pandas as pd
import pandas_ta as ta
import numpy as np
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
    atr_period = 14  # ATR 기간을 변수로 지정
    df_copy.ta.atr(length=atr_period, append=True)
    df_copy.ta.obv(append=True)
    # ADX 지표는 시장 국면 정의에 필요하므로 여기서 미리 계산해 줍니다.
    df_copy.ta.adx(append=True)
    df_copy['range'] = df_copy['high'].shift(1) - df_copy['low'].shift(1)

    # pandas_ta가 생성한 기본 컬럼 이름 (예: 'ATRr_14')
    original_atr_col_name = f'ATRr_{atr_period}'
    if original_atr_col_name in df_copy.columns:
        df_copy.rename(columns={original_atr_col_name: 'ATR'}, inplace=True)
        logger.info(f"'{original_atr_col_name}' 컬럼을 'ATR'로 변경했습니다.")

    # 4. 거시 경제 데이터가 있다면, 관련 지표도 추가할 수 있습니다. (예: 이동평균선)
    if 'nasdaq_close' in df_copy.columns:
        df_copy['nasdaq_sma_200'] = df_copy['nasdaq_close'].rolling(window=200).mean()

    logger.info("✅ 모든 기술적 지표 계산이 완료되었습니다.")
    return df_copy


def define_market_regime(df: pd.DataFrame, adx_threshold: int = 25, sma_period: int = 50) -> pd.DataFrame:
    """
    ADX와 이동평균선을 조합하여 시장 국면을 'bull', 'bear', 'sideways'로 정의합니다.

    Args:
        df (pd.DataFrame): 'ADX_14', 'DMP_14', 'DMN_14', 'SMA_50', 'close' 컬럼이 포함된 데이터프레임
        adx_threshold (int): 추세의 유무를 판단하는 ADX 임계값
        sma_period (int): 추세의 방향을 판단하는 이동평균선 기간

    Returns:
        pd.DataFrame: 'regime' 컬럼이 추가된 데이터프레임
    """
    sma_col = f'SMA_{sma_period}'
    # 함수 실행을 위해 필요한 지표가 있는지 확인
    if not all(col in df.columns for col in ['ADX_14', 'DMP_14', 'DMN_14', sma_col]):
        logger.warning(f"필수 지표가 없어 시장 국면을 정의할 수 없습니다. (ADX, DMI, {sma_col})")
        df['regime'] = 'sideways'  # 지표가 없으면 일단 관망
        return df

    # 1. ADX가 임계값보다 낮으면 '횡보장(sideways)'으로 우선 정의
    is_sideways = df['ADX_14'] < adx_threshold

    # 2. ADX가 임계값보다 높을 때 (추세가 있을 때)
    # 2-1. 상승 추세 조건: +DI가 -DI보다 위에 있고, 종가가 이평선 위에 있을 때
    is_bull_trend = (df['ADX_14'] >= adx_threshold) & (df['DMP_14'] > df['DMN_14']) & (df['close'] > df[sma_col])

    # 2-2. 하락 추세 조건: -DI가 +DI보다 위에 있고, 종가가 이평선 아래에 있을 때
    is_bear_trend = (df['ADX_14'] >= adx_threshold) & (df['DMN_14'] > df['DMP_14']) & (df['close'] < df[sma_col])

    # np.select를 사용하여 조건에 따라 'bull', 'bear', 'sideways' 값을 부여
    # 횡보장 조건을 가장 먼저 체크합니다.
    df['regime'] = np.select(
        [is_sideways, is_bull_trend, is_bear_trend],
        ['sideways', 'bull', 'bear'],
        default='sideways'  # 위 세가지 명확한 조건 외 애매한 경우는 모두 '횡보(관망)'으로 처리
    )

    logger.info(f"✅ 개선된 로직으로 시장 국면('regime') 컬럼을 생성/업데이트했습니다.")
    return df