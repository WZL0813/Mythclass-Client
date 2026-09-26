"""一体机的本地网页

老师跟这台机器在同一个局域网时，直接打开 http://<这台机器的内网IP>:26924/
就能看画面、下命令，完全不用经过服务器。

为什么是 http 不是 https：
  教师端页面是 https，浏览器不许它连 ws://（混合内容，静默失败）。
  但「点链接跳到另一个页面」是允许的 —— 老师从教师端点一下打开这个页面，
  它自己就是 http 源，跟同源接口说话就没有混合内容问题，也不用证书。
  浏览器会标「不安全」：这条路上确实是明文，前提是你信这个局域网。

鉴权：必须带配对密钥（MythclassClient.exe --status 里能看到）。
密钥是同一个 IP 的教师端直连满 3 次之后才发得出来的，
局域网里随便一台机器拿不到。
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, quote, urlparse
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

DEFAULT_PORT = 26925  # 网页端口（跟 lanport 的 26924 分开）
COOKIE = "mythkey"


class LanWeb:
    def __init__(
        self,
        trust,
        on_command: Callable[[str, dict], tuple[bool, str]],
        on_info: Callable[[], dict],
        on_frame: Callable[[], bytes | None],
        log: Callable[[str], None],
        port: int = DEFAULT_PORT,
    ):
        self.trust = trust
        self.on_command = on_command
        self.on_info = on_info
        self.on_frame = on_frame
        # 抓帧失败时问它要原因（显示给用户，别只回「抓不到画面」）
        self.frame_error = None
        self.log = log
        self.port = int(port)
        self.running = False
        self.hits = 0
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------ 生命周期 ------------------------------

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "MythclassLan/1.0"

            def log_message(self, fmt, *args):  # 别往 stderr 刷
                pass

            def _ip(self) -> str:
                return self.client_address[0] if self.client_address else ""

            def _key(self) -> str:
                head = self.headers.get("X-Mythclass-Key", "")
                if head:
                    return head.strip()
                raw = self.headers.get("Cookie", "")
                for part in raw.split(";"):
                    if part.strip().startswith(COOKIE + "="):
                        return part.split("=", 1)[1].strip()
                return ""

            def _allowed(self) -> bool:
                # 两条路任一条通就行：
                #   1) 这个 IP 直连满 3 次，已经被记成信任（主人定的规则）
                #   2) 贴了一张任意被信任机器发出来的配对密钥（换台电脑也能用）
                if outer.is_trusted_ip(self._ip()):
                    return True
                return outer.trust.check_any(self._key())

            def _send(self, code: int, body: bytes, ctype: str, extra: list[tuple[str, str]] | None = None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for k, v in extra or []:
                    self.send_header(k, v)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _json(self, code: int, payload: dict, extra=None):
                self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8", extra)

            # ------------------------------ 路由 ------------------------------

            def do_GET(self):
                # 整体包一层：处理器里出异常会让浏览器看到「连接被关闭」，
                # 完全看不出是哪儿的问题
                try:
                    self._do_get()
                except Exception as err:
                    outer.log(f"本地网页出错（GET {self.path}）：{type(err).__name__}: {err}")
                    self._json(500, {"error": "INTERNAL", "message": str(err)})

            def _do_get(self):
                raw_path = self.path
                path = raw_path.split("?")[0]
                query = raw_path.split("?", 1)[1] if "?" in raw_path else ""
                outer.hits += 1

                # 链接里带密钥就直接对上暗号：老师点一下就能用，
                # 不用手输、也少一步（?key=xxxx）
                if "key=" in query:
                    
                    given = (parse_qs(query).get("key") or [""])[0].strip()
                    if outer.trust.check_any(given):
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header(
                            "Set-Cookie", f"{COOKIE}={given}; Path=/; SameSite=Lax"
                        )
                        body = PAGE.encode("utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        try:
                            self.wfile.write(body)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        outer.log(f"本地网页：{self._ip()} 用链接里的密钥进来了。")
                        return
                    outer.log(f"本地网页：{self._ip()} 链接里的密钥不对。")

                if path in ("/", "/index.html"):
                    self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                    return

                if path == "/api/screenshot":
                    """截一张全分辨率的图直接回给页面（不缩、不压，尽量清晰）"""
                    getter = getattr(outer, "screenshot", None)
                    data = getter() if callable(getter) else None
                    if not data:
                        why = ""
                        err = getattr(outer, "frame_error", None)
                        if callable(err):
                            why = err() or ""
                        self._json(503, {"error": "NO_SHOT", "message": "截不到屏" + (f"（{why}）" if why else "")})
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Disposition", 'inline; filename="mythclass-shot.png"')
                    self.end_headers()
                    self.wfile.write(data)
                    return

                if path in ("/api/file-logs", "/api/logs"):
                    getter = getattr(outer, "file_logs", None)
                    rows = getter(200) if callable(getter) else []
                    self._json(200, {"items": rows})
                    return

                if path == "/api/audio":
                    getter = getattr(outer, "audio_items", None)
                    self._json(200, {"items": getter() if callable(getter) else []})
                    return

                if path == "/api/usage":
                    getter = getattr(outer, "usage_items", None)
                    self._json(200, {"items": getter() if callable(getter) else []})
                    return

                if path == "/api/windows":
                    getter = getattr(outer, "window_items", None)
                    self._json(200, {"items": getter() if callable(getter) else []})
                    return

                if path.startswith("/api/files"):
                    query = parse_qs(urlparse(self.path).query)
                    want = (query.get("path") or [""])[0]
                    getter = getattr(outer, "dir_listing", None)
                    data = getter(want) if callable(getter) else None
                    if data is None:
                        self._json(404, {"error": "NO_DIR", "message": "看不了这个目录"})
                    else:
                        self._json(200, data)
                    return

                if path.startswith("/api/file"):
                    query = parse_qs(urlparse(self.path).query)
                    want = (query.get("path") or [""])[0]
                    getter = getattr(outer, "read_file", None)
                    blob = getter(want) if callable(getter) else None
                    if blob is None:
                        self._json(404, {"error": "NO_FILE", "message": "读不到这个文件"})
                        return
                    name, data = blob
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header(
                        "Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}"
                    )
                    self.end_headers()
                    self.wfile.write(data)
                    return

                if path == "/api/quiet":
                    getter = getattr(outer, "quiet_state", None)
                    self._json(200, getter() if callable(getter) else {})
                    return

                if path == "/api/settings":
                    """这台机器的设置（只读）"""
                    getter = getattr(outer, "settings_view", None)
                    data = getter() if callable(getter) else {}
                    self._json(200, data)
                    return

                if path == "/api/info":
                    info = outer.on_info() or {}
                    info["clientIp"] = self._ip()
                    info["trusted"] = outer.is_trusted_ip(self._ip()) or outer.trust.check_any(
                        self._key()
                    )
                    self._json(200, info)
                    return

                if path == "/frame":
                    if not self._allowed():
                        self._json(401, {"error": "KEY_REQUIRED", "message": "密钥不对"})
                        return
                    data = outer.on_frame()
                    if not data:
                        why = ""
                        getter = getattr(outer, "frame_error", None)
                        if callable(getter):
                            why = getter() or ""
                        msg = "抓不到屏幕" + (f"（{why}）" if why else "")
                        self._json(503, {"error": "NO_FRAME", "message": msg})
                        return
                    self._send(200, data, "image/jpeg")
                    return

                self._json(404, {"error": "NOT_FOUND"})

            def do_POST(self):
                try:
                    self._do_post()
                except Exception as err:
                    outer.log(f"本地网页出错（POST {self.path}）：{type(err).__name__}: {err}")
                    self._json(500, {"error": "INTERNAL", "message": str(err)})

            def _do_post(self):
                path = self.path.split("?")[0]
                outer.hits += 1
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except ValueError:
                    payload = {}

                if path == "/api/auth":
                    key = str(payload.get("key") or "").strip()
                    if outer.trust.check_any(key):
                        outer.log(f"本地网页：{self._ip()} 密钥正确，放行。")
                        self._json(200, {"ok": True, "ip": self._ip()},
                                   [("Set-Cookie", f"{COOKIE}={key}; Path=/; SameSite=Lax")])
                    else:
                        outer.log(f"本地网页：{self._ip()} 密钥不对。")
                        self._json(401, {"ok": False, "error": "KEY_REJECTED", "message": "密钥不对"})
                    return

                if path == "/api/windows/close":
                    handler = getattr(outer, "close_window", None)
                    ok2, msg = handler(int(payload.get("hwnd") or 0)) if callable(handler) else (False, "不支持")
                    self._json(200 if ok2 else 400, {"ok": ok2, "message": msg})
                    return

                if path == "/api/quiet":
                    handler = getattr(outer, "set_quiet", None)
                    data = handler(bool(payload.get("on", True)), str(payload.get("text") or "")) \
                        if callable(handler) else {"on": False}
                    self._json(200, data)
                    return

                if path == "/api/hand":
                    handler = getattr(outer, "set_hand", None)
                    data = handler(bool(payload.get("on", True))) if callable(handler) else {"hand": False}
                    self._json(200, data)
                    return

                if path == "/api/command":
                    if not self._allowed():
                        self._json(401, {"ok": False, "error": "KEY_REQUIRED", "message": "先连上"})
                        return
                    command = str(payload.get("command") or "").strip()
                    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
                    if not command:
                        self._json(400, {"ok": False, "message": "没说干什么"})
                        return
                    try:
                        ok, output = outer.on_command(command, args)
                    except Exception as err:
                        ok, output = False, f"{type(err).__name__}: {err}"
                    outer.log(f"本地网页命令 {command}：{'成功' if ok else '失败'} - {output}")
                    self._json(200, {"ok": ok, "output": output, "command": command})
                    return

                self._json(404, {"error": "NOT_FOUND"})

        try:
            httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
            httpd.daemon_threads = True
        except OSError as err:
            self.log(f"本地网页端口 {self.port} 开不起来（{err}），跳过。")
            return False

        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="mythclass-lanweb", daemon=True)
        self._thread.start()
        self.running = True
        self.log(f"本地网页已开：http://<本机内网IP>:{self.port}/")
        return True

    def stop(self) -> None:
        self.running = False
        try:
            if self._httpd:
                self._httpd.shutdown()
                self._httpd.server_close()
        except OSError:
            pass
        self._httpd = None

    def is_trusted_ip(self, ip: str) -> bool:
        """这个 IP 是否已经因为连满 3 次被记成信任（是的话免密钥放行）"""
        try:
            return bool(self.trust.key_for(ip))
        except Exception:
            return False

    def status(self) -> str:
        if not self.running:
            return "没开"
        return f"开着（{self.port}，被访问 {self.hits} 次）"


PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mythclass 局域网控制台</title>
<style>
  :root {
    --ink: #0f1411; --panel: #141b17; --line: #2a3a2e;
    --text: #e8efe6; --dim: #8fa88e; --sage: #8fa88e; --moss: #5e9a73;
    --amber: #c97b3c;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; color: var(--text);
    font: 15px/1.6 "Microsoft YaHei UI", "PingFang SC", system-ui, sans-serif;
    background:
      radial-gradient(1100px 620px at 6% -12%, rgba(94,154,115,.15), transparent 62%),
      radial-gradient(760px 420px at 108% 6%, rgba(94,154,115,.08), transparent 60%),
      var(--ink);
    background-attachment: fixed;
  }
  /* 控制台一开始藏着 —— 等星子聚成 Mythclass 之后再显出来 */
  .wrap {
    max-width: 1500px; margin: 0 auto; padding: 14px 16px 40px;
    position: relative; z-index: 1;
    opacity: 0; transform: translateY(10px);
    transition: opacity 0.7s ease, transform 0.7s ease;
  }
  .wrap.shown { opacity: 1; transform: none; }

  /* 背后的星野：和教师端同一套（星子拼出 Mythclass，另一部分慢慢漂） */
  .star-backdrop {
    position: fixed; inset: 0; z-index: 0; pointer-events: none;
    background:
      radial-gradient(120% 90% at 18% 0%, rgba(52, 74, 60, 0.5), transparent 62%),
      radial-gradient(90% 70% at 88% 12%, rgba(84, 66, 34, 0.34), transparent 60%);
  }
  .star-backdrop canvas { width: 100%; height: 100%; display: block; }

  /* 开屏：进来先盖一层，中间 logo + 名字，等一下淡出 */
  #splash {
    position: fixed; inset: 0; z-index: 200; display: grid; place-items: center;
    background: #0f1411;
    transition: opacity 0.75s ease, visibility 0.75s ease;
  }
  #splash.gone { opacity: 0; visibility: hidden; pointer-events: none; }
  .splash-in { display: grid; justify-items: center; gap: 14px; }
  .splash-in img { width: 68px; height: 68px; animation: pop 0.9s ease both; }
  .splash-in .s-name {
    font-size: 19px; letter-spacing: 1px; color: #e8efe6;
    animation: rise 0.9s 0.15s ease both;
  }
  .splash-in .s-sub { font-size: 12.5px; color: #8fa88e; animation: rise 0.9s 0.3s ease both; }
  @keyframes pop { from { opacity: 0; transform: scale(0.86); } to { opacity: 1; transform: none; } }
  @keyframes rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }

  /* 顶部：品牌 + 机器名 + 状态 */
  .brand-row { display: flex; align-items: center; gap: 12px; padding: 6px 2px 14px; }
  .mark {
    width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center;
    border: 1px solid rgba(94,154,115,.45); background: rgba(94,154,115,.14); color: #9fd0ac;
  }
  .brand-name { font-size: 17px; font-weight: 600; letter-spacing: .4px; }
  .mark img { width: 100%; height: 100%; object-fit: contain; border-radius: 9px; }
  .brand-sub { color: var(--dim); font-size: 12.5px; }
  .pills { margin-left: auto; display: flex; gap: 8px; }
  .pill {
    border: 1px solid var(--line); border-radius: 999px; padding: 4px 11px;
    color: var(--dim); font-size: 12.5px; white-space: nowrap;
  }
  .pill.on { border-color: rgba(94,154,115,.5); color: #a6d4b3; background: rgba(94,154,115,.12); }

  /* 工具条 */
  .tools {
    display: flex; flex-wrap: wrap; gap: 4px; padding: 8px 10px;
    border: 1px solid var(--line); border-radius: 12px;
    background: color-mix(in srgb, var(--panel) 88%, transparent);
  }
  .tool {
    display: inline-flex; flex-direction: column; align-items: center; gap: 3px;
    min-width: 62px; padding: 7px 8px; border-radius: 9px; cursor: pointer;
    border: 1px solid transparent; background: transparent; color: var(--dim); font: inherit; font-size: 12px;
  }
  .tool:hover:not(:disabled) { background: rgba(243,239,227,.06); color: var(--text); border-color: rgba(143,168,142,.3); }
  .tool:disabled { opacity: .35; cursor: not-allowed; }
  .tool svg { width: 17px; height: 17px; }

  /* 主体三栏 */
  .grid { display: grid; grid-template-columns: var(--rail-w, 264px) minmax(0,1fr) var(--ev-w, 274px); gap: 14px; margin-top: 14px; transition: grid-template-columns .28s ease; }
  .grid.rail-off { --rail-w: 0px; }
  .grid.events-off { --ev-w: 0px; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 14px; }
  .card h2 { margin: 0 0 10px; font-size: 13.5px; color: var(--dim); font-weight: 500; }
  .grid.rail-off .rail, .grid.events-off .events { overflow: hidden; padding: 0; border: 0; opacity: 0; pointer-events: none; }

  .kv { display: grid; grid-template-columns: 76px minmax(0,1fr); gap: 6px 8px; font-size: 13px; }
  .kv b { color: var(--dim); font-weight: 400; }
  .kv span { word-break: break-all; }
  ul { margin: 0; padding-left: 16px; color: var(--dim); font-size: 13px; }

  /* 标签栏（和教师端一套） */
  .tabs { display: flex; gap: 4px; padding: 0 4px; margin: 12px 0 10px; border-bottom: 1px solid var(--line); }
  .tab {
    display: inline-flex; align-items: center; gap: 6px; padding: 9px 14px;
    border: 0; border-bottom: 2px solid transparent; background: transparent;
    color: var(--dim); font: inherit; font-size: 13.5px; cursor: pointer;
  }
  .tab svg { width: 15px; height: 15px; }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--text); border-bottom-color: var(--moss); }
  .pane { display: none; }
  .pane.on { display: block; }
  table.logs { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  table.logs th { text-align: left; color: var(--sage); font-weight: 400; padding: 6px 8px; border-bottom: 1px solid var(--line); }
  table.logs td { padding: 7px 8px; border-bottom: 1px dashed rgba(143,168,142,.14); word-break: break-all; }
  table.logs tr:hover td { background: rgba(243,239,227,.03); }
  .cmd-row { display: flex; gap: 8px; margin-bottom: 12px; }
  .cmd-row input { flex: 1; }
  pre.out {
    margin: 0; padding: 12px; border-radius: 9px; border: 1px solid var(--line);
    background: #0b0f0c; color: #cfe0cc; font-size: 12.5px; white-space: pre-wrap;
    word-break: break-all; max-height: 46vh; overflow: auto;
  }

  .crumbs { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; margin: 2px 0 10px; font-size: 12.5px; }
  .crumb { padding: 3px 8px; border-radius: 7px; cursor: pointer; color: var(--dim); border: 1px solid transparent; }
  .crumb:hover { color: var(--text); border-color: var(--line); }
  .crumb.on { color: var(--text); background: rgba(94, 154, 115, 0.14); border-color: rgba(94, 154, 115, 0.4); }
  .crumbs .sep { color: #4d5c50; }

  /* 屏幕区 */
  .screen-head { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 10px; }
  .btn {
    display: inline-flex; align-items: center; gap: 6px; padding: 8px 13px; border-radius: 9px;
    border: 1px solid var(--line); background: #1b241e; color: var(--text); font: inherit; font-size: 13px; cursor: pointer;
  }
  .btn.primary { background: #3f6b52; border-color: #4c7d61; color: #f1f6ef; }
  .btn:disabled { opacity: .45; cursor: not-allowed; }
  .btn svg { width: 15px; height: 15px; }
  .stats { margin-left: auto; display: flex; gap: 12px; color: var(--dim); font-size: 12.5px; }
  #screen { width: 100%; border-radius: 10px; border: 1px solid var(--line); background: #0b0f0c; min-height: 300px; display: block; object-fit: contain; }
  .empty { color: var(--dim); font-size: 13px; text-align: center; padding: 26px 10px 6px; }

  /* 事件栏 */
  .ev-tabs { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
  .ev-tab { padding: 5px 11px; border-radius: 8px; border: 1px solid transparent; background: transparent; color: var(--dim); font: inherit; font-size: 13px; cursor: pointer; }
  .ev-tab.active { color: var(--text); border-color: var(--line); background: rgba(243,239,227,.05); }
  .ev-list { list-style: none; margin: 0; padding: 0; max-height: 62vh; overflow: auto; }
  .ev-list li { display: flex; gap: 8px; padding: 6px 0; border-bottom: 1px dashed rgba(143,168,142,.14); font-size: 12.5px; }
  .ev-time { color: #6f8a70; flex: 0 0 62px; }
  .ev-text { color: #c9d6c6; word-break: break-all; }
  .ev-text.bad { color: #e0a07a; }

  /* 贴屏幕边缘的伸缩竖条（和教师端同一套） */
  .edge-toggle {
    position: fixed; top: 50%; transform: translateY(-50%); z-index: 40;
    display: inline-flex; align-items: center; justify-content: center;
    width: 20px; height: 64px; padding: 0; cursor: pointer;
    border: 1px solid rgba(143,168,142,.22);
    background: color-mix(in srgb, var(--ink) 72%, transparent);
    color: var(--sage); backdrop-filter: blur(8px);
  }
  .edge-toggle:hover { color: #c9e6d2; border-color: rgba(94,154,115,.55); }
  .edge-toggle.left { left: 0; border-left: 0; border-radius: 0 10px 10px 0; }
  .edge-toggle.right { right: 0; border-right: 0; border-radius: 10px 0 0 10px; }
  .edge-toggle svg { width: 14px; height: 14px; }

  /* 发通知对话框 */
  .nt-mask {
    position: fixed; inset: 0; z-index: 120; display: grid; place-items: center;
    background: rgba(6, 10, 8, 0.62); backdrop-filter: blur(3px);
  }
  .nt-box {
    width: min(560px, 92vw); max-height: 88vh; overflow: auto;
    padding: 20px 22px 18px; border: 1px solid var(--line); border-radius: 16px;
    background: var(--panel); color: var(--text); box-shadow: 0 24px 60px rgba(0,0,0,.45);
  }
  .nt-box h3 { margin: 0 0 6px; font-size: 16px; }
  .nt-label { margin: 14px 0 6px; font-size: 12.5px; color: var(--sage); }
  .nt-input {
    width: 100%; padding: 9px 11px; border-radius: 9px; font: inherit; font-size: 13.5px;
    background: #0f1613; color: var(--text); border: 1px solid var(--line);
  }
  .nt-row { display: flex; align-items: center; gap: 8px; font-size: 13.5px; cursor: pointer; }
  .nt-opt { display: grid; grid-template-columns: 168px minmax(0,1fr); gap: 10px; align-items: center; margin-bottom: 8px; }
  .nt-slot { color: var(--sage); font-size: 12.5px; }
  .nt-col { display: grid; gap: 6px; }
  .nt-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 8px; }
  .nt-mini { display: grid; gap: 4px; font-size: 12px; color: var(--sage); }
  .nt-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
  .btn.gh { background: transparent; border: 1px solid var(--line); color: var(--sage); }

  /* 要密钥那一屏 */
  .gate { max-width: 560px; margin: 8vh auto 0; }
  input[type=password], input[type=text] {
    width: 100%; padding: 10px 12px; border-radius: 9px; color: var(--text);
    background: #0f1613; border: 1px solid var(--line); font: inherit;
  }
  .row { display: flex; gap: 9px; flex-wrap: wrap; margin-top: 12px; }
  .muted { color: var(--dim); font-size: 13px; }
  .hidden { display: none !important; }
</style>
</head>
<body>
<!-- 开屏 -->
<div id="splash">
  <div class="splash-in">
    <img id="splashLogo" src="data:image/png;base64,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" alt="Mythclass">
    <div class="s-name">Mythclass 局域网控制台</div>
    <div class="s-sub">正在连上这台机器…</div>
  </div>
</div>

<!-- 背后的星野 -->
<div class="star-backdrop" aria-hidden="true"><canvas id="stars"></canvas></div>

<div class="wrap">
  <div class="brand-row">
    <div class="mark">
      <img src="data:image/png;base64,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" alt="Mythclass" />
    </div>
    <div>
      <div class="brand-name">Mythclass 局域网控制台</div>
      <div class="brand-sub" id="sub">直连这台机器 · 不经过服务器</div>
    </div>
    <div class="pills">
      <span class="pill" id="ipPill">—</span>
      <span class="pill" id="keyPill">未连接</span>
    </div>
  </div>

  <!-- 要密钥 -->
  <section class="card gate" id="gate">
    <h2>先对一下暗号</h2>
    <p class="muted">
      配对密钥在这台机器的客户端里看：托盘右键 → 状态，或者命令行跑
      <code>MythclassClient.exe --status</code>。
      从教师端点「打开局域网控制台」进来的链接会自带密钥，不用手输。
    </p>
    <div class="row">
      <input type="password" id="key" placeholder="粘贴配对密钥" autocomplete="off">
      <button class="btn primary" id="authBtn">连上</button>
    </div>
    <div class="muted" id="authOut" style="margin-top:10px"></div>
  </section>

  <!-- 发通知对话框 -->
  <div id="ntMask" class="nt-mask hidden">
    <div class="nt-box">
      <h3>发通知</h3>
      <p class="muted" style="font-size:12.5px;margin:0 0 12px">
        窗口标题固定是「Mythclass消息通知」，下面的标题和内容由你写。
      </p>
      <label class="nt-row"><input type="checkbox" id="ntTop" checked><span>置顶显示（压在其他窗口上面）</span></label>
      <label class="nt-row"><input type="checkbox" id="ntFull"><span>全屏显示（占满整块屏幕）</span></label>

      <p class="nt-label">标题</p>
      <input class="nt-input" id="ntTitle" maxlength="40" placeholder="比如：第三节自习安排">

      <p class="nt-label">内容</p>
      <textarea class="nt-input" id="ntBody" rows="3" maxlength="300"
                placeholder="比如：请把作业交到讲台，交完再看书。"></textarea>

      <p class="nt-label">窗口大小与字号</p>
      <div class="nt-grid">
        <label class="nt-mini">宽<input class="nt-input" type="number" id="ntW" value="520" min="320" max="2400"></label>
        <label class="nt-mini">高<input class="nt-input" type="number" id="ntH" value="300" min="180" max="1600"></label>
        <label class="nt-mini">标题字号<input class="nt-input" type="number" id="ntFT" value="16" min="9" max="72"></label>
        <label class="nt-mini">内容字号<input class="nt-input" type="number" id="ntFB" value="12" min="8" max="60"></label>
        <label class="nt-mini">按钮字号<input class="nt-input" type="number" id="ntFBtn" value="10" min="8" max="40"></label>
      </div>
      <label class="nt-row" style="margin-top:8px">
        <input type="checkbox" id="ntFit"><span>自适应窗口最大（把文字按比例拉到屏幕能放的最大）</span>
      </label>

      <p class="nt-label">回复方式</p>
      <label class="nt-row">
        <input type="checkbox" id="ntAuto"><span>纯弹出，不用回复（到点自己关闭）</span>
      </label>
      <label class="nt-mini hidden" id="ntAutoRow" style="margin-top:8px;max-width:180px">
        倒计时秒数（最多 3600）
        <input class="nt-input" type="number" id="ntAutoSec" value="30" min="5" max="3600">
      </label>
      <p class="muted tiny hidden" id="ntAutoTip" style="font-size:12.5px">
        勾了它，下面三个回复选项就自动取消了。
      </p>

      <p class="nt-label">回复选项（最多三个，勾上才显示）</p>
      <div class="nt-opt">
        <label class="nt-row"><input type="checkbox" id="ntOn0" checked><span class="nt-slot">高亮按钮</span></label>
        <input class="nt-input" id="ntLabel0" maxlength="12" value="知道了" placeholder="按钮上的字">
      </div>
      <div class="nt-opt">
        <label class="nt-row"><input type="checkbox" id="ntOn1"><span class="nt-slot">普通按钮</span></label>
        <input class="nt-input" id="ntLabel1" maxlength="12" placeholder="按钮上的字，比如：等一下">
      </div>
      <div class="nt-opt">
        <label class="nt-row"><input type="checkbox" id="ntOn2"><span class="nt-slot">输入框</span></label>
        <div class="nt-col">
          <input class="nt-input" id="ntLabel2" maxlength="12" placeholder="输入框的提示文字，比如：写下你的想法">
          <input class="nt-input" id="ntSend2" maxlength="12" placeholder="发送选项：发送按钮上的字，比如：提交">
        </div>
      </div>

      <div class="nt-actions">
        <button class="btn gh" id="ntCancel">取消</button>
        <button class="btn primary" id="ntSend">发出去</button>
      </div>
    </div>
  </div>

  <div id="console" class="hidden">
    <!-- 工具条 -->
    <div class="tools" id="tools"></div>

    <div class="grid" id="grid">
      <!-- 左：机器信息 -->
      <aside class="card rail">
        <h2>这台机器</h2>
        <div class="kv" id="info"></div>
        <h2 style="margin-top:16px">归属老师</h2>
        <ul id="owners"><li>—</li></ul>
      </aside>

      <!-- 中：标签页（屏幕 / 文件记录 / 命令 / 设置） -->
      <section class="card">
        <nav class="tabs" id="tabs">
          <button class="tab active" data-pane="screen">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8"/></svg>
            屏幕
          </button>
          <button class="tab" data-pane="logs">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M4 5h11l5 5v9H4z"/><path d="M8 12h8M8 15h5"/></svg>
            文件记录
          </button>
          <button class="tab" data-pane="usage">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg>
            使用时长
          </button>
          <button class="tab" data-pane="disk">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M3 7h6l2 2h10v9H3z"/></svg>
            磁盘
          </button>
          <button class="tab" data-pane="window">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><rect x="3" y="5" width="18" height="12" rx="2"/><path d="M3 9h18M8 20h8"/></svg>
            窗口
          </button>
          <button class="tab" data-pane="audio">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M11 5L6 9H3v6h3l5 4z"/><path d="M16 9a4 4 0 010 6"/></svg>
            音频
          </button>
          <button class="tab" data-pane="cmd">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M5 7l5 5-5 5"/><path d="M13 17h6"/></svg>
            命令
          </button>
          <button class="tab" data-pane="settings">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/></svg>
            设置
          </button>
        </nav>

        <div class="pane on" id="pane-screen">
        <div class="screen-head">
          <button class="btn primary" id="watchBtn">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M8 5v14l11-7z"/></svg>
            开始看
          </button>
          <button class="btn" id="stopBtn">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="7" y="7" width="10" height="10" rx="1.5"/></svg>
            停下
          </button>
          <button class="btn" id="fullBtn">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 9V4h5"/><path d="M20 15v5h-5"/><path d="M4 4l6 6"/><path d="M20 20l-6-6"/></svg>
            全屏
          </button>
          <div class="stats">
            <span id="fps">—</span>
            <span id="size">—</span>
          </div>
        </div>
        <img id="screen" alt="这台机器的屏幕">
        <div class="empty" id="hint">点「开始看」拉画面。</div>
        </div><!-- /屏幕 -->

        <div class="pane" id="pane-logs">
          <div class="screen-head">
            <button class="btn" id="logsRefresh">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M20 12a8 8 0 11-3-6.2M20 4v5h-5"/></svg>
              刷新
            </button>
            <div class="stats"><span id="logsCount">—</span></div>
          </div>
          <table class="logs">
            <thead><tr><th style="width:150px">时间</th><th style="width:90px">动作</th><th>文件</th><th style="width:80px">大小</th></tr></thead>
            <tbody id="logsBody"><tr><td colspan="4" class="muted">点「刷新」拉一下。</td></tr></tbody>
          </table>
        </div>

        <div class="pane" id="pane-usage">
          <div class="screen-head">
            <button class="btn" id="usageRefresh">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M20 12a8 8 0 11-3-6.2M20 4v5h-5"/></svg>
              刷新
            </button>
            <div class="stats"><span id="usageNow">—</span></div>
          </div>
          <table class="logs">
            <thead><tr><th style="width:200px">程序</th><th style="width:120px">用了多久</th><th>最后一次在做什么</th></tr></thead>
            <tbody id="usageBody"><tr><td colspan="3" class="muted">点「刷新」看看。</td></tr></tbody>
          </table>
          <p class="muted tiny">每 5 秒看一眼前台是哪个程序，只记「哪个程序、用了多久」，不记内容。</p>
        </div>

        <div class="pane" id="pane-disk">
          <div class="screen-head">
            <button class="btn" id="diskHome">此电脑</button>
            <button class="btn" id="diskUp">上一级</button>
            <input class="nt-input" id="diskPath" placeholder="也可以直接写路径，比如 D:\\课件" style="flex:1">
            <button class="btn primary" id="diskGo">进去</button>
          </div>
          <div class="crumbs" id="diskCrumbs"></div>
          <table class="logs">
            <thead><tr><th>名称</th><th style="width:90px">大小</th><th style="width:140px">改过的时间</th><th style="width:90px">操作</th></tr></thead>
            <tbody id="diskBody"><tr><td colspan="4" class="muted">写个路径，点「进去」。</td></tr></tbody>
          </table>
        </div>

        <div class="pane" id="pane-window">
          <div class="screen-head">
            <button class="btn" id="winRefresh">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M20 12a8 8 0 11-3-6.2M20 4v5h-5"/></svg>
              刷新
            </button>
            <div class="stats"><span id="winCount">—</span></div>
          </div>
          <table class="logs">
            <thead><tr><th style="width:150px">程序</th><th>窗口标题</th><th style="width:90px">状态</th><th style="width:80px">操作</th></tr></thead>
            <tbody id="winBody"><tr><td colspan="4" class="muted">点「刷新」看看。</td></tr></tbody>
          </table>
        </div>

        <div class="pane" id="pane-audio">
          <div class="screen-head">
            <button class="btn" id="audioRefresh">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M20 12a8 8 0 11-3-6.2M20 4v5h-5"/></svg>
              刷新
            </button>
            <div class="stats"><span id="audioNow">—</span></div>
          </div>
          <table class="logs">
            <thead><tr><th style="width:180px">程序</th><th>在放的窗口</th><th style="width:90px">音量</th><th style="width:90px">状态</th></tr></thead>
            <tbody id="audioBody"><tr><td colspan="4" class="muted">点「刷新」看看。</td></tr></tbody>
          </table>
          <p class="muted tiny">看的是「哪些程序正在出声」的会话，和教师端那个音频页一样。</p>
        </div>

        <div class="pane" id="pane-cmd">
          <div class="cmd-row">
            <input class="nt-input" id="cmdInput" placeholder="输入命令，比如 status / screenshot，回车执行">
            <button class="btn primary" id="cmdRun">执行</button>
          </div>
          <div class="row" style="margin-bottom:12px">
            <button class="btn" data-preset="status">status</button>
            <button class="btn" data-preset="screenshot">screenshot</button>
            <button class="btn" data-preset="lock">lock</button>
          </div>
          <pre class="out" id="cmdOut">还没执行过命令。</pre>
        </div>

        <div class="pane" id="pane-settings">
          <div class="kv" id="setKv"></div>
          <p class="muted tiny" style="margin-top:14px">
            这里是这台机器的设置，看得到、改不了 —— 要改去机器上开客户端改。
          </p>
        </div>
      </section>

      <!-- 右：事件 -->
      <aside class="card events">
        <div class="ev-tabs">
          <button class="ev-tab active" id="tabEvent">事件</button>
          <button class="ev-tab" id="tabMessage">消息</button>
        </div>
        <ul class="ev-list" id="evList"></ul>
      </aside>
    </div>

    <!-- 贴屏幕边缘的伸缩条 -->
    <button class="edge-toggle left" id="edgeLeft" title="收起／展开「这台机器」">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 6l-6 6 6 6"/></svg>
    </button>
    <button class="edge-toggle right" id="edgeRight" title="收起／展开「事件 / 消息」">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10 6l6 6-6 6"/></svg>
    </button>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------------
   背后的星野：和教师端 StarBackdrop 同一套做法 ——
   把「Mythclass」画到离屏画布上，扫出墨点当目标，
   一部分星子飞过去拼成字，另一部分慢慢往下漂。
------------------------------------------------------------------ */
(function stars() {
  const canvas = document.getElementById('stars');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const COLORS = ['#F3EFE7', '#F3EFE7', '#F3EFE7', '#CBE0C4', '#A8C6A1', '#E8B778'];
  const SHAPE_COUNT = 700;
  const FREE_COUNT = 130;
  const SPEED = 0.3;
  // 星子先乱闪，再飞过去聚成 Mythclass，然后控制台显现
  const T_STARS = 850;
  const T_GATHER = 1750;
  const T_HOLD = 900;
  const T_TOTAL = T_STARS + T_GATHER + T_HOLD;

  let w = 0, h = 0, shape = [], free = [], startedAt = 0, lastAt = 0, revealed = false;

  function measureTargets() {
    const off = document.createElement('canvas');
    off.width = w; off.height = h;
    const octx = off.getContext('2d');
    const size = Math.min(w * 0.115, h * 0.2, 168);
    octx.fillStyle = '#fff';
    octx.textAlign = 'center';
    octx.textBaseline = 'middle';
    octx.font = '700 ' + size + 'px Inter, "Segoe UI", system-ui, sans-serif';
    try { octx.letterSpacing = Math.round(size * 0.05) + 'px'; } catch (_) { }
    octx.fillText('Mythclass', w / 2, h / 2);

    const data = octx.getImageData(0, 0, w, h).data;
    const stride = Math.max(4, Math.round(size / 30));
    const points = [];
    for (let y = 0; y < h; y += stride) {
      for (let x = 0; x < w; x += stride) {
        if (data[(y * w + x) * 4 + 3] > 130) points.push([x, y]);
      }
    }
    const wanted = Math.min(SHAPE_COUNT, points.length);
    if (!wanted) return [];
    const step = points.length / wanted;
    const picked = [];
    for (let i = 0; i < wanted; i += 1) picked.push(points[Math.floor(i * step)]);
    return picked;
  }

  function seed() {
    shape = measureTargets().map(([tx, ty]) => ({
      tx, ty,
      x: Math.random() * w, y: Math.random() * h,
      size: 0.6 + Math.random() * 1.1,
      delay: Math.random() * 0.4,
      twinkle: Math.random() * Math.PI * 2,
      color: COLORS[Math.floor(Math.random() * COLORS.length)],
    }));
    free = Array.from({ length: FREE_COUNT }, () => ({
      x: Math.random() * w, y: Math.random() * h,
      size: 0.5 + Math.random() * 1.4,
      drift: 0.35 + Math.random() * 0.9,
      twinkle: Math.random() * Math.PI * 2,
      color: COLORS[Math.floor(Math.random() * COLORS.length)],
    }));
  }

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth; h = window.innerHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    seed();
  }

  function frame(now) {
    const dt = lastAt ? Math.min((now - lastAt) / 1000, 0.05) : 0;
    lastAt = now;
    const t = now - startedAt;

    ctx.clearRect(0, 0, w, h);
    ctx.globalCompositeOperation = 'lighter';

    for (const s of free) {
      s.y += SPEED * s.drift * dt * 60;
      s.x += SPEED * 0.25 * dt * 60;
      if (s.y > h + 4) { s.y = -4; s.x = Math.random() * w; }
      if (s.x > w + 4) s.x = -4;
      ctx.globalAlpha = 0.12 + 0.22 * (0.5 + 0.5 * Math.sin(now / 1100 + s.twinkle));
      ctx.fillStyle = s.color;
      ctx.beginPath(); ctx.arc(s.x, s.y, s.size, 0, Math.PI * 2); ctx.fill();
    }

    for (const s of shape) {
      const span = 1 - s.delay || 1;
      // 前 850ms 先乱闪，之后往字上飞
      const local = Math.min(1, Math.max(0, (t - T_STARS) / T_GATHER - s.delay) / span);
      const ease = 1 - Math.pow(1 - local, 3);
      const jitter = (1 - ease) * 5;
      const px = s.x + (s.tx - s.x) * ease + Math.sin(now / 130 + s.twinkle) * jitter;
      const py = s.y + (s.ty - s.y) * ease + Math.cos(now / 150 + s.twinkle) * jitter;
      const breathe = 0.3 + 0.16 * Math.sin(now / 700 + s.twinkle);
      ctx.globalAlpha = Math.max(0, Math.min(1, breathe * (0.35 + 0.65 * ease)));
      ctx.fillStyle = s.color;
      ctx.beginPath(); ctx.arc(px, py, Math.max(0.2, s.size), 0, Math.PI * 2); ctx.fill();
    }

    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';

    // 字聚好了、也停够了 —— 这时候把控制台显出来（星野继续留在背后）
    if (!revealed && t >= T_TOTAL) {
      revealed = true;
      const wrap = document.querySelector('.wrap');
      if (wrap) wrap.classList.add('shown');
    }

    requestAnimationFrame(frame);
  }

  window.addEventListener('resize', resize);
  resize();
  startedAt = performance.now();
  requestAnimationFrame(frame);
})();

/* 开屏：1.2 秒后淡出（星子正好在这时候聚成字） */
setTimeout(() => {
  const sp = document.getElementById('splash');
  if (sp) sp.classList.add('gone');
}, 850);   // 和星子开始汇聚对齐：先淡开屏，星野浮现，然后星子聚成字
const key = () => localStorage.getItem('mythkey') || '';
const RAIL_KEY = 'myth.lan.rail';
const EV_KEY = 'myth.lan.events';

const TOOLS = [
  ['lock', '锁屏', 'M7 10V8a5 5 0 0110 0v2M5 10h14v10H5z'],
  ['message', '弹消息', 'M4 5h16v11H8l-4 4z'],
  ['open_app', '开程序', 'M4 5h16v14H4zM8 9l3 3-3 3M13 15h4', { ask: '要开哪个程序？写完整路径或程序名，比如 notepad.exe' }],
  ['open_url', '开网页', 'M12 3a9 9 0 100 18 9 9 0 000-18zM3 12h18M12 3a14 14 0 010 18 14 14 0 010-18', { ask: '要在这台机器上打开哪个网址？' }],
  ['quiet', '黑屏安静', 'M4 5h16v14H4zM9 12h6', { ask: '黑屏上写点什么？留空就只黑屏。（学生叉不掉，右下角有举手按钮）' }],
  ['quiet', '取消黑屏', 'M4 5h16v14H4zM9 12h6M4 4l16 16', { args: { on: false } }],
  ['hand', '举手状态', 'M8 12V6a2 2 0 014 0v6M12 8a2 2 0 014 0v4M16 11a2 2 0 014 0v5a6 6 0 01-6 6h-2a6 6 0 01-6-6v-3a2 2 0 014 0'],
  ['screenshot', '截图', 'M4 8h3l2-2h6l2 2h3v11H4zM12 16a3.2 3.2 0 100-6.4 3.2 3.2 0 000 6.4z'],
  ['net_ban', '禁止上网', 'M12 3a9 9 0 100 18 9 9 0 000-18zM6 6l12 12'],
  ['net_allow', '放开上网', 'M12 3a9 9 0 100 18 9 9 0 000-18zM8 12.5l3 3 5-6'],
  ['reboot', '重启', 'M20 12a8 8 0 11-3-6.2M20 4v5h-5'],
  ['shutdown', '关机', 'M12 3v9M7.5 6.5a7 7 0 109 0'],
];

function icon(path) {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="' + path + '"/></svg>';
}

// 事件栏
let evTab = 'event';
const logs = { event: [], message: [] };
function logEvent(text, bad) {
  const t = new Date().toTimeString().slice(0, 8);
  logs.event.unshift({ t, text, bad: !!bad });
  if (logs.event.length > 120) logs.event.pop();
  renderEvents();
}
function renderEvents() {
  const list = evTab === 'event' ? logs.event : logs.message;
  $('evList').innerHTML = list.length
    ? list.map((e) => '<li><span class="ev-time">' + e.t + '</span><span class="ev-text' + (e.bad ? ' bad' : '') + '">' + e.text + '</span></li>').join('')
    : '<li class="muted">还没有记录。</li>';
}
$('tabEvent').onclick = () => { evTab = 'event'; $('tabEvent').classList.add('active'); $('tabMessage').classList.remove('active'); renderEvents(); };
$('tabMessage').onclick = () => { evTab = 'message'; $('tabMessage').classList.add('active'); $('tabEvent').classList.remove('active'); renderEvents(); };

// 标签页
const panes = ['screen', 'logs', 'usage', 'disk', 'window', 'audio', 'cmd', 'settings'];
function showPane(name) {
  panes.forEach((p) => {
    const btn = document.querySelector(`.tab[data-pane="${p}"]`);
    const pane = document.getElementById('pane-' + p);
    if (btn) btn.classList.toggle('active', p === name);
    if (pane) pane.classList.toggle('on', p === name);
  });
  if (name === 'logs') loadLogs();
  if (name === 'settings') loadSettings();
  if (name === 'usage') loadUsage();
  if (name === 'disk') loadDisk(diskPath || '');
  if (name === 'window') loadWindows();
  if (name === 'audio') loadAudio();
}
document.querySelectorAll('.tab').forEach((b) => {
  b.onclick = () => showPane(b.dataset.pane);
});

async function loadLogs() {
  const body = document.getElementById('logsBody');
  const { data } = await api('/api/file-logs?limit=200');
  const items = (data && data.items) || [];
  document.getElementById('logsCount').textContent = items.length ? `${items.length} 条（最多留 7 天）` : '还没有记录';
  body.innerHTML = items.length
    ? items.map((r) => `<tr><td class="mono">${(r.time || '').slice(5, 19)}</td><td>${r.operation || ''}</td><td>${r.path || ''}</td><td>${r.size || 0}</td></tr>`).join('')
    : '<tr><td colspan="4" class="muted">这台机器最近没改什么文件。</td></tr>';
}

async function loadSettings() {
  const { data } = await api('/api/settings');
  if (!data) return;
  const rows = [
    ['名称', data.name || '—'],
    ['机器 ID', data.clientUid || '—'],
    ['版本', 'v' + (data.version || '?')],
    ['内网地址', (data.localIps || []).join('、') || '—'],
    ['服务端', (data.servers || []).join('、') || '—'],
    ['监视目录', (data.watchDirs || []).join('、') || '（没设）'],
    ['画面帧率', data.screenFps + ' 帧/秒'],
    ['画面质量', data.screenQuality + '%'],
    ['自动更新', data.autoUpdate ? '开着' : '关着'],
  ];
  document.getElementById('setKv').innerHTML = rows.map(([k, v]) => `<b>${k}</b><span>${v}</span>`).join('');
}

const fmtTime = (s) => {
  const n = Math.round(Number(s) || 0);
  if (n < 60) return n + ' 秒';
  if (n < 3600) return Math.floor(n / 60) + ' 分 ' + (n % 60) + ' 秒';
  return Math.floor(n / 3600) + ' 小时 ' + Math.floor((n % 3600) / 60) + ' 分';
};
// 拼路径：盘符列表时 base 是空，进去就是 "C:" + 分隔符
// 这里用 charCode 拿反斜杠，不再写反斜杠字面量（免得被上层字符串吃掉）
const BS = String.fromCharCode(92);
function joinPath(base, name) {
  const text = String(name || '');
  if (!base) return /:$/.test(text) ? text + BS : text;
  const tail = base.endsWith(BS) || base.endsWith('/');
  return base + (tail ? '' : BS) + text;
}

const fmtSize = (b) => {
  const n = Number(b) || 0;
  if (n >= 1073741824) return (n / 1073741824).toFixed(1) + ' GB';
  if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
  if (n >= 1024) return (n / 1024).toFixed(0) + ' KB';
  return n + ' B';
};

async function loadUsage() {
  const { data } = await api('/api/usage');
  const items = (data && data.items) || [];
  document.getElementById('usageNow').textContent = items.length ? `${items.length} 个程序` : '还没数据';
  document.getElementById('usageBody').innerHTML = items.length
    ? items.map((r) => `<tr><td>${r.app}</td><td class="mono">${fmtTime(r.seconds)}</td><td class="muted">${r.title || ''}</td></tr>`).join('')
    : '<tr><td colspan="3" class="muted">刚开机的话，用一会儿就有了。</td></tr>';
}

let diskPath = '';
async function loadDisk(where) {
  diskPath = where !== undefined ? where : document.getElementById('diskPath').value.trim();
  const body = document.getElementById('diskBody');
  body.innerHTML = '<tr><td colspan="4" class="muted">读取中…</td></tr>';
  const { data } = await api('/api/files?path=' + encodeURIComponent(diskPath));
  if (!data || !data.items) {
    body.innerHTML = '<tr><td colspan="4" class="muted">看不了这个目录（可能没权限）。</td></tr>';
    return;
  }
  document.getElementById('diskPath').value = data.path;
  diskPath = data.path;
  const rows = [];
  (data.items || []).forEach((f) => {
    const act = f.dir
      ? `<button class="btn" data-into="${encodeURIComponent(f.name)}">进去</button>`
      : `<button class="btn" data-dl="${encodeURIComponent(f.name)}">下载</button>`;
    const icon = f.drive ? '💽 ' : f.dir ? '📁 ' : '';
    const size = f.dir ? (f.drive && f.size ? '剩余 ' + fmtSize(f.free) + ' / ' + fmtSize(f.size) : '—') : fmtSize(f.size);
    rows.push(`<tr><td>${icon}${f.name}</td><td class="mono">${size}</td><td class="mono muted">${f.drive ? (f.mtime || '') : (f.mtime || '')}</td><td>${act}</td></tr>`);
  });
  body.innerHTML = rows.length ? rows.join('') : '<tr><td colspan="4" class="muted">空的。</td></tr>';

  // 面包屑：每一段都能点回去
  const crumbs = document.getElementById('diskCrumbs');
  if (crumbs) {
    if (data.drives || !data.path) {
      crumbs.innerHTML = '<span class="crumb on">此电脑</span>';
    } else {
      const parts = String(data.path).split(/[\\/]/).filter(Boolean);
      const bits = ['<span class="crumb" data-crumb="">此电脑</span>'];
      let acc = '';
      parts.forEach((seg, i) => {
        acc += (i === 0 ? seg : '\\\\' + seg);
        const isLast = i === parts.length - 1;
        bits.push(`<span class="crumb${isLast ? ' on' : ''}" data-crumb="${encodeURIComponent(acc + (i === 0 ? '\\\\' : ''))}">${seg}</span>`);
      });
      crumbs.innerHTML = bits.join('<span class="sep">›</span>');
      crumbs.querySelectorAll('[data-crumb]').forEach((b) => {
        b.onclick = () => loadDisk(decodeURIComponent(b.dataset.crumb));
      });
    }
  }
  body.querySelectorAll('[data-into]').forEach((b) => {
    b.onclick = () => loadDisk(joinPath(diskPath, decodeURIComponent(b.dataset.into)));
  });
  body.querySelectorAll('[data-dl]').forEach((b) => {
    b.onclick = () => {
      const full = joinPath(diskPath, decodeURIComponent(b.dataset.dl));
      window.open('/api/file?path=' + encodeURIComponent(full), '_blank');
    };
  });
}

async function loadWindows() {
  const { data } = await api('/api/windows');
  const items = (data && data.items) || [];
  document.getElementById('winCount').textContent = items.length ? `${items.length} 个窗口` : '没有窗口';
  const body = document.getElementById('winBody');
  body.innerHTML = items.length
    ? items.map((w) => `<tr><td>${w.app || '—'}</td><td>${w.title}${w.active ? ' · 当前' : ''}</td><td class="muted">${w.stateText || ''}</td><td><button class="btn" data-close="${w.hwnd}">关掉</button></td></tr>`).join('')
    : '<tr><td colspan="4" class="muted">没看到有标题的窗口。</td></tr>';
  body.querySelectorAll('[data-close]').forEach((b) => {
    b.onclick = async () => {
      if (!confirm('确定关掉这个窗口？')) return;
      const { data: res } = await api('/api/windows/close', { hwnd: Number(b.dataset.close) });
      logEvent((res && res.message) || '关掉了', !(res && res.ok));
      loadWindows();
    };
  });
}

async function loadAudio() {
  const { data } = await api('/api/audio');
  const items = (data && data.items) || [];
  document.getElementById('audioNow').textContent = items.length ? `${items.length} 个在出声` : '现在很安静';
  document.getElementById('audioBody').innerHTML = items.length
    ? items.map((a) => `<tr><td>${a.processName || a.app || '—'}</td><td>${a.title || ''}</td><td class="mono">${a.volume === undefined ? '' : Math.round(a.volume * 100) + '%'}</td><td class="muted">${a.state || ''}</td></tr>`).join('')
    : '<tr><td colspan="4" class="muted">这会儿没有程序在放声音。</td></tr>';
}

async function runCmd(value) {
  const cmd = (value !== undefined ? value : document.getElementById('cmdInput').value).trim();
  if (!cmd) return;
  const out = document.getElementById('cmdOut');
  out.textContent = '执行中…';
  const { data } = await api('/api/command', { command: cmd, args: {} });
  const okFlag = data && data.ok;
  out.textContent = (data && (data.output || data.message)) || '没回话';
  logEvent(`命令 ${cmd}：${okFlag ? '成功' : '失败'}`, !okFlag);
}
document.getElementById('cmdRun').onclick = () => runCmd();
document.getElementById('cmdInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') runCmd();
});
document.querySelectorAll('[data-preset]').forEach((b) => {
  b.onclick = () => {
    document.getElementById('cmdInput').value = b.dataset.preset;
    runCmd(b.dataset.preset);
  };
});
document.getElementById('logsRefresh').onclick = loadLogs;
document.getElementById('usageRefresh').onclick = loadUsage;
document.getElementById('winRefresh').onclick = loadWindows;
document.getElementById('audioRefresh').onclick = loadAudio;
document.getElementById('diskGo').onclick = () => loadDisk();
document.getElementById('diskHome').onclick = () => loadDisk('');
document.getElementById('diskUp').onclick = async () => {
  const { data } = await api('/api/files?path=' + encodeURIComponent(diskPath));
  // 盘根没有上一级 —— 那就回「此电脑」
  loadDisk(data && data.parent ? data.parent : '');
};

// 伸缩
function applyFolds() {
  $('grid').classList.toggle('rail-off', localStorage.getItem(RAIL_KEY) === '0');
  $('grid').classList.toggle('events-off', localStorage.getItem(EV_KEY) === '0');
}
$('edgeLeft').onclick = () => {
  const off = localStorage.getItem(RAIL_KEY) === '0';
  localStorage.setItem(RAIL_KEY, off ? '1' : '0');
  applyFolds();
};
$('edgeRight').onclick = () => {
  const off = localStorage.getItem(EV_KEY) === '0';
  localStorage.setItem(EV_KEY, off ? '1' : '0');
  applyFolds();
};

// 接口
async function api(path, body) {
  const opt = { headers: { 'X-Mythclass-Key': key() } };
  if (body !== undefined) {
    opt.method = 'POST';
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(path, opt);
  let data = null;
  try { data = await res.json(); } catch (_) { }
  return { status: res.status, data };
}

function showConsole() {
  $('gate').classList.add('hidden');
  $('console').classList.remove('hidden');
  $('keyPill').textContent = '已连接';
  $('keyPill').className = 'pill on';
  applyFolds();
}

async function loadInfo() {
  const { data } = await api('/api/info');
  if (!data) return;
  $('sub').textContent = '直连这台机器 · 不经过服务器 · v' + (data.version || '?');
  // 锁屏了就说清楚 —— 画面会是黑的/抓不到，别让老师以为坏了
  if (data.locked) {
    $('hint').classList.remove('hidden');
    $('hint').textContent = '这台机器锁屏了。锁屏时 Windows 不给抓屏，点上面的「解锁」就能看了。';
  } else if ($('hint').dataset.locked === '1') {
    $('hint').classList.add('hidden');
    $('hint').dataset.locked = '';
  }
  $('hint').dataset.locked = data.locked ? '1' : '';
  $('ipPill').textContent = '我的地址 ' + (data.clientIp || '?');
  const rows = [
    ['名称', data.name || '—'],
    ['机器 ID', data.clientUid || '—'],
    ['版本', 'v' + (data.version || '?')],
    ['内网地址', (data.localIps || []).join('、') || '—'],
  ];
  $('info').innerHTML = rows.map(([k, v]) => '<b>' + k + '</b><span>' + v + '</span>').join('');
  const owners = (data.teachers || []).map((t) => t.username).filter(Boolean);
  $('owners').innerHTML = owners.length ? owners.map((n) => '<li>' + n + '</li>').join('') : '<li class="muted">还没绑定老师</li>';
  if (data.trusted) showConsole();
}

$('authBtn').onclick = async () => {
  const k = $('key').value.trim();
  if (!k) { $('authOut').textContent = '密钥是空的'; return; }
  const { status, data } = await api('/api/auth', { key: k });
  if (status === 200) {
    localStorage.setItem('mythkey', k);
    $('authOut').textContent = '连上了';
    logEvent('配对密钥验证通过');
    showConsole();
    loadInfo();
  } else {
    $('authOut').textContent = (data && data.message) || '密钥不对';
    logEvent('密钥不对', true);
  }
};
$('key').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('authBtn').click(); });

// 工具条
$('tools').innerHTML = TOOLS.map(([cmd, label, p], i) =>
  '<button class="tool" data-tool="' + i + '" title="' + label + '">' + icon(p) + '<span>' + label + '</span></button>'
).join('');
document.querySelectorAll('[data-tool]').forEach((b) => {
  b.onclick = () => {
    const [cmd, label, , extra] = TOOLS[Number(b.dataset.tool)];
    if (extra && extra.args) return send(cmd, extra.args);
    if (extra && extra.ask !== undefined) {
      const answer = prompt(extra.ask, '');
      if (answer === null) return;
      if (cmd === 'open_app') return send(cmd, { app: answer.trim() });
      if (cmd === 'open_url') return send(cmd, { url: answer.trim(), text: answer.trim() });
      if (cmd === 'quiet') return askQuiet(answer.trim());
      return send(cmd, { text: answer });
    }
    return send(cmd);
  };
});

async function askQuiet(text) {
  const { data } = await api('/api/quiet', { on: true, text });
  logEvent(`黑屏安静：${data && data.on ? '开了' : '没开'}（${text || '无字'}）`, !(data && data.on));
  const st = await api('/api/quiet');
  if (st && st.data && st.data.on) logEvent('学生现在看不到屏幕了，右下角有举手按钮');
}

/** 打开「发通知」对话框（局域网这边也能自定义，不再用浏览器自带的 prompt） */
function openNotice() {
  $('ntMask').classList.remove('hidden');
}

function closeNotice() {
  $('ntMask').classList.add('hidden');
}

function noticeArgs() {
  const opts = [];
  for (let i = 0; i < 3; i++) {
    if (!$('ntOn' + i).checked) continue;
    opts.push({
      on: true,
      label: ($('ntLabel' + i).value || '').trim(),
      slot: i,
      send: i === 2 ? ($('ntSend2').value || '').trim() : '',
    });
  }
  const num = (id, fallback) => Number($(id).value) || fallback;
  return {
    title: ($('ntTitle').value || '').trim() || '老师有话要说',
    body: ($('ntBody').value || '').trim() || '老师有话要说',
    topmost: $('ntTop').checked,
    fullscreen: $('ntFull').checked,
    autoFit: $('ntFit').checked,
    autoClose: $('ntAuto').checked ? (Number($('ntAutoSec').value) || 0) : 0,
    size: { w: num('ntW', 520), h: num('ntH', 300) },
    fontSize: { title: num('ntFT', 16), body: num('ntFB', 12), button: num('ntFBtn', 10) },
    options: opts,
  };
}

async function sendNotice() {
  const args = noticeArgs();
  closeNotice();
  $('hint').classList.remove('hidden');
  $('hint').textContent = '通知已发出去，等他回答…';
  logEvent('发通知：' + args.title);
  const { data } = await api('/api/command', { command: 'message', args });
  const out = (data && (data.output || data.message)) || '没回话';
  logEvent(out, !(data && data.ok));
  $('hint').textContent = out;
}

// 勾选互斥：纯弹出关掉三个选项；勾选项关掉纯弹出
function syncAuto() {
  const on = $('ntAuto').checked;
  $('ntAutoRow').classList.toggle('hidden', !on);
  $('ntAutoTip').classList.toggle('hidden', !on);
  if (on) for (let i = 0; i < 3; i++) $('ntOn' + i).checked = false;
}
$('ntAuto').onchange = syncAuto;
for (let i = 0; i < 3; i++) {
  $('ntOn' + i).addEventListener('change', () => {
    if ($('ntOn' + i).checked) { $('ntAuto').checked = false; syncAuto(); }
  });
}

$('ntCancel').onclick = closeNotice;
$('ntSend').onclick = sendNotice;

async function send(command, override) {
  let args = override || {};
  if (command === 'message' && !override) {
    // 局域网这边也要能自定义置顶/全屏/标题/内容/选项
    return openNotice();
  }
  const { data } = await api('/api/command', { command, args });
  const out = (data && (data.output || data.message)) || '没回话';
  const label = (TOOLS.find((t) => t[0] === command) || [, command])[1];
  logEvent(label + '：' + out, !(data && data.ok));
  if (command === 'screenshot') grabOnce();
}

// 画面
let timer = null, frames = 0, t0 = 0;
function tick() {
  const img = new Image();
  img.onload = () => {
    $('screen').src = img.src;
    frames++;
    const sec = (Date.now() - t0) / 1000;
    $('fps').textContent = sec > 0 ? (frames / sec).toFixed(1) + ' 帧/秒' : '—';
    $('size').textContent = img.naturalWidth + '×' + img.naturalHeight;
    $('hint').classList.add('hidden');
  };
  img.onerror = async () => {
    $('fps').textContent = '抓不到画面';
    if ($('hint').dataset.why) return;
    try {
      const res = await fetch('/frame', { headers: { 'X-Mythclass-Key': key() } });
      const data = await res.json();
      const why = (data && data.message) || '抓不到屏幕';
      $('hint').dataset.why = '1';
      $('hint').classList.remove('hidden');
      $('hint').textContent = why + '　（这台机器锁屏时抓不到画面，先解锁再试）';
      logEvent(why, true);
    } catch (_) {
      /* 问不出来就算了 */
    }
  };
  img.src = '/frame?t=' + Date.now();
}
function startWatch() {
  if (timer) return;
  t0 = Date.now(); frames = 0;
  tick();
  timer = setInterval(tick, 250)   // 4 帧/秒，局域网里够顺了;
  logEvent('开始看画面');
}
function stopWatch() {
  if (timer) { clearInterval(timer); timer = null; }
  $('fps').textContent = '停了';
  logEvent('停下画面');
}
function grabOnce() {
  const img = new Image();
  img.onload = () => { $('screen').src = img.src; $('hint').classList.add('hidden'); };
  img.src = '/frame?t=' + Date.now();
}
$('watchBtn').onclick = startWatch;
$('stopBtn').onclick = stopWatch;
$('fullBtn').onclick = () => {
  const el = $('screen');
  if (el.requestFullscreen) el.requestFullscreen();
};

(async () => {
  const fromUrl = new URLSearchParams(location.search).get('key');
  if (fromUrl) {
    localStorage.setItem('mythkey', fromUrl);
    history.replaceState(null, '', location.pathname);
    logEvent('密钥来自链接，自动连上');
  }
  if (key()) {
    const { status } = await api('/api/info');
    if (status === 200) {
      showConsole();
      startWatch();   // 连上就自动开始看，不用再点一下
    }
  }
  loadInfo();
  renderEvents();
  setInterval(loadInfo, 15000);
})();
</script>
</body>
</html>
"""
