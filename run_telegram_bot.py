# run_telegram_bot.py (최종 수정본 - ATR 및 이동 손절가 모두 포함)

import os
import logging
import sqlite3
import pandas as pd
import pyupbit
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import config
from apis import upbit_api
from data import data_manager
from utils import indicators

# 로거 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 환경 변수 및 상수
TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_PATH = config.LOG_DB_PATH


# ✨ 1. [함수 기능 확장] ATR 손절가와 이동 손절가를 모두 계산하는 헬퍼 함수
async def get_stop_loss_prices(ticker: str, avg_buy_price: float, highest_price: float) -> dict:
    """주어진 티커의 최신 데이터를 기반으로 모든 종류의 손절가를 계산합니다."""
    results = {
        'atr_stop': 0,
        'trailing_stop': 0
    }
    if avg_buy_price == 0:
        return results

    try:
        # --- ATR 손절가 계산 ---
        df_raw = data_manager.load_prepared_data(ticker, config.TRADE_INTERVAL, for_bot=True)
        if not df_raw.empty:
            all_possible_params = [s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()]
            all_possible_params.append(config.COMMON_EXIT_PARAMS)
            df_final = indicators.add_technical_indicators(df_raw, all_possible_params)

            latest_atr = df_final['ATR'].iloc[-1]
            atr_multiplier = config.COMMON_EXIT_PARAMS.get('stop_loss_atr_multiplier', 0)

            if latest_atr > 0 and atr_multiplier > 0:
                results['atr_stop'] = avg_buy_price - (latest_atr * atr_multiplier)

        # --- 이동 손절가(Trailing Stop) 계산 ---
        trailing_percent = config.COMMON_EXIT_PARAMS.get('trailing_stop_percent', 0)
        if highest_price > 0 and trailing_percent > 0:
            results['trailing_stop'] = highest_price * (1 - trailing_percent)

        return results

    except Exception as e:
        logger.error(f"[{ticker}] 손절가 계산 중 오류: {e}")
        return results


async def get_real_portfolio_status() -> str:
    """[실제 투자용] Upbit API와 DB를 조회하여 현재 포트폴리오 상태를 반환합니다."""
    try:
        upbit_client = upbit_api.UpbitAPI(config.UPBIT_ACCESS_KEY, config.UPBIT_SECRET_KEY)
        my_accounts = upbit_client.client.get_balances()

        if not my_accounts:
            return "Upbit 계좌 정보를 불러올 수 없습니다."

        cash_balance = 0
        total_asset_value = 0
        total_buy_amount = 0
        holdings_info = []

        for acc in my_accounts:
            currency = acc['currency']
            balance = float(acc['balance'])
            avg_buy_price = float(acc['avg_buy_price'])

            if currency == "KRW":
                cash_balance = balance
                continue

            ticker_id = f"KRW-{currency}"
            current_price = pyupbit.get_current_price(ticker_id)
            if not current_price: continue

            eval_amount = balance * current_price
            buy_amount = balance * avg_buy_price
            pnl = eval_amount - buy_amount
            roi = (pnl / buy_amount) * 100 if buy_amount > 0 else 0

            total_asset_value += eval_amount
            total_buy_amount += buy_amount

            # ✨ 2. [실제 투자] 손절가 계산 (이동 손절은 highest_price가 없어 0으로 처리)
            stop_prices = await get_stop_loss_prices(ticker_id, avg_buy_price, highest_price=0)
            sl_texts = []
            if stop_prices['atr_stop'] > 0:
                sl_texts.append(f"ATR손절: {stop_prices['atr_stop']:,.0f}원")

            stop_loss_text = " (" + ", ".join(sl_texts) + ")" if sl_texts else ""
            holdings_info.append(f" - {ticker_id}: {pnl:,.0f}원 ({roi:.2f}%){stop_loss_text}")

        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
            df_real_log = pd.read_sql_query("SELECT profit FROM real_trade_log WHERE action = 'sell'", conn)

        total_realized_pnl = df_real_log['profit'].sum() if not df_real_log.empty else 0
        total_unrealized_pnl = total_asset_value - total_buy_amount
        total_portfolio_value = cash_balance + total_asset_value

        message = f"--- 📊 포트폴리오 현황 (실제 투자) ---\n"
        message += f"총 자산: {total_portfolio_value:,.0f} 원\n"
        message += f"총 손익 (실현+미실현): {total_realized_pnl + total_unrealized_pnl:,.0f} 원\n"
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
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
            df_state = pd.read_sql_query("SELECT * FROM paper_portfolio_state", conn)
            df_trade_log = pd.read_sql_query("SELECT action, profit FROM paper_trade_log WHERE action = 'sell'", conn)

        if df_state.empty:
            return "모의 투자 포트폴리오 데이터가 없습니다."

        cash_balance = df_state['krw_balance'].sum()
        initial_capital_total = df_state['initial_capital'].sum()
        total_realized_pnl = df_trade_log['profit'].sum() if not df_trade_log.empty else 0

        total_asset_value = 0
        total_unrealized_pnl = 0
        holdings_info = []

        holding_states = df_state[df_state['asset_balance'] > 0]
        if not holding_states.empty:
            tickers = holding_states['ticker'].tolist()
            current_prices = pyupbit.get_current_price(tickers)

            for _, row in holding_states.iterrows():
                price = current_prices.get(row['ticker']) if isinstance(current_prices, dict) else current_prices
                if not price: continue

                eval_amount = row['asset_balance'] * price
                unrealized_pnl_per_ticker = (price - row['avg_buy_price']) * row['asset_balance']
                total_asset_value += eval_amount
                total_unrealized_pnl += unrealized_pnl_per_ticker
                roi = (price / row['avg_buy_price'] - 1) * 100 if row['avg_buy_price'] > 0 else 0

                # ✨ 3. [모의 투자] 모든 종류의 손절가를 계산합니다.
                stop_prices = await get_stop_loss_prices(row['ticker'], row['avg_buy_price'],
                                                         row['highest_price_since_buy'])
                sl_texts = []
                if stop_prices['atr_stop'] > 0:
                    sl_texts.append(f"ATR손절: {stop_prices['atr_stop']:,.0f}원")
                if stop_prices['trailing_stop'] > 0:
                    sl_texts.append(f"이동손절: {stop_prices['trailing_stop']:,.0f}원")

                stop_loss_text = " (" + ", ".join(sl_texts) + ")" if sl_texts else ""
                holdings_info.append(
                    f" - {row['ticker']}: {unrealized_pnl_per_ticker:,.0f}원 ({roi:.2f}%){stop_loss_text}")

        total_portfolio_value = cash_balance + total_asset_value
        total_pnl = total_realized_pnl + total_unrealized_pnl
        total_roi = (total_pnl / initial_capital_total) * 100 if initial_capital_total > 0 else 0

        message = f"--- 📊 포트폴리오 현황 (모의 투자) ---\n"
        message += f"총 자산: {total_portfolio_value:,.0f} 원\n"
        message += f"총 손익: {total_pnl:,.0f} 원 ({total_roi:.2f}%)\n"
        message += "---------------------\n"
        message += f"현금: {cash_balance:,.0f} 원\n"
        message += f"코인 평가액: {total_asset_value:,.0f} 원\n"

        if holdings_info:
            message += "\n--- 보유 코인 (미실현 손익) ---\n"
            message += "\n".join(holdings_info)

        return message

    except sqlite3.OperationalError as e:
        logger.error(f"데이터베이스 조회 중 오류: {e}")
        return "데이터베이스에 아직 접근할 수 없거나, 거래 기록이 없습니다."
    except Exception as e:
        logger.error(f"상태 조회 중 알 수 없는 오류: {e}")
        return f"상태를 불러오는 중 알 수 없는 오류가 발생했습니다: {e}"


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status 명령어에 응답합니다."""
    await update.message.reply_text("잠시만요, 포트폴리오 상태를 조회하고 있습니다...")

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

    # Use run_until_complete for async operation in sync function
    import asyncio
    try:
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if chat_id:
            mode_text = "실제 투자" if config.RUN_MODE == 'real' else "모의 투자"
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(
                    application.bot.send_message(chat_id=chat_id, text=f"ℹ️ 포트폴리오 조회 봇이 [{mode_text}] 모드로 시작되었습니다."))
            else:
                loop.run_until_complete(
                    application.bot.send_message(chat_id=chat_id, text=f"ℹ️ 포트폴리오 조회 봇이 [{mode_text}] 모드로 시작되었습니다."))
    except Exception as e:
        logger.warning(f"시작 알림 메시지 발송 실패: {e}")

    application.run_polling()


if __name__ == '__main__':
    main()