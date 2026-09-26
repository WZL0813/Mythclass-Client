"""语音播报（Windows SAPI）

音色：默认挑中文的（系统里一般是「Microsoft Huihui Desktop」中文女声），
     没有中文就退回默认音色。管理页面上可以换、也能调音量。

为什么 comtypes 和 win32com 都留着：打包成 exe 之后 win32com 的
动态派发有时会用不了（gen_py 缓存），comtypes 稳一些 —— 两个都试。
"""

from __future__ import annotations

import threading

try:  # 优先 comtypes：打包后更稳
    import comtypes.client as _comtypes
except Exception:  # pragma: no cover
    _comtypes = None

try:
    import win32com.client as _win32com
except Exception:  # pragma: no cover
    _win32com = None

# 挑中文音色的关键词（按优先级）
CHINESE_HINTS = ("huihui", "chinese", "zh-cn", "zh_cn", "yaoyao", "kangkang", "xiaoxiao")

_LOCK = threading.Lock()
_ENGINE = None


def available() -> bool:
    return _comtypes is not None or _win32com is not None


def _engine():
    """拿一个 SAPI 引擎（拿过一次就留着）"""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    with _LOCK:
        if _ENGINE is not None:
            return _ENGINE
        if _comtypes is not None:
            try:
                _ENGINE = _comtypes.CreateObject("SAPI.SpVoice")
                return _ENGINE
            except Exception:
                _ENGINE = None
        if _win32com is not None:
            try:
                _ENGINE = _win32com.Dispatch("SAPI.SpVoice")
                return _ENGINE
            except Exception:
                _ENGINE = None
    return None


def list_voices() -> list[dict]:
    """系统里能用的音色"""
    engine = _engine()
    if engine is None:
        return []
    out: list[dict] = []
    try:
        voices = engine.GetVoices()
        for i in range(voices.Count):
            item = voices.Item(i)
            desc = ""
            try:
                desc = str(item.GetDescription())
            except Exception:
                desc = f"音色 {i}"
            out.append({"index": i, "name": desc, "chinese": _is_chinese(desc)})
    except Exception:
        return []
    return out


def _is_chinese(desc: str) -> bool:
    text = (desc or "").lower()
    return any(h in text for h in CHINESE_HINTS)


def default_voice() -> str:
    """默认音色名：优先中文"""
    voices = list_voices()
    for v in voices:
        if v["chinese"]:
            return v["name"]
    return voices[0]["name"] if voices else ""


def _select(engine, name: str) -> None:
    """挑音色；名字对不上就保持默认"""
    want = (name or "").strip().lower()
    if not want:
        return
    try:
        voices = engine.GetVoices()
        for i in range(voices.Count):
            item = voices.Item(i)
            try:
                desc = str(item.GetDescription())
            except Exception:
                continue
            if want in desc.lower() or desc.lower() in want:
                engine.Voice = item
                return
    except Exception:
        pass


def speak(text: str, volume: int = 100, rate: int = 0, voice: str = "") -> tuple[bool, str]:
    """念一段话。volume 0-100，rate -10~10（负数更慢）"""
    body = (text or "").strip()
    if not body:
        return False, "没内容可念"

    engine = _engine()
    if engine is None:
        return False, "这台机器上的语音播报用不了（SAPI 起不来）"

    try:
        _select(engine, voice)
        try:
            engine.Volume = max(0, min(100, int(volume)))
        except Exception:
            pass
        try:
            engine.Rate = max(-10, min(10, int(rate)))
        except Exception:
            pass
        # 异步念：别把通知窗口卡住
        try:
            engine.Speak(body, 1)  # 1 = SVSFlagsAsync
        except TypeError:
            engine.Speak(body)
        return True, f"开始播报（音量 {max(0, min(100, int(volume)))}）"
    except Exception as err:
        return False, f"播报失败：{type(err).__name__}: {err}"


def status() -> dict:
    """给页面看的状态"""
    return {
        "available": available(),
        "voices": list_voices(),
        "defaultVoice": default_voice(),
    }
