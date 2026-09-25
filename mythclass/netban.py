"""禁止上网：白名单模式

需求：把教室机器的外网掐掉，但**必须留着客户端到服务端那条线**。
不然老师再也发不出「放开上网」，那台机器就锁死了，只能人到跟前用管理员解锁。

做法（Windows 防火墙的「放行模型」）：

  1. 把三个配置文件的「默认出站动作」改成 Block
  2. 再放行这几样：
       服务端 IP 的 443    ← 控制通道，命根子
       DNS 53              ← 得能重新解析服务端域名（Cloudflare 的 IP 会变）
       DHCP 67/68          ← 别把 IP 弄丢
     回环不用管，Windows 防火墙本来就不拦
  3. 改之前的默认策略记下来，放开时原样还原
  4. 保险：到点自动放开（默认 60 分钟），防止把机器永久锁死

为什么**不放行**「本地网段」：默认网关通常就是 192.168.x.1，
放行本地网段等于把路由器也放行了，外网照样通，禁网就成了摆设。
代价是局域网里其他教学工具（走非 443 端口的）也会被拦，这是预期内的。

为什么用 PowerShell 的 *-NetFirewallProfile / *-NetFirewallRule 而不是 netsh：
netsh 的输出是本地化文本（中文系统上写「出站」），解析容易出错；
PowerShell 给的是结构化对象，Allow / Block 不随系统语言变。
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from . import config

RULE_GROUP = "Mythclass-NetBan"
STATE_FILE_NAME = "netban.json"
CREATE_NO_WINDOW = 0x08000000

DEFAULT_AUTO_LIFT_MINUTES = 60


# ------------------------------ 状态文件 ------------------------------


def state_path() -> Path:
    return config.APP_DIR / STATE_FILE_NAME


def load_state() -> dict:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    config.ensure_dirs()
    state_path().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_state() -> None:
    try:
        state_path().unlink()
    except OSError:
        pass


def is_active() -> bool:
    return bool(load_state().get("active"))


def due_for_auto_lift(now: datetime | None = None) -> bool:
    """到点了没？没设自动解除就永远返回 False"""
    state = load_state()
    if not state.get("active"):
        return False
    deadline = state.get("autoLiftAt")
    if not deadline:
        return False
    try:
        when = datetime.strptime(deadline, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return (now or datetime.now()) >= when


# ------------------------------ PowerShell ------------------------------


def _run_powershell(script: str, timeout: int = 60) -> tuple[bool, str]:
    """跑一段 PowerShell。返回值 (是否成功, 输出)"""
    if sys.platform != "win32":
        return False, "禁止上网只在 Windows 上有效"
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        return False, "找不到 powershell.exe"
    except subprocess.TimeoutExpired:
        return False, "PowerShell 执行超时"

    out = (result.stdout or b"").decode("utf-8", "ignore").strip()
    err = (result.stderr or b"").decode("utf-8", "ignore").strip()
    if result.returncode != 0:
        return False, err or out or f"退出码 {result.returncode}"
    return True, out


# ------------------------------ 地址解析 ------------------------------


def resolve_server_ips(servers: list[str]) -> list[str]:
    """把服务端域名解析成 IPv4 列表（去重，保序）。解析失败会重试一次"""
    ips: list[str] = []
    for url in servers:
        host = urlparse(url).hostname or ""
        if not host:
            continue

        infos = None
        for _ in range(2):  # DNS 偶尔抽风，给一次机会
            try:
                infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
                break
            except socket.gaierror:
                time.sleep(0.5)
        if not infos:
            continue

        for info in infos:
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    return ips


# ------------------------------ 防火墙命令 ------------------------------


def _default_outbound_actions() -> dict:
    """读当前三个配置文件的默认出站动作。返回 {'Domain': 'Allow', ...}

    显式 ToString()：直接 ConvertTo-Json 会把枚举序列化成数字（0/1），
    拿数字去还原策略容易出错，字符串稳。
    """
    ok, out = _run_powershell(
        "Get-NetFirewallProfile | Select-Object Name,"
        "@{n='Action';e={$_.DefaultOutboundAction.ToString()}} | ConvertTo-Json -Compress"
    )
    if not ok or not out:
        return {}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict):
        data = [data]  # 只有一个 profile 时 PowerShell 给对象而不是数组
    return {item.get("Name"): item.get("Action") for item in data if item.get("Name")}


def _remove_rules_script(whatif: bool = False) -> str:
    """删掉我们那一组规则。

    先查有没有再删：规则不存在时 Remove-NetFirewallRule 会报错退出（退出码 1），
    而 ErrorAction SilentlyContinue 挡不住它，于是「放开上网」会被误判成失败。

    whatif 必须拼在 cmdlet 后面、不能拼在整个脚本后面——
    那样 PowerShell 会把 -WhatIf 当成一条独立命令。
    """
    tail = " -WhatIf" if whatif else ""
    return (
        "$ErrorActionPreference = 'SilentlyContinue'; "
        f"$m = Get-NetFirewallRule -Group '{RULE_GROUP}'; "
        "if (-not $m) { exit 0 }; "
        f"$m | Remove-NetFirewallRule{tail}; "
        "if ($?) { exit 0 } else { exit 1 }"
    )


def _allow_rules_script(ips: list[str]) -> list[str]:
    """生成放行规则。每条都是独立的 New-NetFirewallRule"""
    rules = [
        # 控制通道：命根子，只放服务端那几个 IP 的 443
        "New-NetFirewallRule -DisplayName 'Mythclass NetBan - 控制通道' -Group '{g}' "
        "-Direction Outbound -Action Allow -Protocol TCP -RemotePort 443 -RemoteAddress {ips} -Profile Any",
        # 局域网必须放行 —— 不然教师端直连、局域网控制台、屏幕流
        # 会被自己的防火墙一起掐掉（表现是页面卡死，只有放开上网才恢复）
        "New-NetFirewallRule -DisplayName 'Mythclass NetBan - 局域网' -Group '{g}' "
        "-Direction Outbound -Action Allow -RemoteAddress LocalSubnet -Profile Any",
        # 局域网那几个端口再点名放行一次（本机回自己也算）
        "New-NetFirewallRule -DisplayName 'Mythclass NetBan - 局域网端口' -Group '{g}' "
        "-Direction Outbound -Action Allow -Protocol TCP -LocalPort 26924,26925,26926 -Profile Any",
        # DNS：要能重新解析服务端域名
        "New-NetFirewallRule -DisplayName 'Mythclass NetBan - DNS' -Group '{g}' "
        "-Direction Outbound -Action Allow -Protocol UDP -RemotePort 53 -Profile Any",
        "New-NetFirewallRule -DisplayName 'Mythclass NetBan - DNS(TCP)' -Group '{g}' "
        "-Direction Outbound -Action Allow -Protocol TCP -RemotePort 53 -Profile Any",
        # DHCP：别把 IP 弄丢
        "New-NetFirewallRule -DisplayName 'Mythclass NetBan - DHCP' -Group '{g}' "
        "-Direction Outbound -Action Allow -Protocol UDP -RemotePort 67,68 -Profile Any",
    ]
    ips_text = ",".join(ips) if ips else "0.0.0.0/32"  # 一个都没有时不给任何地址，等于没放行
    return [r.format(g=RULE_GROUP, ips=ips_text) for r in rules]


def apply(servers: list[str], minutes: int | None = None, dry_run: bool = False) -> tuple[bool, str]:
    """开禁网。servers 是要放行的服务端地址列表（wss:// 或 https:// 都行）"""
    if sys.platform != "win32":
        return False, "禁止上网只在 Windows 上有效"

    ips = resolve_server_ips(servers)
    if not ips:
        return False, "解析不出服务端 IP，先别禁——否则会把自己也掐掉"

    if is_active():
        # 已经禁着，那就只刷新一下 IP，别重复改策略
        ok, msg = refresh_control_ips(servers, dry_run=dry_run)
        return ok, f"本来就已经禁着了，只刷新了放行地址。{msg}"

    previous = _default_outbound_actions()
    if not previous and not dry_run:
        return False, "读不到当前防火墙策略，为安全起见没有动手"

    auto_minutes = int(minutes or config.load().get("netBanAutoLiftMinutes") or DEFAULT_AUTO_LIFT_MINUTES)
    auto_minutes = max(0, min(auto_minutes, 24 * 60))
    deadline = datetime.now() + timedelta(minutes=auto_minutes) if auto_minutes else None

    scripts = [
        "Set-NetFirewallProfile -Profile Domain,Private,Public -DefaultOutboundAction Block -Confirm:$false"
    ] + _allow_rules_script(ips)

    tail = " -WhatIf" if dry_run else ""
    for script in scripts:
        ok, out = _run_powershell(script + tail)
        if not ok:
            # 半路失败就赶紧回滚，别留个半禁状态
            if not dry_run:
                lift(dry_run=False)
            return False, f"设置防火墙失败：{out}"

    if not dry_run:
        save_state(
            {
                "active": True,
                "since": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "autoLiftAt": deadline.strftime("%Y-%m-%d %H:%M:%S") if deadline else None,
                "previousPolicy": previous,
                "controlIps": ips,
                "ruleGroup": RULE_GROUP,
            }
        )

    tail_msg = f"{auto_minutes} 分钟后自动放开" if auto_minutes else "不会自动放开（要记得手动解）"
    return True, f"已禁止上网（放行 {len(ips)} 个服务端地址）{tail_msg}"


def refresh_control_ips(servers: list[str], dry_run: bool = False) -> tuple[bool, str]:
    """服务端 IP 变了就把控制通道那条规则更新掉"""
    state = load_state()
    if not state.get("active"):
        return True, "当前没在禁网"

    ips = resolve_server_ips(servers)
    if not ips or ips == state.get("controlIps"):
        return True, "放行地址没变"

    tail = " -WhatIf" if dry_run else ""
    ok, out = _run_powershell(_remove_rules_script(dry_run))
    if not ok:
        return False, f"更新放行地址失败：{out}"

    for script in _allow_rules_script(ips):
        ok, out = _run_powershell(script + tail)
        if not ok:
            return False, f"更新放行地址失败：{out}"

    if not dry_run:
        state["controlIps"] = ips
        state["refreshedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)

    return True, f"放行地址已刷新为 {', '.join(ips)}"


def lift(dry_run: bool = False) -> tuple[bool, str]:
    """放开上网：删掉我们的规则，把默认出站策略还原成原来的样子"""
    if sys.platform != "win32":
        return False, "禁止上网只在 Windows 上有效"

    state = load_state()
    previous = state.get("previousPolicy") or {}
    tail = " -WhatIf" if dry_run else ""

    problems = []

    ok, out = _run_powershell(_remove_rules_script(dry_run))
    if not ok:
        problems.append(f"删规则失败：{out}")

    # 还原默认策略。读不到原值就退回 Allow（Windows 的默认值）
    for name in ("Domain", "Private", "Public"):
        action = previous.get(name) or "Allow"
        ok, out = _run_powershell(
            f"Set-NetFirewallProfile -Profile {name} -DefaultOutboundAction {action} -Confirm:$false{tail}"
        )
        if not ok:
            problems.append(f"还原 {name} 失败：{out}")

    if not dry_run:
        clear_state()

    if problems:
        return False, "；".join(problems)
    return True, "已放开上网"


def describe() -> str:
    """给 --status 用的一行说明"""
    state = load_state()
    if not state.get("active"):
        return "没禁"
    deadline = state.get("autoLiftAt") or "不会自动解除"
    return f"禁着（{state.get('since', '?')} 起，{deadline} 放开，放行 {len(state.get('controlIps') or [])} 个地址）"
