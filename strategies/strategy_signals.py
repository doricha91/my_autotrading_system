# strategies/strategy_signals.py
# 이 파일은 모든 개별 전략의 '매수' 신호와, 모든 자산에 공통으로 적용될 '매도' 신호를
# 중앙에서 관리하여 백테스터와 실시간 트레이더가 동일한 로직을 공유하게 합니다.

import pandas as pd
import numpy as np
# ✨ 1. [핵심 수정] 모든 전략 함수가 모여있는 'core.strategy'를 임포트합니다.
from core import strategy


# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +                          매수 신호 생성 함수                          +
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

def get_buy_signal(data: pd.DataFrame, strategy_name: str, params: dict) -> bool:
    """
    [수정 완료] 주어진 데이터와 전략에 따라 최종 매수 신호를 판단합니다.
    - 이제 이 함수는 직접 로직을 계산하지 않고, 'core.strategy'의 중앙 함수를 호출합니다.
    - 이를 통해 모든 전략(신규 하이브리드 전략 포함)을 별도의 수정 없이 바로 사용할 수 있습니다.
    """
    try:
        # 1. 'core.strategy'에서 전략 이름에 맞는 실제 함수를 가져옵니다.
        strategy_func = strategy.get_strategy_function(strategy_name)

        # 2. 해당 전략 함수를 실행하여 신호가 포함된 DataFrame을 받습니다.
        #    이것은 실시간 트레이더가 신호를 생성하는 방식과 100% 동일합니다.
        df_with_signal = strategy_func(data.copy(), params)

        # 3. 생성된 신호의 가장 마지막 값이 1(매수)인지 확인하여 결과를 반환합니다.
        #    이렇게 하면 백테스터가 최신 데이터를 기준으로 매수 여부를 결정할 수 있습니다.
        latest_signal = df_with_signal['signal'].iloc[-1]

        return latest_signal > 0  # 신호가 1이면 True, 아니면 False

    except Exception as e:
        # 오류 발생 시 매수하지 않도록 False를 반환합니다.
        print(f"매수 신호 생성 중 오류 발생: {e}")
        return False


# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +                          매도 신호 생성 함수                          +
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

def get_sell_signal(data: pd.DataFrame, position: dict, exit_params: dict, strategy_name: str,
                    strategy_params: dict) -> (bool, str):
    """
    [수정 없음] 보유 포지션에 대해 모든 매도 조건을 종합적으로 판단합니다.
    - 이 함수는 특정 매수 전략에 종속되지 않고, 공통 청산 규칙(손절, 트레일링 스탑)과
      전략별 기본 청산 로직을 따르므로 기존 코드를 그대로 유지합니다.
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

        # --- [수정] 모든 전략에 공통으로 적용될 청산 로직 ---
        # 이 부분을 수정하여 hybrid_trend_strategy의 청산 로직도 통합 관리합니다.
        else:
            # 1. ATR 기반 손절
            atr_multiplier = exit_params.get('stop_loss_atr_multiplier')
            if atr_multiplier and 'ATR' in data.columns:
                entry_atr = position.get('entry_atr', latest['ATR'])
                stop_loss_price = entry_price - (entry_atr * atr_multiplier)
                if latest['low'] <= stop_loss_price:
                    return True, f"ATR 손절 (x{atr_multiplier})"

            # 2. 트레일링 스탑
            trailing_stop_pct = exit_params.get('trailing_stop_percent')
            if trailing_stop_pct:
                highest_since_buy = position.get('highest_since_buy', entry_price)
                trailing_price = highest_since_buy * (1 - trailing_stop_pct)
                if latest['low'] <= trailing_price:
                    return True, f"트레일링 스탑 ({trailing_stop_pct * 100}%)"

            # 3. 전략별 기본 청산 신호
            #    - 하이브리드 전략은 내부적으로 trend_following과 ma_trend_continuation의
            #      매도 조건을 모두 사용하므로, trend_following의 청산 규칙을 따릅니다.
            if strategy_name in ['trend_following', 'hybrid_trend_strategy']:
                # trend_following 전략의 매도 조건은 core/strategy.py에 정의된 대로
                # 단기 이평선 하회입니다.
                exit_sma_period = strategy_params.get('exit_sma_period', 10)  # 하이브리드는 trend_following_params 안에 있음
                if strategy_name == 'hybrid_trend_strategy':
                    exit_sma_period = strategy_params.get('trend_following_params', {}).get('exit_sma_period', 10)

                if latest['close'] < latest[f'SMA_{exit_sma_period}']:
                    return True, f"전략 매도 ({exit_sma_period}SMA 이탈)"

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