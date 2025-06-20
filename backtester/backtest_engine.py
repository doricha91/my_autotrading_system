# backtester/backtest_engine.py

import pandas as pd
import itertools
import logging
from typing import Dict, Any, List

# 프로젝트의 다른 모듈 임포트
import config
from data import data_manager
from utils import indicators
from backtester import performance
from core import strategy # <--- 이 줄을 추가하세요.
from backtester import performance, results_handler # <--- results_handler 추가


# 로거 설정
logger = logging.getLogger(__name__)


def _run_single_backtest(df_with_indicators: pd.DataFrame, params: Dict[str, Any]) -> (pd.DataFrame, pd.DataFrame):
    """단일 파라미터 조합에 대한 백테스트를 수행하는 내부 함수"""
    strategy_func = strategy.get_strategy_function(params['strategy_name'])
    if not strategy_func:
        logger.error(f"전략 함수 '{params['strategy_name']}'를 찾을 수 없습니다.")
        return pd.DataFrame(), pd.DataFrame()

    # 시그널 생성
    df_signal = strategy_func(df_with_indicators.copy(), params)

    # 포트폴리오 시뮬레이션
    trade_log, portfolio_history = performance.run_portfolio_simulation(
        df_signal,
        initial_capital=config.INITIAL_CAPITAL,
        stop_loss_atr_multiplier=params.get('stop_loss_atr_multiplier'),
        trailing_stop_percent=params.get('trailing_stop_percent'),
        partial_profit_target=params.get('partial_profit_target'),
        partial_profit_ratio=params.get('partial_profit_ratio')
    )
    return trade_log, portfolio_history


def run_grid_search(
        ticker: str,
        interval: str,
        strategy_name: str,
        param_grid: Dict,
        base_params: Dict,
        data_df: pd.DataFrame = None,
        start_date: str = None,
        end_date: str = None
) -> (pd.DataFrame, Dict):
    """
    주어진 설정에 따라 그리드 서치를 수행하고, 최적의 파라미터와 그 성과를 반환합니다.
    이 함수는 이제 독립적으로 재사용 가능합니다.
    """
    logger.info(f"===== 그리드 서치 시작: Ticker: {ticker}, Strategy: {strategy_name} =====")

    # 1. 데이터 준비
    if data_df is None:
        logger.info(f"{ticker} ({interval}) 데이터를 로드합니다.")
        df_raw = data_manager.load_prepared_data(ticker, interval)
        if df_raw.empty:
            logger.error("데이터 로드 실패. 그리드 서치를 종료합니다.")
            return pd.DataFrame(), {}
    else:
        logger.info("제공된 데이터프레임을 사용하여 그리드 서치를 진행합니다.")
        df_raw = data_df.copy()

    # 2. 파라미터 조합 생성
    keys = param_grid.keys()
    values = param_grid.values()
    param_combinations = [dict(zip(keys, combo)) for combo in itertools.product(*values)]

    all_strategies_to_run = []
    for i, combo_params in enumerate(param_combinations):
        params = {**base_params, **combo_params, 'strategy_name': strategy_name}
        exp_name_parts = [f"{key[:4]}{val}" for key, val in combo_params.items()]
        params['experiment_name'] = f"GS_{strategy_name[:5]}_{'_'.join(exp_name_parts)}_{i}"
        all_strategies_to_run.append(params)

    if not all_strategies_to_run:
        logger.warning("테스트할 파라미터 조합이 없습니다.")
        return pd.DataFrame(), {}

    # 3. 모든 조합에 필요한 지표를 한 번에 계산
    df_with_indicators = indicators.add_technical_indicators(df_raw, all_strategies_to_run)

    # 4. 날짜 필터링 (필요시)
    df_ready = df_with_indicators
    if start_date and end_date:
        logger.info(f"백테스트 기간을 {start_date}부터 {end_date}까지로 제한합니다.")
        df_ready = df_ready.loc[start_date:end_date].copy()
        if df_ready.empty:
            logger.error("지정된 기간에 해당하는 데이터가 없어 백테스트를 중단합니다.")
            return pd.DataFrame(), {}

    # 5. 각 조합에 대해 백테스팅 실행
    all_results = []
    for params_to_run in all_strategies_to_run:
        logger.info(f"--- 실험 시작: {params_to_run['experiment_name']} ---")
        trade_log, portfolio_history = _run_single_backtest(df_ready.copy(), params_to_run)

        if not portfolio_history.empty:
            summary = performance.analyze_performance(portfolio_history, trade_log, config.INITIAL_CAPITAL, interval)
            summary.update({'실험명': params_to_run['experiment_name'], '파라미터': str(params_to_run)})
            all_results.append(summary)

    if not all_results:
        logger.error("그리드 서치에서 유의미한 결과를 얻지 못했습니다.")
        return pd.DataFrame(), {}

        # 6. 최적 결과 선정, 저장 및 반환
    results_df = pd.DataFrame(all_results).sort_values(by='Calmar', ascending=False)
    best_result = results_df.iloc[0].to_dict()

    # --- 추가된 DB 저장 로직 ---
    logger.info("그리드 서치 결과를 DB에 저장합니다...")
    results_handler.save_results(results_df, 'grid_search_results')
    # ---------------------------

    logger.info(f"===== 그리드 서치 완료: 최적 파라미터 Calmar: {best_result.get('Calmar', 0):.2f} =====")

    return results_df, best_result


# multi_ticker_test도 날짜 인자를 받을 수 있도록 수정합니다.
def run_multi_ticker_test(
        tickers: List[str],
        interval: str,
        champions_to_run: List[Dict],
        start_date: str = None,
        end_date: str = None
) -> pd.DataFrame:
    """
    여러 티커에 대해 여러 '챔피언' 전략을 테스트합니다.
    """
    logger.info("===== 멀티 티커 '왕중왕전' 모드로 백테스팅을 시작합니다. =====")

    overall_results = []
    for ticker in tickers:
        logger.info(f"\n======= 티커 [{ticker}] 테스트 시작 =======")
        try:
            df_raw = data_manager.load_prepared_data(ticker, interval)
            df_with_indicators = indicators.add_technical_indicators(df_raw, champions_to_run)

            # 날짜 필터링 로직 추가
            df_ready = df_with_indicators
            if start_date and end_date:
                logger.info(f"백테스트 기간을 {start_date}부터 {end_date}까지로 제한합니다.")
                df_ready = df_ready.loc[start_date:end_date].copy()
                if df_ready.empty:
                    logger.warning("지정된 기간에 해당하는 데이터가 없어 이 티커를 건너뜁니다.")
                    continue

        except Exception as e:
            logger.error(f"[{ticker}] 데이터 로드 또는 지표 계산 실패: {e}")
            continue

        for champion_params in champions_to_run:
            exp_name = f"{champion_params['experiment_name_prefix']}_{ticker}"
            params = {**champion_params['params'], 'strategy_name': champion_params['strategy_name'],
                      'experiment_name': exp_name}

            logger.info(f"--- 실험 시작: {exp_name} ---")
            trade_log, portfolio_history = _run_single_backtest(df_ready.copy(), params)  # df_ready 사용

            if not portfolio_history.empty:
                summary = performance.analyze_performance(portfolio_history, trade_log, config.INITIAL_CAPITAL,
                                                          interval)
                summary.update({'티커': ticker, '실험명': exp_name, '파라미터': str(champion_params['params'])})
                overall_results.append(summary)

    if not overall_results:
        logger.warning("멀티 티커 테스트에서 유의미한 결과를 얻지 못했습니다.")
        return pd.DataFrame()

    results_df = pd.DataFrame(overall_results).sort_values(by=['티커', 'Calmar'], ascending=[True, False])

    logger.info("\n\n" + "=" * 80 + "\n" + "🏆 멀티 티커 테스트 최종 결과 요약" + "\n" + "=" * 80)
    print(results_df)

    results_handler.save_results(results_df, 'multi_ticker_results')
    return results_df


def run(start_date: str = None, end_date: str = None):
    """
    config.py 설정을 읽어 백테스트의 메인 모드를 결정하고 실행하는 엔트리 포인트.
    `main.py`에서 이 함수를 호출합니다.
    """
    if not hasattr(config, 'BACKTEST_MODE'):
        logger.error("'config.py'에 'BACKTEST_MODE' 설정이 없습니다. 'grid_search' 또는 'multi_ticker'를 설정해주세요.")
        return

    mode = config.BACKTEST_MODE
    logger.info(f"백테스트 엔진 실행. 모드: {mode}")

    if mode == 'grid_search':
        cfg = config.GRID_SEARCH_CONFIG
        all_results_df, _ = run_grid_search(
            ticker=cfg['target_ticker'],
            interval=cfg['target_interval'],
            strategy_name=cfg['target_strategy_name'],
            param_grid=cfg['param_grid'],
            base_params=cfg['base_params'],
            start_date=start_date,  # 날짜 인자 전달
            end_date=end_date      # 날짜 인자 전달
        )
        if not all_results_df.empty:
            logger.info("\n\n" + "=" * 80 + "\n" + "💰 그리드 서치 최종 결과 요약 (Calmar 기준 정렬)" + "\n" + "=" * 80)
            print(all_results_df)

    elif mode == 'multi_ticker':
        cfg = config.MULTI_TICKER_CONFIG
        results_df = run_multi_ticker_test(
            tickers=cfg['tickers_to_test'],
            interval=cfg['target_interval'],
            champions_to_run=cfg['champions_to_run'],
            start_date=start_date,  # 날짜 인자 전달
            end_date=end_date      # 날짜 인자 전달
        )
        # 멀티 티커의 결과 요약은 run_multi_ticker_test 함수 내부에서 처리됩니다.
    else:
        logger.error(f"알 수 없는 백테스트 모드: {mode}. 'grid_search' 또는 'multi_ticker'를 사용하세요.")
