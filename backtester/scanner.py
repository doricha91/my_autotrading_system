# backtester/scanner.py
# 📈 백테스트에서 검증된 국면 분석 및 랭킹 로직을 사용하여 유망 코인을 스캔하는 모듈입니다.

import logging
import pandas as pd

# --- 프로젝트 핵심 모듈 임포트 ---
import config
from data import data_manager
from utils import indicators  # 국면 분석 및 랭킹 함수가 있는 모듈

class Scanner:
    """
    설정된 기준에 따라 티커를 스캔, 필터링, 랭킹하여 최종 거래 대상을 선정합니다.
    (run_scanner_backtest.py의 실시간 버전)
    """
    # 💡 [수정] __init__ 메서드에서 upbit_api 파라미터를 완전히 제거합니다.
    def __init__(self, settings: dict = None):
        """
        스캐너를 초기화합니다.
        :param settings: config.py에 정의된 SCANNER_SETTINGS 딕셔너리
        """
        self.logger = logging.getLogger(__name__)
        self.settings = settings if settings is not None else config.SCANNER_SETTINGS
        self.logger.info(f"Scanner initialized with strategy: Regime Analysis (using historical data for ranking)")

    def scan_tickers(self) -> tuple[list, dict]:  # ✨ 반환 값에 dict 추가
        """
        유망한 티커를 스캔하고 필터링하여 최종 목록과 국면 분석 결과를 함께 반환합니다.
        """
        self.logger.info("시장 국면 분석 기반 스캔을 시작합니다...")
        try:
            # 1. 설정 파일에서 모니터링할 전체 티커 목록 로드
            tickers_to_monitor = config.TICKERS_TO_MONITOR
            if not tickers_to_monitor:
                self.logger.warning("config.TICKERS_TO_MONITOR에 스캔할 티커가 지정되지 않았습니다.")
                return [], {}  # ✨ 반환 값을 튜플로 변경

            # ... (데이터 로드 및 보조 지표 추가 로직은 기존과 동일) ...
            all_data = {}
            for ticker in tickers_to_monitor:
                df = data_manager.load_prepared_data(ticker, config.TRADE_INTERVAL, for_bot=True)
                if df is not None and not df.empty:
                    all_data[ticker] = df

            if not all_data:
                self.logger.error("스캔을 위한 데이터를 로드할 수 없습니다.")
                return [], {}  # ✨ 반환 값을 튜플로 변경

            all_params_for_indicators = []
            all_params_for_indicators.extend(
                [s.get('params', {}) for s in config.ENSEMBLE_CONFIG.get('strategies', [])])
            all_params_for_indicators.extend([s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()])
            all_params_for_indicators.append(config.COMMON_REGIME_PARAMS)

            for ticker, df in all_data.items():
                all_data[ticker] = indicators.add_technical_indicators(df, all_params_for_indicators)

            # 3. 모든 티커에 대해 현재 시장 국면 분석
            current_date = pd.Timestamp.now().floor('D')
            regime_results = indicators.analyze_regimes_for_all_tickers(
                all_data, current_date, **config.COMMON_REGIME_PARAMS
            )

            # 4. '상승장(bull)'에 있는 코인만 필터링
            bull_tickers = [ticker for ticker, regime in regime_results.items() if regime == 'bull']
            if not bull_tickers:
                self.logger.info("현재 상승장으로 판단되는 코인이 없습니다.")
                return [], {}  # ✨ 반환 값을 튜플로 변경
            self.logger.info(f"상승장 후보 발견: {bull_tickers}")
            self.logger.info(f"--- [상승장 필터링 결과] --- 모든 상승장 코인: {bull_tickers}")

            # 5. 거래량 기준 정렬
            ranked_candidates = indicators.rank_candidates_by_volume(
                bull_tickers, all_data, current_date, config.TRADE_INTERVAL_HOURS
            )
            self.logger.info(f"거래량(최신 데이터 기준) 순위: {ranked_candidates}")
            self.logger.info(f"--- [거래량 랭킹 결과] --- {ranked_candidates}")

            # 6. 최종 대상 선정
            max_trades = config.MAX_CONCURRENT_TRADES
            final_candidates = ranked_candidates[:max_trades]
            self.logger.info(f"최대 동시 투자 개수({max_trades}개) 적용 후 최종 타겟: {final_candidates}")

            # ✨ [핵심 수정] 최종 후보 리스트와 함께, 전체 국면 분석 결과를 반환합니다.
            return final_candidates, regime_results

        except Exception as e:
            self.logger.error(f"티커 스캔 중 오류 발생: {e}", exc_info=True)
            return [], {}  # ✨ 반환 값을 튜플로 변경