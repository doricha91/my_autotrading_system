# apis/binance_api.py

import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException
import config  # 설정 파일 임포트

# 로거 설정
logger = logging.getLogger(__name__)


class BinanceAPI:
    """
    바이낸스 API와의 통신을 담당하는 클래스입니다.
    기존 UpbitAPI 클래스와 메서드 인터페이스를 유사하게 맞춰
    다른 모듈에서의 교체 사용을 용이하게 합니다.
    """

    def __init__(self, api_key: str, api_secret: str, is_testnet: bool = False):
        """
        BinanceAPI 클래스 초기화
        :param api_key: 바이낸스 API 키
        :param api_secret: 바이낸스 API 시크릿 키
        :param is_testnet: 테스트넷 사용 여부 (기본값: False)
        """
        if not api_key or not api_secret:
            logger.warning("Binance API 키가 제공되지 않았습니다. 조회 기능만 사용 가능합니다.")
            self.client = Client()  # 인증 없이 Public API만 사용하는 클라이언트
        else:
            try:
                self.client = Client(api_key, api_secret, testnet=is_testnet)
                # 계정 정보 조회를 통해 API 키 유효성 검증
                self.client.get_account()
                logger.info("✅ Binance API 클라이언트 초기화 및 계정 인증 성공.")
            except (BinanceAPIException, BinanceRequestException) as e:
                logger.error(f"❌ Binance API 클라이언트 초기화 실패: {e}")
                logger.error("API 키가 유효한지, IP 제한 설정이 올바른지 확인해주세요.")
                self.client = None
            except Exception as e:
                logger.error(f"❌ 예상치 못한 오류로 Binance API 클라이언트 초기화 실패: {e}")
                self.client = None

    def _format_ticker(self, ticker: str) -> str:
        """
        'BTC-USDT' 형식의 티커를 바이낸스 API 형식인 'BTCUSDT'로 변환합니다.
        :param ticker: '기준코인-쿼트코인' 형식의 티커 (e.g., 'BTC-USDT')
        :return: 'BTCUSDT' 형식의 심볼
        """
        return ticker.replace('-', '')

    def get_current_price(self, ticker: str) -> float | None:
        """
        지정된 티커의 현재 가격을 조회합니다.
        :param ticker: 'BTC-USDT' 형식의 티커
        :return: 현재 가격 (float) 또는 실패 시 None
        """
        symbol = self._format_ticker(ticker)
        try:
            ticker_info = self.client.get_symbol_ticker(symbol=symbol)
            return float(ticker_info['price'])
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"'{symbol}' 현재가 조회 중 API 오류 발생: {e}")
            return None
        except Exception as e:
            logger.error(f"'{symbol}' 현재가 조회 중 예상치 못한 오류 발생: {e}")
            return None

    def get_balance(self, currency: str = 'USDT') -> float:
        """
        지정된 화폐의 사용 가능한 잔고를 조회합니다.
        :param currency: 잔고를 조회할 화폐의 심볼 (e.g., 'USDT', 'BTC')
        :return: 사용 가능한 잔고 (float)
        """
        if self.client is None:
            logger.warning("API 클라이언트가 초기화되지 않아 잔고를 조회할 수 없습니다.")
            return 0.0

        try:
            balance_info = self.client.get_asset_balance(asset=currency)
            return float(balance_info['free']) if balance_info else 0.0
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"'{currency}' 잔고 조회 중 API 오류 발생: {e}")
            return 0.0
        except Exception as e:
            logger.error(f"'{currency}' 잔고 조회 중 예상치 못한 오류 발생: {e}")
            return 0.0

    def get_my_position(self, ticker: str) -> dict | None:
        """
        (구현이 복잡하여 예시로 제공)
        지정된 티커의 평균 매수 단가와 보유 수량을 조회합니다.
        pyupbit의 get_avg_buy_price와 달리, 바이낸스는 이 기능을 직접 제공하지 않아
        거래 내역(my_trades)을 모두 조회하여 직접 계산해야 합니다.
        이는 요청 수 제한(Rate Limit)에 영향을 줄 수 있어 신중한 접근이 필요합니다.

        NOTE: 실제 프로덕션 환경에서는 DB에 매수내역을 기록하고,
              이를 기반으로 평단가를 계산하는 것이 훨씬 효율적이고 안정적입니다.
              기존 프로젝트의 PortfolioManager가 이 역할을 하므로,
              API 모듈에서는 보유 '수량'만 정확히 가져오는 것이 더 나은 설계일 수 있습니다.

        :param ticker: 'BTC-USDT' 형식의 티커
        :return: {'avg_buy_price': 평단가, 'balance': 보유수량} 또는 None
        """
        base_currency, _ = ticker.split('-')
        balance = self.get_balance(currency=base_currency)

        if balance > 0:
            # 포트폴리오 관리는 PortfolioManager에서 담당하므로, 여기서는 현재 보유 수량만 반환.
            # 평단가는 PortfolioManager가 DB를 통해 계산하도록 설계.
            return {
                'avg_buy_price': 0,  # PortfolioManager가 계산해야 할 값
                'balance': balance
            }
        return None

    def buy_market_order(self, ticker: str, quote_order_qty: float) -> dict | None:
        """
        시장가 매수를 실행합니다. 바이낸스는 수량(quantity) 또는 주문 총액(quoteOrderQty)으로 주문 가능.
        UpbitAPI와의 호환성을 위해 주문 총액(USDT 기준)으로 매수합니다.
        :param ticker: 'BTC-USDT' 형식의 티커
        :param quote_order_qty: 주문할 총액 (e.g., 100 USDT)
        :return: 주문 결과 딕셔너리 또는 실패 시 None
        """
        if self.client is None:
            logger.error("API 클라이언트가 초기화되지 않아 매수 주문을 실행할 수 없습니다.")
            return None

        symbol = self._format_ticker(ticker)
        try:
            logger.info(f"[매수 주문] 티커: {symbol}, 주문액: {quote_order_qty} USDT")
            # quoteOrderQty를 사용하여 USDT 금액만큼 시장가 매수
            order = self.client.order_market_buy(
                symbol=symbol,
                quoteOrderQty=quote_order_qty
            )
            logger.info(f"✅ [매수 성공] 주문 ID: {order.get('orderId')}")
            return order
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"'{symbol}' 시장가 매수 주문 중 API 오류 발생: {e}")
            return None
        except Exception as e:
            logger.error(f"'{symbol}' 시장가 매수 주문 중 예상치 못한 오류 발생: {e}")
            return None

    def sell_market_order(self, ticker: str, quantity: float) -> dict | None:
        """
        시장가 매도를 실행합니다.
        :param ticker: 'BTC-USDT' 형식의 티커
        :param quantity: 매도할 수량 (e.g., 0.1 BTC)
        :return: 주문 결과 딕셔너리 또는 실패 시 None
        """
        if self.client is None:
            logger.error("API 클라이언트가 초기화되지 않아 매도 주문을 실행할 수 없습니다.")
            return None

        symbol = self._format_ticker(ticker)
        try:
            # 매도 주문 전, 해당 자산의 소수점 정밀도 확인 (필수)
            info = self.client.get_symbol_info(symbol)
            step_size = float(info['filters'][1]['stepSize'])  # LOT_SIZE 필터
            precision = int(round(-math.log(step_size, 10), 0))

            # 정밀도에 맞춰 수량 포맷팅
            formatted_quantity = f"{quantity:.{precision}f}"
            logger.info(f"[매도 주문] 티커: {symbol}, 주문 수량(원본): {quantity}, 주문 수량(조정): {formatted_quantity}")

            order = self.client.order_market_sell(
                symbol=symbol,
                quantity=float(formatted_quantity)
            )
            logger.info(f"✅ [매도 성공] 주문 ID: {order.get('orderId')}")
            return order
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"'{symbol}' 시장가 매도 주문 중 API 오류 발생: {e}")
            return None
        except Exception as e:
            logger.error(f"'{symbol}' 시장가 매도 주문 중 예상치 못한 오류 발생: {e}")
            return None


# 아래는 이 파일을 직접 실행했을 때 테스트를 위한 코드입니다.
# 실제 시스템에서는 import 되어 클래스만 사용됩니다.
if __name__ == '__main__':
    # 로깅 기본 설정 (테스트용)
    logging.basicConfig(level=logging.INFO)

    # config.py 에 BINANCE_API_KEY와 BINANCE_SECRET_KEY가 설정되어 있어야 합니다.
    # 테스트넷을 사용하려면 is_testnet=True 로 설정
    binance = BinanceAPI(api_key=config.BINANCE_API_KEY, api_secret=config.BINANCE_SECRET_KEY, is_testnet=False)

    if binance.client:
        # --- 기능 테스트 ---
        # 1. 현재가 조회
        btc_price = binance.get_current_price('BTC-USDT')
        if btc_price:
            logger.info(f"BTC/USDT 현재가: {btc_price}")

        # 2. 잔고 조회
        usdt_balance = binance.get_balance('USDT')
        logger.info(f"사용 가능 USDT 잔고: {usdt_balance}")

        # 3. 매수/매도 주문 테스트 (실제 주문이 나가므로 매우 주의!)
        # 아래 주석을 풀고 실행하면 15 USDT 만큼의 BNB를 시장가 매수합니다.
        # buy_result = binance.buy_market_order('BNB-USDT', quote_order_qty=15)
        # if buy_result:
        #     logger.info("매수 주문 결과:", buy_result)
        #     time.sleep(2) # 주문 처리 시간 대기
        #
        #     # 매수한 수량 확인 후 그대로 매도
        #     bnb_balance = binance.get_balance('BNB')
        #     if bnb_balance > 0:
        #         sell_result = binance.sell_market_order('BNB-USDT', quantity=bnb_balance)
        #         if sell_result:
        #             logger.info("매도 주문 결과:", sell_result)