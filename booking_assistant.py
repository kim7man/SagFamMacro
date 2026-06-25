"""
사그라다 파밀리아 티켓 - 자동 예약 도우미

빈자리가 확인되면 '보이는 브라우저'로 다음을 자동 진행한다:
  1. 해당 날짜 선택
  2. 비어있는 시간 선택
  3. 인원 수 선택 (config.TICKET_QUANTITIES)
  4. 개인정보 입력 (config.BOOKING_PASSENGERS: 이름/성/여권/국가/번호)
  5. CONTINUE 클릭 (결제 페이지까지) — 이후 reCAPTCHA/결제는 사람이 마무리

탐색으로 확인된 사이트 동작:
  - 쿠키 'Accept all'은 JS 클릭 필요 (수락해야 가용 데이터 로드)
  - 달력은 NEXT로 해당 월을 '활성화'해야 그 달 가용이 채워짐
  - 날짜=td 클릭, 시간=button.event, 인원=div.buyerType 내 +버튼
  - 진짜 CONTINUE는 button.btn-custom-next (보임+활성). btn-custom-addToCart(비활성)는 함정.
  - 개인정보 폼(블록 bt-X-Y): Name=3711, Surname=3712, DocType=3876(→Pasaporte),
    Country=3879(Pasaporte 선택 시 등장), PassportNo=3887(Country 선택 시 등장)

실행:
  python booking_assistant.py 2026-7-5     # 특정 날짜
  python booking_assistant.py              # config.TARGET_DATES 중 첫 가용 날짜 자동
"""

import sys
import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException, StaleElementReferenceException, TimeoutException,
    SessionNotCreatedException,
)

import config
import api_monitor      # 가용 날짜 확인용
import monitor          # create_driver, accept_cookies, _click, SEL_NEXT 재사용

MONTH_NAMES = monitor.MONTH_NAMES

# 개인정보 폼 필드 ID (네트워크/DOM 분석으로 확인)
F_NAME, F_SURNAME, F_DOCTYPE, F_COUNTRY, F_DOCNUM = "3711", "3712", "3876", "3879", "3887"

# 속도 우선: 고정 sleep 대신 조건이 충족되는 즉시 진행. 폴링 간격을 짧게.
POLL = 0.05


def fast_wait(driver, timeout=15):
    return WebDriverWait(driver, timeout, poll_frequency=POLL)


def wait_visible_css(driver, css, timeout=15):
    """css에 매칭되는 '보이는' 요소가 나타나는 즉시 반환 (최고 속도)."""
    end = time.time() + timeout
    while time.time() < end:
        for e in driver.find_elements(By.CSS_SELECTOR, css):
            try:
                if e.is_displayed():
                    return e
            except StaleElementReferenceException:
                continue
        time.sleep(POLL)
    raise TimeoutException(f"요소 미등장: {css}")


# ----------------------------------------------------------------- 드라이버
def _default_chrome_user_data_dir():
    import os
    local = os.environ.get("LOCALAPPDATA", "")
    return os.path.join(local, "Google", "Chrome", "User Data") if local else ""


def _automation_chrome_user_data_dir():
    import os
    return os.path.abspath(".chrome-profile")


def create_visible_driver():
    """예약은 항상 보이는 브라우저로. 옵션으로 실제 크롬 프로필 사용(reCAPTCHA 통과용)."""
    if getattr(config, "USE_REAL_CHROME_PROFILE", False):
        return _create_real_profile_driver()
    orig = getattr(config, "SHOW_BROWSER", True)
    config.SHOW_BROWSER = True
    try:
        return monitor.create_driver()
    finally:
        config.SHOW_BROWSER = orig


def _prepare_real_profile_copy():
    """실제 크롬 프로필을 별도 폴더(.chrome-profile-real)로 복사해 반환.
    Chrome 136+는 '실제 기본 프로필'의 자동화 직접사용을 막으므로(보안), 복사본을 쓴다.
    쿠키/이력/Local State(쿠키 복호화 키)를 유지해 reCAPTCHA 신뢰점수를 높인다.
    용량 큰 캐시류는 제외해 복사를 빠르게 한다. 실패하면 None."""
    import os, shutil
    src_root = _default_chrome_user_data_dir()
    profile = getattr(config, "CHROME_PROFILE_DIR", "Default") or "Default"
    src_profile = os.path.join(src_root, profile)
    if not (src_root and os.path.isdir(src_profile)):
        return None
    dst_root = os.path.abspath(".chrome-profile-real")
    dst_profile = os.path.join(dst_root, profile)
    # 이미 복사돼 있으면(쿠키 존재) 재복사 생략 — 전체복사는 수십 초 걸림.
    # 쿠키를 갱신하려면 .chrome-profile-real 폴더를 지우고 다시 실행하면 됨.
    cookies_existing = (os.path.exists(os.path.join(dst_profile, "Network", "Cookies"))
                        or os.path.exists(os.path.join(dst_profile, "Cookies")))
    if cookies_existing:
        print("    (기존 프로필 복사본 재사용 — 갱신하려면 .chrome-profile-real 삭제)")
        return dst_root
    os.makedirs(dst_profile, exist_ok=True)

    # 쿠키 복호화 키가 든 Local State 복사 (없으면 쿠키 해독 불가 → 로그인 풀림)
    src_ls = os.path.join(src_root, "Local State")
    if os.path.exists(src_ls):
        try:
            shutil.copy2(src_ls, os.path.join(dst_root, "Local State"))
        except Exception:
            pass

    # 프로필 폴더 복사 (캐시/락 등 대용량·불필요 항목 제외)
    skip = {"Cache", "Code Cache", "GPUCache", "GrShaderCache", "DawnCache",
            "DawnGraphiteCache", "DawnWebGPUCache", "Service Worker", "Crashpad",
            "component_crx_cache", "extensions_crx_cache", "Application Cache",
            "File System", "IndexedDB", "blob_storage", "Media Cache"}
    lock_like = {"lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"}
    for name in os.listdir(src_profile):
        if name in skip or name in lock_like:
            continue
        s = os.path.join(src_profile, name)
        d = os.path.join(dst_profile, name)
        try:
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns(*skip))
            else:
                shutil.copy2(s, d)
        except (shutil.Error, OSError, PermissionError):
            pass  # 잠긴/일부 파일 실패는 무시 (쿠키 등 핵심만 있으면 됨)
    return dst_root


def _create_real_profile_driver():
    """실제 크롬 프로필(이력/로그인 있음)로 구동 → reCAPTCHA 신뢰점수↑.
    주의: 실행 전 모든 크롬 창을 닫아야 함(프로필 잠금)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    explicit = (getattr(config, "CHROME_USER_DATA_DIR", "") or "").strip()
    if explicit:
        user_data = explicit
    else:
        # 빈 값 → 실제 크롬 프로필 복사본 사용(reCAPTCHA 신뢰↑). 복사 실패 시 자동화 전용.
        print("    실제 크롬 프로필 복사 중(.chrome-profile-real)... 쿠키/이력 유지")
        user_data = _prepare_real_profile_copy() or _automation_chrome_user_data_dir()
    profile = getattr(config, "CHROME_PROFILE_DIR", "Default") or "Default"
    print(f"    [실제 크롬 프로필 사용] {user_data}\\{profile}  (크롬을 모두 닫아두세요)")

    opts = Options()
    opts.add_argument(f"--user-data-dir={user_data}")
    opts.add_argument(f"--profile-directory={profile}")
    opts.add_argument("--window-size=1500,1000")
    opts.add_argument("--remote-debugging-port=0")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    # 실제 프로필은 이미지 차단/headless 안 함(자연스러운 세션 유지)
    service = Service(ChromeDriverManager().install())
    try:
        driver = webdriver.Chrome(service=service, options=opts)
    except SessionNotCreatedException as exc:
        if "DevToolsActivePort" in str(exc):
            raise RuntimeError(
                "Chrome real-profile mode failed because Chrome did not start. "
                f"Close every Chrome window/process using this profile, then retry: {user_data}\\{profile}. "
                "If you want to run without your real Chrome profile, set USE_REAL_CHROME_PROFILE = False in config.py."
            ) from exc
        raise
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"})
    return driver


# ----------------------------------------------------------------- 달력/날짜
def _clickable_dates(driver, monthname_year):
    out = []
    for t in driver.find_elements(By.CSS_SELECTOR, "td[aria-label]"):
        try:
            lbl = t.get_attribute("aria-label") or ""
        except StaleElementReferenceException:
            continue  # 달력 재렌더링 중 — 다음 폴링에서 다시 봄
        if monthname_year in lbl and "Not available" not in lbl:
            out.append((t, lbl))
    return out


def navigate_and_pick_date(driver, year, month, day):
    """타겟 월을 활성화하고 해당 날짜 td를 클릭."""
    monthname_year = f"{MONTH_NAMES[month]} {year}"
    frag = f"{day} {MONTH_NAMES[month]} {year}"

    # NEXT를 눌러가며 해당 월의 클릭가능 날짜가 생기는 즉시 진행
    for _ in range(8):
        if _clickable_dates(driver, monthname_year):
            break
        try:
            driver.find_element(By.CSS_SELECTOR, monitor.SEL_NEXT).click()
        except (NoSuchElementException, StaleElementReferenceException):
            pass
        deadline = time.time() + 4
        while time.time() < deadline:
            if _clickable_dates(driver, monthname_year):
                break
            time.sleep(POLL)

    # 해당 날짜 td 찾기 (즉시 클릭). 재렌더링/stale 대비 짧게 재시도.
    for _ in range(10):
        try:
            for t in driver.find_elements(By.CSS_SELECTOR, f"td[aria-label*='{frag}']"):
                lbl = t.get_attribute("aria-label") or ""
                if frag in lbl and "Not available" not in lbl:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'})", t)
                    monitor._click(driver, t)
                    return True
        except StaleElementReferenceException:
            pass
        time.sleep(POLL)
    raise RuntimeError(f"{frag} 클릭 가능한 날짜를 찾지 못함 (이미 매진됐을 수 있음)")


# ----------------------------------------------------------------- 시간
def pick_first_time(driver):
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "button.event")))
    slots = [b for b in driver.find_elements(By.CSS_SELECTOR, "button.event")
             if b.is_displayed() and b.is_enabled()]
    if not slots:
        raise RuntimeError("선택 가능한 시간 슬롯이 없음")
    chosen = slots[0]
    label = chosen.text.strip()
    monitor._click(driver, chosen)
    return label


# ----------------------------------------------------------------- 인원
def set_quantities(driver):
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.buyerType")))
    for label_contains, qty in config.TICKET_QUANTITIES:
        ok = False
        for bt in driver.find_elements(By.CSS_SELECTOR, "div.buyerType"):
            try:
                lbl = bt.find_element(By.CSS_SELECTOR, "label").text.strip()
            except NoSuchElementException:
                continue
            if label_contains.lower() in lbl.lower():
                inp = bt.find_element(By.CSS_SELECTOR, "input[name='quantity']")
                inc = bt.find_element(By.CSS_SELECTOR, "button[data-action-id='increment']")
                # 값이 목표에 도달하는지 확인하며 빠르게 클릭 (고정 sleep 없이)
                for _ in range(qty * 3):  # 안전 상한
                    if int(inp.get_attribute("value") or "0") >= qty:
                        break
                    monitor._click(driver, inc)
                    time.sleep(POLL)
                print(f"    인원 '{lbl}' = {inp.get_attribute('value')}")
                ok = True
                break
        if not ok:
            print(f"    [경고] 인원 타입 '{label_contains}' 못찾음")


# ----------------------------------------------------------------- CONTINUE
# 단계별 CONTINUE 버튼 클래스:
#   - 티켓 선택 페이지: btn-custom-next  (addToCart는 여기선 disabled = 함정)
#   - 개인정보 페이지(→결제): btn-custom-addToCart (여기선 활성)
CONTINUE_SELECTORS = ["button.btn-custom-next", "button.btn-custom-addToCart"]


def click_continue(driver, timeout=15):
    """현재 단계의 진짜 CONTINUE(보임+활성)를 활성화 즉시 클릭."""
    end = time.time() + timeout
    while time.time() < end:
        for css in CONTINUE_SELECTORS:
            for b in driver.find_elements(By.CSS_SELECTOR, css):
                try:
                    if b.is_displayed() and b.is_enabled():
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'})", b)
                        monitor._click(driver, b)
                        return True
                except StaleElementReferenceException:
                    continue
        time.sleep(POLL)
    return False


# 업셀/크로스셀 모달(예: "Improve your experience – Add a tower visit")이 간헐적으로
# 떠서 결제 진행을 막는다. 거절 버튼(No)/닫기(×)를 눌러 제거한다.
_NO_TEXTS = {"no", "no thanks", "no, thanks", "no gracias", "no, gracias",
             "no, gràcies", "skip", "not now", "maybe later"}


def dismiss_upsell(driver):
    """업셀/크로스셀 모달이 떠 있으면 거절(No)/닫기로 제거. 없으면 no-op. 닫은 개수 반환."""
    closed = 0
    # 1) 보이는 모달/다이얼로그 컨테이너 안에서 'No' 계열 버튼 우선
    containers = [c for c in driver.find_elements(
                    By.CSS_SELECTOR, ".modal, [role='dialog'], [class*='popup'], [class*='Modal']")
                  if _safe_displayed(c)]
    for c in containers:
        try:
            txt = (c.get_attribute("textContent") or "").lower()
            # 업셀로 보이는 모달만 (체험/타워/업그레이드/추가 등) — 결제/약관 모달 오인 방지
            if not any(k in txt for k in ("experience", "tower", "torre", "visit", "upgrade",
                                          "improve", "añade", "add a", "afegeix")):
                continue
            for b in c.find_elements(By.CSS_SELECTOR, "button, a"):
                if not _safe_displayed(b):
                    continue
                bt = (b.text or driver.execute_script("return arguments[0].textContent;", b) or "").strip().lower()
                if bt in _NO_TEXTS:
                    monitor._click(driver, b); closed += 1; time.sleep(0.2)
                    break
            else:
                # 'No'가 없으면 닫기(×) 시도
                for x in c.find_elements(By.CSS_SELECTOR, ".close, [aria-label*='close' i], button.btn-close"):
                    if _safe_displayed(x):
                        monitor._click(driver, x); closed += 1; time.sleep(0.2)
                        break
        except StaleElementReferenceException:
            continue
    return closed


def _safe_displayed(el):
    try:
        return el.is_displayed()
    except StaleElementReferenceException:
        return False


# ----------------------------------------------------------------- 쇼핑카트
# 이전에 미완료된 예약 건이 쇼핑카트에 남아 있으면 새 예약 진행이 막히는 문제가 있어,
# 시작 시 카트를 비운다. (헤더 카트 아이콘: li.option_shopping_cart, 뱃지=건수,
#  삭제 아이콘: span.icon-own-remove)
def _cart_count(driver):
    """헤더 쇼핑카트 뱃지의 건수를 반환. 못 찾으면 0."""
    for b in driver.find_elements(By.CSS_SELECTOR,
                                  ".option_shopping_cart .badge, .shoppingCart-icon .badge"):
        try:
            t = (b.text or "").strip()
            if t.isdigit():
                return int(t)
        except StaleElementReferenceException:
            continue
    return 0


def _open_cart(driver):
    """헤더 쇼핑카트를 클릭해 카트 모달('My shopping cart')을 연다."""
    for o in driver.find_elements(By.CSS_SELECTOR,
                                  ".option_shopping_cart a, .option_shopping_cart .shoppingCart-icon,"
                                  " .shoppingCart-icon"):
        if _safe_displayed(o):
            monitor._click(driver, o)
            return True
    return False


# 카트 항목 삭제 버튼: 실제로는 휴지통 아이콘 span.icon-trash (모달 내부).
# (icon-own-remove는 'No availability' 숨김 템플릿이라 안 먹힘 — 실측 확인)
_CART_DELETE_SEL = "span.icon-trash, .icon-trash, span.icon-own-remove"
_CONFIRM_TEXTS = {"yes", "confirm", "ok", "delete", "remove", "sí", "si",
                  "eliminar", "aceptar", "확인", "네", "삭제"}


def _confirm_cart_delete(driver):
    """삭제 후 확인 모달이 뜨면 확인 버튼을 누른다(있을 때만)."""
    for c in driver.find_elements(By.CSS_SELECTOR,
                                  ".modal button, [role=dialog] button, .swal2-confirm"):
        try:
            if not _safe_displayed(c):
                continue
            t = (c.text or driver.execute_script("return arguments[0].textContent;", c) or "").strip().lower()
            if t in _CONFIRM_TEXTS:
                monitor._click(driver, c)
                time.sleep(0.3)
                return True
        except StaleElementReferenceException:
            continue
    return False


def ensure_cart_empty(driver, settle=7, timeout=20):
    """시작 시 쇼핑카트에 남은(이전 미완료) 예약 건이 있으면 비운다.
    카트에 항목이 남아 있으면 새 예약/결제가 막히는 경우가 있어 선행한다.
    [주의] 카트 뱃지는 비동기로 ~5초 뒤 로드되므로 settle 동안 기다렸다가 판단한다."""
    # 뱃지가 비동기 로드될 시간을 준다. 그 사이 건수>0가 보이면 즉시 진행.
    s_end = time.time() + settle
    n = 0
    while time.time() < s_end:
        n = _cart_count(driver)
        if n > 0:
            break
        time.sleep(0.3)
    if n == 0:
        print("    🛒 쇼핑카트 비어있음.")
        return True

    print(f"    🛒 쇼핑카트에 {n}건 남음 — 비웁니다.")
    end = time.time() + timeout
    while time.time() < end and _cart_count(driver) > 0:
        if not _open_cart(driver):     # 카트 모달 열기
            time.sleep(0.3)
            continue
        time.sleep(0.6)
        dels = [d for d in driver.find_elements(By.CSS_SELECTOR, _CART_DELETE_SEL)
                if _safe_displayed(d)]
        if not dels:
            time.sleep(0.4)
            continue
        before = _cart_count(driver)
        monitor._click(driver, dels[0])
        time.sleep(0.4)
        _confirm_cart_delete(driver)   # 확인창 있으면 처리
        # 삭제 반영(뱃지 감소)까지 대기
        w = time.time() + 4
        while time.time() < w and _cart_count(driver) >= before and before > 0:
            time.sleep(POLL)
    ok = _cart_count(driver) == 0
    print("    🛒 쇼핑카트 비움 완료." if ok else
          "    [주의] 쇼핑카트를 완전히 비우지 못함 — 화면에서 직접 비워주세요.")
    return ok


# ----------------------------------------------------------------- 개인정보
def _set_field(driver, field_id, suffix, value, is_select=False, wait=8):
    """field-{id}-{suffix} 요소에 값 입력(또는 select). 등장 대기 포함."""
    css = f"[name*='field-{field_id}-{suffix}']"
    el = None
    end = time.time() + wait
    while time.time() < end:
        els = [e for e in driver.find_elements(By.CSS_SELECTOR, css) if e.is_displayed()]
        if els:
            el = els[0]
            break
        time.sleep(POLL)
    if el is None:
        raise RuntimeError(f"필드 field-{field_id}-{suffix} 미등장")
    if is_select:
        Select(el).select_by_visible_text(value)
    else:
        el.clear()
        el.send_keys(value)
    return el


def personal_form_present(driver):
    """개인정보 폼(이름칸)이 아직 화면에 있는지."""
    for e in driver.find_elements(By.CSS_SELECTOR, f"input[name*='field-{F_NAME}-bt-']"):
        try:
            if e.is_displayed():
                return True
        except StaleElementReferenceException:
            continue
    return False


def wait_left_personal(driver, timeout=25):
    """최종 제출 후 개인정보 폼이 사라질(=결제 단계 진입) 때까지 대기.
    최종 제출은 reCAPTCHA 검증+장바구니 생성으로 수 초 걸리므로 이 대기가 필요.
    진행을 막는 업셀 모달이 뜨면 닫아준다."""
    end = time.time() + timeout
    while time.time() < end:
        if not personal_form_present(driver):
            return True
        try:
            if dismiss_upsell(driver):
                print("    (업셀 모달 닫음)")
        except Exception:
            pass
        time.sleep(0.2)
    return False


def fill_passengers(driver):
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, f"[name*='field-{F_NAME}-bt-']")))
    # 이름 입력칸들을 블록 순서(bt-1-0, bt-1-1, bt-2-0 ...)로 정렬
    name_inputs = driver.find_elements(By.CSS_SELECTOR, f"input[name*='field-{F_NAME}-bt-']")

    def suffix_of(el):
        m = re.search(r"bt-\d+-\d+", el.get_attribute("name") or "")
        return m.group(0) if m else "zz"

    blocks = sorted({suffix_of(e) for e in name_inputs})
    passengers = config.BOOKING_PASSENGERS
    if len(passengers) < len(blocks):
        print(f"    [경고] 폼 블록 {len(blocks)}개 < 예약자 {len(passengers)}명 — 가능한 만큼만 입력")

    for suf, p in zip(blocks, passengers):
        print(f"    [{suf}] {p['name']} {p['surname']} / {p['doc_type']} {p['country']} {p['doc_number']}")
        _set_field(driver, F_NAME, suf, p["name"])
        _set_field(driver, F_SURNAME, suf, p["surname"])
        _set_field(driver, F_DOCTYPE, suf, p["doc_type"], is_select=True)
        if p["doc_type"].lower().startswith("pasaport") or p["doc_type"].lower() == "passport":
            _set_field(driver, F_COUNTRY, suf, p["country"], is_select=True)
            _set_field(driver, F_DOCNUM, suf, p["doc_number"])


# ----------------------------------------------------------------- 결제 연락처
def _match_country_option(select_el, wanted):
    """select 옵션 중 wanted와 가장 잘 맞는 visible text 반환.
    [성능] 옵션 텍스트를 JS 한 번으로 일괄 수집한다. 개별 element.text는 옵션당
    별도 왕복이라 국가목록(약 240개)에서 수 초가 걸렸다(전화→국가 사이 긴 지연의 원인)."""
    try:
        opts = select_el.parent.execute_script(
            "return Array.from(arguments[0].options)"
            ".map(function(o){return (o.textContent||'').trim();});",
            select_el)
        opts = [o for o in opts if o]
    except Exception:
        opts = [o.text.strip() for o in select_el.find_elements(By.TAG_NAME, "option") if o.text.strip()]
    w = wanted.lower()
    # 1) 정확 일치
    for o in opts:
        if o.lower() == w:
            return o
    # 2) 'korea' + ('south'/'republic') 우선
    keys = [k for k in w.split() if k not in ("of", "the")]
    cand = [o for o in opts if all(k in o.lower() for k in keys)]
    if cand:
        return cand[0]
    # 3) 핵심 단어(korea 등) 포함
    core = "korea" if "korea" in w else keys[-1] if keys else w
    for o in opts:
        if core in o.lower():
            return o
    return None


# 국가 dial code (react-tel-input은 입력칸에 +<dial><national> 전체를 담음)
DIAL_CODES = {"kr": "82", "us": "1", "jp": "81", "cn": "86", "gb": "44", "es": "34"}


def _set_phone(driver, phone, iso2="kr"):
    """react-tel-input: 국가 플래그를 iso2로 바꾼 뒤 국내번호 입력.
    실패 시 전체선택 후 풀번호(+82...) 입력으로 폴백."""
    from selenium.webdriver.common.keys import Keys
    phone = (phone or "").strip()
    if not phone:
        return
    dial = DIAL_CODES.get(iso2, "")
    national = phone
    if phone.startswith("+" + dial):
        national = phone[1 + len(dial):]
    national = national.lstrip("0") if not national.startswith("+") else national

    tels = [e for e in driver.find_elements(By.CSS_SELECTOR, "input[type=tel]") if e.is_displayed()]
    if not tels:
        print("    [주의] 전화 입력칸 못찾음")
        return
    tel = tels[0]

    # 1) 국가 드롭다운으로 Korea 선택 (고정 sleep 대신 등장 즉시 진행)
    try:
        openers = [o for o in driver.find_elements(By.CSS_SELECTOR, ".selected-flag, .flag-dropdown")
                   if o.is_displayed()]
        if openers:
            monitor._click(driver, openers[0])
            # 드롭다운 목록의 해당 국가 항목이 나타나는 즉시 클릭
            items = []
            d_end = time.time() + 3
            while time.time() < d_end:
                items = [i for i in driver.find_elements(By.CSS_SELECTOR, f"li[data-country-code='{iso2}']")
                         if i.is_displayed()]
                if items:
                    break
                time.sleep(POLL)
            if items:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'})", items[0])
                monitor._click(driver, items[0])
                # 위젯이 국가코드(+82)를 반영할 때까지 짧게 폴링 후 국내번호 입력
                p_end = time.time() + 1.5
                while time.time() < p_end:
                    cur = (tel.get_attribute("value") or "")
                    if cur.replace(" ", "").startswith("+" + dial) or cur.strip() in ("", "+"):
                        break
                    time.sleep(POLL)
                tel.send_keys(national)   # 국가코드(+82)는 위젯이 이미 넣음
                print(f"    전화: +{dial} {national} (국가 {iso2} 선택)")
                return
    except (StaleElementReferenceException, NoSuchElementException):
        pass

    # 2) 폴백: 전체선택 후 풀번호 입력
    tel.send_keys(Keys.CONTROL, "a")
    tel.send_keys(Keys.DELETE)
    tel.send_keys(phone)
    print(f"    전화(폴백): {phone}")


def fill_payment_contact(driver, timeout=20):
    """결제 페이지(summaryPurchase)의 구매자 연락처 입력."""
    c = getattr(config, "BOOKING_CONTACT", None)
    if not c:
        print("    (BOOKING_CONTACT 미설정 — 연락처 자동입력 건너뜀)")
        return
    # 폼 등장 대기
    fn = None
    end = time.time() + timeout
    while time.time() < end:
        els = [e for e in driver.find_elements(By.CSS_SELECTOR, "#contact-firstName") if e.is_displayed()]
        if els:
            fn = els[0]
            break
        time.sleep(POLL)
    if fn is None:
        print("    [주의] 결제 연락처 폼을 찾지 못함 — 화면에서 직접 입력하세요.")
        return

    def fill_id(css, value):
        for e in driver.find_elements(By.CSS_SELECTOR, css):
            if e.is_displayed():
                e.clear(); e.send_keys(value)
                return True
        return False

    fill_id("#contact-firstName", c["first_name"])
    fill_id("#contact-lastName", c["last_name"])
    fill_id("#contact-email", c["email"])
    fill_id("#contact-repeatEmail", c["email"])

    # 전화 (react-tel-input 위젯: 기본 +34. Korea 선택 후 국내번호 입력)
    _set_phone(driver, c.get("phone", ""), c.get("phone_iso", "kr"))

    # 국가 select
    for sel in driver.find_elements(By.CSS_SELECTOR, "#contact-country"):
        if not sel.is_displayed():
            continue
        opt = _match_country_option(sel, c["country"])
        if opt:
            Select(sel).select_by_visible_text(opt)
            print(f"    국가: '{opt}'")
        else:
            print(f"    [주의] 국가 '{c['country']}' 옵션 매칭 실패")
        break
    print(f"    연락처: {c['first_name']} {c['last_name']} / {c['phone']} / {c['email']}")


# ----------------------------------------------------------------- 약관 + Pay
def _checkbox_label_text(driver, cb):
    cid = cb.get_attribute("id") or ""
    if cid:
        labels = driver.find_elements(By.CSS_SELECTOR, f"label[for='{cid}']")
        if labels:
            return labels[0].get_attribute("textContent") or ""
    return driver.execute_script(
        "return arguments[0].closest('label,.form-group,div')?.textContent||'';", cb)


def _find_pay_button(driver):
    """summaryPurchase의 활성 Pay 버튼(btn-custom-addToCart, 텍스트 'Pay') 반환."""
    for b in driver.find_elements(By.CSS_SELECTOR, "button.btn-custom-addToCart"):
        try:
            txt = (driver.execute_script("return arguments[0].textContent;", b) or "").strip().lower()
            if b.is_displayed() and b.is_enabled() and "pay" in txt:
                return b
        except StaleElementReferenceException:
            continue
    return None


def _has_retry_error(driver):
    """화면에 'retry again' 류 오류 토스트가 떠 있는지.
    이 알림은 div.notifications-wrapper 하위에 잠깐 떴다 사라진다(실측)."""
    kws = ("retry", "again", "reintente", "intente", "vuelva", "error")
    try:
        for w in driver.find_elements(By.CSS_SELECTOR,
                                      ".notifications-wrapper, .notification, .toast, .alert"):
            t = (w.get_attribute("textContent") or "").strip().lower()
            if t and any(k in t for k in kws):
                return True
    except Exception:
        pass
    return False


def accept_terms_and_pay(driver, timeout=15, pay_attempts=6, redirect_wait=12):
    """필수 약관(라벨에 '*')만 체크하고 Pay 클릭 → 은행 결제 페이지로 이동.
    Pay 제출은 reCAPTCHA 검증을 거치는데, 점수가 낮으면 'retry again' 오류가 떠
    리디렉션이 안 된다. 이 경우 Pay를 여러 번 자동 재시도한다(에러가 권하는 'retry').
    그래도 계속 막히면 실제 크롬 프로필 모드로 reCAPTCHA 신뢰점수를 높여야 한다."""
    # 약관 영역이 보이도록 스크롤
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(0.5)

    checked = 0
    for cb in driver.find_elements(By.CSS_SELECTOR, "input[type=checkbox]"):
        try:
            text = _checkbox_label_text(driver, cb)
            if "*" in text and not cb.is_selected():       # 필수 항목만
                driver.execute_script("arguments[0].click()", cb)  # 커스텀 체크박스 → JS 클릭
                time.sleep(0.15)
                checked += 1
        except StaleElementReferenceException:
            continue
    print(f"    필수 약관 {checked}개 체크")

    # Pay 버튼 등장/활성 대기
    end = time.time() + timeout
    pay = None
    while time.time() < end:
        pay = _find_pay_button(driver)
        if pay:
            break
        time.sleep(POLL)
    if not pay:
        print("    [주의] Pay 버튼이 활성화되지 않음 — 필수 약관/입력값을 화면에서 확인하세요.")
        return False

    before = driver.current_url
    # Pay 클릭 → 리디렉션 대기. 'retry again'이면 재시도.
    for attempt in range(1, pay_attempts + 1):
        pay = _find_pay_button(driver)
        if pay is None:
            # 버튼이 사라졌으면 이미 넘어갔을 수 있음
            if driver.current_url != before:
                print(f"    💳 Pay 완료 → 은행 결제 페이지로 이동: {driver.current_url[:60]}")
                print("       카드 입력/3DS 인증은 직접 진행하세요.")
                return True
            break
        driver.execute_script("arguments[0].scrollIntoView({block:'center'})", pay)
        monitor._click(driver, pay)
        print(f"    💳 Pay 클릭 (시도 {attempt}/{pay_attempts})")
        rend = time.time() + redirect_wait
        saw_retry = False
        while time.time() < rend:
            if driver.current_url != before:
                print(f"    💳 Pay 완료 → 은행 결제 페이지로 이동: {driver.current_url[:60]}")
                print("       카드 입력/3DS 인증은 직접 진행하세요.")
                return True
            if _has_retry_error(driver):     # 알림이 떠 있는 동안 즉시 포착
                saw_retry = True
                break
            time.sleep(0.1)
        print("    ⚠️ 'retry again'(reCAPTCHA) 감지 — 재시도" if saw_retry
              else "    (리디렉션 없음 — 재시도)")
        time.sleep(1.5)   # reCAPTCHA 토큰 재발급 여유

    print("    ⚠️ Pay 제출이 reCAPTCHA('retry again')에 계속 막혔습니다.")
    print("       → 폼은 모두 채워졌습니다. 화면의 Pay 버튼을 직접 눌러보세요.")
    print("       → 반복되면 USE_REAL_CHROME_PROFILE로 실제 크롬 프로필(이력/로그인 있는)을")
    print("          써서 reCAPTCHA 신뢰점수를 높이세요(자동화 전용 프로필은 점수가 낮음).")
    return False


# ----------------------------------------------------------------- 카드 결제(은행 페이지)
# 결제 게이트웨이(보통 Redsys)는 사이트마다 DOM이 다르므로, 한 셀렉터에 의존하지 않고
# 입력칸의 여러 속성(autocomplete/name/id/placeholder/aria-label)을 신호로 삼아 탐지한다.
# 카드 칸이 iframe 안에 있는 경우가 많아 프레임을 재귀적으로 훑는다.
CARD_NUMBER_HINTS = ["cc-number", "cardnumber", "card-number", "card_number",
                     "numerotarjeta", "numero-tarjeta", "numero_tarjeta", "numtarjeta",
                     "ccnumber", "pan"]
CARD_EXP_HINTS    = ["cc-exp", "expiry", "expiration", "exp-date", "expdate",
                     "caducidad", "fechacaducidad", "vencimiento"]
CARD_MONTH_HINTS  = ["cc-exp-month", "expmonth", "exp_month", "exp-month",
                     "caducidadmes", "mes ", "month"]
CARD_YEAR_HINTS   = ["cc-exp-year", "expyear", "exp_year", "exp-year",
                     "caducidadanio", "caducidadano", "anio", "ano", "year"]
CARD_CVV_HINTS    = ["cc-csc", "cvv", "cvc", "cvc2", "cvv2", "csc",
                     "codigoseguridad", "securitycode", "cardcode", "cvcvalue"]
CARD_NAME_HINTS   = ["cc-name", "cardholder", "card-holder", "ccname",
                     "nombretitular", "titular", "holdername", "holder-name"]

# 오탐 방지: 카드번호 칸이 cvv/만료/이름 칸을 잡지 않도록, 또 그 반대도.
# 주의: 'tarjeta'(=카드)는 제외어로 쓰면 안 됨 — 실제 Redsys CVV 필드명이
#       'Sis_Tarjeta_CVV2'라서 진짜 CVV 칸까지 배제돼버린다(실측 확인).
_EXCL_NUMBER = ["cvv", "cvc", "csc", "exp", "caducidad", "vencim", "titular", "holder"]
_EXCL_CVV    = ["number", "numero", "num-", "pan", "exp", "caducidad", "titular", "holder"]


def _input_signal(el):
    parts = []
    for a in ("name", "id", "placeholder", "autocomplete", "aria-label", "title", "data-recurly"):
        try:
            parts.append((el.get_attribute(a) or "").lower())
        except StaleElementReferenceException:
            return ""
        except Exception:
            parts.append("")
    return " ".join(parts)


def _type_into(el, value):
    from selenium.webdriver.common.keys import Keys
    try:
        el.click()
    except Exception:
        pass
    try:
        el.send_keys(Keys.CONTROL, "a"); el.send_keys(Keys.DELETE)
    except Exception:
        pass
    el.send_keys(str(value))


def _fill_card_in_current_frame(driver, card, filled):
    """현재 프레임(iframe 컨텍스트)에서 매칭되는 카드 칸을 채운다. filled를 갱신."""
    number = re.sub(r"\D", "", card.get("number", ""))
    cvv = re.sub(r"\D", "", str(card.get("cvv", "")))
    exp_m = re.sub(r"\D", "", str(card.get("exp_month", ""))).zfill(2)[-2:]
    exp_y = re.sub(r"\D", "", str(card.get("exp_year", "")))
    exp_y2 = exp_y[-2:]
    exp_y4 = exp_y if len(exp_y) == 4 else ("20" + exp_y2)
    holder = card.get("name", "")

    inputs = driver.find_elements(By.CSS_SELECTOR, "input, select")
    for el in inputs:
        try:
            if not el.is_displayed() or not el.is_enabled():
                continue
            tag = (el.tag_name or "").lower()
            sig = _input_signal(el)
            if not sig:
                continue

            # 카드번호
            if (not filled["number"] and any(h in sig for h in CARD_NUMBER_HINTS)
                    and not any(x in sig for x in _EXCL_NUMBER)):
                _type_into(el, number); filled["number"] = True
                print("    ✓ 카드번호 입력"); continue

            # CVV
            if (not filled["cvv"] and any(h in sig for h in CARD_CVV_HINTS)
                    and not any(x in sig for x in _EXCL_CVV)):
                _type_into(el, cvv); filled["cvv"] = True
                print("    ✓ CVV 입력"); continue

            # 카드 소유자명
            if (not filled["name"] and holder and any(h in sig for h in CARD_NAME_HINTS)):
                _type_into(el, holder); filled["name"] = True
                print("    ✓ 소유자명 입력"); continue

            # 만료 — 단일칸(MM/YY)
            if (not filled["exp"] and any(h in sig for h in CARD_EXP_HINTS)
                    and not any(h in sig for h in CARD_MONTH_HINTS + CARD_YEAR_HINTS)):
                _type_into(el, f"{exp_m}/{exp_y2}"); filled["exp"] = True
                print("    ✓ 만료(MM/YY) 입력"); continue

            # 만료 — 월/년 분리칸 (input 또는 select)
            if not filled["exp_m"] and any(h in sig for h in CARD_MONTH_HINTS):
                if tag == "select":
                    _select_best(el, [exp_m, str(int(exp_m))])
                else:
                    _type_into(el, exp_m)
                filled["exp_m"] = True
                print("    ✓ 만료(월) 입력"); continue
            if not filled["exp_y"] and any(h in sig for h in CARD_YEAR_HINTS):
                if tag == "select":
                    _select_best(el, [exp_y4, exp_y2])
                else:
                    _type_into(el, exp_y4 if len(exp_y) == 4 else exp_y2)
                filled["exp_y"] = True
                print("    ✓ 만료(년) 입력"); continue
        except StaleElementReferenceException:
            continue
        except Exception:
            continue


def _select_best(select_el, candidates):
    """select에서 후보 텍스트/값 중 첫 매칭을 선택."""
    sel = Select(select_el)
    opts = select_el.find_elements(By.TAG_NAME, "option")
    for cand in candidates:
        c = str(cand).strip()
        for o in opts:
            val = (o.get_attribute("value") or "").strip()
            txt = (o.text or "").strip()
            if c == val or c == txt or txt.endswith(c):
                sel.select_by_visible_text(txt) if txt else sel.select_by_value(val)
                return True
    return False


def _walk_frames(driver, fn, depth=0, maxdepth=3):
    """현재 프레임에서 fn() 실행 후 하위 iframe들에 대해 재귀 실행."""
    try:
        fn()
    except Exception:
        pass
    if depth >= maxdepth:
        return
    count = len(driver.find_elements(By.CSS_SELECTOR, "iframe, frame"))
    for i in range(count):
        try:
            frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
            if i >= len(frames):
                break
            driver.switch_to.frame(frames[i])
            _walk_frames(driver, fn, depth + 1, maxdepth)
        except Exception:
            pass
        finally:
            try:
                driver.switch_to.parent_frame()
            except Exception:
                driver.switch_to.default_content()


def _ensure_cvv(driver, cvv, attempts=3):
    """CVV 칸을 (프레임 포함) 다시 탐색해 값이 실제로 들어갔는지 검증하고,
    비었거나 다르면 재입력. 게이트웨이가 CVV를 늦게 렌더링/초기화하는 경우 대비."""
    cvv = re.sub(r"\D", "", str(cvv))
    if not cvv:
        return False
    state = {"ok": False}

    def pass_once():
        if state["ok"]:
            return
        for el in driver.find_elements(By.CSS_SELECTOR, "input"):
            try:
                if not el.is_displayed() or not el.is_enabled():
                    continue
                sig = _input_signal(el)
                if not sig or not any(h in sig for h in CARD_CVV_HINTS):
                    continue
                if any(x in sig for x in _EXCL_CVV):
                    continue
                cur = re.sub(r"\D", "", el.get_attribute("value") or "")
                if cur == cvv:
                    state["ok"] = True
                    return
                _type_into(el, cvv)
                cur = re.sub(r"\D", "", el.get_attribute("value") or "")
                if cur == cvv:
                    state["ok"] = True
                    print("    ✓ CVV 재탐색·입력 확인")
                    return
            except StaleElementReferenceException:
                continue
            except Exception:
                continue

    for _ in range(attempts):
        driver.switch_to.default_content()
        _walk_frames(driver, pass_once)
        driver.switch_to.default_content()
        if state["ok"]:
            return True
        time.sleep(0.3)
    print("    ⚠️ CVV 칸을 확정하지 못했습니다 — 화면에서 CVV를 직접 확인/입력하세요.")
    return False


# 결제 버튼 텍스트: 단어 경계로 매칭한다. (부분문자열로 'pay'를 찾으면
# "Payment by Card" 패널 헤더의 'Payment'에 걸려 박스만 접히는 오클릭이 났음 — 실측)
_PAY_RE = re.compile(
    r"\b(pay|pagar|paga|pague|aceptar|confirmar|confirm|finalizar|realizar\s+pago|결제)\b",
    re.I)
# 결제 버튼이 아닌데 'pay'류가 들어가는 헤더/토글 텍스트 (오클릭 방지)
_PAY_NEG = ("payment by card", "payment method", "payment methods", "by card",
            "forma de pago", "método de pago", "metodo de pago", "view details", "detalle")


def _click_final_pay(driver):
    """결제 게이트웨이의 '진짜' 최종 결제 버튼을 (프레임 포함) 찾아 클릭.
    아코디언 헤더/토글('Payment by Card' 등)은 제외하고, submit류를 우선한다."""
    clicked = {"done": False}

    def try_click():
        if clicked["done"]:
            return
        best, best_score = None, 0
        cands = driver.find_elements(
            By.CSS_SELECTOR,
            "button, input[type=submit], input[type=button], a.btn, [role=button]")
        for b in cands:
            try:
                if not b.is_displayed() or not b.is_enabled():
                    continue
                txt = ((b.get_attribute("value") or "") + " " +
                       (driver.execute_script("return arguments[0].textContent;", b) or "")).strip()
                low = " ".join(txt.split()).lower()
                if not low or any(n in low for n in _PAY_NEG):
                    continue
                # 아코디언/접기 토글 제외
                if b.get_attribute("aria-expanded") is not None:
                    continue
                if (b.get_attribute("data-toggle") or "") == "collapse":
                    continue
                if not _PAY_RE.search(low):        # 'payment'는 \bpay\b에 안 걸림 → 헤더 자동 제외
                    continue
                # 점수: submit류·짧은 라벨일수록 진짜 결제 버튼일 확률↑
                score = 3
                typ = (b.get_attribute("type") or "").lower()
                tag = (b.tag_name or "").lower()
                if typ == "submit":
                    score += 3
                if tag in ("button", "input"):
                    score += 1
                if len(low) <= 25:
                    score += 1
                if score > best_score:
                    best, best_score = b, score
            except StaleElementReferenceException:
                continue
            except Exception:
                continue
        if best is not None:
            try:
                txt = (best.get_attribute("value") or "") + " " + (best.text or "")
                driver.execute_script("arguments[0].scrollIntoView({block:'center'})", best)
                monitor._click(driver, best)
                print(f"    💳 최종 결제 버튼 클릭: '{' '.join(txt.split())[:30]}'")
                clicked["done"] = True
            except Exception:
                pass

    driver.switch_to.default_content()
    _walk_frames(driver, try_click)
    driver.switch_to.default_content()
    return clicked["done"]


def fill_card_and_pay(driver, timeout=40):
    """은행/게이트웨이 결제 페이지에서 카드 정보를 채우고(필요시) 결제 버튼 클릭.
    3DS(은행 OTP/앱 인증)는 자동화 불가 — 이후는 사람이 마무리."""
    card = getattr(config, "BOOKING_CARD", None)
    if not card or not re.sub(r"\D", "", card.get("number", "")):
        print("    (BOOKING_CARD 미설정 — 카드 자동입력 건너뜀)")
        return False

    print("    은행 결제 페이지 카드 입력 대기중 (게이트웨이 로딩)...")
    filled = {"number": False, "cvv": False, "name": False,
              "exp": False, "exp_m": False, "exp_y": False}

    end = time.time() + timeout
    while time.time() < end:
        driver.switch_to.default_content()
        _walk_frames(driver, lambda: _fill_card_in_current_frame(driver, card, filled))
        driver.switch_to.default_content()
        exp_ok = filled["exp"] or (filled["exp_m"] and filled["exp_y"])
        if filled["number"] and filled["cvv"] and exp_ok:
            break
        time.sleep(0.3)

    exp_ok = filled["exp"] or (filled["exp_m"] and filled["exp_y"])
    if not (filled["number"] and filled["cvv"] and exp_ok):
        print(f"    ⚠️ 카드 칸 자동탐지 부분 실패 (번호={filled['number']}, "
              f"CVV={filled['cvv']}, 만료={exp_ok}).")
        print("       → 화면에서 빈 칸을 직접 채우고 결제를 진행하세요.")
        return False

    # CVV는 마지막에 다시 탐색해 실제로 값이 들어갔는지 검증·재입력
    _ensure_cvv(driver, card.get("cvv", ""))

    print("    ✅ 카드 정보 입력 완료.")
    if not getattr(config, "BOOKING_CLICK_FINAL_PAY", False):
        print("    (BOOKING_CLICK_FINAL_PAY=False — 결제 버튼은 직접 누르세요.)")
        return True

    if _click_final_pay(driver):
        print("    💳 결제 제출됨 → 3DS(OTP/은행앱) 인증이 뜨면 직접 완료하세요.")
        return True
    print("    ⚠️ 결제 버튼을 찾지 못함 — 화면에서 직접 결제 버튼을 누르세요.")
    return False


# ----------------------------------------------------------------- 오케스트레이션
def resolve_target_date(arg):
    """인자가 있으면 그 날짜, 없으면 TARGET_DATES 중 첫 가용 날짜."""
    if arg:
        y, m, d = map(int, arg.split("-"))
        return y, m, d
    client = api_monitor.ClorianClient()
    available, _ = client.check_targets()
    if not available:
        raise RuntimeError("현재 가용한 타겟 날짜가 없음 (인자로 날짜를 직접 지정하세요)")
    y, m, d = map(int, available[0].split("-"))
    print(f"가용 타겟 자동선택: {available[0]}")
    return y, m, d


def run_booking(arg=None):
    y, m, d = resolve_target_date(arg)
    print("=" * 60)
    print(f"  자동 예약 시작: {y}-{m}-{d}")
    print("=" * 60)

    t0 = time.time()
    driver = create_visible_driver()
    try:
        driver.get(config.TARGET_URL)
        monitor.accept_cookies(driver)
        print("[0/5] 쇼핑카트 확인...")
        ensure_cart_empty(driver)   # 이전 미완료 예약이 남아 있으면 진행이 막혀서 먼저 비움
        fast_wait(driver, 25).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".CalendarMonth_caption")))

        print("[1/5] 날짜 선택...")
        navigate_and_pick_date(driver, y, m, d)

        print("[2/5] 시간 선택...")           # pick_first_time이 button.event 등장을 대기
        t = pick_first_time(driver)
        print(f"    선택된 시간: {t}")

        print("[3/5] 인원 설정...")           # set_quantities가 div.buyerType 등장을 대기
        set_quantities(driver)

        print("[4/5] CONTINUE → 개인정보 단계...")
        if not click_continue(driver):
            raise RuntimeError("CONTINUE(btn-custom-next) 클릭 실패")

        print("[4/5] 개인정보 입력...")        # fill_passengers가 폼 등장을 대기
        fill_passengers(driver)

        if config.BOOKING_SUBMIT_TO_PAYMENT:
            print("[5/5] CONTINUE → 결제 페이지...")
            if click_continue(driver):
                dismiss_upsell(driver)   # 제출 직후 업셀 모달이 뜨면 닫기
                # 최종 제출은 reCAPTCHA 검증+예약생성으로 수십 초 걸릴 수 있음 → 대기
                if wait_left_personal(driver, timeout=45):
                    print("    ✅ 결제 페이지 진입 완료.")
                    print("[+] 결제 페이지 연락처 입력...")
                    fill_payment_contact(driver)
                    if getattr(config, "BOOKING_CLICK_PAY", False):
                        print("[+] 필수 약관 체크 + Pay...")
                        moved = accept_terms_and_pay(driver)
                        # Pay 클릭으로 은행/게이트웨이 페이지로 이동했으면 카드 입력 진행
                        if moved and getattr(config, "BOOKING_AUTO_CARD", False):
                            print("[+] 카드 정보 입력 + 결제...")
                            fill_card_and_pay(driver)
                else:
                    print("    제출 처리 중... 몇 초 더 걸릴 수 있으니 화면을 확인하세요.")
            else:
                print("    [주의] 최종 CONTINUE 버튼을 못 찾음 — 화면에서 직접 눌러주세요.")
        else:
            print("[5/5] (설정상 자동 진행 안 함) 개인정보까지 입력 완료.")

        print(f"\n✅ 자동 입력 완료 (소요 {time.time()-t0:.1f}초). 이제 reCAPTCHA/결제를 직접 마무리하세요.")
        print("   (이 창은 열려 있습니다. 끝나면 콘솔에서 Enter)")
        try:
            input()
        except EOFError:
            time.sleep(600)
    except Exception as e:
        print(f"\n⚠️ 예약 자동화 중 오류: {type(e).__name__}: {e}")
        print("   브라우저 창에서 수동으로 이어서 진행하세요. (끝나면 Enter)")
        try:
            input()
        except EOFError:
            time.sleep(300)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    run_booking(sys.argv[1] if len(sys.argv) > 1 else None)
