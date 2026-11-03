"""给几条线路测延迟。

客户端启动时会先 ping 一遍官方那几条（主站 / 英国 / 意大利 / Cloudflare），
挑延迟最短的先连；设置窗口里也有个「测延迟」按钮，看得见每条多少毫秒。

测法：拿 HTTP 版的 /api/meta 计一次往返（ws:// 换成 http://）。
Socket.IO 的握手也能测，但那要建连接、还要带 token —— 只想知道快慢，这个够用。
"""

from __future__ import annotations

import concurrent.futures
import time

TIMEOUT = 2.5
TRIES = 3          # 一条线路最多试几次（抖动/丢包时别一口咬定"不通"）
RETRY_GAP = 0.25   # 两次之间歇一下


def _http_base(url: str) -> str:
    text = (url or "").strip().rstrip("/")
    if text.startswith("wss://"):
        return "https://" + text[6:]
    if text.startswith("ws://"):
        return "http://" + text[5:]
    return text


def _probe(url: str, timeout: float) -> int | None:
    """问一句 /api/meta，返回往返毫秒；不通返回 None"""
    base = _http_base(url)
    if not base:
        return None
    try:
        import requests

        from .api import ca_bundle

        started = time.perf_counter()
        res = requests.get(
            f"{base}/api/meta",
            timeout=timeout,
            verify=ca_bundle() or True,
            headers={"User-Agent": "MythclassClient/ping"},
        )
        if res.status_code >= 400:
            return None
        elapsed = (time.perf_counter() - started) * 1000
        return int(round(elapsed))
    except Exception:
        return None


def _probe_with_retry(url: str, timeout: float, tries: int) -> int | None:
    """多试几次，取最快的那次；真就连不上才返回 None

    一次就判"不通"太武断了 —— 断流、丢包、DNS 慢半拍都会让第一下超时，
    过一会儿再问就通了。所以默认试 3 次。
    """
    best: int | None = None
    for i in range(max(1, tries)):
        ms = _probe(url, timeout)
        if ms is not None and (best is None or ms < best):
            best = ms
        if best is not None and i >= 1:
            break  # 已经有结果了，别磨蹭
        if i < tries - 1:
            time.sleep(RETRY_GAP)
    return best


def measure(urls: list[str], timeout: float = TIMEOUT, tries: int = TRIES) -> dict[str, int | None]:
    """同时测所有线路，返回 {地址: 毫秒 或 None}（每条最多试 tries 次，取最快）"""
    clean = []
    for url in urls or []:
        text = (url or "").strip()
        if text and text not in clean:
            clean.append(text)
    if not clean:
        return {}

    result: dict[str, int | None] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(clean))) as pool:
        futures = {pool.submit(_probe_with_retry, url, timeout, tries): url for url in clean}
        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            try:
                result[url] = future.result()
            except Exception:
                result[url] = None
    return {url: result.get(url) for url in clean}


def fastest(urls: list[str], timeout: float = TIMEOUT, tries: int = TRIES) -> tuple[list[str], dict[str, int | None]]:
    """按延迟排好序（不通的排最后还是按原顺序），返回 (顺序, 每条延迟)"""
    lat = measure(urls, timeout, tries)
    ok = [u for u in urls if lat.get(u) is not None]
    bad = [u for u in urls if lat.get(u) is None]
    ok.sort(key=lambda u: lat[u])
    return ok + bad, lat


def describe(lat: dict[str, int | None]) -> str:
    """一行字：主站 120ms / 英国 240ms / 意大利 不通"""
    parts = []
    for url, ms in lat.items():
        host = _http_base(url).split("//")[-1]
        parts.append(f"{host} {'不通' if ms is None else str(ms) + 'ms'}")
    return " / ".join(parts)
