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
import time
import tkinter as tk
import urllib.request
from datetime import datetime
from pathlib import Path

from . import config, netban

CREATE_NO_WINDOW = 0x08000000


SAFE_ENV = "MYTHCLASS_SAFE"


def safe_mode() -> bool:
    """测试模式：环境里有 MYTHCLASS_SAFE 就成立。

    自动化测试会带上它 —— 免得点着点着真把机器关了、网断了。
    """
    import os

    return bool(os.environ.get(SAFE_ENV))


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
    if safe_mode():
        return False, "关机在测试模式下被挡下了（环境里有 MYTHCLASS_SAFE）"
    return _run("shutdown /s /t 5 /c \"Mythclass：老师要求关机\"")


def cmd_reboot(args: dict) -> tuple[bool, str]:
    if safe_mode():
        return False, "重启在测试模式下被挡下了（环境里有 MYTHCLASS_SAFE）"
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


# 自适应时逐个试这些倍率（低到高），放得下就往上走
_FIT_STEPS = (1.0, 1.15, 1.3, 1.5, 1.7, 1.95, 2.2, 2.5, 2.85, 3.2, 3.6, 4.0)


def cmd_message(args: dict, on_reply=None, wait: bool = False) -> tuple[bool, str]:
    """弹一条通知。

    能自定义：窗口大小、标题/内容/按钮的字号、自适应最大、
    置顶、全屏、最多三个回复选项。

    自适应（autoFit）指的是：**把所有文字按原比例整体放大到屏幕最大**。
    如果调用方自己改过比例（标题 30 / 内容 12 之类），就按他那套比例放大，
    倍率对三者一视同仁，比例不变。

    on_reply：学生回答后用这个回调把答案交出去（走 socket 的用）
    wait：等学生回答完再返回（局域网网页那种一问一答的用，最多等 120 秒）
    """
    title = str(args.get("title") or "老师有话要说").strip() or "老师有话要说"
    body = str(args.get("body") or args.get("text") or "").strip() or "老师有话要说"
    topmost = bool(args.get("topmost", True))
    fullscreen = bool(args.get("fullscreen", False))
    options = _notice_options(args.get("options"))

    # 纯弹出、不需要回复：到点自己关（主人要的倒计时通知），最长 3600 秒
    auto_close = _int_or(args.get("autoClose"), 0, 0, 3600)

    size = args.get("size") or {}
    win_w = _int_or(size.get("w"), 520, 320, 2400)
    win_h = _int_or(size.get("h"), 300, 180, 1600)
    auto_fit = bool(args.get("autoFit", False))

    fonts = args.get("fontSize") or {}
    base_title = _int_or(fonts.get("title"), 16, 9, 72)
    base_body = _int_or(fonts.get("body"), 12, 8, 60)
    base_button = _int_or(fonts.get("button"), 10, 8, 40)

    box: dict = {"answer": None}
    done = threading.Event() if wait else None

    def show() -> None:
        root = tk.Tk()
        # 窗口标题固定，不跟着内容变（主人要求）
        root.title(NOTICE_TITLE)
        if topmost:
            root.attributes("-topmost", True)
        root.configure(bg="#f3efe3")
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()

        frame = tk.Frame(root, bg="#f3efe3", padx=30, pady=26)
        frame.pack(fill="both", expand=True)

        title_label = tk.Label(
            frame, text=title, bg="#f3efe3", fg="#2f4f3e", justify="left",
            font=("Microsoft YaHei", base_title, "bold"),
        )
        title_label.pack(anchor="w")

        body_label = tk.Message(
            frame, text=body, bg="#f3efe3", fg="#10160f",
            font=("Microsoft YaHei", base_body),
        )
        body_label.pack(anchor="w", pady=(14, 18), fill="both", expand=True)

        bar = tk.Frame(frame, bg="#f3efe3")
        bar.pack(fill="x", side="bottom")

        scale_box = {"value": 1.0}
        # (控件, 原始字号) —— 放大时统一乘倍率，比例保持不变
        sized: list = [(title_label, base_title), (body_label, base_body)]

        def apply_scale(scale: float) -> None:
            scale_box["value"] = scale
            wrap = max(280, int(win_w * min(scale, 1.6)) - 80)
            for widget, base in sized:
                step = 1 if widget is body_label else 1
                widget.configure(font=("Microsoft YaHei", max(8, int(round(base * scale))), "bold")
                                 if widget is title_label
                                 else ("Microsoft YaHei", max(8, int(round(base * scale)))))
            title_label.configure(wraplength=wrap)
            body_label.configure(width=wrap)
            root.update_idletasks()

        # 倒计时：显示还剩几秒，到点自己关
        countdown_label = None
        if auto_close > 0:
            countdown_label = tk.Label(
                bar, text="", bg="#f3efe3", fg="#6f8a70",
                font=("Microsoft YaHei", max(9, base_button)),
            )
            countdown_label.pack(side="left", padx=(0, 10))
            sized.append((countdown_label, base_button))

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

        for option in options:
            if option["kind"] == "input":
                hint = option["label"]
                entry = tk.Entry(bar, width=20, relief="flat", bg="#ffffff", fg="#9aa79a",
                                 font=("Microsoft YaHei", base_button))
                entry.pack(side="left", padx=(0, 8), ipady=5)
                entry.insert(0, hint)
                sized.append((entry, base_button))

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
                send_btn = tk.Button(bar, text=option.get("send") or "发送", relief="flat",
                                     padx=16, pady=4, bg="#e2ddcc", fg="#10160f",
                                     font=("Microsoft YaHei", base_button), command=submit)
                send_btn.pack(side="left", padx=(0, 8))
                sized.append((send_btn, base_button))
            elif option["kind"] == "primary":
                btn = tk.Button(
                    bar, text=option["label"], relief="flat", padx=20, pady=5,
                    bg="#2f4f3e", fg="#f3efe3", activebackground="#3f6b52", activeforeground="#ffffff",
                    font=("Microsoft YaHei", base_button),
                    command=lambda o=option: finish(o["label"]),
                )
                btn.pack(side="right", padx=(8, 0))
                sized.append((btn, base_button))
            else:
                btn = tk.Button(
                    bar, text=option["label"], relief="flat", padx=18, pady=5,
                    bg="#e2ddcc", fg="#10160f", font=("Microsoft YaHei", base_button),
                    command=lambda o=option: finish(o["label"]),
                )
                btn.pack(side="right", padx=(8, 0))
                sized.append((btn, base_button))

        if not options:
            # 纯弹出、不用回复：给个「关闭」就行，回传也别写成「回答」
            if auto_close > 0:
                btn = tk.Button(bar, text="关闭", relief="flat", padx=20, pady=5,
                                bg="#2f4f3e", fg="#f3efe3", font=("Microsoft YaHei", base_button),
                                command=lambda: finish("（关掉了）"))
            else:
                btn = tk.Button(bar, text="知道了", relief="flat", padx=20, pady=5,
                                bg="#2f4f3e", fg="#f3efe3", font=("Microsoft YaHei", base_button),
                                command=lambda: finish("知道了"))
            btn.pack(side="right")
            sized.append((btn, base_button))

        # 右上角关掉也算「关掉了」
        root.protocol("WM_DELETE_WINDOW", lambda: finish("（关掉了）"))

        if fullscreen:
            # 退出全屏放右上角（主人要求）
            quit_btn = tk.Button(
                root, text="退出全屏", relief="flat", padx=12, pady=3,
                bg="#e2ddcc", fg="#10160f", font=("Microsoft YaHei", base_button),
                command=lambda: root.attributes("-fullscreen", False),
            )
            quit_btn.place(relx=1.0, x=-12, y=10, anchor="ne")
            sized.append((quit_btn, base_button))
            root.attributes("-fullscreen", True)
        else:
            limit_w, limit_h = int(screen_w * 0.9), int(screen_h * 0.9)
            if auto_fit:
                # 先把窗口放到屏幕上，再逐档放大文字，量出来的内容放得下就留着
                root.geometry("%dx%d+0+0" % (min(win_w, screen_w), min(win_h, screen_h)))
                root.update_idletasks()
                best = _FIT_STEPS[0]
                for scale in _FIT_STEPS:
                    apply_scale(scale)
                    if root.winfo_reqwidth() <= limit_w and root.winfo_reqheight() <= limit_h:
                        best = scale
                    else:
                        break
                apply_scale(best)
                root.update_idletasks()
                w = min(max(root.winfo_reqwidth() + 30, 360), int(screen_w * 0.96))
                h = min(max(root.winfo_reqheight() + 24, 180), int(screen_h * 0.96))
            else:
                w, h = min(win_w, int(screen_w * 0.96)), min(win_h, int(screen_h * 0.96))
                apply_scale(1.0)

            root.geometry(
                "%dx%d+%d+%d"
                % (w, h, max(screen_w // 2 - w // 2, 0), max(screen_h // 3, 0))
            )

        if auto_close > 0 and countdown_label is not None:
            deadline = time.time() + auto_close

            def tick() -> None:
                left = int(round(deadline - time.time()))
                if left <= 0:
                    finish("（时间到，自己关了）")
                    return
                try:
                    countdown_label.configure(text=f"{left} 秒后自动关闭")
                    root.after(500, tick)
                except Exception:
                    pass

            tick()

        root.mainloop()

    threading.Thread(target=show, daemon=True).start()

    if not wait:
        return True, "通知已弹出，等他回答"

    # 等回答（局域网网页用）：最多两分钟
    if done is not None and not done.wait(120):
        return False, "等了两分钟没人回"
    return True, f"回答：{box['answer']}"



def cmd_usage_stats(args: dict) -> tuple[bool, str]:
    """软件使用时长排行（返回 JSON 文本，两端都解析它）"""
    import json

    from . import usage as usage_mod

    items = []
    try:
        from .db import RecordStore

        items = RecordStore().usage_summary(int(args.get("limit") or 40))
    except Exception:
        items = []
    return True, json.dumps(
        {"items": items, "current": usage_mod.current_app()}, ensure_ascii=False
    )


def _drive_items() -> list[dict]:
    """列出所有盘符（带总量和剩余空间）"""
    items: list[dict] = []
    try:
        import psutil

        for part in psutil.disk_partitions(all=False):
            mount = part.mountpoint or ""
            if not mount:
                continue
            name = mount.rstrip("\\/") or mount
            total = 0
            free = 0
            try:
                usage = psutil.disk_usage(mount)
                total, free = usage.total, usage.free
            except Exception:
                pass
            items.append(
                {
                    "name": name,
                    "dir": True,
                    "drive": True,
                    "size": total,
                    "free": free,
                    "mtime": part.fstype or "",
                }
            )
    except Exception:
        # psutil 不在就退回 Win32
        try:
            import ctypes

            mask = ctypes.windll.kernel32.GetLogicalDrives()
            for i in range(26):
                if mask & (1 << i):
                    letter = chr(ord("A") + i) + ":"
                    items.append({"name": letter, "dir": True, "drive": True, "size": 0, "free": 0, "mtime": ""})
        except Exception:
            pass
    return items


def cmd_list_dir(args: dict) -> tuple[bool, str]:
    """列一个目录（磁盘文件查看用）"""
    import json
    from datetime import datetime as _dt

    raw = str(args.get("path") or "").strip()
    # 不给路径 / 给「此电脑」/ 给盘符根 → 先给盘符列表，省得用户手打路径
    if raw in ("", "drives", "此电脑", "我的电脑", "/", "\\"):
        import json as _json

        return True, _json.dumps(
            {"path": "", "parent": "", "drives": True, "items": _drive_items()},
            ensure_ascii=False,
        )

    target = Path(raw).expanduser()
    if not target.exists():
        return False, f"没有这个路径：{target}"
    if target.is_file():
        return False, f"这是个文件，不是目录：{target}"

    items = []
    try:
        for entry in os.scandir(target):
            try:
                stat = entry.stat()
                items.append(
                    {
                        "name": entry.name,
                        "dir": entry.is_dir(),
                        "drive": False,
                        "size": 0 if entry.is_dir() else stat.st_size,
                        "mtime": _dt.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    }
                )
            except Exception:
                continue
    except PermissionError:
        return False, f"没权限看这个目录：{target}"
    except Exception as err:
        return False, f"列目录失败：{type(err).__name__}: {err}"

    items.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    parent = str(target.parent) if target.parent != target else ""
    return True, json.dumps(
        {"path": str(target), "parent": parent, "items": items[:400]}, ensure_ascii=False
    )


def cmd_list_windows(args: dict) -> tuple[bool, str]:
    """当前开着的窗口（含最小化/最大化/全屏）"""
    import json

    from . import windows as windows_mod

    return True, json.dumps({"items": windows_mod.list_windows()}, ensure_ascii=False)


def cmd_force_close_window(args: dict) -> tuple[bool, str]:
    """强制关掉一个窗口的进程"""
    from . import windows as windows_mod

    return windows_mod.force_close_window(int(args.get("hwnd") or 0))


def cmd_close_window(args: dict) -> tuple[bool, str]:
    """关掉指定的窗口"""
    from . import windows as windows_mod

    return windows_mod.close_window(int(args.get("hwnd") or 0))


def cmd_quiet(args: dict) -> tuple[bool, str]:
    """黑屏安静：开 / 关"""
    from . import quiet as quiet_mod

    on = args.get("on")
    if on is None:
        on = True
    if isinstance(on, str):
        on = on.lower() not in ("0", "false", "off", "no")
    if on:
        return quiet_mod.show(str(args.get("text") or ""))
    return quiet_mod.hide()


def cmd_hand(args: dict) -> tuple[bool, str]:
    """举手状态（学生自己在黑屏里点，老师也能看/清）"""
    import json

    from . import quiet as quiet_mod

    on = args.get("on")
    if on is None:
        return True, json.dumps(quiet_mod.status(), ensure_ascii=False)
    if isinstance(on, str):
        on = on.lower() not in ("0", "false", "off", "no")
    quiet_mod.raise_hand(bool(on))
    return True, json.dumps(quiet_mod.status(), ensure_ascii=False)


def cmd_screenshot(args: dict) -> tuple[bool, str]:
    """截一张图存到本机（全分辨率 PNG，尽量清晰），返回存到哪儿了。

    局域网那个页面点「截图」走的是 /api/screenshot（直接回图，能看能存）；
    这个命令是给「截图存到机器上」用的。
    """
    try:
        import mss
        from PIL import Image

        folder = Path(os.path.expanduser("~")) / "Pictures" / "Mythclass"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = folder / f"截图-{stamp}.png"

        with mss.mss() as grabber:
            monitor = grabber.monitors[1] if len(grabber.monitors) > 1 else grabber.monitors[0]
            shot = grabber.grab(monitor)
            image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        image.save(dest, format="PNG")
        return True, f"截好了：{dest}（{image.width}x{image.height}）"
    except Exception as err:
        return False, f"截图失败：{type(err).__name__}: {err}"


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


def cmd_net_allow(args: dict) -> tuple[bool, str]:
    """放开上网（局域网那个按钮发的就是这个名字）"""
    if safe_mode():
        return False, "放开上网在测试模式下被挡下了（环境里有 MYTHCLASS_SAFE）"
    from . import netban

    return netban.lift()


def cmd_net_ban(args: dict) -> tuple[bool, str]:
    """禁网 / 放开。要管理员权限。

    走 netban 的白名单模式：断掉外网，但**留着到服务端那条线**。
    老实现是一句 netsh 拦掉所有出站，连自己都掐——老师再也发不出「放开上网」，
    那台机器就只能人到跟前解锁了。
    """
    if safe_mode():
        return False, "禁网在测试模式下被挡下了（环境里有 MYTHCLASS_SAFE）"
    enable = str(args.get("enable", "true")).lower() in ("1", "true", "yes", "on")

    if not enable:
        return netban.lift()

    # 改防火墙要管理员权限。没有就别假装成功，说清楚怎么修。
    if not is_admin():
        return False, (
            "改防火墙要管理员权限，客户端现在没有。\n"
            "修法：用安装包装一次（安装程序是提权的，会顺手建一个「最高权限登录自启」，"
            "以后开机就是管理员身份），或者右键客户端选「以管理员身份运行」。"
        )

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
    "net_allow": cmd_net_allow,
    "net_ban_lift": cmd_net_allow,
    "screenshot": cmd_screenshot,
    "usage_stats": cmd_usage_stats,
    "list_dir": cmd_list_dir,
    "list_windows": cmd_list_windows,
    "close_window": cmd_close_window,
    "force_close_window": cmd_force_close_window,
    "quiet": cmd_quiet,
    "hand": cmd_hand,
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
