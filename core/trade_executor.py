# core/trade_executor.py
# ⚡️ 최종 매매 결정을 실제 주문으로 실행하는 모듈입니다.
# 모의 투자와 실제 투자를 분기하여 처리합니다.

import logging
from datetime import datetime
import json

import config
# from .portfolio import PortfolioManager
# from apis.upbit_api import UpbitAPI

logger = logging.getLogger()


def determine_final_action(ensemble_signal, ai_decision, position, latest_data, ensemble_config):
    """
    앙상블 신호, AI 결정, 리스크 관리 규칙을 종합하여 최종 행동을 결정합니다.
    (autotrading.py의 get_trading_decision 함수 로직을 가져옴)

    Returns:
        tuple: (최종 결정(str), 거래 비율(float), 결정 이유(str))
    """
    current_price = latest_data['close']

    # 1. 리스크 관리 청산 조건 (가장 높은 우선순위)
    if position.get('asset_balance', 0) > 0:
        exit_params = ensemble_config.get('common_exit_params', {})
        should_sell, reason = False, ""

        # ATR 손절
        stop_loss_atr = exit_params.get('stop_loss_atr_multiplier')
        if stop_loss_atr and 'ATRr_14' in latest_data:
            stop_loss_price = position['avg_buy_price'] - (stop_loss_atr * latest_data['ATRr_14'])
            if current_price < stop_loss_price:
                should_sell, reason = True, f"ATR Stop-loss (Price < {stop_loss_price:,.0f})"

        # 트레일링 스탑
        trailing_stop = exit_params.get('trailing_stop_percent')
        if not should_sell and trailing_stop:
            trailing_price = position.get('highest_price_since_buy', 0) * (1 - trailing_stop)
            if current_price < trailing_price:
                should_sell, reason = True, f"Trailing Stop (Price < {trailing_price:,.0f})"

        if should_sell:
            logger.info(f"리스크 관리 규칙에 의해 매도 결정: {reason}")
            return 'sell', 1.0, reason  # 리스크 관리는 전량 매도

    # 2. AI와 앙상블 신호 조합
    oai_decision = ai_decision.get('decision', 'hold')
    oai_ratio = float(ai_decision.get('percentage', 0.0))
    oai_reason = ai_decision.get('reason', '')

    # 규칙 1: 둘 다 'buy' -> 매수
    if ensemble_signal == 'buy' and oai_decision == 'buy':
        return 'buy', oai_ratio if oai_ratio > 0 else 0.5, f"AI & Ensemble Agree [BUY]: {oai_reason}"

    # 규칙 2: 앙상블 'buy' vs AI 'sell' -> 소량 매수
    elif ensemble_signal == 'buy' and oai_decision == 'sell':
        return 'buy', 0.25, f"CONFLICT [Ensemble BUY vs AI SELL]: Cautious partial buy. AI: {oai_reason}"

    # 규칙 3: 둘 다 'sell' -> 매도
    elif ensemble_signal == 'sell' and oai_decision == 'sell':
        return 'sell', oai_ratio if oai_ratio > 0 else 1.0, f"AI & Ensemble Agree [SELL]: {oai_reason}"

    # 규칙 4: 그 외 모든 경우 -> 보류
    else:
        return 'hold', 0.0, f"No Consensus or Hold Signal. Ensemble: {ensemble_signal}, AI: {oai_decision}. AI Reason: {oai_reason}"


def execute_trade(decision: str, ratio: float, reason: str, portfolio_manager,
                  upbit_api_client):
    """
    결정된 행동을 실제 또는 모의 거래로 실행합니다.
    """
    mode_log = "실제" if config.RUN_MODE == 'real' else "모의"
    logger.info(f"--- [{mode_log} 거래 실행] 결정: {decision.upper()}, 비율: {ratio:.2%}, 이유: {reason} ---")

    context_json = json.dumps({"reason": reason})
    current_price = upbit_api_client.get_current_price(config.TICKER_TO_TRADE)
    if not current_price:
        logger.error("현재가 조회에 실패하여 거래를 실행할 수 없습니다.")
        return

    # 1. 'hold' 결정 처리

    if decision == 'hold':
        log_entry = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'action': 'hold', 'price': current_price,
            'amount': 0, 'krw_value': 0, 'fee': 0, 'context': context_json,
            'ticker': config.TICKER_TO_TRADE, 'reason': reason
        }
        portfolio_manager.log_trade(log_entry, is_real_trade=(config.RUN_MODE == 'real'))
        return

    # 2. 실제 거래 모드
    if config.RUN_MODE == 'real':
        position = portfolio_manager.get_current_position()
        log_entry_base = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'action': decision,
            'ticker': config.TICKER_TO_TRADE,
            'reason': reason,
            'context': context_json
        }

        if decision == 'buy' and position.get('krw_balance', 0) > config.MIN_ORDER_KRW:
            buy_krw = position['krw_balance'] * ratio
            response = upbit_api_client.buy_market_order(config.TICKER_TO_TRADE, buy_krw)
            if response:
                log_entry = {**log_entry_base, 'upbit_uuid': response.get('uuid'), 'krw_value': buy_krw,
                             'upbit_response': json.dumps(response)}
                portfolio_manager.log_trade(log_entry, is_real_trade=True)

        elif decision == 'sell' and position.get('asset_balance', 0) > 0:
            amount_to_sell = position['asset_balance'] * ratio
            response = upbit_api_client.sell_market_order(config.TICKER_TO_TRADE, amount_to_sell)
            if response:
                log_entry = {**log_entry_base, 'upbit_uuid': response.get('uuid'), 'amount': amount_to_sell,
                             'upbit_response': json.dumps(response)}
                portfolio_manager.log_trade(log_entry, is_real_trade=True)

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
            # 포트폴리오 상태 업데이트 및 로그 기록
            portfolio_manager.update_portfolio_on_trade(trade_result)
            portfolio_manager.log_trade({
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'context': context_json,
                **trade_result
            }, is_real_trade=False)