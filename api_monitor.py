"""
사그라다 파밀리아 티켓 모니터링 - API 직접 호출 방식

Selenium/브라우저 없이 Clorian 백엔드 API를 직접 호출해 가용 현황을 확인한다.
브라우저 띄우기/달력 네비게이션이 사라져 매우 가볍고 빠르며, 차단 위험도 낮다.

핵심 엔드포인트 (페이지 네트워크 분석으로 확인):
  1) 토큰  : POST {BASE}/user/api/oauth/token?secretKey=...
  2) 가용  : GET  {BASE}/catalog/salesGroups/{SG}/product/{PID}/availability
             ?month=M&venueId=V&year=Y
             헤더: Authorization: Bearer <token>, content-type, pos
응답 예) {"2026-07-05":"no-availability","2026-07-20":"availability", ...}

실행:  python api_monitor.py        (콘솔 루프)
       python api_monitor.py once   (1회만 확인하고 종료)
"""

import sys
import time
import base64
import json
from datetime import datetime, date as date_cls

import requests

import config
import notifier


TOKEN_URL = f"{config.CLORIAN_BASE}/user/api/oauth/token?secretKey={config.CLORIAN_SECRET_KEY}"
AVAIL_URL = (
    f"{config.CLORIAN_BASE}/catalog/salesGroups/{config.CLORIAN_SALES_GROUP}"
    f"/product/{config.CLORIAN_PRODUCT_ID}/availability"
)

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Origin": "https://tickets.sagradafamilia.org",
    "Referer": "https://tickets.sagradafamilia.org/",
    "Accept": "application/json, text/plain, */*",
}

# "no-availability"처럼 'no-'로 시작하면 매진. 그 외(availability 등)는 가용으로 간주.
def _is_available_status(status: str) -> bool:
    s = (status or "").strip().lower()
    return bool(s) and not s.startswith("no-")


def normalize_targets() -> list[dict]:
    """config.TARGET_DATES('2026-7-5')를 API 키('2026-07-05')로 정규화."""
    wd = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    out = []
    for ds in config.TARGET_DATES:
        y, m, d = map(int, ds.split("-"))
        api_key = f"{y:04d}-{m:02d}-{d:02d}"
        out.append({
            "key": ds,                       # 원본 표기 (알림/표시용)
            "api_key": api_key,
            "label": f"{api_key} ({wd[date_cls(y, m, d).weekday()]})",
            "year": y, "month": m, "day": d,
        })
    return out


TARGETS = normalize_targets()


class ClorianClient:
    """토큰 캐싱 + 401 자동 재발급을 처리하는 경량 API 클라이언트."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(BASE_HEADERS)
        self._token = None
        self._token_exp = 0  # epoch seconds

    # ---- 토큰 ----
    def _fetch_token(self) -> str:
        r = self.session.post(TOKEN_URL, timeout=15)
        r.raise_for_status()
        tok = r.json()["access_token"]
        self._token = tok
        self._token_exp = self._parse_exp(tok)
        return tok

    @staticmethod
    def _parse_exp(jwt: str) -> float:
        """JWT payload에서 exp(만료 epoch) 추출. 실패 시 10분 뒤로 간주."""
        try:
            payload = jwt.split(".")[1]
            payload += "=" * (-len(payload) % 4)  # base64 padding
            data = json.loads(base64.urlsafe_b64decode(payload))
            return float(data.get("exp", time.time() + 600))
        except Exception:
            return time.time() + 600

    def _valid_token(self) -> str:
        # 만료 30초 전이면 미리 재발급
        if not self._token or time.time() >= self._token_exp - 30:
            return self._fetch_token()
        return self._token

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._valid_token()}",
            "content-type": "application/json",
            "pos": str(config.CLORIAN_POS),
        }

    # ---- 가용 현황 ----
    def fetch_month(self, year: int, month: int) -> dict:
        """해당 월의 {날짜: 상태} 딕셔너리 반환. 401이면 토큰 재발급 후 1회 재시도."""
        params = {"month": month, "venueId": config.CLORIAN_VENUE_ID, "year": year}
        r = self.session.get(AVAIL_URL, params=params,
                             headers=self._auth_headers(), timeout=15)
        if r.status_code == 401:
            self._token = None  # 강제 재발급
            r = self.session.get(AVAIL_URL, params=params,
                                 headers=self._auth_headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def check_targets(self) -> tuple[list[str], dict]:
        """
        타겟 날짜들의 가용 여부 확인.
        반환: (가용한 타겟 key 목록, {타겟 key: 원시 상태문자열})
        """
        # 타겟이 속한 (연,월)별로 한 번씩만 호출
        months = {(t["year"], t["month"]) for t in TARGETS}
        month_data = {}
        for (y, m) in months:
            month_data[(y, m)] = self.fetch_month(y, m)

        available, statuses = [], {}
        for t in TARGETS:
            data = month_data.get((t["year"], t["month"]), {})
            status = data.get(t["api_key"], "unknown")
            statuses[t["key"]] = status
            if _is_available_status(status):
                available.append(t["key"])
        return available, statuses


def run_monitor():
    """콘솔 루프 (notifier로 알림)."""
    client = ClorianClient()
    notified: set[str] = set()
    count = 0

    print("=" * 60)
    print("  사그라다 파밀리아 티켓 모니터링 (API 방식)")
    print(f"  대상 날짜: {config.TARGET_DATES}")
    print(f"  확인 주기: {config.CHECK_INTERVAL_SECONDS}초")
    print("=" * 60)

    try:
        while True:
            count += 1
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now}] #{count} 확인 중...", end=" ", flush=True)
            try:
                available, statuses = client.check_targets()
                if available:
                    new_dates = [d for d in available if d not in notified]
                    if new_dates:
                        notifier.notify(new_dates)
                        notified.update(new_dates)
                    else:
                        print(f"✅ 가용({available}) - 이미 알림 발송됨")
                else:
                    print("❌ 모두 매진")
            except Exception as e:
                print(f"⚠️  오류: {type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}")
                time.sleep(config.COOLDOWN_SECONDS)
                continue

            time.sleep(config.CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\n\n모니터링 종료 (Ctrl+C)")


def check_once_cli():
    """1회 확인 후 상태 출력 (테스트용)."""
    client = ClorianClient()
    available, statuses = client.check_targets()
    print("타겟별 상태:")
    for k, s in statuses.items():
        mark = "✅ 가용" if k in available else "❌ 매진"
        print(f"  {k}: {s}  -> {mark}")
    print("가용 목록:", available or "(없음)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        check_once_cli()
    else:
        run_monitor()
