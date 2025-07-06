# core/portfolio.py
# 💼 모의투자 및 실제투자 포트폴리오의 상태를 관리하고 DB와 연동합니다.

import sqlite3
import logging
import copy
from datetime import datetime
from typing import Dict, Any, Optional

import pyupbit
import config

logger = logging.getLogger()


class DatabaseManager:
    """
    데이터베이스 연결 및 거래/포트폴리오 상태 로깅을 담당하는 클래스.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._setup_database()

    def _setup_database(self):
        """거래 로그 및 포트폴리오 상태 저장을 위한 DB 테이블을 생성하고, 필요시 스키마를 업데이트합니다."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # --- ✨ 핵심 수정: 실행 순서 변경 ✨ ---
                # 1. 먼저 모든 테이블이 존재하는지 확인하고 없으면 '최신 스키마'로 생성합니다.
                #    이렇게 하면 처음 실행 시 항상 올바른 테이블 구조를 갖게 됩니다.
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS paper_portfolio_state (
                        id INTEGER PRIMARY KEY, 
                        ticker TEXT UNIQUE,
                        krw_balance REAL, 
                        asset_balance REAL, 
                        avg_buy_price REAL,
                        initial_capital REAL, 
                        fee_rate REAL, 
                        roi_percent REAL, 
                        highest_price_since_buy REAL,
                        last_updated TEXT, 
                        trade_cycle_count INTEGER DEFAULT 0
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS paper_trade_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        timestamp TEXT, 
                        ticker TEXT,
                        action TEXT, 
                        price REAL, 
                        amount REAL, 
                        krw_value REAL, 
                        fee REAL, 
                        context TEXT
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS real_trade_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        timestamp TEXT, 
                        action TEXT, 
                        ticker TEXT, 
                        upbit_uuid TEXT UNIQUE, 
                        price REAL, 
                        amount REAL, 
                        krw_value REAL, 
                        reason TEXT, 
                        context TEXT, 
                        upbit_response TEXT
                    )
                ''')

                # 2. [호환성 유지] 테이블이 확실히 존재한 후에, 구 버전 DB를 위해 'ticker' 컬럼이 있는지 확인하고 없으면 추가합니다.
                #    이 로직은 구 버전 DB 파일을 가지고 있는 경우에만 동작합니다.
                try:
                    cursor.execute("SELECT ticker FROM paper_portfolio_state LIMIT 1")
                except sqlite3.OperationalError as e:
                    # 'no such column' 에러는 컬럼이 없다는 의미이므로, 추가 작업을 진행합니다.
                    if "no such column" in str(e):
                        logger.info("기존 'paper_portfolio_state' 테이블에 'ticker' 컬럼을 추가합니다.")
                        cursor.execute("ALTER TABLE paper_portfolio_state ADD COLUMN ticker TEXT UNIQUE")
                    else:
                        # 다른 DB 에러는 그대로 다시 발생시킵니다.
                        raise e

                logger.info(f"✅ '{self.db_path}' 데이터베이스가 성공적으로 준비되었습니다.")

        except sqlite3.Error as e:
            logger.error(f"❌ 데이터베이스 설정 중 오류 발생: {e}", exc_info=True)
            raise

    def load_paper_portfolio_state(self, ticker: str) -> Optional[Dict[str, Any]]:
        """DB에서 특정 티커의 모의투자 포트폴리오 상태를 로드합니다."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM paper_portfolio_state WHERE ticker = ?", (ticker,))
                row = cursor.fetchone()
                if row:
                    state = dict(row)
                    if 'trade_cycle_count' not in state or state['trade_cycle_count'] is None:
                        state['trade_cycle_count'] = 0
                    return state
                return None
        except sqlite3.Error as e:
            logger.error(f"❌ 모의 포트폴리오 '{ticker}' 로드 오류: {e}", exc_info=True)
            return None

    def save_paper_portfolio_state(self, state: Dict[str, Any]):
        """현재 모의투자 포트폴리오 상태를 DB에 저장하거나 업데이트합니다."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                state['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                conn.execute('''
                    INSERT INTO paper_portfolio_state (
                        ticker, krw_balance, asset_balance, avg_buy_price, initial_capital, 
                        fee_rate, roi_percent, highest_price_since_buy, trade_cycle_count, last_updated
                    ) VALUES (
                        :ticker, :krw_balance, :asset_balance, :avg_buy_price, :initial_capital, 
                        :fee_rate, :roi_percent, :highest_price_since_buy, :trade_cycle_count, :last_updated
                    ) ON CONFLICT(ticker) DO UPDATE SET
                        krw_balance=excluded.krw_balance, 
                        asset_balance=excluded.asset_balance, 
                        avg_buy_price=excluded.avg_buy_price,
                        initial_capital=excluded.initial_capital, 
                        fee_rate=excluded.fee_rate, 
                        roi_percent=excluded.roi_percent,
                        highest_price_since_buy=excluded.highest_price_since_buy, 
                        trade_cycle_count=excluded.trade_cycle_count, 
                        last_updated=excluded.last_updated
                ''', state)
        except sqlite3.Error as e:
            logger.error(f"❌ 모의 포트폴리오 저장 오류: {e}", exc_info=True)

    def log_trade(self, log_entry: dict, is_real_trade: bool):
        """거래 기록을 DB에 저장합니다."""
        table = 'real_trade_log' if is_real_trade else 'paper_trade_log'
        try:
            with sqlite3.connect(self.db_path) as conn:
                if is_real_trade:
                    conn.execute('''
                        INSERT INTO real_trade_log (timestamp, action, ticker, upbit_uuid, price, amount, krw_value, reason, context, upbit_response)
                        VALUES (:timestamp, :action, :ticker, :upbit_uuid, :price, :amount, :krw_value, :reason, :context, :upbit_response)
                    ''', log_entry)
                else:
                    conn.execute('''
                        INSERT INTO paper_trade_log (timestamp, ticker, action, price, amount, krw_value, fee, context)
                        VALUES (:timestamp, :ticker, :action, :price, :amount, :krw_value, :fee, :context)
                    ''', log_entry)
            logger.info(f"✅ [{table}] 테이블에 거래 로그를 성공적으로 저장했습니다.")
        except sqlite3.Error as e:
            logger.error(f"❌ [{table}] 테이블에 로그 저장 중 오류 발생: {e}", exc_info=True)


class PortfolioManager:
    """
    모의투자 및 실제투자 포트폴리오를 관리합니다.
    """

    def __init__(self, mode: str, ticker: str, upbit_api_client=None, initial_capital=10000000.0):
        self.mode = mode
        self.ticker = ticker
        self.upbit_api = upbit_api_client
        self.initial_capital = initial_capital

        self.db_manager = DatabaseManager(config.LOG_DB_PATH)

        self.state: Dict[str, Any] = {}
        self._initialize_portfolio()

    def _initialize_portfolio(self):
        """운용 모드에 따라 포트폴리오를 초기화합니다."""
        if self.mode == 'simulation':
            self._load_or_create_paper_portfolio()
        else:
            self.state = self._fetch_real_position() if self.upbit_api else {}

    def _load_or_create_paper_portfolio(self):
        """DB에서 모의투자 포트폴리오를 로드하거나, 없으면 새로 생성합니다."""
        loaded_state = self.db_manager.load_paper_portfolio_state(self.ticker)
        if loaded_state:
            self.state = loaded_state
            logger.info(f"DB에서 '{self.ticker}' 모의투자 포트폴리오를 로드했습니다. (Cycle: {self.state.get('trade_cycle_count', 0)})")
        else:
            self.state = {
                "ticker": self.ticker,
                "krw_balance": self.initial_capital,
                "asset_balance": 0.0,
                "avg_buy_price": 0.0,
                "initial_capital": self.initial_capital,
                "fee_rate": config.FEE_RATE,
                "roi_percent": 0.0,
                "highest_price_since_buy": 0.0,
                "trade_cycle_count": 0
            }
            logger.info(f"저장된 '{self.ticker}' 모의 포트폴리오가 없어 초기값으로 시작합니다.")
            self.db_manager.save_paper_portfolio_state(self.state)

    def _fetch_real_position(self) -> Dict[str, Any]:
        """Upbit API를 통해 실제 계좌 정보를 가져옵니다."""
        if self.upbit_api:
            try:
                return self.upbit_api.get_my_position(self.ticker)
            except Exception as e:
                logger.error(f"❌ Upbit 계좌 정보 조회 중 오류 발생: {e}", exc_info=True)
        return {}

    def get_current_position(self) -> Dict[str, Any]:
        """현재 포트폴리오/계좌 상태를 반환합니다."""
        if self.mode == 'real':
            self.state = self._fetch_real_position()
        return self.state

    def update_portfolio_on_trade(self, trade_result: Dict[str, Any]):
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
            if self.state['asset_balance'] > 1e-9:
                self.state['avg_buy_price'] = new_total_cost / self.state['asset_balance']
            else:
                self.state['avg_buy_price'] = 0
            self.state['highest_price_since_buy'] = price

        elif action == 'sell':
            self.state['krw_balance'] += (krw_value - fee)
            self.state['asset_balance'] -= amount

            if self.state['asset_balance'] < 1e-9:
                self.state['asset_balance'] = 0.0
                self.state['avg_buy_price'] = 0.0
                self.state['highest_price_since_buy'] = 0.0
                self.state['trade_cycle_count'] += 1
                logger.info(f"🎉 매매 사이클 완료! 새로운 사이클 시작 (총: {self.state['trade_cycle_count']}회)")

        self.update_and_save_state()

    def update_and_save_state(self, current_price: Optional[float] = None):
        """포트폴리오의 현재 가치와 수익률을 계산하고 DB에 저장합니다."""
        if self.mode != 'simulation':
            return

        if current_price is None:
            current_price = pyupbit.get_current_price(self.ticker)

        if current_price:
            self._calculate_roi(current_price)
        else:
            logger.warning(f"'{self.ticker}'의 현재가를 조회할 수 없어 수익률 계산을 건너뜁니다.")

        self.db_manager.save_paper_portfolio_state(self.state)

    def _calculate_roi(self, current_price: float):
        """수익률(ROI)을 계산하여 포트폴리오 상태에 업데이트합니다."""
        asset_value = self.state.get('asset_balance', 0) * current_price
        total_value = self.state.get('krw_balance', 0) + asset_value
        initial_capital = self.state.get('initial_capital', 1)

        if initial_capital > 0:
            pnl = total_value - initial_capital
            roi = (pnl / initial_capital) * 100
        else:
            pnl = 0
            roi = 0

        self.state['roi_percent'] = roi

        logger.info(
            f"--- 모의투자 현황 ({self.ticker}) --- | "
            f"KRW: {self.state.get('krw_balance', 0):,.0f} | "
            f"보유수량: {self.state.get('asset_balance', 0):.4f} | "
            f"총 가치: {total_value:,.0f} KRW | "
            f"총 손익: {pnl:,.0f} KRW | "
            f"수익률: {roi:.2f}%"
        )

    def log_trade(self, log_entry: dict):
        """
        거래 기록을 DB에 저장합니다.
        """
        log_entry_with_ticker = copy.deepcopy(log_entry)
        log_entry_with_ticker['ticker'] = self.ticker

        is_real = self.mode == 'real'
        self.db_manager.log_trade(log_entry_with_ticker, is_real_trade=is_real)