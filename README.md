# 🤖 AI 기반 암호화폐 자동매매 시스템

이 프로젝트는 파이썬을 이용해 구축한 AI 기반 암호화폐 자동매매 시스템입니다. 다양한 금융 데이터를 수집하고, 여러 트레이딩 전략을 백테스트하며, 최종적으로 모의 또는 실제 투자를 실행하는 것을 목표로 합니다.

## ⚠️ 중요: 면책 조항 (Disclaimer)

**이 프로젝트는 교육적인 목적으로 제작되었으며, 특정 금융 상품의 매매를 추천하거나 투자 수익을 보장하지 않습니다.**

여기에 제시된 코드와 전략은 자동매매 시스템을 구축하는 **하나의 방법론을 설명**하기 위한 것입니다. 모든 투자에 대한 책임은 투자자 본인에게 있으며, 실제 자금으로 이 시스템을 운용하기 전에는 반드시 충분한 백테스팅과 모의투자를 통해 그 성능과 위험을 직접 검증하셔야 합니다. 금융 시장은 예측 불가능한 요인으로 인해 언제나 손실의 위험이 따르므로, 신중하게 결정하시기 바랍니다.

---

## ✨ 주요 기능

* **데이터 수집**: 업비트의 OHLCV, 공포탐욕지수(F&G), 거시경제지표(금리, 환율 등)를 수집하여 로컬 데이터베이스에 저장
* **전략 백테스팅**: 여러 코인과 파라미터에 대해 전략의 과거 성과를 검증하는 백테스팅 엔진
    * **그리드 서치**: 특정 전략의 최적 파라미터를 탐색
    * **다수 티커 테스트**: 여러 코인에 대해 검증된 전략을 동시 테스트
* **자동매매 실행**:
    * **앙상블 전략**: 여러 전략의 신호를 조합하여 투자 결정을 내림
    * **AI 분석**: OpenAI(GPT-4o mini)를 이용해 시장 상황과 앙상블 신호를 종합 분석하여 최종 결정
    * **모의/실제 투자**: 설정에 따라 모의투자 또는 실제 업비트 계좌로 주문 실행
* **AI 회고 분석**: 주기적으로 과거의 매매 기록을 AI가 분석하여 성공/실패 요인과 개선점을 리포트

## 🛠️ 프로젝트 구조
    /autotrading_project/
    ├── main.py                 # 메인 실행 파일
    ├── config.py               # 모든 설정을 담당하는 '제어판'
    ├── requirements.txt        # 필요한 라이브러리 목록
    ├── .env                    # API 키 등 비밀 정보 저장
    ├── .gitignore              # Git이 무시할 파일 목록
    │
    ├── data/
    │   ├── data_manager.py     # 데이터 총괄 관리자
    │   └── collectors/         # 데이터 수집기 모음
    │
    ├── core/
    │   ├── strategy.py         # 트레이딩 전략 (매수/매도 신호)
    │   ├── portfolio.py        # 계좌 관리
    │   └── trade_executor.py   # 주문 실행
    │
    ├── apis/
    │   ├── upbit_api.py        # 업비트 API 통신
    │   └── ai_analyzer.py      # OpenAI API 통신 및 회고 분석
    │
    ├── backtester/
    │   ├── backtest_engine.py  # 백테스트 실행 엔진
    │   └── performance.py      # 백테스트 성과 분석
    │
    └── utils/
        └── indicators.py       # 기술적 지표 계산

## 🚀 시작하기

### 1. 프로젝트 복제
```
git clone [https://github.com/YourUsername/my-autotrading-system.git](https://github.com/YourUsername/my-autotrading-system.git)
cd my-autotrading-system
```
2. 가상환경 생성 및 활성화
```
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
# source venv/bin/activate
```
3. 필요 라이브러리 설치
```
pip install -r requirements.txt
```
4. 설정 파일 준비
config.py.example 파일을 복사하여 config.py 파일을 생성합니다. 그 후, config.py 파일 안의 내용을 자신의 환경에 맞게 수정합니다.

.env 파일을 생성하고 아래와 같이 자신의 API 키를 입력합니다.
```
UPBIT_ACCESS_KEY="YOUR_UPBIT_ACCESS_KEY_HERE"
UPBIT_SECRET_KEY="YOUR_UPBIT_SECRET_KEY_HERE"
OPENAI_API_KEY="YOUR_OPENAI_API_KEY_HERE"
```
📖 사용법
프로젝트의 모든 기능은 main.py를 통해 실행됩니다.

데이터 수집
로컬 데이터베이스에 모든 최신 데이터를 수집하고 업데이트합니다.
```
python main.py collect
```
백테스팅
config.py 파일의 GRID_SEARCH_CONFIG 또는 MULTI_TICKER_CONFIG 설정을 수정한 후 실행합니다.
```
# 그리드 서치 실행
python main.py backtest --backtest_mode grid

# 다수 티커 테스트 실행
python main.py backtest --backtest_mode multi
```
자동매매 실행
config.py 파일의 RUN_MODE ('simulation' 또는 'real')와 ENSEMBLE_CONFIG를 설정한 후 실행합니다.
```
python main.py trade
```
📝 라이선스
```
이 프로젝트는 MIT License를 따릅니다.
```