"""
사그라다 파밀리아 티켓 모니터링 스크립트 (수정본)
- react-dates 달력의 div[role=button] 네비게이션 셀렉터 수정
- 여러 달이 동시 렌더링되는 특성을 활용 (타겟 날짜가 DOM에 나타날 때까지만 이동)
- 네비게이션은 Selenium WebElement.click() (신뢰 입력) 사용
"""

import time
from datetime import datetime, date as date_cls

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
)
from webdriver_manager.chrome import ChromeDriverManager

import config
import notifier


MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December"
}
DAY_NAMES = {
    0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
    4: "Friday", 5: "Saturday", 6: "Sunday"
}

# 네비게이션 셀렉터 (수정 핵심): button이 아니라 div[role=button]
SEL_NEXT = "div.DayPickerNavigation_button[aria-label*='forward']"
SEL_PREV = "div.DayPickerNavigation_button[aria-label*='backward']"
SEL_CAPTION = ".CalendarMonth_caption strong"


def parse_target_dates() -> list[dict]:
    """config.TARGET_DATES("YYYY-M-D")를 aria-label 비교용으로 변환."""
    results = []
    for ds in config.TARGET_DATES:
        y, m, d = map(int, ds.split("-"))
        dt = date_cls(y, m, d)
        aria_fragment = f"{DAY_NAMES[dt.weekday()]}, {d} {MONTH_NAMES[m]} {y}"
        results.append({
            "key": ds, "year": y, "month": m, "day": d,
            "aria_fragment": aria_fragment,
        })
    return results


TARGET_DATE_INFO = parse_target_dates()


def create_driver() -> webdriver.Chrome:
    opts = Options()
    if not config.SHOW_BROWSER:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    # 넓은 창: 여러 달을 한 번에 렌더링하게 해서 네비게이션 최소화
    opts.add_argument("--window-size=1600,1000")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    # 부하 완화: 전체 로드를 기다리지 않고 DOM 완성 시점에 진행
    opts.page_load_strategy = "eager"
    # 부하 완화: 이미지 로딩 차단 (달력은 텍스트/aria-label만 읽으므로 이미지 불필요)
    opts.add_experimental_option(
        "prefs", {"profile.managed_default_content_settings.images": 2}
    )
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"}
    )
    return driver


# 쿠키 동의 '수락' 버튼 후보. 이 사이트는 class='btn-cookies-primary' + 텍스트 'Accept all'.
COOKIE_ACCEPT_SELECTORS = [
    ".btn-cookies-primary",          # 사그라다 사이트 실제 버튼 (Accept all / Reject optional)
    "#onetrust-accept-btn-handler",
    "button#cookie-accept",
    "button[aria-label*='accept' i]",
]


def _click(driver, el):
    """일반 클릭이 가로채이면 JS 클릭으로 폴백."""
    try:
        el.click()
    except (ElementClickInterceptedException, NoSuchElementException):
        driver.execute_script("arguments[0].click()", el)


def accept_cookies(driver):
    """쿠키 동의 배너가 있으면 'Accept all' 클릭 (달력/예매폼을 가리는 오버레이 제거).

    주의: 이 사이트의 쿠키 버튼은 일반 click()이 가로채여서 JS 클릭 폴백이 필수.
    쿠키를 수락해야 가용 데이터가 로드되므로 예매에 반드시 선행되어야 한다.
    """
    # 1) 텍스트로 'Accept all' 버튼 우선 (가장 확실)
    try:
        for b in driver.find_elements(By.XPATH,
                "//button[contains(., 'Accept all') or contains(., 'Aceptar todas') "
                "or normalize-space()='Accept' or normalize-space()='Aceptar']"):
            if b.is_displayed():
                _click(driver, b)
                time.sleep(0.5)
                return True
    except (NoSuchElementException, StaleElementReferenceException):
        pass
    # 2) CSS 후보들 (단, btn-cookies-primary는 'Accept all'만 선택)
    for sel in COOKIE_ACCEPT_SELECTORS:
        try:
            for b in driver.find_elements(By.CSS_SELECTOR, sel):
                if not b.is_displayed():
                    continue
                if sel == ".btn-cookies-primary" and "accept" not in (b.text or "").lower():
                    continue  # 'Reject optional' 회피
                _click(driver, b)
                time.sleep(0.5)
                return True
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    return False


def get_visible_headers(driver) -> list[str]:
    """현재 DOM에 렌더링된 달 헤더 목록 (예: ['June 2026','July 2026',...])."""
    caps = driver.find_elements(By.CSS_SELECTOR, SEL_CAPTION)
    return [c.text.strip() for c in caps if c.text.strip()]


def is_target_in_dom(driver, target: dict) -> bool:
    """타겟 날짜의 td가 DOM에 존재하는지(= 그 달이 렌더링됐는지)."""
    frag = target["aria_fragment"]
    tds = driver.find_elements(By.CSS_SELECTOR, f"td[aria-label*='{frag}']")
    return len(tds) > 0


def ensure_target_rendered(driver, target: dict, max_steps: int = 14):
    """
    타겟 날짜가 DOM에 나타날 때까지 달력을 앞/뒤로 이동.
    여러 달이 동시 렌더링되므로 보통 0~2회 클릭이면 충분.
    """
    if is_target_in_dom(driver, target):
        return True

    target_header = f"{MONTH_NAMES[target['month']]} {target['year']}"
    target_ord = target["year"] * 12 + target["month"]

    for _ in range(max_steps):
        headers = get_visible_headers(driver)
        if not headers:
            time.sleep(0.5)
            continue

        # 현재 렌더링된 달들의 (year*12+month) 범위 계산
        ords = []
        for h in headers:
            for mnum, mname in MONTH_NAMES.items():
                if h.startswith(mname):
                    yr = int(h.split()[-1])
                    ords.append(yr * 12 + mnum)
        if not ords:
            break

        if min(ords) <= target_ord <= max(ords):
            return True  # 타겟 달이 범위 안에 들어옴

        try:
            if target_ord > max(ords):
                driver.find_element(By.CSS_SELECTOR, SEL_NEXT).click()
            else:
                driver.find_element(By.CSS_SELECTOR, SEL_PREV).click()
        except NoSuchElementException:
            print("[경고] 네비게이션 버튼을 찾을 수 없음")
            break
        except ElementClickInterceptedException:
            # 쿠키 배너 등이 클릭을 가로챈 경우: 배너 닫고 재시도
            accept_cookies(driver)
            time.sleep(0.3)
            continue
        except StaleElementReferenceException:
            time.sleep(0.3)
            continue
        time.sleep(0.8)

    return is_target_in_dom(driver, target)


def get_available_dates(driver) -> list[str]:
    """
    타겟 날짜 중 available인 것 반환.
    aria-label에 'Not available'이 없고 aria-disabled != 'true' 이면 가용.
    """
    available = []
    for target in TARGET_DATE_INFO:
        frag = target["aria_fragment"]
        # React 재렌더링으로 인한 stale 요소 대비 최대 3회 재시도
        for _ in range(3):
            try:
                tds = driver.find_elements(By.CSS_SELECTOR, f"td[aria-label*='{frag}']")
                if not tds:
                    break
                td = tds[0]
                label = td.get_attribute("aria-label") or ""
                disabled = td.get_attribute("aria-disabled")
                if "Not available" not in label and disabled != "true":
                    available.append(target["key"])
                break
            except StaleElementReferenceException:
                time.sleep(0.2)
    return available


def check_once(driver) -> list[str]:
    wait = WebDriverWait(driver, 20)
    driver.get(config.TARGET_URL)
    accept_cookies(driver)  # 쿠키 배너가 달력을 가리지 않도록 먼저 처리
    wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, ".CalendarMonth_caption")))
    time.sleep(1.5)  # JS 렌더링 대기

    # 타겟 월별로 한 번씩만 보장 (중복 제거)
    seen_months = set()
    for target in TARGET_DATE_INFO:
        mk = (target["year"], target["month"])
        if mk in seen_months:
            continue
        seen_months.add(mk)
        ensure_target_rendered(driver, target)

    return get_available_dates(driver)


def run_monitor():
    driver = None
    notified_dates: set[str] = set()
    check_count = 0

    print("=" * 60)
    print("  사그라다 파밀리아 티켓 모니터링 시작")
    print(f"  대상 날짜: {config.TARGET_DATES}")
    print(f"  확인 주기: {config.CHECK_INTERVAL_SECONDS}초")
    print("=" * 60)

    try:
        driver = create_driver()
        while True:
            check_count += 1
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now}] #{check_count} 확인 중...", end=" ", flush=True)

            try:
                available = check_once(driver)
                if available:
                    new_dates = [d for d in available if d not in notified_dates]
                    if new_dates:
                        notifier.notify(new_dates)
                        notified_dates.update(new_dates)
                    else:
                        print(f"✅ 가용({available}) - 이미 알림 발송됨")
                else:
                    print("❌ 모두 매진")
            except TimeoutException:
                # 달력이 안 뜸 = 레이트리밋/차단/지연 의심.
                # 드라이버 재시작은 의미 없고(같은 IP) 오히려 차단을 키우므로
                # 길게 쉬었다가(쿨다운) 같은 세션으로 재시도한다.
                print(f"⏳ 페이지 로딩 실패(차단/지연 의심) "
                      f"- {config.COOLDOWN_SECONDS}초 쉬었다 재시도")
                time.sleep(config.COOLDOWN_SECONDS)
                continue
            except Exception as e:
                print(f"⚠️  오류: {type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}")
                try:
                    driver.quit()
                except Exception:
                    pass
                time.sleep(10)
                driver = create_driver()
                continue

            print(f"  → {config.CHECK_INTERVAL_SECONDS}초 후 재확인")
            time.sleep(config.CHECK_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print("\n\n모니터링 종료 (Ctrl+C)")
    finally:
        if driver:
            driver.quit()


if __name__ == "__main__":
    run_monitor()