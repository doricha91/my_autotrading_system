# utils/indicators.py
# 🛠️ 기술적 지표 계산을 전담하는 유틸리티 파일입니다.
# pandas-ta 라이브러리를 사용해 다양한 보조지표를 계산합니다.

import pandas as pd
import pandas_ta as ta
import numpy as np
import logging
import config # ✨ 1. config를 import 합니다.


logger = logging.getLogger()


def add_technical_indicators(df: pd.DataFrame, all_params_list: list) -> pd.DataFrame:
    """
    주어진 데이터프레임에 전략 실행에 필요한 모든 기술적 보조지표를 동적으로 계산하여 추가합니다.
    (개선된 버전)
    """
    logger.info("기술적 지표 동적 계산을 시작합니다...")
    if df is None or df.empty:
        logger.warning("입력 데이터프레임이 비어있어 지표 계산을 건너뜁니다.")
        return df

    df_copy = df.copy()
    sma_periods, high_low_periods, rsi_periods = set(), set(), set()

    # 중첩된 딕셔너리를 재귀적으로 탐색하여 모든 지표 계산 주기를 수집하는 헬퍼 함수
    def find_periods_recursively(params_dict):
        for key, value in params_dict.items():
            if isinstance(value, dict):
                find_periods_recursively(value)  # 값이 딕셔너리면 더 깊이 탐색
            elif isinstance(value, (int, float)) and value > 0:
                value = int(value)
                # --- ✨ 핵심 개선 부분 ---
                # 'sma', 'period' 뿐만 아니라 '_ma' 키워드도 SMA 주기로 인식하도록 조건 확장
                if ('sma' in key and 'period' in key) or ('_ma' in key):
                    sma_periods.add(value)
                elif any(p in key for p in ['entry_period', 'exit_period', 'breakout_window']):
                    high_low_periods.add(value)
                elif 'rsi_period' in key:
                    rsi_periods.add(value)
                # --- ✨ 개선 끝 ---

    # ✨ [핵심 수정 1] 국면 판단에 필요한 SMA 주기를 항상 계산 목록에 포함시킵니다.
    if hasattr(config, 'COMMON_REGIME_PARAMS') and 'regime_sma_period' in config.COMMON_REGIME_PARAMS:
        sma_periods.add(config.COMMON_REGIME_PARAMS['regime_sma_period'])

    # ✨ [핵심 수정 2] 새로운 재귀 함수를 사용하여 모든 파라미터를 탐색합니다.
    for params in all_params_list:
        find_periods_recursively(params)

    # 1. SMA(이동평균선) 지표 계산
    logger.info(f"계산 필요 SMA 기간: {sorted(list(sma_periods))}")
    for period in sorted(list(sma_periods)):
        if period > 0 and f'SMA_{period}' not in df_copy.columns:
            df_copy.ta.sma(length=period, append=True)

    # 2. 최고가/최저가 지표 계산
    logger.info(f"계산 필요 High/Low 기간: {sorted(list(high_low_periods))}")
    for period in sorted(list(high_low_periods)):
        if period > 0:
            if f'high_{period}d' not in df_copy.columns:
                df_copy[f'high_{period}d'] = df_copy['high'].rolling(window=period).max()
            if f'low_{period}d' not in df_copy.columns:
                df_copy[f'low_{period}d'] = df_copy['low'].rolling(window=period).min()

    # 3. RSI 지표 계산
    logger.info(f"계산 필요 RSI 기간: {sorted(list(rsi_periods))}")
    for period in sorted(list(rsi_periods)):
        if period > 0 and f'RSI_{period}' not in df_copy.columns:
            df_copy.ta.rsi(length=period, append=True)

    # 3. 모든 전략에서 공통적으로 사용할 수 있는 기타 기본 지표들을 계산합니다.
    logger.info("공통 기본 지표(RSI, BBands, ATR, OBV, ADX 등)를 계산합니다.")
    df_copy.ta.rsi(length=14, append=True)
    df_copy.ta.bbands(length=20, std=2, append=True)
    atr_period = 14
    df_copy.ta.atr(length=atr_period, append=True)
    df_copy.ta.obv(append=True)
    df_copy.ta.adx(append=True)

    df_copy['range'] = df_copy['high'].shift(1) - df_copy['low'].shift(1)
    original_atr_col_name = f'ATRr_{atr_period}'
    if original_atr_col_name in df_copy.columns:
        df_copy.rename(columns={original_atr_col_name: 'ATR'}, inplace=True)
        # logger.info(f"'{original_atr_col_name}' 컬럼을 'ATR'로 변경했습니다.") # 로그 간소화

    if 'nasdaq_close' in df_copy.columns:
        df_copy['nasdaq_sma_200'] = df_copy['nasdaq_close'].rolling(window=200).mean()

    logger.info("✅ 모든 기술적 지표 계산이 완료되었습니다.")
    return df_copy


# (이하 함수들은 변경 없음)
def define_market_regime(df: pd.DataFrame) -> pd.DataFrame:  # ✨ 인자에서 기본값 제거
    """
    ADX와 이동평균선을 조합하여 시장 국면을 'bull', 'bear', 'sideways'로 정의합니다.
    ✨ [핵심 수정 2] 이제 모든 파라미터를 config.py에서 직접 가져와 사용합니다.
    """
    # config.py에서 국면 판단 파라미터를 직접 가져옵니다.
    params = config.COMMON_REGIME_PARAMS
    adx_threshold = params.get('adx_threshold', 25)
    sma_period = params.get('regime_sma_period', 50)

    sma_col = f'SMA_{sma_period}'
    required_cols = ['ADX_14', 'DMP_14', 'DMN_14', sma_col]

    if not all(col in df.columns for col in required_cols):
        missing_cols = [col for col in required_cols if col not in df.columns]
        logger.warning(f"필수 지표가 없어 시장 국면을 정의할 수 없습니다. (누락: {missing_cols})")
        df['regime'] = 'sideways'
        return df

    is_sideways = df['ADX_14'] < adx_threshold
    is_bull_trend = (df['ADX_14'] >= adx_threshold) & (df['DMP_14'] > df['DMN_14']) & (df['close'] > df[sma_col])
    is_bear_trend = (df['ADX_14'] >= adx_threshold) & (df['DMN_14'] > df['DMP_14']) & (df['close'] < df[sma_col])

    df['regime'] = np.select(
        [is_sideways, is_bull_trend, is_bear_trend],
        ['sideways', 'bull', 'bear'],
        default='sideways'
    )
    return df


def define_market_regime_v2_bb(df: pd.DataFrame, sma_period: int = 20) -> pd.DataFrame:
    """
    ✨[신규 함수]✨ 볼린저 밴드와 이동평균선을 기준으로 국면을 정의합니다.
    """
    upper_band = f'BBU_{sma_period}_2.0'
    lower_band = f'BBL_{sma_period}_2.0'

    if not all(col in df.columns for col in [upper_band, lower_band]):
        df.ta.bbands(length=sma_period, std=2.0, append=True)

    is_bull = df['close'] > df[upper_band]
    is_bear = df['close'] < df[lower_band]

    df['regime'] = np.select(
        [is_bull, is_bear],
        ['bull', 'bear'],
        default='sideways'
    )
    return df


def analyze_regimes_for_all_tickers(all_data: dict, current_date: pd.Timestamp,
                                    regime_sma_period: int = 50, version: str = 'v1',
                                    adx_threshold: int = 25) -> dict:
    """
    [로직 수정] 국면 판단 로직을 수정하여, 필요한 지표를 먼저 계산하도록 합니다.
    """
    regime_results = {}
    for ticker, df in all_data.items():
        data_at_date = df.loc[df.index <= current_date].copy()

        # ✨ 참고: config.py의 regime_sma_period 값을 직접 사용하도록 수정되었으므로,
        # 이 함수로 전달되는 regime_sma_period 값은 현재 사용되지 않습니다.
        # 호환성을 위해 파라미터는 남겨둡니다.
        sma_period_for_check = config.COMMON_REGIME_PARAMS.get('regime_sma_period', 50)
        if len(data_at_date) < sma_period_for_check:
            continue

        data_at_date.ta.adx(append=True)
        data_at_date.ta.sma(length=sma_period_for_check, append=True)

        if version == 'v2':
            data_at_date.ta.bbands(length=sma_period_for_check, std=2.0, append=True)
            df_with_regime = define_market_regime_v2_bb(data_at_date, sma_period=sma_period_for_check)
        else:
            # ✨ [핵심 수정] define_market_regime 함수 호출 시 불필요한 인자를 모두 제거합니다.
            # 이제 define_market_regime 함수가 직접 config.py를 참조하므로 인자 전달이 필요 없습니다.
            df_with_regime = define_market_regime(data_at_date)

        if not df_with_regime.empty:
            current_regime = df_with_regime['regime'].iloc[-1]
            regime_results[ticker] = current_regime

    return regime_results


def rank_candidates_by_volume(bull_tickers: list, all_data: dict, current_date: pd.Timestamp, interval: int) -> list:
    """
    [수정] 상승 국면 코인들을 '동적으로 계산된 기간'의 평균 거래대금을 기준으로 정렬합니다.
    """
    if not bull_tickers:
        return []

    # ✨ 2. config 파일에서 승수를 가져오고, 전달받은 interval을 곱하여 period를 동적으로 계산
    multiplier = config.SCANNER_SETTINGS.get('ranking_volume_period_multiplier', 5)
    period = interval * multiplier

    volume_ranks = {}
    for ticker in bull_tickers:
        data_at_date = all_data[ticker].loc[all_data[ticker].index <= current_date]

        if not data_at_date.empty:
            # ✨ 3. 하드코딩된 '5'를 동적으로 계산된 'period' 변수로 대체
            if len(data_at_date) >= period:
                avg_trade_value = (data_at_date['close'].iloc[-period:] * data_at_date['volume'].iloc[-period:]).mean()
                volume_ranks[ticker] = avg_trade_value

    sorted_tickers = sorted(volume_ranks.keys(), key=lambda t: volume_ranks[t], reverse=True)
    return sorted_tickers


def rank_candidates_by_momentum(bull_tickers: list, all_data: dict, current_date: pd.Timestamp,
                                momentum_days: int = 5) -> list:
    """
    상승 국면 코인들을 '최근 N일 가격 상승률' 기준으로 정렬합니다.
    """
    if not bull_tickers:
        return []

    momentum_ranks = {}
    for ticker in bull_tickers:
        if current_date in all_data[ticker].index:
            data_at_date = all_data[ticker].loc[all_data[ticker].index <= current_date]
            if len(data_at_date) >= momentum_days:
                price_now = data_at_date['close'].iloc[-1]
                price_before = data_at_date['close'].iloc[-momentum_days]
                momentum = (price_now - price_before) / price_before
                momentum_ranks[ticker] = momentum

    sorted_tickers = sorted(momentum_ranks.keys(), key=lambda t: momentum_ranks[t], reverse=True)
    return sorted_tickers
