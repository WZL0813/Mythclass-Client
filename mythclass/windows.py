"""窗口相关的小工具：列出、判断状态、关掉

只用 win32gui / win32process / psutil —— 客户端本来就装了这几个。
"""

from __future__ import annotations

import ctypes

STATE_TEXT = {
    "min": "最小化",
    "max": "最大化",
    "full": "全屏",
    "normal": "普通窗口",
}


def _screen_rect() -> tuple[int, int]:
    try:
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return (0, 0)


def _proc_name(pid: int) -> str:
    if not pid:
        return ""
    try:
        import psutil

        return psutil.Process(pid).name() or ""
    except Exception:
        return ""


def foreground_pid() -> int:
    """当前前台窗口属于哪个进程（拿不到返回 0）"""
    try:
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return 0
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return int(pid or 0)
    except Exception:
        return 0


def foreground_app() -> tuple[str, str]:
    """当前前台：(进程名, 窗口标题)"""
    try:
        import win32gui

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return ("", "")
        title = win32gui.GetWindowText(hwnd) or ""
        return (_proc_name(foreground_pid()), title)
    except Exception:
        return ("", "")


def list_windows() -> list[dict]:
    """所有「有标题、看得见」的窗口，按前台优先排前面"""
    try:
        import win32gui
        import win32process
    except Exception:
        return []

    screen_w, screen_h = _screen_rect()
    fg = 0
    try:
        fg = win32gui.GetForegroundWindow()
    except Exception:
        pass

    items: list[dict] = []

    def visit(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            if not title:
                return True
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width, height = right - left, bottom - top
            if width <= 0 or height <= 0:
                return True

            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if win32gui.IsIconic(hwnd):
                state = "min"
            elif win32gui.IsZoomed(hwnd):
                state = "max"
            elif screen_w and screen_h and width >= screen_w and height >= screen_h:
                # 铺满整块屏幕又不是最大化 —— 那就是全屏（游戏、播放器常见）
                state = "full"
            else:
                state = "normal"

            items.append(
                {
                    "hwnd": int(hwnd),
                    "title": title,
                    "app": _proc_name(pid),
                    "pid": int(pid or 0),
                    "state": state,
                    "stateText": STATE_TEXT.get(state, state),
                    "size": [width, height],
                    "active": int(hwnd) == int(fg),
                }
            )
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(visit, None)
    except Exception:
        pass

    items.sort(key=lambda w: (not w["active"], w["state"] != "normal", w["app"].lower(), w["title"]))
    return items


def close_window(hwnd: int) -> tuple[bool, str]:
    """关掉一个窗口（发 WM_CLOSE，等于点了右上角的叉）"""
    try:
        import win32gui

        handle = int(hwnd)
        if not handle or not win32gui.IsWindow(handle):
            return False, "这个窗口已经不在了"
        title = win32gui.GetWindowText(handle) or "（没标题）"
        win32gui.PostMessage(handle, 0x0010, 0, 0)  # WM_CLOSE
        return True, f"已经让它关了：{title}"
    except Exception as err:
        return False, f"关不掉：{type(err).__name__}: {err}"
