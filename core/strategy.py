# core/strategy.py
# 🧠 매매 신호를 생성하는 모든 전략 함수들을 관리합니다.

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger()


# --- 개별 전략 함수들 ---
# advanced_backtest.py 에 있던 strategy_* 함수들을 모두 이곳으로 옮깁니다.
def strategy_trend_following(df, params):
    buy_condition = (df['high'] > df[f"high_{params.get('breakout_window')}d"].shift(1)) & \
                    (df['volume'] > df['volume'].rolling(window=params.get('volume_avg_window')).mean().shift(
                        1) * params.get('volume_multiplier'))
    if params.get('long_term_sma_period'): buy_condition &= (
            df['close'] > df[f"SMA_{params.get('long_term_sma_period')}"])
    df['signal'] = np.where(buy_condition, 1, 0)
    return df


def strategy_volatility_breakout(df, params):
    buy_cond = df['high'] > (df['open'] + df['range'] * params.get('k', 0.5))
    if params.get('long_term_sma_period'): buy_cond &= (df['close'] > df[f"SMA_{params.get('long_term_sma_period')}"])
    df['signal'] = np.where(buy_cond, 1, 0)
    return df


def strategy_turtle_trading(df, params):
    base_buy_condition = df['high'] > df[f"high_{params.get('entry_period')}d"].shift(1)
    if params.get('long_term_sma_period'): base_buy_condition &= (
            df['close'] > df[f"SMA_{params.get('long_term_sma_period')}"])
    df['signal'] = np.where(base_buy_condition, 1, 0)
    return df


def strategy_rsi_mean_reversion(df, params):
    rsi_col = f"RSI_{params['rsi_period']}"
    buy_cond = (df[rsi_col] > params['oversold_level']) & (df[rsi_col].shift(1) <= params['oversold_level'])
    if params.get('long_term_sma_period'): buy_cond &= (df['close'] > df[f"SMA_{params['long_term_sma_period']}"])
    sell_cond = (df[rsi_col] < params['overbought_level']) & (df[rsi_col].shift(1) >= params['overbought_level'])
    df['signal'] = np.where(buy_cond, 1, np.where(sell_cond, -1, 0))
    return df


# --- 전략 실행기 ---
def generate_signals(df, params):
    """전략 이름에 맞는 함수를 호출하여 매매 신호(signal)를 생성합니다."""
    strategy_name = params.get('strategy_name')

    strategy_functions = {
        'trend_following': strategy_trend_following,
        'volatility_breakout': strategy_volatility_breakout,
        'turtle_trading': strategy_turtle_trading,
        'rsi_mean_reversion': strategy_rsi_mean_reversion,
        # 새로운 전략 추가 시 여기에 등록
    }

    if strategy_name in strategy_functions:
        return strategy_functions[strategy_name](df, params)
    else:
        raise ValueError(f"'{strategy_name}'은(는) 알 수 없는 전략입니다.")


def get_ensemble_strategy_signal(df, config):
    """앙상블 전략의 최종 신호와 점수를 계산합니다."""
    final_score = 0.0
    strategy_functions = {'rsi_mean_reversion': strategy_rsi_mean_reversion,
                          'volatility_breakout': strategy_volatility_breakout,
                          'turtle_trading': strategy_turtle_trading,
                          'trend_following': strategy_trend_following}
    logging.info("--- 앙상블 전략 점수 계산 시작 ---")
    for strategy_config in config['strategies']:
        name, weight, params = strategy_config['name'], strategy_config['weight'], strategy_config['params']
        if name in strategy_functions:
            df_signal = strategy_functions[name](df.copy(), params)
            signal_val = df_signal['signal'].iloc[-1]
            score = signal_val * weight
            final_score += score
            logging.info(f" - 전략: {name:<20} | 신호: {signal_val:<3} | 가중치: {weight:<4} | 점수: {score:+.2f}")
    logging.info(f"--- 최종 합산 점수: {final_score:.2f} ---")
    if final_score >= config['buy_threshold']: return 'buy', final_score
    if final_score <= config['sell_threshold']: return 'sell', final_score
    return 'hold', final_score