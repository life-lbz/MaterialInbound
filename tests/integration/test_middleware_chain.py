"""中间件链集成测试（开发文档 §8.3、api_contract §4/§5、D11/D1/D32 回归）。

语义事实源：
- 装载顺序固定：AuthRole→RateLimit→Audit→Approval→Logging→Timing→ErrorHandling
  （module_boundaries §4 红线，server.py §6.6 装配段，禁止 A3/A4 改写）。
- 未授权身份（MIIRP_ROLE 缺失）→ PermissionError 经协议层转 McpError（任务语义），
  工具不可见/不可调用（api_contract §4 身份注入口径）。
- tools/list 按角色白名单裁剪（最小可见，§6.7 on_list_tools）：
  warehouse-manager 可见 create_inbound_record/approve_inbound_request 等；
  qty-check-agent 仅可见 get_po/get_grn。
- tools/call 按角色白名单强制校验（双重拦截第 2 层，DC-002/D32、RV-M2 P0-1）：
  越权角色直接调用白名单外工具 → PermissionDeniedError（api_contract §4）。
"""
from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError

from app.exceptions import PermissionDeniedError


def test_middleware_registration_order(mcp_app):
    """§6.6 装载顺序：AuthRole→RateLimit→Audit→Approval→Logging→Timing→ErrorHandling。"""
    names = [type(m).__name__ for m in mcp_app.middleware]
    expect = [
        "AuthRoleMiddleware", "RateLimitMiddleware", "AuditMiddleware",
        "ApprovalMiddleware", "LoggingMiddleware", "TimingMiddleware",
        "BusinessErrorHandlingMiddleware",     # P2-4 整改：业务错误码透传中间件替代内置
    ]
    assert names[: len(expect)] == expect, f"中间件顺序不符: {names}"


@pytest.mark.asyncio
async def test_unauthorized_identity_blocked(mcp_app, monkeypatch):
    """未授权身份（无 MIIRP_ROLE）：list_tools → McpError；call_tool → ToolError（工具不可调用）。"""
    monkeypatch.delenv("MIIRP_ROLE", raising=False)
    async with Client(mcp_app) as client:
        with pytest.raises(McpError):
            await client.list_tools()
        with pytest.raises(ToolError):
            await client.call_tool("get_po", {"po_no": "PO-20260818001"})


@pytest.mark.asyncio
async def test_tools_list_role_based_cutoff(mcp_app, monkeypatch):
    """最小可见：tools/list 按角色白名单裁剪（§6.7、api_contract §4）。"""
    monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")
    wh = {t.name for t in await mcp_app.list_tools()}
    assert {"create_inbound_record", "approve_inbound_request",
            "list_pending_approvals", "notify", "get_inventory"} <= wh
    assert "get_po" not in wh          # 非 warehouse-manager 白名单工具不可见
    assert "get_grn" not in wh

    monkeypatch.setenv("MIIRP_ROLE", "qty-check-agent")
    qty = {t.name for t in await mcp_app.list_tools()}
    assert qty == {"get_po", "get_grn"}
    assert "approve_inbound_request" not in qty   # §8.4 角色最小可见语义
    assert "create_inbound_record" not in qty

    monkeypatch.setenv("MIIRP_ROLE", "review-agent")
    review = {t.name for t in await mcp_app.list_tools()}
    assert review == {"create_inbound_record", "notify"}


@pytest.mark.asyncio
async def test_authorized_read_tool_called(mcp_app, monkeypatch):
    """授权角色（warehouse-manager）可正常调用白名单读工具 get_inventory。"""
    monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")
    res = await mcp_app.call_tool("get_inventory", {"sku": "M-1001"})
    data = getattr(res, "structuredContent", None) or res.content[0].text
    assert data  # 调用成功（种子台账 M-1001 存在，data_contract §4）


@pytest.mark.asyncio
async def test_unauthorized_read_tool_denied(mcp_app, monkeypatch):
    """D32/P1-1：越权读工具被拒——qty-check-agent（白名单 get_po/get_grn，§4）
    直接 tools/call get_inventory → PermissionDeniedError（tools/call 强制校验，
    双重拦截第 2 层，DC-002；即使该工具不在其 list_tools 可见集内仍须拒绝）。"""
    monkeypatch.setenv("MIIRP_ROLE", "qty-check-agent")
    with pytest.raises(PermissionDeniedError) as exc_info:
        await mcp_app.call_tool("get_inventory", {"sku": "M-1001"})
    assert exc_info.value.code == "PERMISSION_DENIED"
    assert "无权调用" in str(exc_info.value)


@pytest.mark.asyncio
async def test_unauthorized_approval_tool_denied(mcp_app, monkeypatch):
    """D32/P1-1：越权审批工具被拒——grn-operator（白名单 get_po/create_grn，§4）
    直接 tools/call approve_inbound_request → PermissionDeniedError（D32 攻击链
    "越权两签"断点：低权限角色无法触达审批决策；api_contract §4/§5 双重拦截）。"""
    monkeypatch.setenv("MIIRP_ROLE", "grn-operator")
    with pytest.raises(PermissionDeniedError) as exc_info:
        await mcp_app.call_tool("approve_inbound_request",
                                {"request_id": "dummy", "approved": True, "reason": "越权"})
    assert exc_info.value.code == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_unauthorized_call_denied_via_client(mcp_app, monkeypatch):
    """D32/P1-1（传输层语义）：经 Client（stdio 会话，§8 口径 #5）越权调用 →
    ToolError（ErrorHandlingMiddleware 统一转译），消息含"无权调用"——证明
    真实客户端会话路径同样拒绝，而非仅 in-process 直调被拦截。"""
    monkeypatch.setenv("MIIRP_ROLE", "grn-operator")
    async with Client(mcp_app) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("list_pending_approvals", {})
        assert "无权调用" in str(exc_info.value)
