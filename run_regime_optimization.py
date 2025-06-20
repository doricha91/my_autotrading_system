# run_regime_optimization.py

import pandas as pd
import logging
import warnings

# 프로젝트 모듈 임포트
from data import data_manager
from utils import indicators
from backtester import backtest_engine, performance, results_handler # <--- results_handler 추가
import pandas_ta as ta # <--- pandas_ta 임포트 추가


# 경고 메시지 무시 (선택 사항)
warnings.filterwarnings('ignore', category=FutureWarning)

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def run_full_regime_optimization():
    """
    국면 분석 -> 국면별 그리드 서치 -> 결과 통합의 전체 과정을 자동 수행합니다.
    """
    ticker = "KRW-BTC"
    interval = "day"

    logging.info(f"'{ticker}'의 '{interval}' 데이터에 대한 국면 기반 전체 최적화를 시작합니다.")

    # 1. 데이터 로드 및 국면 정의
    try:
        full_df = data_manager.load_prepared_data(ticker, interval)

        # 국면 정의에 필요한 지표를 *먼저* 계산합니다.
        logging.info("국면 정의를 위한 필수 지표(ADX, SMA)를 계산합니다.")
        full_df['SMA_50'] = ta.sma(full_df['close'], length=50)
        adx_indicator = ta.adx(full_df['high'], full_df['low'], full_df['close'], length=14)
        if adx_indicator is not None and not adx_indicator.empty:
            full_df = full_df.join(adx_indicator)  # ADX, DMP, DMN 컬럼 추가

        # 지표 계산 후 국면을 정의합니다.
        full_df = indicators.define_market_regime(full_df)
        logging.info(f"국면 정의 완료. 분포:\n{full_df['regime'].value_counts(normalize=True)}")

    except Exception as e:
        logging.error(f"데이터 로드 또는 국면 정의 중 오류: {e}")
        return

    # 2. 국면별 최적화 설정
    regime_grid_search_setup = {
        'bull': {
            'strategy_name': 'trend_following',
            'param_grid': {
                'breakout_window': [20, 30, 40],
                'volume_multiplier': [1.5, 2.0],
            },
            'base_params': {'long_term_sma_period': 50, 'volume_avg_window': 20}
        },
        'sideways': {
            'strategy_name': 'rsi_mean_reversion',
            'param_grid': {
                'rsi_period': [14, 21],
                'oversold_level': [25, 30, 35],
            },
            'base_params': {'overbought_level': 70}
        },
        'bear': {
            'strategy_name': 'volatility_breakout',
            'param_grid': {
                'k': [0.3, 0.5, 0.7],
            },
            'base_params': {'long_term_sma_period': 200}
        }
    }

    final_best_strategies = {}

    # 3. 국면별 그리드 서치 실행
    for regime, setup in regime_grid_search_setup.items():
        logging.info(f"\n===== '{regime.upper()}' 국면 그리드 서치 시작 =====")
        regime_df = full_df[full_df['regime'] == regime].copy()

        if regime_df.empty:
            logging.warning(f"'{regime}' 국면 데이터가 없어 건너뜁니다.")
            continue

        _, best_result = backtest_engine.run_grid_search(
            ticker=ticker,
            interval=interval,
            strategy_name=setup['strategy_name'],
            param_grid=setup['param_grid'],
            base_params=setup['base_params'],
            data_df=regime_df  # 필터링된 데이터를 직접 전달
        )

        if best_result:
            final_best_strategies[regime] = best_result

    # 4. 최종 결과 출력
    logging.info("\n\n" + "=" * 80 + "\n" + "👑 국면별 최적 전략 최종 요약" + "\n" + "=" * 80)
    summary_list = []
    for regime, result in final_best_strategies.items():
        print(f"\n--- {regime.upper()} 국면 최적 전략 ---")
        print(f"  - 최적 파라미터: {result.get('파라미터')}")
        # result 딕셔너리에서 주요 성과 지표를 직접 꺼내서 출력합니다.
        print(f"    - 최종 수익률 (ROI): {result.get('ROI (%)', 0):.2f}%")
        print(f"    - 최대 낙폭 (MDD): {result.get('MDD (%)', 0):.2f}%")
        print(f"    - 캘머 지수 (Calmar): {result.get('Calmar', 0):.2f}")
        print(f"    - 승률 (Win Rate): {result.get('Win Rate (%)', 0):.2f}%")
        print(f"    - 총 거래 횟수: {result.get('Total Trades', 0)}")

        result['regime'] = regime
        summary_list.append(result)
    if summary_list:
        summary_df = pd.DataFrame(summary_list)
        results_handler.save_results(summary_df, 'regime_optimization_summary')


if __name__ == "__main__":
    run_full_regime_optimization()