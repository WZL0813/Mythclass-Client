import os, sys, tempfile
from pathlib import Path
CLIENT = Path(r"D:\Code\DeepSeek\deepseek\deepseek-ai\deepseek-harness\work\Mythclass-Client")
sys.path.insert(0, str(CLIENT))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="myth-launch-")
from mythclass import watchdog
import inspect
src = inspect.getsource(watchdog._launch)
print("  用了 start 脱开:", "'start'" in src)
print("  有 DETACHED 兜底:", "creationflags=flags" in src)
