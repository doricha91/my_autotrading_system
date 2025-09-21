# view_analysis.py (최종 수정본)
import sqlite3
import json
import pandas as pd
import argparse  # 1. argparse 임포트
import importlib  # 2. importlib 임포트


def view_latest_analysis(config):  # 3. config 객체를 인자로 받도록 수정
    """
    DB에서 가장 최신의 회고 분석 결과를 가져와 3가지 방식으로 요약하여 보여줍니다.
    """
    try:
        # 4. 인자로 받은 config의 DB 경로를 사용
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

        # ... (이하 분석 및 출력 로직은 기존과 동일)
        summary_data = []
        for item in evaluated_decisions:
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
                "ID": item["decision"]["id"], "시간": item["decision"]["timestamp"],
                "코인": item["decision"]["ticker"], "판단": item["decision"]["decision"].upper(),
                "성과": item["outcome"]["evaluation"], "판단근거": reason_category,
                "상세": item["outcome"]["details"]
            })

        df = pd.DataFrame(summary_data)

        print("\n" + "=" * 80)
        print("--- 📝 최신 회고 분석 요약 ---")
        print(df[['ID', '시간', '코인', '판단', '성과', '판단근거', '상세']].to_string())
        print("\n" + "-" * 80)
        print("--- 📈 성과 통계 ---")
        print(df['성과'].value_counts().to_string())
        print("\n" + "-" * 80)
        print("--- 🧠 판단 근거별 성과 분석 ---")
        print(df.groupby('판단근거')['성과'].value_counts().unstack(fill_value=0).to_string())
        print("\n" + "-" * 80)
        print("--- 💡 AI 조언 ---")
        print(ai_reflection)
        print("=" * 80 + "\n")

    except Exception as e:
        print(f"분석 결과를 불러오는 중 오류 발생: {e}")


if __name__ == '__main__':
    # 2. main.py처럼 --config 인자를 받도록 수정
    parser = argparse.ArgumentParser(description="AI 회고 분석 결과 조회")
    parser.add_argument('--config', type=str, default='config', help="사용할 설정 파일 (예: config_real)")
    args = parser.parse_args()

    try:
        config_module = importlib.import_module(args.config)
        print(f"✅ '{args.config}.py' 설정으로 분석을 시작합니다.")
        view_latest_analysis(config_module)
    except ImportError:
        print(f"❌ 설정 파일 '{args.config}.py'을(를) 찾을 수 없습니다.")