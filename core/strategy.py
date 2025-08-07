# core/strategy.py
# 🧠 매매 신호를 생성하는 모든 전략 함수들을 관리합니다.

import pandas as pd
import numpy as np
import pandas_ta as ta
import logging

logger = logging.getLogger()


# --- 개별 전략 함수들 ---
def trend_following(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    추세 추종 전략 신호를 생성합니다.
    - 매수: N일 고점 돌파 + 거래량 증가 + 장기 추세 상승
    - 매도: 추세가 꺾이는 신호 (예: 단기 이평선 하회)
    """
    # 파라미터 추출
    breakout_window = params.get('breakout_window', 20)
    volume_avg_window = params.get('volume_avg_window', 20)
    volume_multiplier = params.get('volume_multiplier', 1.5)
    long_term_sma = params.get('long_term_sma_period', 50)
    exit_sma_period = params.get('exit_sma_period', 10)

    # 매수 조건
    buy_cond_breakout = df['high'] > df[f'high_{breakout_window}d'].shift(1)
    buy_cond_volume = df['volume'] > df['volume'].rolling(window=volume_avg_window).mean().shift(1) * volume_multiplier
    buy_cond_trend = df['close'] > df[f'SMA_{long_term_sma}']
    buy_condition = buy_cond_breakout & buy_cond_volume & buy_cond_trend

    # 매도 조건
    sell_condition = df['close'] < df[f'SMA_{exit_sma_period}']

    df['signal'] = np.where(buy_condition, 1, np.where(sell_condition, -1, 0))
    return df


def ma_trend_continuation(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    [전략 2: 달리는 말에 올라타기]
    이동평균선을 이용해 상승 추세가 '지속' 중인지 판단합니다.
    - 매수: 단기 이평선이 장기 이평선 위에 있고 (정배열), 현재가가 단기 이평선 위에 있을 때
    - 매도: 단기 이평선이 장기 이평선 아래로 내려갈 때 (데드 크로스)
    """
    # 파라미터 추출
    short_ma_period = params.get('short_ma', 20)
    long_ma_period = params.get('long_ma', 60)

    # 필요한 보조지표 컬럼 이름 정의
    short_ma_col = f'SMA_{short_ma_period}'
    long_ma_col = f'SMA_{long_ma_period}'

    # 데이터프레임에 해당 이동평균선 컬럼이 있는지 확인 (없으면 계산 불가)
    if short_ma_col not in df.columns or long_ma_col not in df.columns:
        df['signal'] = 0
        return df

    # 매수 조건: 정배열 상태에서 가격이 단기 이평선 위에 위치
    buy_condition = (df[short_ma_col] > df[long_ma_col]) & (df['close'] > df[short_ma_col])
    # 매도 조건: 역배열 상태 (데드 크로스)
    sell_condition = df[short_ma_col] < df[long_ma_col]

    df['signal'] = np.where(buy_condition, 1, np.where(sell_condition, -1, 0))
    return df


def hybrid_trend_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    [하이브리드 전략]
    1차로 '신고가 돌파'를 시도하고, 실패 시 2차로 '이동평균선 추세 지속'을 시도합니다.
    """
    # ✨ [핵심 수정]
    # params 딕셔너리 안에 있는 'params' 키에 접근하여 실제 파라미터 딕셔너리를 가져옵니다.
    actual_params = params.get('params', {})

    # 1. 먼저, 기존의 'trend_following'(신고가 돌파) 전략을 시도합니다.
    df_breakout = trend_following(df.copy(), actual_params.get('trend_following_params', {}))

    # 2. 'ma_trend_continuation' 전략도 별도로 계산합니다.
    df_ma_trend = ma_trend_continuation(df.copy(), actual_params.get('ma_trend_params', {}))

    # 3. 신호를 결합합니다.
    df['signal'] = np.where(df_breakout['signal'] == 1, 1, df_ma_trend['signal'])

    return df

def volatility_breakout(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    변동성 돌파 전략 신호를 생성합니다.
    - 매수: 당일 변동성 돌파 + 장기 추세 상승
    - 매도: 장기 추세가 꺾이면 매도
    """
    # 파라미터 추출
    k = params.get('k', 0.5)
    long_term_sma = params.get('long_term_sma_period', 200)

    # 매수 조건
    buy_cond_breakout = df['high'] > (df['open'] + df['range'].shift(1) * k)
    buy_cond_trend = df['close'] > df[f'SMA_{long_term_sma}']
    buy_condition = buy_cond_breakout & buy_cond_trend

    # 매도 조건
    sell_condition = df['close'] < df[f'SMA_{long_term_sma}']

    df['signal'] = np.where(buy_condition, 1, np.where(sell_condition, -1, 0))
    return df


def turtle_trading(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    터틀 트레이딩 전략 신호를 생성합니다.
    - 매수: N1일 고점 돌파
    - 매도: N2일 저점 돌파
    """
    # 파라미터 추출
    entry_period = params.get('entry_period', 20)
    exit_period = params.get('exit_period', 10)
    long_term_sma = params.get('long_term_sma_period')

    # 매수 조건
    buy_condition = df['high'] > df[f'high_{entry_period}d'].shift(1)
    if long_term_sma:
        buy_condition &= (df['close'] > df[f'SMA_{long_term_sma}'])

    # 매도 조건
    sell_condition = df['low'] < df[f'low_{exit_period}d'].shift(1)

    df['signal'] = np.where(buy_condition, 1, np.where(sell_condition, -1, 0))
    return df


def rsi_mean_reversion(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    (최종 수정) 대탐소실(大貪小失) 볼린저 밴드 채널 전략
    - 매수: BB 하단 터치
    - 매도: BB '상단' 터치 (수익 극대화)
    """
    bb_period = params.get('bb_period', 20)
    bb_std_dev = params.get('bb_std_dev', 2.0)

    lower_band_col = f'BBL_{bb_period}_{bb_std_dev}'
    upper_band_col = f'BBU_{bb_period}_{bb_std_dev}'  # ✨ 중간선(BBM) -> 상단선(BBU)으로 변경

    if upper_band_col not in df.columns:
        df.ta.bbands(length=bb_period, std=bb_std_dev, append=True)

    buy_condition = df['close'] <= df[lower_band_col]
    sell_condition = df['close'] >= df[upper_band_col]

    df['signal'] = np.where(buy_condition, 1, np.where(sell_condition, -1, 0))
    return df


def bb_rsi_mean_reversion(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    [신규 전략] 볼린저 밴드와 RSI를 함께 사용하는 평균 회귀 전략
    - 매수: BB 하단 터치 + RSI 과매도 동시 충족
    - 매도: BB 중간선 터치
    """
    # 파라미터 추출
    bb_period = params.get('bb_period', 20)
    bb_std_dev = params.get('bb_std_dev', 2.0)
    rsi_period = params.get('rsi_period', 14)
    rsi_oversold = params.get('oversold_level', 30)

    # 보조지표 컬럼 이름 정의
    lower_band_col = f'BBL_{bb_period}_{bb_std_dev}'
    middle_band_col = f'BBM_{bb_period}_{bb_std_dev}'  # 중간선(이동평균선)
    rsi_col = f'RSI_{rsi_period}'

    # 보조지표가 없는 경우를 대비해 계산 (pandas-ta가 자동으로 중복 계산 방지)
    df.ta.bbands(length=bb_period, std=bb_std_dev, append=True)
    df.ta.rsi(length=rsi_period, append=True)

    # 매수 조건: 1) 가격이 BB 하단보다 낮고, 2) RSI가 과매도 기준보다 낮을 때
    buy_condition = (df['close'] < df[lower_band_col]) & (df[rsi_col] < rsi_oversold)

    # 매도 조건: 가격이 반등하여 BB 중간선에 닿았을 때
    sell_condition = df['close'] > df[middle_band_col]

    df['signal'] = np.where(buy_condition, 1, np.where(sell_condition, -1, 0))
    return df


# --- 전략 실행기 ---
def get_strategy_function(strategy_name: str):
    """전략 이름(문자열)에 해당하는 실제 전략 함수 객체를 반환합니다."""
    strategies = {
        "trend_following": trend_following,
        "volatility_breakout": volatility_breakout,
        "turtle_trading": turtle_trading,
        "rsi_mean_reversion": rsi_mean_reversion,
        # ✨ 4. 새로운 전략들을 사전에 등록합니다.
        "ma_trend_continuation": ma_trend_continuation,
        "hybrid_trend_strategy": hybrid_trend_strategy,
        "bb_rsi_mean_reversion": bb_rsi_mean_reversion,  # ✨ 신규 전략 등록
    }
    strategy_func = strategies.get(strategy_name)
    if strategy_func is None:
        raise ValueError(f"알 수 없는 전략 이름입니다: {strategy_name}")
    return strategy_func


def clean_signals(signals: pd.DataFrame) -> pd.DataFrame:
    """
    연속적인 신호를 정리하여 포지션 진입/청산 시점만 남깁니다.
    """
    # 현재 포지션 상태를 나타내는 'positions' 컬럼 생성 (1: 매수, -1: 매도, 0: 무포지션)
    signals['positions'] = 0

    # 직전 신호와 현재 신호가 다를 때만 유효한 신호로 간주
    # 이를 위해 먼저 신호가 0이 아닌 경우를 찾아 포지션의 변화를 기록
    last_signal = 0
    for i in range(len(signals)):
        current_signal = signals['signal'].iloc[i]
        if current_signal == 1:  # 매수 신호
            if last_signal != 1:
                signals.loc[signals.index[i], 'positions'] = 1
                last_signal = 1
            else:  # 이미 매수 포지션이면 신호 무시
                signals.loc[signals.index[i], 'signal'] = 0
        elif current_signal == -1:  # 매도 신호
            if last_signal == 1:  # 매수 포지션 상태에서만 매도
                signals.loc[signals.index[i], 'positions'] = 0
                last_signal = 0
            else:  # 매수 포지션이 아니면 매도 신호 무시
                signals.loc[signals.index[i], 'signal'] = 0
        else:  # 홀드 신호
            signals.loc[signals.index[i], 'positions'] = last_signal

    return signals['signal']


def get_ensemble_strategy_signal(df, config):
    """앙상블 전략의 최종 신호와 점수를 계산합니다."""
    final_score = 0.0

    logging.info("--- 앙상블 전략 점수 계산 시작 ---")
    for strategy_config in config['strategies']:
        name, weight, params = strategy_config['name'], strategy_config['weight'], strategy_config['params']
        strategy_func = get_strategy_function(name)

        # 각 전략별 신호 생성
        df_signal = strategy_func(df.copy(), params)
        signal_val = df_signal['signal'].iloc[-1]

        score = signal_val * weight
        final_score += score
        logging.info(f" - 전략: {name:<20} | 신호: {signal_val:<3} | 가중치: {weight:<4} | 점수: {score:+.2f}")

    logging.info(f"--- 최종 합산 점수: {final_score:.2f} ---")

    if final_score >= config['buy_threshold']:
        return 'buy', final_score
    if final_score <= config['sell_threshold']:
        return 'sell', final_score

    return 'hold', final_score


def generate_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    전략 이름에 맞는 함수를 호출하여 최종 매매 신호(signal)를 생성하고 정리합니다.
    """
    strategy_name = params.get('strategy_name')
    strategy_func = get_strategy_function(strategy_name)

    # 1. 기본 신호 생성
    signals = strategy_func(df, params)

    # 2. 신호 정리 (중복 진입/청산 방지)
    # 아래 로직은 백테스터가 상태를 관리하므로, 순수 신호만 생성하는 것이 더 명확할 수 있습니다.
    # 만약 백테스터가 상태 관리를 하지 않는다면 이 로직이 유용합니다.
    # signals['signal'] = clean_signals(signals.copy()) # 필요시 활성화

    return signals

