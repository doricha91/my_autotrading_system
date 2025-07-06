# core/trade_executor.py
# ⚡️ 최종 매매 결정을 실제 주문으로 실행하는 모듈입니다.
# 모의 투자와 실제 투자를 분기하여 처리합니다.

import logging
from datetime import datetime
import json

import config

logger = logging.getLogger()


def check_fast_exit_conditions(position: dict, current_price: float, latest_data: dict, exit_params: dict) -> (bool, str):
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
    if stop_loss_atr and 'ATRr_14' in latest_data:
        # ✨ 참고: entry_atr을 사용하려면 position에 저장되어 있어야 합니다. 없다면 최신 ATR을 사용합니다.
        entry_atr = position.get('entry_atr', latest_data['ATRr_14'])
        stop_loss_price = position['avg_buy_price'] - (stop_loss_atr * entry_atr)
        if current_price < stop_loss_price:
            return True, f"ATR Stop-loss (Price < {stop_loss_price:,.0f})"

    # 트레일링 스탑
    trailing_stop = exit_params.get('trailing_stop_percent')
    if trailing_stop:
        # ✨ 중요: 이 로직이 잘 동작하려면 portfolio_manager가 'highest_price_since_buy'를
        #    실시간으로 업데이트 해주어야 합니다.
        highest_price = position.get('highest_price_since_buy', 0)
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
    else:
        return 'hold', 0.0, f"No Consensus or Hold Signal. Ensemble: {ensemble_signal}, AI: {oai_decision}. AI Reason: {oai_reason}"


# --- ✨✨✨ 핵심 수정 부분 (trade_executor.py) ✨✨✨ ---
def execute_trade(decision: str, ratio: float, reason: str, ticker: str, portfolio_manager,
                  upbit_api_client):
    """
    결정된 행동을 실제 또는 모의 거래로 실행합니다.
    [수정] config.TICKER_TO_TRADE 대신 'ticker'를 인자로 직접 받습니다.
    """
    mode_log = "실제" if config.RUN_MODE == 'real' else "모의"
    logger.info(f"--- [{mode_log} 거래 실행] 결정: {decision.upper()}, 비율: {ratio:.2%}, 이유: {reason} ---")

    context_json = json.dumps({"reason": reason})
    # [수정] config.TICKER_TO_TRADE 대신 인자로 받은 ticker 사용
    current_price = upbit_api_client.get_current_price(ticker)
    if not current_price:
        logger.error(f"[{ticker}] 현재가 조회에 실패하여 거래를 실행할 수 없습니다.")
        return

    # 1. 'hold' 결정 처리
    if decision == 'hold':
        log_entry = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'action': 'hold', 'price': current_price,
            'amount': 0, 'krw_value': 0, 'fee': 0, 'context': context_json,
            'ticker': ticker, # [수정]
            'reason': reason
        }
        # [수정] portfolio_manager는 이미 ticker를 알고 있으므로, log_trade 호출 방식은 그대로 유지
        portfolio_manager.log_trade(log_entry)
        return

    # 2. 실제 거래 모드
    if config.RUN_MODE == 'real':
        position = portfolio_manager.get_current_position()
        log_entry_base = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'action': decision,
            'ticker': ticker, # [수정]
            'reason': reason,
            'context': context_json
        }

        if decision == 'buy' and position.get('krw_balance', 0) > config.MIN_ORDER_KRW:
            buy_krw = position['krw_balance'] * ratio
            response = upbit_api_client.buy_market_order(ticker, buy_krw) # [수정]
            if response:
                log_entry = {**log_entry_base, 'upbit_uuid': response.get('uuid'), 'krw_value': buy_krw,
                             'upbit_response': json.dumps(response)}
                portfolio_manager.log_trade(log_entry)

        elif decision == 'sell' and position.get('asset_balance', 0) > 0:
            amount_to_sell = position['asset_balance'] * ratio
            response = upbit_api_client.sell_market_order(ticker, amount_to_sell) # [수정]
            if response:
                log_entry = {**log_entry_base, 'upbit_uuid': response.get('uuid'), 'amount': amount_to_sell,
                             'upbit_response': json.dumps(response)}
                portfolio_manager.log_trade(log_entry)

    # 3. 모의 투자 모드
    else:
        portfolio_state = portfolio_manager.get_current_position()
        trade_result = None

        if decision == 'buy' and portfolio_state.get('krw_balance', 0) > config.MIN_ORDER_KRW:
            buy_krw = portfolio_state['krw_balance'] * ratio
            fee = buy_krw * config.FEE_RATE
            amount = (buy_krw - fee) / current_price
            trade_result = {'action': 'buy', 'price': current_price, 'amount': amount, 'krw_value': buy_krw, 'fee': fee}

        elif decision == 'sell' and portfolio_state.get('asset_balance', 0) > 0:
            amount_to_sell = portfolio_state['asset_balance'] * ratio
            sell_krw = amount_to_sell * current_price
            fee = sell_krw * config.FEE_RATE
            trade_result = {'action': 'sell', 'price': current_price, 'amount': amount_to_sell, 'krw_value': sell_krw,
                            'fee': fee}

        if trade_result:
            portfolio_manager.update_portfolio_on_trade(trade_result)
            portfolio_manager.log_trade({
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'context': context_json,
                **trade_result
            })