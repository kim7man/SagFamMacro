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
from datetime import date as date_cls

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException, StaleElementReferenceException, TimeoutException,
)

import config
import monitor          # create_driver, accept_cookies, _click, SEL_NEXT 재사용
import api_monitor      # 가용 날짜 확인용

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


def _create_real_profile_driver():
    """실제 크롬 프로필(이력/로그인 있음)로 구동 → reCAPTCHA 신뢰점수↑.
    주의: 실행 전 모든 크롬 창을 닫아야 함(프로필 잠금)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    user_data = (getattr(config, "CHROME_USER_DATA_DIR", "") or "").strip() or _default_chrome_user_data_dir()
    profile = getattr(config, "CHROME_PROFILE_DIR", "Default") or "Default"
    print(f"    [실제 크롬 프로필 사용] {user_data}\\{profile}  (크롬을 모두 닫아두세요)")

    opts = Options()
    opts.add_argument(f"--user-data-dir={user_data}")
    opts.add_argument(f"--profile-directory={profile}")
    opts.add_argument("--window-size=1500,1000")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    # 실제 프로필은 이미지 차단/headless 안 함(자연스러운 세션 유지)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
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
    최종 제출은 reCAPTCHA 검증+장바구니 생성으로 수 초 걸리므로 이 대기가 필요."""
    end = time.time() + timeout
    while time.time() < end:
        if not personal_form_present(driver):
            return True
        time.sleep(POLL)
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
    """select 옵션 중 wanted와 가장 잘 맞는 visible text 반환."""
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

    # 1) 국가 드롭다운으로 Korea 선택
    try:
        openers = [o for o in driver.find_elements(By.CSS_SELECTOR, ".selected-flag, .flag-dropdown")
                   if o.is_displayed()]
        if openers:
            monitor._click(driver, openers[0])
            time.sleep(0.4)
            items = driver.find_elements(By.CSS_SELECTOR, f"li[data-country-code='{iso2}']")
            if items:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'})", items[0])
                monitor._click(driver, items[0])
                time.sleep(0.4)
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


def accept_terms_and_pay(driver, timeout=15):
    """필수 약관(라벨에 '*')만 체크하고 Pay 버튼 클릭 → 은행 결제 페이지로 이동.
    선택 항목(마케팅 수신 등)은 체크하지 않음. 카드입력/3DS는 사람이."""
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

    # Pay 버튼(btn-custom-addToCart, 텍스트 'Pay')이 활성화되면 클릭
    end = time.time() + timeout
    while time.time() < end:
        for b in driver.find_elements(By.CSS_SELECTOR, "button.btn-custom-addToCart"):
            try:
                txt = (driver.execute_script("return arguments[0].textContent;", b) or "").strip().lower()
                if b.is_displayed() and b.is_enabled() and "pay" in txt:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'})", b)
                    before = driver.current_url
                    monitor._click(driver, b)
                    # Pay 제출은 reCAPTCHA 엄격검증 단계 → 은행 페이지 리디렉션 대기
                    rend = time.time() + 20
                    while time.time() < rend:
                        if driver.current_url != before:
                            print(f"    💳 Pay 완료 → 은행 결제 페이지로 이동: {driver.current_url[:60]}")
                            print("       카드 입력/3DS 인증은 직접 진행하세요.")
                            return True
                        time.sleep(POLL)
                    # 리디렉션이 없으면 reCAPTCHA가 자동 세션을 거부한 것('retry again' 에러)
                    print("    ⚠️ Pay 제출이 사이트 보안(reCAPTCHA)에 막혔습니다('retry' 에러 가능).")
                    print("       → 폼은 모두 채워졌습니다. 화면의 Pay 버튼을 직접 눌러보세요.")
                    print("       → 그래도 막히면 실제 크롬 프로필 모드(USE_REAL_CHROME_PROFILE)로 실행하세요.")
                    return False
            except StaleElementReferenceException:
                continue
        time.sleep(POLL)
    print("    [주의] Pay 버튼이 활성화되지 않음 — 필수 약관/입력값을 화면에서 확인하세요.")
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
                # 최종 제출은 reCAPTCHA 검증으로 수 초 걸림 → 폼이 사라질 때까지 대기
                if wait_left_personal(driver, timeout=25):
                    print("    ✅ 결제 페이지 진입 완료.")
                    print("[+] 결제 페이지 연락처 입력...")
                    fill_payment_contact(driver)
                    if getattr(config, "BOOKING_CLICK_PAY", False):
                        print("[+] 필수 약관 체크 + Pay...")
                        accept_terms_and_pay(driver)
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
