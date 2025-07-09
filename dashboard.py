# dashboard.py

import streamlit as st
import pandas as pd
import sqlite3
import pyupbit
import plotly.express as px
import os

# --- 페이지 설정 (가장 먼저 호출되어야 함) ---
st.set_page_config(
    page_title="나의 자동매매 대시보드",
    page_icon="🤖",
    layout="wide",
)

# --- 데이터베이스 경로 설정 ---
# config.py와 동일한 경로를 사용하도록 설정합니다.
DB_DIR = "data"
LOG_DB_PATH = os.path.join(DB_DIR, "autotrading_log.db")


# --- 데이터 로딩 및 캐싱 ---
@st.cache_data(ttl=60)  # 60초마다 데이터 다시 로드
def load_data():
    """데이터베이스에서 거래 기록과 포트폴리오 상태를 불러옵니다."""
    if not os.path.exists(LOG_DB_PATH):
        st.error(f"데이터베이스 파일을 찾을 수 없습니다: {LOG_DB_PATH}")
        return pd.DataFrame(), pd.DataFrame()

    with sqlite3.connect(LOG_DB_PATH) as conn:
        # 모든 거래 기록 (buy, sell, hold 포함)
        trade_log_df = pd.read_sql_query("SELECT * FROM paper_trade_log", conn)
        # 모든 코인의 마지막 포트폴리오 상태
        portfolio_state_df = pd.read_sql_query("SELECT * FROM paper_portfolio_state", conn)

    # 타임스탬프 컬럼을 datetime 객체로 변환
    if not trade_log_df.empty:
        trade_log_df['timestamp'] = pd.to_datetime(trade_log_df['timestamp'])
    return trade_log_df, portfolio_state_df


# --- 데이터 처리 및 지표 계산 함수 ---
def get_dashboard_metrics(trade_log_df, portfolio_state_df):
    """대시보드에 필요한 모든 지표를 계산합니다."""
    metrics = {}

    # 1. 보유 코인 평가금액 및 총 자산 계산
    total_asset_value = 0
    current_holdings = []
    if not portfolio_state_df.empty:
        # 보유 자산이 있는 코인만 필터링
        holding_states = portfolio_state_df[portfolio_state_df['asset_balance'] > 0]
        for _, row in holding_states.iterrows():
            try:
                current_price = pyupbit.get_current_price(row['ticker'])
                eval_amount = row['asset_balance'] * current_price
                total_asset_value += eval_amount
                current_holdings.append({
                    "코인": row['ticker'],
                    "보유수량": row['asset_balance'],
                    "평단가": row['avg_buy_price'],
                    "현재가": current_price,
                    "평가금액": eval_amount,
                    "수익률(%)": ((current_price / row['avg_buy_price']) - 1) * 100 if row['avg_buy_price'] > 0 else 0
                })
            except Exception:
                # 현재가 조회 실패 시 0으로 처리
                pass

    # 현금 잔고는 첫 번째 포트폴리오의 것을 대표로 사용 (실제로는 총합을 구해야 더 정확)
    cash_balance = portfolio_state_df['krw_balance'].iloc[0] if not portfolio_state_df.empty else 0
    metrics['current_total_assets'] = cash_balance + total_asset_value

    # 초기 자본금 합계
    initial_capital = portfolio_state_df['initial_capital'].sum()

    # 2. 총 손익 및 수익률
    metrics['total_pnl'] = metrics['current_total_assets'] - initial_capital if initial_capital > 0 else 0
    metrics['total_roi_percent'] = (metrics['total_pnl'] / initial_capital) * 100 if initial_capital > 0 else 0

    # 3. 거래 관련 지표 계산
    completed_trades = trade_log_df[trade_log_df['action'] == 'sell']
    metrics['trade_count'] = len(completed_trades)

    if not completed_trades.empty:
        winning_trades = completed_trades[completed_trades['profit'] > 0]
        losing_trades = completed_trades[completed_trades['profit'] <= 0]

        metrics['win_rate'] = (len(winning_trades) / metrics['trade_count']) * 100 if metrics['trade_count'] > 0 else 0
        metrics['avg_profit'] = winning_trades['profit'].mean() if len(winning_trades) > 0 else 0
        metrics['avg_loss'] = losing_trades['profit'].mean() if len(losing_trades) > 0 else 0
        metrics['profit_loss_ratio'] = abs(metrics['avg_profit'] / metrics['avg_loss']) if metrics['avg_loss'] != 0 else float('inf')
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


# --- 대시보드 UI 구성 ---
st.title("🤖 나의 자동매매 시스템 대시보드")

# 데이터 로드
trade_log_df, portfolio_state_df = load_data()

if trade_log_df.empty or portfolio_state_df.empty:
    st.warning("아직 거래 기록이나 포트폴리오 데이터가 없습니다.")
else:
    # 지표 계산
    metrics = get_dashboard_metrics(trade_log_df, portfolio_state_df)

    # --- 1. 핵심 요약 지표 ---
    st.subheader("📊 핵심 요약 지표")
    cols = st.columns(5)
    cols[0].metric("현재 총 자산", f"{metrics['current_total_assets']:,.0f} 원")
    cols[1].metric("총 수익률", f"{metrics['total_roi_percent']:.2f} %")
    cols[2].metric("총 손익", f"{metrics['total_pnl']:,.0f} 원")
    cols[3].metric("총 거래 횟수", f"{metrics['trade_count']} 회")
    cols[4].metric("거래 승률", f"{metrics['win_rate']:.2f} %")

    cols = st.columns(5)
    cols[0].metric("평균 수익", f"{metrics['avg_profit']:,.0f} 원")
    cols[1].metric("평균 손실", f"{metrics['avg_loss']:,.0f} 원")
    cols[2].metric("손익비", f"{metrics['profit_loss_ratio']:.2f}")


    st.markdown("---") # 구분선

    # --- 2. 시각적 차트 ---
    st.subheader("📈 시각화")
    chart_cols = st.columns([1, 2]) # 1:2 비율로 컬럼 나누기

    with chart_cols[0]:
        st.markdown("##### 자산 비중")
        fig_pie = px.pie(metrics['asset_allocation_df'], values='금액', names='자산', title='현금 vs 코인')
        st.plotly_chart(fig_pie, use_container_width=True)

    with chart_cols[1]:
        st.markdown("##### 월별 실현 손익")
        completed_trades = trade_log_df[trade_log_df['action'] == 'sell'].copy()
        if not completed_trades.empty:
            completed_trades['month'] = completed_trades['timestamp'].dt.to_period('M').astype(str)
            monthly_pnl = completed_trades.groupby('month')['profit'].sum().reset_index()
            fig_bar = px.bar(monthly_pnl, x='month', y='profit', title='월별 실현 손익', labels={'profit':'실현손익(원)'})
            st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("아직 실현된 손익이 없습니다.")

    st.markdown("---")

    # --- 3. 상세 데이터 ---
    st.subheader("📋 상세 데이터")

    st.markdown("##### 현재 보유 코인")
    st.dataframe(metrics['current_holdings_df'], use_container_width=True)

    st.markdown("##### 전체 활동 기록 (최신 100건)")
    # context 컬럼의 reason을 파싱하여 새로운 컬럼으로 만듭니다.
    trade_log_df['reason'] = trade_log_df['context'].apply(lambda x: eval(x).get('reason', '') if isinstance(x, str) and x.startswith('{') else '')
    st.dataframe(trade_log_df.tail(100).sort_values(by='timestamp', ascending=False), use_container_width=True)

# 1분마다 페이지를 자동으로 새로고침
st.experimental_rerun()