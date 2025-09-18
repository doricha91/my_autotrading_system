# run_telegram_bot.py (실행 시 설정 파일 선택 기능 추가)

import os
import logging
import sqlite3
import pandas as pd
import pyupbit
import argparse  # 1. argparse 임포트
import importlib  # 2. importlib 임포트
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ✨ config를 직접 임포트하는 대신, 나중에 동적으로 불러올 것이므로 관련 코드 일부 수정
from apis import upbit_api
from data import data_manager
from utils import indicators

# 로거 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 환경 변수 로드
TOKEN = os.getenv("TELEGRAM_TOKEN")


async def get_stop_loss_prices(config, ticker: str, avg_buy_price: float) -> dict:
    """주어진 티커의 최신 데이터를 기반으로 ATR 손절가를 계산합니다."""
    results = {'atr_stop': 0}
    if avg_buy_price == 0:
        return results

    try:
        # ✨ data_manager.load_prepared_data가 config를 받도록 수정
        df_raw = data_manager.load_prepared_data(config, ticker, config.TRADE_INTERVAL, for_bot=True)
        if not df_raw.empty:
            # ✨ 필요한 파라미터 목록을 config에서 직접 가져오도록 수정
            all_possible_params = [config.COMMON_EXIT_PARAMS]
            if hasattr(config, 'REGIME_STRATEGY_MAP'):
                all_possible_params.extend([s.get('params', {}) for s in config.REGIME_STRATEGY_MAP.values()])

            df_final = indicators.add_technical_indicators(df_raw, all_possible_params)

            latest_atr = df_final['ATR'].iloc[-1]
            atr_multiplier = config.COMMON_EXIT_PARAMS.get('stop_loss_atr_multiplier', 0)

            if latest_atr > 0 and atr_multiplier > 0:
                results['atr_stop'] = avg_buy_price - (latest_atr * atr_multiplier)
        return results
    except Exception as e:
        logger.error(f"[{ticker}] 손절가 계산 중 오류: {e}")
        return results


async def get_portfolio_status(config) -> str:
    """
    [통합 함수] 모드에 관계없이 설정 파일에 따라 포트폴리오 상태를 반환합니다.
    실제 투자 모드에서는 Upbit API와 DB를, 모의 투자에서는 DB만 조회합니다.
    """
    try:
        if config.RUN_MODE == 'real':
            # --- 실제 투자 모드 로직 ---
            upbit_client = upbit_api.UpbitAPI(config.UPBIT_ACCESS_KEY, config.UPBIT_SECRET_KEY)
            my_accounts = upbit_client.client.get_balances()
            if not my_accounts: return "Upbit 계좌 정보를 불러올 수 없습니다."

            cash_balance = next((float(acc['balance']) for acc in my_accounts if acc['currency'] == 'KRW'), 0)

            with sqlite3.connect(f"file:{config.LOG_DB_PATH}?mode=ro", uri=True) as conn:
                df_real_log = pd.read_sql_query("SELECT profit FROM real_trade_log WHERE action = 'sell'", conn)
            total_realized_pnl = df_real_log['profit'].sum() if not df_real_log.empty else 0

            # 보유 코인 정보 계산
            coins_held = [acc for acc in my_accounts if acc['currency'] != 'KRW']
            coin_tickers = [f"KRW-{acc['currency']}" for acc in coins_held]
            current_prices = pyupbit.get_current_price(coin_tickers) if coin_tickers else {}

            total_asset_value = 0
            total_buy_amount = 0
            holdings_info = []

            for acc in coins_held:
                ticker_id = f"KRW-{acc['currency']}"
                balance = float(acc['balance'])
                avg_buy_price = float(acc['avg_buy_price'])
                current_price = current_prices.get(ticker_id)
                if not current_price: continue

                eval_amount = balance * current_price
                buy_amount = balance * avg_buy_price
                pnl = eval_amount - buy_amount
                roi = (pnl / buy_amount) * 100 if buy_amount > 0 else 0

                total_asset_value += eval_amount
                total_buy_amount += buy_amount

                stop_prices = await get_stop_loss_prices(config, ticker_id, avg_buy_price)
                details_texts = [f"현재가: {current_price:,.0f}원", f"평단: {avg_buy_price:,.0f}원"]
                if stop_prices['atr_stop'] > 0: details_texts.append(f"ATR손절: {stop_prices['atr_stop']:,.0f}원")
                holdings_info.append(f" - {ticker_id}: {pnl:,.0f}원 ({roi:.2f}%) ({', '.join(details_texts)})")

            total_unrealized_pnl = total_asset_value - total_buy_amount
            total_portfolio_value = cash_balance + total_asset_value

            message = f"--- 📊 포트폴리오 현황 (실제 투자) ---\n"
            message += f"총 자산: {total_portfolio_value:,.0f} 원\n"
            message += f"총 손익 (실현+미실현): {total_realized_pnl + total_unrealized_pnl:,.0f} 원\n"
            message += "---------------------\n"

        else:
            # --- 모의 투자 모드 로직 ---
            with sqlite3.connect(f"file:{config.LOG_DB_PATH}?mode=ro", uri=True) as conn:
                df_state = pd.read_sql_query("SELECT * FROM paper_portfolio_state", conn)
                df_trade_log = pd.read_sql_query("SELECT action, profit FROM paper_trade_log WHERE action = 'sell'",
                                                 conn)
            if df_state.empty: return "모의 투자 포트폴리오 데이터가 없습니다."

            cash_balance = df_state['krw_balance'].sum()
            initial_capital_total = df_state['initial_capital'].sum()
            total_realized_pnl = df_trade_log['profit'].sum() if not df_trade_log.empty else 0

            # 보유 코인 정보 계산
            holding_states = df_state[df_state['asset_balance'] > 0]
            tickers_to_fetch = holding_states['ticker'].tolist()
            current_prices = pyupbit.get_current_price(tickers_to_fetch) if tickers_to_fetch else {}

            total_asset_value = 0
            total_unrealized_pnl = 0
            holdings_info = []

            for _, row in holding_states.iterrows():
                price = current_prices.get(row['ticker'], row['avg_buy_price'])
                eval_amount = row['asset_balance'] * price
                unrealized_pnl = (price - row['avg_buy_price']) * row['asset_balance']

                total_asset_value += eval_amount
                total_unrealized_pnl += unrealized_pnl
                roi = (unrealized_pnl / (row['avg_buy_price'] * row['asset_balance'])) * 100 if row[
                                                                                                    'avg_buy_price'] > 0 else 0

                stop_prices = await get_stop_loss_prices(config, row['ticker'], row['avg_buy_price'])
                details_texts = [f"현재가: {price:,.0f}원", f"평단: {row['avg_buy_price']:,.0f}원"]
                if stop_prices['atr_stop'] > 0: details_texts.append(f"ATR손절: {stop_prices['atr_stop']:,.0f}원")
                holdings_info.append(
                    f" - {row['ticker']}: {unrealized_pnl:,.0f}원 ({roi:.2f}%) ({', '.join(details_texts)})")

            total_portfolio_value = cash_balance + total_asset_value
            total_pnl = total_realized_pnl + total_unrealized_pnl
            total_roi = (total_pnl / initial_capital_total) * 100 if initial_capital_total > 0 else 0

            message = f"--- 📊 포트폴리오 현황 (모의 투자) ---\n"
            message += f"총 자산: {total_portfolio_value:,.0f} 원\n"
            message += f"총 손익: {total_pnl:,.0f} 원 ({total_roi:.2f}%)\n"
            message += "---------------------\n"

        # --- 공통 출력 부분 ---
        message += f"현금: {cash_balance:,.0f} 원\n"
        message += f"코인 평가액: {total_asset_value:,.0f} 원\n"
        if holdings_info:
            message += "\n--- 보유 코인 (미실현 손익) ---\n"
            message += "\n".join(holdings_info)

        return message

    except sqlite3.OperationalError as e:
        return f"데이터베이스 '{config.LOG_DB_PATH}'에 아직 테이블이 없거나 접근할 수 없습니다. create_tables.py를 실행했는지 확인해주세요."
    except Exception as e:
        logger.error(f"상태 조회 중 오류: {e}", exc_info=True)
        return f"상태를 불러오는 중 오류가 발생했습니다: {e}"


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status 명령어에 응답합니다."""
    # context.bot_data에서 설정 모듈을 가져옵니다.
    config = context.bot_data['config']

    await update.message.reply_text("잠시만요, 포트폴리오 상태를 조회하고 있습니다...")
    status_message = await get_portfolio_status(config)
    await update.message.reply_text(status_message)


def main() -> None:
    """텔레그램 봇을 시작합니다."""
    # 3. argparse를 사용하여 --config 인자 파싱
    parser = argparse.ArgumentParser(description="텔레그램 봇 실행 스크립트")
    parser.add_argument('--config', type=str, default='config', help="사용할 설정 파일 이름 (예: config_real)")
    args = parser.parse_args()

    try:
        # 4. --config 인자로 지정된 설정 파일을 동적으로 불러오기
        config_module = importlib.import_module(args.config)
        logger.info(f"✅ '{args.config}.py' 설정 파일을 성공적으로 불러왔습니다.")
    except ImportError:
        logger.error(f"❌ 지정된 설정 파일 '{args.config}.py'을(를) 찾을 수 없습니다. 프로그램을 종료합니다.")
        return

    if not TOKEN:
        logger.error("텔레그램 봇 토큰이 .env 파일에 설정되지 않았습니다!")
        return

    application = Application.builder().token(TOKEN).build()

    # 5. 불러온 설정 모듈을 봇의 context 데이터에 저장하여 모든 핸들러에서 접근 가능하게 함
    application.bot_data['config'] = config_module

    application.add_handler(CommandHandler("status", status_command))

    logger.info("텔레그램 봇이 메시지 수신을 시작합니다...")

    # 시작 알림 메시지 발송
    try:
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if chat_id:
            mode_text = "실제 투자" if config_module.RUN_MODE == 'real' else "모의 투자"
            start_message = f"ℹ️ 포트폴리오 조회 봇이 [{mode_text}] 모드로 시작되었습니다. (`{args.config}.py` 사용)"

            import asyncio
            # 비동기 함수를 안전하게 실행
            async def send_startup_message():
                await application.bot.send_message(chat_id=chat_id, text=start_message)

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(send_startup_message())
            except RuntimeError:  # No running loop
                asyncio.run(send_startup_message())

    except Exception as e:
        logger.warning(f"시작 알림 메시지 발송 실패: {e}")

    application.run_polling()


if __name__ == '__main__':
    main()