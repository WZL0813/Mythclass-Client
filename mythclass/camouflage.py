"""给看门狗副本换上"真系统进程"的图标和版本信息

问题：副本是主程序的复制品 → 图标是 Mythclass 的，文件说明也写着 Mythclass
→ 任务管理器里一眼就露馅。

做法（纯 ctypes，不加依赖）：
  用 LoadLibraryEx(LOAD_LIBRARY_AS_DATAFILE) 打开一个**真的系统 exe**，
  把它的图标组（RT_GROUP_ICON + 它引用的那些 RT_ICON）和版本信息（RT_VERSION）
  整组搬进副本里 —— 换完副本的图标、文件说明、公司名都跟系统那个一样。

  WmiPrvSE.exe      ← C:\\Windows\\System32\\wbem\\WmiPrvSE.exe
  RuntimeBroker.exe ← C:\\Windows\\System32\\RuntimeBroker.exe
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from ctypes import wintypes

# 换资源时用它
LOAD_LIBRARY_AS_DATAFILE = 0x00000002
RT_ICON = 3
RT_GROUP_ICON = 14
RT_VERSION = 16

# 两个副本各自"学"哪个系统进程
SOURCE_EXE = {
    "main": [r"System32\wbem\WmiPrvSE.exe", r"System32\WmiPrvSE.exe", r"System32\svchost.exe"],
    "tools": [r"System32\RuntimeBroker.exe", r"System32\SearchHost.exe", r"System32\svchost.exe"],
}

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

kernel32.LoadLibraryExW.restype = wintypes.HMODULE
kernel32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
kernel32.FreeLibrary.argtypes = [wintypes.HMODULE]
kernel32.FindResourceW.restype = wintypes.HANDLE
kernel32.FindResourceW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p]
kernel32.LoadResource.restype = wintypes.HANDLE
kernel32.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
kernel32.LockResource.restype = ctypes.c_void_p
kernel32.LockResource.argtypes = [wintypes.HANDLE]
kernel32.SizeofResource.restype = wintypes.DWORD
kernel32.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
kernel32.EnumResourceNamesW.restype = wintypes.BOOL
kernel32.EnumResourceNamesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
kernel32.BeginUpdateResourceW.restype = wintypes.HANDLE
kernel32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
kernel32.UpdateResourceW.restype = wintypes.BOOL
kernel32.UpdateResourceW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                     wintypes.WORD, ctypes.c_void_p, wintypes.DWORD]
kernel32.EndUpdateResourceW.restype = wintypes.BOOL
kernel32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]

# 注意：第二个参数（类型）和第三个参数（名字）都可能是**数字 id**，
# 系统是按"伪指针"传的。所以这两个位置一律用 c_void_p ——
# 一旦声明成 LPWSTR/LPCWSTR，ctypes 就会去解引用 0x0001 这种地址，
# 直接访问越界把整个进程打死（try/except 都拦不住）。
ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE,
                              ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


def _as_id(value: int):
    """把数字 id 当成 MAKEINTRESOURCE 用

    返回 c_void_p，配合下面把函数签名里的类型参数改成 c_void_p ——
    这样 ctypes 只把它当"一个地址值"传下去，不会去当字符串读。
    """
    return ctypes.c_void_p(int(value))


def _enum_ids(module, res_type: int) -> list[int]:
    ids: list[int] = []

    def cb(_h, _t, name, _p):
        # name 是伪指针：小数值 = 数字 id；大地址才是真字符串（这里不需要）
        try:
            value = int(name or 0)
        except Exception:
            return True
        if 0 < value < 0x10000:
            ids.append(value)
        return True

    try:
        kernel32.EnumResourceNamesW(module, _as_id(res_type), ENUMPROC(cb), None)
    except Exception:
        pass
    return ids


def _read(module, res_type: int, name) -> bytes | None:
    handle = kernel32.FindResourceW(module, name, _as_id(res_type))
    if not handle:
        return None
    size = kernel32.SizeofResource(module, handle)
    if not size:
        return None
    ptr = kernel32.LockResource(kernel32.LoadResource(module, handle))
    if not ptr:
        return None
    return ctypes.string_at(ptr, size)


def find_source(role: str) -> Path | None:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    for rel in SOURCE_EXE.get(role, []):
        p = Path(root) / rel
        if p.exists():
            return p
    return None


def dress_up(target: Path, role: str) -> tuple[bool, str]:
    """把 target 的图标和版本信息换成系统进程那套"""
    src = find_source(role)
    if not src:
        return False, "找不到可参照的系统程序"

    try:
        module = kernel32.LoadLibraryExW(str(src), None, LOAD_LIBRARY_AS_DATAFILE)
        if not module:
            return False, f"打不开参照程序 {src.name}"

        # ① 图标组：先把组里的每一个 RT_ICON 抠出来
        groups = _enum_ids(module, RT_GROUP_ICON)
        icons: dict[int, bytes] = {}
        for gid in groups:
            data = _read(module, RT_GROUP_ICON, _as_id(gid))
            if not data:
                continue
            # 图标组头 6 字节 + 每个条目 14 字节，条目里的 id 在第 3、4 字节
            count = int.from_bytes(data[4:6], "little")
            for i in range(count):
                off = 6 + i * 14
                if off + 4 > len(data):
                    break
                iid = int.from_bytes(data[off + 2 : off + 4], "little")
                icon = _read(module, RT_ICON, _as_id(iid))
                if icon:
                    icons[iid] = icon
        group_data = {gid: _read(module, RT_GROUP_ICON, _as_id(gid)) for gid in groups}

        # ② 版本信息（文件说明、公司名都在这里）
        versions = {}
        for vid in _enum_ids(module, RT_VERSION):
            v = _read(module, RT_VERSION, _as_id(vid))
            if v:
                versions[vid] = v

        kernel32.FreeLibrary(module)
    except Exception as err:
        return False, f"读参照程序失败：{type(err).__name__}: {err}"

    if not group_data and not versions:
        return False, "参照程序里没找到图标或版本信息"

    # ③ 写进副本
    # 先把 PyInstaller 追加在尾部的档案切下来 —— 不然改资源会把它截掉，
    # 副本一跑就报 "Could not load PyInstaller's embedded PKG archive"。
    head, tail = _split_tail(target)
    if tail:
        try:
            target.write_bytes(head)
        except Exception as err:
            return False, f"切尾巴失败：{type(err).__name__}: {err}"

    handle = kernel32.BeginUpdateResourceW(str(target), False)
    if not handle:
        if tail:
            try:
                target.write_bytes(head + tail)
            except Exception:
                pass
        return False, "打不开副本（可能没有写权限）"
    try:
        for iid, data in icons.items():
            kernel32.UpdateResourceW(handle, _as_id(RT_ICON), _as_id(iid), 0, data, len(data))
        for gid, data in group_data.items():
            if data:
                kernel32.UpdateResourceW(handle, _as_id(RT_GROUP_ICON), _as_id(gid), 0, data, len(data))
        for vid, data in versions.items():
            kernel32.UpdateResourceW(handle, _as_id(RT_VERSION), _as_id(vid), 0, data, len(data))
    finally:
        kernel32.EndUpdateResourceW(handle, False)

    # 把档案尾巴接回去（接上之后 PyInstaller 的入口还能按魔数找到它）
    if tail:
        try:
            with open(target, "ab") as fh:
                fh.write(tail)
        except Exception as err:
            return False, f"接回档案失败：{type(err).__name__}: {err}"

    return True, f"换成了 {src.name} 的图标和版本信息（{len(icons)} 个图标，尾巴 {len(tail)} 字节）"

# PyInstaller 档案的魔数（入口就是靠它找档案的）
PYI_MAGIC = b"MEI\014\013\012\013\016"


def _split_tail(path: Path) -> tuple[bytes, bytes]:
    """把文件切成 (PE 本体, PyInstaller 档案尾巴)；不是打包版就返回 (全部, b"")"""
    try:
        data = path.read_bytes()
    except Exception:
        return b"", b""
    pos = data.rfind(PYI_MAGIC)
    if pos <= 0:
        return data, b""
    return data[:pos], data[pos:]
