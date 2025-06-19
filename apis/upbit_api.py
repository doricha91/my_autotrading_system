# apis/upbit_api.py
# 🏦 Upbit 거래소와의 모든 통신을 책임지는 파일입니다.
# pyupbit 라이브러리를 감싸서 우리에게 필요한 기능만 노출시키고, 오류 처리를 추가합니다.

import pyupbit
import logging
import config

logger = logging.getLogger()


class UpbitAPI:
    def __init__(self, access_key: str, secret_key: str):
        """
        UpbitAPI 클래스 초기화
        - access_key와 secret_key가 있어야 실제 주문 관련 기능 사용 가능
        """
        if not access_key or not secret_key:
            logger.warning("API 키가 제공되지 않았습니다. 조회 기능만 사용 가능합니다.")
            self.client = None
        else:
            try:
                self.client = pyupbit.Upbit(access_key, secret_key)
                my_balance = self.client.get_balance("KRW")

                # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
                # 오류 해결: my_balance가 None이 아닌지 확인 후 로그 출력
                if my_balance is not None:
                    logger.info(f"✅ Upbit API 클라이언트 초기화 성공. KRW 잔고: {my_balance:,.0f} 원")
                else:
                    logger.warning("Upbit API 클라이언트 초기화는 되었으나, KRW 잔고를 가져올 수 없습니다. API 키가 유효한지 확인하세요.")
                # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

            except Exception as e:
                logger.error(f"❌ Upbit API 클라이언트 초기화 실패: {e}")
                self.client = None

    def get_current_price(self, ticker: str):
        """특정 티커의 현재가를 조회합니다."""
        try:
            return pyupbit.get_current_price(ticker)
        except Exception as e:
            logger.error(f"'{ticker}' 현재가 조회 중 오류 발생: {e}")
            return None

    def get_my_position(self, ticker: str):
        """내 계좌의 특정 티커 보유 현황과 KRW 잔고를 조회합니다."""
        if not self.client:
            return None

        position = {'asset_balance': 0.0, 'avg_buy_price': 0.0, 'krw_balance': 0.0}
        try:
            ticker_currency = ticker.split('-')[1]  # "KRW-BTC" -> "BTC"
            balances = self.client.get_balances()
            for b in balances:
                if b['currency'] == ticker_currency:
                    position['asset_balance'] = float(b['balance'])
                    position['avg_buy_price'] = float(b['avg_buy_price'])
                if b['currency'] == 'KRW':
                    position['krw_balance'] = float(b['balance'])
            return position
        except Exception as e:
            logger.error(f"계좌 정보 조회 중 오류 발생: {e}")
            return position

    def buy_market_order(self, ticker: str, price: float):
        """시장가 매수 주문을 실행합니다."""
        if not self.client:
            logger.error("실제 매수 불가: API 클라이언트가 초기화되지 않았습니다.")
            return None

        if price < config.MIN_ORDER_KRW:
            logger.warning(f"매수 금액({price:,.0f} KRW)이 최소 주문 금액({config.MIN_ORDER_KRW:,.0f} KRW)보다 작습니다.")
            return None

        try:
            logger.info(f"[실제 주문] 시장가 매수 시도: {ticker}, {price:,.0f} KRW")
            response = self.client.buy_market_order(ticker, price)
            logger.info(f"Upbit 매수 API 응답: {response}")
            return response
        except Exception as e:
            logger.error(f"시장가 매수 주문 중 오류 발생: {e}")
            return None

    def sell_market_order(self, ticker: str, volume: float):
        """시장가 매도 주문을 실행합니다."""
        if not self.client:
            logger.error("실제 매도 불가: API 클라이언트가 초기화되지 않았습니다.")
            return None

        try:
            logger.info(f"[실제 주문] 시장가 매도 시도: {ticker}, 수량: {volume:.8f}")
            response = self.client.sell_market_order(ticker, volume)
            logger.info(f"Upbit 매도 API 응답: {response}")
            return response
        except Exception as e:
            logger.error(f"시장가 매도 주문 중 오류 발생: {e}")
            return None