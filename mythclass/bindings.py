"""这台机器绑定了哪些老师

从服务端拉一次存下来。本地网页要显示「本机归属」，
断网时也得能显示上次拉到的，所以落盘。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import config

FILE_NAME = "bindings.json"
STALE_SECONDS = 300  # 超过这么久就重新拉一次


def _path() -> Path:
    return config.APP_DIR / FILE_NAME


def load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save(payload: dict) -> None:
    try:
        config.ensure_dirs()
        data = dict(payload)
        data["fetchedAt"] = time.time()
        _path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def names() -> list[str]:
    """绑定老师的名字列表"""
    return [str(t.get("username") or "") for t in (load().get("teachers") or []) if t.get("username")]


def is_stale() -> bool:
    fetched = float(load().get("fetchedAt") or 0)
    return (time.time() - fetched) > STALE_SECONDS


def describe() -> str:
    got = names()
    if not got:
        return "没拉过（或者还没绑定老师）"
    when = time.strftime("%m-%d %H:%M", time.localtime(float(load().get("fetchedAt") or 0)))
    return f"{'、'.join(got)}（{when} 拉的）"
