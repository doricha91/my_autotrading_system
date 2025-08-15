# view_analysis.py
import sqlite3
import json
import pandas as pd
from collections import defaultdict
import config


def view_latest_analysis():
    """
    DB에서 가장 최신의 회고 분석 결과를 가져와 3가지 방식으로 요약하여 보여줍니다.
    1. 판단 요약 테이블
    2. 성과 통계
    3. 판단 근거별 성과 분석
    """
    try:
        with sqlite3.connect(config.LOG_DB_PATH) as conn:
            query = "SELECT evaluated_decisions_json, ai_reflection_text FROM retrospection_log ORDER BY id DESC LIMIT 1"
            cursor = conn.cursor()
            cursor.execute(query)
            row = cursor.fetchone()

        if not row:
            print("분석 결과가 아직 없습니다.")
            return

        evaluated_decisions = json.loads(row[0])
        ai_reflection = row[1]

        summary_data = []
        for item in evaluated_decisions:
            # 1. 'reason' 텍스트에서 핵심 패턴을 찾아 표준화된 카테고리로 분류합니다.
            reason_text = item["decision"]["reason"]
            reason_category = "기타"
            if "AI & Ensemble Agree [BUY]" in reason_text:
                reason_category = "AI 동의(BUY)"
            elif "AI & Ensemble Agree [SELL]" in reason_text:
                reason_category = "AI 동의(SELL)"
            elif "No Consensus or Hold Signal" in reason_text:
                reason_category = "AI 보류(HOLD)"
            elif "Sell signal ignored" in reason_text:
                reason_category = "미보유(SELL 무시)"
            elif "CONFLICT" in reason_text:
                reason_category = "신호 충돌"

            summary_data.append({
                "ID": item["decision"]["id"],
                "시간": item["decision"]["timestamp"],
                "코인": item["decision"]["ticker"],
                "판단": item["decision"]["decision"].upper(),
                "성과": item["outcome"]["evaluation"],
                "판단근거": reason_category,  # 분류된 카테고리를 추가합니다.
                "상세": item["outcome"]["details"]
            })

        # 2. 가공된 데이터를 pandas DataFrame으로 변환합니다.
        df = pd.DataFrame(summary_data)

        # 3. 분석 결과를 세 부분으로 나누어 출력합니다.
        print("\n" + "=" * 80)
        print("--- 📝 최신 회고 분석 요약 ---")
        print(df[['ID', '시간', '코인', '판단', '성과', '판단근거']].to_string())

        print("\n" + "-" * 80)
        print("--- 📈 성과 통계 ---")
        outcome_counts = df['성과'].value_counts()
        print(outcome_counts.to_string())

        print("\n" + "-" * 80)
        print("--- 🧠 판단 근거별 성과 분석 ---")
        # '판단근거'로 그룹을 묶고, 각 그룹 내의 '성과' 개수를 셉니다.
        # .unstack()으로 결과를 보기 좋은 테이블 형태로 만듭니다.
        reason_performance = df.groupby('판단근거')['성과'].value_counts().unstack(fill_value=0)
        print(reason_performance.to_string())

        print("\n" + "-" * 80)
        print("--- 💡 AI 조언 ---")
        print(ai_reflection)
        print("=" * 80 + "\n")

    except Exception as e:
        print(f"분석 결과를 불러오는 중 오류 발생: {e}")


if __name__ == '__main__':
    view_latest_analysis()