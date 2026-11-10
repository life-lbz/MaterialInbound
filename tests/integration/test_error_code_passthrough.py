"""P2-4 整改：业务错误码经传输层透传（Client 会话路径）。

背景：fastmcp 内置 ErrorHandlingMiddleware 将业务异常归一为 Generic Error
（McpError -32603），业务错误码在传输层丢失语义。fastmcp 客户端传输层只透传
CallToolResult.content 文本（不携带 JSON-RPC error.data），故整改将业务码
编码进 message 前缀 "{code}: {message}"（ErrorData.data 保留结构化扩展）。

本测试经真实 Client（stdio 会话，走 on_message 传输层）验证：
- 审批拒绝路径 → ToolError，文本以 "APPROVAL_REJECTED: " 开头（可分支处理）；
- 越权拒绝路径 → "PERMISSION_DENIED: " 开头；
- 原语义（"审批未通过"/"无权调用"）不退化。
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

_SUBMIT_LOW = {
    "grn_no": "GRN-20260820-011", "iqc_no": "IQC-20260820-011", "sku": "M-1001",
    "quantity": 50000, "unit_price": 0.0071, "currency": "USD", "supplier_id": "SUP-02",
}


@pytest.mark.asyncio
async def test_approval_rejected_code_preserved(mcp_app, monkeypatch):
    """P2-4 整改：审批拒绝业务码透传（文本前缀 APPROVAL_REJECTED，非 Generic Error）。"""
    monkeypatch.setenv("MIIRP_ROLE", "review-agent")
    async with Client(mcp_app) as client:
        submit = asyncio.create_task(client.call_tool("create_inbound_record", _SUBMIT_LOW))
        await asyncio.sleep(0.6)
        monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")
        pend = await client.call_tool("list_pending_approvals", {})
        items = None
        if hasattr(pend, "structuredContent") and pend.structuredContent:
            items = pend.structuredContent.get("result")
        if items is None and getattr(pend, "content", None):
            items = json.loads(pend.content[0].text)
        rid = items[0]["id"]
        await client.call_tool("approve_inbound_request",
                               {"request_id": rid, "approved": False, "reason": "价格存疑"})
        with pytest.raises(ToolError) as exc_info:
            await submit
        msg = str(exc_info.value)
        assert msg.startswith("APPROVAL_REJECTED: "), f"业务错误码丢失: {msg}"
        assert "审批未通过" in msg                        # message 保留（拒绝语义不退化）


@pytest.mark.asyncio
async def test_permission_denied_code_preserved(mcp_app, monkeypatch):
    """P2-4 整改：越权调用业务码透传（文本前缀 PERMISSION_DENIED）。"""
    monkeypatch.setenv("MIIRP_ROLE", "grn-operator")
    async with Client(mcp_app) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("approve_inbound_request",
                                   {"request_id": "dummy", "approved": True, "reason": "越权"})
        msg = str(exc_info.value)
        assert msg.startswith("PERMISSION_DENIED: "), f"业务错误码丢失: {msg}"
        assert "无权调用" in msg
