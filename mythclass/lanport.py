"""局域网直连端口

老师在同一个局域网里时，可以不经过服务器直接连这个端口控制这台机器。
协议故意做得极土：一行一个 JSON。

  → {"type":"hello","key":"<配对密钥>","name":"老师的机器"}
  ← {"type":"hello","ok":true,"name":"高一(3)班一体机","version":"2.0.5","screen":false}

  → {"type":"ping"}
  ← {"type":"pong","t":1758700000000}

  → {"type":"command","command":"lock","args":{}}
  ← {"type":"result","ok":true,"output":"锁了"}

密钥对不上就直接断开。密钥来自 trust.py：同一个 IP 的教师端直连满 3 次
之后才会拿到一张。

为什么还要密钥：局域网里谁都能把 IP 改成老师那台机器的地址，
光看 IP 等于谁都能控制一体机。
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Callable

DEFAULT_PORT = 26924
MAX_LINE = 8192
HELLO_TIMEOUT = 10.0


class LanPort:
    """监听一个端口，认密钥，然后把命令交给主程序执行"""

    def __init__(
        self,
        trust,
        on_command: Callable[[str, dict], tuple[bool, str]],
        log: Callable[[str], None],
        port: int = DEFAULT_PORT,
    ):
        self.trust = trust
        self.on_command = on_command
        self.log = log
        self.port = int(port)
        self.running = False
        self.clients = 0
        self.served = 0  # 一共服务过多少次命令

        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------ 生命周期 ------------------------------

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # 同上层：端口可能被系统保留，往后找 300 个
            bound = False
            last_err = None
            for offset in range(0, 300):
                want = int(self.port) + offset
                try:
                    sock.bind(("0.0.0.0", want))
                    if offset:
                        self.log(f"局域网直连端口 {self.port} 用不了，改用 {want}")
                    self.port = want
                    bound = True
                    break
                except OSError as err:
                    last_err = err
                    continue
            if not bound:
                raise OSError(f"直连端口 20 个都绑不上：{last_err}")
            sock.listen(8)
            sock.settimeout(1.0)
        except OSError as err:
            self.log(f"局域网端口 {self.port} 开不起来（{err}），跳过。")
            return False

        self._sock = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._accept_loop, name="mythclass-lan", daemon=True)
        self._thread.start()
        self.running = True
        self.log(f"局域网直连端口已开：0.0.0.0:{self.port}")
        return True

    def stop(self) -> None:
        self._stop.set()
        self.running = False
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        self._sock = None

    def status(self) -> str:
        if not self.running:
            return "没开"
        return f"开着（{self.port}，已服务 {self.served} 条命令）"

    # -------------------------------- 内部 --------------------------------

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn, addr), daemon=True).start()
        self.running = False

    def _serve(self, conn: socket.socket, addr) -> None:
        ip = addr[0] if addr else ""
        self.clients += 1
        try:
            conn.settimeout(HELLO_TIMEOUT)
            line = self._read_line(conn)
            hello = json.loads(line) if line else {}
            if hello.get("type") != "hello" or not self.trust.check(ip, hello.get("key") or ""):
                self.log(f"局域网端口：{ip} 密钥不对，断开。")
                self._send(conn, {"type": "hello", "ok": False, "reason": "KEY_REJECTED"})
                return

            self._send(
                conn,
                {
                    "type": "hello",
                    "ok": True,
                    "port": self.port,
                    "at": int(time.time() * 1000),
                },
            )
            self.log(f"局域网端口：{ip}（{hello.get('name') or '未报名'}）已连上。")

            conn.settimeout(60)
            while not self._stop.is_set():
                line = self._read_line(conn)
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue

                kind = msg.get("type")
                if kind == "ping":
                    self._send(conn, {"type": "pong", "t": int(time.time() * 1000)})
                elif kind == "command":
                    command = str(msg.get("command") or "").strip()
                    args = msg.get("args") if isinstance(msg.get("args"), dict) else {}
                    if not command:
                        continue
                    try:
                        ok, output = self.on_command(command, args)
                    except Exception as err:  # 命令炸了不能把端口带崩
                        ok, output = False, f"{type(err).__name__}: {err}"
                    self.served += 1
                    self.log(f"局域网命令 {command}：{'成功' if ok else '失败'} - {output}")
                    self._send(conn, {"type": "result", "command": command, "ok": ok, "output": output})
        except Exception as err:
            self.log(f"局域网端口：{ip} 连接出错（{type(err).__name__}: {err}）")
        finally:
            self.clients = max(0, self.clients - 1)
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _read_line(conn: socket.socket) -> str:
        buf = bytearray()
        while len(buf) < MAX_LINE:
            try:
                chunk = conn.recv(1)
            except socket.timeout:
                return ""
            except OSError:
                return ""
            if not chunk:
                return ""
            if chunk == b"\n":
                break
            if chunk != b"\r":
                buf += chunk
        return buf.decode("utf-8", "replace").strip()

    @staticmethod
    def _send(conn: socket.socket, payload: dict) -> None:
        try:
            conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass
