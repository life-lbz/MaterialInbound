"""tests 共享 fixture（A7 所有权，开发文档 §8.1 测试分层）。"""
from __future__ import annotations

import fastmcp.server.context as ctx_mod
import pytest

import app.db.connection as conn_mod
from app.db.migrate import migrate


@pytest.fixture
def isolated_sqlite(tmp_path, monkeypatch):
    """把连接层重定向到临时 SQLite 并完成幂等迁移，避免污染 data/miirp.db。

    data_contract §1 语义：migrate 幂等（CREATE TABLE IF NOT EXISTS + version <= MAX 跳过）。
    """
    dsn = f"sqlite:///{tmp_path / 'miirp_test.db'}"
    monkeypatch.setattr(conn_mod, "DB_DSN", dsn)
    monkeypatch.setattr(conn_mod, "_SCHEME", "sqlite")
    monkeypatch.setattr(conn_mod, "_IS_PG", False)
    monkeypatch.setattr(conn_mod, "_IS_MYSQL", False)
    migrate()
    return dsn


@pytest.fixture
def flat_state_key(monkeypatch):
    """fastmcp 3.4.7 in-process Context 无 session_id（§8.3 in-process 语义）：

    Context.set_state/get_state 的键默认带 session_id 前缀，无 session 时抛
    RuntimeError(session_id is not available)。测试适配：state 键退化为全局键，
    等价真实 stdio/HTTP 服务端同一 session 内跨请求共享 state（中间件链行为一致）。
    仅改 fastmcp 框架层行为，不触碰 app/ 业务代码。
    """
    monkeypatch.setattr(ctx_mod.Context, "_make_state_key", lambda self, key: key)
