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

    def get_total_portfolio_value(self, all_data: dict, current_date: pd.Timestamp) -> float:
        """
        ✨[신규 함수]✨ 특정 날짜의 총 자산 가치(현금 + 모든 보유 자산의 평가 가치)를 계산합니다.
        """
        asset_value = 0.0
        for ticker, position in self.positions.items():
            # 해당 날짜에 코인 가격 데이터가 있는지 확인
            if current_date in all_data[ticker].index:
                current_price = all_data[ticker].loc[current_date, 'close']
                asset_value += position['size'] * current_price
            else:
                # 데이터가 없는 날(주말 등)은 가장 마지막에 알려진 가격(진입가)으로 평가
                asset_value += position['size'] * position['entry_price']
        return self.capital + asset_value

    def execute_buy(self, ticker: str, price: float, trade_date: pd.Timestamp,
                    strategy_info: dict, entry_atr: float, all_data: dict):
        """
        ✨[업데이트됨]✨ 터틀 전략의 ATR 기반 포지션 사이징을 완벽하게 구현합니다.
        이제 이 함수가 직접 all_data를 인자로 받아 총 자산을 계산합니다.
        """
        strategy_name = strategy_info.get('strategy')
        strategy_params = strategy_info.get('params', {})

        investment_amount = 0

        # --- 터틀 전략 포지션 사이징 로직 ---
        if strategy_name == 'turtle':
            risk_percent = strategy_params.get('risk_per_trade_percent', 1.0)
            atr_multiplier = strategy_params.get('stop_loss_atr_multiplier', 2.0)

            if entry_atr <= 0: return  # ATR이 0이면 리스크 계산 불가

            # 1. ✨ 현재 총 자산 가치를 직접 계산합니다.
            total_value = self.get_total_portfolio_value(all_data, trade_date)
            risk_amount_per_trade = total_value * (risk_percent / 100.0)

            # 2. 1 단위(unit) 당 리스크(달러 가치) 계산
            dollar_per_risk_unit = entry_atr * atr_multiplier

            # 3. 매수할 수량(size)에 따른 투자금 계산
            size = risk_amount_per_trade / dollar_per_risk_unit if dollar_per_risk_unit > 0 else 0
            investment_amount = size * price

        # --- 다른 전략의 포지션 사이징 로직 ---
        else:
            investment_amount = self.capital / (config.MAX_CONCURRENT_TRADES - len(self.positions) + 1)

        # --- 공통 실행 로직 ---
        if self.capital < investment_amount or investment_amount < config.MIN_ORDER_KRW: return

        fee = investment_amount * config.FEE_RATE
        final_size = (investment_amount - fee) / price if price > 0 else 0
        self.capital -= investment_amount

        self.positions[ticker] = {
            'entry_price': price, 'size': final_size, 'entry_date': trade_date,
            'initial_investment': investment_amount, 'highest_since_buy': price,
            'entry_atr': entry_atr, **strategy_info
        }

        self.trade_log.append({
            'ticker': ticker, 'action': 'buy', 'timestamp': trade_date,
            'price': price, 'size': final_size, 'value': investment_amount,
            'fee': fee, 'profit': 0, 'reason': 'entry'
        })

    # execute_buy 함수에서 all_data를 사용하기 위해 임시 저장
    def set_temp_data(self, all_data):
        self.temp_all_data = all_data

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