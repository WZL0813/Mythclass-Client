"""本地记录库：文件改动 + 音频状态

SQLite 存在 %APPDATA%\\Mythclass\\records.db。
超出配置的条数/体积就自动清旧的，别把一体机塞满。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS file_logs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp  TEXT NOT NULL,
  operation  TEXT NOT NULL,
  file_path  TEXT,
  file_size  INTEGER,
  uploaded   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_fl_time ON file_logs (timestamp DESC);

CREATE TABLE IF NOT EXISTS audio_logs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp    TEXT NOT NULL,
  process_name TEXT,
  title        TEXT,
  volume       INTEGER,
  state        TEXT,
  uploaded     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_al_time ON audio_logs (timestamp DESC);
"""


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class RecordStore:
    """线程安全的记录库"""

    def __init__(self, db_file: Path | None = None):
        config.ensure_dirs()
        self.path = db_file or config.DB_FILE
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------ 文件记录 ------------------------------

    # 文件修改日志自动清理：默认留 7 天、最多 5000 条（主人要求）
    FILE_LOG_KEEP_DAYS = 7
    FILE_LOG_KEEP_ROWS = 5000

    def prune_file_logs(self, days: int | None = None, max_rows: int | None = None) -> int:
        """删掉太老/太多的文件修改日志，返回删了几条"""
        days = self.FILE_LOG_KEEP_DAYS if days is None else days
        max_rows = self.FILE_LOG_KEEP_ROWS if max_rows is None else max_rows
        # 跟 now_str() 一个格式，字符串比较才靠得住
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        removed = 0
        try:
            with self._lock:
                cur = self._conn.execute("DELETE FROM file_logs WHERE timestamp < ?", (cutoff,))
                removed += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                cur = self._conn.execute(
                    "DELETE FROM file_logs WHERE id NOT IN "
                    "(SELECT id FROM file_logs ORDER BY id DESC LIMIT ?)",
                    (max_rows,),
                )
                removed += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                self._conn.commit()
        except Exception as err:  # 清理失败不该影响正常记录，但得留下痕迹
            self._prune_error = f"{type(err).__name__}: {err}"
        return removed

    def add_file_log(
        self, operation: str, file_path: str, file_size: int = 0
    ) -> None:
        # 顺手清一清太老的日志（每 50 次写一次就够，别每次查）
        self._fl_writes = getattr(self, "_fl_writes", 0) + 1
        if self._fl_writes % 50 == 1:
            self.prune_file_logs()
        with self._lock:
            self._conn.execute(
                "INSERT INTO file_logs (timestamp, operation, file_path, file_size) VALUES (?, ?, ?, ?)",
                (now_str(), operation, file_path, int(file_size or 0)),
            )
            self._conn.commit()

    def pending_file_logs(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM file_logs WHERE uploaded = 0 ORDER BY id ASC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_uploaded(self, table: str, ids: list[int]) -> None:
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        with self._lock:
            self._conn.execute(f"UPDATE {table} SET uploaded = 1 WHERE id IN ({marks})", ids)
            self._conn.commit()

    def recent_file_logs(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM file_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------ 音频记录 ------------------------------

    def add_audio_log(self, process_name: str, title: str, volume: int, state: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audio_logs (timestamp, process_name, title, volume, state) VALUES (?, ?, ?, ?, ?)",
                (now_str(), process_name, title, int(volume), state),
            )
            self._conn.commit()

    def pending_audio_logs(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audio_logs WHERE uploaded = 0 ORDER BY id ASC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------- 清理 --------------------------------

    def trim(self, max_count: int, max_size: int) -> int:
        """超出上限就删最旧的。返回删掉的条数。"""
        removed = 0
        with self._lock:
            for table in ("file_logs", "audio_logs"):
                total = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if total > max_count:
                    self._conn.execute(
                        f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} ORDER BY id ASC LIMIT ?)",
                        (total - max_count,),
                    )
                    removed += total - max_count
            self._conn.commit()

        # 再按体积兜底：整库大了就删一半最旧的
        try:
            if self.path.stat().st_size > max_size:
                with self._lock:
                    for table in ("file_logs", "audio_logs"):
                        total = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                        if total:
                            self._conn.execute(
                                f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} ORDER BY id ASC LIMIT ?)",
                                (total // 2,),
                            )
                            removed += total // 2
                    self._conn.commit()
                self._vacuum()
        except OSError:
            pass
        return removed

    def _vacuum(self) -> None:
        try:
            with self._lock:
                self._conn.execute("VACUUM")
        except sqlite3.Error:
            pass

    def stats(self) -> dict:
        with self._lock:
            files = self._conn.execute("SELECT COUNT(*) FROM file_logs").fetchone()[0]
            audios = self._conn.execute("SELECT COUNT(*) FROM audio_logs").fetchone()[0]
        return {"fileLogs": files, "audioLogs": audios, "dbFile": str(self.path)}

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass
