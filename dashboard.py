# dashboard.py (실제/모의 투자 조회 기능 추가)

import streamlit as st
import pandas as pd
import sqlite3
import pyupbit
import plotly.express as px
import os
import json
from dotenv import load_dotenv
from apis import upbit_api # 실제 계좌 조회를 위해 upbit_api 임포트

# --- 페이지 및 기본 설정 ---
st.set_page_config(
    page_title="나의 자동매매 대시보드",
    page_icon="🤖",
    layout="wide",
)

# systemd 서비스의 환경 변수 파일을 직접 로드합니다.
env_file_path = '/etc/default/autotrader.env'
if os.path.exists(env_file_path):
    load_dotenv(dotenv_path=env_file_path)

# 만약 위 파일이 없다면, 로컬 테스트를 위해 프로젝트 폴더의 .env 파일을 찾습니다.
else:
    dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path)

UPBIT_ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY")
UPBIT_SECRET_KEY = os.getenv("UPBIT_SECRET_KEY")

# --- 데이터베이스 경로 설정 ---
# 사용자가 선택한 모드에 따라 DB 경로를 동적으로 변경
def get_db_path(mode):
    db_file = "autotrading_log_real.db" if mode == 'real' else "autotrading_log.db"
    return os.path.join("data", db_file)

# --- 데이터 로딩 함수 (모드별로 수정) ---
@st.cache_data(ttl=60)
def load_data(mode):
    """선택된 모드(real/simulation)에 따라 데이터베이스에서 데이터를 불러옵니다."""
    db_path = get_db_path(mode)
    if not os.path.exists(db_path):
        st.error(f"데이터베이스 파일을 찾을 수 없습니다: {db_path}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    with sqlite3.connect(db_path) as conn:
        # 모드에 따라 다른 테이블에서 거래 기록을 로드합니다.
        trade_table = "real_trade_log" if mode == 'real' else "paper_trade_log"
        trade_log_df = pd.read_sql_query(f"SELECT * FROM {trade_table}", conn, parse_dates=['timestamp'])

        decision_log_df = pd.read_sql_query("SELECT * FROM decision_log", conn, parse_dates=['timestamp'])

        # 실제 투자 모드에서는 paper_portfolio_state 테이블이 없으므로 빈 DataFrame을 반환합니다.
        portfolio_state_df = pd.DataFrame()
        if mode == 'simulation':
            portfolio_state_df = pd.read_sql_query("SELECT * FROM paper_portfolio_state", conn)

    return trade_log_df, decision_log_df, portfolio_state_df

# --- ✨ [신규] 실제 투자용 지표 계산 함수 ---
def get_real_dashboard_metrics(trade_log_df):
    """Upbit API를 통해 실제 계좌 정보를 가져와 대시보드 지표를 계산합니다."""
    metrics = {}
    upbit_client = upbit_api.UpbitAPI(UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY)

    if upbit_client.client is None:
        st.error("Upbit API 클라이언트 초기화에 실패했습니다. .env 파일의 API 키 설정을 확인해주세요.")
        return {}

    my_accounts = upbit_client.client.get_balances()

    if not my_accounts:
        st.warning("Upbit 계좌 정보를 불러올 수 없습니다. API 키의 권한(자산 조회)을 확인해주세요.")
        return {}

    # 1. 실현 손익 계산
    completed_trades = trade_log_df[trade_log_df['action'] == 'sell']
    total_realized_pnl = completed_trades['profit'].sum() if not completed_trades.empty else 0

    # 2. 보유 자산 평가 및 미실현 손익 계산
    cash_balance = 0
    total_asset_value = 0
    total_buy_amount = 0
    current_holdings = []

    # KRW를 제외한 보유 코인 목록 생성
    coins_held = [acc for acc in my_accounts if acc['currency'] != 'KRW']
    coin_tickers = [f"KRW-{acc['currency']}" for acc in coins_held]

    # 1. 실현 손익 계산
    completed_trades = trade_log_df[trade_log_df['action'] == 'sell']
    total_realized_pnl = completed_trades['profit'].sum() if not completed_trades.empty else 0

    # 2. 보유 자산 평가 및 미실현 손익 계산
    cash_balance = 0
    total_asset_value = 0
    total_buy_amount = 0
    current_holdings = []

    coins_held = [acc for acc in my_accounts if acc['currency'] != 'KRW' and float(acc['balance']) > 0]
    coin_tickers = [f"KRW-{acc['currency']}" for acc in coins_held]

    if coin_tickers:
        try:
            current_prices = pyupbit.get_current_price(coin_tickers)

            # --- ✨ [핵심 수정] 보유 코인이 1개일 경우를 처리하는 로직 ---
            if isinstance(current_prices, float):
                # pyupbit이 숫자 하나만 반환한 경우, 강제로 딕셔너리 형태로 만듭니다.
                current_prices = {coin_tickers[0]: current_prices}
            # --- 수정 끝 ---

            for acc in coins_held:
                ticker = f"KRW-{acc['currency']}"
                balance = float(acc['balance'])
                avg_buy_price = float(acc['avg_buy_price'])
                current_price = current_prices.get(ticker)

                if not current_price: continue

                eval_amount = balance * current_price
                buy_amount = balance * avg_buy_price
                unrealized_pnl = eval_amount - buy_amount

                total_asset_value += eval_amount
                total_buy_amount += buy_amount

                current_holdings.append({
                    "코인": ticker,
                    "보유수량": balance,
                    "평단가": avg_buy_price,
                    "현재가": current_price,
                    "평가금액": eval_amount,
                    "미실현손익": unrealized_pnl,
                    "수익률(%)": (unrealized_pnl / buy_amount) * 100 if buy_amount > 0 else 0
                })
        except Exception as e:
            st.error(f"Upbit 현재가 조회 중 오류: {e}")

    # 3. 최종 지표 계산
    cash_balance = upbit_client.client.get_balance("KRW")
    metrics['current_total_assets'] = cash_balance + total_asset_value
    total_unrealized_pnl = total_asset_value - total_buy_amount
    metrics['total_pnl'] = total_realized_pnl + total_unrealized_pnl

    # 실제 투자의 초기 자본금은 직접 정의하거나, 첫 입금액 등으로 계산해야 합니다.
    # 여기서는 편의상 현재 총 자산에서 총 손익을 뺀 값으로 추정합니다.
    initial_capital_est = metrics['current_total_assets'] - metrics['total_pnl']
    metrics['total_roi_percent'] = (metrics['total_pnl'] / initial_capital_est) * 100 if initial_capital_est > 0 else 0

    # 4. 거래 관련 지표 (모의투자 로직과 동일)
    metrics['trade_count'] = len(completed_trades)
    if not completed_trades.empty:
        wins = completed_trades[completed_trades['profit'] > 0]
        losses = completed_trades[completed_trades['profit'] <= 0]
        metrics['win_rate'] = (len(wins) / len(completed_trades)) * 100 if len(completed_trades) > 0 else 0
        metrics['avg_profit'] = wins['profit'].mean() if not wins.empty else 0
        metrics['avg_loss'] = losses['profit'].mean() if not losses.empty else 0
        metrics['profit_loss_ratio'] = abs(metrics['avg_profit'] / metrics['avg_loss']) if metrics[
                                                                                               'avg_loss'] != 0 else float(
            'inf')
    else:
        metrics.update({'win_rate': 0, 'avg_profit': 0, 'avg_loss': 0, 'profit_loss_ratio': 0})

    metrics['current_holdings_df'] = pd.DataFrame(current_holdings)
    metrics['asset_allocation_df'] = pd.DataFrame([
        {'자산': '현금', '금액': cash_balance},
        {'자산': '코인', '금액': total_asset_value}
    ])

    return metrics

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


# --- ✨ [수정] 모의 투자용 지표 계산 함수 (기존 함수 재활용) ---
def get_simulation_dashboard_metrics(trade_log_df, portfolio_state_df):
    # 이 함수는 기존 get_dashboard_metrics 함수의 로직과 동일합니다.
    # 명확성을 위해 이름을 변경하여 사용합니다.
    return get_dashboard_metrics(trade_log_df, portfolio_state_df) # 기존 함수 호출

# --- 대시보드 UI 구성 ---
st.title("🤖 나의 자동매매 시스템 대시보드")

# ✨ [수정] 모드 선택 기능 추가
mode = st.sidebar.radio(
    "조회할 포트폴리오를 선택하세요:",
    ('simulation', 'real'),
    captions=["모의 투자 현황", "실제 투자 현황"]
)

# 탭을 사용하여 정보 분리
main_tab, analysis_tab = st.tabs(["📊 메인 대시보드", "🧠 AI 회고 분석"])

# --- 탭 1: 메인 대시보드 ---
with main_tab:
    # ✨ [수정] 헤더에 현재 모드를 명확히 표시
    st.header(f"'{mode.upper()}' 포트폴리오 현황")

    trade_log_df, decision_log_df, portfolio_state_df = load_data(mode)

    # ✨ [핵심 수정] 모드에 따라 다른 지표 계산 함수를 호출하도록 변경
    metrics = {}  # metrics 딕셔너리 초기화
    if mode == 'real':
        # 실제 투자 모드일 경우, API를 사용하는 get_real_dashboard_metrics 함수 호출
        metrics = get_real_dashboard_metrics(trade_log_df)
    else:
        # 모의 투자 모드일 경우, 기존 함수(get_simulation_dashboard_metrics) 호출
        if portfolio_state_df.empty:
            st.warning("아직 모의투자 포트폴리오 데이터가 없습니다.")
        else:
            metrics = get_simulation_dashboard_metrics(trade_log_df, portfolio_state_df)

    # metrics 딕셔너리가 비어있지 않을 때만 아래 UI를 그림
    if not metrics:
        st.warning(f"'{mode}' 모드의 데이터를 불러올 수 없습니다.")
    else:
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
            # ✨ [수정] trade_log_df 직접 사용
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
        # ✨ [수정] metrics 딕셔너리에서 데이터프레임을 가져올 때 기본값으로 빈 DF 제공
        if not metrics.get('current_holdings_df', pd.DataFrame()).empty:
            st.dataframe(metrics['current_holdings_df'], use_container_width=True)
        else:
            st.info("현재 보유 중인 코인이 없습니다.")

        st.markdown(f"##### {'실제' if mode == 'real' else '모의'} 매매 기록 (최신 100건)")
        if not trade_log_df.empty:
            display_cols = ['timestamp', 'ticker', 'action', 'price', 'amount', 'krw_value', 'profit']
            # real_trade_log에는 fee 컬럼이 없을 수 있으므로 확인 후 추가
            if 'fee' in trade_log_df.columns: display_cols.append('fee')

            # DB에 없는 컬럼을 요청할 경우를 대비하여, 존재하는 컬럼만 선택
            existing_cols = [col for col in display_cols if col in trade_log_df.columns]
            display_trades = trade_log_df[existing_cols].copy()

            # 컬럼 이름 변경
            rename_map = {'timestamp': '체결시간', 'ticker': '코인', 'action': '종류', 'price': '체결단가',
                          'amount': '수량', 'krw_value': '거래금액', 'profit': '실현손익', 'fee': '수수료'}
            display_trades.rename(columns=rename_map, inplace=True)

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


    # ✨ [수정] 함수들이 mode에 따라 올바른 DB를 바라보도록 수정
    @st.cache_data(ttl=60)
    def load_analysis_history_list(mode):
        """DB에 저장된 모든 회고 분석 기록의 목록을 불러옵니다."""
        db_path = get_db_path(mode)  # get_db_path 사용
        if not os.path.exists(db_path): return []
        try:
            with sqlite3.connect(db_path) as conn:
                query = "SELECT id, timestamp, cycle_count FROM retrospection_log ORDER BY id DESC"
                history = conn.execute(query).fetchall()
            return history
        except sqlite3.OperationalError:
            return []


    @st.cache_data(ttl=60)
    def load_specific_analysis(analysis_id, mode):
        """선택된 특정 ID의 회고 분석 상세 데이터를 불러옵니다."""
        db_path = get_db_path(mode)  # get_db_path 사용
        with sqlite3.connect(db_path) as conn:
            query = "SELECT evaluated_decisions_json, ai_reflection_text FROM retrospection_log WHERE id = ?"
            row = conn.execute(query, (analysis_id,)).fetchone()
        return row


    # ✨ [수정] 현재 선택된 mode를 인자로 넘겨줌
    analysis_history = load_analysis_history_list(mode)
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
        analysis_details = load_specific_analysis(selected_id, mode)

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

