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
        name = Path(url.split("?")[0]).name or f"MythclassSetup-{__version__}.exe"
    if not name.lower().endswith(".exe"):
        name = f"MythclassSetup-{__version__}.exe"
    dest = update_dir() / name

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

    # 不管有没有 sha256，都要确认下下来的**真的是个 exe**。
    # 曾经出现过下载地址返回 HTML（页面）的情况：文件被当成安装包跑，
    # Windows 弹「此应用无法在你的电脑上运行」，还不容易看出为什么。
    try:
        size = dest.stat().st_size
        head = dest.open("rb").read(2)
    except Exception as err:
        LAST_ERROR[:] = [f"下载完读不了：{type(err).__name__}: {err}"]
        return None

    if head != b"MZ":
        try:
            snippet = dest.open("rb").read(120).decode("utf-8", errors="replace").replace("\n", " ")
        except Exception:
            snippet = ""
        try:
            dest.unlink()
        except Exception:
            pass
        LAST_ERROR[:] = [
            f"下下来的不是安装包（{size} 字节，开头是 {head!r}）\n"
            f"内容开头：{snippet[:100]}\n"
            f"多半是下载地址不对或者服务端没给到这个文件。"
        ]
        return None

    if size < 1024 * 1024:
        try:
            dest.unlink()
        except Exception:
            pass
        LAST_ERROR[:] = [f"下下来的文件太小（{size} 字节），不像安装包，已删掉。"]
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


def update_dir() -> Path:
    """安装包下到这儿，别丢在 Temp（Temp 会被清，手动双击也不好找）"""
    try:
        from . import config

        folder = config.APP_DIR / "update"
    except Exception:
        import tempfile

        folder = Path(tempfile.gettempdir()) / "Mythclass"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# ---------------------------------------------------------------- 免 UAC 更新

# 安装时（提权那次）建好这个任务；以后更新让任务去跑，就不弹 UAC 了
UPDATE_TASK = "MythclassApplyUpdate"


def apply_cmd_path() -> Path:
    """更新脚本放在用户自己目录下（和下载的安装包同一个文件夹）。

    为什么不用 C: 下的 ProgramData：更新的时候客户端正是"非管理员"身份，
    那个目录普通用户写不进去，整条免 UAC 的路就死了。
    计划任务是以"同一个用户的最高权限"跑的，读用户自己的目录没问题。
    """
    return update_dir() / "apply-update.cmd"


# ShellExecuteW 的错误码，说人话（不然只丢个数字，没法跟主人解释）
SHELL_ERRORS = {
    0: "系统内存或资源不够",
    2: "找不到这个文件",
    3: "找不到这个路径",
    5: "拒绝访问（UAC 被拒绝或被策略挡住）",
    8: "内存不够",
    26: "文件共享冲突",
    27: "文件关联不完整",
    28: "DDE 超时",
    29: "DDE 失败",
    30: "DDE 忙",
    31: "没有关联的程序",
    32: "DLL 问题",
}


def is_admin() -> bool:
    """现在是不是管理员身份"""
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _task_xml(arguments: str) -> str:
    """更新执行器的任务定义：交互登录 + 最高权限（不需要存密码）"""
    who = (os.environ.get("USERDOMAIN") or "") + "\\" + (os.environ.get("USERNAME") or "")
    who = who.strip("\\")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Mythclass 更新执行器（最高权限，不弹 UAC）</Description></RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>{who}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>cmd.exe</Command><Arguments>/c "{apply_cmd_path()}"</Arguments></Exec>
  </Actions>
</Task>
"""


def ensure_apply_task() -> tuple[bool, str]:
    """建「最高权限跑更新脚本」的计划任务（要管理员，装完那次顺手建）

    这是自动更新不再弹 UAC 的关键：任务以 HighestAvailable 跑，
    而**运行一个已经存在的任务不需要管理员权限**。
    """
    if os.name != "nt":
        return False, "只有 Windows 需要"
    if not is_admin():
        return False, "现在不是管理员身份，建不了（安装程序那次会建）"

    try:
        cmd_path = apply_cmd_path()
        cmd_path.parent.mkdir(parents=True, exist_ok=True)
        if not cmd_path.exists():
            cmd_path.write_text("@echo off\r\nrem 由客户端写入要执行的更新命令\r\n", encoding="utf-8")
    except Exception as err:
        return False, f"更新脚本目录建不了：{err}"

    tmp = Path(tempfile.gettempdir()) / "mythclass-update-task.xml"
    try:
        tmp.write_text(_task_xml(""), encoding="utf-16")
    except Exception as err:
        return False, f"写任务定义失败：{err}"

    try:
        done = subprocess.run(
            ["schtasks", "/Create", "/TN", UPDATE_TASK, "/XML", str(tmp), "/F"],
            capture_output=True, text=True, timeout=30,
        )
        if done.returncode == 0:
            return True, "更新执行器建好了（以后更新不弹 UAC）"
        return False, f"建任务失败：{(done.stderr or done.stdout or '').strip()[:160]}"
    except Exception as err:
        return False, f"建任务失败：{type(err).__name__}: {err}"
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


def _run_apply_task(path: Path) -> tuple[bool, str]:
    """把要装的包写进脚本，让计划任务以最高权限去跑（不弹 UAC）"""
    if os.name != "nt":
        return False, "只有 Windows 需要"
    try:
        cmd_path = apply_cmd_path()
        cmd_path.parent.mkdir(parents=True, exist_ok=True)
        cmd_path.write_text(
            "@echo off\r\n"
            "rem Mythclass 自动更新：由客户端写入，计划任务以最高权限执行\r\n"
            f'"{path}" --silent --noelevate\r\n',
            encoding="utf-8",
        )
    except Exception as err:
        return False, f"写更新脚本失败：{type(err).__name__}"

    try:
        done = subprocess.run(
            ["schtasks", "/Run", "/TN", UPDATE_TASK],
            capture_output=True, text=True, timeout=30,
        )
        if done.returncode == 0:
            return True, "已交给最高权限的更新执行器"
        return False, f"任务没跑起来：{(done.stderr or done.stdout or '').strip()[:100]}"
    except Exception as err:
        return False, f"任务没跑起来：{type(err).__name__}: {err}"


def _open_direct(path: Path) -> tuple[bool, str]:
    """直接开（自己已经是管理员时不会弹 UAC）"""
    if os.name != "nt":
        return False, "只有 Windows 需要"
    import ctypes

    result = int(ctypes.windll.shell32.ShellExecuteW(None, "open", str(path), "--silent --noelevate", None, 1))
    if result > 32:
        return True, "已经起来了"
    return False, SHELL_ERRORS.get(result, f"错误码 {result}")


def _open_runas(path: Path) -> tuple[bool, str]:
    """提权开（会弹 UAC，需要有人在机器前点「是」）"""
    if os.name != "nt":
        return False, "只有 Windows 需要"
    import ctypes

    result = int(ctypes.windll.shell32.ShellExecuteW(None, "runas", str(path), "--silent --noelevate", None, 1))
    if result > 32:
        return True, "已经起来了（UAC 已同意）"
    return False, SHELL_ERRORS.get(result, f"错误码 {result}")


def run_installer(path: Path) -> bool:
    """跑安装包。按「最不打扰人」的顺序试三条路，并把原因记下来。

    1. 计划任务（最高权限，不弹 UAC）—— 装的时候建好，之后一直能用
    2. 自己就是管理员 → 直接开
    3. 退回 runas（弹 UAC，教室里可能没人点）
    """
    LAST_ERROR[:] = [""]
    if os.name != "nt":
        LAST_ERROR[:] = ["只有 Windows 需要"]
        return False

    tried: list[str] = []

    if is_admin():
        # 顺手把更新执行器补上，下次就不用弹 UAC 了
        made, why = ensure_apply_task()
        tried.append(f"建更新执行器：{why}" if made else f"建更新执行器没成：{why}")

    done, why = _run_apply_task(path)
    tried.append(f"计划任务：{why}")
    if done:
        return True

    if is_admin():
        done, why = _open_direct(path)
        tried.append(f"直接开（管理员）：{why}")
        if done:
            return True

    done, why = _open_runas(path)
    tried.append(f"提权开（会弹 UAC）：{why}")
    if done:
        return True

    LAST_ERROR[:] = ["\n".join(tried)]
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
