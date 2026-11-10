"""审批流集成测试（api_contract §5/§5.1、data_contract §2、D1/D5/D6/D32 回归）。

真实挂载中间件链（§8.3），经 FastMCP.call_tool 全链调用：
- 写工具（create_inbound_record）被 ApprovalMiddleware 拦截并创建审批单（D1 唯一入口）；
- 选项 B：审批通过后注入 miirp_approval_request_id，入库单携带 approval_request_id（D1 升级）；
- 拒绝 → ApprovalRejectedError（api_contract §5，code=APPROVAL_REJECTED）；
- 高价值（amount_cny = unit_price × quantity ≥ 100000.0）→ double_review=1，双人两签通过；
- 审计含失败事件（status="error:ApprovalRejectedError"，data_contract §1.2）。

安全回归（DC-002、RV-M2 P0-2/P0-3/P1-1，api_contract §5.1）：
- 非审批角色（grn-operator）对审批单决策 → PermissionDeniedError，决策不被接受、状态不变；
- 双人审批同人两签（wh-001 连续两签）→ 第二签 PermissionDeniedError，未置 approved；
- 合法双人两签（wh-001 + comp-001）仍通过（§5.1 异人校验语义）。

并发模型：submit（review-agent）阻塞于 wait_for_decision 轮询时让出事件循环，
approver（warehouse-manager/compliance-officer）在另一协程内 list/approve（MCP 协议并发语义）。
"""
from __future__ import annotations

import asyncio
import json

import pytest
from mcp.shared.exceptions import McpError

from app.approval.service import ApprovalService
from app.audit.store import AuditStore
from app.exceptions import ApprovalRejectedError, PermissionDeniedError

# 低价值单（amount = 0.0071 × 50000 × 7.1 = 2520.5 CNY < 100000，单审批；P1-8 折算）
_SUBMIT_LOW = {
    "grn_no": "GRN-20260820-011", "iqc_no": "IQC-20260820-011", "sku": "M-1001",
    "quantity": 50000, "unit_price": 0.0071, "currency": "USD", "supplier_id": "SUP-02",
}
# 高价值单（amount = 10.0 × 20000 = 200000 ≥ 100000，双人审批；
# P0-4 整改：引用种子 GRN-20260820-021/IQC-20260820-021/PO-20260819003）
_SUBMIT_HIGH = {
    "grn_no": "GRN-20260820-021", "iqc_no": "IQC-20260820-021", "sku": "M-1001",
    "quantity": 20000, "unit_price": 10.0, "currency": "CNY", "supplier_id": "SUP-02",
}


def _data(result):
    """从 ToolResult 提取结构化数据（structuredContent 优先，回退 JSON 文本）。"""
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    return json.loads(result.content[0].text)


async def _submit(mcp, args: dict):
    """提交入库单（会被审批中间件阻塞，最长 15s 保护，防 wait_for_decision 120s 挂死）。"""
    return await asyncio.wait_for(mcp.call_tool("create_inbound_record", args), timeout=15)


async def _first_pending(mcp):
    """以 warehouse-manager 身份取最新待办审批单。"""
    pend = _data(await mcp.call_tool("list_pending_approvals", {}))
    assert pend, "未生成审批单"
    return pend[0]


@pytest.mark.asyncio
async def test_write_tool_blocked_creates_approval(mcp_app, monkeypatch):
    """写工具被审批拦截：调用不直接执行，而是生成审批单（D1 唯一入口）。"""
    monkeypatch.setenv("MIIRP_ROLE", "review-agent")
    captured = {}

    async def approver():
        await asyncio.sleep(0.8)
        monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")
        pending = _data(await mcp_app.call_tool("list_pending_approvals", {}))
        captured["pending"] = pending
        rid = pending[0]["id"]
        await mcp_app.call_tool("approve_inbound_request",
                                {"request_id": rid, "approved": True, "reason": "集成通过"})

    sub = asyncio.create_task(_submit(mcp_app, _SUBMIT_LOW))
    ap = asyncio.create_task(approver())
    inbound = _data(await sub)
    await ap

    pending = captured["pending"]
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "create_inbound_record"   # 审批单由中间件创建
    assert pending[0]["proposer"] == "review-001"               # proposer = 提议人 client_id
    assert pending[0]["status"] == "pending"
    assert "in_no" in inbound


@pytest.mark.asyncio
async def test_approval_pass_injects_approval_request_id(mcp_app, monkeypatch):
    """选项 B：审批通过后入库单携带 approval_request_id（业务单 ↔ 审批单强关联）。"""
    monkeypatch.setenv("MIIRP_ROLE", "review-agent")
    captured = {}

    async def approver():
        await asyncio.sleep(0.8)
        monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")
        row = await _first_pending(mcp_app)
        captured["rid"] = row["id"]
        await mcp_app.call_tool("approve_inbound_request",
                                {"request_id": row["id"], "approved": True, "reason": "通过"})

    sub = asyncio.create_task(_submit(mcp_app, _SUBMIT_LOW))
    ap = asyncio.create_task(approver())
    inbound = _data(await sub)
    await ap

    assert inbound["status"] == "approved"                      # IN 落库恒 approved（§5 口径 #1）
    assert inbound["reviewed_by"] == "approval-flow"
    assert inbound["approval_request_id"] == captured["rid"]    # 选项 B 强关联


@pytest.mark.asyncio
async def test_approval_reject_raises(mcp_app, monkeypatch):
    """拒绝 → submit 抛 ApprovalRejectedError（审批未通过），无入库单。"""
    monkeypatch.setenv("MIIRP_ROLE", "review-agent")

    async def approver():
        await asyncio.sleep(0.8)
        monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")
        row = await _first_pending(mcp_app)
        res = await mcp_app.call_tool("approve_inbound_request",
                                      {"request_id": row["id"], "approved": False, "reason": "价格存疑"})
        assert _data(res)["status"] == "rejected"

    sub = asyncio.create_task(_submit(mcp_app, _SUBMIT_LOW))
    ap = asyncio.create_task(approver())
    with pytest.raises(ApprovalRejectedError) as exc_info:
        await sub
    await ap
    assert "审批未通过" in str(exc_info.value)   # api_contract §5 拒绝语义


@pytest.mark.asyncio
async def test_high_value_double_review(mcp_app, monkeypatch):
    """高价值（amount ≥ 100000）：double_review=1，第一签通过仍 pending，第二签通过才放行。"""
    monkeypatch.setenv("MIIRP_ROLE", "review-agent")
    captured = {}

    async def double_approver():
        await asyncio.sleep(0.8)
        monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")
        row = await _first_pending(mcp_app)
        captured["row"] = row
        r1 = await mcp_app.call_tool("approve_inbound_request",
                                     {"request_id": row["id"], "approved": True, "reason": "第一签"})
        assert _data(r1)["status"] == "pending"                 # 第一签仍 pending（等第二签）
        monkeypatch.setenv("MIIRP_ROLE", "compliance-officer")
        r2 = await mcp_app.call_tool("approve_inbound_request",
                                     {"request_id": row["id"], "approved": True, "reason": "第二签"})
        assert _data(r2)["status"] == "approved"

    sub = asyncio.create_task(_submit(mcp_app, _SUBMIT_HIGH))
    ap = asyncio.create_task(double_approver())
    inbound = _data(await sub)
    await ap

    assert captured["row"]["double_review"] == 1                # 高价值置双人审批
    assert captured["row"]["amount_cny"] == 200000.0
    assert inbound["approval_request_id"] == captured["row"]["id"]


@pytest.mark.asyncio
async def test_audit_records_failure_event(mcp_app, monkeypatch):
    """审计含失败事件：拒绝路径落 error:ApprovalRejectedError（data_contract §1.2）。"""
    monkeypatch.setenv("MIIRP_ROLE", "review-agent")

    async def rejecter():
        await asyncio.sleep(0.8)
        monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")
        row = await _first_pending(mcp_app)
        await mcp_app.call_tool("approve_inbound_request",
                                {"request_id": row["id"], "approved": False, "reason": "no"})

    sub = asyncio.create_task(_submit(mcp_app, _SUBMIT_LOW))
    ap = asyncio.create_task(rejecter())
    with pytest.raises(ApprovalRejectedError):
        await sub
    await ap

    rows = AuditStore.query(tool_name="create_inbound_record")
    assert rows, "审批拒绝路径应有审计事件"
    assert any(r["status"].startswith("error:") for r in rows), \
        f"应有失败事件, got {[r['status'] for r in rows]}"


@pytest.mark.asyncio
async def test_non_approver_role_decision_denied(mcp_app, monkeypatch):
    """P0-2（api_contract §5.1）：非审批角色 grn-operator 决策被拒（PermissionDeniedError），
    决策不被接受、审批单状态不变；随后合法审批人决策仍通过（拒绝仅因授权，审批机制未破坏）。

    集成层断点：approve_inbound_request ∉ grn-operator 白名单（§4），P0-1 tools/call
    强制校验先拦截（D32 攻击链断点）；decide 层审批人角色校验（reviewer ∈ APPROVE_ROLES
    解析 client_id 集合）由 unit 直测锚定（test_approval.py::test_decide_non_approver_denied）。"""
    monkeypatch.setenv("MIIRP_ROLE", "review-agent")
    captured = {}

    async def actor():
        await asyncio.sleep(0.8)
        # 待办查询本身亦仅审批角色可见（§4），故先以合法身份取 rid
        monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")
        row = await _first_pending(mcp_app)
        captured["rid"] = row["id"]
        # 非审批角色提交决策 → 拒绝
        monkeypatch.setenv("MIIRP_ROLE", "grn-operator")
        with pytest.raises(PermissionDeniedError) as exc_info:
            await mcp_app.call_tool("approve_inbound_request",
                                    {"request_id": row["id"], "approved": True, "reason": "越权决策"})
        assert exc_info.value.code == "PERMISSION_DENIED"
        assert "无权调用" in str(exc_info.value)
        # §5.1"决策不被接受、状态不变"
        assert ApprovalService.get_status(row["id"]) == "pending"
        # 合法审批人补签 → 通过（证明拒绝仅因授权角色）
        monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")
        res = await mcp_app.call_tool("approve_inbound_request",
                                      {"request_id": row["id"], "approved": True, "reason": "合法补签"})
        assert _data(res)["status"] == "approved"

    sub = asyncio.create_task(_submit(mcp_app, _SUBMIT_LOW))
    ap = asyncio.create_task(actor())
    inbound = _data(await sub)
    await ap
    assert inbound["approval_request_id"] == captured["rid"]


@pytest.mark.asyncio
async def test_same_reviewer_double_sign_denied(mcp_app, monkeypatch):
    """P0-3（api_contract §5.1）：双人审批第二签必须与第一签为不同审批人——
    wh-001 连续两签：第一签仍 pending，同人第二签 → PermissionDeniedError，未置 approved；
    随后异人 comp-001 补第二签 → approved（异人校验语义，D6 任一拒绝前置条件"两名不同审批人"成立）。"""
    monkeypatch.setenv("MIIRP_ROLE", "review-agent")
    captured = {}

    async def same_reviewer():
        await asyncio.sleep(0.8)
        monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")
        row = await _first_pending(mcp_app)
        captured["row"] = row
        r1 = await mcp_app.call_tool("approve_inbound_request",
                                     {"request_id": row["id"], "approved": True, "reason": "第一签"})
        assert _data(r1)["status"] == "pending"                 # 第一签仍 pending（等第二签）
        # 同一审批人（wh-001）第二签 → 拒绝（P0-3）
        # 注：decide 层抛 PermissionDeniedError，经 fastmcp 工具执行包装 + ErrorHandlingMiddleware
        # 在传输层统一转译（McpError，错误码归一）；decide 层 PermissionDeniedError 直测
        # 锚点在 unit test_approval.py::test_decide_same_reviewer_double_sign_denied。
        with pytest.raises(McpError) as exc_info:
            await mcp_app.call_tool("approve_inbound_request",
                                    {"request_id": row["id"], "approved": True, "reason": "第二签同人"})
        assert "双人审批第二签须为不同审批人" in str(exc_info.value)
        assert ApprovalService.get_status(row["id"]) == "pending"   # 同人第二签被拒，未置 approved
        # 异人（comp-001）补第二签 → approved
        monkeypatch.setenv("MIIRP_ROLE", "compliance-officer")
        r2 = await mcp_app.call_tool("approve_inbound_request",
                                     {"request_id": row["id"], "approved": True, "reason": "第二签"})
        assert _data(r2)["status"] == "approved"

    sub = asyncio.create_task(_submit(mcp_app, _SUBMIT_HIGH))
    ap = asyncio.create_task(same_reviewer())
    inbound = _data(await sub)
    await ap

    assert captured["row"]["double_review"] == 1                # 高价值置双人审批
    assert inbound["approval_request_id"] == captured["row"]["id"]
