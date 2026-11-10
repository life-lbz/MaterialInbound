"""幂等迁移：启动时执行，记录 schema_version。

迁移文件按方言命名：migrations/<版本>_init.<dialect>.sql
（dialect ∈ sqlite | pg | mysql），migrate 只执行当前 DSN 对应的方言文件，
自增主键等方言差异在各方言文件中分别表达。
幂等语义（开发文档 §4.6）：CREATE TABLE IF NOT EXISTS + version <= MAX(version) 跳过已应用迁移，
重复启动不重复执行；applied_at 由应用侧写入（不依赖数据库时间函数）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.db.connection import DIALECT, connection

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


def migrate() -> None:
    """执行未应用的方言迁移（幂等：重复调用不报错、不重复执行）。"""
    with connection() as c:
        c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)")
        rows = c.query("SELECT MAX(version) AS v FROM schema_version")
        current = rows[0]["v"] if rows and rows[0]["v"] is not None else 0

        for sql_file in sorted(_MIGRATIONS_DIR.glob(f"*.{DIALECT}.sql")):
            version = int(sql_file.stem.split("_")[0])
            if version <= current:
                continue
            c.execute_script(sql_file.read_text(encoding="utf-8"))
            c.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                      (version, datetime.now(timezone.utc).isoformat()))
