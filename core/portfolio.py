# core/portfolio.py
# 💼 모의투자 및 실제투자 포트폴리오의 상태를 관리하고 DB와 연동합니다.

import sqlite3
import logging
import copy
from datetime import datetime
import config
import pyupbit

logger = logging.getLogger()


class PortfolioManager:
    def __init__(self, mode: str, upbit_api_client=None, initial_capital=10000000.0):
        self.mode = mode
        # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
        # 오류 해결: 클래스 인스턴스 변수에 DB 경로 저장
        self.db_path = config.LOG_DB_PATH
        # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
        self.upbit_api = upbit_api_client
        self.state = {}
        self.initial_capital = initial_capital

        self._setup_database()

        if self.mode == 'simulation':
            self._load_paper_portfolio()
        else:
            self.state = self._fetch_real_position() \
                if self.upbit_api \
                else {}

    def _setup_database(self):
        """거래 로그 저장을 위한 DB 테이블을 생성합니다."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # 모의 투자 상태 저장 테이블
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS paper_portfolio_state (
                    id INTEGER PRIMARY KEY, krw_balance REAL, asset_balance REAL, avg_buy_price REAL,
                    initial_capital REAL, fee_rate REAL, roi_percent REAL, highest_price_since_buy REAL,
                    last_updated TEXT, trade_cycle_count INTEGER DEFAULT 0)
            ''')
            # 모의 투자 거래 로그
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS paper_trade_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, action TEXT, price REAL, 
                    amount REAL, krw_value REAL, fee REAL, context TEXT)
            ''')
            # 실제 투자 거래 로그
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS real_trade_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, action TEXT, ticker TEXT, 
                    upbit_uuid TEXT UNIQUE, price REAL, amount REAL, krw_value REAL, 
                    reason TEXT, context TEXT, upbit_response TEXT)
            ''')
            logger.info(f"✅ '{self.db_path}' 데이터베이스가 성공적으로 준비되었습니다.")

    def _load_paper_portfolio(self):
        """DB에서 모의투자 포트폴리오 상태를 로드합니다."""
        try:
            # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
            # 오류 해결: self.db_path 사용
            with sqlite3.connect(self.db_path) as conn:
            # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM paper_portfolio_state WHERE id = 1").fetchone()
                if row:
                    self.state = dict(row)
                    if 'trade_cycle_count' not in self.state or self.state['trade_cycle_count'] is None:
                        self.state['trade_cycle_count'] = 0
                    logger.info(f"DB에서 모의투자 포트폴리오 상태를 로드했습니다. (Cycle: {self.state['trade_cycle_count']})")
                else:
                    self.state = copy.deepcopy({
                        "krw_balance": self.initial_capital, "asset_balance": 0.0, "avg_buy_price": 0.0,
                        "initial_capital": self.initial_capital, "fee_rate": config.FEE_RATE, "roi_percent": 0.0,
                        "highest_price_since_buy": 0.0, "trade_cycle_count": 0
                    })
                    logger.info("저장된 모의 포트폴리오가 없어 초기값으로 시작합니다.")
                    self._save_paper_portfolio()
        except Exception as e:
            logger.error(f"모의 포트폴리오 로드 오류: {e}", exc_info=True)
            self.state = {}

    def _save_paper_portfolio(self):
        """현재 모의투자 포트폴리오 상태를 DB에 저장합니다."""
        try:
            # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
            # 오류 해결: self.db_path 사용
            with sqlite3.connect(self.db_path) as conn:
            # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
                conn.execute('''
                    INSERT INTO paper_portfolio_state (id, krw_balance, asset_balance, avg_buy_price, initial_capital, fee_rate, roi_percent, highest_price_since_buy, trade_cycle_count, last_updated)
                    VALUES (1, :krw_balance, :asset_balance, :avg_buy_price, :initial_capital, :fee_rate, :roi_percent, :highest_price_since_buy, :trade_cycle_count, :last_updated)
                    ON CONFLICT(id) DO UPDATE SET
                    krw_balance=excluded.krw_balance, asset_balance=excluded.asset_balance, avg_buy_price=excluded.avg_buy_price,
                    initial_capital=excluded.initial_capital, fee_rate=excluded.fee_rate, roi_percent=excluded.roi_percent,
                    highest_price_since_buy=excluded.highest_price_since_buy, trade_cycle_count=excluded.trade_cycle_count, last_updated=excluded.last_updated
                ''', {**self.state, 'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
        except Exception as e:
            logger.error(f"모의 포트폴리오 저장 오류: {e}", exc_info=True)

    def _fetch_real_position(self):
        """Upbit API를 통해 실제 계좌 정보를 가져옵니다."""
        if self.upbit_api:
            return self.upbit_api.get_my_position(config.TICKER_TO_TRADE)
        return {}

    def get_current_position(self):
        """현재 포트폴리오/계좌 상태를 반환합니다."""
        if self.mode == 'real':
            self.state = self._fetch_real_position() # 항상 최신 정보로 갱신
        return self.state

    def update_portfolio_on_trade(self, trade_result):
        """모의 투자 시, 거래 결과를 바탕으로 포트폴리오 상태를 업데이트합니다."""
        if self.mode != 'simulation' or not trade_result:
            return

        action = trade_result['action']
        price = trade_result['price']
        amount = trade_result['amount']
        krw_value = trade_result['krw_value']
        fee = trade_result.get('fee', 0)

        if action == 'buy':
            self.state['krw_balance'] -= krw_value
            new_total_cost = (self.state['avg_buy_price'] * self.state['asset_balance']) + (krw_value - fee)
            self.state['asset_balance'] += amount
            self.state['avg_buy_price'] = new_total_cost / self.state['asset_balance'] if self.state[
                                                                                              'asset_balance'] > 0 else 0
            self.state['highest_price_since_buy'] = price
        elif action == 'sell':
            self.state['asset_balance'] -= amount
            self.state['krw_balance'] += (krw_value - fee)
            if self.state['asset_balance'] < 1e-8:  # 사실상 0
                self.state['avg_buy_price'] = 0
                self.state['highest_price_since_buy'] = 0

        self.calculate_and_log_roi()  # 수익률 다시 계산
        self._save_paper_portfolio()  # DB에 저장

    def log_trade(self, log_entry: dict, is_real_trade: bool):
        """거래 기록을 DB에 저장합니다."""
        table = 'real_trade_log' if is_real_trade else 'paper_trade_log'
        try:
            with sqlite3.connect(self.db_path) as conn:
                if is_real_trade:
                    conn.execute('''
                        INSERT INTO real_trade_log (timestamp, action, ticker, upbit_uuid, price, amount, krw_value, reason, context, upbit_response)
                        VALUES (:timestamp, :action, :ticker, :upbit_uuid, :price, :amount, :krw_value, :reason, :context, :upbit_response)''',
                                 log_entry)
                else: # 모의 거래
                    # 'hold' 결정도 paper_trade_log에 기록
                    conn.execute('''
                        INSERT INTO paper_trade_log (timestamp, action, price, amount, krw_value, fee, context)
                        VALUES (:timestamp, :action, :price, :amount, :krw_value, :fee, :context)''',
                                 log_entry)
            logger.info(f"✅ [{table}] 테이블에 거래 로그를 성공적으로 저장했습니다.")
        except Exception as e:
            logger.error(f"❌ [{table}] 테이블에 로그 저장 중 오류 발생: {e}", exc_info=True)

    def calculate_and_log_roi(self):
        """수익률을 계산하고 로그로 출력합니다."""
        # config 파일에서 거래 대상 티커를 가져옵니다.
        ticker = config.TICKER_TO_TRADE

        current_price = pyupbit.get_current_price(ticker)
        if not current_price:
            logger.warning(f"'{ticker}'의 현재가를 조회할 수 없어 수익률 계산을 건너뜁니다.")
            return

        # 모의 투자 포트폴리오 상태를 사용합니다.
        portfolio = self.state

        asset_value = portfolio.get('asset_balance', 0) * current_price
        total_value = portfolio.get('krw_balance', 0) + asset_value
        initial_capital = portfolio.get('initial_capital', 1)

        pnl = total_value - initial_capital
        roi = (pnl / initial_capital) * 100 if initial_capital > 0 else 0

        # 계산된 수익률을 포트폴리오 상태에 업데이트
        self.state['roi_percent'] = roi

        logger.info("--- 모의투자 포트폴리오 현황 ---")
        logger.info(
            f"KRW 잔고: {portfolio.get('krw_balance', 0):,.0f} | "
            f"보유자산: {portfolio.get('asset_balance', 0):.4f} {ticker.split('-')[1]}"
        )
        logger.info(
            f"현재 총 가치: {total_value:,.0f} KRW | 총 손익: {pnl:,.0f} KRW | 수익률: {roi:.2f}%"
        )