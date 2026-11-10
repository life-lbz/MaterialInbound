"""server.py 装配测试（M2 语义，module_boundaries §5.1 过渡装配自然收敛）。

语义事实源（§6.6、module_boundaries §5.1、defect_regression D7/D31）：
- M2 交付后 try-import 命中真实模块：4 个治理中间件非 None、9 工具模块注册 9 个（§5.1 过渡自然消失）。
- mount_http + http_app：/mcp /healthz /metrics /approvals 四路由全部挂载（api_contract §3）。
- /approvals 无 X-MIIRP-Key 头或角色不符 → 403 {"error": "forbidden"}；warehouse-manager 密钥 → 200 列表（api_contract §3）。
- server.version 与 config/server.version 统一 3.1.0（D31）。
"""
from __future__ import annotations

import httpx
import pytest

import app.db.connection as conn_mod
import server as server_mod
from server import build_app, mount_http


@pytest.fixture
def isolated_sqlite(tmp_path, monkeypatch):
    """build_app 内 migrate 写入临时 SQLite，避免污染 data/miirp.db。"""
    dsn = f"sqlite:///{tmp_path / 'miirp_server_test.db'}"
    monkeypatch.setattr(conn_mod, "DB_DSN", dsn)
    monkeypatch.setattr(conn_mod, "_SCHEME", "sqlite")
    monkeypatch.setattr(conn_mod, "_IS_PG", False)
    monkeypatch.setattr(conn_mod, "_IS_MYSQL", False)
    return dsn


def test_try_import_real_middleware():
    """§5.1：M2 交付后 try-import 命中真实模块，四个治理中间件均为真实类（非 None）。"""
    assert server_mod._AuthRoleMiddleware is not None
    assert server_mod._RateLimitMiddleware is not None
    assert server_mod._AuditMiddleware is not None
    assert server_mod._ApprovalMiddleware is not None
    assert server_mod._AuthRoleMiddleware.__name__ == "AuthRoleMiddleware"
    assert server_mod._RateLimitMiddleware.__name__ == "RateLimitMiddleware"
    assert server_mod._AuditMiddleware.__name__ == "AuditMiddleware"
    assert server_mod._ApprovalMiddleware.__name__ == "ApprovalMiddleware"


def test_register_tools_real_count(isolated_sqlite):
    """§5.1：M2 交付后 9 工具模块全部真实注册（po/grn/iqc/inventory/supplier/trace/inbound/approval/notify）。"""
    mcp = build_app()
    assert server_mod._register_tools(mcp) == 9


def test_build_app_basic(isolated_sqlite):
    """build_app 可装配：name/version 正确，fastmcp 内置三件套挂载（P2-4 整改后错误处理为业务码透传版）。"""
    mcp = build_app()
    assert mcp.name == "miirp"
    assert mcp.version == "3.1.0"  # D31
    names = [getattr(x, "__name__", None) or x.__class__.__name__ for x in mcp.middleware]
    assert "LoggingMiddleware" in names
    assert "TimingMiddleware" in names
    assert "BusinessErrorHandlingMiddleware" in names   # P2-4 整改：替代内置 ErrorHandlingMiddleware


def test_version_unified_with_config():
    """D31：server 侧版本与 config/server.version 统一 3.1.0。"""
    assert server_mod.SERVER_CFG.get("version") == "3.1.0"


def test_mount_http_registers_routes(isolated_sqlite):
    """mount_http：/mcp /healthz /metrics /approvals 四路由全部挂载（api_contract §3）。"""
    mcp = mount_http(build_app())
    app = mcp.http_app(path="/mcp")
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/mcp" in paths
    assert "/healthz" in paths
    assert "/metrics" in paths
    assert "/approvals" in paths


def _asgi_get(app, path: str) -> httpx.Response:
    """经 ASGITransport 发起 GET（ASGITransport 为异步 transport，包 asyncio.run）。"""
    import asyncio

    async def _request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(_request())


def test_healthz_route_ok(isolated_sqlite):
    """D7 语义：GET /healthz 返回 200 且 status=ok（SQLite 可连通）。"""
    mcp = mount_http(build_app())
    app = mcp.http_app(path="/mcp")
    resp = _asgi_get(app, "/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert body["version"] == "3.1.0"


def test_metrics_route_ok(isolated_sqlite):
    """GET /metrics 返回 Prometheus 文本（含计数器与直方图）。"""
    mcp = mount_http(build_app())
    app = mcp.http_app(path="/mcp")
    resp = _asgi_get(app, "/metrics")
    assert resp.status_code == 200
    assert "miirp_tool_calls_total" in resp.text
    assert "miirp_latency_histogram_bucket" in resp.text


def test_approvals_route_requires_key(isolated_sqlite):
    """api_contract §3：/approvals 必带 X-MIIRP-Key；缺失 → 403 forbidden；warehouse-manager → 200 列表。"""
    mcp = mount_http(build_app())
    app = mcp.http_app(path="/mcp")

    async def _get(headers: dict | None = None) -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/approvals", headers=headers or {})

    import asyncio

    # 无密钥 → 403 forbidden（api_contract §3 行）
    resp = asyncio.run(_get())
    assert resp.status_code == 403
    assert resp.json() == {"error": "forbidden"}

    # 非审批角色（qty-check-agent 密钥）→ 403 forbidden（角色须 warehouse-manager/compliance-officer）
    resp = asyncio.run(_get({"X-MIIRP-Key": "key-qty-001"}))
    assert resp.status_code == 403
    assert resp.json() == {"error": "forbidden"}

    # warehouse-manager 密钥 → 200，审批待办列表（空表 → 空列表）
    resp = asyncio.run(_get({"X-MIIRP-Key": "key-wh-001"}))
    assert resp.status_code == 200
    assert resp.json() == []
