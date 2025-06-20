# analyze_market_regime.py
# 수집된 데이터를 바탕으로 과거 시장 국면을 분석하고 시각화하는 스크립트입니다.

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import logging

# --- 초기 설정 ---
import config
from data import data_manager
from utils import indicators
from logging_setup import setup_logger

# 로거 설정
setup_logger()
logger = logging.getLogger()


# --- 1. 데이터 준비 ---
def prepare_data_for_analysis(ticker, interval):
    """분석에 필요한 모든 데이터를 로드하고 지표 및 국면을 계산합니다."""
    logger.info(f"분석을 위해 '{ticker}'의 전체 데이터를 로드합니다...")
    df_raw = data_manager.load_prepared_data(ticker, interval, for_bot=False)

    if df_raw is None or df_raw.empty:
        logger.error("데이터 로드에 실패했습니다. 분석을 중단합니다.")
        return None

    logger.info("기술적 지표를 계산합니다...")
    all_params = []
    all_params.extend([s.get('params', {}) for s in config.ENSEMBLE_CONFIG['strategies']])
    all_params.extend([s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()])
    df_with_indicators = indicators.add_technical_indicators(df_raw, all_params)

    logger.info("계산된 지표를 바탕으로 시장 국면을 정의합니다...")
    df_with_regime = indicators.define_market_regime_sma(df_with_indicators)
    df_with_regime = indicators.define_market_regime_adx(df_with_regime)
    df_with_regime = indicators.define_market_regime_atr(df_with_regime)

    if not all(k in df_with_regime for k in ['regime_sma', 'regime_adx', 'regime_atr']):
        logger.error("시장 국면 정의에 필요한 컬럼이 부족하여 최종 국면을 정의할 수 없습니다.")
        return None

    conditions = [
        (df_with_regime['regime_atr'] == 'sideways') & (df_with_regime['regime_adx'] == 'sideways'),
        (df_with_regime['regime_sma'] == 'bull') & (df_with_regime['regime_adx'] == 'bull'),
        (df_with_regime['regime_sma'] == 'bear') & (df_with_regime['regime_adx'] == 'bear')
    ]
    choices = ['sideways', 'bull', 'bear']
    df_with_regime['regime'] = np.select(conditions, choices, default='sideways')

    logger.info("✅ 데이터 준비가 모두 완료되었습니다.")
    return df_with_regime


# --- 2. 분석 및 시각화 ---
def analyze_specific_date(df, target_date):
    """과거 특정 날짜의 시장 국면을 확인합니다."""
    try:
        regime_on_date = df.loc[target_date, 'regime']
        print(f"\n[분석 결과] {target_date} 시점의 시장 국면은 '{regime_on_date.upper()}' 이었습니다.")
    except KeyError:
        print(f"\n[분석 결과] {target_date}에 해당하는 데이터가 없습니다.")
    except AttributeError:
        # regime_on_date가 Series로 반환되는 예외적인 경우를 대비한 방어 코드
        regime_series = df.loc[target_date, 'regime']
        if not regime_series.empty:
            print(f"\n[분석 결과] {target_date} 시점의 시장 국면은 '{regime_series.iloc[0].upper()}' 이었습니다.")
        else:
            print(f"\n[분석 결과] {target_date}에 해당하는 데이터를 찾았으나 국면 값을 읽을 수 없습니다.")


def plot_regime_chart(df, ticker):
    """전체 기간 그래프에 시장 국면을 색상으로 표시합니다."""
    print("\n[시각화] 시장 국면 차트를 생성합니다...")
    # matplotlib에서 한글 폰트가 깨지지 않도록 설정
    try:
        # 윈도우에 기본적으로 설치된 '맑은 고딕' 폰트를 사용합니다.
        plt.rc('font', family='Malgun Gothic')
        # 마이너스 기호가 깨지는 것을 방지합니다.
        plt.rcParams['axes.unicode_minus'] = False
    except Exception as e:
        logger.warning(f"한글 폰트(Malgun Gothic) 설정 중 오류 발생: {e}. 폰트가 깨질 수 있습니다.")
        logger.warning("macOS의 경우 'AppleGothic', 리눅스의 경우 'NanumGothic' 등을 설치하고 사용해야 합니다.")

    plt.style.use('seaborn-v0_8-whitegrid')
    fig, ax = plt.subplots(figsize=(20, 10))

    # df['regime'] 컬럼의 값('bull', 'bear', 'sideways')과 직접 비교합니다.
    df_bull = df[df['regime'] == 'bull']
    df_bear = df[df['regime'] == 'bear']
    df_sideways = df[df['regime'] == 'sideways']

    ax.scatter(df_bull.index, df_bull['close'], color='limegreen', label='상승장 (Bull)', alpha=0.5, s=10, zorder=2)
    ax.scatter(df_bear.index, df_bear['close'], color='crimson', label='하락장 (Bear)', alpha=0.5, s=10, zorder=2)
    ax.scatter(df_sideways.index, df_sideways['close'], color='darkgray', label='횡보장 (Sideways)', alpha=0.3, s=10,
               zorder=2)

    ax.plot(df.index, df['close'], color='black', alpha=0.4, linewidth=1.5, label=f'{ticker} 종가', zorder=1)

    ax.set_title(f'{ticker} 종가 그래프와 시장 국면 분석', fontsize=20, pad=20)
    ax.set_xlabel('날짜', fontsize=12)
    ax.set_ylabel('종가 (KRW) - 로그 스케일', fontsize=12)
    ax.set_yscale('log')
    ax.legend(fontsize=12, loc='upper left')

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    fig.autofmt_xdate()

    plt.show()

# --- 3. 메인 실행 블록 ---
if __name__ == "__main__":
    TICKER_TO_ANALYZE = "KRW-BTC"
    INTERVAL_TO_ANALYZE = "day"
    DATE_TO_CHECK = '2022-11-21'

    final_df = prepare_data_for_analysis(TICKER_TO_ANALYZE, INTERVAL_TO_ANALYZE)

    if final_df is not None:
        analyze_specific_date(final_df, DATE_TO_CHECK)
        plot_regime_chart(final_df, TICKER_TO_ANALYZE)