# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置

用法（在仓库根目录执行）：

    pip install -r requirements.txt
    pip install pyinstaller
    pyinstaller build/mythclass.spec

产物：dist/Mythclass.exe，单文件、无控制台窗口。

两个坑记在这儿，免得下次又踩：

1. 入口必须是 build/entry.py，不能用 mythclass/__main__.py。
   后者是包内模块，用的相对导入，PyInstaller 当独立脚本分析会炸：
   `attempted relative import with no known parent package`

2. 路径一律用 SPECPATH 推导，不写 '../xxx' 这种跟着工作目录跑的路径，
   这样在哪儿执行 pyinstaller 都能对上。
"""

from pathlib import Path

ROOT = Path(SPECPATH).parent          # SPECPATH = 本文件所在目录（build/），上一层就是仓库根

a = Analysis(
    [str(ROOT / 'build' / 'entry.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # 首次运行会把它复制到 %APPDATA%\Mythclass\config.json
        (str(ROOT / 'config.example.json'), '.'),
    ],
    hiddenimports=[
        # 这几个都是写在函数体里、运行时才 import 的，显式点名更保险
        'pystray._win32',              # 托盘（Windows 后端）
        'PIL._tkinter_finder',         # 关于 / 设置窗口
        'PIL.Image', 'PIL.ImageDraw',
        'watchdog.observers.winapi',   # 文件监控（Windows 原生）
        'watchdog.observers.polling',  # 原生不可用时的兜底
        'pycaw.pycaw',                 # 音频会话
        'comtypes', 'comtypes.client',
        'win32timezone',               # pywin32 时间相关
        'win32com', 'win32com.client',
    ],
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
    a.binaries,
    a.datas,
    [],
    name='Mythclass',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # UPX 压过的 exe 常被杀软误报，教室机器上不值得
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 无界面，别弹黑框
    disable_windowed_traceback=False,   # 出错了留 traceback，方便排查
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # 有 .ico 就填路径，没有就用默认
)
