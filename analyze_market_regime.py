# analyze_market_regime.py
# 과거 데이터 전체에 대한 시장 국면을 분석하고 시각화하는 스크립트

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import logging

import config
from data import data_manager
from utils import indicators
from logging_setup import setup_logger

logger = logging.getLogger()


def analyze_and_plot_regime(ticker: str, interval: str):
    """
    특정 티커의 전체 데이터를 로드하여 시장 국면을 분석하고 그래프로 시각화합니다.
    """
    logger.info(f"분석을 위해 '{ticker}'의 전체 데이터를 로드합니다...")

    # 1. 데이터 로드 (for_bot=False로 전체 기간 데이터를 가져옴)
    df_full = data_manager.load_prepared_data(ticker, interval, for_bot=False)

    if df_full.empty:
        logger.error("데이터 로드에 실패하여 분석을 중단합니다.")
        return

    logger.info("기술적 지표를 계산합니다...")
    # 2. 기술적 지표 계산
    # 백테스트에 필요한 모든 전략 파라미터를 임시로 가져와 필요한 모든 지표를 계산
    all_params = []
    all_params.extend([s.get('params', {}) for s in config.ENSEMBLE_CONFIG['strategies']])
    all_params.extend([s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()])
    df_with_indicators = indicators.add_technical_indicators(df_full, all_params)

    logger.info("계산된 지표를 바탕으로 시장 국면을 정의합니다...")
    # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
    # 오류 해결: 지표가 계산된 데이터프레임을 받아 국면을 정의합니다.
    # 3. 시장 국면 정의
    df_final = indicators.define_market_regime(df_with_indicators)

    # 4. 국면 정의가 성공적으로 되었는지 확인
    if 'regime' not in df_final.columns:
        logger.error("시장 국면 정의에 필요한 컬럼이 부족하여 최종 국면을 정의할 수 없습니다.")
        return
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

    # 5. 시각화
    logger.info("분석 결과를 시각화합니다...")
    plt.style.use('dark_background')  # 어두운 배경 스타일 사용
    plt.figure(figsize=(20, 10))

    # 국면별 데이터 분리
    df_bull = df_final[df_final['regime'] == 'bull']
    df_bear = df_final[df_final['regime'] == 'bear']
    df_sideways = df_final[df_final['regime'] == 'sideways']

    # 국면별로 다른 색상으로 점(scatter plot) 그리기
    plt.scatter(df_bull.index, df_bull['close'], color='#4CAF50', label='Bull Market', alpha=0.6, s=10)  # Green
    plt.scatter(df_bear.index, df_bear['close'], color='#F44336', label='Bear Market', alpha=0.6, s=10)  # Red
    plt.scatter(df_sideways.index, df_sideways['close'], color='#757575', label='Sideways Market', alpha=0.4,
                s=5)  # Gray

    # 전체 종가 그래프를 얇은 선으로 겹쳐 그리기
    plt.plot(df_final.index, df_final['close'], color='white', alpha=0.3, linewidth=1, label=f'{ticker} 종가')

    # 그래프 스타일 설정
    plt.title(f'{ticker} 종가 그래프와 시장 국면 분석', fontsize=20, color='white')
    plt.xlabel('날짜', fontsize=12, color='white')
    plt.ylabel('종가 (KRW, 로그 스케일)', fontsize=12, color='white')
    plt.yscale('log')
    plt.legend(fontsize=12)
    plt.grid(True, which="both", ls="--", linewidth=0.5, color='gray')

    # X, Y축 눈금 색상 및 스타일
    plt.tick_params(axis='x', colors='white')
    plt.tick_params(axis='y', colors='white')

    # 날짜 포맷 설정
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    plt.gca().xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    plt.gcf().autofmt_xdate()

    plt.show()


if __name__ == '__main__':
    # 로거 설정
    setup_logger()

    # 분석하고 싶은 티커와 인터벌을 여기서 지정
    TICKER_TO_ANALYZE = "KRW-BTC"
    INTERVAL_TO_ANALYZE = "day"

    analyze_and_plot_regime(TICKER_TO_ANALYZE, INTERVAL_TO_ANALYZE)