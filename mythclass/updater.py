"""检查更新

服务端那边管理员发布一条（版本 + 下载地址 + 说明 + sha256），
客户端来问一句「我 2.5.2，有新的吗」。

下载地址随便是什么：GitHub 的 release 也行、自己服务器上的也行。
下完校验 sha256，然后带管理员权限跑安装包（装到 Program Files 需要提权），
跑起来之后自己退出，把文件让给安装包。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from . import __version__

TAG = "检查更新"

# 最近一次失败的原因（界面上要说清楚，别只说"网络问题"）
LAST_ERROR: list = [""]


def parse_version(text: str) -> list[int] | None:
    """把 2.6.0 / v2.6.0 拆成数字，认不出来返回 None"""
    if not text:
        return None
    nums = []
    for part in str(text).strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            return None
        nums.append(int(digits))
    return nums or None


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    if not a or not b:
        return False
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else 0
        y = b[i] if i < len(b) else 0
        if x != y:
            return x > y
    return False


def server_bases(cfg) -> list[str]:
    """按客户端设置里的连接顺序，列出可用的服务端地址"""
    bases = []
    for item in cfg.get("servers") or []:
        url = (item or {}).get("url") if isinstance(item, dict) else str(item or "")
        url = (url or "").strip().rstrip("/")
        if not url:
            continue
        if url.startswith("ws://"):
            url = "http://" + url[5:]
        elif url.startswith("wss://"):
            url = "https://" + url[6:]
        if url.startswith("http"):
            bases.append(url)
    return bases


def check(cfg, timeout: float = 12.0) -> dict | None:
    """问服务端有没有新版本。

    返回 dict（可能 update=False）或者 None（一个服务端都没问通）。
    """
    from .api import ca_bundle  # 项目里现成的证书处理

    for base in server_bases(cfg):
        url = f"{base}/api/client/update?version={__version__}&platform=win"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"MythclassClient/{__version__}"})
            ctx = None
            bundle = ca_bundle()
            if bundle:
                import ssl

                ctx = ssl.create_default_context(cafile=bundle)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as res:
                data = json.loads(res.read().decode("utf-8"))
                data["server"] = base
                data["current"] = __version__
                return data
        except Exception:
            continue
    return None


def _ssl_context():
    """用项目自带的证书库（api.py 里那份），别让 https 卡在证书上"""
    try:
        from .api import ca_bundle
        import ssl

        bundle = ca_bundle()
        if bundle:
            return ssl.create_default_context(cafile=bundle)
    except Exception:
        pass
    return None


def download(url: str, sha256: str = "", on_progress=None, timeout: float = 600.0) -> Path | None:
    """下载安装包到临时目录，顺手校验 sha256。失败返回 None"""
    name = url.split("/")[-1].split("?")[0] or "MythclassSetup.exe"
    if not name.lower().endswith(".exe"):
        name += ".exe"
    dest = Path(tempfile.gettempdir()) / name

    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"MythclassClient/{__version__}"})
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as res:
            total = int(res.headers.get("Content-Length") or 0)
            done = 0
            digest = hashlib.sha256()
            with open(dest, "wb") as fh:
                while True:
                    chunk = res.read(256 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if on_progress and total:
                        on_progress(done, total)
    except Exception as err:
        LAST_ERROR[:] = [f"{type(err).__name__}: {err}"]
        return None

    if sha256:
        actual = digest.hexdigest().lower()
        if actual != sha256.strip().lower():
            try:
                dest.unlink()
            except Exception:
                pass
            LAST_ERROR[:] = [f"下载下来的文件 sha256 对不上（要 {sha256[:12]}…，实际 {actual[:12]}…）"]
            return None
    LAST_ERROR[:] = [""]
    return dest


def run_installer(path: Path) -> bool:
    """带管理员权限跑安装包。装到 Program Files 要提权，所以走 runas"""
    try:
        if os.name != "nt":
            return False
        import ctypes

        # ShellExecuteW 的 runas：弹 UAC，用户点了同意才开始装
        # 注意：**不能**加 --nolaunch —— 安装器装完会先把旧客户端杀掉，
        # 不许它启动新的，机器上就没客户端了，要等下次登录才自启。
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", str(path), "--silent --noelevate", None, 1
        )
        return int(result) > 32
    except Exception:
        return False


def schedule_relaunch(delay: float = 45.0) -> None:
    """兜底：过一会儿把客户端再拉起来一次。

    正常情况安装器自己会拉（去掉 --nolaunch 之后）。
    万一它没拉（比如用户点了取消、或者安装器版本老），
    这个小助手会在 delay 秒后补一次 —— 已经在跑就不重复拉。
    """
    import subprocess
    import sys

    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        target = [str(exe), "-m", "mythclass"]
    else:
        # 打包后的客户端：直接跑同目录下的 exe
        cand = exe.parent / "MythclassClient.exe"
        target = [str(cand)] if cand.exists() else [str(exe), "-m", "mythclass"]

    script = (
        "import subprocess, sys, time\n"
        f"time.sleep({delay!r})\n"
        "import ctypes\n"
        "u = ctypes.windll.user32\n"
        "h = u.FindWindowW(None, 'Mythclass')\n"
        "if h:\n"
        "    sys.exit(0)\n"
        f"subprocess.Popen({target!r}, close_fds=True)\n"
    )
    try:
        subprocess.Popen([str(exe), "-c", script], close_fds=True)
    except Exception:
        pass


def restart_soon(delay: float = 1.0) -> None:
    """给安装包让路：稍后退出自己"""
    import threading
    import time

    def bye():
        time.sleep(delay)
        os._exit(0)

    threading.Thread(target=bye, daemon=True).start()
