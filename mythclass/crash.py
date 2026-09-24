"""客户端：出事就上报 + 桌面留日志 + 不弹框 + 自动退出

为什么要这套：
- 教室里的机器没人看着，弹个框就得等老师跑过去点掉
- 桌面上的日志方便现场的人直接拷给管理员
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
    return "\n".join(lines) + "\n"


def write_desktop_log(text: str) -> Path | None:
    """在桌面写一份日志。写不进去就算了（桌面可能是只读/重定向的）"""
    try:
        target = desktop_dir()
        target.mkdir(parents=True, exist_ok=True)
        name = f"Mythclass出错日志-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        path = target / name
        path.write_text(text, encoding="utf-8")
        return path
    except Exception:
        return None


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
    """上报 + 写桌面日志；（该退出时）退出"""
    text = build_text(kind, message, detail)
    path = write_desktop_log(text)
    sent = send_to_server(kind, message, detail)

    # 控制台可能是不支持这些字符的老编码，别让「上报」这件事本身再炸一次
    try:
        print(
            f"[Mythclass] {kind}：{message}"
            + (f" | 日志：{path}" if path else "")
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
    if _recent_crash():
        print("[Mythclass] 刚崩过不久，这次不再退出，先把能干的活干完。", file=sys.stderr)
        return

    _mark_crash()
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
