"""黑屏安静（极域那种）＋ 右下角举手

- 全屏、纯黑、无边框、置顶
- 学生**叉不掉**：关窗口的事件被吃掉，还定期把自己顶回最前
- 右下角一个「举手」按钮，学生点一下老师那边能看到
- 只有老师下「取消黑屏安静」才会消失
"""

from __future__ import annotations

import threading

_state = {
    "on": False,
    "text": "",
    "hand": False,
    "hand_at": 0.0,
    "root": None,
    "raised": 0,
}
_lock = threading.Lock()


def status() -> dict:
    with _lock:
        return {
            "on": bool(_state["on"]),
            "text": _state["text"],
            "hand": bool(_state["hand"]),
            "handAt": _state["hand_at"],
        }


def raise_hand(on: bool = True, on_report=None) -> None:
    """学生举手 / 放下（黑屏里点按钮走这里）"""
    import time

    with _lock:
        _state["hand"] = bool(on)
        _state["hand_at"] = time.time() if on else 0.0
        _state["raised"] = _state.get("raised", 0) + 1
    if on_report:
        try:
            on_report("举手" if on else "放下手")
        except Exception:
            pass


def show(text: str = "", on_report=None) -> tuple[bool, str]:
    """开始黑屏安静"""
    import tkinter as tk

    with _lock:
        if _state["on"]:
            _state["text"] = text or _state["text"]
            return True, "已经黑着屏了"
        _state["on"] = True
        _state["text"] = text or ""

    def run() -> None:
        root = tk.Tk()
        root.title("Mythclass")
        root.configure(bg="#000000")
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        try:
            root.overrideredirect(True)  # 没有标题栏，点不到叉
        except Exception:
            pass

        frame = tk.Frame(root, bg="#000000")
        frame.pack(fill="both", expand=True)

        msg = tk.Label(
            frame, text=_state["text"] or "", bg="#000000", fg="#4a5a4c",
            font=("Microsoft YaHei", 22), wraplength=900, justify="center",
        )
        msg.pack(expand=True)

        hint = tk.Label(
            frame, text="现在是安静时间，请看向黑板。", bg="#000000", fg="#2f3a31",
            font=("Microsoft YaHei", 13),
        )
        hint.pack(pady=(0, 26))

        hand_btn = tk.Button(
            frame, text="举手", relief="flat", padx=26, pady=10,
            bg="#1d2a20", fg="#8fa88e", activebackground="#2f4f3e", activeforeground="#f3efe3",
            font=("Microsoft YaHei", 15, "bold"), cursor="hand2",
        )

        def toggle_hand() -> None:
            now = not _state["hand"]
            raise_hand(now, on_report)
            hand_btn.configure(text="已举手，点一下放下" if now else "举手",
                               bg="#2f4f3e" if now else "#1d2a20",
                               fg="#f3efe3" if now else "#8fa88e")

        hand_btn.configure(command=toggle_hand)
        hand_btn.place(relx=1.0, rely=1.0, x=-28, y=-26, anchor="se")

        # 老师/局域网那边把手放下时，这个按钮也得跟着变回去
        def sync_button() -> None:
            if not _state["on"]:
                return
            up = bool(_state["hand"])
            want = "已举手，点一下放下" if up else "举手"
            try:
                if hand_btn.cget("text") != want:
                    hand_btn.configure(
                        text=want,
                        bg="#2f4f3e" if up else "#1d2a20",
                        fg="#f3efe3" if up else "#8fa88e",
                    )
            except Exception:
                return
            root.after(1000, sync_button)

        sync_button()

        # 叉不掉：关窗口的事件直接吃掉
        root.protocol("WM_DELETE_WINDOW", lambda: None)

        def keep_on_top() -> None:
            if not _state["on"]:
                return
            try:
                root.attributes("-topmost", True)
                root.lift()
                root.focus_force()
            except Exception:
                pass
            root.after(1200, keep_on_top)

        keep_on_top()

        with _lock:
            _state["root"] = root
        try:
            root.mainloop()
        finally:
            with _lock:
                _state["root"] = None
                _state["on"] = False
                _state["hand"] = False

    threading.Thread(target=run, daemon=True).start()
    return True, "黑屏安静开始了"


def hide() -> tuple[bool, str]:
    """取消黑屏安静（只有老师那边能调）"""
    with _lock:
        root = _state["root"]
        was = _state["on"]
        _state["on"] = False
        _state["hand"] = False
    if root is not None:
        try:
            root.after(0, root.destroy)
        except Exception:
            pass
    return True, "黑屏安静结束了" if was else "本来就没黑着"
