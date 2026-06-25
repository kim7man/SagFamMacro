# ============================================================
# 설정 파일 - 여기만 수정하면 됩니다
# ============================================================

# 모니터링 대상 URL (7/1처럼 클릭 가능한 날짜를 선택한 상태의 페이지)
TARGET_URL = "https://tickets.sagradafamilia.org/en/1-individual/4375-sagrada-familia"

# 티켓을 원하는 날짜 목록 (YYYY-M-D 형식, 1~9월은 앞에 0 없이)
TARGET_DATES = [
    "2026-7-5",   # 일요일
    "2026-7-6",   # 월요일
    "2026-7-7",   # 화요일
    "2026-7-8",   # 수요일
    "2026-7-9",   # 목요일
]
#TARGET_DATES = [
    #"2026-7-1",   # 일요일
#]

# 확인 주기 (초 단위). API 방식은 가벼워서 1초 폴링도 300회+ 무리 없음(실측 확인).
# (참고: Selenium 방식은 1초면 약 1분 만에 차단됐었음)
CHECK_INTERVAL_SECONDS = 1

# 오류(네트워크/차단 등)로 실패 시 쉬어가는 시간(초).
COOLDOWN_SECONDS = 60

# ============================================================
# Clorian API 설정 (TARGET_URL의 백엔드. 보통 수정할 필요 없음)
# ------------------------------------------------------------
# 페이지 네트워크 분석으로 확인된 값들. 사이트가 바뀌면 갱신 필요.
CLORIAN_BASE = "https://services.clorian.com"
CLORIAN_SECRET_KEY = "thesagradafamiliafrontendoftomorrow"
CLORIAN_PRODUCT_ID = 4375   # 4375-sagrada-familia
CLORIAN_SALES_GROUP = 1
CLORIAN_VENUE_ID = 1
CLORIAN_POS = 649           # 토큰의 posAllowed 값

# 가용 발견 시 예매 페이지를 기본 브라우저로 자동 열기 (사람이 바로 결제할 수 있게)
OPEN_BROWSER_ON_HIT = True

# ============================================================
# 자동 예약 도우미 (booking_assistant.py) 설정
# ------------------------------------------------------------
# 빈자리 발견 시 보이는 브라우저로 1~4단계(날짜/시간/인원/개인정보)를 자동 입력하고
# 결제 직전(또는 결제 페이지)까지 진행한다. reCAPTCHA/결제는 사람이 마무리.

# 인원 구성: (buyer type 라벨, 수량). 라벨은 사이트 표기의 일부만 매칭돼도 됨.
TICKET_QUANTITIES = [
    ("General", 2),
    ("Children under 11", 1),
]

# 예약자 정보 — 위 TICKET_QUANTITIES 순서대로(General 2명 → Children 1명) 폼 블록에 채워짐.
# doc_type: "Pasaporte"(여권) / "DNI" / "NIE",  country: 사이트 옵션명(예: "Korea")
BOOKING_PASSENGERS = [
    {"name": "Taekyung", "surname": "Kim", "doc_type": "Pasaporte", "country": "Korea", "doc_number": "M95626274"},
    {"name": "Yanghyun", "surname": "Cho", "doc_type": "Pasaporte", "country": "Korea", "doc_number": "M594Z6731"},
    {"name": "Bada",     "surname": "Kim", "doc_type": "Pasaporte", "country": "Korea", "doc_number": "M93703093"},
]

# 개인정보 입력 후 결제 페이지로 넘기는 마지막 CONTINUE까지 자동 클릭할지.
# False면 개인정보까지만 채우고 멈춤(사람이 확인 후 직접 진행).
BOOKING_SUBMIT_TO_PAYMENT = True

# 결제 페이지(summaryPurchase)의 구매자 연락처 정보 자동 입력.
# country는 사이트 옵션명과 부분일치로 자동 매칭(예: "Korea" → "Korea, Republic of").
BOOKING_CONTACT = {
    "first_name": "Taekyung",
    "last_name": "Kim",
    "country": "South Korea",
    "phone": "+821027224030",
    "email": "kim7man@gmail.com",
}

# 결제 페이지에서 필수 약관(이용약관/개인정보) 체크 후 Pay 버튼까지 클릭할지.
# Pay를 누르면 '은행 결제 페이지'로 이동 → 카드입력/3DS 인증은 사람이 직접.
# (마케팅 수신 동의 등 선택 항목은 체크하지 않음)
BOOKING_CLICK_PAY = True

# [중요] Pay 단계 reCAPTCHA 통과용 — 실제 크롬 프로필로 실행.
# 깨끗한 자동화 크롬은 reCAPTCHA 점수가 낮아 Pay에서 'retry' 에러로 막힘.
# 이력/로그인이 있는 실제 프로필을 쓰면 신뢰점수가 높아 통과 가능성이 큼.
# 사용법: (1) 모든 크롬 창을 완전히 닫는다(프로필 잠금 때문) → (2) True로 설정.
USE_REAL_CHROME_PROFILE = True
CHROME_USER_DATA_DIR = ""        # 비우면 자동탐지: %LOCALAPPDATA%\Google\Chrome\User Data
CHROME_PROFILE_DIR = "Default"   # 특정 프로필 폴더명(예: "Profile 1")

# 이메일 알림 설정 (사용하지 않으면 False로 설정)
EMAIL_NOTIFY = False
EMAIL_SENDER = "your_email@gmail.com"
EMAIL_PASSWORD = "your_app_password"   # Gmail 앱 비밀번호
EMAIL_RECEIVER = "your_email@gmail.com"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# 브라우저 창 표시 여부 (True = 창 보임, False = 백그라운드)
SHOW_BROWSER = True

# 최대 재시도 횟수 (페이지 로드 실패 시)
MAX_RETRIES = 100
