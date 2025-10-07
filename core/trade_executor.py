# core/trade_executor.py
# ⚡️ 최종 매매 결정을 실제 주문으로 실행하는 모듈입니다.
# 모의 투자와 실제 투자를 분기하여 처리합니다.

import logging
import sqlite3
import pandas as pd
from datetime import datetime
import json
from utils import notifier

logger = logging.getLogger()

# --- ✨ 1. 신규 함수: 모든 최종 판단을 'decision_log'에 기록 ---
def log_final_decision(config, decision: str, reason: str, ticker: str, price_at_decision: float):
    """
    봇의 모든 최종 판단(buy, sell, hold)을 'decision_log' 테이블에 기록합니다.
    이 함수는 거래 실행 여부와 관계없이 항상 호출됩니다.
    """
    try:
        with sqlite3.connect(config.LOG_DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO decision_log (timestamp, ticker, decision, reason, price_at_decision)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    ticker,
                    decision,
                    reason,
                    price_at_decision
                )
            )
            conn.commit()
            logger.info(f"[{ticker}] 최종 판단 '{decision.upper()}'을(를) decision_log에 기록했습니다.")
    except Exception as e:
        logger.error(f"[{ticker}] decision_log 기록 중 오류 발생: {e}", exc_info=True)

def check_fast_exit_conditions(position: dict, current_price: float, latest_data: dict, exit_params: dict, highest_price_from_db: float = 0.0) -> (bool, str):
    """
    ✨ [신규 함수] ✨
    빠른 청산 조건(손절, 트레일링 스탑)만 확인하여 즉각적인 반응을 처리합니다.
    이 함수는 가벼워서 빠른 루프 안에서 반복적으로 호출될 수 있습니다.

    :param position: 현재 포지션 정보
    :param current_price: 실시간으로 조회된 현재 가격
    :param latest_data: ATR 등 보조지표가 포함된 최신 데이터 행
    :param exit_params: 손절 및 트레일링 스탑 설정값
    :return: (매도 여부, 매도 사유) 튜플
    """
    # ATR 손절
    stop_loss_atr = exit_params.get('stop_loss_atr_multiplier')
    if stop_loss_atr and 'ATR' in latest_data and not pd.isna(latest_data['ATR']):
        # 진입 시점의 ATR 대신, 항상 최신 데이터의 ATR 값을 사용합니다.
        current_atr = latest_data['ATR']
        stop_loss_price = position['avg_buy_price'] - (stop_loss_atr * current_atr)
        if current_price < stop_loss_price:
            return True, f"ATR Stop-loss (Price < {stop_loss_price:,.0f})"

    # 트레일링 스탑
    trailing_stop = exit_params.get('trailing_stop_percent')
    if trailing_stop:
        # 모의투자는 position 딕셔너리에서, 실제투자는 DB에서 직접 읽어온 값을 사용
        highest_price = position.get('highest_price_since_buy', highest_price_from_db)

        if highest_price > 0:
            trailing_price = highest_price * (1 - trailing_stop)
            if current_price < trailing_price:
                return True, f"Trailing Stop (Price < {trailing_price:,.0f})"

    return False, ""


def determine_final_action(ensemble_signal, ai_decision, position, latest_data, ensemble_config):
    """
    앙상블 신호, AI 결정, 리스크 관리 규칙을 종합하여 최종 행동을 결정합니다.
    """

    # 1. 리스크 관리 청산 조건 (느린 루프에서 한 번만 확인)
    # ✨ [수정] ✨ 새로 만든 check_fast_exit_conditions 함수를 호출하여 중복을 제거합니다.
    if position.get('asset_balance', 0) > 0:
        exit_params = ensemble_config.get('common_exit_params', {})
        should_sell, reason = check_fast_exit_conditions(position, latest_data['close'], latest_data, exit_params)
        if should_sell:
            logger.info(f"리스크 관리 규칙에 의해 매도 결정: {reason}")
            return 'sell', 1.0, reason

    # 2. AI와 앙상블 신호 조합 (기존 로직과 동일)
    oai_decision = ai_decision.get('decision', 'hold')
    oai_ratio = float(ai_decision.get('percentage', 0.0))
    oai_reason = ai_decision.get('reason', '')

    if ensemble_signal == 'buy' and oai_decision == 'buy':
        return 'buy', oai_ratio if oai_ratio > 0 else 0.5, f"AI & Ensemble Agree [BUY]: {oai_reason}"
    elif ensemble_signal == 'buy' and oai_decision == 'sell':
        return 'buy', 0.25, f"CONFLICT [Ensemble BUY vs AI SELL]: Cautious partial buy. AI: {oai_reason}"
    elif ensemble_signal == 'sell' and oai_decision == 'sell':
        return 'sell', oai_ratio if oai_ratio > 0 else 1.0, f"AI & Ensemble Agree [SELL]: {oai_reason}"
        # 전략 신호는 'BUY'인데 AI가 'HOLD'로 판단한 'missed_opportunity' 상황
    elif ensemble_signal == 'buy' and oai_decision == 'hold':
        # 기회를 완전히 놓치는 대신, 평소의 20%만 '탐색 매수'를 시도
        return 'buy', 0.2, f"Scout Buy [Ensemble BUY vs AI HOLD]: Cautious partial buy. AI: {oai_reason}"
    else:
        return 'hold', 0.0, f"No Consensus or Hold Signal. Ensemble: {ensemble_signal}, AI: {oai_decision}. AI Reason: {oai_reason}"


# --- ✨✨✨ 핵심 수정 부분 (trade_executor.py) ✨✨✨ ---
def execute_trade(config, decision: str, ratio: float, reason: str, ticker: str, portfolio_manager, upbit_api_client, current_price: float):
    """
    'buy' 또는 'sell' 결정을 실제 또는 모의 거래로 실행합니다.
    실제/모의 투자 모두 공통된 알림 및 로깅 로직을 사용하도록 통일합니다.
    """
    if decision == 'hold':
        return

    mode_log = "실제" if config.RUN_MODE == 'real' else "모의"
    logger.info(f"--- [{mode_log} 거래 실행] 결정: {decision.upper()}, 비율: {ratio:.2%}, 이유: {reason} ---")

    # current_price = upbit_api_client.get_current_price(ticker)
    if not current_price:
        error_msg = f"[{ticker}] 현재가 조회에 실패하여 거래를 실행할 수 없습니다."
        logger.error(error_msg)
        notifier.send_telegram_message(f"🚨 시스템 경고: {error_msg}")
        return

    context_json = json.dumps({"reason": reason})
    trade_result = None
    position = portfolio_manager.get_current_position()

    # --- 1. 거래 실행 및 결과 생성 ---
    if decision == 'buy' and position.get('krw_balance', 0) > config.MIN_ORDER_KRW:
        capital_per_trade = position['krw_balance'] / config.MAX_CONCURRENT_TRADES
        buy_krw = capital_per_trade * ratio

        if buy_krw < config.MIN_ORDER_KRW:
            logger.warning(
                f"[{ticker}] 계산된 주문액({buy_krw:,.0f}원)이 최소 주문액({config.MIN_ORDER_KRW:,.0f}원)보다 작아 주문을 실행하지 않습니다.")
            return

        response = upbit_api_client.buy_market_order(ticker, buy_krw) if config.RUN_MODE == 'real' else {'status': 'ok'}
        if response:
            fee = buy_krw * config.FEE_RATE
            amount = (buy_krw - fee) / current_price
            trade_result = {'action': 'buy', 'price': current_price, 'amount': amount, 'krw_value': buy_krw, 'fee': fee,
                            'profit': None}
            if config.RUN_MODE == 'real':
                initial_state = {
                    'ticker': ticker,
                    'highest_price_since_buy': current_price  # 매수 가격을 초기 최고가로 설정
                }
                portfolio_manager.db_manager.save_real_portfolio_state(initial_state)
                logger.info(f"✅ [{ticker}] 신규 매수에 따라 'real_portfolio_state'에 초기 상태를 기록했습니다.")

    elif decision == 'sell' and position.get('asset_balance', 0) > 0:
        amount_to_sell = position['asset_balance'] * ratio

        response = upbit_api_client.sell_market_order(ticker, amount_to_sell) if config.RUN_MODE == 'real' else {
            'status': 'ok'}
        if response:
            sell_krw = amount_to_sell * current_price
            fee = sell_krw * config.FEE_RATE
            avg_buy_price = position.get('avg_buy_price', 0)
            profit = (current_price - avg_buy_price) * amount_to_sell - fee if avg_buy_price > 0 else 0
            trade_result = {'action': 'sell', 'price': current_price, 'amount': amount_to_sell, 'krw_value': sell_krw,
                            'fee': fee, 'profit': profit}

    # --- 2. 최종 결과 처리 (공통 로직) ---
    if trade_result:
        # 모의 투자일 경우에만 포트폴리오 상태를 직접 업데이트
        if config.RUN_MODE == 'simulation':
            portfolio_manager.update_portfolio_on_trade(trade_result)

        # 텔레그램 알림 발송
        trade_alert = f"--- ⚙️ [{mode_log}] 주문 실행 완료 ---\n"
        trade_alert += f"코인: {ticker}\n"
        trade_alert += f"종류: {trade_result['action'].upper()} (사유: {reason})\n"
        trade_alert += f"가격: {trade_result['price']:,.0f} KRW\n"
        trade_alert += f"수량: {trade_result['amount']:.4f}"
        if trade_result['action'] == 'sell' and trade_result['profit'] is not None:
            profit_str = f"+{trade_result['profit']:,.0f}" if trade_result[
                                                                  'profit'] > 0 else f"{trade_result['profit']:,.0f}"
            trade_alert += f"\n손익: {profit_str} 원"

        notifier.send_telegram_message(trade_alert)

        # DB에 로그 기록
        is_real = config.RUN_MODE == 'real'
        log_entry_data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'context': context_json,
            'ticker': ticker,
            **trade_result
        }
        # 실제 투자 로그에 필요한 추가 정보
        if is_real:
            log_entry_data['upbit_uuid'] = response.get('uuid') if isinstance(response, dict) else None
            log_entry_data['upbit_response'] = json.dumps(response) if isinstance(response, dict) else None
            log_entry_data['reason'] = reason

        portfolio_manager.log_trade(log_entry_data, is_real_trade=is_real)