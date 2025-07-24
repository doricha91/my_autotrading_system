# run_telegram_bot.py (최종 수정본 - 실제/모의 투자 조회 분기)

import os
import logging
import sqlite3
import pandas as pd
import pyupbit
from telegram import Update
# ✨ 1. [핵심 수정] 필요한 클래스를 새로운 위치에서 가져옵니다.
from telegram.ext import Application, CommandHandler, ContextTypes
import config
from apis import upbit_api


# 로거 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 환경 변수 및 상수
TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_PATH = config.LOG_DB_PATH


async def get_real_portfolio_status() -> str:
    """[실제 투자용] Upbit API와 DB를 조회하여 현재 포트폴리오 상태를 반환합니다."""
    try:
        # 실제 투자용 Upbit API 클라이언트를 생성합니다.
        upbit_client = upbit_api.UpbitAPI(config.UPBIT_ACCESS_KEY, config.UPBIT_SECRET_KEY)
        my_accounts = upbit_client.get_accounts()  # 내 전체 계좌 정보를 가져옵니다.

        if not my_accounts:
            return "Upbit 계좌 정보를 불러올 수 없습니다."

        # --- 지표 계산 ---
        cash_balance = 0
        total_asset_value = 0
        total_buy_amount = 0  # 총 매수 금액
        holdings_info = []

        for acc in my_accounts:
            currency = acc['currency']
            balance = float(acc['balance'])
            avg_buy_price = float(acc['avg_buy_price'])

            if currency == "KRW":
                cash_balance = balance
                continue  # 원화는 아래 로직에서 제외

            # 보유 코인에 대해서만 처리
            current_price = pyupbit.get_current_price(f"KRW-{currency}")
            if not current_price: continue

            eval_amount = balance * current_price
            buy_amount = balance * avg_buy_price
            pnl = eval_amount - buy_amount
            roi = (eval_amount / buy_amount - 1) * 100 if buy_amount > 0 else 0

            total_asset_value += eval_amount
            total_buy_amount += buy_amount
            holdings_info.append(f" - KRW-{currency}: {pnl:,.0f}원 ({roi:.2f}%)")

        # DB에서 실제 거래의 실현 손익을 가져옵니다.
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
            df_real_log = pd.read_sql_query("SELECT profit FROM real_trade_log WHERE action = 'sell'", conn)

        total_realized_pnl = df_real_log['profit'].sum() if not df_real_log.empty else 0
        total_unrealized_pnl = total_asset_value - total_buy_amount

        # 실제 투자에서는 초기 투자금을 직접 알 수 없으므로, 현재 자산을 기준으로 보여줍니다.
        total_portfolio_value = cash_balance + total_asset_value

        # --- 최종 메시지 조합 ---
        message = f"--- 📊 포트폴리오 현황 (실제 투자) ---\n"
        message += f"총 자산: {total_portfolio_value:,.0f} 원\n"
        message += f"총 손익 (실현+미실현): {total_realized_pnl + total_unrealized_pnl:,.0f} 원\n"
        message += f"   - 실현 손익: {total_realized_pnl:,.0f} 원\n"
        message += f"   - 미실현 손익: {total_unrealized_pnl:,.0f} 원\n"
        message += "---------------------\n"
        message += f"현금: {cash_balance:,.0f} 원\n"
        message += f"코인 평가액: {total_asset_value:,.0f} 원\n"

        if holdings_info:
            message += "\n--- 보유 코인 (미실현 손익) ---\n"
            message += "\n".join(holdings_info)

        return message

    except Exception as e:
        logger.error(f"실제 투자 상태 조회 중 오류: {e}", exc_info=True)
        return f"실제 투자 상태를 불러오는 중 오류가 발생했습니다: {e}"


async def get_simulation_portfolio_status() -> str:
    """[모의 투자용] DB를 조회하여 현재 포트폴리오 상태를 반환합니다."""
    try:
        # 데이터베이스에 읽기 전용으로 안전하게 접속합니다.
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
            # 포트폴리오 상태 테이블을 읽어옵니다.
            df_state = pd.read_sql_query("SELECT * FROM paper_portfolio_state", conn)
            # 실현 손익을 계산하기 위해 'sell' 거래 기록만 읽어옵니다.
            df_trade_log = pd.read_sql_query("SELECT action, profit FROM paper_trade_log WHERE action = 'sell'", conn)

        # 데이터가 없을 경우 메시지를 반환하고 종료합니다.
        if df_state.empty:
            return "모의 투자 포트폴리오 데이터가 없습니다."

        # --- 지표 계산 시작 ---

        # 1. 모든 가상 지갑의 현금과 초기 투자금을 합산합니다.
        cash_balance = df_state['krw_balance'].sum()
        initial_capital_total = df_state['initial_capital'].sum()

        # 2. 모든 'sell' 거래의 'profit'을 합산하여 총 실현 손익을 계산합니다.
        total_realized_pnl = df_trade_log['profit'].sum() if not df_trade_log.empty else 0

        # 3. 보유 코인의 평가 가치와 미실현 손익을 계산합니다.
        total_asset_value = 0
        total_unrealized_pnl = 0
        holdings_info = []

        # 현재 자산 잔고가 0보다 큰 코인들만 필터링합니다.
        holding_states = df_state[df_state['asset_balance'] > 0]
        if not holding_states.empty:
            tickers = holding_states['ticker'].tolist()
            # 보유 코인들의 현재가를 한 번의 API 호출로 모두 가져옵니다.
            current_prices = pyupbit.get_current_price(tickers)

            # 각 코인별로 평가금액과 미실현 손익을 계산합니다.
            for _, row in holding_states.iterrows():
                # get_current_price는 코인이 하나일 때와 여러 개일 때 다른 형태로 결과를 주므로 분기 처리합니다.
                price = current_prices.get(row['ticker']) if isinstance(current_prices, dict) else current_prices
                if not price: continue

                eval_amount = row['asset_balance'] * price
                unrealized_pnl_per_ticker = (price - row['avg_buy_price']) * row['asset_balance']

                total_asset_value += eval_amount
                total_unrealized_pnl += unrealized_pnl_per_ticker

                roi = (price / row['avg_buy_price'] - 1) * 100 if row['avg_buy_price'] > 0 else 0
                holdings_info.append(f" - {row['ticker']}: {unrealized_pnl_per_ticker:,.0f}원 ({roi:.2f}%)")

        # 4. 최종 지표들을 조합합니다.
        total_portfolio_value = cash_balance + total_asset_value
        total_pnl = total_realized_pnl + total_unrealized_pnl
        total_roi = (total_pnl / initial_capital_total) * 100 if initial_capital_total > 0 else 0

        # --- 최종 메시지 조합 ---
        message = f"--- 📊 포트폴리오 현황 (모의 투자) ---\n"
        message += f"총 자산: {total_portfolio_value:,.0f} 원\n"
        message += f"총 손익 (실현+미실현): {total_pnl:,.0f} 원\n"
        message += f"   - 실현 손익: {total_realized_pnl:,.0f} 원\n"
        message += f"   - 미실현 손익: {total_unrealized_pnl:,.0f} 원\n"
        message += f"총 수익률: {total_roi:.2f}%\n"
        message += "---------------------\n"
        message += f"현금: {cash_balance:,.0f} 원\n"
        message += f"코인 평가액: {total_asset_value:,.0f} 원\n"

        if holdings_info:
            message += "\n--- 보유 코인 (미실현 손익) ---\n"
            message += "\n".join(holdings_info)

        return message

    except sqlite3.OperationalError as e:
        # DB 파일이 없거나 테이블이 없는 등 DB 관련 오류일 때 더 친절한 메시지를 보냅니다.
        logger.error(f"데이터베이스 조회 중 오류: {e}")
        return "데이터베이스에 아직 접근할 수 없거나, 거래 기록이 없습니다."
    except Exception as e:
        logger.error(f"상태 조회 중 알 수 없는 오류: {e}")
        return f"상태를 불러오는 중 알 수 없는 오류가 발생했습니다: {e}"


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status 명령어에 응답합니다."""
    await update.message.reply_text("잠시만요, 포트폴리오 상태를 조회하고 있습니다...")

    # ✨ 2. [핵심 수정] config.RUN_MODE에 따라 다른 함수를 호출합니다.
    if config.RUN_MODE == 'real':
        status_message = await get_real_portfolio_status()
    else:
        status_message = await get_simulation_portfolio_status()

    await update.message.reply_text(status_message)


def main() -> None:
    """텔레그램 봇을 시작합니다."""
    if not TOKEN:
        logger.error("텔레그램 봇 토큰이 환경 변수에 설정되지 않았습니다!")
        return

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("status", status_command))

    logger.info("텔레그램 봇이 메시지 수신을 시작합니다...")

    # 봇이 시작되었음을 텔레그램으로 알립니다.
    try:
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if chat_id:
            mode_text = "실제 투자" if config.RUN_MODE == 'real' else "모의 투자"
            application.bot.send_message(chat_id=chat_id, text=f"ℹ️ 포트폴리오 조회 봇이 [{mode_text}] 모드로 시작되었습니다.")
    except Exception as e:
        logger.warning(f"시작 알림 메시지 발송 실패: {e}")

    application.run_polling()


if __name__ == '__main__':
    main()
