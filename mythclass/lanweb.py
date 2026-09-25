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
                    from urllib.parse import parse_qs

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
<title>Mythclass · 局域网控制台</title>
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
  .wrap { max-width: 1500px; margin: 0 auto; padding: 14px 16px 40px; }

  /* 顶部：品牌 + 机器名 + 状态 */
  .brand-row { display: flex; align-items: center; gap: 12px; padding: 6px 2px 14px; }
  .mark {
    width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center;
    border: 1px solid rgba(94,154,115,.45); background: rgba(94,154,115,.14); color: #9fd0ac;
  }
  .brand-name { font-size: 17px; font-weight: 600; letter-spacing: .4px; }
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
<div class="wrap">
  <div class="brand-row">
    <div class="mark">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M4 20V9l8-5 8 5v11"/><path d="M9 20v-6h6v6"/></svg>
    </div>
    <div>
      <div class="brand-name">局域网控制台</div>
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

      <!-- 中：屏幕 -->
      <section class="card">
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
const key = () => localStorage.getItem('mythkey') || '';
const RAIL_KEY = 'myth.lan.rail';
const EV_KEY = 'myth.lan.events';

const TOOLS = [
  ['lock', '锁屏', 'M7 10V8a5 5 0 0110 0v2M5 10h14v10H5z'],
  ['unlock', '解锁', 'M7 10V8a5 5 0 019-3M5 10h14v10H5z'],
  ['message', '弹消息', 'M4 5h16v11H8l-4 4z'],
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
$('tools').innerHTML = TOOLS.map(([cmd, label, p]) =>
  '<button class="tool" data-cmd="' + cmd + '" title="' + label + '">' + icon(p) + '<span>' + label + '</span></button>'
).join('');
document.querySelectorAll('[data-cmd]').forEach((b) => {
  b.onclick = () => send(b.dataset.cmd);
});

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
  return {
    title: ($('ntTitle').value || '').trim() || '老师有话要说',
    body: ($('ntBody').value || '').trim() || '老师有话要说',
    topmost: $('ntTop').checked,
    fullscreen: $('ntFull').checked,
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

$('ntCancel').onclick = closeNotice;
$('ntSend').onclick = sendNotice;

async function send(command) {
  let args = {};
  if (command === 'message') {
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
