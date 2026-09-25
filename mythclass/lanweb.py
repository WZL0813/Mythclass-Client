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
                        self._json(503, {"error": "NO_FRAME", "message": "抓不到画面"})
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
<title>Mythclass · 局域网直连</title>
<style>
  :root {
    --ink: #0f1411; --panel: #141b17; --line: #2a3a2e;
    --text: #e8efe6; --dim: #8fa88e; --moss: #5e9a73;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; color: var(--text);
    font: 15px/1.65 "Microsoft YaHei UI", "PingFang SC", system-ui, sans-serif;
    background:
      radial-gradient(1100px 620px at 8% -10%, rgba(94,154,115,.16), transparent 62%),
      radial-gradient(760px 420px at 108% 8%, rgba(94,154,115,.09), transparent 60%),
      var(--ink);
    background-attachment: fixed;
  }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 34px 26px 60px; }
  header { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-end; justify-content: space-between; }
  h1 { margin: 0; font-size: 26px; letter-spacing: .5px; }
  .sub { color: var(--dim); font-size: 13px; margin-top: 6px; }
  .pill { border: 1px solid var(--line); border-radius: 999px; padding: 5px 12px; color: var(--dim); font-size: 12.5px; }
  .pill.on { border-color: rgba(94,154,115,.5); color: #a6d4b3; background: rgba(94,154,115,.12); }
  .grid { display: grid; grid-template-columns: 1.55fr .95fr; gap: 20px; margin-top: 26px; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 18px; }
  .card h2 { margin: 0 0 12px; font-size: 15px; color: var(--dim); font-weight: 500; }
  #screen { width: 100%; border-radius: 10px; background: #0b0f0c; min-height: 260px; display: block; border: 1px solid var(--line); }
  input, button { font: inherit; }
  input[type=password], input[type=text] {
    width: 100%; padding: 10px 12px; border-radius: 9px; color: var(--text);
    background: #0f1613; border: 1px solid var(--line);
  }
  button {
    cursor: pointer; border-radius: 9px; padding: 9px 14px; color: var(--text);
    background: #1b241e; border: 1px solid var(--line);
  }
  button.primary { background: #3f6b52; border-color: #4c7d61; color: #f1f6ef; }
  button:disabled { opacity: .45; cursor: not-allowed; }
  .row { display: flex; gap: 9px; flex-wrap: wrap; margin-top: 12px; }
  .muted { color: var(--dim); font-size: 13px; }
  .out { margin-top: 12px; font-size: 13px; color: #a6bca4; min-height: 22px; white-space: pre-wrap; }
  ul { margin: 0; padding-left: 18px; color: var(--dim); font-size: 13.5px; }
  .hidden { display: none; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1 id="name">正在读这台机器…</h1>
      <div class="sub" id="sub">局域网直连 · 不经服务器</div>
    </div>
    <div>
      <span class="pill" id="ipPill">—</span>
      <span class="pill" id="keyPill">未连接</span>
    </div>
  </header>

  <section class="card" id="authCard" style="margin-top:26px">
    <h2>先对一下暗号</h2>
    <p class="muted">配对密钥在一体机的客户端里看：命令行跑 <code>MythclassClient.exe --status</code>，或者翻它的日志。同一台电脑的老师连满 3 次才会发密钥。</p>
    <div class="row">
      <input type="password" id="key" placeholder="粘贴配对密钥" autocomplete="off">
      <button class="primary" id="authBtn">连上</button>
    </div>
    <div class="out" id="authOut"></div>
  </section>

  <div class="grid hidden" id="main">
    <section class="card">
      <h2>画面</h2>
      <img id="screen" alt="这台机器的屏幕">
      <div class="row">
        <button class="primary" id="watchBtn">开始看</button>
        <button id="stopBtn">停下</button>
        <span class="muted" id="fps" style="align-self:center"></span>
      </div>
    </section>
    <section class="card">
      <h2>常用操作</h2>
      <div class="row">
        <button data-cmd="lock">锁屏</button>
        <button data-cmd="unlock">解锁</button>
        <button data-cmd="shutdown">关机</button>
        <button data-cmd="reboot">重启</button>
        <button data-cmd="logout">注销</button>
        <button data-cmd="message">发消息</button>
        <button data-cmd="screen_stop">关屏幕流</button>
      </div>
      <h2 style="margin-top:20px">这台机器归属</h2>
      <ul id="owners"><li>—</li></ul>
      <div class="out" id="out"></div>
    </section>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);
const key = () => localStorage.getItem('mythkey') || '';

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

function show(main) {
  $('authCard').classList.toggle('hidden', main);
  $('main').classList.toggle('hidden', !main);
  $('keyPill').textContent = main ? '已连接' : '未连接';
  $('keyPill').className = 'pill' + (main ? ' on' : '');
}

async function loadInfo() {
  const { data } = await api('/api/info');
  if (!data) return;
  $('name').textContent = data.name || '这台一体机';
  $('sub').textContent = '局域网直连 · 不经服务器 · v' + (data.version || '?');
  $('ipPill').textContent = '我的地址 ' + (data.clientIp || '?');
  const owners = (data.teachers || []).map(t => t.username).filter(Boolean);
  $('owners').innerHTML = owners.length
    ? owners.map(n => '<li>' + n + '</li>').join('')
    : '<li class="muted">还没绑定老师</li>';
  if (data.trusted) show(true);
}

async function doAuth() {
  const k = $('key').value.trim();
  if (!k) { $('authOut').textContent = '密钥是空的'; return; }
  const { status, data } = await api('/api/auth', { key: k });
  if (status === 200) {
    localStorage.setItem('mythkey', k);
    $('authOut').textContent = '连上了';
    show(true);
    loadInfo();
  } else {
    $('authOut').textContent = (data && data.message) || '密钥不对';
  }
}

let timer = null, frames = 0, t0 = 0;
function startWatch() {
  if (timer) return;
  t0 = Date.now(); frames = 0;
  const tick = () => {
    const img = new Image();
    img.onload = () => {
      $('screen').src = img.src;
      frames++;
      const sec = (Date.now() - t0) / 1000;
      $('fps').textContent = sec > 0 ? (frames / sec).toFixed(1) + ' 帧/秒' : '';
    };
    img.onerror = () => { $('fps').textContent = '抓不到画面'; };
    img.src = '/frame?t=' + Date.now();
  };
  tick();
  timer = setInterval(tick, 400);
}
function stopWatch() {
  if (timer) { clearInterval(timer); timer = null; }
  $('fps').textContent = '停了';
}

async function send(command) {
  let args = {};
  if (command === 'message') {
    const text = prompt('要在一体机上显示什么？');
    if (!text) return;
    args = { text: text };
  }
  const { data } = await api('/api/command', { command: command, args: args });
  $('out').textContent = (data && (data.output || data.message)) || '没回话';
}

$('authBtn').onclick = doAuth;
$('key').addEventListener('keydown', (e) => { if (e.key === 'Enter') doAuth(); });
$('watchBtn').onclick = startWatch;
$('stopBtn').onclick = stopWatch;
document.querySelectorAll('[data-cmd]').forEach(b => { b.onclick = () => send(b.dataset.cmd); });

(async () => {
  // 链接里带 key（老师从教师端点过来就是这种）：自动连上，不用手输
  const fromUrl = new URLSearchParams(location.search).get('key');
  if (fromUrl) {
    localStorage.setItem('mythkey', fromUrl);
    history.replaceState(null, '', location.pathname + location.search);
  }
  if (key()) {
    const { status } = await api('/api/info');
    if (status === 200) show(true);
  }
  loadInfo();
  setInterval(loadInfo, 15000);
})();
</script>
</body>
</html>
"""
