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