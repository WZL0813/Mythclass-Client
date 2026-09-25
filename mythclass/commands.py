"""命令执行：老师发什么，这儿就干什么

每个命令返回 (是否成功, 说明)。屏幕相关的不在这儿，由 runtime 直接开关流。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.request
from pathlib import Path

from . import config, netban

CREATE_NO_WINDOW = 0x08000000


def _hidden(cmd, **kwargs):
    """起个不弹黑框的子进程"""
    kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    kwargs.setdefault("shell", isinstance(cmd, str))
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.STDOUT)
    return subprocess.run(cmd, **kwargs)


def _run(cmd) -> tuple[bool, str]:
    try:
        result = _hidden(cmd)
        output = (result.stdout or b"").decode("utf-8", "ignore").strip()
        return result.returncode == 0, output or f"退出码 {result.returncode}"
    except Exception as err:
        return False, str(err)


# ============================== 各种命令 ==============================


def cmd_lock(args: dict) -> tuple[bool, str]:
    try:
        ctypes.windll.user32.LockWorkStation()
        return True, "已锁屏"
    except Exception as err:
        return False, str(err)


def cmd_unlock(args: dict) -> tuple[bool, str]:
    # Windows 不允许程序替谁解锁，这是系统边界
    return True, "解锁需要人在机器前输入密码，这边只能提示到这"


def cmd_shutdown(args: dict) -> tuple[bool, str]:
    return _run("shutdown /s /t 5 /c \"Mythclass：老师要求关机\"")


def cmd_reboot(args: dict) -> tuple[bool, str]:
    return _run("shutdown /r /t 5 /c \"Mythclass：老师要求重启\"")


def cmd_logout(args: dict) -> tuple[bool, str]:
    return _run("shutdown /l")


NOTICE_TITLE = "Mythclass消息通知"


def _notice_options(raw) -> list[dict]:
    """把教师端传来的选项规整成最多三个按钮

    位置决定样式（主人定的）：
      第一个 = 高亮按钮，第二个 = 普通按钮，第三个 = 输入框
    没勾的（on=False）直接跳过。
    """
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for index, item in enumerate(raw[:3]):
        if not isinstance(item, dict):
            continue
        if item.get("on") is False:
            continue
        label = str(item.get("label") or "").strip()
        if index == 2:
            send = str(item.get("send") or "").strip() or "发送"
            out.append({"kind": "input", "label": label or "写点什么", "send": send})
        elif index == 0:
            out.append({"kind": "primary", "label": label or "知道了"})
        else:
            out.append({"kind": "normal", "label": label or "稍后再说"})
    return out


def _int_or(value, fallback: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return fallback
    return max(low, min(number, high))


def cmd_message(args: dict, on_reply=None, wait: bool = False) -> tuple[bool, str]:
    """弹一条通知。

    能自定义：窗口大小、标题/内容/按钮的字号、自适应最大、
    置顶、全屏、最多三个回复选项。

    on_reply：学生回答后用这个回调把答案交出去（走 socket 的用）
    wait：等学生回答完再返回（局域网网页那种一问一答的用，最多等 120 秒）
    """
    title = str(args.get("title") or "老师有话要说").strip() or "老师有话要说"
    body = str(args.get("body") or args.get("text") or "").strip() or "老师有话要说"
    topmost = bool(args.get("topmost", True))
    fullscreen = bool(args.get("fullscreen", False))
    options = _notice_options(args.get("options"))

    size = args.get("size") or {}
    win_w = _int_or(size.get("w"), 520, 320, 2400)
    win_h = _int_or(size.get("h"), 300, 180, 1600)
    auto_fit = bool(args.get("autoFit", False))

    fonts = args.get("fontSize") or {}
    font_title = _int_or(fonts.get("title"), 16, 9, 72)
    font_body = _int_or(fonts.get("body"), 12, 8, 60)
    font_button = _int_or(fonts.get("button"), 10, 8, 40)
    # 按钮上的字比正文小一点：tk 的 Button 不好调内边距，靠字号拉开层次
    button_size = max(font_button, 8)

    box: dict = {"answer": None}
    done = threading.Event() if wait else None

    def show() -> None:
        root = tk.Tk()
        # 窗口标题固定，不跟着内容变（主人要求）
        root.title(NOTICE_TITLE)
        if topmost:
            root.attributes("-topmost", True)
        root.configure(bg="#f3efe3")
        if fullscreen:
            root.attributes("-fullscreen", True)
        else:
            screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
            if auto_fit:
                # 自适应：先让内容自己算出需要多大，再按屏幕留边
                root.update_idletasks()
                w = max(360, min(root.winfo_reqwidth() + 40, int(screen_w * 0.92)))
                h = max(180, min(root.winfo_reqheight() + 30, int(screen_h * 0.92)))
            else:
                w, h = win_w, win_h
            w = min(w, int(screen_w * 0.96))
            h = min(h, int(screen_h * 0.96))
            root.geometry(
                "%dx%d+%d+%d"
                % (w, h, max(screen_w // 2 - w // 2, 0), max(screen_h // 3, 0))
            )

        frame = tk.Frame(root, bg="#f3efe3", padx=30, pady=26)
        frame.pack(fill="both", expand=True)

        tk.Label(
            frame, text=title, bg="#f3efe3", fg="#2f4f3e",
            font=("Microsoft YaHei", font_title, "bold"),
            wraplength=max(300, win_w - 80), justify="left",
        ).pack(anchor="w")
        tk.Message(
            frame, text=body, bg="#f3efe3", fg="#10160f",
            width=max(280, win_w - 80), font=("Microsoft YaHei", font_body),
        ).pack(anchor="w", pady=(14, 18), fill="both", expand=True)

        def finish(answer: str) -> None:
            box["answer"] = answer
            if on_reply is not None:
                try:
                    on_reply(answer)
                except Exception:
                    pass
            if done is not None:
                done.set()
            try:
                root.destroy()
            except Exception:
                pass

        bar = tk.Frame(frame, bg="#f3efe3")
        bar.pack(fill="x", side="bottom")

        for option in options:
            if option["kind"] == "input":
                hint = option["label"]
                entry = tk.Entry(bar, font=("Microsoft YaHei", button_size), width=20, relief="flat",
                                 bg="#ffffff", fg="#9aa79a")
                entry.pack(side="left", padx=(0, 8), ipady=5)
                # 提示文字做成真占位符：显示在输入框里，一聚焦就清掉
                entry.insert(0, hint)

                def on_focus_in(_event, ent=entry, tip=hint):
                    if ent.get() == tip:
                        ent.delete(0, "end")
                        ent.configure(fg="#10160f")

                def on_focus_out(_event, ent=entry, tip=hint):
                    if not ent.get().strip():
                        ent.insert(0, tip)
                        ent.configure(fg="#9aa79a")

                def submit(ent=entry, tip=hint):
                    value = ent.get().strip()
                    if value == tip:
                        value = ""
                    finish("（输入）" + (value or "（空）"))

                entry.bind("<FocusIn>", on_focus_in)
                entry.bind("<FocusOut>", on_focus_out)
                entry.bind("<Return>", lambda _e, f=submit: f())
                tk.Button(
                    bar, text=option.get("send") or "发送", relief="flat", padx=16, pady=4,
                    bg="#e2ddcc", fg="#10160f", font=("Microsoft YaHei", button_size), command=submit,
                ).pack(side="left", padx=(0, 8))
            elif option["kind"] == "primary":
                tk.Button(
                    bar, text=option["label"], relief="flat", padx=20, pady=5,
                    bg="#2f4f3e", fg="#f3efe3", activebackground="#3f6b52", activeforeground="#ffffff",
                    font=("Microsoft YaHei", button_size),
                    command=lambda o=option: finish(o["label"]),
                ).pack(side="right", padx=(8, 0))
            else:
                tk.Button(
                    bar, text=option["label"], relief="flat", padx=18, pady=5,
                    bg="#e2ddcc", fg="#10160f", font=("Microsoft YaHei", button_size),
                    command=lambda o=option: finish(o["label"]),
                ).pack(side="right", padx=(8, 0))

        if not options:
            tk.Button(
                bar, text="知道了", relief="flat", padx=20, pady=5,
                bg="#2f4f3e", fg="#f3efe3", command=lambda: finish("知道了"),
            ).pack(side="right")

        # 右上角关掉也算「关掉了」
        root.protocol("WM_DELETE_WINDOW", lambda: finish("（关掉了）"))

        # 退出全屏放右上角（主人要求）
        if fullscreen:
            quit_btn = tk.Button(
                root, text="退出全屏", relief="flat", padx=12, pady=3,
                bg="#e2ddcc", fg="#10160f",
                command=lambda: root.attributes("-fullscreen", False),
            )
            quit_btn.place(relx=1.0, x=-12, y=10, anchor="ne")
        root.mainloop()

    threading.Thread(target=show, daemon=True).start()

    if not wait:
        return True, "通知已弹出，等他回答"

    # 等回答（局域网网页用）：最多两分钟
    if done is not None and not done.wait(120):
        return False, "等了两分钟没人回"
    return True, f"回答：{box['answer']}"


def cmd_open_url(args: dict) -> tuple[bool, str]:
    url = str(args.get("url") or "").strip()
    if not url:
        return False, "没给网址"
    try:
        os.startfile(url)  # noqa: S606
        return True, f"打开了 {url}"
    except OSError:
        return _run(["cmd", "/c", "start", "", url])


def cmd_open_app(args: dict) -> tuple[bool, str]:
    path = str(args.get("path") or "").strip()
    if not path:
        return False, "没给路径"
    try:
        subprocess.Popen(path, creationflags=CREATE_NO_WINDOW, shell=True)
        return True, f"起了 {path}"
    except Exception as err:
        return False, str(err)


def cmd_file_distribute(args: dict) -> tuple[bool, str]:
    """从老师给的地址下个文件，落到指定位置"""
    url = str(args.get("url") or "").strip()
    target = str(args.get("path") or "").strip()
    if not url:
        return False, "没给下载地址"
    if not target:
        target = str(Path(tempfile.gettempdir()) / (url.split("/")[-1] or "mythclass-file"))
    try:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, target)
        return True, f"已保存到 {target}"
    except Exception as err:
        return False, str(err)


def cmd_screen_broadcast(args: dict) -> tuple[bool, str]:
    """全屏开个网页，当广播用"""
    url = str(args.get("url") or "").strip()
    if not url:
        return False, "没给广播地址"

    def show():
        root = tk.Tk()
        root.attributes("-fullscreen", True)
        root.configure(bg="#10160f")
        label = tk.Label(root, text="屏幕广播中\n按 Esc 退出", bg="#10160f", fg="#f3efe3", font=("Microsoft YaHei", 26))
        label.pack(expand=True)
        tk.Button(root, text="打开广播内容", command=lambda: os.startfile(url)).pack(pady=10)
        root.bind("<Escape>", lambda _e: root.destroy())
        root.mainloop()

    import threading

    threading.Thread(target=show, name="mythclass-broadcast", daemon=True).start()
    return True, "广播已经开起来"


def cmd_net_ban(args: dict) -> tuple[bool, str]:
    """禁网 / 放开。要管理员权限。

    走 netban 的白名单模式：断掉外网，但**留着到服务端那条线**。
    老实现是一句 netsh 拦掉所有出站，连自己都掐——老师再也发不出「放开上网」，
    那台机器就只能人到跟前解锁了。
    """
    enable = str(args.get("enable", "true")).lower() in ("1", "true", "yes", "on")

    if not enable:
        return netban.lift()

    servers = config.enabled_servers(config.load())
    raw_minutes = args.get("minutes")
    try:
        minutes = int(str(raw_minutes)) if raw_minutes not in (None, "") else None
    except ValueError:
        minutes = None
    return netban.apply(servers, minutes=minutes)


REGISTRY = {
    "lock": cmd_lock,
    "unlock": cmd_unlock,
    "shutdown": cmd_shutdown,
    "reboot": cmd_reboot,
    "logout": cmd_logout,
    "message": cmd_message,
    "open_url": cmd_open_url,
    "open_app": cmd_open_app,
    "file_distribute": cmd_file_distribute,
    "screen_broadcast": cmd_screen_broadcast,
    "net_ban": cmd_net_ban,
}


def execute(command: str, args: dict | None = None) -> tuple[bool, str]:
    handler = REGISTRY.get(command)
    if not handler:
        return False, f"不认识这个命令：{command}"
    try:
        return handler(args or {})
    except Exception as err:
        return False, f"{type(err).__name__}: {err}"


def known_commands() -> list[str]:
    return sorted(REGISTRY.keys()) + ["screen_start", "screen_stop"]


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


if sys.platform != "win32":  # 开发机上跑得动，别炸
    def _noop(_args):  # type: ignore
        return False, "这个命令只在 Windows 上有用"

    for _name in list(REGISTRY):
        REGISTRY[_name] = _noop
