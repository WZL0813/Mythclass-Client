# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 目录版（onedir）

用法（在仓库根目录执行）：

    pyinstaller build/mythclass-onedir.spec

产物：dist-onedir/MythclassClient/MythclassClient.exe 加上同目录的一堆依赖。

**为什么要有这个版本**

单文件版（mythclass.spec）每次启动都要把 57MB、两千多个文件解包到
%TEMP%\\_MEIxxxxxx，再从那里面启动 Python。这一步一旦失败——
临时目录被塞满、杀软把解出来的 python312.dll 删了、文件被占用——
引导层就会弹：

    Failed to start embedded python interpreter!

而且被强杀时（任务管理器、跟班互杀）解包目录不会自己清，
一个 57MB，攒十几个就把小磁盘的虚拟机撑满了，于是越用越容易报这个错。

目录版不存在解包这一步，直接从文件夹里读，启动也快得多。
代价是它是个文件夹，不是单个 exe —— 分发时打成一个 zip 就行。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent

AIORTC_HIDDEN = collect_submodules('aiortc')
AV_DATAS, AV_BINARIES, AV_HIDDEN = collect_all('av')

a = Analysis(
    [str(ROOT / 'build' / 'entry.py')],
    pathex=[str(ROOT)],
    binaries=AV_BINARIES,
    datas=AV_DATAS + [
        (str(ROOT / 'config.example.json'), '.'),
        (str(ROOT / 'mythclass' / 'assets'), 'mythclass/assets'),
    ],
    hiddenimports=[
        'pystray._win32',
        'PIL._tkinter_finder',
        'PIL.Image', 'PIL.ImageDraw',
        'watchdog.observers.winapi',
        'watchdog.observers.polling',
        'pycaw.pycaw',
        'comtypes', 'comtypes.client',
        'win32timezone',
        'win32com', 'win32com.client',
        'aiortc',
        'aioice', 'pylibsrtp', 'google_crc32c', 'pyee', 'cryptography',
    ] + AIORTC_HIDDEN + AV_HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'numpy', 'pandas', 'scipy',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'notebook', 'IPython', 'pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # 依赖不塞进 exe，交给下面的 COLLECT 摊在文件夹里
    name='MythclassClient',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / 'build' / 'mythclass.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MythclassClient',
)
