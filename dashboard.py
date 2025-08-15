# dashboard.py

import streamlit as st
import pandas as pd
import sqlite3
import pyupbit
import plotly.express as px
import os
import json
from collections import Counter

# --- 페이지 설정 (가장 먼저 호출되어야 함) ---
st.set_page_config(
    page_title="나의 자동매매 대시보드",
    page_icon="🤖",
    layout="wide",
)

# --- 데이터베이스 경로 설정 ---
DB_DIR = "data"
LOG_DB_PATH = os.path.join(DB_DIR, "autotrading_log.db")


# --- 데이터 로딩 및 캐싱 ---
@st.cache_data(ttl=60)  # 60초마다 데이터 다시 로드
def load_data():
    """데이터베이스에서 필요한 모든 데이터를 불러옵니다."""
    if not os.path.exists(LOG_DB_PATH):
        st.error(f"데이터베이스 파일을 찾을 수 없습니다: {LOG_DB_PATH}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    with sqlite3.connect(LOG_DB_PATH) as conn:
        # '거래' 기록과 '판단' 기록, 포트폴리오 상태를 명확히 분리하여 로드
        trade_log_df = pd.read_sql_query("SELECT * FROM paper_trade_log", conn, parse_dates=['timestamp'])
        decision_log_df = pd.read_sql_query("SELECT * FROM decision_log", conn, parse_dates=['timestamp'])
        portfolio_state_df = pd.read_sql_query("SELECT * FROM paper_portfolio_state", conn)

    return trade_log_df, decision_log_df, portfolio_state_df


@st.cache_data(ttl=60)
def load_retrospection_data():
    """가장 최신의 회고 분석 데이터를 불러옵니다."""
    if not os.path.exists(LOG_DB_PATH):
        return None

    try:
        with sqlite3.connect(LOG_DB_PATH) as conn:
            query = "SELECT timestamp, cycle_count, evaluated_decisions_json, ai_reflection_text FROM retrospection_log ORDER BY id DESC LIMIT 1"
            row = conn.execute(query).fetchone()
        return row
    except sqlite3.OperationalError:
        # 테이블이 아직 없는 경우를 대비
        return None


def get_dashboard_metrics(trade_log_df, portfolio_state_df):
    """대시보드에 필요한 모든 지표를 계산합니다."""
    metrics = {}

    # --- 1. 실현 손익 계산 (sell 기록 기준) ---
    completed_trades = trade_log_df[trade_log_df['action'] == 'sell']
    total_realized_pnl = completed_trades['profit'].sum() if 'profit' in completed_trades.columns else 0

    # --- 2. 보유 자산 평가 및 미실현 손익 계산 ---
    total_asset_value = 0
    total_unrealized_pnl = 0
    current_holdings = []

    if not portfolio_state_df.empty:
        holding_states = portfolio_state_df[portfolio_state_df['asset_balance'] > 0]
        tickers_to_fetch = holding_states['ticker'].tolist()

        if tickers_to_fetch:
            try:
                current_prices = pyupbit.get_current_price(tickers_to_fetch)

                for _, row in holding_states.iterrows():
                    current_price = current_prices.get(row['ticker'])
                    if current_price is None: continue

                    eval_amount = row['asset_balance'] * current_price
                    unrealized_pnl_per_ticker = (current_price - row['avg_buy_price']) * row['asset_balance']

                    total_asset_value += eval_amount
                    total_unrealized_pnl += unrealized_pnl_per_ticker

                    current_holdings.append({
                        "코인": row['ticker'],
                        "보유수량": row['asset_balance'],
                        "평단가": row['avg_buy_price'],
                        "현재가": current_price,
                        "평가금액": eval_amount,
                        "미실현손익": unrealized_pnl_per_ticker,
                        "수익률(%)": ((current_price / row['avg_buy_price']) - 1) * 100 if row['avg_buy_price'] > 0 else 0
                    })
            except Exception as e:
                st.error(f"Upbit 현재가 조회 중 오류 발생: {e}")
                pass

    # --- 3. 최종 지표 계산 ---
    cash_balance = portfolio_state_df['krw_balance'].sum()
    metrics['current_total_assets'] = cash_balance + total_asset_value
    metrics['total_pnl'] = total_realized_pnl + total_unrealized_pnl

    initial_capital_total = portfolio_state_df['initial_capital'].sum()
    metrics['total_roi_percent'] = (metrics[
                                        'total_pnl'] / initial_capital_total) * 100 if initial_capital_total > 0 else 0

    # --- 4. 거래 관련 지표 계산 ---
    metrics['trade_count'] = len(completed_trades)
    if not completed_trades.empty:
        winning_trades = completed_trades[completed_trades['profit'] > 0]
        losing_trades = completed_trades[completed_trades['profit'] <= 0]

        metrics['win_rate'] = (len(winning_trades) / metrics['trade_count']) * 100 if metrics['trade_count'] > 0 else 0
        metrics['avg_profit'] = winning_trades['profit'].mean() if len(winning_trades) > 0 else 0
        metrics['avg_loss'] = losing_trades['profit'].mean() if len(losing_trades) > 0 else 0
        metrics['profit_loss_ratio'] = abs(metrics['avg_profit'] / metrics['avg_loss']) if metrics[
                                                                                               'avg_loss'] != 0 else float(
            'inf')
    else:
        metrics['win_rate'] = 0
        metrics['avg_profit'] = 0
        metrics['avg_loss'] = 0
        metrics['profit_loss_ratio'] = 0

    metrics['current_holdings_df'] = pd.DataFrame(current_holdings)
    metrics['asset_allocation_df'] = pd.DataFrame([
        {'자산': '현금', '금액': cash_balance},
        {'자산': '코인', '금액': total_asset_value}
    ])

    return metrics

# --- 데이터 로딩 함수 수정 및 추가 ---
@st.cache_data(ttl=60)
def load_analysis_history_list():
    """DB에 저장된 모든 회고 분석 기록의 목록을 불러옵니다."""
    if not os.path.exists(LOG_DB_PATH): return []
    try:
        with sqlite3.connect(LOG_DB_PATH) as conn:
            query = "SELECT id, timestamp, cycle_count FROM retrospection_log ORDER BY id DESC"
            history = conn.execute(query).fetchall()
        # [(1, '2025-08-15...', 12), (2, '2025-08-16...', 24), ...] 형태로 반환
        return history
    except sqlite3.OperationalError:
        return []

@st.cache_data(ttl=60)
def load_specific_analysis(analysis_id):
    """선택된 특정 ID의 회고 분석 상세 데이터를 불러옵니다."""
    with sqlite3.connect(LOG_DB_PATH) as conn:
        query = "SELECT evaluated_decisions_json, ai_reflection_text FROM retrospection_log WHERE id = ?"
        row = conn.execute(query, (analysis_id,)).fetchone()
    return row

# --- 대시보드 UI 구성 ---
st.title("🤖 나의 자동매매 시스템 대시보드")

# 탭을 사용하여 정보 분리
main_tab, analysis_tab = st.tabs(["📊 메인 대시보드", "🧠 AI 회고 분석"])

# --- 탭 1: 메인 대시보드 ---
with main_tab:
    trade_log_df, decision_log_df, portfolio_state_df = load_data()

    if portfolio_state_df.empty:
        st.warning("아직 포트폴리오 데이터가 없습니다.")
    else:
        metrics = get_dashboard_metrics(trade_log_df, portfolio_state_df)

        st.subheader("📊 핵심 요약 지표")
        cols = st.columns(5)
        cols[0].metric("현재 총 자산", f"{metrics.get('current_total_assets', 0):,.0f} 원")
        cols[1].metric("총 수익률", f"{metrics.get('total_roi_percent', 0):.2f} %")
        cols[2].metric("총 손익", f"{metrics.get('total_pnl', 0):,.0f} 원")
        cols[3].metric("총 거래 횟수", f"{metrics.get('trade_count', 0)} 회")
        cols[4].metric("거래 승률", f"{metrics.get('win_rate', 0):.2f} %")

        cols2 = st.columns(5)
        cols2[0].metric("평균 수익", f"{metrics.get('avg_profit', 0):,.0f} 원")
        cols2[1].metric("평균 손실", f"{metrics.get('avg_loss', 0):,.0f} 원")
        cols2[2].metric("손익비", f"{metrics.get('profit_loss_ratio', 0):.2f}")

        st.markdown("---")

        st.subheader("📈 시각화")
        chart_cols = st.columns([1, 2])

        with chart_cols[0]:
            st.markdown("##### 자산 비중")
            if metrics.get('current_total_assets', 0) > 0:
                fig_pie = px.pie(metrics['asset_allocation_df'], values='금액', names='자산', title='현금 vs 코인')
                st.plotly_chart(fig_pie, use_container_width=True)
            else:
                st.info("자산이 없습니다.")

        with chart_cols[1]:
            st.markdown("##### 월별 실현 손익")
            completed_trades = trade_log_df[trade_log_df['action'] == 'sell'].copy()
            if not completed_trades.empty and 'profit' in completed_trades.columns:
                completed_trades['month'] = completed_trades['timestamp'].dt.to_period('M').astype(str)
                monthly_pnl = completed_trades.groupby('month')['profit'].sum().reset_index()
                fig_bar = px.bar(monthly_pnl, x='month', y='profit', title='월별 실현 손익', labels={'profit': '실현손익(원)'})
                st.plotly_chart(fig_bar, use_container_width=True)
            else:
                st.info("아직 실현된 손익이 없습니다.")

        st.markdown("---")

        st.subheader("📋 상세 데이터")
        st.markdown("##### 현재 보유 코인")
        if not metrics['current_holdings_df'].empty:
            st.dataframe(metrics['current_holdings_df'], use_container_width=True)
        else:
            st.info("현재 보유 중인 코인이 없습니다.")

        # --- ✨ 1. 실제 매매 기록 표시 코드 추가 ✨ ---
        st.markdown("##### 실제 매매 기록 (최신 100건)")
        if not trade_log_df.empty:
            # 보기 좋게 표시할 컬럼만 선택하고, 한글로 이름을 변경합니다.
            display_trades = trade_log_df[
                ['timestamp', 'ticker', 'action', 'price', 'amount', 'krw_value', 'profit', 'fee']].copy()
            display_trades.columns = ['체결시간', '코인', '종류', '체결단가', '수량', '거래금액', '실현손익', '수수료']

            st.dataframe(
                display_trades.tail(100).sort_values(by='체결시간', ascending=False),
                use_container_width=True
            )
        else:
            st.info("아직 체결된 거래가 없습니다.")

        st.markdown("##### 전체 판단 기록 (최신 100건)")
        if not decision_log_df.empty:
            st.dataframe(decision_log_df.tail(100).sort_values(by='timestamp', ascending=False),
                         use_container_width=True)
        else:
            st.info("아직 판단 기록이 없습니다.")

# --- 탭 2: AI 회고 분석 (UI 로직 수정) ---
with analysis_tab:
    st.header("🧠 AI 회고 분석 결과")

    analysis_history = load_analysis_history_list()

    if not analysis_history:
        st.warning("아직 회고 분석 결과가 없습니다. 사이클이 충분히 돌아야 생성됩니다.")
    else:
        # 1. 선택 메뉴(selectbox) 생성
        # 사용자가 보기 편하도록 '사이클: 12 (분석시간: ...)' 형태로 메뉴 옵션을 만듭니다.
        history_options = {f"사이클: {cycle} ({timestamp})": analysis_id for analysis_id, timestamp, cycle in
                           analysis_history}
        selected_option = st.selectbox("보고 싶은 분석 기록을 선택하세요:", options=history_options.keys())

        # 2. 선택된 분석 기록의 상세 데이터 로드
        selected_id = history_options[selected_option]
        analysis_details = load_specific_analysis(selected_id)

        if analysis_details:
            decisions_json, reflection = analysis_details
            decisions = json.loads(decisions_json)

            # --- (이하 시각화 로직은 기존과 동일) ---
            summary_data = []
            for item in decisions:
                summary_data.append({
                    "ID": item["decision"]["id"],
                    "시간": item["decision"]["timestamp"],
                    "코인": item["decision"]["ticker"],
                    "판단": item["decision"]["decision"].upper(),
                    "성과": item["outcome"]["evaluation"],
                    "상세": item["outcome"]["details"]
                })
            df = pd.DataFrame(summary_data)

            col1, col2 = st.columns([1, 2])
            with col1:
                st.subheader("📈 성과 통계")
                outcome_counts = df['성과'].value_counts()
                st.bar_chart(outcome_counts)
                st.dataframe(outcome_counts)

            with col2:
                st.subheader("📝 판단 요약")
                st.dataframe(df, height=400)

            st.subheader("💡 AI 조언")
            st.text_area("AI의 분석 및 제안", reflection, height=300)

# --- 자동 새로고침 로직 ---
refresh_interval = 300  # 초 단위 (300초 = 5분)
st.html(f"""
    <meta http-equiv="refresh" content="{refresh_interval}">
""")