import os

# ==========================================
# 1. 자본금 / 현금 관리
# ==========================================
TOTAL_CAPITAL = 100000000      # 운용 기준 자본금 (모의투자 기본 1억 원)
CASH_BUFFER_RATIO = 0.30       # 최소 현금 비중 (30%)
MAX_POSITIONS = 10             # 최대 보유 종목 수
MAX_PER_STOCK_RATIO = 0.10     # 종목당 최대 투자 비중 (10%)

# ==========================================
# 2. 손절 / 익절 규칙
# ==========================================
STOP_LOSS_RATE = -0.07         # 손절 기준 (-7%)
FORGIVENESS_COUNT = 7          # 손절 유예 거래일 수
TAKE_PROFIT_HALF = 0.06        # 1차 익절 기준 (+6%, 50% 매도)
TAKE_PROFIT_FULL = 0.10        # 2차 익절 기준 (+10%, 전량 매도)

# ==========================================
# 3. 피라미딩 (분할 매수)
# ==========================================
PYRAMID_STAGE_1 = 0.30         # 1단계 비중 (30%)
PYRAMID_STAGE_2 = 0.30         # 2단계 비중 (30%)
PYRAMID_STAGE_3 = 0.40         # 3단계 비중 (40%)
PYRAMID_TRIGGER_2 = 0.02       # 2단계 진입 조건 (+2% 수익 시)
PYRAMID_TRIGGER_3 = 0.04       # 3단계 진입 조건 (+4% 수익 시)

# ==========================================
# 4. AI (Google Gemini) 설정
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY_HERE")
GEMINI_MODEL = "gemini-1.5-flash"
MIN_AI_SCORE = 70             # 최소 AI 분석 점수 (70점 이상일 때 매수)
HIT_RATE_CHECK_DAYS = 5       # 적중 여부 평가 데드라인 (5일)
HIT_THRESHOLD_PCT = 0.03      # 수익률 기준치 (+3% 이상이면 HIT)
# ==========================================
# 5. 거시경제 임계값 (VIX 지수 기준)
# ==========================================
VIX_STORMY_THRESHOLD = 30      # VIX 공포 기준 (30 이상이면 매매 보수적)
VIX_CLOUDY_THRESHOLD = 20      # VIX 불안 기준 (20 이상이면 주의)

# ==========================================
# 6. 일일 한도 / 안전장치
# ==========================================
MAX_DAILY_TRADES = 10          # 일일 최대 거래 횟수
MAX_DAILY_LOSS = -0.05         # 일일 최대 허용 손실률 (-5%)

# ==========================================
# 7. 텔레그램 설정
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")

# ==========================================
# 8. 한국투자증권 API 설정 (모의투자 기준)
# ==========================================
KIS_APP_KEY = os.environ.get("KIS_APP_KEY", "YOUR_APP_KEY_HERE")
KIS_APP_SECRET = os.environ.get("KIS_APP_SECRET", "YOUR_APP_SECRET_HERE")
KIS_ACCOUNT_NO = os.environ.get("KIS_ACCOUNT_NO", "YOUR_ACCOUNT_NO_HERE")
KIS_PRODUCT_CODE = "01"        # 종합계좌 구분코드
IS_PAPER_TRADING = True        # 모의투자 여부

# ==========================================
# 9. 종목 유니버스 (매매 대상 10개 종목)
# ==========================================
UNIVERSE = [
    {"code": "005930", "name": "삼성전자", "sector": "반도체"},
    {"code": "000660", "name": "SK하이닉스", "sector": "반도체"},
    {"code": "373220", "name": "LG엔솔", "sector": "2차전지"},
    {"code": "005380", "name": "현대차", "sector": "자동차"},
    {"code": "035420", "name": "NAVER", "sector": "IT/서비스"},
    {"code": "012330", "name": "현대모비스", "sector": "자동차부품"},
    {"code": "207940", "name": "삼성바이오", "sector": "바이오"},
    {"code": "068270", "name": "셀트리온", "sector": "바이오"},
    {"code": "105560", "name": "KB금융", "sector": "금융"},
    {"code": "005490", "name": "POSCO홀딩스", "sector": "철강/소재"}
]

# ==========================================
# 10. 파일 / 디렉토리 경로
# ==========================================
DATA_DIR = "data"
LOG_DIR = "logs"
REPORT_DIR = "reports"

# 실행 시간 설정
TRADE_EXECUTION_TIME = "09:05"

# 필요한 폴더 자동 생성
for d in [DATA_DIR, LOG_DIR, REPORT_DIR]:
    if not os.path.exists(d):
        os.makedirs(d)
