# core/scanner_portfolio.py
# '다수 코인 스캐너' 백테스팅을 위한 전용 포트폴리오 관리자입니다.
# 여러 자산을 동시에 보유하고 상태를 추적하는 기능을 담당합니다.

import pandas as pd
import numpy as np
import config


class ScannerPortfolioManager:
    """
    다수의 자산을 동시에 관리하며 백테스팅을 수행하는 포트폴리오 관리자 클래스.
    메모리 상에서 모든 상태를 관리하고, 최종 결과만 로그로 남깁니다.
    """

    def __init__(self, initial_capital: float):
        """
        초기 자본금과 빈 포트폴리오 상태로 초기화합니다.
        """
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.positions = {}  # {'KRW-BTC': {'entry_price': ..., 'size': ...}, 'KRW-ETH': ...}
        self.trade_log = []
        self.daily_portfolio_log = []

    def execute_buy(self, ticker: str, price: float, trade_date: pd.Timestamp, strategy_info: dict, entry_atr: float):
        """
        매수 주문을 처리하고 포트폴리오 상태를 업데이트합니다.
        """
        # 현재 가용 자본의 일부를 투자 (간단한 자금 관리)
        # 실제 투자에서는 더 정교한 자금 관리 모델을 적용해야 합니다.
        investment_amount_per_trade = self.capital / (config.MAX_CONCURRENT_TRADES - len(self.positions) + 1)

        if self.capital < config.MIN_ORDER_KRW or investment_amount_per_trade < config.MIN_ORDER_KRW:
            return

        fee = investment_amount_per_trade * config.FEE_RATE
        size = (investment_amount_per_trade - fee) / price
        self.capital -= investment_amount_per_trade

        self.positions[ticker] = {
            'entry_price': price,
            'size': size,
            'entry_date': trade_date,
            'initial_investment': investment_amount_per_trade,
            'highest_since_buy': price,
            'entry_atr': entry_atr,
            **strategy_info
        }

        self.trade_log.append({
            'ticker': ticker,
            'action': 'buy',
            'timestamp': trade_date,
            'price': price,
            'size': size,
            'value': investment_amount_per_trade,
            'fee': fee,
            'profit': 0,
            'reason': 'entry'
        })

    def execute_sell(self, ticker: str, price: float, trade_date: pd.Timestamp, reason: str):
        """
        매도 주문을 처리하고 포트폴리오 상태를 업데이트합니다.
        """
        if ticker not in self.positions:
            return

        position = self.positions.pop(ticker)
        sell_value = position['size'] * price
        fee = sell_value * config.FEE_RATE
        profit = (price - position['entry_price']) * position['size'] - fee

        self.capital += (sell_value - fee)

        self.trade_log.append({
            'ticker': ticker,
            'action': 'sell',
            'timestamp': trade_date,
            'price': price,
            'size': position['size'],
            'value': sell_value,
            'fee': fee,
            'profit': profit,
            'reason': reason
        })

    def update_portfolio_value(self, all_data: dict, current_date: pd.Timestamp):
        """
        매일 현재가를 기준으로 포트폴리오의 총 가치를 평가하고 기록합니다.
        이는 MDD(최대 낙폭) 계산에 필수적입니다.
        """
        asset_value = 0
        for ticker, position in self.positions.items():
            # 당일 종가가 없을 경우 (데이터 누락), 어제 종가로 평가
            if current_date in all_data[ticker].index:
                current_price = all_data[ticker].loc[current_date, 'close']
                asset_value += position['size'] * current_price

                # 트레일링 스탑을 위한 최고가 업데이트
                if current_price > position['highest_since_buy']:
                    self.positions[ticker]['highest_since_buy'] = current_price
            else:
                # 데이터가 없는 경우, 포지션 가치를 그대로 유지 (이전 값 사용)
                asset_value += position['size'] * position['entry_price']

        total_value = self.capital + asset_value
        self.daily_portfolio_log.append({
            'timestamp': current_date,
            'total_value': total_value
        })

    def get_open_positions(self) -> list:
        """현재 보유 중인 모든 포지션의 티커 리스트를 반환합니다."""
        return list(self.positions.keys())

    def get_position(self, ticker: str) -> dict:
        """특정 티커의 상세 포지션 정보를 반환합니다."""
        return self.positions.get(ticker, {})

    def get_trade_log_df(self) -> pd.DataFrame:
        """전체 거래 기록을 DataFrame으로 변환하여 반환합니다."""
        return pd.DataFrame(self.trade_log)

    def get_daily_log_df(self) -> pd.DataFrame:
        """일별 포트폴리오 가치 기록을 DataFrame으로 변환하여 반환합니다."""
        return pd.DataFrame(self.daily_portfolio_log)