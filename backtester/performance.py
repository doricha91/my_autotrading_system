# backtester/performance.py
# 📊 백테스팅 결과를 상세히 분석하고 성과 지표를 계산하는 모듈입니다.

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger()


# 기존 run_portfolio_simulation 함수를 지우고 아래 코드를 붙여넣으세요.

def run_portfolio_simulation(
        df_signal: pd.DataFrame,
        initial_capital: float,
        stop_loss_percent: float = None,
        stop_loss_atr_multiplier: float = None,
        trailing_stop_percent: float = None,
        partial_profit_target: float = None,  # 참고: 현재 이 기능은 아래 로직에 구현되어 있지 않습니다.
        partial_profit_ratio: float = None  # 참고: 현재 이 기능은 아래 로직에 구현되어 있지 않습니다.
) -> (pd.DataFrame, pd.DataFrame):
    """
    (최종 완성 버전) 모든 청산 조건을 올바른 우선순위로 처리하는 포트폴리오 시뮬레이터
    """
    df = df_signal.copy()
    balance = initial_capital
    position = 0.0
    avg_price = 0.0
    trailing_stop_anchor_price = 0.0

    portfolio_history = []
    trade_log = []

    for i in range(len(df)):
        current_price = df['close'].iloc[i]
        current_low = df['low'].iloc[i]
        current_high = df['high'].iloc[i]

        # 1. 매수 신호 처리
        if df['signal'].iloc[i] == 1 and position == 0:
            invest_amount = balance
            position = invest_amount / current_price
            balance = 0
            avg_price = current_price
            trailing_stop_anchor_price = current_price  # 트레일링 스탑 기준가 초기화
            trade_log.append(
                {'timestamp': df.index[i], 'type': 'buy', 'price': avg_price, 'amount': position, 'balance': balance})

        # 2. 청산 조건 확인 (포지션 보유 시)
        elif position > 0:
            sell_price = 0.0
            sell_type = ''

            # --- ✨ 모든 청산 로직을 올바른 우선순위로 재정립 ---

            # 1순위: 고정 비율 손절매 (Fixed Stop-Loss)
            if stop_loss_percent:
                fixed_stop_loss_price = avg_price * (1 - stop_loss_percent)
                if current_low <= fixed_stop_loss_price:
                    sell_price = fixed_stop_loss_price
                    sell_type = 'fixed_stop'

            # 2순위: ATR 손절매 (ATR Stop-Loss) - 고정 손절이 발동하지 않았을 때만 확인
            if sell_price == 0 and stop_loss_atr_multiplier and 'ATR' in df.columns and not pd.isna(df['ATR'].iloc[i]):
                atr_stop_loss_price = avg_price - (df['ATR'].iloc[i] * stop_loss_atr_multiplier)
                if current_low <= atr_stop_loss_price:
                    sell_price = atr_stop_loss_price
                    sell_type = 'atr_stop'

            # 3순위: 트레일링 스탑 (Trailing Stop) - 위 손절들이 발동하지 않았을 때만 확인
            if sell_price == 0 and trailing_stop_percent:
                if current_high > trailing_stop_anchor_price:
                    trailing_stop_anchor_price = current_high

                trailing_stop_price = trailing_stop_anchor_price * (1 - trailing_stop_percent)
                if current_low <= trailing_stop_price:
                    sell_price = trailing_stop_price
                    sell_type = 'trailing_stop'

            # 4순위: 전략적 매도 신호 (Signal Sell) - 모든 손절/익절 조건에 해당하지 않을 때
            if sell_price == 0 and df['signal'].iloc[i] == -1:
                sell_price = current_price
                sell_type = 'signal_sell'

            # 최종 매도 실행
            if sell_price > 0:
                balance += position * sell_price
                trade_log.append({'timestamp': df.index[i], 'type': sell_type, 'price': sell_price, 'amount': position,
                                  'balance': balance})
                position = 0
                avg_price = 0

        # 3. 일별 포트폴리오 가치 계산
        if position > 0:
            portfolio_value = position * current_price
        else:
            portfolio_value = balance
        portfolio_history.append({'date': df.index[i], 'portfolio_value': portfolio_value})

    return pd.DataFrame(trade_log), pd.DataFrame(portfolio_history).set_index('date')


def get_round_trip_trades(trade_log_df: pd.DataFrame) -> pd.DataFrame:
    """
    매수-매도 사이클(Round Trip)을 기반으로 개별 거래의 손익(PnL)을 계산합니다.
    부분 익절을 포함한 복잡한 거래 시나리오도 처리합니다.

    Args:
        trade_log_df (pd.DataFrame): 'buy', 'partial_sell', 'sell' 거래 기록

    Returns:
        pd.DataFrame: 각 Round Trip 거래의 손익 정보가 담긴 데이터프레임
    """
    if trade_log_df.empty:
        return pd.DataFrame()

    round_trips = []
    active_buy_info = None  # 현재 진입한 포지션 정보: {'entry_date', 'entry_price', 'amount_remaining'}

    for _, trade in trade_log_df.iterrows():
        if trade['type'] == 'buy':
            # 이전 포지션이 있었다면 종료 처리 (일반적으론 발생하지 않음)
            if active_buy_info:
                logger.warning(f"기존 매수 포지션이 있는 상태에서 새로운 매수 발생: {active_buy_info}")

            active_buy_info = {
                'entry_date': trade['timestamp'],
                'entry_price': trade['price'],
                'amount_remaining': trade['amount']
            }


        elif trade['type'] in ['sell', 'signal_sell', 'stop_loss', 'trailing_stop', 'partial_sell'] and active_buy_info:
            amount_to_sell = trade['amount']

            # 매도 수량이 남은 수량보다 많으면 남은 수량만큼만 매도 처리
            if amount_to_sell > active_buy_info['amount_remaining']:
                amount_to_sell = active_buy_info['amount_remaining']

            pnl = (trade['price'] - active_buy_info['entry_price']) * amount_to_sell
            round_trips.append({'pnl': pnl})

            active_buy_info['amount_remaining'] -= amount_to_sell

            # 남은 수량이 거의 없으면 포지션 완전 종료
            if active_buy_info['amount_remaining'] < 1e-9 or trade['type'] == 'sell':
                active_buy_info = None

    return pd.DataFrame(round_trips)


def analyze_performance(portfolio_history_df: pd.DataFrame, trade_log_df: pd.DataFrame, initial_capital: float,
                        interval: str) -> dict:
    """
    포트폴리오 가치 변화와 거래 로그를 바탕으로 종합적인 성과를 분석합니다.

    Args:
        portfolio_history_df (pd.DataFrame): 시간대별 포트폴리오 가치 기록
        trade_log_df (pd.DataFrame): 모든 거래(buy/sell) 기록
        initial_capital (float): 초기 자본금
        interval (str): 테스트에 사용된 시간 단위 ('day' 또는 'minute60')

    Returns:
        dict: 주요 성과 지표가 담긴 딕셔너리
    """
    if portfolio_history_df.empty:
        logger.warning("포트폴리오 기록이 없어 성과 분석을 할 수 없습니다.")
        return {}

    # 1. 최종 수익률 및 손익 계산
    final_value = portfolio_history_df['portfolio_value'].iloc[-1]
    total_pnl = final_value - initial_capital
    total_roi_pct = (total_pnl / initial_capital) * 100

    # 2. MDD (Maximum Drawdown, 최대 낙폭) 계산
    # 포트폴리오 가치가 전 고점 대비 얼마나 하락했는지를 나타내는 지표. 리스크 관리의 핵심.
    portfolio_history_df['rolling_max'] = portfolio_history_df['portfolio_value'].cummax()
    portfolio_history_df['drawdown'] = portfolio_history_df['portfolio_value'] / portfolio_history_df[
        'rolling_max'] - 1.0
    mdd_pct = portfolio_history_df['drawdown'].min() * 100

    # 3. 위험 조정 수익률 지표 계산
    # 수익률의 변동성을 고려하여 얼마나 안정적으로 수익을 냈는지 평가합니다.
    portfolio_history_df['returns'] = portfolio_history_df['portfolio_value'].pct_change().fillna(0)

    # 시간 단위를 연율화하기 위한 계수 설정
    periods_per_year = 365 if interval == 'day' else 365 * 24

    # 샤프 지수 (Sharpe Ratio): (수익률 - 무위험수익률) / 수익률 표준편차. 높을수록 좋음.
    sharpe_ratio = 0
    if portfolio_history_df['returns'].std() > 0:
        sharpe_ratio = portfolio_history_df['returns'].mean() / portfolio_history_df['returns'].std() * np.sqrt(
            periods_per_year)

    # 캘머 지수 (Calmar Ratio): 연율화 수익률 / MDD. MDD 대비 수익률. 높을수록 좋음.
    annual_return = portfolio_history_df['returns'].mean() * periods_per_year
    calmar_ratio = 0
    if mdd_pct != 0:
        calmar_ratio = (annual_return * 100) / abs(mdd_pct)

    # 4. 거래 기반 지표 계산
    rt_trades_df = get_round_trip_trades(trade_log_df)
    total_trades, win_rate_pct, profit_factor = 0, 0.0, 0.0

    if not rt_trades_df.empty:
        total_trades = len(rt_trades_df)
        wins = rt_trades_df[rt_trades_df['pnl'] > 0]
        losses = rt_trades_df[rt_trades_df['pnl'] <= 0]

        # 승률 (Win Rate)
        win_rate_pct = (len(wins) / total_trades) * 100 if total_trades > 0 else 0

        # 수익 팩터 (Profit Factor): 총수익 / 총손실. 1보다 커야하며, 높을수록 좋음.
        gross_profit = wins['pnl'].sum()
        gross_loss = abs(losses['pnl'].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    # 5. 최종 결과 정리
    performance_summary = {
        'ROI (%)': round(total_roi_pct, 2),
        'MDD (%)': round(mdd_pct, 2),
        'Sharpe': round(sharpe_ratio, 2),
        'Calmar': round(calmar_ratio, 2),
        'Profit Factor': round(profit_factor, 2) if profit_factor != float('inf') else 'inf',
        'Win Rate (%)': round(win_rate_pct, 2),
        'Total Trades': total_trades,
    }

    logger.info(f"성과 분석 결과: {performance_summary}")
    return performance_summary