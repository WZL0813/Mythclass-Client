"""客户端：出事就上报 + 桌面留日志 + 不弹框 + 自动退出

为什么要这套：
- 教室里的机器没人看着，弹个框就得等老师跑过去点掉
- 桌面上的日志方便现场的人直接拷给管理员（同样的内容也进 client.log，
  桌面写不出去、或者进程半路被收掉时，还有地方能看）
- 退出之后跟班进程会把它拉起来，等于自动重启

防自锁：120 秒内崩第二次就只上报、不再退出。
不然遇到「必崩」的场景会变成 崩→重启→再崩 的死循环。
"""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from . import __version__, config

GUARD_MIN_GAP = 120  # 秒。两次崩溃间隔小于它就只上报不退出
MARKER = "last-crash.json"
MAX_TEXT = 8000


def utf8_safe(text: str) -> str:
    """把 utf-8 编不出来的字符（孤代理，来自 Windows API / 奇怪文件名）换成 ?

    为什么非做不可：带孤代理的字符串写文件时会先把文件截成 0 字节，
    再在 flush 时抛 UnicodeEncodeError —— 桌面上只剩一个空日志，
    现场的人拷走啥也看不到，等于这次崩溃白报了。
    """
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        return text.encode("utf-8", "replace").decode("utf-8")


def desktop_dir() -> Path:
    """桌面路径。测试可以用 MYTHCLASS_DESKTOP_DIR 顶掉"""
    override = os.environ.get("MYTHCLASS_DESKTOP_DIR")
    if override:
        return Path(override)

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            buf = ctypes.c_wchar_p()
            # FOLDERID_Desktop
            guid = ctypes.create_string_buffer(
                bytes.fromhex("B4BFCC3A DB2C D424 B029 7FE941FFFFCA".replace(" ", ""))
            )
            if ctypes.windll.shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(buf)) == 0:
                path = buf.value
                if path and Path(path).is_dir():
                    return Path(path)
        except Exception:
            pass

        fallback = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
        if fallback.is_dir():
            return fallback

    return Path.home()


def _recent_crash() -> bool:
    try:
        data = json.loads((config.APP_DIR / MARKER).read_text(encoding="utf-8"))
        return (time.time() - float(data.get("at", 0))) < GUARD_MIN_GAP
    except Exception:
        return False


def _mark_crash() -> None:
    try:
        config.ensure_dirs()
        (config.APP_DIR / MARKER).write_text(json.dumps({"at": time.time()}), encoding="utf-8")
    except Exception:
        pass


def build_text(kind: str, message: str, detail: str) -> str:
    lines = [
        "Mythclass 客户端出问题了",
        "=" * 46,
        f"时间      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"机器号    {config.load().get('clientUid') or '(还没定)'}",
        f"客户端    v{__version__}",
        f"系统      {platform.system()} {platform.release()} ({platform.version()})",
        f"Python    {platform.python_version()}",
        f"进程号    {os.getpid()}",
        f"类型      {kind}",
        "",
        "错误信息",
        "-" * 46,
        message or "(没给)",
        "",
        "详细堆栈",
        "-" * 46,
        detail or "(没给)",
        "",
        "这条也会发给服务端。桌面上留这份是方便现场直接拷走。",
    ]
    return utf8_safe("\n".join(lines)) + "\n"


def write_desktop_log(text: str) -> Path | None:
    """在桌面写一份日志。写不进去就算了（桌面可能是只读/重定向的）

    先写 .part 再改名：半路被结束进程/断电时，桌面上只会多一个 .part，
    不会留下一个看着像日志、打开却啥也没有的空文件。
    """
    target = desktop_dir()
    tmp = None
    try:
        target.mkdir(parents=True, exist_ok=True)
        name = f"Mythclass出错日志-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        path = target / name
        tmp = target / (name + ".part")
        with tmp.open("w", encoding="utf-8", errors="replace") as handle:
            handle.write(text)
        os.replace(tmp, path)
        return path
    except Exception:
        try:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return None


def append_to_app_log(text: str) -> None:
    """同样的内容再往 %APPDATA%\\Mythclass\\client.log 塞一份

    桌面上那份可能写不进去（桌面被重定向、被杀进程打断），
    这份跟着主日志走，更新前后都在，远程也捞得到。
    """
    try:
        config.ensure_dirs()
        with (config.APP_DIR / "client.log").open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 崩溃上报\n{text}\n")
    except Exception:
        pass


def send_to_server(kind: str, message: str, detail: str) -> bool:
    """往每个配置好的服务端都试一次。失败就算了，不能因为上报再崩一次"""
    try:
        import requests

        from .api import http_base

        payload = {
            "clientUid": config.load().get("clientUid") or "",
            "kind": kind,
            "message": message[:480],
            "detail": detail[:MAX_TEXT],
            "version": __version__,
        }
        for url in config.enabled_servers(config.load()):
            try:
                resp = requests.post(
                    f"{http_base(url)}/api/client/errors", json=payload, timeout=8
                )
                if resp.status_code < 400:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def report(kind: str, message: str, detail: str = "", fatal: bool = True) -> None:
    """上报 + 写日志；（该退出时）退出

    顺序是有讲究的：先把三份记录落地，再上网报告。
    （更新的时候进程随时可能被安装器/新版客户端收掉 ——
    先报告再写日志的话，日志就没了，等于这次崩溃白报。）
    """
    text = build_text(kind, message, detail)
    recent = _recent_crash()  # 先判断：这是头一回崩，还是刚崩完又崩
    path = write_desktop_log(text)
    append_to_app_log(text)  # 桌面那份写不进去时，主日志里还有一份

    if fatal and not recent:
        # 防自锁的标记也提前写：网络慢的时候被收掉，标记晚写就等于没写
        _mark_crash()

    sent = send_to_server(kind, message, detail)

    # 控制台可能是不支持这些字符的老编码，别让「上报」这件事本身再炸一次
    try:
        print(
            f"[Mythclass] {kind}：{message}"
            + (f" | 日志：{path}" if path else " | 日志写不出去")
            + (" | 已上报服务端" if sent else " | 上报失败"),
            file=sys.stderr,
        )
    except Exception:
        try:
            sys.stderr.write(f"[Mythclass] {kind} (detail in desktop log)\n")
        except Exception:
            pass

    if not fatal:
        return

    # 短时间内的第二次崩溃就不再退出了，免得崩→重启→再崩
    if recent:
        print("[Mythclass] 刚崩过不久，这次不再退出，先把能干的活干完。", file=sys.stderr)
        return

    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(1)


def _hook(exc_type, exc, tb) -> None:
    detail = "".join(traceback.format_exception(exc_type, exc, tb))
    report("未捕获异常", f"{exc_type.__name__}: {exc}", detail, fatal=True)


def _thread_hook(args) -> None:
    detail = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    where = getattr(args.thread, "name", "?")
    report("线程崩了", f"[{where}] {args.exc_type.__name__}: {args.exc_value}", detail, fatal=True)


def install() -> None:
    """装钩子。程序一开始就得装，不然启动阶段的错抓不到"""
    sys.excepthook = _hook
    if hasattr(threading, "excepthook"):
        threading.excepthook = _thread_hook


def safe_call(func, *args, **kwargs):
    """跑一段可能出错的逻辑：出错就按上面那套处理，不往外抛"""
    try:
        return func(*args, **kwargs)
    except SystemExit:
        raise
    except BaseException:
        _hook(*sys.exc_info())
        return None
