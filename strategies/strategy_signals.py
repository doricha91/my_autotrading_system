# strategies/strategy_signals.py
# 이 파일은 모든 개별 전략의 '매수' 신호와, 모든 자산에 공통으로 적용될 '매도' 신호를
# 중앙에서 관리하여 백테스터와 실시간 트레이더가 동일한 로직을 공유하게 합니다.

import pandas as pd
import numpy as np


# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +                          매수 신호 생성 함수                          +
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

def get_buy_signal(data: pd.DataFrame, strategy_name: str, params: dict) -> bool:
    """
    주어진 데이터와 전략에 따라 최종 매수 신호를 판단합니다.
    - 각 전략의 매수 조건은 `core/strategy.py`의 로직을 기반으로 구현합니다.

    :param data: 특정 코인의 OHLCV 및 보조지표가 포함된 DataFrame
    :param strategy_name: 실행할 전략의 이름 (예: 'trend_following')
    :param params: 해당 전략에 필요한 파라미터 딕셔너리
    :return: 매수 시점이 맞으면 True, 아니면 False
    """
    try:
        # 마지막 행의 데이터가 최신 데이터입니다.
        latest = data.iloc[-1]

        # --- ✨ 터틀 트레이딩 (Turtle Trading) 전략 추가 ---
        if strategy_name == 'turtle':
            entry_period = params.get('entry_period', 20)
            # N일 신고가 돌파 확인 (어제까지의 고점 기준)
            high_col_name = f'high_{entry_period}d'  # add_technical_indicators 에서 계산된 컬럼
            highest_in_window = data[high_col_name].iloc[-2]  # 어제 날짜의 N일 최고가

            # 현재 고가가 어제의 N일 최고가를 돌파했는지 확인
            return latest['high'] > highest_in_window

        # --- 1. 추세 추종 (Trend Following) 전략 ---
        if strategy_name == 'trend_following':
            window = params.get('breakout_window', 20)
            vol_window = params.get('volume_avg_window', 20)
            vol_multiplier = params.get('volume_multiplier', 1.5)
            sma_period = params.get('long_term_sma_period', 50)

            # N일 신고가 돌파 확인 (어제까지의 고점 기준)
            highest_in_window = data['high'].iloc[-window - 1:-1].max()
            breakout_cond = latest['high'] > highest_in_window

            # 거래량 증가 확인
            volume_cond = latest['volume'] > (data['volume'].iloc[-vol_window - 1:-1].mean() * vol_multiplier)

            # 장기 추세 상승 확인
            trend_cond = latest['close'] > latest[f'SMA_{sma_period}']

            return breakout_cond and volume_cond and trend_cond

        # --- 2. 변동성 돌파 (Volatility Breakout) 전략 ---
        elif strategy_name == 'volatility_breakout':
            k = params.get('k', 0.5)
            sma_period = params.get('long_term_sma_period', 200)

            # 당일 변동성 돌파 (어제의 range 사용)
            target_price = latest['open'] + (data['range'].iloc[-2] * k)
            breakout_cond = latest['high'] > target_price

            # 장기 추세 상승 확인
            trend_cond = latest['close'] > latest[f'SMA_{sma_period}']

            return breakout_cond and trend_cond

        # --- 3. 터틀 트레이딩 (Turtle Trading) 전략 ---
        elif strategy_name == 'turtle_trading':
            entry_period = params.get('entry_period', 20)
            sma_period = params.get('long_term_sma_period')

            # N1일 신고가 돌파
            highest_in_window = data['high'].iloc[-entry_period - 1:-1].max()
            buy_cond = latest['high'] > highest_in_window

            if sma_period:
                buy_cond &= (latest['close'] > latest[f'SMA_{sma_period}'])

            return buy_cond

        # --- 4. 역추세 (RSI Mean Reversion / Bollinger Band) 전략 ---
        elif strategy_name == 'rsi_mean_reversion':
            bb_period = params.get('bb_period', 20)
            bb_std = params.get('bb_std_dev', 2.0)
            lower_band_col = f'BBL_{bb_period}_{bb_std}'

            # 볼린저밴드 하단에 닿거나 뚫었을 때 매수
            return latest['close'] <= latest[lower_band_col]

        else:
            print(f"경고: 알 수 없는 매수 전략 '{strategy_name}'")
            return False

    except Exception as e:
        return False


# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +                          매도 신호 생성 함수                          +
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

def get_sell_signal(data: pd.DataFrame, position: dict, exit_params: dict, strategy_name: str,
                    strategy_params: dict) -> (bool, str):
    """
    보유 포지션에 대해 모든 매도 조건을 종합적으로 판단합니다.
    - 매도 조건의 우선순위는 `backtester/performance.py`의 로직을 따릅니다.
    - 하나의 조건이라도 만족하면 즉시 (True, "사유")를 반환합니다.

    :param data: 특정 코인의 OHLCV 및 보조지표가 포함된 DataFrame
    :param position: 현재 보유 포지션 정보 딕셔너리 (portfolio 객체에서 관리)
    :param exit_params: 공통 청산 규칙 파라미터 (손절, 트레일링 스탑 등)
    :param strategy_name: 진입 시 사용했던 전략 이름
    :param strategy_params: 진입 시 사용했던 전략 파라미터
    :return: (매도 여부, 매도 사유) 튜플
    """
    try:
        latest = data.iloc[-1]
        entry_price = position['entry_price']

        # --- ✨ 터틀 트레이딩 (Turtle Trading) 청산 규칙 ---
        if strategy_name == 'turtle':
            # 1순위: 손절 (Stop-Loss)
            atr_multiplier = strategy_params.get('stop_loss_atr_multiplier', 2.0)
            entry_atr = position.get('entry_atr', 0)
            if entry_atr > 0:
                stop_loss_price = entry_price - (entry_atr * atr_multiplier)
                if latest['low'] <= stop_loss_price:
                    return True, f"터틀 손절 (2N)"

            # 2순위: 이익 실현 (Profit-Taking)
            exit_period = strategy_params.get('exit_period', 10)
            low_col_name = f'low_{exit_period}d'  # add_technical_indicators 에서 계산된 컬럼
            lowest_in_window = data[low_col_name].iloc[-2]  # 어제 날짜의 N일 최저가
            if latest['low'] < lowest_in_window:
                return True, f"터틀 이익실현 ({exit_period}일 저점 이탈)"

        # --- 1순위: 고정 비율 손절 (Fixed Stop-Loss) ---
        else:
            stop_loss_pct = strategy_params.get('stop_loss_percent')
            if stop_loss_pct:
                stop_loss_price = entry_price * (1 - stop_loss_pct)
                if latest['low'] <= stop_loss_price:
                    return True, f"고정 손절 ({stop_loss_pct * 100}%)"

            # --- 2순위: ATR 기반 손절 (ATR Stop-Loss) ---
            atr_multiplier = exit_params.get('stop_loss_atr_multiplier')
            if atr_multiplier and 'ATR' in data.columns:
                entry_atr = position.get('entry_atr', latest['ATR'])
                stop_loss_price = entry_price - (entry_atr * atr_multiplier)
                if latest['low'] <= stop_loss_price:
                    return True, f"ATR 손절 (x{atr_multiplier})"

            # --- 3순위: 트레일링 스탑 (Trailing Stop) ---
            trailing_stop_pct = exit_params.get('trailing_stop_percent')
            if trailing_stop_pct:
                highest_since_buy = position.get('highest_since_buy', entry_price)
                trailing_price = highest_since_buy * (1 - trailing_stop_pct)
                if latest['low'] <= trailing_price:
                    return True, f"트레일링 스탑 ({trailing_stop_pct * 100}%)"

            # --- 4순위: 전략별 매도 신호 ---
            if strategy_name == 'rsi_mean_reversion':
                bb_period = strategy_params.get('bb_period', 20)
                bb_std = strategy_params.get('bb_std_dev', 2.0)
                upper_band_col = f'BBU_{bb_period}_{bb_std}'
                if latest['close'] >= latest[upper_band_col]:
                    return True, "전략 매도 (BB 상단 터치)"
            elif strategy_name == 'turtle_trading':
                exit_period = strategy_params.get('exit_period', 10)
                lowest_in_window = data['low'].iloc[-exit_period - 1:-1].min()
                if latest['low'] < lowest_in_window:
                    return True, f"전략 매도 ({exit_period}일 저점 이탈)"
            elif strategy_name == 'trend_following':
                exit_sma_period = strategy_params.get('exit_sma_period', 10)
                if latest['close'] < latest[f'SMA_{exit_sma_period}']:
                    return True, f"전략 매도 ({exit_sma_period}SMA 이탈)"

    except Exception as e:
        return False, ""

    return False, ""