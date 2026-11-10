"""限流集成测试（defect_regression D11、api_contract §4 限流主体 client_id）。

语义事实源（§6.7 rate_limit.py）：
- 主体 = AuthRoleMiddleware 注入的 state 键 miirp_client_id（role → client_id 映射见 api_contract §4）。
- 滑动窗口：窗口容量 = qps × window_seconds（default 20×60=1200），
  过期时间戳（now - ts > window_seconds）从队首弹出后计数。
- 命中抛 RateLimitError（code=RATE_LIMITED，§6.4）。
- 集成测试真实挂载中间件链（§8.3）：经 FastMCP.call_tool 全链调用。
"""
from __future__ import annotations

import pytest

import app.middleware.rate_limit as rl_mod
from app.exceptions import RateLimitError


@pytest.fixture
def low_capacity(monkeypatch):
    """把窗口容量压到 2（qps=1 × window=2s），并清空命中表防跨用例污染。"""
    monkeypatch.setattr(rl_mod, "_qps", 1.0)
    monkeypatch.setattr(rl_mod, "_window", 2.0)
    rl_mod._hits.clear()
    yield
    rl_mod._hits.clear()


@pytest.fixture
def clear_hits():
    """默认容量下清空命中表（防其它集成用例的调用计入）。"""
    rl_mod._hits.clear()
    yield
    rl_mod._hits.clear()


@pytest.mark.asyncio
async def test_rate_limit_exceeded(mcp_app, monkeypatch, low_capacity):
    """超窗口容量 → RateLimitError（"rate limit exceeded"）。"""
    monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")   # client_id=wh-001
    for i in range(3):
        if i < 2:
            await mcp_app.call_tool("list_pending_approvals", {})
        else:
            with pytest.raises(RateLimitError) as exc_info:
                await mcp_app.call_tool("list_pending_approvals", {})
            assert "rate limit exceeded" in str(exc_info.value)


@pytest.mark.asyncio
async def test_rate_limit_normal_not_blocked(mcp_app, monkeypatch, clear_hits):
    """窗口容量内连续调用不误伤（默认容量 1200）。"""
    monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")
    for _ in range(10):
        await mcp_app.call_tool("list_pending_approvals", {})


@pytest.mark.asyncio
async def test_rate_limit_per_client_id(mcp_app, monkeypatch, low_capacity):
    """限流按 client_id 独立计数：不同角色互不影响。"""
    monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")   # wh-001 打满容量
    await mcp_app.call_tool("list_pending_approvals", {})
    await mcp_app.call_tool("list_pending_approvals", {})
    with pytest.raises(RateLimitError):
        await mcp_app.call_tool("list_pending_approvals", {})
    # 换角色（client_id=comp-001）→ 窗口独立，不受影响
    monkeypatch.setenv("MIIRP_ROLE", "compliance-officer")
    await mcp_app.call_tool("list_pending_approvals", {})
