"""消息铃声：默认铃声、本机文件、老师上传的都走这里

播放用 winsound（wav 原生支持，异步不挡窗口）。
mp3 之类 winsound 不认 —— 退回系统提示音，别让它把通知卡住。
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from . import config

# 主人指定的默认铃声
DEFAULT_PATH = r"W:\Files\Download\NeatDownloadManager\BetterWX-main\BetterWX-main\Sound_1_0001678C.wav"

DATA_URL_RE = re.compile(r"^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.+)$", re.S)

# 认得的扩展名（winsound 只吃 wav；别的存下来也能"选"，但播不出来就退系统音）
KNOWN_EXT = (".wav", ".mp3", ".m4a", ".ogg", ".wma")


def sounds_dir() -> Path:
    """老师上传的铃声放这儿"""
    folder = config.APP_DIR / "sounds"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def default_path() -> Path | None:
    """默认铃声（主人指定的那个，不在就算了）"""
    p = Path(DEFAULT_PATH)
    return p if p.is_file() else None


def save_data_url(spec: str) -> Path | None:
    """把 data:audio/...;base64,... 存成本地文件（同名就不重复存）"""
    m = DATA_URL_RE.match((spec or "").strip())
    if not m:
        return None
    try:
        raw = base64.b64decode(m.group("data"), validate=False)
    except Exception:
        return None
    if not raw or len(raw) > 8 * 1024 * 1024:
        return None

    mime = m.group("mime").lower()
    ext = ".wav"
    for cand in KNOWN_EXT:
        if cand.strip(".") in mime:
            ext = cand
            break

    digest = hashlib.sha1(raw).hexdigest()[:16]
    dest = sounds_dir() / f"notice-{digest}{ext}"
    try:
        if not dest.exists():
            dest.write_bytes(raw)
        return dest
    except Exception:
        return None


def resolve(spec: str) -> Path | None:
    """把「铃声」这一项解析成能播的文件。

    spec 可以是：
      ""            用默认铃声
      本机路径       直接用
      data:...      老师上传的，存下来再用
    """
    text = (spec or "").strip()
    if not text:
        return default_path()
    if text.startswith("data:"):
        return save_data_url(text)
    try:
        p = Path(text).expanduser()
        if p.is_file():
            return p
    except Exception:
        pass
    # 也可能只是存过的名字
    try:
        cand = sounds_dir() / Path(text).name
        if cand.is_file():
            return cand
    except Exception:
        pass
    return default_path()


def play(spec: str = "") -> tuple[bool, str]:
    """播一下。返回 (成没成, 说明)"""
    path = resolve(spec)
    if path is None:
        return False, "没有可用的铃声文件"
    try:
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        return True, f"正在放：{path.name}"
    except Exception as err:
        # mp3 之类 winsound 不认 —— 退系统提示音，别让通知卡住
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
            return True, f"放不了 {path.name}（{type(err).__name__}），改成系统提示音"
        except Exception:
            return False, f"播放失败：{type(err).__name__}: {err}"


def list_saved() -> list[dict]:
    """已经存下来的铃声（页面上的下拉用）"""
    out = []
    d = default_path()
    if d is not None:
        out.append({"name": "默认铃声（" + d.name + "）", "path": str(d), "builtin": True})
    try:
        for f in sorted(sounds_dir().glob("*")):
            if f.is_file() and f.suffix.lower() in KNOWN_EXT:
                out.append({"name": f.name, "path": str(f), "builtin": False, "size": f.stat().st_size})
    except Exception:
        pass
    return out
