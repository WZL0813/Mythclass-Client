"""自启、进程保护、限制任务管理器

这块是「学生不好关掉」的那部分。默认全开，设置里能关。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from . import config

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
POLICY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
APP_NAME = "Mythclass"
TASK_NAME = "MythclassClient"
GUARD_TASK = "MythclassGuard"

CREATE_NO_WINDOW = 0x08000000


def _pythonw() -> str:
    """优先用 pythonw，省得黑框一闪"""
    exe = sys.executable or "python.exe"
    candidate = exe.replace("python.exe", "pythonw.exe")
    return candidate if os.path.exists(candidate) else exe


def _launch_command() -> str:
    """发布版是 exe，开发态是 python -m mythclass"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{_pythonw()}" -m mythclass'


# ============================== 开机自启 ==============================


TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Mythclass 客户端（最高权限，便于管防火墙/关机）</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
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
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{exe}</Command></Exec></Actions>
</Task>
"""


def ensure_elevated_autostart() -> tuple[bool, str]:
    """建一个「登录时以最高权限启动」的计划任务。

    只在客户端自己是管理员时能建（安装程序启动的第一次就是）。
    有了它，以后开机就是提权身份 —— 改防火墙、关机这些才做得动，
    而且不像 UAC 那样要点确认。

    用 XML 建任务的关键：LogonType=InteractiveToken + RunLevel=HighestAvailable，
    这样不需要存用户密码。
    """
    import os
    import subprocess
    import tempfile

    if sys.platform != "win32":
        return False, "只有 Windows 上需要"
    if not _is_admin():
        return False, "现在不是管理员，建不了（用安装包装一次就会建）"

    user = os.environ.get("USERNAME") or ""
    domain = os.environ.get("USERDOMAIN") or ""
    who = f"{domain}\\{user}" if domain else user
    exe = _launch_command()

    xml = TASK_XML.format(user=who, exe=exe)
    tmp = Path(tempfile.gettempdir()) / "mythclass-task.xml"
    try:
        tmp.write_text(xml, encoding="utf-16")
    except Exception as err:
        return False, f"写任务定义失败：{err}"

    try:
        done = subprocess.run(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(tmp), "/F"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if done.returncode == 0:
            return True, "已经建好「最高权限登录自启」"
        return False, f"建任务失败：{(done.stderr or done.stdout or '').strip()[:160]}"
    except Exception as err:
        return False, f"建任务失败：{type(err).__name__}: {err}"
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


def _is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def enable_autostart() -> tuple[bool, str]:
    """两个都试：当前用户 Run 键（稳），以及计划任务 SYSTEM 权限（狠）"""
    messages = []
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        messages.append("已加入当前用户自启")
    except Exception as err:
        messages.append(f"Run 键失败：{err}")

    try:
        subprocess.run(
            [
                "schtasks", "/Create", "/TN", TASK_NAME, "/TR", _launch_command(),
                "/SC", "ONLOGON", "/RL", "HIGHEST", "/F",
            ],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        messages.append("已建登录计划任务（最高权限）")
    except Exception as err:
        messages.append(f"计划任务失败：{err}")

    return True, "；".join(messages)


def disable_autostart() -> tuple[bool, str]:
    messages = []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
        messages.append("已移除 Run 键")
    except FileNotFoundError:
        pass
    except Exception as err:
        messages.append(f"Run 键：{err}")

    for task in (TASK_NAME, GUARD_TASK):
        try:
            subprocess.run(
                ["schtasks", "/Delete", "/TN", task, "/F"],
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
        except Exception:
            pass
    messages.append("已清理计划任务")
    return True, "；".join(messages)


def autostart_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except Exception:
        return False


# ============================== 任务管理器 ==============================


def set_task_manager_disabled(disabled: bool) -> tuple[bool, str]:
    """写注册表策略。对当前用户生效，改完要注销或重启资源管理器才彻底。"""
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, POLICY_KEY) as key:
            winreg.SetValueEx(key, "DisableTaskMgr", 0, winreg.REG_DWORD, 1 if disabled else 0)
        return True, "任务管理器已禁用" if disabled else "任务管理器放开了"
    except Exception as err:
        return False, str(err)


def task_manager_disabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, POLICY_KEY, 0, winreg.KEY_READ) as key:
            return bool(winreg.QueryValueEx(key, "DisableTaskMgr")[0])
    except Exception:
        return False


# ============================== 守护进程 ==============================
#
# 判断「客户端到底在不在跑」不靠 PID，靠它自己那个单实例互斥体。
# 原因：PID 会复用；计划任务里传进来的 0 更是永远判成「已死」，
# 于是每 10 秒空拉一次，攒一堆立刻退出的僵尸进程。
#
# 跟班自己也有一个互斥体：谁先拿到谁负责盯，后来的直接退场。
# 否则每被杀一次就多攒一个跟班，它们互相打架。

CLIENT_MUTEX = "Global\\MythclassClientMutex"
GUARD_MUTEX = "Global\\MythclassGuardianMutex"
STOP_FLAG_NAME = "guardian-stop.flag"

ERROR_ALREADY_EXISTS = 183


def _stop_flag() -> Path:
    return config.APP_DIR / STOP_FLAG_NAME


def request_stop() -> None:
    """跟班别盯了。客户端正经退出（老师输密码点的）时调用"""
    try:
        config.ensure_dirs()
        _stop_flag().write_text("stopped\n", encoding="utf-8")
    except OSError:
        pass


def clear_stop() -> None:
    """客户端正常启动时清掉标记，保护重新生效"""
    try:
        _stop_flag().unlink()
    except OSError:
        pass


def stop_requested() -> bool:
    return _stop_flag().exists()


def _mutex_in_use(name: str) -> bool:
    """问一句：这个互斥体现在有人拿着吗。

    查完必须 CloseHandle —— 否则这一次创建就把自己算成「已存在」，
    下次再问永远是真。
    """
    if sys.platform != "win32":
        return False

    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, name)
    already = kernel32.GetLastError() == ERROR_ALREADY_EXISTS
    if handle:
        kernel32.CloseHandle(handle)
    return bool(already)


def client_running() -> bool:
    """客户端活着吗（看那个单实例互斥体在不在）"""
    return _mutex_in_use(CLIENT_MUTEX)


def guardian_running() -> bool:
    """跟班活着吗（自检用）"""
    return _mutex_in_use(GUARD_MUTEX)


def _launch_client() -> bool:
    """把客户端拉起来。

    工作目录必须给对：源码模式下 `-m mythclass` 要靠它找到包，
    计划任务拉起的跟班工作目录可能在 System32。
    """
    repo_root = Path(__file__).resolve().parent.parent
    try:
        if getattr(sys, "frozen", False):
            subprocess.Popen(
                [sys.executable],
                cwd=str(Path(sys.executable).parent),
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            subprocess.Popen(
                [_pythonw(), "-m", "mythclass"],
                cwd=str(repo_root),
                creationflags=CREATE_NO_WINDOW,
            )
        return True
    except Exception:
        return False


def start_guardian() -> bool:
    """拉一个跟班。已经有一个在盯就不用再拉"""
    if _mutex_in_use(GUARD_MUTEX):
        return True

    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--guard"]
    else:
        cmd = [_pythonw(), "-m", "mythclass", "--guard"]

    try:
        subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW, close_fds=True)
        return True
    except Exception:
        return False


def run_guardian(parent_pid: int = 0) -> None:
    """跟班主体：客户端不在就把它拉回来。

    parent_pid 只为兼容老参数（计划任务里写着 `--guard 0`），不再用它判死活。
    """
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, GUARD_MUTEX)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        # 已经有人在盯了，这个跟班退场
        if handle:
            kernel32.CloseHandle(handle)
        return

    while True:
        try:
            if stop_requested():
                # 老师正经退出的，别去烦他
                return
            if not client_running():
                _launch_client()
                time.sleep(8)      # 给新实例一点启动时间，别连着拉
        except Exception:
            pass
        time.sleep(4)
