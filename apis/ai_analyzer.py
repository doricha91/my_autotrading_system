# apis/ai_analyzer.py
# 🤖 OpenAI API와 통신하여 시장 데이터를 분석하고 투자 결정을 내립니다.
# 회고 분석 기능 또한 이 파일에 포함됩니다.

import openai
import json
import logging
import sqlite3
import pyupbit
import pandas as pd
from datetime import datetime

import config

logger = logging.getLogger()


def get_ai_trading_decision(ticker: str, df_recent: pd.DataFrame, ensemble_signal: str, ensemble_score: float) -> dict:
    """
    최신 시장 데이터와 앙상블 신호를 기반으로 AI에게 최종 투자 판단을 요청합니다.
    """
    if not config.OPENAI_API_KEY:
        logger.warning("OpenAI API 키가 설정되지 않았습니다. AI 분석을 건너뛰고 앙상블 신호를 그대로 사용합니다.")
        if ensemble_signal == 'buy':
            return {'decision': 'buy', 'percentage': 0.5, 'reason': 'Ensemble signal only (AI skip).'}
        elif ensemble_signal == 'sell':
            return {'decision': 'sell', 'percentage': 1.0, 'reason': 'Ensemble signal only (AI skip).'}
        else:
            return {'decision': 'hold', 'percentage': 0.0, 'reason': 'Ensemble signal only (AI skip).'}

    client = openai.OpenAI(api_key=config.OPENAI_API_KEY)

    cols_to_send = [
        'open', 'high', 'low', 'close', 'volume', 'fng_value', 'BBU_20_2.0',
        'BBL_20_2.0', 'ATRr_14', 'OBV', 'market_index_value', 'nasdaq_close',
        'dxy_close', 'us_interest_rate'
    ]
    existing_cols = [col for col in cols_to_send if col in df_recent.columns]
    recent_data_json = df_recent[existing_cols].to_json(orient='records', date_format='iso', indent=2)

    prompt = f"""
You are an expert crypto analyst for {ticker}. Your task is to make a final trading decision by holistically analyzing a pre-calculated strategy signal and a rich set of recent market data.

1.  **Pre-calculated Ensemble Signal**: The initial signal is '{ensemble_signal.upper()}' with a confidence score of {ensemble_score:.2f}. This is a primary reference.
2.  **Recent Market Data (Time-Series in JSON)**: Here is the detailed data for the last 30 periods.
    ```json
    {recent_data_json}
    ```

**Analysis and Decision Guidelines:**
- Synthesize all data. How does the macro environment support or contradict the crypto market situation?
- Confirm with technicals. If the signal is 'buy', is it supported by increasing volume (`OBV` trend)?
- Use the Ensemble Signal Wisely. If the Ensemble Signal is 'BUY' but macro indicators are flashing warnings, you should be cautious.

Your final decision MUST be in JSON format with three keys: 'decision' ('buy', 'sell', or 'hold'), 'percentage' (a float from 0.0 to 1.0 for trade size), and 'reason' (a concise, data-driven explanation). For 'hold', the percentage must be 0.0.
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        ai_decision_data = json.loads(response.choices[0].message.content)
        logger.info(f"✅ OpenAI 응답 수신: {ai_decision_data}")

        if not all(k in ai_decision_data for k in ['decision', 'percentage', 'reason']):
            raise ValueError("AI 응답에 필수 키가 누락되었습니다.")
        return ai_decision_data
    except Exception as e:
        logger.error(f"❌ OpenAI API 호출 또는 응답 처리 중 오류: {e}")
        return {'decision': 'hold', 'percentage': 0.0, 'reason': 'AI analysis failed due to an error.'}


# --- 회고 분석 관련 함수들 ---

def _get_future_price_data(ticker: str, interval: str, start_datetime_str: str, count: int) -> pd.DataFrame:
    """거래 후 가격 추이 확인을 위한 헬퍼 함수"""
    try:
        # 현재 시점에서 과거 데이터를 충분히 가져와 필터링
        df = pyupbit.get_ohlcv(ticker, interval=interval, count=200)
        if df is None or df.empty: return pd.DataFrame()

        start_dt = pd.to_datetime(start_datetime_str)

        if df.index.tz is not None: df.index = df.index.tz_localize(None)
        if start_dt.tz is not None: start_dt = start_dt.tz_localize(None)

        future_data = df[df.index > start_dt].head(count)
        return future_data
    except Exception as e:
        logger.error(f"미래 가격 데이터 조회 중 오류: {e}")
        return pd.DataFrame()


def _evaluate_trade_outcome(log_entry: dict) -> dict:
    """단일 거래 기록의 성과를 평가합니다."""
    action = log_entry.get('action')
    trade_price = log_entry.get('price')
    trade_timestamp = log_entry.get('timestamp')
    outcome = {"evaluation": "neutral", "details": "N/A"}

    if not all([action, trade_price, trade_timestamp]) or action == 'hold':
        return outcome

    future_data_df = _get_future_price_data(
        ticker=log_entry.get('ticker', config.TICKER_TO_TRADE),
        interval=config.TRADE_INTERVAL,
        start_datetime_str=trade_timestamp,
        count=12  # 12개 캔들(12시간 또는 12일) 동안의 추이 확인
    )

    if future_data_df.empty:
        outcome["details"] = "미래 가격 데이터 조회 불가"
        return outcome

    highest_price_after = future_data_df['high'].max()
    lowest_price_after = future_data_df['low'].min()

    if action == 'buy':
        price_change_vs_high = ((highest_price_after - trade_price) / trade_price) * 100
        if price_change_vs_high > 5:
            outcome["evaluation"] = "good_buy"
            outcome["details"] = f"매수 후 +{price_change_vs_high:.2f}% 까지 상승."
        elif ((lowest_price_after - trade_price) / trade_price) * 100 < -3:
            outcome["evaluation"] = "bad_buy"
            outcome["details"] = "매수 후 3% 이상 하락."
    elif action == 'sell':
        price_change_vs_low = ((lowest_price_after - trade_price) / trade_price) * 100
        if price_change_vs_low < -3:
            outcome["evaluation"] = "good_sell"
            outcome["details"] = f"매도 후 {price_change_vs_low:.2f}% 까지 추가 하락 (손실 회피)."

    return outcome


def perform_retrospective_analysis(openai_client, portfolio_manager):
    """
    과거 거래 기록을 바탕으로 AI에게 회고 분석을 요청합니다.
    """
    logger.info("--- 회고 분석을 시작합니다 ---")

    # 현재 실행 모드에 따라 분석할 테이블과 ROI를 가져옵니다.
    is_real_mode = (config.RUN_MODE == 'real')
    table = 'real_trade_log' if is_real_mode else 'paper_trade_log'

    try:
        # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
        # 오류 해결: get_current_roi() 대신 state에서 직접 ROI 값을 가져옵니다.
        # 실제 투자 모드일 경우, ROI 계산을 위해 upbit_api 클라이언트가 필요하지만
        # 이 함수에서는 단순화를 위해 모의 투자와 동일하게 마지막 기록된 상태를 기준으로 합니다.
        # 더 정확한 실시간 ROI는 별도 함수로 구현이 필요합니다.
        current_roi = portfolio_manager.state.get('roi_percent', 0.0)
        # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

        with sqlite3.connect(config.LOG_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            # 'hold'를 포함한 모든 최근 결정 20개를 가져옵니다.
            recent_decisions = conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 20").fetchall()

        if not recent_decisions:
            logger.info("분석할 최근 거래 기록이 없습니다.")
            return

        # 각 거래 기록을 평가하여 'good', 'bad' 등으로 분류합니다.
        evaluated_decisions = []
        for d in recent_decisions:
            log_dict = dict(d)
            outcome = _evaluate_trade_outcome(log_dict)
            evaluated_decisions.append({"decision": log_dict, "outcome": outcome})

        # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
        # 오류 해결: 미완성된 프롬프트를 완성합니다.
        # JSON 데이터를 프롬프트 문자열에 포함시키기 위해 f-string을 올바르게 사용합니다.
        prompt = f"""
    You are a trading performance coach. Analyze the following recent trading decisions for the {config.TICKER_TO_TRADE} bot.
    The bot operates in '{config.RUN_MODE}' mode, and the current portfolio ROI is {current_roi:.2f}%.

    Recent Decisions & Short-term Outcomes:
        ```json
        {json.dumps(evaluated_decisions, indent=2, default=str)}
        ```
    Based on this data, provide a concise analysis in Korean:
    1. Success Patterns: 'good_buy' 또는 'good_sell' 결정들의 공통적인 특징은 무엇이었는가? (예: "성공적인 매수는 주로 F&G 지수가 낮고 시장 지수가 상승 추세일 때 발생했습니다.")
    2. Failure Patterns: 'bad_buy' 또는 'bad_sell' 결정들의 공통적인 특징은 무엇이었는가? (예: "아쉬운 매도는 거시 경제 지표가 하락 신호를 보낼 때 발생했습니다.")
    3. Actionable Recommendations: 1-2가지의 구체적이고 실행 가능한 전략 개선 방안을 제안하라. (예: "시장 변동성(ATRr_14)이 특정 값 이상일 때는 매수 비율을 줄이는 것을 고려하십시오.")
    """
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        reflection = response.choices[0].message.content
        logger.info("\n\n--- 💡 AI 회고 분석 결과 💡 ---\n" + reflection + "\n---------------------------------")
    except Exception as e:
        logger.error(f"회고 분석 중 오류 발생: {e}", exc_info=True)