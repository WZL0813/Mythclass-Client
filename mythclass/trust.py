"""局域网信任记录

老师端每次**直连**（P2P 那条通道）成功都记一笔；同一个 IP 连满 3 次，
就把它记成「信任」，并生成一个配对密钥。以后就算连不上服务器，
拿着这个密钥也能直接控制这台机器。

为什么是「IP + 密钥」而不是光看 IP：
  教室局域网里谁都能把 IP 改成老师那台机器的地址。只认 IP 的话，
  随便一台机器就能控制一体机。密钥是第一次（那时还有服务器认证）发的，
  别人拿不到。
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

from . import config

THRESHOLD = 3  # 同一个 IP 连满这么多次就记进信任
FILE_NAME = "trusted-teachers.json"


def _path() -> Path:
    return config.APP_DIR / FILE_NAME


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    try:
        config.ensure_dirs()
        _path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def record(ip: str, teacher: str = "") -> dict:
    """记一次直连。到阈值就给这个 IP 发一张长期密钥。"""
    ip = (ip or "").strip()
    if not ip:
        return {"ok": False, "reason": "没有 IP"}

    data = _load()
    entry = data.get(ip) or {"count": 0, "key": "", "teacher": "", "first": "", "last": ""}
    entry["count"] = int(entry.get("count") or 0) + 1
    entry["last"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if not entry.get("first"):
        entry["first"] = entry["last"]
    if teacher:
        entry["teacher"] = teacher

    fresh = False
    if entry["count"] >= THRESHOLD and not entry.get("key"):
        entry["key"] = secrets.token_hex(16)
        fresh = True

    data[ip] = entry
    _save(data)
    return {
        "ok": True,
        "ip": ip,
        "count": entry["count"],
        "trusted": bool(entry.get("key")),
        "key": entry.get("key") or "",
        "just_trusted": fresh,
    }


def check(ip: str, key: str) -> bool:
    """这个 IP 拿着这张密钥，能不能免服务器直接控制？"""
    if not ip or not key:
        return False
    entry = _load().get(ip.strip()) or {}
    saved = entry.get("key") or ""
    if not saved:
        return False
    return secrets.compare_digest(saved, key.strip())


def own_key() -> str:
    """这台机器自己的一张固定密钥。

    老师那边弹「建议用它自己的网页」时，就把这张拼进链接，
    点开就自动对上暗号，不用手输、也不用等连满三次。

    存在 trust 文件里（键名 _self），这样 check_any() 也能认它。
    """
    data = _load()
    entry = data.get("_self") or {}
    key = str(entry.get("key") or "")
    if key:
        return key

    key = secrets.token_hex(16)
    data["_self"] = {
        "count": 0,
        "key": key,
        "teacher": "本机",
        "first": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save(data)
    return key


def check_any(key: str) -> bool:
    """这张密钥是不是任何一台被信任的机器发的

    老师换台电脑（IP 变了）也能用同一张密钥 —— 这是密钥存在的意义。
    光按 IP 判的话，密钥就白存了。
    """
    key = (key or "").strip()
    if not key:
        return False
    for entry in _load().values():
        saved = entry.get("key") or ""
        if saved and secrets.compare_digest(saved, key):
            return True
    return False


def trusted_count() -> int:
    return len([1 for e in _load().values() if e.get("key")])


def describe() -> str:
    data = _load()
    if not data:
        return "还没有记录"
    bits = []
    for ip, entry in sorted(data.items()):
        mark = "已信任" if entry.get("key") else f"{entry.get('count', 0)}/{THRESHOLD} 次"
        bits.append(f"{ip}（{mark}）")
    return "；".join(bits)


def key_for(ip: str) -> str:
    return (_load().get((ip or "").strip()) or {}).get("key") or ""
