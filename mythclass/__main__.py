"""Mythclass 客户端主程序

python -m mythclass              正常启动（托盘 + 后台）
python -m mythclass --guard <pid> 守护模式，盯住主进程
python -m mythclass --console    前台跑，日志直接打屏上（调试用）
python -m mythclass --status     打印状态就退出
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time

from . import __product__, __version__, config, guard, identity
from .api import ServerApi, SocketClient
from .commands import execute, known_commands
from .db import RecordStore
from .monitors import AudioMonitor, FileMonitor, ScreenStreamer
from . import netban
from .tray import Tray, make_icon_image

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class MythclassClient:
    def __init__(self, console: bool = False):
        self.cfg = config.load()
        self.client_uid = identity.resolve_uid(self.cfg)
        self.store = RecordStore()
        self.console = console

        self.server_url = ""
        self.api: ServerApi | None = None
        self.socket: SocketClient | None = None
        self.connected = False

        self.file_monitor: FileMonitor | None = None
        self.audio_monitor: AudioMonitor | None = None
        self.screen = ScreenStreamer(self.cfg.get("screenFps", 12), self.cfg.get("screenQuality", 60))

        self.tray = Tray(self)
        self._stop = threading.Event()
        self._last_netban_refresh = 0.0
        self.p2p = None            # 惰性创建：老师要直连才起 asyncio 线程
        self._last_thumb = 0.0     # 上次抓缩略图的时间，用来限流
        self.logger = logging.getLogger("mythclass")

    # ------------------------------ 日志 ------------------------------

    def log(self, message: str, level: int = logging.INFO) -> None:
        self.logger.log(level, message)
        if self.console:
            print(f"[Mythclass] {message}", flush=True)

    # ------------------------------ 连接 ------------------------------

    def _connect_loop(self) -> None:
        """按官方 → 自定义的顺序挨个试，全挂了就等一会儿再来"""
        backoff = 5
        while not self._stop.is_set():
            for url in config.enabled_servers(self.cfg):
                if self._stop.is_set():
                    return
                try:
                    api = ServerApi(url)
                    info = api.register(self.client_uid, self.cfg.get("clientName") or "教室一体机")
                    if abs(api.clock_skew) > 120:
                        self.log(
                            f"本机时间与服务端差了 {int(api.clock_skew)} 秒，"
                            "时间差太大会导致凭证校验失败，建议把系统时间同步一下。",
                            logging.WARNING,
                        )
                    self.api = api
                    self.server_url = url
                    self.log(f"已经连上 {url}（机器号 {self.client_uid}）")

                    # 服务端那边的保留策略，以它为准
                    settings = (info.get("server") or {})
                    if settings.get("relayEnabled") is False:
                        self.log("服务端关了中继，只能靠 P2P。")

                    self.socket = SocketClient(
                        url,
                        api.token,
                        self._on_event,
                        self._on_state,
                        self._on_ready,
                        on_log=lambda m: self.log(f"连接层：{m}", logging.WARNING),
                    )
                    self.socket.start()
                    backoff = 5
                    self._wait_until_disconnected()
                    break
                except Exception as err:
                    self.log(f"{url} 连不上：{err}", logging.WARNING)

            if self._stop.is_set():
                return
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 60)

    def _wait_until_disconnected(self) -> None:
        """连上之后就在这儿盯着，直到连接断开"""
        while not self._stop.is_set():
            if self.socket and not self.socket.connected:
                # 给了它几次重连的机会
                time.sleep(3)
                if self.socket and not self.socket.connected:
                    self.log("连接断了，重新找服务器。", logging.WARNING)
                    return
            time.sleep(1)

    def _on_state(self, connected: bool) -> None:
        self.connected = connected
        if connected:
            self.tray.notify("已经连上服务端。")

    def _on_ready(self) -> None:
        self.socket.emit(
            "hello",
            {"clientUid": self.client_uid, "name": self.cfg.get("clientName"), "version": __version__},
        )

    # ---------------------------- 服务端事件 ----------------------------

    def _on_event(self, event: str, payload: dict) -> None:
        if event == "command":
            threading.Thread(target=self._handle_command, args=(payload,), daemon=True).start()
        elif event == "control_event":
            self._handle_control(payload)
        elif event == "offer":
            # 老师想直连：建 WebRTC 数据通道，成了画面就不经服务端了
            self._ensure_p2p().handle_offer(payload.get("sdp") or {})
        elif event == "settings:update":
            self._apply_settings(payload.get("settings") or {})
        elif event in ("heartbeat:ack", "registered", "client:presence"):
            pass
        else:
            self.log(f"收到不认识的事件：{event}", logging.DEBUG)

    def _handle_command(self, payload: dict) -> None:
        command = str(payload.get("command") or "")
        args = payload.get("args") or {}
        request_id = payload.get("requestId") or ""

        if command == "screen_start":
            if not self.screen.alive():
                self.screen = ScreenStreamer(
                    args.get("fps", self.cfg.get("screenFps", 12)),
                    args.get("quality", self.cfg.get("screenQuality", 60)),
                )
                self.screen.start(self._on_screen_frame)
            ok, output = True, "屏幕流开着了"
        elif command == "screen_stop":
            self.screen.stop()
            ok, output = True, "屏幕流关了"
        elif command == "request_frame":
            threading.Thread(target=self._send_thumb, args=(args,), daemon=True).start()
            ok, output = True, "给你抓了张小图"
        else:
            ok, output = execute(command, args)

        self.log(f"命令 {command}：{'成功' if ok else '失败'} - {output}")
        if request_id:
            threading.Thread(
                target=lambda: self.api and self.api.command_result(request_id, command, ok, output), daemon=True
            ).start()
        if self.socket:
            self.socket.emit("command_result", {"requestId": request_id, "command": command, "ok": ok, "output": output})

    # ------------------------------ P2P 直连 ------------------------------

    def _ensure_p2p(self):
        """第一次要用才建，省得平时白养一个 asyncio 线程"""
        if self.p2p is None:
            from .p2p import P2PSession

            self.p2p = P2PSession(self._send_signal, self._handle_control, self.log)
        return self.p2p

    def _send_thumb(self, args: dict) -> None:
        """给老师一张小图当缩略图。限流：1.5 秒内只认一次。"""
        now = time.time()
        if now - self._last_thumb < 1.5:
            return
        self._last_thumb = now

        try:
            from .monitors import grab_thumbnail

            data = grab_thumbnail(int(args.get("width") or 320), int(args.get("quality") or 40))
        except Exception as err:
            self.log(f"抓缩略图失败：{err}")
            return

        if data and self.socket:
            self.socket.emit("screen_frame", {"data": data, "thumb": True, "ts": int(now * 1000)})

    def _send_signal(self, event: str, payload: dict) -> None:
        """把 answer 这类信令交给服务端转发"""
        if self.socket:
            self.socket.emit(event, {**payload, "clientUid": self.client_uid})

    def _on_screen_frame(self, frame: dict) -> None:
        """有直连就走直连，没建起来还走服务端中继"""
        if self.p2p is not None and self.p2p.send_frame(frame):
            return
        if self.socket:
            self.socket.emit("screen_frame", frame)

    def _handle_control(self, payload: dict) -> None:
        """老师的鼠标键盘，落到这台机器上"""
        event = payload.get("event") or payload
        kind = event.get("type")
        try:
            import pyautogui  # type: ignore

            pyautogui.FAILSAFE = False
            screen_w, screen_h = pyautogui.size()
            if kind == "mousemove":
                pyautogui.moveTo(int(event.get("x", 0) * screen_w), int(event.get("y", 0) * screen_h))
            elif kind == "mousedown":
                pyautogui.mouseDown(button=event.get("button", "left"))
            elif kind == "mouseup":
                pyautogui.mouseUp(button=event.get("button", "left"))
            elif kind == "key":
                pyautogui.press(str(event.get("key", ""))[:20])
        except ImportError:
            self._control_fallback(event, kind)
        except Exception as err:
            self.log(f"控制事件没执行：{err}", logging.WARNING)

    @staticmethod
    def _control_fallback(event: dict, kind: str) -> None:
        """没装 pyautogui 就用 user32 顶上"""
        if sys.platform != "win32":
            return
        user32 = ctypes.windll.user32
        width = user32.GetSystemMetrics(0)
        height = user32.GetSystemMetrics(1)
        if kind == "mousemove":
            user32.SetCursorPos(int(event.get("x", 0) * width), int(event.get("y", 0) * height))
        elif kind == "mousedown":
            user32.mouse_event(0x0002, 0, 0, 0, 0)
        elif kind == "mouseup":
            user32.mouse_event(0x0004, 0, 0, 0, 0)

    def _apply_settings(self, settings: dict) -> None:
        if "maxLogCount" in settings:
            self.cfg["maxLogCount"] = int(settings["maxLogCount"])
        if "maxLogSize" in settings:
            self.cfg["maxLogSize"] = int(settings["maxLogSize"])
        config.save(self.cfg)
        self.log("记录保留策略被老师改了，已经落地。")

    # ------------------------------ 后台活 ------------------------------

    def _upload_loop(self) -> None:
        """攒着的记录往服务端送，顺手心跳"""
        while not self._stop.is_set():
            try:
                if self.api and self.connected:
                    logs = self.store.pending_file_logs()
                    if logs and self.api.upload_file_logs(logs):
                        self.store.mark_uploaded("file_logs", [item["id"] for item in logs])

                    audio = self.store.pending_audio_logs()
                    if audio and self.api.upload_audio(audio):
                        self.store.mark_uploaded("audio_logs", [item["id"] for item in audio])

                    self.api.heartbeat()
            except Exception as err:
                self.log(f"上报出问题了：{err}", logging.WARNING)

            # 禁网状态下要盯两件事：到点自动放开；服务端 IP 变了刷新放行
            try:
                if netban.due_for_auto_lift():
                    ok, msg = netban.lift()
                    self.log(f"禁网到点自动放开：{'成功' if ok else '失败'} - {msg}")
                elif netban.is_active() and time.time() - self._last_netban_refresh > 300:
                    self._last_netban_refresh = time.time()
                    ok, msg = netban.refresh_control_ips(config.enabled_servers(self.cfg))
                    if not ok:
                        self.log(f"刷新禁网放行地址失败：{msg}", logging.WARNING)
            except Exception as err:
                self.log(f"禁网状态检查出错：{err}", logging.WARNING)

            self._stop.wait(15)

    def _housekeeping_loop(self) -> None:
        """每 5 分钟清一次旧记录"""
        while not self._stop.is_set():
            try:
                removed = self.store.trim(int(self.cfg.get("maxLogCount", 5000)), int(self.cfg.get("maxLogSize", 209715200)))
                if removed:
                    self.log(f"清掉了 {removed} 条旧记录")
            except Exception as err:
                self.log(f"清理失败：{err}", logging.WARNING)
            self._stop.wait(300)

    def on_settings_saved(self, cfg: dict) -> None:
        self.cfg = cfg
        self.log("设置更新了，部分项目下次启动才生效。")

    # ------------------------------ 生命周期 ------------------------------

    def start(self) -> None:
        self.log(f"{__product__} v{__version__} 启动，机器号 {self.client_uid}")

        if not self.cfg.get("complianceAccepted"):
            self.log("合规使用承诺还没勾，去托盘「设置」里勾上再跑。", logging.WARNING)

        # 监控起来
        self.file_monitor = FileMonitor(self.store, dirs=config.watch_dirs(self.cfg))
        self.file_monitor.start()
        self.audio_monitor = AudioMonitor(self.store, on_info=lambda items: self.socket and self.socket.emit("audio_info", {"items": items}))
        self.audio_monitor.start()

        # 保护起来
        if self.cfg.get("protectProcess"):
            guard.clear_stop()        # 这次是正经启动，把「别盯我」的标记清掉
            guard.start_guardian()
        if self.cfg.get("autostart"):
            guard.enable_autostart()

        # 任务管理器策略以配置文件为准。默认是关的，只有人勾过才禁
        if self.cfg.get("disableTaskManager"):
            guard.set_task_manager_disabled(True)
        elif guard.task_manager_disabled():
            # 以前开过，现在配置里关掉了，顺手放开
            guard.set_task_manager_disabled(False)

        threading.Thread(target=self._connect_loop, name="mythclass-connect", daemon=True).start()
        threading.Thread(target=self._upload_loop, name="mythclass-upload", daemon=True).start()
        threading.Thread(target=self._housekeeping_loop, name="mythclass-housekeeping", daemon=True).start()

        self.tray.start()

    def wait_forever(self) -> None:
        while not self._stop.is_set():
            time.sleep(2)

    def shutdown(self) -> None:
        self.log("收工中…")
        # 先让跟班别盯了，否则它几秒后就把我们拉回来
        if self.cfg.get("protectProcess"):
            guard.request_stop()
        self._stop.set()
        self.screen.stop()
        if self.p2p is not None:
            self.p2p.close()
        if self.socket:
            self.socket.stop()
        if self.file_monitor:
            self.file_monitor.stop()
        if self.audio_monitor:
            self.audio_monitor.stop()
        self.store.close()
        self.tray.stop()
        time.sleep(0.5)
        sys.exit(0)

    @staticmethod
    def tls_check() -> str:
        """自检：WebSocket 用哪份证书库

        系统证书库不全时，心跳能通但 WebSocket 会报
        CERTIFICATE_VERIFY_FAILED —— 这个自检就是为了不再瞎猜。
        """
        try:
            from .api import ca_bundle

            ca = ca_bundle()
            if not ca:
                return "用的是系统证书库（certifi 没找到）"
            # 不用 os.path：这个文件里没导入 os，打包后才发现会 NameError
            name = ca.replace("\\", "/").split("/")[-1]
            return f"ok（{name}，和心跳同一份）"
        except Exception as err:
            return f"检查不了：{type(err).__name__}: {err}"

    @staticmethod
    def p2p_check() -> str:
        """自检：P2P 依赖在打包后能不能加载。

        aiortc 是运行时才 import 的，等老师点「开始看」才发现缺件就太晚了。
        """
        try:
            import aiortc

            return f"ok（aiortc {aiortc.__version__}）"
        except Exception as err:
            return f"加载不了：{type(err).__name__}: {err}"

    @staticmethod
    def icon_check() -> str:
        """自检：logo 到底读不读得到。

        打包漏带资源时，托盘会在真启动那一刻才炸，查起来很烦；
        所以 --status 顺手把它验一遍。
        """
        try:
            icon = make_icon_image(64)
            if not icon:
                return "没读到（缺 Pillow）"
            return f"ok {icon.size[0]}x{icon.size[1]}"
        except Exception as err:
            return f"出错：{type(err).__name__}: {err}"

    def status(self) -> str:
        return (
            f"{__product__} v{__version__}\n"
            f"机器 ID：{self.client_uid}\n"
            f"服务器：{self.server_url or '（还没连上）'}\n"
            f"状态：{'已连接' if self.connected else '未连接'}\n"
            f"屏幕流：{'推着呢' if self.screen.alive() else '关着'}\n"
            f"托盘图标：{self.icon_check()}\n"
            f"证书库：{self.tls_check()}\n"
            f"连接层：{self.socket.last_error if self.socket and self.socket.last_error else '没有报错'}\n"
            f"P2P 依赖：{self.p2p_check()}\n"
            f"P2P 直连：{self.p2p.status() if self.p2p else '还没用过'}\n"
            f"禁网：{netban.describe()}\n"
            f"进程保护：{'开' if self.cfg.get('protectProcess') else '关'}"
            f"（跟班{'在跑' if guard.guardian_running() else '没跑'}）\n"
            f"记录：{self.store.stats()}\n"
            f"能用命令：{', '.join(known_commands())}"
        )


def _single_instance() -> bool:
    """同一台机器只跑一个，两个一起推流会打架"""
    if sys.platform != "win32":
        return True
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\MythclassClientMutex")
    already = ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    return not already


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])

    # 目录必须先建：%APPDATA%\Mythclass 不在的话，
    # logging 写文件会直接抛 FileNotFoundError（打包成 exe 后尤其明显）
    config.ensure_dirs()

    try:
        logging.basicConfig(
            level=logging.INFO,
            format=LOG_FORMAT,
            filename=str(config.LOG_FILE) if "--console" not in argv else None,
            # 不指定的话跟系统编码走（中文 Windows 是 GBK），
            # 日志发出去别人打开全是乱码
            encoding="utf-8",
        )
    except OSError:
        # 日志文件写不进去也不能让程序死在启动线上，退回只打控制台
        logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)

    if "--guard" in argv:
        index = argv.index("--guard")
        pid = int(argv[index + 1]) if len(argv) > index + 1 else 0
        guard.run_guardian(pid)
        return 0

    if "--status" in argv:
        client = MythclassClient(console=True)
        print(client.status())
        return 0

    if not _single_instance():
        print("[Mythclass] 已经有一个在跑了，这个先退。")
        return 0

    client = MythclassClient(console="--console" in argv)
    try:
        client.start()
    except KeyboardInterrupt:
        client.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
