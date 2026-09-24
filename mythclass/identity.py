"""机器身份：算一个稳定的 client_uid

取 MAC + 主机名 + 机器 GUID 揉一起，取哈希前 12 位。
稳定、可读、不暴露真实信息。也可以在配置里手动指定。
"""

from __future__ import annotations

import hashlib
import platform
import uuid

PREFIX = "MYTH"


def machine_fingerprint() -> str:
    parts = [
        str(uuid.getnode()),          # MAC
        platform.node(),              # 主机名
        platform.machine(),
    ]
    try:
        # Windows 上的机器 GUID，重装系统前都不变
        import winreg  # type: ignore

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            parts.append(str(winreg.QueryValueEx(key, "MachineGuid")[0]))
    except Exception:
        pass

    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()
    return f"{PREFIX}-{digest[:4]}-{digest[4:8]}-{digest[8:12]}"


def resolve_uid(cfg: dict) -> str:
    uid = (cfg.get("clientUid") or "").strip().upper()
    return uid or machine_fingerprint()


def local_ips() -> list[str]:
    """本机的内网 IPv4 列表（给服务端判断「是不是同一个局域网」用）

    主地址用 UDP connect 取：不会真发包，但能拿到出口那张网卡的地址。
    另外把主机名解析出来的地址也算上，多网卡的机器能多报几个。
    """
    found: list[str] = []

    try:
        import socket

        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("223.5.5.5", 53))  # 只为让系统选出出口网卡
            found.append(probe.getsockname()[0])
        finally:
            probe.close()
    except Exception:
        pass

    try:
        import socket

        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except Exception:
        pass

    # 只留内网段，去重保序
    keep: list[str] = []
    for ip in found:
        if not ip or ip.startswith("127.") or ip.startswith("169.254."):
            continue
        if ip.startswith(("10.", "192.168.")) or ip.startswith("172."):
            if ip not in keep:
                keep.append(ip)
    return keep


def os_description() -> str:
    return f"{platform.system()} {platform.release()} ({platform.version()})"
