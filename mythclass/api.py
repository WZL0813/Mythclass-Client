"""跟服务端打交道

两条路：
- REST（requests）：注册、心跳、上报记录
- WebSocket：自己实现了最小可用的 Engine.IO v4 + Socket.IO 帧，省一个依赖

协议细节见 docs/通信协议.md
"""

from __future__ import annotations

import json
import os
import ssl
import threading
import time
from typing import Callable

import requests
import websocket

from . import __version__, config, identity

DEFAULT_TIMEOUT = 15


def http_base(ws_url: str) -> str:
    """wss://x → https://x"""
    url = (ws_url or "").strip().rstrip("/")
    if url.startswith("wss://"):
        return "https://" + url[6:]
    if url.startswith("ws://"):
        return "http://" + url[5:]
    if url.startswith("http"):
        return url
    return "https://" + url


def ca_bundle() -> str | None:
    """certifi 的证书库路径。拿不到就返回 None，退回系统库。

    为什么要用它：requests 默认就用这份，所以心跳一直是通的；
    而 websocket-client 默认用系统证书库 —— 老 Windows 镜像
    （虚拟机里很常见）根 CA 不全，于是「心跳能通、WebSocket 验不过」。
    """
    try:
        import certifi

        path = certifi.where()
        return path if path and os.path.exists(path) else None
    except Exception:
        return None


def socket_url(ws_url: str) -> str:
    url = (ws_url or "").strip().rstrip("/")
    if not url.startswith(("ws://", "wss://")):
        url = "wss://" + url
    return f"{url}/socket.io/?EIO=4&transport=websocket"


class ServerApi:
    """REST 那一半"""

    def __init__(self, base: str):
        self.base = http_base(base)
        self.session = requests.Session()
        self.token = ""
        self.clock_skew = 0.0  # 本机时间 − 服务端时间，秒

    def _headers(self) -> dict:
        head = {"Content-Type": "application/json"}
        if self.token:
            head["Authorization"] = f"Bearer {self.token}"
        return head

    def register(
        self, client_uid: str, name: str, local_ips: list[str] | None = None,
        lan_key: str = "", lan_port: int = 0, lan_web_port: int = 0,
    ) -> dict:
        resp = self.session.post(
            f"{self.base}/api/client/register",
            json={
                "clientUid": client_uid,
                "name": name,
                "os": identity.os_description(),
                "version": __version__,
                "localIps": local_ips or [],
                # 本机局域网密钥：教师端拼带密钥的直连链接要用
                "lanKey": lan_key or "",
                # 实际在用的局域网端口（系统占用时会自己换，教师端拼地址要用）
                "lanPort": int(lan_port or 0),
                "lanWebPort": int(lan_web_port or 0),
            },
            headers=self._headers(),
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        self.token = data.get("token", "")

        # HTTP 的 Date 头就是服务端的时间。虚拟机时钟最爱飘，
        # 而 JWT 是时间敏感的：差太多会变成「凭证不认」。
        date_header = resp.headers.get("Date")
        if date_header:
            try:
                from email.utils import parsedate_to_datetime

                server_time = parsedate_to_datetime(date_header).timestamp()
                self.clock_skew = time.time() - server_time
            except Exception:
                self.clock_skew = 0.0
        return data

    def teachers(self) -> dict | None:
        """拉这台机器绑定的老师（本地网页要显示归属）"""
        try:
            res = requests.get(
                f"{self.base}/api/client/teachers",
                headers=self._headers(),
                timeout=DEFAULT_TIMEOUT,
            )
            if res.status_code != 200:
                return None
            data = res.json()
            return data if isinstance(data, dict) else None
        except requests.RequestException:
            return None

    def heartbeat(
        self, local_ips: list[str] | None = None, lan_key: str = "",
        lan_port: int = 0, lan_web_port: int = 0,
    ) -> bool:
        try:
            resp = self.session.post(
                f"{self.base}/api/client/heartbeat",
                json={
                    "localIps": local_ips or [],
                    "lanKey": lan_key,
                    "lanPort": int(lan_port or 0),
                    "lanWebPort": int(lan_web_port or 0),
                },
                headers=self._headers(),
                timeout=DEFAULT_TIMEOUT,
            )
            return resp.ok
        except requests.RequestException:
            return False

    def upload_file_logs(self, logs: list[dict]) -> bool:
        if not logs:
            return True
        payload = {
            "logs": [
                {
                    "timestamp": item.get("timestamp"),
                    "operation": item.get("operation"),
                    "filePath": item.get("file_path"),
                    "fileSize": item.get("file_size"),
                }
                for item in logs
            ]
        }
        try:
            resp = self.session.post(
                f"{self.base}/api/client/file-logs",
                json=payload,
                headers=self._headers(),
                timeout=DEFAULT_TIMEOUT,
            )
            return resp.ok
        except requests.RequestException:
            return False

    def upload_audio(self, items: list[dict]) -> bool:
        if not items:
            return True
        try:
            resp = self.session.post(
                f"{self.base}/api/client/audio-info",
                json={"items": items},
                headers=self._headers(),
                timeout=DEFAULT_TIMEOUT,
            )
            return resp.ok
        except requests.RequestException:
            return False

    def command_result(self, request_id: str, command: str, ok: bool, output: str) -> None:
        try:
            self.session.post(
                f"{self.base}/api/client/command-result",
                json={"requestId": request_id, "command": command, "ok": ok, "output": output[:1500]},
                headers=self._headers(),
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException:
            pass


class SocketClient:
    """WebSocket 那一半。自己解 Engine.IO v4 的帧。"""

    def __init__(
        self,
        ws_url: str,
        token: str,
        on_event: Callable[[str, dict], None],
        on_state: Callable[[bool], None] | None = None,
        on_ready: Callable[[], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        idle_timeout: float = 75.0,
    ):
        self.url = socket_url(ws_url)
        self.token = token
        self.on_event = on_event
        self.on_state = on_state or (lambda _s: None)
        self.on_ready = on_ready or (lambda: None)
        self.on_log = on_log or (lambda _m: None)
        self.idle_timeout = idle_timeout
        self.auth_failed = False  # 凭证不认：得让上层重新注册换一张
        self.last_error = ""     # 最近一次连不上的原因，--status 会显示
        self.fail_count = 0

        self._ws: websocket.WebSocket | None = None
        self._stop = threading.Event()
        # 心跳时顺便报一下实际端口（由外层设成可调用对象）
        self.ports_provider = None
        # 手动重连用：叫醒退避等待，不用干等最多 60 秒
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._outbox: list[str] = []
        self._lock = threading.Lock()
        self.connected = False

    # ------------------------------ 生命周期 ------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mythclass-socket", daemon=True)
        self._thread.start()

    def force_reconnect(self) -> None:
        """手动重连：掐掉当前连接、叫醒退避等待，立刻重来一次。

        老师那边网络换了、或者只是想马上重连，不用干等退避，
        也不用重启客户端。
        """
        self.fail_count = 0
        self.last_error = ""
        self._wake.set()
        try:
            # 属性名是 _ws（写成 ws 会被下面的 except 吞掉，静默失效）
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    def emit(self, event: str, payload: dict | None = None) -> None:
        frame = "42" + json.dumps([event, payload] if payload is not None else [event], ensure_ascii=False)
        with self._lock:
            self._outbox.append(frame)

    # -------------------------------- 内部 --------------------------------

    def _loop(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            try:
                self._connect_once()
                backoff = 2
                self.fail_count = 0
            except Exception as err:
                self.connected = False
                self.on_state(False)
                self.last_error = f"{type(err).__name__}: {err}"
                self.fail_count += 1
                if self.fail_count <= 3 or self.fail_count % 10 == 0:
                    self.on_log(f"WebSocket 连不上（第 {self.fail_count} 次）：{self.last_error}")
                if self.fail_count == 3:
                    self.on_log(
                        "提示：心跳能通但 WebSocket 连不上，通常是网络里有代理/防火墙"
                        "挡了 WebSocket 升级，或者系统根证书太旧（心跳走 requests 自带的"
                        "证书库，WebSocket 走系统的）。"
                    )
            if self.auth_failed:
                # 凭证不认，再撞多少次都一样。收工，让上层重新注册
                self._stop.set()
                break
            if self._stop.is_set():
                break
            # 断线重连：慢慢退避，最多 60 秒。
            # 用 _wake 而不是 _stop，这样「手动重连」能立刻把它叫醒
            self._wake.wait(backoff)
            if self._wake.is_set():
                self._wake.clear()
                backoff = 2  # 手动重连：退避从头开始，别又等一分钟
            else:
                backoff = min(backoff * 2, 60)

    def _connect_once(self) -> None:
        sslopt = {"cert_reqs": ssl.CERT_REQUIRED}
        ca = ca_bundle()
        if ca:
            # 和 requests 用同一份，别让系统证书库拖后腿
            sslopt["ca_certs"] = ca

        ws = websocket.create_connection(
            self.url,
            timeout=10,
            sslopt=sslopt,
            enable_multithread=True,
        )
        self._ws = ws

        # 1) 握手包：0{"sid":...}
        handshake = ws.recv()
        if not isinstance(handshake, str) or not handshake.startswith("0"):
            raise ConnectionError(f"握手失败：{handshake!r}")

        # 2) Socket.IO CONNECT，把 token 塞进 auth
        ws.send("40" + json.dumps({"token": self.token}))
        ws.settimeout(10)
        ack = ws.recv()
        if isinstance(ack, str) and ack.startswith("44"):
            # 别拿同一张死证反复撞：标记一下，让外层重新注册换新的
            self.auth_failed = True
            raise ConnectionError(f"服务端不认这个凭证：{ack[2:]}")

        self.connected = True
        self.on_state(True)
        self.on_ready()

        ws.settimeout(1.0)
        last_heartbeat = 0.0
        last_rx = time.time()

        while not self._stop.is_set():
            # 发心跳事件（每 20 秒）
            if time.time() - last_heartbeat > 20:
                self.emit(
                    "heartbeat",
                    self.ports_provider() if callable(self.ports_provider) else None,
                )
                last_heartbeat = time.time()

            # 把攒着的帧发出去
            with self._lock:
                pending, self._outbox = self._outbox, []
            for frame in pending:
                ws.send(frame)

            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                # 服务端每 25 秒会 ping 一次；超时太久说明这条连接已经死了
                # （虚拟机睡眠/恢复后 NAT 表失效就是这种半开状态）
                if time.time() - last_rx > self.idle_timeout:
                    self.on_log(f"{int(self.idle_timeout)} 秒没收到服务端任何数据，判定连接已死，重连。")
                    break
                continue
            except Exception:
                break
            last_rx = time.time()

            if not raw:
                continue
            if not isinstance(raw, str):
                continue
            self._handle(raw)

        self.connected = False
        self.on_state(False)
        try:
            ws.close()
        except Exception:
            pass
        self._ws = None

    def _handle(self, raw: str) -> None:
        # Engine.IO 层
        if raw == "2":  # ping → pong
            try:
                self._ws.send("3")
            except Exception:
                pass
            return
        if raw == "1":  # server close
            raise ConnectionError("服务端关掉了连接")

        if not raw.startswith("4"):
            return

        packet = raw[1:]
        if packet.startswith("2"):  # 42["event", {...}]
            try:
                data = json.loads(packet[1:])
            except json.JSONDecodeError:
                return
            if isinstance(data, list) and data:
                event = data[0]
                payload = data[1] if len(data) > 1 else {}
                try:
                    self.on_event(event, payload if isinstance(payload, dict) else {"value": payload})
                except Exception:
                    pass
        # 40 = connect ack，41 = disconnect，忽略就好
