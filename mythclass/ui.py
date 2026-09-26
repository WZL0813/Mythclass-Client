"""两个小窗口：关于、设置

设置要密码。默认 admin123，第一次进去会催你改掉；密码存哈希，不存明文。
"""

from __future__ import annotations

import os

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import __author__, __product__, __version__, config, guard, identity

REPO = "https://github.com/WZL0813/Mythclass-Client"

BG = "#f3efe3"
INK = "#10160f"
MOSS = "#2f4f3e"
AMBER = "#c97b3c"


def _center(window: tk.Tk, width: int, height: int) -> None:
    window.update_idletasks()
    x = (window.winfo_screenwidth() - width) // 2
    y = (window.winfo_screenheight() - height) // 3
    window.geometry(f"{width}x{height}+{x}+{y}")


def _apply_window_icon(window: tk.Tk) -> None:
    """给窗口装上项目图标。装不上就算了，不影响功能"""
    try:
        from .tray import asset_path

        icon_file = asset_path("logo-mark.png")
        if icon_file.exists():
            window._logo_icon = tk.PhotoImage(file=str(icon_file))   # 保住引用，不然会被回收
            window.iconphoto(True, window._logo_icon)
    except Exception:
        pass


# ================================ 关于 ================================


class AboutWindow:
    def __init__(self, cfg: dict, client_uid: str, on_settings):
        self.cfg = cfg
        self.client_uid = client_uid
        self.on_settings = on_settings
        self.root = tk.Tk()
        self.root.title("关于 Mythclass")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        _center(self.root, 460, 330)
        _apply_window_icon(self.root)
        self._build()

    def _build(self) -> None:
        pad = tk.Frame(self.root, bg=BG, padx=26, pady=22)
        pad.pack(fill="both", expand=True)

        # 顶上摆 logo
        try:
            from PIL import Image, ImageTk

            from .tray import asset_path

            logo_file = asset_path("logo.png")
            if logo_file.exists():
                photo = ImageTk.PhotoImage(Image.open(logo_file).resize((88, 88), Image.LANCZOS))
                holder = tk.Label(pad, image=photo, bg=BG)
                holder.image = photo      # 同上，引用得留着
                holder.pack(anchor="w", pady=(0, 8))
        except Exception:
            pass

        tk.Label(pad, text=__product__, bg=BG, fg=MOSS, font=("Microsoft YaHei", 14, "bold"), wraplength=400, justify="left").pack(anchor="w")
        tk.Label(pad, text=f"v{__version__} / by {__author__}", bg=BG, fg=INK, font=("Microsoft YaHei", 10)).pack(anchor="w", pady=(4, 14))

        info = tk.Frame(pad, bg=BG)
        info.pack(fill="x")
        rows = [
            ("机器 ID", self.client_uid),
            ("机器名", self.cfg.get("clientName") or "未命名"),
            ("当前服务器", self._current_server()),
        ]
        for i, (key, value) in enumerate(rows):
            tk.Label(info, text=key, bg=BG, fg=MOSS, font=("Microsoft YaHei", 10), width=10, anchor="w").grid(row=i, column=0, sticky="w", pady=2)
            tk.Label(info, text=value, bg=BG, fg=INK, font=("Consolas", 10), anchor="w", wraplength=280, justify="left").grid(row=i, column=1, sticky="w", pady=2)

        link = tk.Label(pad, text=REPO, bg=BG, fg=AMBER, font=("Consolas", 10), cursor="hand2")
        link.pack(anchor="w", pady=(14, 0))
        link.bind("<Button-1>", lambda _e: self._open_repo())

        btns = tk.Frame(pad, bg=BG)
        btns.pack(fill="x", pady=(18, 0))
        self._button(btns, "检查更新", lambda: run_update_check(self.cfg)).pack(side="left")
        self._button(btns, "复制仓库地址", self._copy_repo).pack(side="left", padx=8)
        self._button(btns, "设置", lambda: self._open_settings()).pack(side="left", padx=8)
        self._button(btns, "关闭", self.root.destroy).pack(side="right")

    def _button(self, parent, text, command):
        return tk.Button(
            parent, text=text, command=command, bg=MOSS, fg=BG, relief="flat",
            activebackground="#3f6b52", activeforeground=BG, padx=14, pady=5,
            font=("Microsoft YaHei", 10), cursor="hand2",
        )

    def _current_server(self) -> str:
        servers = config.enabled_servers(self.cfg)
        return servers[0] if servers else "（没配）"

    def _copy_repo(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(REPO)
        messagebox.showinfo("好了", "仓库地址已经放剪贴板里了。", parent=self.root)

    def _open_repo(self) -> None:
        import webbrowser

        webbrowser.open(REPO)

    def _open_settings(self) -> None:
        self.root.destroy()
        self.on_settings()

    def show(self) -> None:
        self.root.mainloop()


# ================================ 设置 ================================


class SettingsWindow:
    def __init__(self, cfg: dict, client_uid: str, on_save):
        self.cfg = dict(cfg)
        self.client_uid = client_uid
        self.on_save = on_save
        self.root = tk.Tk()
        self.root.title("Mythclass 设置")
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        _center(self.root, 620, 640)
        _apply_window_icon(self.root)
        self.vars: dict[str, tk.Variable] = {}

        if not self._ask_password():
            self.root.destroy()
            self.authorized = False
            return
        self.authorized = True
        self._build()

    # ------------------------------ 密码门 ------------------------------

    def _ask_password(self) -> bool:
        dialog = tk.Toplevel(self.root)
        dialog.title("要密码")
        dialog.configure(bg=BG)
        dialog.attributes("-topmost", True)
        dialog.resizable(False, False)
        _center(dialog, 340, 190)

        tk.Label(dialog, text="管理员密码", bg=BG, fg=MOSS, font=("Microsoft YaHei", 11, "bold")).pack(pady=(22, 6))
        entry = tk.Entry(dialog, show="*", width=26, font=("Consolas", 11))
        entry.pack()
        entry.focus_set()

        result = {"ok": False, "password": ""}

        def confirm(_event=None):
            entered = entry.get()
            if config.verify_admin_password(self.cfg, entered):
                result["ok"] = True
                result["password"] = entered
                dialog.destroy()
            else:
                messagebox.showerror("不对", "密码错了。", parent=dialog)
                entry.delete(0, tk.END)

        entry.bind("<Return>", confirm)
        tk.Button(dialog, text="进去", command=confirm, bg=MOSS, fg=BG, relief="flat", padx=18, pady=4, cursor="hand2").pack(pady=14)
        dialog.grab_set()
        self.root.wait_window(dialog)
        return result["ok"]

    # ------------------------------- 界面 -------------------------------

    def _build(self) -> None:
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=14, pady=12)
        notebook.add(self._tab_basic(notebook), text="基本")
        notebook.add(self._tab_servers(notebook), text="服务器")
        notebook.add(self._tab_monitor(notebook), text="监控与记录")
        notebook.add(self._tab_protect(notebook), text="保护")

        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=14, pady=(0, 14))
        tk.Button(bar, text="检查更新", command=lambda: run_update_check(self.cfg), bg="#d8d2c2", fg=INK, relief="flat", padx=16, pady=6, cursor="hand2").pack(side="left")
        tk.Button(bar, text="保存", command=self._save, bg=MOSS, fg=BG, relief="flat", padx=20, pady=6, cursor="hand2").pack(side="right")
        tk.Button(bar, text="取消", command=self.root.destroy, bg="#d8d2c2", fg=INK, relief="flat", padx=16, pady=6, cursor="hand2").pack(side="right", padx=8)

    def _tab_basic(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=BG, padx=18, pady=16)
        v = self.vars

        tk.Label(frame, text="机器名", bg=BG, fg=MOSS).grid(row=0, column=0, sticky="w", pady=6)
        v["clientName"] = tk.StringVar(value=self.cfg.get("clientName", ""))
        tk.Entry(frame, textvariable=v["clientName"], width=36).grid(row=0, column=1, sticky="w")

        tk.Label(frame, text="机器 ID", bg=BG, fg=MOSS).grid(row=1, column=0, sticky="w", pady=6)
        tk.Label(frame, text=self.client_uid, bg=BG, fg=INK, font=("Consolas", 10)).grid(row=1, column=1, sticky="w")

        tk.Label(frame, text="管理员密码", bg=BG, fg=MOSS).grid(row=2, column=0, sticky="w", pady=6)
        # 不显示当前密码（存的是哈希，也显示不出来）。留空 = 不改
        v["adminPassword"] = tk.StringVar(value="")
        tk.Entry(frame, textvariable=v["adminPassword"], width=36, show="*").grid(row=2, column=1, sticky="w")
        tk.Label(frame, text="留空表示不改。密码只存哈希，看不到原文。", bg=BG, fg=MOSS, font=("Microsoft YaHei", 9)).grid(row=3, column=1, sticky="w")

        if config.needs_password_change(self.cfg):
            tk.Label(frame, text="现在还是默认密码 admin123，建议改掉。", bg=BG, fg=AMBER, font=("Microsoft YaHei", 9)).grid(row=4, column=1, sticky="w")

        v["autoUpdate"] = tk.BooleanVar(value=bool(self.cfg.get("autoUpdate", True)))
        tk.Checkbutton(
            frame, variable=v["autoUpdate"], bg=BG, fg=INK, activebackground=BG,
            text="自动更新（有新版就自己下载安装，默认开着）",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(14, 2))

        v["complianceAccepted"] = tk.BooleanVar(value=bool(self.cfg.get("complianceAccepted")))
        tk.Checkbutton(
            frame, variable=v["complianceAccepted"], bg=BG, fg=INK, activebackground=BG, wraplength=420, justify="left",
            text="我知道这套软件会看屏幕、记文件，只用在合法教学管理上，并已告知学生、取得学校同意。",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(14, 4))
        return frame

    def _tab_servers(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=BG, padx=18, pady=16)

        tk.Label(frame, text="连接顺序：官方优先，然后按下面顺序试。官方删不掉。", bg=BG, fg=MOSS, wraplength=520, justify="left").pack(anchor="w")

        self.server_list = tk.Listbox(frame, height=6, font=("Consolas", 10))
        self.server_list.pack(fill="x", pady=10)
        self._refresh_servers()

        add = tk.Frame(frame, bg=BG)
        add.pack(fill="x")
        tk.Label(add, text="名称", bg=BG, fg=MOSS).grid(row=0, column=0, sticky="w")
        self.vars["newName"] = tk.StringVar()
        tk.Entry(add, textvariable=self.vars["newName"], width=16).grid(row=0, column=1, padx=6)
        tk.Label(add, text="地址", bg=BG, fg=MOSS).grid(row=0, column=2, sticky="w")
        self.vars["newUrl"] = tk.StringVar()
        tk.Entry(add, textvariable=self.vars["newUrl"], width=28).grid(row=0, column=3, padx=6)
        tk.Button(add, text="加上", command=self._add_server, bg=MOSS, fg=BG, relief="flat", padx=12, cursor="hand2").grid(row=0, column=4)
        tk.Button(add, text="删掉选中的", command=self._remove_server, bg="#d8d2c2", fg=INK, relief="flat", padx=12, cursor="hand2").grid(row=1, column=4, pady=8)
        return frame

    def _refresh_servers(self) -> None:
        self.server_list.delete(0, tk.END)
        for s in self.cfg.get("servers", []):
            tag = "官方" if s.get("official") else ("启用" if s.get("enabled", True) else "停用")
            self.server_list.insert(tk.END, f"[{tag}] {s.get('name', '')} · {s.get('url', '')}")

    def _add_server(self) -> None:
        url = self.vars["newUrl"].get().strip()
        if not url:
            return messagebox.showwarning("缺东西", "地址得填。", parent=self.root)
        if not url.startswith(("ws://", "wss://")):
            url = "wss://" + url
        self.cfg.setdefault("servers", []).append(
            {"name": self.vars["newName"].get().strip() or url, "url": url, "remark": "", "enabled": True, "official": False}
        )
        self.vars["newName"].set("")
        self.vars["newUrl"].set("")
        self._refresh_servers()

    def _remove_server(self) -> None:
        picked = self.server_list.curselection()
        if not picked:
            return
        index = picked[0]
        servers = self.cfg.get("servers", [])
        if 0 <= index < len(servers):
            if servers[index].get("official"):
                return messagebox.showerror("不行", "官方服务器不能删。", parent=self.root)
            servers.pop(index)
            self._refresh_servers()

    def _tab_monitor(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=BG, padx=18, pady=16)
        v = self.vars

        tk.Label(frame, text="监控目录（一行一个）", bg=BG, fg=MOSS).pack(anchor="w")
        self.watch_text = tk.Text(frame, height=7, font=("Consolas", 10))
        self.watch_text.pack(fill="x", pady=8)
        self.watch_text.insert("1.0", "\n".join(self.cfg.get("watchDirs", [])))

        row = tk.Frame(frame, bg=BG)
        row.pack(fill="x", pady=6)
        tk.Label(row, text="最多留多少条", bg=BG, fg=MOSS).grid(row=0, column=0, sticky="w")
        v["maxLogCount"] = tk.StringVar(value=str(self.cfg.get("maxLogCount", 5000)))
        tk.Entry(row, textvariable=v["maxLogCount"], width=12).grid(row=0, column=1, padx=8)
        tk.Label(row, text="最多占多少 MB", bg=BG, fg=MOSS).grid(row=0, column=2, sticky="w")
        v["maxLogSizeMB"] = tk.StringVar(value=str(int(self.cfg.get("maxLogSize", 209715200)) // 1024 // 1024))
        tk.Entry(row, textvariable=v["maxLogSizeMB"], width=12).grid(row=0, column=3, padx=8)

        tk.Label(frame, text="超了就删最旧的，不会把硬盘塞满。", bg=BG, fg=INK).pack(anchor="w", pady=(8, 0))
        return frame

    def _tab_protect(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=BG, padx=18, pady=16)
        v = self.vars

        for key, text in (
            ("protectProcess", "进程保护（被结束后自动回来）"),
            ("disableTaskManager", "禁用任务管理器"),
            ("autostart", "开机自启"),
        ):
            v[key] = tk.BooleanVar(value=bool(self.cfg.get(key)))
            tk.Checkbutton(frame, text=text, variable=v[key], bg=BG, fg=INK, activebackground=BG, anchor="w").pack(anchor="w", pady=4)

        tk.Label(
            frame,
            text="禁用任务管理器只影响当前用户，勾上并保存后立刻生效。\n自启会同时写 Run 键和一条登录计划任务。\n进程保护：常驻一个跟班进程，主进程被结束后几秒内会被拉回来。",
            bg=BG, fg=MOSS, justify="left", font=("Microsoft YaHei", 9),
        ).pack(anchor="w", pady=(14, 0))
        return frame

    # ------------------------------- 保存 -------------------------------

    def _save(self) -> None:
        cfg = dict(self.cfg)
        cfg["clientName"] = self.vars["clientName"].get().strip() or "教室一体机"

        # 密码留空就保持原样；填了就换掉（存哈希，不留明文）
        new_password = self.vars["adminPassword"].get().strip()
        if new_password:
            if len(new_password) < 6:
                messagebox.showerror("太短了", "管理员密码至少 6 位。", parent=self.root)
                return
            config.set_admin_password(cfg, new_password)

        cfg["complianceAccepted"] = bool(self.vars["complianceAccepted"].get())
        cfg["watchDirs"] = [line.strip() for line in self.watch_text.get("1.0", tk.END).splitlines() if line.strip()]

        try:
            cfg["maxLogCount"] = max(100, int(float(self.vars["maxLogCount"].get())))
        except ValueError:
            cfg["maxLogCount"] = 5000
        try:
            cfg["maxLogSize"] = max(1, int(float(self.vars["maxLogSizeMB"].get()))) * 1024 * 1024
        except ValueError:
            cfg["maxLogSize"] = 209715200

        cfg["protectProcess"] = bool(self.vars["protectProcess"].get())
        cfg["disableTaskManager"] = bool(self.vars["disableTaskManager"].get())
        cfg["autostart"] = bool(self.vars["autostart"].get())

        # 保护开关顺手落地
        guard.set_task_manager_disabled(cfg["disableTaskManager"])
        if cfg["autostart"]:
            guard.enable_autostart()
        else:
            guard.disable_autostart()

        saved = config.save(cfg)
        self.on_save(saved)
        messagebox.showinfo("好了", "设置存下来了。", parent=self.root)
        self.root.destroy()

    def show(self) -> None:
        self.root.mainloop()


def open_about_async(cfg: dict, client_uid: str, on_settings) -> None:
    """托盘是主线程，窗口得另开一个线程跑 tkinter"""
    def run():
        AboutWindow(cfg, client_uid, on_settings).show()

    threading.Thread(target=run, name="mythclass-about", daemon=True).start()


def open_settings_async(cfg: dict, client_uid: str, on_save) -> None:
    def run():
        window = SettingsWindow(cfg, client_uid, on_save)
        if getattr(window, "authorized", False):
            window.show()

    threading.Thread(target=run, name="mythclass-settings", daemon=True).start()

def _open_in_explorer(path) -> None:
    """在资源管理器里选中这个文件（找不到目录就开目录）"""
    import subprocess

    try:
        p = str(path)
        if os.path.isfile(p):
            subprocess.Popen(["explorer", "/select,", p])
        else:
            folder = os.path.dirname(p) or p
            if os.path.isdir(folder):
                subprocess.Popen(["explorer", folder])
    except Exception:
        pass


def _reinstall_prompt(path, retry) -> None:
    """装不起来时，给一个「再试一次」的窗口（教室那台机器前面的人可以点）"""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    box = tk.Toplevel(root)
    box.title("客户端需要更新")
    box.configure(bg=BG)
    box.attributes("-topmost", True)
    _center(box, 520, 330)
    pad = tk.Frame(box, bg=BG, padx=22, pady=20)
    pad.pack(fill="both", expand=True)
    tk.Label(pad, text="客户端需要更新", bg=BG, fg=MOSS,
             font=("Microsoft YaHei", 13, "bold")).pack(anchor="w")
    tk.Message(
        pad,
        text=("新版本已经下好了，但自动安装需要管理员权限。\n"
              "请点下面的「现在更新」；弹出来的窗口（用户账户控制）里点「是」。\n\n"
              f"也可以自己双击这个文件：\n{path}"),
        bg=BG, fg=INK, width=470, font=("Microsoft YaHei", 10),
    ).pack(anchor="w", pady=(12, 14))
    bar = tk.Frame(pad, bg=BG)
    bar.pack(fill="x", side="bottom")
    tk.Button(bar, text="现在更新", command=lambda: retry(),
              bg=MOSS, fg=BG, relief="flat", padx=16, pady=6,
              font=("Microsoft YaHei", 10), cursor="hand2").pack(side="right")
    tk.Button(bar, text="打开所在文件夹", command=lambda: _open_in_explorer(path),
              bg="#d8d2c2", fg=INK, relief="flat", padx=14, pady=6,
              font=("Microsoft YaHei", 10), cursor="hand2").pack(side="right", padx=8)
    tk.Button(bar, text="稍后", command=lambda: (box.destroy(), root.destroy()),
              bg="#d8d2c2", fg=INK, relief="flat", padx=14, pady=6,
              font=("Microsoft YaHei", 10), cursor="hand2").pack(side="left")
    root.mainloop()


def run_update_check(cfg, silent: bool = False, auto: bool = False) -> None:
    """检查更新。

    silent=True 且没有新版时不弹窗（开机自动查用）
    auto=True  有新版本时直接下载安装，不先问（自动更新用）
    """
    import threading

    def work():
        from . import updater

        info = updater.check(cfg)

        def ui_result(text, offer=None):
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            box = tk.Toplevel(root)
            box.title("检查更新")
            box.configure(bg=BG)
            box.attributes("-topmost", True)
            _center(box, 460, 300)
            pad = tk.Frame(box, bg=BG, padx=22, pady=20)
            pad.pack(fill="both", expand=True)
            tk.Label(pad, text="检查更新", bg=BG, fg=MOSS, font=("Microsoft YaHei", 13, "bold")).pack(anchor="w")
            tk.Message(pad, text=text, bg=BG, fg=INK, width=410, font=("Microsoft YaHei", 10)).pack(anchor="w", pady=(12, 16))
            bar = tk.Frame(pad, bg=BG)
            bar.pack(fill="x", side="bottom")
            if offer:
                tk.Button(bar, text="现在更新", command=lambda: (box.destroy(), root.destroy(), offer()),
                          bg=MOSS, fg=BG, relief="flat", padx=16, pady=5, font=("Microsoft YaHei", 10),
                          cursor="hand2").pack(side="right")
            tk.Button(bar, text="关闭", command=lambda: (box.destroy(), root.destroy()),
                      bg="#d8d2c2", fg=INK, relief="flat", padx=16, pady=5,
                      font=("Microsoft YaHei", 10), cursor="hand2").pack(side="right", padx=8)
            root.mainloop()

        def do_install(info_dict):
            from . import updater as up

            def go():
                text = "正在下载新版本…"
                ui_result(text)

            path = up.download(info_dict.get("url", ""), info_dict.get("sha256", ""))
            if not path:
                why = (up.LAST_ERROR[0] if getattr(up, "LAST_ERROR", None) else "") or "不知道原因"
                ui_result("下载没成功：\n" + why + "\n\n下载地址：\n" + (info_dict.get("url") or "（服务端没给地址）"))
                return
            if up.run_installer(path):
                # 装完应该自己回来；再挂一个兜底，免得更新完机器上没客户端
                up.schedule_relaunch(45)
                ui_result("安装包已经起来了，装完客户端会自动打开（约一分钟）。\n"
                          "要是过两分钟还没看到托盘图标，手动开一下就行。")
                up.restart_soon(1.5)
            else:
                why = (up.LAST_ERROR[0] if getattr(up, "LAST_ERROR", None) else "") or "不知道原因"
                # 教室那台机器前面可能有人 —— 给个能点的窗口，别只留一句话
                if info_dict.get("mandatory"):
                    _reinstall_prompt(path, lambda: do_install(info_dict))
                else:
                    ui_result(
                        "安装包下好了，放在：\n" + str(path) + "\n\n自动装不上，试过的路：\n" + why,
                        offer=lambda: do_install(info_dict),
                    )

        if info is None:
            if not silent:
                ui_result("一个服务端都没问通，检查一下网络或者服务端地址。")
            return

        if not info.get("update"):
            if not silent:
                ui_result(f"已经是最新的：v{info.get('current') or ''}")
            return

        notes = (info.get("notes") or "").strip() or "（作者没写说明）"
        text = (f"有新版啦：v{info.get('current')} → v{info.get('latest')}\n\n"
                f"{notes}")
        if auto or info.get("mandatory"):
            do_install(info)
        else:
            ui_result(text, offer=lambda: do_install(info))

    threading.Thread(target=work, daemon=True).start()
