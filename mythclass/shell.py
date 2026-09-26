"""在客户端上跑一条 cmd 命令，把输出拿回来

给局域网「命令」页的终端用。
- 走 cmd.exe /c，不弹黑框（CREATE_NO_WINDOW）
- 有超时，有输出上限，免得一条命令把页面拖死
- 中文输出按系统 OEM 编码解（中文 Windows 是 GBK），解不开就用替换符，
  不能因为编码把整个回显搞成乱码或者异常
"""

from __future__ import annotations

import os
import subprocess
import time

DEFAULT_TIMEOUT = 20.0
MAX_OUTPUT = 64 * 1024  # 64KB，够看 dir / tasklist 了


def _decode(raw: bytes) -> str:
    for enc in ("oem", "mbcs", "utf-8"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def run(command: str, timeout: float = DEFAULT_TIMEOUT) -> tuple[bool, str]:
    """跑一条命令。返回 (成没成, 文本)"""
    line = (command or "").strip()
    if not line:
        return False, "要跑什么命令？"

    if os.name != "nt":
        return False, "这台机器不是 Windows"

    started = time.time()
    try:
        done = subprocess.run(
            ["cmd.exe", "/c", line],
            capture_output=True,
            timeout=max(1.0, float(timeout)),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, f"命令超过 {int(timeout)} 秒还没结束，已经中断了。"
    except Exception as err:
        return False, f"跑不起来：{type(err).__name__}: {err}"

    out = _decode(done.stdout or b"")
    err = _decode(done.stderr or b"")
    text = out
    if err.strip():
        text += ("\n" if text and not text.endswith("\n") else "") + err

    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT] + f"\n…（输出太长，截到 {MAX_OUTPUT // 1024}KB）"
    if not text.strip():
        text = "（这条命令没有任何输出）"

    cost = time.time() - started
    head = f"[退出码 {done.returncode}，用时 {cost:.1f}s]\n"
    return done.returncode == 0, head + text
