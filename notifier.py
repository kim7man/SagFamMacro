import os
import sys
import subprocess
import webbrowser
from datetime import datetime

import requests
from dotenv import load_dotenv

import config

# .env 로드 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def notify(available_dates: list[str]):
    """가용 날짜가 생겼을 때 모든 알림 수단을 실행."""
    date_str = ", ".join(available_dates)
    message = (
        f"🎉 사그라다 파밀리아 티켓 발견!\n"
        f"가용 날짜: {date_str}\n"
        f"즉시 예매하세요!\n"
        f"{config.TARGET_URL}"
    )
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"[{timestamp}] {message}"

    print("\n" + "=" * 60)
    print(full_message)
    print("=" * 60 + "\n")

    _play_sound()
    _send_telegram(full_message)

    if getattr(config, "OPEN_BROWSER_ON_HIT", False):
        _open_booking_page(available_dates)


def _open_booking_page(available_dates: list[str]):
    """가용 발견 시 예매 페이지를 기본 브라우저로 연다 (사람이 바로 결제)."""
    url = config.TARGET_URL
    try:
        # 첫 가용 날짜를 쿼리로 덧붙여 가능하면 해당 날짜로 진입 (무시돼도 페이지엔 정상 진입)
        if available_dates:
            y, m, d = map(int, available_dates[0].split("-"))
            url = f"{config.TARGET_URL}?date={y:04d}-{m:02d}-{d:02d}"
        webbrowser.open(url, new=2)  # new=2: 새 탭
        print(f"[브라우저] 예매 페이지 열기: {url}")
    except Exception as e:
        print(f"[브라우저 오류] {e}")


def _send_telegram(text: str) -> bool:
    """텔레그램 봇으로 메시지 발송. 성공 여부 반환."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[텔레그램] BOT_TOKEN/CHAT_ID가 .env에 설정되지 않음 - 발송 건너뜀")
        return False
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN),
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            print("[텔레그램] 발송 완료")
            return True
        print(f"[텔레그램 오류] status={resp.status_code} body={resp.text}")
        return False
    except Exception as e:
        print(f"[텔레그램 오류] {e}")
        return False


def send_test_message() -> bool:
    """설정이 올바른지 확인용 테스트 메시지 발송."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return _send_telegram(f"✅ [테스트] 사그라다 모니터 텔레그램 알림 정상 작동 ({ts})")


def _play_sound():
    """시스템 알림음 재생."""
    try:
        if sys.platform == "win32":
            import winsound
            for _ in range(3):
                winsound.Beep(1000, 500)
        elif sys.platform == "darwin":
            subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False)
        else:
            subprocess.run(
                ["paplay", "/usr/share/sounds/freedesktop/stereo/bell.oga"],
                check=False,
            )
    except Exception as e:
        print(f"[알림음 오류] {e}")


if __name__ == "__main__":
    # python notifier.py 로 텔레그램 연결 테스트
    print("텔레그램 테스트 메시지 발송 중...")
    ok = send_test_message()
    print("결과:", "성공" if ok else "실패")
