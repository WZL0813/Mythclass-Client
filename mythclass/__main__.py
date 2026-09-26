"""Mythclass 客户端主程序

python -m mythclass              正常启动（托盘 + 后台）
python -m mythclass --guard <pid> 守护模式，盯住主进程
python -m mythclass --console    前台跑，日志直接打屏上（调试用）
python -m mythclass --status     打印状态就退出
python -m mythclass --set-password 新密码   忘了管理密码时改一下
python -m mythclass --apply-update  以管理员身份执行待安装的更新（内部用）
"""

from __future__ import annotations

from pathlib import Path
import ctypes
import logging
import sys
import threading
import time

from . import __product__, __version__, config, guard, identity
from . import updater as updater_mod
from .api import ServerApi, SocketClient
from .commands import execute, known_commands
from .db import RecordStore
from .monitors import AudioMonitor, FileMonitor, ScreenStreamer
from . import usage
from . import quiet, windows
from .usage import UsageMonitor
from .commands import cmd_list_dir
from . import bindings, crash, lanport, lanweb, netban, trust
from .tray import Tray, make_icon_image

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class MythclassClient:
    def __init__(self, console: bool = False):
        self.cfg = config.load()
        # 机器号必须是「定下来就永远不变」的：虚拟机上 MAC 会变，
        # 每次重算就会被当成一台新机器，老师那边绑的还是旧的。
        # ensure_client_uid 第一次算出来就写进 config.json。
        self.client_uid = config.ensure_client_uid(self.cfg)
        self.store = RecordStore()
        self.console = console

        self.server_url = ""
        self.api: ServerApi | None = None
        self.socket: SocketClient | None = None
        self.connected = False
        # 「已连接」气泡：只在断开后重连上时弹一次，别一直烦人
        self._notified_connected = False
        self._ever_connected = False
        self._last_connect_notify = 0.0
        # 本地网页抓帧失败的原因（显示在页面上，省得黑屏查不出问题）
        self.lan_frame_error = ""

        self.file_monitor: FileMonitor | None = None
        self.audio_monitor: AudioMonitor | None = None
        self.usage_monitor: UsageMonitor | None = None
        self.last_audio: list = []
        self.screen = ScreenStreamer(self.cfg.get("screenFps", 12), self.cfg.get("screenQuality", 60))

        self.tray = Tray(self)
        self._stop = threading.Event()
        self._last_netban_refresh = 0.0
        self.p2p = None            # 惰性创建：老师要直连才起 asyncio 线程
        self._last_thumb = 0.0     # 上次抓缩略图的时间，用来限流
        # 局域网直连：老师端在同一个网段时可以直接连这个端口，不走服务器
        # 本地网页：老师在同一局域网时直接打开浏览器就能控制，不经服务器
        self.web = lanweb.LanWeb(
            trust=trust,
            on_command=self._run_command_sync,
            on_info=self._lan_info,
            on_frame=self._lan_frame,
            log=lambda m: self.log(m),
            port=int(self.cfg.get("lanWebPort") or lanweb.DEFAULT_PORT),
        )
        self.web.frame_error = lambda: self.lan_frame_error
        # 局域网页面要看的：文件修改记录、设置（只读）
        self.web.file_logs = lambda limit=200: self.store.recent_file_logs(limit)
        self.web.screenshot = self._lan_screenshot

        # 局域网页面的其它数据源
        self.web.audio_items = lambda: self.last_audio
        self.web.usage_items = lambda: self.store.usage_summary(60)
        self.web.window_items = windows.list_windows
        self.web.quiet_state = quiet.status

        def _close_window(hwnd: int):
            return windows.close_window(hwnd)

        def _dir_listing(where: str):
            import json as _json

            good, out = cmd_list_dir({"path": where})
            if not good:
                return None
            try:
                return _json.loads(out)
            except Exception:
                return None

        def _read_file(where: str):
            """局域网下载用。失败一定要说清原因 —— 返回个 None 谁也查不出来。"""
            p = Path(str(where or "")).expanduser()
            try:
                if not p.exists():
                    self.log(f"局域网下载：路径不存在 → {p}", logging.WARNING)
                    return None
                if p.is_dir():
                    self.log(f"局域网下载：这是个文件夹 → {p}", logging.WARNING)
                    return None
                if not p.is_file():
                    self.log(f"局域网下载：不是普通文件 → {p}", logging.WARNING)
                    return None
                size = p.stat().st_size
                if size > 64 * 1024 * 1024:
                    self.log(f"局域网下载：文件太大（{size // 1048576}MB，上限 64MB）→ {p}", logging.WARNING)
                    return None
                return (p.name, p.read_bytes())
            except Exception as err:
                self.log(f"局域网下载失败：{p} {type(err).__name__}: {err}", logging.WARNING)
                return None

        def _set_quiet(on: bool, text: str = ""):
            if on:
                quiet.show(text, on_report=self._report_hand)
            else:
                quiet.hide()
            return quiet.status()

        def _set_hand(on: bool):
            quiet.raise_hand(on, on_report=self._report_hand)
            return quiet.status()

        self.web.close_window = _close_window
        self.web.force_close_window = windows.force_close_window
        self.web.dir_listing = _dir_listing
        self.web.read_file = _read_file
        self.web.set_quiet = _set_quiet
        self.web.set_hand = _set_hand

        # 铃声：列出来、存下来、试听
        def _sound_list():
            from . import sounds as sounds_mod

            return sounds_mod.list_saved()

        def _save_sound(name: str, blob: bytes):
            import os.path as _osp

            from . import sounds as sounds_mod

            if not blob or len(blob) > 8 * 1024 * 1024:
                return {"ok": False, "message": "文件不对或者太大（上限 8MB）"}
            # 只取文件名，别想用 ../ 跑出去
            safe = _osp.basename(str(name or "notice.wav")).replace("\\", "") or "notice.wav"
            if not safe.lower().endswith(sounds_mod.KNOWN_EXT):
                safe += ".wav"
            try:
                dest = sounds_mod.sounds_dir() / safe
                dest.write_bytes(blob)
                return {"ok": True, "message": f"传好了：{safe}", "path": str(dest)}
            except Exception as err:
                return {"ok": False, "message": f"存不下：{err}"}

        def _play_sound(spec: str):
            from . import sounds as sounds_mod

            return sounds_mod.play(spec)

        self.web.sound_list = _sound_list
        self.web.save_sound = _save_sound
        self.web.play_sound = _play_sound
        self.web.settings_view = lambda: {
            "name": self.cfg.get("clientName") or "教室一体机",
            "clientUid": self.client_uid,
            "version": __version__,
            "servers": [s.get("url") for s in (self.cfg.get("servers") or []) if isinstance(s, dict)],
            "watchDirs": list(self.cfg.get("watchDirs") or []),
            "screenFps": self.cfg.get("screenFps", 12),
            "screenQuality": self.cfg.get("screenQuality", 60),
            "autoUpdate": bool(self.cfg.get("autoUpdate", True)),
            "localIps": identity.local_ips(),
            "lanPort": int(getattr(self.lan, "port", 0) or 0),
            "lanWebPort": int(getattr(self.web, "port", 0) or 0),
            "fields": self._settings_fields(),
        }
        self.web.apply_lan_settings = self._apply_lan_settings
        self.lan = lanport.LanPort(
            trust=trust,
            on_command=self._run_command_sync,
            log=lambda m: self.log(m),
            port=int(self.cfg.get("lanPort") or lanport.DEFAULT_PORT),
        )
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
                    info = api.register(
                        self.client_uid,
                        self.cfg.get("clientName") or "教室一体机",
                        identity.local_ips(),
                        trust.own_key(),
                        # 注册可能早于 lan/web 建好 —— 那就先报默认端口，
                        # 心跳会带上真实端口（被系统占用时换过的那个）
                        lan_port=self._used_lan_port(),
                        lan_web_port=self._used_web_port(),
                    )
                    self.refresh_bindings()
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
        was = self.connected
        self.connected = connected
        if not connected:
            self._notified_connected = False
            return

        # 本来就在线：重复回调，不弹
        if was:
            return
        # 刚启动那次不弹（用户自己刚开的客户端，用不着告诉）
        # 只有真弹了才记时间 —— 否则静默的首次连接会把冷却占掉，
        # 紧接着的重连反而弹不出来
        if self._ever_connected:
            now = time.time()
            if now - self._last_connect_notify < 300:
                return  # 5 分钟内弹过，网络抖动别刷屏
            self.tray.notify("已经重新连上服务端。")
            self._last_connect_notify = now
        self._ever_connected = True

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
            # 老师想直连：先记一次「这个 IP 连过来了」，再建数据通道。
            # 记满 3 次就把这个 IP 记成信任、发一张配对密钥，
            # 以后没有服务器也能拿密钥直接控制（走局域网端口）。
            self._note_teacher(payload.get("from") or {})
            self._ensure_p2p().handle_offer(payload.get("sdp") or {})
        elif event == "settings:update":
            self._apply_settings(payload.get("settings") or {})
        elif event in ("heartbeat:ack", "registered", "client:presence"):
            pass
        else:
            self.log(f"收到不认识的事件：{event}", logging.DEBUG)

    # ------------------------------ 局域网直连 ------------------------------

    # ------------------------------ 本地网页 ------------------------------

    def refresh_bindings(self) -> None:
        """拉一次「这台机器绑定了哪些老师」，存起来给本地网页显示"""
        try:
            data = self.api.teachers()
        except Exception as err:
            self.log(f"拉绑定老师失败：{err}", logging.WARNING)
            return
        if not data:
            return
        bindings.save(data)
        names = bindings.names()
        if names:
            self.log(f"本机绑定的老师：{'、'.join(names)}")

    def _lan_info(self) -> dict:
        """本地网页要的信息。绑定列表旧了就顺手刷新一次"""
        # 刷新绑定老师要发网络请求，服务器不通会一直等到超时 ——
        # 本地网页的意义就是「服务器不通也能用」，绝不能在这里同步等。
        # 所以：先把缓存里的数据返回，刷新丢到后台线程。
        api = getattr(self, "api", None)
        if bindings.is_stale() and api is not None and getattr(api, "token", None):
            threading.Thread(target=self.refresh_bindings, daemon=True).start()
        data = bindings.load()
        return {
            "name": self.cfg.get("clientName") or "教室一体机",
            "clientUid": self.client_uid,
            "version": __version__,
            "teachers": data.get("teachers") or [],
            "localIps": identity.local_ips(),
            "screenAlive": self.screen.alive(),
            # 锁屏时抓不到画面，页面上要说清楚（不然老师以为坏了）
            "locked": self.session_locked(),
            "unlockHint": "锁屏时 Windows 不给抓屏，点上面的「解锁」再试",
        }

    def _lan_screenshot(self) -> bytes | None:
        """全分辨率 PNG（局域网页面点截图用，尽量清晰）"""
        if self.session_locked():
            self.lan_frame_error = "这台机器锁屏了，锁屏时 Windows 不给抓屏"
            return None
        try:
            import io

            import mss
            from PIL import Image

            with mss.mss() as grabber:
                monitor = grabber.monitors[1] if len(grabber.monitors) > 1 else grabber.monitors[0]
                shot = grabber.grab(monitor)
                image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", optimize=False)
            self.lan_frame_error = ""
            return buffer.getvalue()
        except Exception as err:
            self.lan_frame_error = f"{type(err).__name__}: {err}"
            self.log(f"局域网截图失败：{self.lan_frame_error}", logging.WARNING)
            return None

    @staticmethod
    def session_locked() -> bool:
        """这台机器现在是不是锁屏状态。

        锁屏时 Windows 会把输入桌面切走，抓屏会失败（或者抓到全黑）。
        判断办法：试着打开输入桌面，打不开就是锁了。
        """
        try:
            import ctypes

            user32 = ctypes.windll.user32
            # 0x0100 = DESKTOP_READOBJECTS
            handle = user32.OpenInputDesktop(0, False, 0x0100)
            if handle:
                user32.CloseDesktop(handle)
                return False
            return True
        except Exception:
            return False

    def _used_lan_port(self) -> int:
        """实际在用的直连端口（没起来就报默认值）"""
        try:
            from . import lanport as _lp

            return int(getattr(self.lan, "port", 0) or 0) or int(_lp.DEFAULT_PORT)
        except Exception:
            return 0

    def _used_web_port(self) -> int:
        """实际在用的本地网页端口"""
        try:
            from . import lanweb as _lw

            return int(getattr(self.web, "port", 0) or 0) or int(_lw.DEFAULT_PORT)
        except Exception:
            return 0

    # ------------------------------ 局域网改设置 ------------------------------

    # 能被局域网页面一键改的设置。密钥验证在 _apply_lan_settings 里做。
    EDITABLE = [
        {"key": "clientName", "label": "机器名", "type": "text"},
        {"key": "screenFps", "label": "屏幕帧率", "type": "number", "min": 1, "max": 30},
        {"key": "screenQuality", "label": "画面质量", "type": "number", "min": 20, "max": 95},
        {"key": "netBanAutoLiftMinutes", "label": "禁止上网几分钟后自动放开（0 = 不自动）",
         "type": "number", "min": 0, "max": 1440},
        {"key": "autoUpdate", "label": "自动更新", "type": "bool"},
        {"key": "updateNotify", "label": "更新时弹提示", "type": "bool"},
        {"key": "noticeSpeak", "label": "发通知时默认语音播报", "type": "bool"},
        {"key": "noticeVolume", "label": "播报音量（0-100）", "type": "number", "min": 0, "max": 100},
        {"key": "noticeVoice", "label": "播报音色（留空用默认中文音色）", "type": "text"},
        {"key": "noticeVoiceParts", "label": "播报念哪些（title=标题，content=内容，一行一个）",
         "type": "list"},
        {"key": "noticeVoiceOrder", "label": "播报顺序（一行一个，先念的在前）", "type": "list"},
        {"key": "autostart", "label": "开机自启", "type": "bool", "restart": True},
        {"key": "protectProcess", "label": "进程保护", "type": "bool", "restart": True},
        {"key": "disableTaskManager", "label": "禁用任务管理器", "type": "bool", "restart": True},
        {"key": "maxLogCount", "label": "最多保留记录条数", "type": "number", "min": 100, "max": 100000},
        {"key": "maxLogSizeMB", "label": "记录占用上限（MB）", "type": "number", "min": 10, "max": 2048},
        {"key": "teacherIp", "label": "常用老师 IP", "type": "text"},
        {"key": "watchDirs", "label": "监控哪些文件夹（一行一个）", "type": "list", "restart": True},
        {"key": "servers", "label": "服务器地址（一行一个，官方那条会保留）", "type": "list", "restart": True},
        {"key": "newAdminPassword", "label": "客户端管理密码（留空表示不改）", "type": "secret"},
    ]

    def _settings_fields(self) -> list[dict]:
        """清单 + 当前值，页面照着渲染"""
        out = []
        for item in self.EDITABLE:
            row = dict(item)
            key = item["key"]
            if key == "maxLogSizeMB":
                row["value"] = int((self.cfg.get("maxLogSize") or 0) // (1024 * 1024)) or 200
            elif key == "servers":
                row["value"] = [s.get("url") for s in (self.cfg.get("servers") or []) if isinstance(s, dict)]
            elif key == "newAdminPassword":
                row["value"] = ""
            else:
                row["value"] = self.cfg.get(key)
            out.append(row)
        return out

    def _apply_lan_settings(self, key: str, values: dict):
        """局域网一键改设置。必须带对本机密钥 —— 改设置是敏感操作。"""
        import hmac

        try:
            good = trust.own_key()
        except Exception as err:
            return {"ok": False, "code": "NO_KEY", "error": f"本机密钥取不到：{err}"}
        if not good or not hmac.compare_digest(str(key or ""), str(good)):
            self.log("局域网改设置被拒：密钥不对", logging.WARNING)
            return {"ok": False, "code": "BAD_KEY", "error": "密钥不对，改不了"}

        values = values if isinstance(values, dict) else {}
        schema = {f["key"]: f for f in self.EDITABLE}
        applied: list[str] = []
        need_restart: list[str] = []
        problems: list[str] = []
        changed_autostart = None

        for name, raw in values.items():
            field = schema.get(name)
            if not field:
                problems.append(f"{name}：不认识这一项")
                continue
            label = field["label"]
            kind = field["type"]
            hit = False
            try:
                if kind == "bool":
                    val = bool(raw)
                    if self.cfg.get(name) != val:
                        self.cfg[name] = val
                        applied.append(f"{label} = {'开' if val else '关'}")
                        hit = True
                        if name == "autostart":
                            changed_autostart = val
                elif kind == "number":
                    val = int(float(raw))
                    lo, hi = field.get("min"), field.get("max")
                    if lo is not None and val < lo:
                        problems.append(f"{label}：不能小于 {lo}")
                        continue
                    if hi is not None and val > hi:
                        problems.append(f"{label}：不能大于 {hi}")
                        continue
                    if name == "maxLogSizeMB":
                        self.cfg["maxLogSize"] = val * 1024 * 1024
                    else:
                        self.cfg[name] = val
                    applied.append(f"{label} = {val}")
                    hit = True
                elif kind == "list":
                    if isinstance(raw, str):
                        items = [x.strip() for x in raw.replace(chr(13), "").split(chr(10))]
                    else:
                        items = [str(x).strip() for x in (raw or [])]
                    items = [x for x in items if x]
                    if name == "servers":
                        keep = [s for s in (self.cfg.get("servers") or [])
                                if isinstance(s, dict) and s.get("official")]
                        new_list = list(keep)
                        for url in items:
                            if any(s.get("url") == url for s in new_list):
                                continue
                            new_list.append({"name": url, "url": url, "enabled": True, "official": False})
                        self.cfg["servers"] = new_list
                        applied.append(f"服务器 = {len(new_list)} 条")
                    else:
                        self.cfg[name] = items
                        applied.append(f"{label} = {len(items)} 个")
                    hit = True
                elif kind == "secret":
                    text = str(raw or "")
                    if not text:
                        continue
                    if len(text) < 6:
                        problems.append("管理密码：至少 6 位")
                        continue
                    from . import security

                    self.cfg["adminPasswordHash"] = security.hash_password(text)
                    self.cfg["requirePasswordChange"] = False
                    applied.append("客户端管理密码：已改")
                    hit = True
                else:
                    text = str(raw or "").strip()
                    if self.cfg.get(name) != text:
                        self.cfg[name] = text
                        applied.append(f"{label} = {text or '（空）'}")
                        hit = True
            except Exception as err:
                problems.append(f"{label}：{type(err).__name__}")
                continue

            if hit and field.get("restart"):
                need_restart.append(label)

        if applied:
            config.save(self.cfg)

        # 自启要真的去登记/取消
        if changed_autostart is not None:
            try:
                if changed_autostart:
                    guard.enable_autostart()
                else:
                    guard.disable_autostart()
            except Exception as err:
                problems.append(f"自启设置：{err}")

        if applied:
            self.log("局域网改了设置：" + "；".join(applied))
        return {
            "ok": bool(applied) and not problems,
            "applied": applied,
            "needRestart": need_restart,
            "problems": problems,
        }

    def _report_hand(self, what: str) -> None:
        """学生在黑屏里举手 / 放下，报给老师（带状态，两端好显示）"""
        try:
            import json as _json

            from . import quiet as quiet_mod

            self.log(f"黑屏安静：{what}")
            state = quiet_mod.status()
            payload = _json.dumps(
                {"hand": state.get("hand"), "at": state.get("handAt"), "text": what},
                ensure_ascii=False,
            )
            if self.api:
                self.api.command_result("", "hand", True, payload)
        except Exception:
            pass

    def _on_audio_info(self, items: list) -> None:
        """音频会话（哪些程序在出声）—— 留一份给局域网页面看，同时照旧上报服务端"""
        self.last_audio = list(items or [])
        if self.socket:
            self.socket.emit("audio_info", {"items": items})

    def _lan_frame(self) -> bytes | None:
        """本地网页要一帧画面。

        自己抓、自己记错误 —— 原来走 monitors.grab_thumbnail()，
        它把异常吞了只返回 None，页面就只能黑着，谁也查不出为什么。
        """
        if self.session_locked():
            self.lan_frame_error = "这台机器锁屏了，锁屏时 Windows 不给抓屏"
            return None

        try:
            import io

            import mss
            from PIL import Image

            with mss.mss() as grabber:
                monitor = grabber.monitors[1] if len(grabber.monitors) > 1 else grabber.monitors[0]
                shot = grabber.grab(monitor)
                image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                if image.width > 1280:
                    image = image.resize((1280, max(1, int(image.height * 1280 / image.width))))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=60)
                self.lan_frame_error = ""
                return buffer.getvalue()
        except Exception as err:
            # 常见原因：这台机器锁屏了（Windows 不给抓）、远程桌面会话、
            # 或者客户端跑在没有桌面的会话里
            self.lan_frame_error = f"{type(err).__name__}: {err}"
            self.log(f"本地网页抓画面失败：{self.lan_frame_error}", logging.WARNING)
            return None

    def _note_teacher(self, sender: dict) -> None:
        """老师连过来一次就记一笔。IP 由服务端带下来（隧道后面只有它看得见）"""
        ip = str(sender.get("ip") or "").strip()
        if not ip:
            return
        try:
            info = trust.record(ip, str(sender.get("username") or ""))
        except Exception as err:
            self.log(f"记录教师端连接失败：{err}", logging.WARNING)
            return
        if info.get("just_trusted"):
            self.log(
                f"教师端 {ip} 直连已满 {trust.THRESHOLD} 次，已记成信任并生成配对密钥："
                f"{info.get('key')}"
            )

    def _run_command_sync(self, command: str, args: dict) -> tuple[bool, str]:
        """局域网端口来的命令：同步执行、直接返回结果。

        和 _handle_command 的区别：那边要发回执、要报给服务端；
        这边是点对点，谁问谁等结果。
        """
        command = str(command or "").strip()

        if command == "screen_start":
            if not self.screen.alive():
                self.screen = ScreenStreamer(
                    args.get("fps", self.cfg.get("screenFps", 12)),
                    args.get("quality", self.cfg.get("screenQuality", 60)),
                )
                self.screen.start(self._on_screen_frame)
            return True, "屏幕流开着了"
        if command == "screen_stop":
            self.screen.stop()
            return True, "屏幕流关了"
        if command == "status":
            return True, f"v{__version__}，屏幕流{'开着' if self.screen.alive() else '关着'}"

        if command == "message":
            # 局域网网页是一问一答，等它回答再返回
            from .commands import cmd_message

            return cmd_message(args or {}, wait=True)

        try:
            return execute(command, args or {})
        except Exception as err:
            return False, f"{type(err).__name__}: {err}"

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
        elif command == "message":
            # 通知：先回一条「已弹出」，学生点了再用 command_result
            # 补一条「回答：xxx」——教师端就收到回复了
            from .commands import cmd_message

            def _reply(answer: str, rid=request_id) -> None:
                try:
                    self.api.command_result(rid, "message", True, f"回答：{answer}")
                except Exception as err:
                    self.log(f"回传通知回答失败：{err}", logging.WARNING)

            ok, output = cmd_message(args, on_reply=_reply)
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

                    self.api.heartbeat(
            identity.local_ips(),
            trust.own_key(),
            lan_port=self._used_lan_port(),
            lan_web_port=self._used_web_port(),
        )
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

    def _auto_update_loop(self) -> None:
        """开机自动检查更新：默认开着，6 小时最多查一次。

        先等 25 秒，别跟启动抢时间；查到有新版本就下下来、提权装上、自己退出。
        """
        import time as _time

        _time.sleep(25)
        try:
            if not self.cfg.get("autoUpdate", True):
                return
            from datetime import datetime, timedelta

            last = str(self.cfg.get("lastUpdateCheck") or "")
            if last:
                try:
                    if datetime.fromisoformat(last) > datetime.now() - timedelta(hours=6):
                        return
                except Exception:
                    pass
            from . import ui

            # cfg 是个 dict，要存档得走 config.save
            self.cfg["lastUpdateCheck"] = datetime.now().isoformat(timespec="seconds")
            config.save(self.cfg)
            ui.run_update_check(self.cfg, silent=True, auto=True)
        except Exception as err:
            self.log(f"自动检查更新失败：{err}", logging.WARNING)

    def start(self) -> None:
        self.log(f"{__product__} v{__version__} 启动，机器号 {self.client_uid}")

        if not self.cfg.get("complianceAccepted"):
            self.log("合规使用承诺还没勾，去托盘「设置」里勾上再跑。", logging.WARNING)

        # 监控起来
        self.file_monitor = FileMonitor(self.store, dirs=config.watch_dirs(self.cfg))
        self.file_monitor.start()
        self.audio_monitor = AudioMonitor(self.store, on_info=self._on_audio_info)
        self.audio_monitor.start()

        # 软件使用时长：每 5 秒看一眼前台
        self.usage_monitor = UsageMonitor(self.store)
        usage.set_monitor(self.usage_monitor)
        self.usage_monitor.start()

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

        # 本机局域网密钥：启动就生成，别等注册成功 ——
        # 服务器不通时本地网页照样要能用
        try:
            self.log(f"本机局域网密钥：{trust.own_key()}")
        except Exception as err:
            self.log(f"生成局域网密钥失败：{err}", logging.WARNING)

        threading.Thread(target=self._connect_loop, name="mythclass-connect", daemon=True).start()
        threading.Thread(target=self._upload_loop, name="mythclass-upload", daemon=True).start()
        threading.Thread(target=self._housekeeping_loop, name="mythclass-housekeeping", daemon=True).start()

        # 顺序很讲究：
        #   1) 局域网端口与本地网页必须先起 —— tray.start() 会阻塞在
        #      pystray 的 icon.run() 里，排在它後面就永远执行不到
        #      （v2.0.6 到 v2.1.5 本地网页一直是「没开」，就是这个原因）
        #   2) 托盘放最后，它阻塞住正好当主循环，进程才不会起来就退出
        # 如果这次是管理员（安装程序启动的第一次就是），把"提权自启"建好，
        # 以后开机就是管理员身份 —— 改防火墙、关机这些才做得动
        try:
            made, why = guard.ensure_elevated_autostart()
            if made:
                self.log(f"提权自启：{why}")

            # 更新执行器：只要是管理员就建一个，以后自动更新不用弹 UAC
            from . import updater as _up  # noqa: E402

            if _up.is_admin():
                _ok, _why = _up.ensure_apply_task()
                self.log(f"更新执行器：{_why}")
        except Exception as err:
            self.log(f"建提权自启失败：{err}", logging.WARNING)

        # 刚更新完？报一声（受「更新时弹提示」控制）
        def _say_update_done() -> None:
            try:
                time.sleep(4)  # 等托盘起来，免得提示比窗口还早
                done = updater_mod.take_update_done()
                if not done:
                    return
                self.log(f"更新完成：已更新到 v{done}")
                if not self.cfg.get("updateNotify", True):
                    return
                import tkinter as _tk  # noqa: F401  （ui 里有现成的）

                from . import ui as _ui

                _ui.toast_note(f"更新完成：已经更新到 v{done}，一切正常。")
            except Exception as err:
                self.log(f"更新完成提示失败：{err}", logging.WARNING)

        threading.Thread(target=_say_update_done, daemon=True).start()

        # 自动检查更新：后台慢慢来，不挡启动
        threading.Thread(target=self._auto_update_loop, daemon=True).start()

        self.lan.start()
        # 直连端口要是被系统占着换了，网页那边别跟着挑到同一个
        self.web.avoid_port = int(getattr(self.lan, 'port', 0) or 0)
        # 端口都起来了，立刻补一次真实端口（注册那会儿它们还没建）
        def _report_ports() -> None:
            try:
                import time as _t

                _t.sleep(3)  # 等连接先握上手
                if self.api:
                    self.api.heartbeat(
                        identity.local_ips(),
                        trust.own_key(),
                        lan_port=self._used_lan_port(),
                        lan_web_port=self._used_web_port(),
                    )
                    self.log(
                        f"已上报局域网端口：{self._used_lan_port()}/{self._used_web_port()}"
                    )
            except Exception as err:
                self.log(f"补报端口失败：{err}", logging.WARNING)

        threading.Thread(target=_report_ports, daemon=True).start()
        # socket 心跳也报一次实际端口（教师端拼局域网地址要用）
        try:
            self.socket.ports_provider = lambda: {
                "lanPort": self._used_lan_port(),
                "lanWebPort": self._used_web_port(),
            }
        except Exception:
            pass
        self.web.start()
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
        self.lan.stop()
        self.web.stop()
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
            f"局域网端口：{self.lan.status()}\n"
            f"本地网页：{self.web.status()}\n"
            f"绑定老师：{bindings.describe()}\n"
            f"信任的教师端：{trust.describe()}\n"
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

    # 第一件事就装崩溃钩子：启动阶段的错也得能上报 + 在桌面留日志
    crash.install()

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

    # 忘了管理密码时：直接改掉（配置就在用户目录里，本来也是可写的）
    if "--set-password" in argv:
        index = argv.index("--set-password")
        new_password = argv[index + 1].strip() if len(argv) > index + 1 else ""
        cfg = config.load()
        if len(new_password) < 6:
            message = "密码至少 6 位。用法：--set-password 新密码"
        else:
            config.set_admin_password(cfg, new_password)
            config.save(cfg)
            message = "管理密码改好了。托盘「设置」里用它就能进。"
        try:
            print(message)
        except Exception:
            pass
        try:
            import tkinter as _tk
            from tkinter import messagebox as _mb

            _root = _tk.Tk()
            _root.withdraw()
            _root.attributes("-topmost", True)
            _mb.showinfo("Mythclass", message)
            _root.destroy()
        except Exception:
            pass
        return 0

    # 被最高权限任务拉起来装更新：核对服务端的 sha256，通过才装
    if "--apply-update" in argv:
        from . import updater as _up

        made, why = _up.apply_pending_update()
        try:
            with open(config.APP_DIR / "apply-update.log", "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {'成功' if made else '失败'}：{why}\n")
        except Exception:
            pass
        return 0 if made else 1

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
