"""窗口相关的小工具：列出、判断状态、关掉

只用 win32gui / win32process / psutil —— 客户端本来就装了这几个。
"""

from __future__ import annotations

import ctypes
import os

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


def _window_state(hwnd: int, width: int, height: int, screen_w: int, screen_h: int) -> str:
    """这个窗口是什么状态。

    ⚠️ win32gui.IsZoomed 在有些 pywin32 版本里没有 —— 一旦用它判断，
    异常会被上层吞掉，整行窗口就消失了（前台窗口看不见就是这么来的）。
    这里先用 GetWindowPlacement（showCmd：1 正常 / 2 最小化 / 3 最大化），
    再用 IsIconic 兜底，最后才靠尺寸猜全屏。每步各自兜底。
    """
    try:
        import win32gui

        placement = win32gui.GetWindowPlacement(hwnd)
        show = placement[1] if len(placement) > 1 else 0
        if show == 2:
            return "min"
        if show == 3:
            return "max"
        if show == 1 and screen_w and screen_h and width >= screen_w and height >= screen_h:
            return "full"
        if show == 1:
            return "normal"
    except Exception:
        pass

    try:
        import win32gui

        if win32gui.IsIconic(hwnd):
            return "min"
    except Exception:
        pass

    if screen_w and screen_h and width >= screen_w and height >= screen_h:
        return "full"
    return "normal"


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
            # 状态判断单独兜底：某个 API 不在也不该丢掉这一行
            state = _window_state(hwnd, width, height, screen_w, screen_h)

            items.append(
                {
                    "hwnd": int(hwnd),
                    "title": title,
                    "app": _proc_name(pid) or (f"pid {pid}" if pid else "未知程序"),
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


def force_close_window(hwnd: int) -> tuple[bool, str]:
    """强制关：WM_CLOSE 不理的（前台程序、全屏游戏常见），直接结束它的进程

    会丢未保存的东西，所以界面上要确认一下再用。
    """
    try:
        import psutil
        import win32gui
        import win32process

        handle = int(hwnd)
        if not handle or not win32gui.IsWindow(handle):
            return False, "这个窗口已经不在了"
        title = win32gui.GetWindowText(handle) or "（没标题）"
        _, pid = win32process.GetWindowThreadProcessId(handle)
        if not pid:
            return False, "问不到这是哪个进程"
        if pid == os.getpid():
            return False, "这是客户端自己，不关"

        proc = psutil.Process(int(pid))
        name = proc.name()
        proc.kill()
        return True, f"已经强制结束了：{name}（{title}）"
    except Exception as err:
        return False, f"强制关失败：{type(err).__name__}: {err}"


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
