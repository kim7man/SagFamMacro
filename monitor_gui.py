"""
사그라다 파밀리아 티켓 모니터링 - GUI 대시보드

실제 창이 떠서 진행상황을 눈으로 볼 수 있는 버전.
- 체크 로직은 api_monitor.py(Clorian API 직접 호출)를 재사용 (브라우저 불필요)
- 모니터링은 백그라운드 스레드, UI는 메인 스레드(tkinter)
- 스레드 → UI 통신은 queue + root.after 폴링 (스레드 안전)

실행:  python monitor_gui.py
"""

import queue
import threading
from datetime import datetime

import tkinter as tk
from tkinter import scrolledtext

import config
import notifier
import api_monitor  # ClorianClient, TARGETS 재사용 (브라우저 없이 API 호출)


# ----- 색상 테마 -----
BG = "#1e1e2e"
CARD = "#2a2a3c"
FG = "#e0e0e0"
MUTED = "#9a9ab0"
ACCENT = "#89b4fa"
OK = "#a6e3a1"       # 가용(초록)
SOLD = "#f38ba8"     # 매진(빨강)
CHECKING = "#f9e2af" # 확인중(노랑)
ERROR = "#fab387"    # 오류(주황)


class MonitorGUI:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.worker = None
        self.stop_event = threading.Event()
        self.running = False

        # 날짜별 상태 위젯 보관: {date_key: (frame, dot, label)}
        self.date_widgets = {}

        self._build_ui()
        self._poll_queue()
        # 창이 뜨면 바로 모니터링 자동 시작 (UI가 먼저 그려지도록 약간 지연)
        self.root.after(500, self.start)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        r = self.root
        r.title("사그라다 파밀리아 티켓 모니터")
        r.configure(bg=BG)
        r.geometry("560x620")
        r.minsize(480, 520)

        # ---- 헤더 ----
        header = tk.Frame(r, bg=BG)
        header.pack(fill="x", padx=20, pady=(18, 6))
        tk.Label(header, text="🎫 사그라다 파밀리아 티켓 모니터",
                 bg=BG, fg=FG, font=("Segoe UI", 15, "bold")).pack(anchor="w")

        # ---- 큰 상태 배너 ----
        self.banner = tk.Label(r, text="대기 중", bg=CARD, fg=MUTED,
                               font=("Segoe UI", 20, "bold"),
                               pady=18)
        self.banner.pack(fill="x", padx=20, pady=(8, 12))

        # ---- 통계 행 ----
        stats = tk.Frame(r, bg=BG)
        stats.pack(fill="x", padx=20)
        self.lbl_count = self._stat_cell(stats, "확인 횟수", "0", 0)
        self.lbl_last = self._stat_cell(stats, "마지막 확인", "-", 1)
        self.lbl_next = self._stat_cell(stats, "다음 확인까지", "-", 2)

        # ---- 날짜별 상태 ----
        tk.Label(r, text="대상 날짜", bg=BG, fg=MUTED,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=22, pady=(16, 4))
        dates_frame = tk.Frame(r, bg=BG)
        dates_frame.pack(fill="x", padx=20)
        for info in api_monitor.TARGETS:
            self._date_row(dates_frame, info)

        # ---- 로그 ----
        tk.Label(r, text="진행 로그", bg=BG, fg=MUTED,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=22, pady=(16, 4))
        self.log = scrolledtext.ScrolledText(
            r, height=8, bg="#11111b", fg=FG, insertbackground=FG,
            font=("Consolas", 9), relief="flat", bd=0, padx=8, pady=6)
        self.log.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self.log.configure(state="disabled")

        # ---- 버튼 ----
        btns = tk.Frame(r, bg=BG)
        btns.pack(fill="x", padx=20, pady=(0, 16))
        self.btn_start = tk.Button(btns, text="▶  시작", command=self.start,
                                   bg=ACCENT, fg="#11111b", font=("Segoe UI", 11, "bold"),
                                   relief="flat", bd=0, padx=20, pady=8, cursor="hand2",
                                   activebackground="#74a0e8")
        self.btn_start.pack(side="left")
        self.btn_stop = tk.Button(btns, text="■  정지", command=self.stop,
                                  bg=CARD, fg=FG, font=("Segoe UI", 11, "bold"),
                                  relief="flat", bd=0, padx=20, pady=8, cursor="hand2",
                                  state="disabled", activebackground="#3a3a4c")
        self.btn_stop.pack(side="left", padx=(10, 0))

        r.protocol("WM_DELETE_WINDOW", self._on_close)

    def _stat_cell(self, parent, title, value, col):
        cell = tk.Frame(parent, bg=CARD)
        cell.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
        parent.grid_columnconfigure(col, weight=1)
        tk.Label(cell, text=title, bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8)).pack(pady=(8, 0))
        val = tk.Label(cell, text=value, bg=CARD, fg=FG,
                       font=("Segoe UI", 14, "bold"))
        val.pack(pady=(0, 8))
        return val

    def _date_row(self, parent, info):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=3)
        dot = tk.Label(row, text="●", bg=CARD, fg=MUTED, font=("Segoe UI", 12))
        dot.pack(side="left", padx=(12, 8), pady=6)
        tk.Label(row, text=info["label"], bg=CARD, fg=FG,
                 font=("Segoe UI", 10)).pack(side="left")
        status = tk.Label(row, text="대기", bg=CARD, fg=MUTED,
                          font=("Segoe UI", 9, "bold"))
        status.pack(side="right", padx=12)
        self.date_widgets[info["key"]] = (dot, status)

    # ------------------------------------------------------------- 워커 제어
    def start(self):
        if self.running:
            return
        self.running = True
        self.stop_event.clear()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._set_banner("확인 중...", CHECKING)
        self.worker = threading.Thread(target=self._run_worker, daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self.btn_stop.configure(state="disabled")
        self._log("정지 요청됨 - 현재 확인 마무리 후 종료합니다...")

    def _on_close(self):
        self.stop_event.set()
        self.root.after(200, self.root.destroy)

    # --------------------------------------------------- 워커 스레드 (백그라운드)
    def _run_worker(self):
        """api_monitor 체크 루프를 GUI 이벤트 방출 형태로 재구성."""
        notified = set()
        count = 0
        self.q.put(("log", f"대상 날짜: {config.TARGET_DATES}"))
        self.q.put(("log", f"확인 주기: {config.CHECK_INTERVAL_SECONDS}초 (API 방식)"))
        try:
            client = api_monitor.ClorianClient()
            self.q.put(("log", "API 클라이언트 준비 완료. 모니터링 시작."))

            while not self.stop_event.is_set():
                count += 1
                self.q.put(("count", count))
                self.q.put(("banner", ("확인 중...", CHECKING)))
                self.q.put(("checking", None))  # 모든 날짜 '확인중' 표시
                now = datetime.now().strftime("%H:%M:%S")

                try:
                    available, statuses = client.check_targets()
                    self.q.put(("last", now))
                    self.q.put(("result", available))

                    if available:
                        new_dates = [d for d in available if d not in notified]
                        if new_dates:
                            notifier.notify(new_dates)
                            notified.update(new_dates)
                            self.q.put(("log", f"🎉 가용 발견! {new_dates} - 알림 발송"))
                        else:
                            self.q.put(("log", f"✅ 가용({available}) - 알림 이미 발송됨"))
                        self.q.put(("banner", (f"가용! {', '.join(available)}", OK)))
                    else:
                        self.q.put(("log", f"#{count} 모두 매진 ({now})"))
                        self.q.put(("banner", ("모두 매진 - 감시 중", SOLD)))
                except Exception as e:
                    cd = getattr(config, "COOLDOWN_SECONDS", 60)
                    self.q.put(("log", f"⚠️ 오류: {type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"))
                    self.q.put(("banner", (f"오류 - {cd}초 후 재시도", ERROR)))
                    if self._countdown(cd):
                        break
                    continue

                # 다음 확인까지 카운트다운
                if self._countdown(config.CHECK_INTERVAL_SECONDS):
                    break
        finally:
            self.q.put(("stopped", None))

    def _countdown(self, seconds):
        """남은 시간을 UI에 갱신하며 대기. 정지되면 True 반환."""
        for remaining in range(seconds, 0, -1):
            if self.stop_event.is_set():
                return True
            self.q.put(("next", f"{remaining}초"))
            if self.stop_event.wait(1):
                return True
        self.q.put(("next", "0초"))
        return False

    def _sleep(self, seconds):
        return self.stop_event.wait(seconds)

    # ----------------------------------------------- 큐 폴링 (메인 스레드/UI)
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _handle(self, kind, payload):
        if kind == "log":
            self._log(payload)
        elif kind == "count":
            self.lbl_count.configure(text=str(payload))
        elif kind == "last":
            self.lbl_last.configure(text=payload)
        elif kind == "next":
            self.lbl_next.configure(text=payload)
        elif kind == "banner":
            self._set_banner(*payload)
        elif kind == "checking":
            for key, (dot, status) in self.date_widgets.items():
                dot.configure(fg=CHECKING)
                status.configure(text="확인중", fg=CHECKING)
        elif kind == "result":
            available = set(payload)
            for key, (dot, status) in self.date_widgets.items():
                if key in available:
                    dot.configure(fg=OK)
                    status.configure(text="가용 ✅", fg=OK)
                else:
                    dot.configure(fg=SOLD)
                    status.configure(text="매진", fg=SOLD)
        elif kind == "stopped":
            self.running = False
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self._set_banner("정지됨", MUTED)
            self.lbl_next.configure(text="-")
            self._log("모니터링 정지됨.")

    def _set_banner(self, text, color):
        self.banner.configure(text=text, fg=color)

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def main():
    root = tk.Tk()
    MonitorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
