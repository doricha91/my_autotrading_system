# apis/ai_analyzer.py
# 🤖 OpenAI API와 통신하여 시장 데이터를 분석하고 투자 결정을 내립니다.
# 회고 분석 기능 또한 이 파일에 포함됩니다.

import openai
import json
import logging
import sqlite3
import pyupbit
import pandas as pd
import time
from datetime import datetime
from datetime import timedelta


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
    """
    거래 후 가격 추이 확인을 위한 헬퍼 함수 (수정된 버전)
    '판단 시점'을 기준으로 미래 데이터를 조회하여 오래된 기록도 평가할 수 있습니다.
    """
    try:
        start_dt = pd.to_datetime(start_datetime_str)
        # 판단 시점으로부터 13시간 뒤를 조회 종료 시점으로 설정 (12개 캔들 확보용)
        # 'to' 파라미터가 해당 시점 '이전' 데이터를 가져오므로 넉넉하게 설정
        end_dt = start_dt + timedelta(hours=count + 1)

        # 'to' 파라미터를 사용하여 특정 과거 시점의 데이터를 조회합니다.
        df = pyupbit.get_ohlcv(ticker, interval=interval, to=end_dt, count=200)

        if df is None or df.empty: return pd.DataFrame()

        # 타임존 정보가 있다면 제거하여 통일시킵니다.
        if df.index.tz is not None: df.index = df.index.tz_localize(None)

        # start_dt 이후의 데이터만 필터링하여 '미래' 데이터를 추출합니다.
        future_data = df[df.index > start_dt].head(count)
        return future_data
    except Exception as e:
        logger.error(f"미래 가격 데이터 조회 중 오류: {e}")
        return pd.DataFrame()


def _evaluate_decision_outcome(decision_entry: dict) -> dict:
    """
    'decision_log'의 단일 '판단' 기록이 어떤 결과를 낳았는지 평가합니다.
    (기존 _evaluate_trade_outcome 함수를 대체)
    """
    decision = decision_entry.get('decision')
    price_at_decision = decision_entry.get('price_at_decision')
    timestamp = decision_entry.get('timestamp')
    ticker = decision_entry.get('ticker')
    outcome = {"evaluation": "neutral", "details": "N/A"}

    if not all([decision, price_at_decision, timestamp, ticker]):
        return outcome

    # 판단 후 12개 캔들(12시간) 동안의 가격 추이를 확인
    future_data_df = _get_future_price_data(
        ticker=ticker,
        interval=config.TRADE_INTERVAL,
        start_datetime_str=timestamp,
        count=12
    )

    if future_data_df.empty:
        outcome["details"] = "미래 가격 데이터 조회 불가"
        return outcome

    highest_price_after = future_data_df['high'].max()
    lowest_price_after = future_data_df['low'].min()
    price_change_high = ((highest_price_after - price_at_decision) / price_at_decision) * 100
    price_change_low = ((lowest_price_after - price_at_decision) / price_at_decision) * 100

    if decision == 'buy':
        if price_change_high > 5:
            outcome["evaluation"] = "good_buy_decision"
            outcome["details"] = f"판단 후 +{price_change_high:.2f}% 까지 상승."
        else:
            outcome["evaluation"] = "bad_buy_decision"
            outcome["details"] = f"판단 후 유의미한 상승 없음 (최고 +{price_change_high:.2f}%)."

    elif decision == 'sell':
        if price_change_low < -3:
            outcome["evaluation"] = "good_sell_decision"
            outcome["details"] = f"판단 후 {price_change_low:.2f}% 까지 추가 하락 (손실 회피)."
        else:
            outcome["evaluation"] = "bad_sell_decision"
            outcome["details"] = f"판단 후 오히려 상승하거나 하락 미미 (최저 {price_change_low:.2f}%)."

    elif decision == 'hold':
        if price_change_high > 5:
            outcome["evaluation"] = "missed_opportunity"
            outcome["details"] = f"Hold 판단 후 +{price_change_high:.2f}% 상승 (기회비용 발생)."
        elif price_change_low < -3:
            outcome["evaluation"] = "good_hold"
            outcome["details"] = f"Hold 판단 후 {price_change_low:.2f}% 하락 (손실 회피)."
        else:
            outcome["evaluation"] = "neutral_hold"
            outcome["details"] = "Hold 판단 후 큰 변동 없음."

    logger.info(f"  - 판단 ID {decision_entry.get('id')} ({ticker}, {decision.upper()}) 평가: {outcome['evaluation']}")
    return outcome


def perform_retrospective_analysis(config, openai_client, portfolio_manager, current_cycle_count):
    """
    'decision_log'를 바탕으로 AI에게 회고 분석을 요청하고, 그 결과를 DB에 저장합니다.
    """
    logger.info("--- 🤖 AI 회고 분석 시스템 (v2) 시작 ---")

    representative_ticker = portfolio_manager.ticker
    current_roi = portfolio_manager.state.get('roi_percent', 0.0)

    try:
        with sqlite3.connect(config.LOG_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            recent_decisions = conn.execute("SELECT * FROM decision_log ORDER BY id DESC LIMIT 20").fetchall()

        if not recent_decisions:
            logger.info("분석할 최근 판단 기록이 없습니다.")
            return

        logger.info(f"decision_log에서 {len(recent_decisions)}개의 최근 판단 기록을 분석합니다.")

        evaluated_decisions = []
        for d in recent_decisions:
            decision_dict = dict(d)
            outcome = _evaluate_decision_outcome(decision_dict)
            evaluated_decisions.append({"decision": decision_dict, "outcome": outcome})
            time.sleep(0.2)


        prompt = f"""
    You are a trading performance coach. Analyze the bot's recent JUDGMENTS.
    The bot's current portfolio ROI is {current_roi:.2f}%.

    Here are the last 20 judgments and their short-term outcomes:
    - `good_buy_decision`: A 'buy' judgment was made, and the price went up.
    - `missed_opportunity`: A 'hold' judgment was made, but the price went up (a missed profit).
    - `good_hold`: A 'hold' judgment was made, and the price went down (a correctly avoided loss).
    - `good_sell_decision`: A 'sell' judgment was made, and the price went down further.
    - `bad_..._decision`: Judgments that were incorrect.

    Judgments & Outcomes Data:
        ```json
        {json.dumps(evaluated_decisions, indent=2, default=str)}
        ```
    Based on this data, provide a concise analysis in Korean:
    1.  **Success Patterns**: 'good_buy_decision'이나 'good_hold' 같은 성공적인 판단들의 공통적인 'reason'이나 시장 상황은 무엇이었는가?
    2.  **Failure Patterns**: 'missed_opportunity'나 'bad_buy_decision' 같은 아쉬운 판단들의 공통적인 특징은 무엇이었는가? (가장 중요한 부분)
    3.  **Actionable Recommendations**: 이 분석을 바탕으로, AI나 앙상블 전략의 어떤 부분을 수정하면 좋을지 구체적인 개선 방안 1~2가지를 제안하라. (예: "Hold 판단 후 기회를 놓치는 경우가 많으니, AI가 'hold'를 결정할 때의 보수적인 기준을 약간 완화하는 것을 고려해 보십시오.")
    """
        logger.debug(f"AI 회고 분석 프롬프트:\n{prompt}")

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        reflection = response.choices[0].message.content
        logger.info("\n\n--- 💡 AI 회고 분석 결과 (v2) 💡 ---\n" + reflection + "\n---------------------------------")

        # ✨ 2. AI 분석 결과를 'retrospection_log' 테이블에 저장하는 로직을 추가합니다.
        try:
            with sqlite3.connect(config.LOG_DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO retrospection_log (timestamp, cycle_count, evaluated_decisions_json, ai_reflection_text)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        current_cycle_count,
                        json.dumps(evaluated_decisions, indent=2, default=str),
                        reflection
                    )
                )
                conn.commit()
                logger.info("✅ AI 회고 분석 결과를 'retrospection_log' 테이블에 성공적으로 저장했습니다.")
        except Exception as e:
            logger.error(f"❌ 회고 분석 결과 DB 저장 중 오류 발생: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"회고 분석 중 오류 발생: {e}", exc_info=True)