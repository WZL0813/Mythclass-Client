"""软件使用时长

每 5 秒看一眼前台是哪个程序，攒进本地库（参考 RunTime_Tracker 的思路：
不记录具体内容，只记「哪个程序、用了多久」，够老师看个大概）。
"""

from __future__ import annotations

import threading
import time

from . import windows

TICK = 5.0


class UsageMonitor:
    def __init__(self, store, on_tick=None, interval: float = TICK):
        self.store = store
        self.on_tick = on_tick
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.current = {"app": "", "title": "", "since": 0.0}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mythclass-usage", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._flush()

    def _flush(self) -> None:
        """把攒着的那一段写进库"""
        app = self.current.get("app") or ""
        since = float(self.current.get("since") or 0)
        if not app or not since:
            return
        seconds = time.time() - since
        self.current["since"] = time.time()
        if seconds >= 1:
            try:
                self.store.add_app_usage(app, seconds, self.current.get("title") or "")
            except Exception:
                pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                app, title = windows.foreground_app()
                if app and app != self.current.get("app"):
                    self._flush()  # 换程序了，先把上一条结掉
                    self.current = {"app": app, "title": title, "since": time.time()}
                elif app:
                    self.current["title"] = title
                else:
                    self._flush()
                    self.current = {"app": "", "title": "", "since": 0.0}
                self._flush()  # 同一个程序继续累加
            except Exception:
                pass
            self._stop.wait(self.interval)


_MONITOR: "UsageMonitor | None" = None


def set_monitor(monitor) -> None:
    global _MONITOR
    _MONITOR = monitor


def current_app() -> dict:
    """现在前台是什么（没有监视器就现问一下）"""
    if _MONITOR is not None:
        app = _MONITOR.current.get("app") or ""
        if app:
            return {"app": app, "title": _MONITOR.current.get("title") or ""}
    app, title = windows.foreground_app()
    return {"app": app or "", "title": title or ""}
