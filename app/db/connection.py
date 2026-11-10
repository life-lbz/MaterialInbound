"""统一数据库访问：SQLite 本地、PostgreSQL / MySQL 生产。

关键设计（v2.0 修复 D2，v3.1 扩展 MySQL，v3.1 修复 D30）：
1. SQL 统一使用 ? 占位符，由本层按方言转换：SQLite 保留 ?，PG/MySQL 转 %s。
2. 统一提供 query()/execute() 接口（内部用 cursor），上层不直接触碰 connection。
3. 时间一律由应用侧写入（isoformat 字符串），不依赖数据库函数。
4. 方言由 DSN scheme 识别：sqlite:// / postgresql:// / mysql://
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from app.config import DB_DSN

_SCHEME = DB_DSN.split("://", 1)[0].lower()
_IS_PG = _SCHEME == "postgresql"
_IS_MYSQL = _SCHEME == "mysql"
DIALECT = "pg" if _IS_PG else ("mysql" if _IS_MYSQL else "sqlite")   # 迁移文件方言标记


def _adapt(sql: str, params: Sequence[Any]) -> tuple[str, Sequence[Any]]:
    """占位符适配：psycopg2 / PyMySQL 均使用 %s，SQLite 原生 ?。"""
    if _IS_PG or _IS_MYSQL:
        return sql.replace("?", "%s"), params
    return sql, params


def _pg_connect():
    import psycopg2
    # P1-1 修复：psycopg2.connect 接受完整 URL（libpq 连接字符串），
    # 禁止 removeprefix 破坏 DSN 结构（旧实现实测报 invalid dsn: missing "="）。
    return psycopg2.connect(DB_DSN)


def _mysql_connect():
    """PyMySQL 连接：DSN 需逐字段解析（v3.1 修复 D30）。

    注意：pymysql.connect 全部为关键字参数（user/host/database...），
    不接收 DSN 字符串；必须用 urllib.parse 拆分后传参。
    """
    from urllib.parse import parse_qs, urlparse

    import pymysql

    parsed = urlparse(DB_DSN)                      # mysql://user:pass@host:port/db?charset=...
    query = parse_qs(parsed.query)
    return pymysql.connect(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=parsed.username,
        password=parsed.password or "",
        database=parsed.path.lstrip("/"),
        charset=query.get("charset", ["utf8mb4"])[0],
        cursorclass=pymysql.cursors.DictCursor,    # 返回 dict 行
        autocommit=False,
    )


def _sqlite_connect():
    """SQLite 连接：自动创建数据文件所在目录（默认 dsn 为 data/miirp.db）。

    sqlite3.connect 不会创建缺失的父目录，首次启动前需确保目录存在。
    """
    path = DB_DSN.removeprefix("sqlite:///")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def connection() -> Iterator[_Adapter]:
    """yield 统一访问接口（不是裸 connection），提交/回滚/关闭由本层负责。"""
    if _IS_PG:
        conn = _pg_connect()
    elif _IS_MYSQL:
        conn = _mysql_connect()
    else:
        conn = _sqlite_connect()
    try:
        yield _Adapter(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class _Adapter:
    """屏蔽 SQLite / psycopg2 / PyMySQL 的访问差异（cursor 抽象）。"""

    def __init__(self, conn):
        self._conn = conn

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """执行查询并返回 dict 行列表。"""
        cur = self._conn.cursor()
        sql, params = _adapt(sql, params)
        cur.execute(sql, tuple(params))
        if _IS_MYSQL:
            rows = [dict(r) for r in cur.fetchall()] if cur.description else []
        else:
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
        cur.close()
        return rows

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """执行写入语句（含 DDL），返回受影响行数（rowcount，P0-3 并发保护依赖）。"""
        cur = self._conn.cursor()
        sql, params = _adapt(sql, params)
        cur.execute(sql, tuple(params))
        affected = cur.rowcount
        cur.close()
        return affected if affected is not None else 0

    def execute_script(self, script: str) -> None:
        """按语句分割执行多语句 SQL 文件（SQLite 走原生 executescript）。"""
        if _IS_PG or _IS_MYSQL:
            cur = self._conn.cursor()
            for stmt in script.split(";"):
                if stmt.strip():
                    cur.execute(stmt)
            cur.close()
        else:
            self._conn.executescript(script)


def ping_db() -> bool:
    """数据库连通性探测（SELECT 1）。"""
    try:
        with connection() as c:
            c.query("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 - 连通性探测须吞掉一切异常，仅返回布尔结果
        return False
