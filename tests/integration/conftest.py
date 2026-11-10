"""tests/integration 共享 fixture：真实中间件链 + 9 工具模块注册（§8.3 M2-05）。

语义事实源（开发文档 §8.3、module_boundaries §4 红线、api_contract §4/§5）：
- 装载顺序固定：AuthRole→RateLimit→Audit→Approval→Logging→Timing→ErrorHandling。
- 集成测试必须真实挂载中间件链，禁止只测纯函数（协作方案 §3.8）。
- in-process 调用经 FastMCP.call_tool/list_tools（中间件 on_request/on_list_tools/on_call_tool
  全链生效），身份经 MIIRP_ROLE 环境变量注入（stdio 模式，api_contract §8 口径 #5）。
"""
from __future__ import annotations

import pytest
from fastmcp import FastMCP
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware

from app.middleware.approval import ApprovalMiddleware
from app.middleware.audit import AuditMiddleware
from app.middleware.auth_role import AuthRoleMiddleware
from app.middleware.error_handling import BusinessErrorHandlingMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from server import _register_tools


@pytest.fixture
def mcp_app(isolated_sqlite, flat_state_key):
    """真实装配的 FastMCP：7 件中间件链 + 9 工具模块注册（M2 交付后 server.py 同构装配）。"""
    mcp = FastMCP(
        "miirp-it",
        middleware=[
            AuthRoleMiddleware(),
            RateLimitMiddleware(),
            AuditMiddleware(),
            ApprovalMiddleware(),
            LoggingMiddleware(),
            TimingMiddleware(),
            BusinessErrorHandlingMiddleware(),   # P2-4 整改：与 server.py 装配一致
        ],
    )
    _register_tools(mcp)
    return mcp
