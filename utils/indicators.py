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
    df_copy.ta.atr(length=14, append=True, col_names=('ATRr_14',))
    df_copy.ta.obv(append=True)
    # ADX 지표는 시장 국면 정의에 필요하므로 여기서 미리 계산해 줍니다.
    df_copy.ta.adx(append=True)
    df_copy['range'] = df_copy['high'].shift(1) - df_copy['low'].shift(1)

    # 4. 거시 경제 데이터가 있다면, 관련 지표도 추가할 수 있습니다. (예: 이동평균선)
    if 'nasdaq_close' in df_copy.columns:
        df_copy['nasdaq_sma_200'] = df_copy['nasdaq_close'].rolling(window=200).mean()

    logger.info("✅ 모든 기술적 지표 계산이 완료되었습니다.")
    return df_copy

def define_market_regime_sma(df: pd.DataFrame) -> pd.DataFrame:
    """
    방법 1: 이동평균선을 이용해 시장 국면을 정의합니다.
    - 상승장(bull): 50일 이평선과 200일 이평선이 정배열이고, 종가가 200일선 위에 있을 때
    - 하락장(bear): 50일 이평선과 200일 이평선이 역배열이고, 종가가 200일선 아래에 있을 때
    - 횡보장(sideways): 그 외 모든 경우
    """
    # 함수 실행을 위해 필요한 지표가 있는지 확인
    if not all(col in df.columns for col in ['SMA_50', 'SMA_200']):
        logger.warning("SMA_50 또는 SMA_200 지표가 없어 SMA 기반 시장 국면을 정의할 수 없습니다.")
        return df

    # 상승장 조건: 정배열 및 상승 추세
    bull_condition = (df['close'] > df['SMA_200']) & (df['SMA_50'] > df['SMA_200'])

    # 하락장 조건: 역배열 및 하락 추세
    bear_condition = (df['close'] < df['SMA_200']) & (df['SMA_50'] < df['SMA_200'])

    # np.select를 사용하여 조건에 따라 'bull', 'bear', 'sideways' 값을 부여
    df['regime_sma'] = np.select(
        [bull_condition, bear_condition],
        ['bull', 'bear'],
        default='sideways'
    )
    logger.info("✅ 이동평균선(SMA) 기반 시장 국면('regime_sma') 컬럼이 추가되었습니다.")
    return df

def define_market_regime_adx(df: pd.DataFrame, threshold: int = 25) -> pd.DataFrame:
    """
    방법 2: ADX 지표를 이용해 시장 국면을 정의합니다.
    - 상승장(bull): ADX가 임계값 이상이고, +DI > -DI 일 때
    - 하락장(bear): ADX가 임계값 이상이고, -DI > +DI 일 때
    - 횡보장(sideways): ADX가 임계값 미만일 때
    """
    # 함수 실행을 위해 필요한 지표가 있는지 확인
    if not all(col in df.columns for col in ['ADX_14', 'DMP_14', 'DMN_14']):
        logger.warning("ADX 또는 DMI 지표가 없어 ADX 기반 시장 국면을 정의할 수 없습니다.")
        return df

    # 상승장 조건: ADX가 기준값보다 높고(추세가 강하고), +DI가 -DI보다 위에 있을 때
    bull_condition = (df['ADX_14'] > threshold) & (df['DMP_14'] > df['DMN_14'])

    # 하락장 조건: ADX가 기준값보다 높고(추세가 강하고), -DI가 +DI보다 위에 있을 때
    bear_condition = (df['ADX_14'] > threshold) & (df['DMN_14'] > df['DMP_14'])

    # np.select를 사용하여 조건에 따라 'bull', 'bear', 'sideways' 값을 부여
    df['regime_adx'] = np.select(
        [bull_condition, bear_condition],
        ['bull', 'bear'],
        default='sideways'
    )
    logger.info(f"✅ ADX(임계값: {threshold}) 기반 시장 국면('regime_adx') 컬럼이 추가되었습니다.")
    return df

def define_market_regime_atr(df: pd.DataFrame, window: int = 50) -> pd.DataFrame:
    """
    방법 3: ATR(Average True Range)을 이용해 변동성 기반으로 시장 국면을 정의합니다.
    - 횡보장(sideways): 현재 변동성(ATR)이 과거 N일 평균 변동성보다 낮을 때
    - 그 외에는 이동평균선으로 추세 판단
    """
    # 함수 실행을 위해 필요한 지표가 있는지 확인
    if not all(col in df.columns for col in ['ATRr_14', 'SMA_50']):
        logger.warning("ATRr_14 또는 SMA_50 지표가 없어 ATR 기반 시장 국면을 정의할 수 없습니다.")
        return df

    # 과거 N일간의 평균 ATR을 계산
    df[f'ATR_MA{window}'] = df['ATRr_14'].rolling(window=window).mean()

    # 횡보장 조건: 현재 ATR이 과거 N일 평균 ATR보다 낮을 때 (변동성 축소)
    sideways_condition = df['ATRr_14'] < df[f'ATR_MA{window}']

    # 추세장 조건: 횡보장이 아니면서, 50일 이평선 위에 있을 때를 상승 추세로 간주
    bull_condition = ~sideways_condition & (df['close'] > df['SMA_50'])

    # 횡보장도 아니고 상승 추세도 아닌 나머지를 하락 추세로 간주
    bear_condition = ~sideways_condition & (df['close'] < df['SMA_50'])

    df['regime_atr'] = np.select(
        [sideways_condition, bull_condition, bear_condition],
        ['sideways', 'bull', 'bear'],
        default='unknown'  # 조건에 해당하지 않는 초기 데이터 등
    )
    logger.info(f"✅ ATR(기간: {window}) 기반 시장 국면('regime_atr') 컬럼이 추가되었습니다.")
    return df