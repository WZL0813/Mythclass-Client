"""把服务器中转过来的请求，转给本机那个局域网网页服务

为什么绕一圈回本机：这样"和局域网页面一模一样"是**天然**的 ——
同一套路由、同一套鉴权、同一套数据，不用再写第二遍。
多一跳本机回环，代价很小。
"""

from __future__ import annotations

import base64
import http.client
import json

DEFAULT_TIMEOUT = 25.0
MAX_BODY = 8 * 1024 * 1024  # 8MB，够传铃声和截图了


def call(
    port: int,
    key: str,
    method: str = "GET",
    path: str = "/",
    query: str = "",
    body: bytes = b"",
    content_type: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """请求本机局域网服务。返回 {status, body(字节), contentType}"""
    method = (method or "GET").upper()
    path = path or "/"
    if not path.startswith("/"):
        path = "/" + path

    # 带上密钥：有些路径（比如首页）只认 ?key= 那条快捷通道
    parts = [q for q in (query or "").split("&") if q]
    if key:
        parts = [q for q in parts if not q.lower().startswith("key=")]
        parts.append("key=" + str(key))
    url = path + ("?" + "&".join(parts) if parts else "")

    headers = {}
    if key:
        headers["X-Mythclass-Key"] = str(key)
    if body:
        headers["Content-Type"] = content_type or "application/octet-stream"
        headers["Content-Length"] = str(len(body))

    conn = None
    try:
        conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=max(2.0, float(timeout)))
        conn.request(method, url, body=body or None, headers=headers)
        res = conn.getresponse()
        data = res.read(MAX_BODY + 1)
        if len(data) > MAX_BODY:
            data = data[:MAX_BODY]
        return {
            "status": int(res.status),
            "body": data,
            "contentType": res.getheader("Content-Type") or "application/octet-stream",
        }
    except Exception as err:
        return {
            "status": 502,
            "body": json.dumps(
                {"ok": False, "error": f"本机局域网服务没答应：{type(err).__name__}: {err}"},
                ensure_ascii=False,
            ).encode("utf-8"),
            "contentType": "application/json; charset=utf-8",
        }
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def call_base64(**kwargs) -> dict:
    """同上，但 body 用 base64 传（跨 socket 传二进制安全）"""
    raw = kwargs.pop("body_base64", "")
    body = base64.b64decode(raw) if raw else b""
    out = call(body=body, **kwargs)
    return {
        "status": out["status"],
        "body": base64.b64encode(out["body"]).decode("ascii"),
        "contentType": out["contentType"],
    }
