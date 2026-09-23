"""PyInstaller 的入口脚本

为什么需要它：

`mythclass/__main__.py` 里用的是包内相对导入（`from . import __product__` 这种）。
`python -m mythclass` 跑的时候没问题，但 PyInstaller 会把入口脚本当成一个
独立模块来分析，于是运行时报：

    ImportError: attempted relative import with no known parent package

所以这儿包一层：正常 import 这个包，再调它的 main()。
"""

import sys

from mythclass.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
