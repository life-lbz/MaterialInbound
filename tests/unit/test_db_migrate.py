"""SQLite 迁移幂等测试（开发文档 §4.6/§6.5、data_contract §1，M1-03 验收）。

语义（§13 验收 #2、defect_regression D2/D15/D30）：
- 连续两次 migrate 不报错（幂等：CREATE TABLE IF NOT EXISTS + version <= MAX 跳过）。
- schema_version 单条唯一记录（version 主键），applied_at 应用侧写入（isoformat，不依赖 DB 时间函数）。
- 表/列/索引对齐 data_contract §1.2/§1.3。
- 连接层方言适配：? → %s（PG/MySQL）、_mysql_connect 逐字段解析（D30）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

import app.db.connection as conn_mod
from app.db.connection import connection
from app.db.migrate import migrate

_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def isolated_sqlite(tmp_path, monkeypatch):
    """把 connection 层重定向到临时 SQLite，避免污染 data/miirp.db。"""
    dsn = f"sqlite:///{tmp_path / 'miirp_test.db'}"
    monkeypatch.setattr(conn_mod, "DB_DSN", dsn)
    monkeypatch.setattr(conn_mod, "_SCHEME", "sqlite")
    monkeypatch.setattr(conn_mod, "_IS_PG", False)
    monkeypatch.setattr(conn_mod, "_IS_MYSQL", False)
    return dsn


def test_migrate_idempotent(isolated_sqlite):
    """连续两次 migrate 不报错（幂等，§13 验收 #2）。"""
    migrate()
    migrate()  # 第二次不抛异常即通过


def test_schema_version_records_match_migrations(isolated_sqlite):
    """重复迁移后 schema_version 记录与迁移文件一致（001 + 002 各一条，P0-4 整改）。"""
    migrate()
    migrate()
    with connection() as c:
        rows = c.query("SELECT version FROM schema_version")
    assert len(rows) == 2
    assert {r["version"] for r in rows} == {1, 2}   # 001_init + 002_inbound


def test_schema_version_primary_key_unique(isolated_sqlite):
    """version 主键唯一：重复插入 001 触发 IntegrityError。"""
    migrate()
    with pytest.raises(sqlite3.IntegrityError), connection() as c:
        c.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, ?)", (1, "dup"))


def test_applied_at_written_by_app(isolated_sqlite):
    """applied_at 应用侧写入：非空且为带时区 isoformat（§4.6，不依赖 DB 时间函数）。"""
    migrate()
    with connection() as c:
        row = c.query("SELECT applied_at FROM schema_version WHERE version = 1")[0]
    assert row["applied_at"]
    dt = datetime.fromisoformat(row["applied_at"])
    assert dt.tzinfo is not None  # UTC 时区


def test_tables_created_with_contract_columns(isolated_sqlite):
    """表与关键列对齐 data_contract §1.2/§1.3（audit_events / approval_requests / inbound_records）。"""
    migrate()
    with connection() as c:
        tables = {r["name"] for r in c.query("SELECT name FROM sqlite_master WHERE type='table'")}
        audit_cols = {r["name"] for r in c.query("PRAGMA table_info(audit_events)")}
        approval_cols = {r["name"] for r in c.query("PRAGMA table_info(approval_requests)")}
        inbound_cols = {r["name"] for r in c.query("PRAGMA table_info(inbound_records)")}
    assert {"schema_version", "audit_events", "approval_requests", "inbound_records"} <= tables
    assert {
        "correlation_id", "client_id", "tool_name", "args_summary",
        "status", "latency_ms", "created_at",
    } <= audit_cols
    assert {
        "id", "tool_name", "payload", "proposer", "status", "reviewer",
        "reason", "amount_cny", "double_review", "correlation_id", "created_at", "decided_at",
    } <= approval_cols
    assert {
        "in_no", "grn_no", "iqc_no", "sku", "inbound_qty", "unit_price",
        "currency", "supplier_id", "approval_request_id", "status", "reviewed_by", "created_at",
    } <= inbound_cols


def test_indexes_created(isolated_sqlite):
    """三方言同名索引齐全（data_contract §1.2/§1.3）。"""
    migrate()
    with connection() as c:
        idx = {r["name"] for r in c.query("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {
        "idx_audit_client", "idx_audit_tool", "idx_audit_time",
        "idx_approval_status", "idx_approval_created",
    } <= idx


def test_three_dialect_ddl_files_exist():
    """三方言 DDL 文件齐全（M1-03 验收 + P0-4 整改：001_init 与 002_inbound 均存在）。"""
    for dialect in ("sqlite", "pg", "mysql"):
        init = list(_ROOT.glob(f"migrations/*_init.{dialect}.sql"))
        assert init, f"缺失 migrations/*_init.{dialect}.sql"
        assert list(_ROOT.glob(f"migrations/002_inbound.{dialect}.sql")), \
            f"缺失 migrations/002_inbound.{dialect}.sql（P0-4 整改）"


# ---- 连接层方言适配（§6.5） ----


def test_adapt_placeholder_sqlite():
    """SQLite 保留 ? 占位符。"""
    sql, params = conn_mod._adapt("SELECT ? WHERE x = ?", (1, "a"))
    assert sql == "SELECT ? WHERE x = ?"
    assert params == (1, "a")


def test_adapt_percent_for_pg_mysql(monkeypatch):
    """PG/MySQL 占位符 ? → %s。"""
    monkeypatch.setattr(conn_mod, "_IS_PG", True)
    monkeypatch.setattr(conn_mod, "_IS_MYSQL", False)
    sql, params = conn_mod._adapt("SELECT ? WHERE x = ?", (1, "a"))
    assert sql == "SELECT %s WHERE x = %s"
    assert params == (1, "a")


class _FakeCursor:
    """最小 cursor 桩：记录 execute 语句，支持 fetchall/description/rowcount。"""

    def __init__(self, rows=None, description=None):
        self.rows = rows or []
        self.executed: list[tuple] = []
        self.description = description
        self.closed = False
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class _FakeConn:
    """最小连接桩：记录 commit/rollback/close 状态。"""

    def __init__(self, cursor=None):
        self._cursor = cursor or _FakeCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_connection_pg_branch_commit(monkeypatch):
    """connection() PG 分支：正常路径 commit + close。"""
    fake = _FakeConn()
    monkeypatch.setattr(conn_mod, "_IS_PG", True)
    monkeypatch.setattr(conn_mod, "_IS_MYSQL", False)
    monkeypatch.setattr(conn_mod, "_pg_connect", lambda: fake)
    with connection() as c:
        c.execute("CREATE TABLE t (id INTEGER)")
        c.query("SELECT 1")
    assert fake.committed
    assert not fake.rolled_back
    assert fake.closed


def test_connection_pg_branch_rollback(monkeypatch):
    """connection() PG 分支：异常路径 rollback + close。"""
    fake = _FakeConn()

    class _Boom(Exception):
        pass

    monkeypatch.setattr(conn_mod, "_IS_PG", True)
    monkeypatch.setattr(conn_mod, "_IS_MYSQL", False)
    monkeypatch.setattr(conn_mod, "_pg_connect", lambda: fake)
    with pytest.raises(_Boom), connection():
        raise _Boom("x")
    assert fake.rolled_back
    assert fake.closed


def test_execute_script_pg_branch(monkeypatch):
    """execute_script PG 分支：按 ; 分割逐条执行。"""
    fake = _FakeConn()
    monkeypatch.setattr(conn_mod, "_IS_PG", True)
    monkeypatch.setattr(conn_mod, "_IS_MYSQL", False)
    monkeypatch.setattr(conn_mod, "_pg_connect", lambda: fake)
    with connection() as c:
        c.execute_script("CREATE TABLE a (id int); CREATE TABLE b (id int);")
    stmts = [executed[0].strip() for executed in fake._cursor.executed]
    assert stmts == ["CREATE TABLE a (id int)", "CREATE TABLE b (id int)"]


def test_mysql_connect_parses_dsn_fields(monkeypatch):
    """D30：_mysql_connect 用 urllib.parse 逐字段解析（不接受 DSN 字符串）。"""
    captured: dict = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return "conn-ok"

    import pymysql

    monkeypatch.setattr(conn_mod, "DB_DSN", "mysql://qa:pw@db.example.com:3307/miirp?charset=utf8mb4")
    monkeypatch.setattr(pymysql, "connect", fake_connect)
    got = conn_mod._mysql_connect()
    assert got == "conn-ok"
    assert captured["host"] == "db.example.com"
    assert captured["port"] == 3307
    assert captured["user"] == "qa"
    assert captured["password"] == "pw"
    assert captured["database"] == "miirp"
    assert captured["charset"] == "utf8mb4"
    assert captured["cursorclass"] is pymysql.cursors.DictCursor


def test_query_mysql_dict_rows(monkeypatch):
    """query() MySQL 分支：DictCursor 行直接转 dict。"""
    rows = [{"a": 1}, {"a": 2}]
    cursor = _FakeCursor(rows=rows, description=[("a",)])
    fake = _FakeConn(cursor=cursor)
    monkeypatch.setattr(conn_mod, "_IS_PG", False)
    monkeypatch.setattr(conn_mod, "_IS_MYSQL", True)
    monkeypatch.setattr(conn_mod, "_mysql_connect", lambda: fake)
    with connection() as c:
        got = c.query("SELECT a")
    assert got == rows
